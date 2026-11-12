//! Independent std-only reader review target; never opens a live log.
//! rustc --edition=2024 --test -C opt-level=1 examples/log_reader_review_tests.rs -o <disposable.exe>
#[path = "../src/log_reader.rs"]
mod log_reader;

#[cfg(test)]
mod review {
    use super::log_reader::{LogReader, READ_BUDGET};
    use std::{
        fs,
        path::PathBuf,
        sync::atomic::{AtomicU64, Ordering},
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);

    struct Fixture(PathBuf);
    impl Fixture {
        fn new(bytes: &[u8]) -> Self {
            let dir = std::env::temp_dir().join(format!(
                "ew-log-independent-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&dir).unwrap();
            let f = Self(dir);
            fs::write(f.path(), bytes).unwrap();
            f
        }
        fn path(&self) -> PathBuf {
            self.0.join("logger.txt")
        }
        fn drain(&self, r: &mut LogReader) {
            for _ in 0..100 {
                r.poll(&self.path());
                if !r.catching_up() {
                    return;
                }
            }
            panic!("review fixture did not catch up");
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            if let (Ok(dir), Ok(root)) =
                (self.0.canonicalize(), std::env::temp_dir().canonicalize())
            {
                if dir != root
                    && dir.starts_with(root)
                    && dir.file_name() == self.0.file_name()
                    && dir
                        .file_name()
                        .is_some_and(|n| n.to_string_lossy().starts_with("ew-log-independent-"))
                {
                    let _ = fs::remove_dir_all(dir);
                }
            }
        }
    }

    #[test]
    fn specific_failure_utf8_across_front_scan_boundary_keeps_exact_offset_and_context() {
        // Place half the emoji at the end of the first scan read. Keep the
        // actual cause outside the recent tail, forcing the sequential path.
        let offset = READ_BUDGET - (b"Couldn't receive ".len() + 2);
        let mut bytes = vec![b'\n'; offset];
        bytes.extend_from_slice(
            "Couldn't receive 😀 length from stream\ntrace one\ntrace two\n".as_bytes(),
        );
        bytes.extend_from_slice(&b"ordinary suffix\n".repeat(30_000));
        let f = Fixture::new(&bytes);
        let mut r = LogReader::default();
        r.poll(&f.path());
        assert!(
            r.first_communication_failure.is_none(),
            "unfinished line must not be prematurely pinned"
        );
        f.drain(&mut r);
        let c = r.first_communication_failure.as_ref().unwrap();
        assert_eq!(c.byte_offset, offset as u64);
        assert_eq!(c.generation, 1);
        assert!(c.earlier_scanned);
        assert!(
            c.text
                .starts_with("Couldn't receive 😀 length from stream\ntrace one\ntrace two\n")
        );
        assert_eq!(c.text.matches("Couldn't receive").count(), 1);
        assert_eq!(c.text.matches("trace one").count(), 1);
        assert!(!r.invalid_utf8);
        assert!(!c.text.contains('�'));
    }

    #[test]
    fn rotation_with_old_partial_utf8_does_not_splice_reader_or_pinned_context() {
        let f =
            Fixture::new(b"ERROR: old root\nCouldn't receive OLD root\nold incomplete \xf0\x9f");
        let mut r = LogReader::default();
        r.poll(&f.path());
        let old_general = r.first_failure.as_ref().unwrap().text.clone();
        let old_specific = r.first_communication_failure.as_ref().unwrap().text.clone();
        fs::write(
            f.path(),
            b"new run begins\nCouldn't receive NEW root\nnew trace\n",
        )
        .unwrap();
        f.drain(&mut r);
        assert_eq!(r.generation, 2);
        assert_eq!(r.row_count(), 3);
        assert_eq!(r.row(0), "new run begins");
        assert_eq!(r.row(1), "Couldn't receive NEW root");
        assert_eq!(r.row(2), "new trace");
        assert_eq!(r.first_failure.as_ref().unwrap().text, old_general);
        assert_eq!(
            r.first_communication_failure.as_ref().unwrap().text,
            old_specific
        );
        assert_eq!(
            r.first_communication_failure.as_ref().unwrap().generation,
            1
        );
        assert!(!r.capture_text().contains("old incomplete"));
        assert!(!r.invalid_utf8);
    }
}
