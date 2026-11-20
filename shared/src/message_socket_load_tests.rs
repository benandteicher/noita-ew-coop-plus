//! Bounded observation of the actual framed socket. No production queue policy.
use super::MessageSocket;
use std::{
    io::{Read, Write},
    net::{Shutdown, TcpListener, TcpStream},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc,
    },
    thread,
    time::{Duration, Instant},
};

#[derive(Debug, bitcode::Encode, bitcode::Decode)]
struct Sample {
    sequence: u64,
    offered_us: u64,
    data: Vec<u8>,
}

fn pair() -> (TcpStream, TcpStream) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let sender =
        TcpStream::connect_timeout(&listener.local_addr().unwrap(), Duration::from_secs(1))
            .unwrap();
    listener.set_nonblocking(true).unwrap();
    let until = Instant::now() + Duration::from_secs(1);
    let receiver = loop {
        match listener.accept() {
            Ok((socket, _)) => break socket,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                assert!(Instant::now() < until, "accept deadline exceeded");
                thread::sleep(Duration::from_millis(1));
            }
            Err(error) => panic!("accept failed: {error}"),
        }
    };
    receiver.set_nonblocking(false).unwrap();
    (sender, receiver)
}

fn watchdog() -> Arc<AtomicBool> {
    let complete = Arc::new(AtomicBool::new(false));
    if let Ok(milliseconds) = std::env::var("EW_SOCKET_WATCHDOG_MS") {
        let milliseconds: u64 = milliseconds.parse().unwrap();
        assert!((1000..=15000).contains(&milliseconds));
        let complete = complete.clone();
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(milliseconds));
            if !complete.load(Ordering::SeqCst) {
                std::process::exit(124);
            }
        });
    }
    complete
}

fn finished<T>(thread: &thread::JoinHandle<T>, timeout: Duration) -> bool {
    let until = Instant::now() + timeout;
    while !thread.is_finished() && Instant::now() < until {
        thread::sleep(Duration::from_millis(1));
    }
    thread.is_finished()
}

#[test]
fn established_reader_drop_finishes_with_peer_open() {
    let complete = watchdog();
    let (mut peer, receiver) = pair();
    let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    let bytes = bitcode::encode(&vec![1u8, 2, 3]);
    peer.write_all(&(bytes.len() as u32).to_le_bytes()).unwrap();
    peer.write_all(&bytes).unwrap();
    assert_eq!(socket.read().unwrap(), vec![1u8, 2, 3]);
    // Consuming a real frame confirms the reader entered its production loop.
    // Allow it to reach the next idle read, unlike the immediate-Drop smoke test.
    thread::sleep(Duration::from_millis(20));
    eprintln!("primed_drop: phase=reader_primed peer_still_open=true");
    let (done, observed) = mpsc::channel();
    let dropper = thread::spawn(move || {
        drop(socket);
        done.send(()).unwrap();
    });
    let local_completed = observed.recv_timeout(Duration::from_millis(250)).is_ok();
    println!("primed_drop_before_peer_close={local_completed}");
    // Close the peer even after a failed local bound, so the baseline defect
    // can be reported without stranding its thread or hanging this process.
    drop(peer);
    let peer_close_completed =
        local_completed || observed.recv_timeout(Duration::from_secs(2)).is_ok();
    println!("primed_drop_after_peer_close={peer_close_completed}");
    assert!(
        finished(&dropper, Duration::from_secs(1)),
        "dropper remained blocked after peer closed"
    );
    dropper.join().unwrap();
    complete.store(true, Ordering::SeqCst);
    assert!(
        local_completed,
        "one-sided Drop waited for remote FIN after an established idle read"
    );
}

fn read_frame_with_deadline(socket: &mut MessageSocket<Vec<u8>, Vec<u8>>) -> Vec<u8> {
    let until = Instant::now() + Duration::from_secs(2);
    loop {
        match socket.try_read().unwrap() {
            Some(frame) => return frame,
            None => {
                assert!(Instant::now() < until, "valid frame never arrived");
                thread::sleep(Duration::from_millis(1));
            }
        }
    }
}

#[test]
fn healthy_idle_and_partial_frames_outlive_reader_poll_intervals() {
    let complete = watchdog();
    let (mut peer, receiver) = pair();
    let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    assert_eq!(
        socket.socket.get_ref().read_timeout().unwrap(),
        None,
        "readiness polling must not install a receive timeout on the connection"
    );
    assert_eq!(
        socket.socket.get_ref().write_timeout().unwrap(),
        Some(Duration::from_secs(10))
    );
    for _ in 0..6 {
        thread::sleep(Duration::from_millis(200));
        assert!(
            socket.try_read().unwrap().is_none(),
            "healthy idle was mistaken for disconnect"
        );
    }
    let expected: Vec<u8> = (0..1024).map(|i| (i % 251) as u8).collect();
    let bytes = bitcode::encode(&expected);
    let length = (bytes.len() as u32).to_le_bytes();
    peer.write_all(&length[..2]).unwrap();
    thread::sleep(Duration::from_millis(250));
    assert!(socket.try_read().unwrap().is_none());
    peer.write_all(&length[2..]).unwrap();
    peer.write_all(&bytes[..3]).unwrap();
    thread::sleep(Duration::from_millis(250));
    assert!(socket.try_read().unwrap().is_none());
    peer.write_all(&bytes[3..]).unwrap();
    assert_eq!(read_frame_with_deadline(&mut socket), expected);
    // A following frame detects discarded/repeated partial-header/body bytes.
    let next = vec![99u8, 88, 77];
    let bytes = bitcode::encode(&next);
    peer.write_all(&(bytes.len() as u32).to_le_bytes()).unwrap();
    peer.write_all(&bytes).unwrap();
    assert_eq!(read_frame_with_deadline(&mut socket), next);
    drop(peer);
    drop(socket);
    complete.store(true, Ordering::SeqCst);
}

#[test]
fn declared_message_length_limit_still_rejects_before_body_read() {
    let complete = watchdog();
    let (mut peer, receiver) = pair();
    let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    peer.write_all(&(super::MAX_MESSAGE_LEN + 1).to_le_bytes())
        .unwrap();
    assert!(terminal_error(&mut socket).contains("exceeds the maximum"));
    assert!(terminal_error(&mut socket).contains("Channel disconnected"));
    drop(peer);
    drop(socket);
    complete.store(true, Ordering::SeqCst);
}

#[test]
fn cancelling_partial_header_or_body_does_not_wait_for_peer_close() {
    let complete = watchdog();
    for partial in [
        vec![1u8, 2],
        [20u32.to_le_bytes().as_slice(), &[1u8, 2, 3]].concat(),
    ] {
        let (mut peer, receiver) = pair();
        let socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
        peer.write_all(&partial).unwrap();
        thread::sleep(Duration::from_millis(150));
        let (done, observed) = mpsc::channel();
        let dropper = thread::spawn(move || {
            drop(socket);
            done.send(()).unwrap();
        });
        let local_completed = observed.recv_timeout(Duration::from_millis(250)).is_ok();
        drop(peer);
        if !local_completed {
            observed.recv_timeout(Duration::from_secs(2)).unwrap();
        }
        assert!(finished(&dropper, Duration::from_secs(1)));
        dropper.join().unwrap();
        assert!(
            local_completed,
            "partial frame cancellation required remote close"
        );
    }
    complete.store(true, Ordering::SeqCst);
}

#[test]
#[ignore = "explicit std Windows blocking-read semantics probe; use process watchdog"]
fn observe_raw_std_shutdown_during_established_read() {
    let complete = watchdog();
    let (peer, local) = pair();
    let mut reader = local.try_clone().unwrap();
    let (entering, entered) = mpsc::channel();
    let read = thread::spawn(move || {
        entering.send(()).unwrap();
        reader.read(&mut [0u8; 1])
    });
    entered.recv_timeout(Duration::from_secs(1)).unwrap();
    thread::sleep(Duration::from_millis(20));
    let result = local.shutdown(Shutdown::Both);
    println!("raw_shutdown_result={result:?}");
    let local_completed = finished(&read, Duration::from_millis(100));
    println!("raw_read_finished_after_local_shutdown={local_completed}");
    drop(peer);
    let peer_completed = finished(&read, Duration::from_secs(2));
    println!("raw_read_finished_after_peer_close={peer_completed}");
    assert!(peer_completed, "raw read did not finish after peer close");
    println!("raw_read_result={:?}", read.join().unwrap());
    complete.store(true, Ordering::SeqCst);
}

fn terminal_error(socket: &mut MessageSocket<Vec<u8>, Vec<u8>>) -> String {
    let until = Instant::now() + Duration::from_secs(2);
    loop {
        match socket.try_read() {
            Err(error) => return format!("{error:#}"),
            Ok(Some(_)) => panic!("unexpected decoded payload"),
            Ok(None) => {
                assert!(Instant::now() < until, "reader did not observe closed peer");
                thread::sleep(Duration::from_millis(1));
            }
        }
    }
}

#[test]
fn first_receive_failure_precedes_generic_channel_disconnected() {
    for (partial, expected) in [
        (vec![1, 2], "Couldn't receive the length from stream"),
        (
            [20u32.to_le_bytes().as_slice(), &[1, 2, 3]].concat(),
            "Couldn't read message body",
        ),
    ] {
        let (mut sender, receiver) = pair();
        let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
        sender.write_all(&partial).unwrap();
        sender.shutdown(Shutdown::Both).unwrap();
        let first = terminal_error(&mut socket);
        assert!(first.contains(expected), "{first}");
        // The initial useful stream error is consumed, then only the channel's
        // terminal state remains. This observes current behavior, not recovery.
        assert!(terminal_error(&mut socket).contains("Channel disconnected"));
        println!("partial_frame_initial={first}; next=Channel disconnected");
    }
}

#[test]
fn dropping_socket_unblocks_its_idle_reader_thread() {
    let (_sender, receiver) = pair();
    let socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    let start = Instant::now();
    drop(socket);
    assert!(start.elapsed() < Duration::from_secs(2));
    println!(
        "idle_reader_drop_us={:.3}",
        start.elapsed().as_secs_f64() * 1e6
    );
}

fn distribution(values: &[f64]) -> (f64, f64, f64) {
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    (
        sorted[sorted.len() / 2],
        sorted[(sorted.len() * 95 / 100).min(sorted.len() - 1)],
        *sorted.last().unwrap(),
    )
}

/// Aggregate work offered but not consumed includes in-progress write,
/// BufWriter, kernel transport, reader decode and channel storage. It is not a
/// direct measurement of std::mpsc occupancy or process memory.
#[test]
#[ignore = "explicit framed-loopback measurement; bounded stalls and evidence output"]
fn measure_framed_loopback_stall_and_recovery() {
    let debug = std::env::var_os("EW_SOCKET_DEBUG").is_some();
    let selected = std::env::var("EW_SOCKET_CASE").ok();
    let complete = watchdog();
    let output = std::path::PathBuf::from(
        std::env::var_os("EW_SOCKET_BENCH_DIR").expect("set EW_SOCKET_BENCH_DIR"),
    );
    std::fs::create_dir_all(&output).unwrap();
    println!(
        "case,messages,data_bytes,peak_offered_not_consumed_messages,peak_offered_not_consumed_data_bytes,write_median_us,write_p95_us,write_max_us,read_median_us,read_p95_us,read_max_us,age_median_us,age_p95_us,age_max_us,completion_ms,teardown_us"
    );
    // name, messages, data bytes/message, producer batch, producer pause ms,
    // initial consumer stall ms, slow consumer batch, slow consumer duration ms.
    for (name, count, bytes, producer_batch, pause_ms, stall_ms, slow_batch, slow_ms) in [
        (
            "ordinary_paced_512B",
            128usize,
            512usize,
            1usize,
            2u64,
            0u64,
            0usize,
            0u64,
        ),
        ("burst_16MiB_stall_200ms", 4096, 4096, 0, 0, 200, 0, 0),
        (
            "sustained_16MiB_slow_consumer",
            8192,
            2048,
            32,
            1,
            0,
            16,
            150,
        ),
    ] {
        if selected.as_ref().is_some_and(|selected| selected != name) {
            continue;
        }
        if debug {
            eprintln!("{name}: phase=pair_start");
        }
        let (sender, receiver) = pair();
        if debug {
            eprintln!("{name}: phase=pair_connected");
        }
        let mut receiver = MessageSocket::<Sample, Sample>::new(receiver).unwrap();
        if debug {
            eprintln!("{name}: phase=receiver_created");
        }
        let offered = Arc::new(AtomicUsize::new(0));
        let offered_writer = offered.clone();
        let (release, hold) = mpsc::channel();
        let start = Instant::now();
        let writer = thread::spawn(move || {
            if debug {
                eprintln!("{name}: phase=writer_create_start");
            }
            let mut socket = MessageSocket::<Sample, Sample>::new(sender).unwrap();
            if debug {
                eprintln!("{name}: phase=writer_created");
            }
            let mut times = Vec::with_capacity(count);
            for sequence in 0..count {
                let data = (0..bytes)
                    .map(|i| ((sequence * 31 + i) % 251) as u8)
                    .collect();
                let sample = Sample {
                    sequence: sequence as u64,
                    offered_us: start.elapsed().as_micros() as u64,
                    data,
                };
                offered_writer.fetch_add(1, Ordering::SeqCst);
                let before = Instant::now();
                socket.write(&sample).unwrap();
                if producer_batch != 0 && (sequence + 1) % producer_batch == 0 {
                    socket.flush().unwrap();
                }
                times.push(before.elapsed().as_secs_f64() * 1e6);
                if pause_ms != 0 && producer_batch != 0 && (sequence + 1) % producer_batch == 0 {
                    thread::sleep(Duration::from_millis(pause_ms));
                }
            }
            socket.flush().unwrap();
            if debug {
                eprintln!("{name}: phase=writer_flushed_all");
            }
            // Keep the stream open until the consumer finishes so EOF cannot
            // be confused with a missing last frame during recovery.
            hold.recv_timeout(Duration::from_secs(8)).unwrap();
            if debug {
                eprintln!("{name}: phase=writer_released_before_drop");
            }
            times
        });
        thread::sleep(Duration::from_millis(stall_ms));
        if debug {
            eprintln!("{name}: phase=consumer_start");
        }
        let mut received = 0usize;
        let mut peak = offered.load(Ordering::SeqCst);
        let mut reads = Vec::with_capacity(count);
        let mut ages = Vec::with_capacity(count);
        let deadline = start + Duration::from_secs(8);
        while received < count {
            assert!(
                Instant::now() < deadline,
                "{name}: recovery deadline exceeded"
            );
            peak = peak.max(offered.load(Ordering::SeqCst).saturating_sub(received));
            let before = Instant::now();
            match receiver.try_read().unwrap() {
                Some(sample) => {
                    reads.push(before.elapsed().as_secs_f64() * 1e6);
                    assert_eq!(
                        sample.sequence, received as u64,
                        "{name}: ordering/duplication"
                    );
                    assert_eq!(sample.data.len(), bytes);
                    assert!(
                        sample
                            .data
                            .iter()
                            .enumerate()
                            .all(|(i, value)| *value == ((received * 31 + i) % 251) as u8),
                        "{name}: payload corruption"
                    );
                    ages.push(
                        start
                            .elapsed()
                            .as_micros()
                            .saturating_sub(sample.offered_us as u128)
                            as f64,
                    );
                    received += 1;
                    if debug && received % 32 == 0 {
                        eprintln!("{name}: phase=received count={received}");
                    }
                    if slow_batch != 0
                        && received % slow_batch == 0
                        && start.elapsed() < Duration::from_millis(slow_ms)
                    {
                        thread::sleep(Duration::from_millis(1));
                    }
                }
                None => thread::sleep(Duration::from_micros(100)),
            }
        }
        assert_eq!(offered.load(Ordering::SeqCst), count);
        let completion_ms = start.elapsed().as_secs_f64() * 1e3;
        if debug {
            eprintln!("{name}: phase=consumer_received_all");
        }
        let teardown = Instant::now();
        release.send(()).unwrap();
        // Fixture policy: both endpoints close concurrently before joining.
        // This separates delivery measurement from the one-sided idle-Drop
        // defect, which has its own primed regression above.
        if debug {
            eprintln!("{name}: phase=receiver_drop_start_writer_released");
        }
        drop(receiver);
        if debug {
            eprintln!("{name}: phase=receiver_dropped_writer_join_start");
        }
        let writes = writer.join().unwrap();
        if debug {
            eprintln!("{name}: phase=writer_joined");
        }
        let teardown_us = teardown.elapsed().as_secs_f64() * 1e6;
        let (wm, wp, wx) = distribution(&writes);
        let (rm, rp, rx) = distribution(&reads);
        let (am, ap, ax) = distribution(&ages);
        println!(
            "{name},{count},{},{peak},{},{wm:.3},{wp:.3},{wx:.3},{rm:.3},{rp:.3},{rx:.3},{am:.3},{ap:.3},{ax:.3},{completion_ms:.3},{teardown_us:.3}",
            count * bytes,
            peak * bytes
        );
        let mut raw = String::from("index,write_us,read_us,age_us\n");
        for i in 0..count {
            raw.push_str(&format!(
                "{i},{:.3},{:.3},{:.3}\n",
                writes[i], reads[i], ages[i]
            ));
        }
        std::fs::write(output.join(format!("{name}.csv")), raw).unwrap();
    }
    complete.store(true, Ordering::SeqCst);
}

#[path = "message_socket_review_tests.rs"]
mod reviewer;
