//! Bounded diagnostic capture for the proxy UI, not a replacement for disk logs.
use std::{
    collections::VecDeque,
    fs::File,
    io::{self, Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    time::{Duration, Instant, SystemTime},
};

pub const RETAIN_BYTES: usize = 256 * 1024;
pub const READ_BUDGET: usize = 64 * 1024;
const MAX_ROWS: usize = 4096;
const MAX_LINE: usize = 4096;
const ERROR_BYTES: usize = 16 * 1024;
const ERROR_LINES: usize = 9;

#[derive(Default)]
struct Line {
    bytes: Vec<u8>,
    truncated: bool,
}
impl Line {
    fn push(&mut self, byte: u8) -> Option<Vec<u8>> {
        if byte == b'\n' {
            let mut bytes = std::mem::take(&mut self.bytes);
            if self.truncated {
                bytes.extend_from_slice(b" [line clipped at 4096 bytes]");
            }
            self.truncated = false;
            return Some(bytes);
        }
        if self.bytes.len() < MAX_LINE {
            self.bytes.push(byte);
        } else {
            self.truncated = true;
        }
        None
    }
    fn display(&self) -> String {
        let mut s = String::from_utf8_lossy(&self.bytes).into_owned();
        if self.truncated {
            s.push_str(" [line clipped at 4096 bytes]");
        }
        s
    }
}

#[derive(Clone, PartialEq, Eq)]
struct Stamp {
    len: u64,
    modified: Option<SystemTime>,
    created: Option<SystemTime>,
}

pub struct FirstFailure {
    pub generation: u64,
    pub text: String,
    pub byte_offset: u64,
    pub earlier_scanned: bool,
    lines: usize,
    next_offset: u64,
}
impl FirstFailure {
    fn new(generation: u64, offset: u64, earlier_scanned: bool) -> Self {
        Self {
            generation,
            text: String::new(),
            byte_offset: offset,
            earlier_scanned,
            lines: 0,
            next_offset: offset,
        }
    }
    fn complete(&self) -> bool {
        self.lines >= ERROR_LINES || self.text.len() >= ERROR_BYTES
    }
    fn append(&mut self, line: &[u8], offset: u64, next_offset: u64) {
        if self.complete() || offset < self.next_offset {
            return;
        }
        if offset != self.next_offset {
            // Never join nonadjacent tail windows into an apparent traceback.
            self.lines = ERROR_LINES;
            return;
        }
        let line = String::from_utf8_lossy(line);
        let mut end = line.len().min(ERROR_BYTES - self.text.len());
        while !line.is_char_boundary(end) {
            end -= 1;
        }
        self.text.push_str(&line[..end]);
        self.text.push('\n');
        self.lines += 1;
        self.next_offset = next_offset;
    }
}

#[derive(Default)]
pub struct LogReader {
    path: PathBuf,
    stamp: Option<Stamp>,
    last_poll: Option<Instant>,
    recent: VecDeque<String>,
    recent_bytes: usize,
    tail: Line,
    partial_display: String,
    scan: Line,
    tail_offset: u64,
    scan_offset: u64,
    tail_line_start: u64,
    scan_line_start: u64,
    tail_anchor: Vec<u8>,
    head_anchor: Vec<u8>,
    skip_tail_line: bool,
    pub generation: u64,
    pub omitted_bytes: u64,
    pub first_failure: Option<FirstFailure>,
    pub first_communication_failure: Option<FirstFailure>,
    pub error: Option<String>,
    pub bytes_read: u64,
    pub invalid_utf8: bool,
}

impl LogReader {
    pub fn poll_if_due(&mut self, path: &Path) {
        if self.path == path
            && self
                .last_poll
                .is_some_and(|t| t.elapsed() < Duration::from_millis(250))
        {
            return;
        }
        self.last_poll = Some(Instant::now());
        self.poll(path);
    }

    /// No throttling: also used by deterministic tests and the comparative probe.
    pub fn poll(&mut self, path: &Path) {
        self.bytes_read = 0;
        if self.path != path {
            *self = Self {
                path: path.to_owned(),
                last_poll: self.last_poll,
                ..Self::default()
            };
        }
        match self.read_update(path) {
            Ok(()) => self.error = None,
            Err(e) => {
                self.error = Some(format!(
                    "Cannot read {}: {e}. Showing the last captured content.",
                    path.display()
                ))
            }
        }
    }

    fn read_update(&mut self, path: &Path) -> io::Result<()> {
        let mut file = File::open(path)?;
        let meta = file.metadata()?;
        let stamp = Stamp {
            len: meta.len(),
            modified: meta.modified().ok(),
            created: meta.created().ok(),
        };
        let mut head = vec![0; stamp.len.min(64) as usize];
        file.read_exact(&mut head)?;
        self.bytes_read += head.len() as u64;
        let mut boundary_changed = false;
        if self.tail_offset <= stamp.len && !self.tail_anchor.is_empty() {
            let n = self.tail_anchor.len();
            let mut anchor = vec![0; n];
            file.seek(SeekFrom::Start(self.tail_offset - n as u64))?;
            file.read_exact(&mut anchor)?;
            self.bytes_read += n as u64;
            boundary_changed = anchor != self.tail_anchor;
        }
        if self.stamp.as_ref() == Some(&stamp)
            && self.head_anchor == head
            && !boundary_changed
            && self.tail_offset == stamp.len
            && self.scan_done(stamp.len)
        {
            return Ok(());
        }
        let head_changed = !head.starts_with(&self.head_anchor);
        let restart = self.stamp.as_ref().is_none_or(|old| {
            stamp.created != old.created
                || stamp.len < old.len
                || (stamp.len == old.len && stamp.modified != old.modified)
                || head_changed
                || boundary_changed
        });
        if restart {
            // Context belongs to the old generation; never append a new run's
            // lines beneath an older failure and imply they are its traceback.
            if let Some(failure) = self.first_failure.as_mut() {
                failure.lines = ERROR_LINES;
            }
            if let Some(failure) = self.first_communication_failure.as_mut() {
                failure.lines = ERROR_LINES;
            }
            self.generation += 1;
            self.recent.clear();
            self.recent_bytes = 0;
            self.tail = Line::default();
            self.scan = Line::default();
            self.partial_display.clear();
            self.tail_anchor.clear();
            self.tail_offset = stamp.len.saturating_sub(RETAIN_BYTES as u64);
            self.omitted_bytes = self.tail_offset;
            self.skip_tail_line = self.tail_offset != 0;
            self.scan_offset = 0;
            self.tail_line_start = self.tail_offset;
            self.scan_line_start = 0;
            self.invalid_utf8 = false;
        } else if stamp.len.saturating_sub(self.tail_offset) > RETAIN_BYTES as u64 {
            // A producer faster than this UI cannot force an ever-growing backlog.
            let target = stamp.len - RETAIN_BYTES as u64;
            self.omitted_bytes += target - self.tail_offset;
            self.tail_offset = target;
            self.tail = Line::default();
            self.tail_line_start = target;
            self.partial_display.clear();
            self.skip_tail_line = true;
        }
        let data = self.read_at(&mut file, self.tail_offset, stamp.len)?;
        let tail_start = self.tail_offset;
        self.tail_offset += data.len() as u64;
        for (index, byte) in data.into_iter().enumerate() {
            if let Some(line) = self.tail.push(byte) {
                let offset = self.tail_line_start;
                let next_offset = tail_start + index as u64 + 1;
                self.tail_line_start = next_offset;
                if self.skip_tail_line {
                    self.skip_tail_line = false;
                    continue;
                }
                self.observe_communication(&line, offset, next_offset, false);
                self.invalid_utf8 |= std::str::from_utf8(&line).is_err();
                let text = String::from_utf8_lossy(&line)
                    .trim_end_matches('\r')
                    .to_owned();
                self.recent_bytes += text.len() + 1;
                self.recent.push_back(text);
                while self.recent_bytes > RETAIN_BYTES || self.recent.len() > MAX_ROWS {
                    let old = self.recent.pop_front().unwrap();
                    self.recent_bytes -= old.len() + 1;
                    self.omitted_bytes += (old.len() + 1) as u64;
                }
            }
        }
        self.partial_display = if self.skip_tail_line {
            String::new()
        } else {
            self.tail.display()
        };
        let n = self.tail_offset.min(64) as usize;
        self.tail_anchor.resize(n, 0);
        if n != 0 {
            file.seek(SeekFrom::Start(self.tail_offset - n as u64))?;
            file.read_exact(&mut self.tail_anchor)?;
            self.bytes_read += n as u64;
        }
        if !self.scan_done(stamp.len) {
            let data = self.read_at(&mut file, self.scan_offset, stamp.len)?;
            let scan_start = self.scan_offset;
            self.scan_offset += data.len() as u64;
            for (index, byte) in data.into_iter().enumerate() {
                if let Some(line) = self.scan.push(byte) {
                    let offset = self.scan_line_start;
                    let next_offset = scan_start + index as u64 + 1;
                    self.scan_line_start = next_offset;
                    if self.first_failure.is_none() && failure_like(&line) {
                        self.first_failure = Some(FirstFailure::new(self.generation, offset, true));
                    }
                    if let Some(capture) = self.first_failure.as_mut() {
                        if capture.generation == self.generation {
                            capture.append(&line, offset, next_offset);
                        }
                    }
                    self.observe_communication(&line, offset, next_offset, true);
                    if self.pinned_contexts_complete() {
                        break;
                    }
                }
            }
        }
        self.stamp = Some(stamp);
        self.head_anchor = head;
        Ok(())
    }

    fn scan_done(&self, len: u64) -> bool {
        self.pinned_contexts_complete() || self.scan_offset >= len
    }
    fn pinned_contexts_complete(&self) -> bool {
        self.first_failure
            .as_ref()
            .is_some_and(FirstFailure::complete)
            && self.first_communication_failure.as_ref().is_some_and(|f| {
                f.complete() && (f.earlier_scanned || f.generation != self.generation)
            })
    }
    fn observe_communication(
        &mut self,
        line: &[u8],
        offset: u64,
        next_offset: u64,
        from_scan: bool,
    ) {
        if communication_failure_like(line) {
            let replace = self.first_communication_failure.as_ref().is_none_or(|f| {
                f.generation == self.generation
                    && (offset < f.byte_offset
                        || (from_scan && offset == f.byte_offset && !f.earlier_scanned))
            });
            if replace {
                self.first_communication_failure =
                    Some(FirstFailure::new(self.generation, offset, from_scan));
            }
        }
        if let Some(capture) = self.first_communication_failure.as_mut() {
            if capture.generation == self.generation {
                capture.append(line, offset, next_offset);
            }
        }
    }
    fn read_at(&mut self, file: &mut File, offset: u64, len: u64) -> io::Result<Vec<u8>> {
        let mut data = vec![0; len.saturating_sub(offset).min(READ_BUDGET as u64) as usize];
        file.seek(SeekFrom::Start(offset))?;
        let mut count = 0;
        while count < data.len() {
            let n = file.read(&mut data[count..])?;
            if n == 0 {
                break;
            }
            count += n;
        }
        data.truncate(count);
        self.bytes_read += count as u64;
        Ok(data)
    }
    pub fn row_count(&self) -> usize {
        self.recent.len() + usize::from(!self.partial_display.is_empty())
    }
    pub fn row(&self, index: usize) -> &str {
        self.recent
            .get(index)
            .map(String::as_str)
            .unwrap_or(&self.partial_display)
    }
    pub fn retained_bytes(&self) -> usize {
        self.recent_bytes
            + self.partial_display.len()
            + self.first_failure.as_ref().map_or(0, |f| f.text.len())
            + self
                .first_communication_failure
                .as_ref()
                .map_or(0, |f| f.text.len())
    }
    pub fn source_len(&self) -> u64 {
        self.stamp.as_ref().map_or(0, |s| s.len)
    }
    pub fn source_path(&self) -> &Path {
        &self.path
    }
    pub fn catching_up(&self) -> bool {
        self.tail_offset < self.source_len() || !self.scan_done(self.source_len())
    }
    pub fn capture_text(&self) -> String {
        let mut out = format!(
            "Partial diagnostic capture of {}\nRecent 256 KiB / 4096 lines; lines clipped at 4096 bytes. First general and first specific communication contexts: up to 16 KiB / 9 lines each. Earlier runs not retained. {} older bytes omitted (approximate).\n",
            self.path.display(),
            self.omitted_bytes
        );
        if self.catching_up() {
            out.push_str("Capture/scanning is still catching up.\n");
        }
        if let Some(error) = &self.error {
            out.push_str(error);
            out.push('\n');
        }
        if let Some(f) = &self.first_failure {
            out.push_str(&format!(
                "First general failure-like context (generation {}, byte {}):\n{}\n",
                f.generation, f.byte_offset, f.text
            ));
        }
        if let Some(f) = &self.first_communication_failure {
            out.push_str(&format!(
                "First specific communication failure observed (generation {}, byte {}; {}):\n{}\n",
                f.generation,
                f.byte_offset,
                if f.earlier_scanned {
                    "earlier bytes scanned"
                } else if f.generation == self.generation {
                    "provisional: earlier bytes not yet verified"
                } else {
                    "provisional: prior generation ended before earlier-byte verification"
                },
                f.text
            ));
        }
        out.push_str("Recent log:\n");
        for n in 0..self.row_count() {
            out.push_str(self.row(n));
            out.push('\n');
        }
        out
    }
}

fn failure_like(line: &[u8]) -> bool {
    [
        b"error".as_slice(),
        b"panic",
        b"failed",
        b"couldn't",
        b"could not",
        b"fatal",
        b"exception",
        b"channel disconnected",
    ]
    .iter()
    .any(|term| {
        line.windows(term.len())
            .any(|piece| piece.eq_ignore_ascii_case(term))
    })
}

fn contains_ci(line: &[u8], term: &[u8]) -> bool {
    line.windows(term.len())
        .any(|piece| piece.eq_ignore_ascii_case(term))
}
fn communication_failure_like(line: &[u8]) -> bool {
    // Preserve generic errors separately. This additional slot protects a useful
    // stream/transport cause from being buried under follow-on disconnect spam.
    [
        b"couldn't receive".as_slice(),
        b"could not receive",
        b"couldn't write",
        b"could not write",
        b"failed to receive",
        b"failed to send",
        b"lost connection to noita",
        b"connection reset",
        b"forcibly closed",
        b"broken pipe",
        b"unexpected eof",
    ]
    .iter()
    .any(|term| contains_ci(line, term))
        || (contains_ci(line, b"error in rust call") && !contains_ci(line, b"channel disconnected"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        io::Write,
        sync::atomic::{AtomicU64, Ordering},
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);
    struct Fixture {
        dir: PathBuf,
        path: PathBuf,
    }
    impl Fixture {
        fn new(bytes: &[u8]) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "ew-log-reader-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir_all(&dir).unwrap();
            let path = dir.join("logger.txt");
            fs::write(&path, bytes).unwrap();
            Self { dir, path }
        }
        fn append(&self, b: &[u8]) {
            fs::OpenOptions::new()
                .append(true)
                .open(&self.path)
                .unwrap()
                .write_all(b)
                .unwrap();
        }
        fn drain(&self, r: &mut LogReader) {
            for _ in 0..2000 {
                r.poll(&self.path);
                if !r.catching_up() {
                    return;
                }
            }
            panic!("reader did not catch up");
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Resolve both paths before recursive cleanup, including on Windows.
            // An unexpected link or changed temp root must never broaden deletion.
            if let (Ok(dir), Ok(root)) =
                (self.dir.canonicalize(), std::env::temp_dir().canonicalize())
            {
                if dir != root
                    && dir.starts_with(&root)
                    && dir.file_name() == self.dir.file_name()
                    && dir
                        .file_name()
                        .is_some_and(|name| name.to_string_lossy().starts_with("ew-log-reader-"))
                {
                    let _ = fs::remove_dir_all(dir);
                }
            }
        }
    }
    #[test]
    fn empty_and_unchanged_do_not_read_again() {
        let f = Fixture::new(b"");
        let mut r = LogReader::default();
        r.poll(&f.path);
        r.poll(&f.path);
        assert_eq!(r.bytes_read, 0);
        assert_eq!(r.row_count(), 0);
    }
    #[test]
    fn appends_are_incremental_and_keep_partial_utf8() {
        let f = Fixture::new(b"ok\npartial \xf0\x9f");
        let mut r = LogReader::default();
        r.poll(&f.path);
        f.append(b"\x98\x80\nnext\n");
        r.poll(&f.path);
        assert_eq!(r.row(1), "partial 😀");
        assert_eq!(r.row(2), "next");
        assert_eq!(
            r.generation, 1,
            "ordinary short-file append is not a new run"
        );
        assert!(r.bytes_read < 256);
        assert!(!r.invalid_utf8);
        r.poll(&f.path);
        assert!(r.bytes_read <= 128);
    }
    #[test]
    fn invalid_utf8_is_visible_instead_of_freezing_capture() {
        let f = Fixture::new(b"before\nERROR: bad \xff\nafter\n");
        let mut r = LogReader::default();
        f.drain(&mut r);
        assert!(r.invalid_utf8);
        assert!(r.row(1).contains('�'));
        assert_eq!(r.row(2), "after");
        assert!(r.first_failure.as_ref().unwrap().text.contains("ERROR:"));
    }
    #[test]
    fn bounded_recent_and_first_failure_survive_long_history() {
        let mut b = b"ERROR: original useful cause\nstack frame\n".to_vec();
        b.extend(b"ordinary long history line\n".repeat(1_800_000));
        b.extend(b"latest tail marker\n");
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        f.drain(&mut r);
        assert!(r.retained_bytes() < RETAIN_BYTES + ERROR_BYTES + MAX_LINE + 1024);
        assert!(
            r.first_failure
                .as_ref()
                .unwrap()
                .text
                .contains("original useful cause")
        );
        assert_eq!(r.row(r.row_count() - 1), "latest tail marker");
        assert!(r.omitted_bytes > 0);
        r.poll(&f.path);
        assert_eq!(r.bytes_read, 128);
    }
    #[test]
    fn truncation_retains_first_failure_but_resets_recent() {
        let f = Fixture::new(b"ERROR: first\nold old old\n");
        let mut r = LogReader::default();
        r.poll(&f.path);
        fs::write(&f.path, b"new\n").unwrap();
        r.poll(&f.path);
        assert_eq!(r.row_count(), 1);
        assert_eq!(r.row(0), "new");
        assert_eq!(r.generation, 2);
        assert!(r.first_failure.as_ref().unwrap().text.contains("first"));
    }
    #[test]
    fn restart_does_not_append_new_run_to_old_failure_context() {
        let f = Fixture::new(b"ERROR: first\nold context\n");
        let mut r = LogReader::default();
        r.poll(&f.path);
        let before = r.first_failure.as_ref().unwrap().text.clone();
        fs::write(&f.path, b"new\n").unwrap();
        r.poll(&f.path);
        assert_eq!(r.first_failure.as_ref().unwrap().text, before);
        assert_eq!(r.first_failure.as_ref().unwrap().generation, 1);
    }
    #[test]
    fn same_length_replacement_changes_current_text() {
        let f = Fixture::new(b"old message\n");
        let mut r = LogReader::default();
        r.poll(&f.path);
        fs::write(&f.path, b"new message\n").unwrap();
        r.poll(&f.path);
        assert_eq!(r.row(0), "new message");
        assert_eq!(r.generation, 2);
    }
    #[test]
    fn same_length_replacement_with_preserved_timestamp_is_sampled() {
        let f = Fixture::new(b"old message\n");
        let mut r = LogReader::default();
        r.poll(&f.path);
        let old = fs::metadata(&f.path).unwrap().modified().unwrap();
        fs::write(&f.path, b"new message\n").unwrap();
        File::options()
            .write(true)
            .open(&f.path)
            .unwrap()
            .set_modified(old)
            .unwrap();
        r.poll(&f.path);
        assert_eq!(r.row(0), "new message");
        assert_eq!(r.generation, 2);
    }
    #[test]
    fn larger_rewrite_detected_by_old_boundary_anchor() {
        let f = Fixture::new(b"old message\n");
        let mut r = LogReader::default();
        r.poll(&f.path);
        fs::write(&f.path, b"new replacement much larger\n").unwrap();
        r.poll(&f.path);
        assert_eq!(r.row(0), "new replacement much larger");
        assert_eq!(r.generation, 2);
    }
    #[test]
    fn missing_file_keeps_capture_and_recovers() {
        let f = Fixture::new(b"keep\n");
        let mut r = LogReader::default();
        r.poll(&f.path);
        fs::remove_file(&f.path).unwrap();
        r.poll(&f.path);
        assert!(r.error.is_some());
        assert_eq!(r.row(0), "keep");
        fs::write(&f.path, b"recovered\n").unwrap();
        r.poll(&f.path);
        assert!(r.error.is_none());
        assert_eq!(r.row(0), "recovered");
    }
    #[test]
    fn empty_line_history_and_unterminated_line_are_bounded() {
        let mut b = vec![b'\n'; 100_000];
        b.extend(vec![b'x'; 1_000_000]);
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        f.drain(&mut r);
        assert!(r.row_count() <= MAX_ROWS + 1);
        assert!(r.retained_bytes() <= RETAIN_BYTES + ERROR_BYTES + MAX_LINE + 1024);
    }
    #[test]
    fn read_budget_is_bounded_even_during_catchup() {
        let f = Fixture::new(&b"ordinary line\n".repeat(100_000));
        let mut r = LogReader::default();
        for _ in 0..5 {
            r.poll(&f.path);
            assert!(r.bytes_read <= 2 * READ_BUDGET as u64 + 192);
        }
        assert!(r.catching_up());
    }
    #[test]
    fn communication_failure_survives_earlier_sprite_error_and_disconnected_spam() {
        let mut b = b"ERROR - LoadSpriteTo() file doesn't exist: data/temp/temp_gun.png\n".to_vec();
        b.extend(b"ordinary setup line\n".repeat(20));
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        r.poll(&f.path);
        f.append(b"LUA: Error in rust call: Couldn't receive the length from stream\nfirst useful trace\n");
        r.poll(&f.path);
        f.append(&b"LUA: Error in rust call: Channel disconnected\n".repeat(20_000));
        f.drain(&mut r);
        assert!(
            r.capture_text()
                .contains("Couldn't receive the length from stream"),
            "the useful communication failure was evicted behind disconnected spam"
        );
        assert!(
            r.first_failure
                .as_ref()
                .unwrap()
                .text
                .contains("temp_gun.png"),
            "do not hide the earlier general error"
        );
    }
    #[test]
    fn late_tail_communication_failure_is_pinned_before_front_scan_catches_up() {
        let mut b = b"ERROR - LoadSpriteTo() file doesn't exist: data/temp/temp_gun.png\n".to_vec();
        b.extend(b"ordinary pre-error history line\n".repeat(1_500_000));
        b.extend(b"LUA: Error in rust call: Couldn't receive the length from stream\nlate useful trace\n");
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        for _ in 0..4 {
            r.poll(&f.path);
        }
        assert!(
            r.capture_text()
                .contains("Couldn't receive the length from stream"),
            "late failure must first be seen in the recent tail"
        );
        f.append(&b"LUA: Error in rust call: Channel disconnected\n".repeat(20_000));
        for _ in 0..4 {
            r.poll(&f.path);
        }
        assert!(
            r.capture_text()
                .contains("Couldn't receive the length from stream"),
            "late observed failure was lost while the earlier scan was pending"
        );
    }
    #[test]
    fn sequential_scan_refines_provisional_tail_failure_to_earlier_cause() {
        let mut b = b"ERROR: general\n".to_vec();
        b.extend(b"ordinary earlier history\n".repeat(50_000));
        b.extend(b"Error in rust call: Couldn't receive EARLIER stream length\nearly trace\n");
        b.extend(b"ordinary middle history\n".repeat(300_000));
        b.extend(b"Error in rust call: Couldn't receive LATER stream body\nlate trace\n");
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        for _ in 0..4 {
            r.poll(&f.path);
        }
        let late = r.first_communication_failure.as_ref().unwrap();
        assert!(late.text.contains("LATER"));
        assert!(!late.earlier_scanned);
        f.drain(&mut r);
        let early = r.first_communication_failure.as_ref().unwrap();
        assert!(early.text.contains("EARLIER"));
        assert!(!early.text.contains("LATER"));
        assert!(early.earlier_scanned);
    }
    #[test]
    fn communication_context_is_not_duplicated_by_front_and_tail_readers() {
        let f = Fixture::new(
            b"Error in rust call: Couldn't receive the length from stream\nframe one\nframe two\n",
        );
        let mut r = LogReader::default();
        f.drain(&mut r);
        let c = r.first_communication_failure.as_ref().unwrap();
        assert_eq!(c.text.matches("Couldn't receive").count(), 1);
        assert_eq!(c.text.matches("frame one").count(), 1);
        assert_eq!(c.lines, 3);
        assert!(c.earlier_scanned);
    }
    #[test]
    fn disconnected_spam_is_not_mistaken_for_specific_transport_cause() {
        let mut b = b"ERROR: general\n".to_vec();
        b.extend(b"Error in rust call: Channel disconnected\n".repeat(2000));
        b.extend(b"Error in rust call: Couldn't write message body to stream\nreal trace\n");
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        f.drain(&mut r);
        assert!(
            r.first_communication_failure
                .as_ref()
                .unwrap()
                .text
                .starts_with("Error in rust call: Couldn't write")
        );
        assert!(r.first_failure.as_ref().unwrap().text.contains("general"));
    }
    #[test]
    fn restart_does_not_splice_runs_or_confirm_unscanned_earlier_bytes() {
        let mut b = b"ordinary history\n".repeat(500_000);
        b.extend(b"Couldn't receive OLD root cause\nold trace\n");
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        for _ in 0..4 {
            r.poll(&f.path);
        }
        let old = r.first_communication_failure.as_ref().unwrap().text.clone();
        assert!(
            !r.first_communication_failure
                .as_ref()
                .unwrap()
                .earlier_scanned
        );
        fs::write(
            &f.path,
            b"ERROR: new run\nCouldn't receive NEW failure\nnew trace\n",
        )
        .unwrap();
        f.drain(&mut r);
        let kept = r.first_communication_failure.as_ref().unwrap();
        assert_eq!(kept.text, old);
        assert_eq!(kept.generation, 1);
        assert!(!kept.earlier_scanned);
        assert_eq!(r.generation, 2);
        assert!(r.capture_text().contains("prior generation ended before earlier-byte verification"));
    }
    #[test]
    fn both_contexts_remain_bounded_and_copied_capture_labels_provisional() {
        let mut b = b"ERROR: general\n".to_vec();
        b.extend(b"g".repeat(8000));
        b.extend(b"\n".repeat(12));
        b.extend(b"ordinary history\n".repeat(100_000));
        b.extend(b"Couldn't receive the length from stream\n");
        b.extend(b"c".repeat(8000));
        b.extend(b"\n".repeat(12));
        let f = Fixture::new(&b);
        let mut r = LogReader::default();
        for _ in 0..4 {
            r.poll(&f.path);
        }
        assert!(
            r.capture_text()
                .contains("provisional: earlier bytes not yet verified")
        );
        assert!(r.retained_bytes() <= RETAIN_BYTES + 2 * ERROR_BYTES + MAX_LINE + 1024);
        f.drain(&mut r);
        assert!(r.first_failure.as_ref().unwrap().text.len() <= ERROR_BYTES + 1);
        assert!(r.first_communication_failure.as_ref().unwrap().text.len() <= ERROR_BYTES + 1);
    }
    #[test]
    fn explicit_stream_cause_is_kept_even_if_the_same_line_mentions_disconnect() {
        let f=Fixture::new(b"ERROR: general\nCouldn't receive length from stream: Channel disconnected\nuseful trace\n");
        let mut r = LogReader::default();
        f.drain(&mut r);
        assert!(
            r.first_communication_failure
                .as_ref()
                .unwrap()
                .text
                .contains("Couldn't receive length")
        );
    }
}
