//! Isolated synthetic-file probe, never opens the user's logger or profile.
use std::{fs, io::Write, path::Path, time::Instant};
#[path = "../src/log_reader.rs"]
mod log_reader;

// Exact game-log update block from first-audit app.rs:1048-1059, with the
// game-directory acquisition passed as an argument. Kept for comparison.
#[derive(Default)]
struct Baseline {
    noitalog: Vec<String>,
    noitalog_number: usize,
}
impl Baseline {
    fn poll(&mut self, game: &Path) -> usize {
        if let Ok(s) = fs::read_to_string(game.join("logger.txt")) {
            let bytes_read = s.len();
            let l = self.noitalog.len();
            if l != 0 && s.len() >= self.noitalog[l - 1].len() {
                if s.len() != self.noitalog[l - 1].len() {
                    self.noitalog[l - 1] = s
                }
            } else {
                self.noitalog_number = self.noitalog.len();
                self.noitalog.push(s);
            }
            return bytes_read;
        }
        0
    }
    fn retained(&self) -> usize {
        self.noitalog.iter().map(String::len).sum()
    }
}
fn percentile(v: &[u128], p: usize) -> u128 {
    v[(v.len() - 1) * p / 100]
}
fn write_samples(root: &Path, variant: &str, scenario: &str, size: usize, times: &[u128]) {
    let path = root.join("samples.csv");
    let new = !path.exists();
    let mut file = fs::OpenOptions::new()
        .append(true)
        .create(true)
        .open(path)
        .unwrap();
    if new {
        writeln!(file, "variant,scenario,file_bytes,sample,elapsed_ns").unwrap();
    }
    for (sample, ns) in times.iter().enumerate() {
        writeln!(file, "{variant},{scenario},{size},{sample},{ns}").unwrap();
    }
}
fn main() {
    let root = std::env::args()
        .nth(1)
        .expect("explicit disposable output directory required");
    let root = Path::new(&root);
    assert!(!root.exists(), "probe output must be a NEW directory");
    fs::create_dir_all(root).unwrap();
    if std::env::args().nth(2).as_deref() == Some("candidate") {
        candidate(root);
        return;
    }
    println!(
        "variant,scenario,file_bytes,samples,p50_ns,p95_ns,max_ns,read_bytes_per_update,retained_bytes"
    );
    for size in [0, 4096, 1024 * 1024, 44 * 1024 * 1024] {
        let dir = root.join(size.to_string());
        fs::create_dir_all(&dir).unwrap();
        let mut bytes = Vec::with_capacity(size);
        let line = b"LUA: ordinary synthetic log event 0123456789 abcdefghijklmnop\n";
        while bytes.len() < size {
            bytes.extend_from_slice(&line[..line.len().min(size - bytes.len())]);
        }
        fs::write(dir.join("logger.txt"), &bytes).unwrap();
        let mut reader = Baseline::default();
        reader.poll(&dir);
        for scenario in ["unchanged", "append", "clone_for_visible_tab"] {
            let mut times = Vec::new();
            let mut read_bytes = 0;
            for _ in 0..40 {
                if scenario == "append" {
                    fs::OpenOptions::new()
                        .append(true)
                        .open(dir.join("logger.txt"))
                        .unwrap()
                        .write_all(b"LUA: appended event\n")
                        .unwrap();
                }
                let start = Instant::now();
                if scenario == "clone_for_visible_tab" {
                    let s = reader.noitalog[reader.noitalog_number].clone() + "\n";
                    std::hint::black_box(s);
                } else {
                    read_bytes += reader.poll(&dir);
                }
                times.push(start.elapsed().as_nanos());
            }
            write_samples(root, "baseline", scenario, size, &times);
            times.sort();
            println!(
                "baseline,{scenario},{size},{},{},{},{},{},{}",
                times.len(),
                percentile(&times, 50),
                percentile(&times, 95),
                times[times.len() - 1],
                read_bytes / 40,
                reader.retained()
            );
        }
        fs::write(dir.join("logger.txt"), b"ERROR: restarted\n").unwrap();
        reader.poll(&dir);
        let after_restart = reader.retained();
        fs::write(dir.join("logger.txt"), b"FATAL: replaced!\n").unwrap();
        reader.poll(&dir);
        assert_eq!(
            reader.noitalog[reader.noitalog_number], "ERROR: restarted\n",
            "baseline unexpectedly detected same-length replacement"
        );
        println!(
            "baseline,restart_and_same_length,{size},1,0,0,0,0,{}",
            reader.retained()
        );
        fs::write(dir.join("logger.txt"), b"ERROR: partial \xf0\x9f").unwrap();
        reader.poll(&dir);
        assert_eq!(
            reader.noitalog[reader.noitalog_number], "ERROR: restarted\n",
            "baseline unexpectedly accepted incomplete UTF-8"
        );
        println!(
            "baseline,incomplete_utf8,{size},1,0,0,0,0,{}",
            reader.retained()
        );
        assert!(reader.retained() >= after_restart);
    }
}

fn candidate(root: &Path) {
    println!(
        "variant,scenario,file_bytes,samples,p50_ns,p95_ns,max_ns,read_bytes_per_update,retained_bytes"
    );
    for size in [0, 4096, 1024 * 1024, 44 * 1024 * 1024] {
        let dir = root.join(size.to_string());
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("logger.txt");
        let mut bytes = Vec::with_capacity(size);
        let line = b"LUA: ordinary synthetic log event 0123456789 abcdefghijklmnop\n";
        while bytes.len() < size {
            bytes.extend_from_slice(&line[..line.len().min(size - bytes.len())]);
        }
        fs::write(&path, &bytes).unwrap();
        let mut reader = log_reader::LogReader::default();
        let mut catchup = Vec::new();
        let mut read_bytes = 0;
        loop {
            let start = Instant::now();
            reader.poll(&path);
            catchup.push(start.elapsed().as_nanos());
            read_bytes += reader.bytes_read;
            if !reader.catching_up() {
                break;
            }
        }
        write_samples(root, "candidate", "cold_catchup", size, &catchup);
        catchup.sort();
        println!(
            "candidate,cold_catchup,{size},{},{},{},{},{},{}",
            catchup.len(),
            percentile(&catchup, 50),
            percentile(&catchup, 95),
            catchup[catchup.len() - 1],
            read_bytes,
            reader.retained_bytes()
        );
        for scenario in ["unchanged", "append", "borrow_visible_40_rows"] {
            let mut times = Vec::new();
            let mut read_bytes = 0;
            for _ in 0..40 {
                if scenario == "append" {
                    fs::OpenOptions::new()
                        .append(true)
                        .open(&path)
                        .unwrap()
                        .write_all(b"LUA: appended event\n")
                        .unwrap();
                }
                let start = Instant::now();
                if scenario == "borrow_visible_40_rows" {
                    for row in reader.row_count().saturating_sub(40)..reader.row_count() {
                        std::hint::black_box(reader.row(row));
                    }
                } else {
                    reader.poll(&path);
                    read_bytes += reader.bytes_read;
                }
                times.push(start.elapsed().as_nanos());
            }
            write_samples(root, "candidate", scenario, size, &times);
            times.sort();
            println!(
                "candidate,{scenario},{size},{},{},{},{},{},{}",
                times.len(),
                percentile(&times, 50),
                percentile(&times, 95),
                times[times.len() - 1],
                read_bytes / 40,
                reader.retained_bytes()
            );
        }
        fs::write(&path, b"ERROR: restarted\n").unwrap();
        reader.poll(&path);
        fs::write(&path, b"FATAL: replaced!\n").unwrap();
        reader.poll(&path);
        assert_eq!(reader.row(0), "FATAL: replaced!");
        println!(
            "candidate,restart_and_same_length,{size},1,0,0,0,{},{}",
            reader.bytes_read,
            reader.retained_bytes()
        );
        fs::write(&path, b"ERROR: partial \xf0\x9f").unwrap();
        reader.poll(&path);
        assert!(reader.error.is_none());
        fs::OpenOptions::new()
            .append(true)
            .open(&path)
            .unwrap()
            .write_all(b"\x98\x80\n")
            .unwrap();
        reader.poll(&path);
        assert_eq!(reader.row(0), "ERROR: partial 😀");
        println!(
            "candidate,incomplete_utf8_completed,{size},1,0,0,0,{},{}",
            reader.bytes_read,
            reader.retained_bytes()
        );
    }
}
