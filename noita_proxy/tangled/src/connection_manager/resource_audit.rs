//! Opt-in production-path resource observations plus focused regression checks.
//! No game, Steam, driver or system networking changes. Coordinator runs serially.
use super::*;
use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
    hint::black_box,
    sync::atomic::{AtomicI64, AtomicU64},
    time::Instant,
};
use tokio::sync::oneshot;

struct AuditAllocator;
static ALLOCATIONS: AtomicU64 = AtomicU64::new(0);
static ALLOCATED_BYTES: AtomicU64 = AtomicU64::new(0);
static LIVE_BYTES: AtomicI64 = AtomicI64::new(0);
static PEAK_BYTES: AtomicI64 = AtomicI64::new(0);
thread_local! {
    static TRACK_8192: Cell<bool> = const { Cell::new(false) };
    static EXACT_8192: Cell<usize> = const { Cell::new(0) };
}

fn account_alloc(size: usize) {
    ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
    ALLOCATED_BYTES.fetch_add(size as u64, Ordering::Relaxed);
    let live = LIVE_BYTES.fetch_add(size as i64, Ordering::Relaxed) + size as i64;
    PEAK_BYTES.fetch_max(live, Ordering::Relaxed);
    if size == 8192 {
        let _ = TRACK_8192.try_with(|enabled| {
            if enabled.get() {
                let _ = EXACT_8192.try_with(|count| count.set(count.get() + 1));
            }
        });
    }
}

unsafe impl GlobalAlloc for AuditAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if !pointer.is_null() { account_alloc(layout.size()); }
        pointer
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc_zeroed(layout) };
        if !pointer.is_null() { account_alloc(layout.size()); }
        pointer
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        LIVE_BYTES.fetch_sub(layout.size() as i64, Ordering::Relaxed);
        unsafe { System.dealloc(pointer, layout) };
    }
    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        let new = unsafe { System.realloc(pointer, layout, size) };
        if !new.is_null() {
            LIVE_BYTES.fetch_sub(layout.size() as i64, Ordering::Relaxed);
            account_alloc(size);
        }
        new
    }
}

#[global_allocator]
static ALLOCATOR: AuditAllocator = AuditAllocator;

fn allocation_snapshot() -> (u64, u64, i64, i64) {
    (ALLOCATIONS.load(Ordering::Relaxed), ALLOCATED_BYTES.load(Ordering::Relaxed),
     LIVE_BYTES.load(Ordering::Relaxed), PEAK_BYTES.load(Ordering::Relaxed))
}

fn exact_tracking(enable: bool) -> usize {
    if enable { EXACT_8192.with(|count| count.set(0)); }
    TRACK_8192.with(|flag| flag.set(enable));
    EXACT_8192.with(Cell::get)
}

fn payload(size: usize, sequence: u64) -> Vec<u8> {
    assert!(size >= 16);
    let mut bytes = vec![0; size];
    let mut state = sequence.wrapping_add(0x632b_e59b_d9b4_e019);
    for byte in &mut bytes {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        *byte = (state >> 19) as u8;
    }
    bytes[..8].copy_from_slice(&sequence.to_le_bytes());
    bytes
}

async fn eventually(timeout: Duration, mut predicate: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if predicate() { return true; }
        tokio::time::sleep(Duration::from_millis(2)).await;
    }
    predicate()
}

#[tokio::test(flavor = "current_thread")]
async fn zero_forwarding_broadcast_moves_payload_to_host_without_copy() {
    let mut manager = ConnectionManager::new(None, None, "127.0.0.1:0".parse().unwrap()).unwrap();
    let shared = manager.shared();
    let data = payload(8192, 17);
    let pointer = data.as_ptr();
    let message = OutboundMessage { src: PeerId(1), dst: Destination::Broadcast,
                                    reliability: Reliability::Reliable, data };
    // Internal handler control: no remote recipient exists. In an ordinary
    // two-peer client broadcast, the source is the only direct peer and is
    // excluded, producing this same zero-forwarding branch.
    exact_tracking(true);
    manager.handle_incoming_message(InternalMessage::Normal(message)).await;
    let payload_allocations = exact_tracking(false);
    let event = shared.inbound_channel.1.try_recv().unwrap();
    let NetworkEvent::Message(message) = event else { panic!("Expected local message") };
    assert_eq!(message.src, PeerId(1));
    assert_eq!(message.data.as_ptr(), pointer);
    assert_eq!(message.data, payload(8192, 17));
    eprintln!("PERF_ZERO_FORWARD payload_bytes=8192 exact_payload_allocations={payload_allocations}");
    assert_eq!(payload_allocations, 0, "Zero-target forwarding must not clone the payload");
}

#[test]
fn payload_allocation_counter_detects_a_real_copy() {
    let source = payload(8192, 91);
    exact_tracking(true);
    let copied = source.clone();
    black_box(copied.as_ptr());
    let count = exact_tracking(false);
    assert_ne!(source.as_ptr(), copied.as_ptr());
    assert_eq!(copied, source);
    assert_eq!(count, 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ordinary_two_peer_client_broadcast_delivers_once_without_echo() {
    // The port is selected with an ephemeral probe, released before the real
    // production bind. A bind race fails rather than reusing another endpoint.
    let probe = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
    let address = probe.local_addr().unwrap();
    drop(probe);
    let host = crate::Peer::host(address, None).unwrap();
    let client = crate::Peer::connect(address, None).unwrap();
    assert!(eventually(Duration::from_secs(5), || client.my_id().is_some()).await);
    assert!(eventually(Duration::from_secs(5), || host.shared.remote_peers.len() == 2).await);
    host.recv().for_each(drop);
    client.recv().for_each(drop);
    let expected = payload(8192, 0);
    client.broadcast(expected.clone(), Reliability::Reliable).unwrap();
    let mut received = Vec::new();
    assert!(eventually(Duration::from_secs(5), || {
        received.extend(host.recv().filter_map(|event| match event {
            NetworkEvent::Message(message) => Some(message), _ => None,
        }));
        !received.is_empty()
    }).await);
    assert_eq!(received.len(), 1);
    assert_eq!(received[0].src, client.my_id().unwrap());
    assert_eq!(received[0].data, expected);
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert!(host.recv().all(|event| !matches!(event, NetworkEvent::Message(_))));
    assert!(client.recv().all(|event| !matches!(event, NetworkEvent::Message(_))));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "Coordinator-only finite actual-QUIC stalled-writer shutdown reproduction"]
async fn audit_drop_last_peer_while_actual_quic_write_is_blocked() {
    let raw = Endpoint::server(default_server_config(), "127.0.0.1:0".parse().unwrap()).unwrap();
    let address = raw.local_addr().unwrap();
    let (header_s, header_r) = oneshot::channel();
    let (release_s, release_r) = oneshot::channel();
    let raw_accept = raw.clone();
    let server = tokio::spawn(async move {
        let connection = raw_accept.accept().await.unwrap().await.unwrap();
        let mut identity = connection.open_uni().await.unwrap();
        identity.write_u16(1).await.unwrap();
        drop(identity);
        let (send, mut receive) = connection.open_bi().await.unwrap();
        let mut send = message_stream::SendMessageStream::new(send);
        // Materialize the stream and perform the real peer identity exchange.
        send.send(&InternalMessage::RemoteConnected(PeerId::HOST)).await.unwrap();
        let length = receive.read_u32().await.unwrap();
        header_s.send(length).ok();
        // Keep the connection alive but do not read the application body.
        // Stock Quinn receive1.25MB/send10MB windows make16MiB genuinely block.
        release_r.await.ok();
        connection.close(0u32.into(), b"bounded audit complete");
    });
    let peer = crate::Peer::connect(address, None).unwrap();
    assert!(eventually(Duration::from_secs(5), || peer.my_id().is_some()).await);
    peer.send(PeerId::HOST, payload(16*1024*1024, 0), Reliability::Reliable).unwrap();
    peer.send(PeerId::HOST, payload(128, 1), Reliability::Reliable).unwrap();
    let length = tokio::time::timeout(Duration::from_secs(5), header_r).await.unwrap().unwrap();
    assert!(length > 10_000_000, "Counterexample must exceed stock send window");
    tokio::time::sleep(Duration::from_millis(150)).await;
    let queued_after_header = peer.shared.audit_outbound_queued.load(Ordering::Relaxed);
    assert_eq!(queued_after_header, 1, "Marker must remain behind a genuinely blocked first write");
    let weak = Arc::downgrade(&peer.shared);
    let started = Instant::now();
    drop(peer);
    let retired = eventually(Duration::from_millis(1500), || weak.strong_count() == 0).await;
    let elapsed = started.elapsed();
    // Always release the intentionally stalled remote before any assertion.
    release_s.send(()).ok();
    raw.close(0u32.into(), b"bounded audit cleanup");
    tokio::time::timeout(Duration::from_secs(2), server).await.ok();
    eprintln!("PERF_STALLED_DROP frame_bytes={length} queued_marker={queued_after_header} retired={retired} elapsed_us={}", elapsed.as_micros());
    assert!(retired, "Final Peer drop retained manager/receiver while QUIC send stayed blocked");
}

#[tokio::test(flavor = "current_thread")]
async fn idle_owner_drop_retires_manager_and_acceptor() {
    let peer = crate::Peer::host("127.0.0.1:0".parse().unwrap(), None).unwrap();
    let weak = Arc::downgrade(&peer.shared);
    tokio::time::sleep(Duration::from_millis(10)).await;
    drop(peer);
    assert!(eventually(Duration::from_millis(1500), || weak.strong_count() == 0).await,
            "Idle manager/acceptor survived explicit owner shutdown");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "Coordinator-only actual-QUIC stalled-writer resume/FIFO countercontrol"]
async fn audit_blocked_quic_write_resumes_fifo_when_remote_reads() {
    let raw = Endpoint::server(default_server_config(), "127.0.0.1:0".parse().unwrap()).unwrap();
    let address = raw.local_addr().unwrap();
    let (header_s, header_r) = oneshot::channel();
    let (resume_s, resume_r) = oneshot::channel();
    let (delivered_s, delivered_r) = oneshot::channel();
    let (close_s, close_r) = oneshot::channel();
    let raw_accept = raw.clone();
    let server = tokio::spawn(async move {
        let connection = raw_accept.accept().await.unwrap().await.unwrap();
        let mut identity = connection.open_uni().await.unwrap();
        identity.write_u16(1).await.unwrap();
        drop(identity);
        let (send, mut receive) = connection.open_bi().await.unwrap();
        let mut send = message_stream::SendMessageStream::new(send);
        send.send(&InternalMessage::RemoteConnected(PeerId::HOST)).await.unwrap();
        let length = receive.read_u32().await.unwrap();
        header_s.send(length).ok();
        resume_r.await.unwrap();
        let mut first = vec![0; length as usize];
        receive.read_exact(&mut first).await.unwrap();
        let InternalMessage::Normal(first): InternalMessage = bitcode::decode(&first).unwrap()
            else { panic!("Expected first application frame") };
        assert_eq!(first.src, PeerId(1));
        assert_eq!(first.data, payload(16*1024*1024, 0));
        let next_len = receive.read_u32().await.unwrap();
        let mut second = vec![0; next_len as usize];
        receive.read_exact(&mut second).await.unwrap();
        let InternalMessage::Normal(second): InternalMessage = bitcode::decode(&second).unwrap()
            else { panic!("Expected second application frame") };
        assert_eq!(second.src, PeerId(1));
        assert_eq!(second.data, payload(128, 1));
        delivered_s.send((length, next_len)).ok();
        close_r.await.ok();
        connection.close(0u32.into(), b"resume control complete");
    });
    let peer = crate::Peer::connect(address, None).unwrap();
    assert!(eventually(Duration::from_secs(5), || peer.my_id().is_some()).await);
    peer.send(PeerId::HOST, payload(16*1024*1024, 0), Reliability::Reliable).unwrap();
    peer.send(PeerId::HOST, payload(128, 1), Reliability::Reliable).unwrap();
    let length = tokio::time::timeout(Duration::from_secs(5), header_r).await.unwrap().unwrap();
    assert!(length > 10_000_000);
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(peer.shared.audit_outbound_queued.load(Ordering::Relaxed), 1);
    resume_s.send(()).unwrap();
    let frames = tokio::time::timeout(Duration::from_secs(5), delivered_r).await.unwrap().unwrap();
    assert!(peer.shared.keep_alive.load(Ordering::Acquire), "No owner shutdown was requested");
    assert!(eventually(Duration::from_secs(1), || peer.shared.audit_outbound_queued.load(Ordering::Relaxed) == 0).await);
    let weak = Arc::downgrade(&peer.shared);
    drop(peer);
    let retired = eventually(Duration::from_millis(1500), || weak.strong_count() == 0).await;
    close_s.send(()).ok();
    raw.close(0u32.into(), b"bounded resume cleanup");
    tokio::time::timeout(Duration::from_secs(2), server).await.ok();
    eprintln!("PERF_STALLED_RESUME first_bytes={} second_bytes={} exact_fifo=true retired={retired}", frames.0, frames.1);
    assert!(retired);
}

#[tokio::test(flavor = "current_thread")]
#[ignore = "Source/API counterexample; no normal production Peer clone call identified"]
async fn audit_clone_drop_observation() {
    let peer = crate::Peer::host("127.0.0.1:0".parse().unwrap(), None).unwrap();
    let before = peer.shared.keep_alive.load(Ordering::Acquire);
    let duplicate = peer.clone();
    drop(duplicate);
    let after = peer.shared.keep_alive.load(Ordering::Acquire);
    eprintln!("PERF_CLONE_DROP before={before} after={after} original_handle_still_alive=true");
    // Observation only: not a candidate success gate or a normal-path claim.
    drop(peer);
    tokio::time::sleep(Duration::from_millis(1100)).await;
}

fn check_payload(data: &[u8], sequence: u64) {
    assert_eq!(u64::from_le_bytes(data[..8].try_into().unwrap()), sequence);
    let mut state = sequence.wrapping_add(0x632b_e59b_d9b4_e019);
    for (index, &byte) in data.iter().enumerate() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        if index >= 16 { assert_eq!(byte, (state >> 19) as u8, "Payload corruption at{index}"); }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "Coordinator-only serial release resource profile, one workload per process"]
async fn audit_two_peer_transport_resources() {
    let workload = std::env::var("EW_PERF_WORKLOAD").unwrap_or_else(|_| "burst".into());
    assert!(["idle", "burst", "sustained", "consumer_stall", "slow_consumer"].contains(&workload.as_str()));
    let direction = std::env::var("EW_PERF_DIRECTION").unwrap_or_else(|_| "client_broadcast".into());
    assert!(["client_broadcast", "host_direct"].contains(&direction.as_str()));
    let size: usize = std::env::var("EW_PERF_BYTES").unwrap_or_else(|_| "8192".into()).parse().unwrap();
    assert!([256, 8192, 65536].contains(&size));
    let budget: usize = std::env::var("EW_PERF_BUDGET").unwrap_or_else(|_| usize::MAX.to_string()).parse().unwrap();
    assert!(budget > 0);
    let count = match workload.as_str() { "idle" => 0, "burst" => 512, "sustained" => 1024, _ => 256 };
    let probe = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
    let address = probe.local_addr().unwrap();
    drop(probe);
    let host = Arc::new(crate::Peer::host(address, None).unwrap());
    let client = Arc::new(crate::Peer::connect(address, None).unwrap());
    assert!(eventually(Duration::from_secs(5), || client.my_id().is_some()).await);
    assert!(eventually(Duration::from_secs(5), || host.shared.remote_peers.len() == 2).await);
    // Connection exchange and idle shutdown timing are outside measured phases.
    tokio::time::sleep(Duration::from_millis(200)).await;
    host.recv().for_each(drop);
    client.recv().for_each(drop);
    let sender = if direction == "client_broadcast" { Arc::clone(&client) } else { Arc::clone(&host) };
    let receiver = if direction == "client_broadcast" { Arc::clone(&host) } else { Arc::clone(&client) };
    let recipient = receiver.my_id().unwrap();
    let offered = Arc::new(AtomicU64::new(0));
    let mut samples = Vec::with_capacity(2048);
    let mut latencies = Vec::with_capacity(count);
    let heap_before = allocation_snapshot();
    let started = Instant::now();
    let source = Arc::clone(&sender);
    let offered_task = Arc::clone(&offered);
    let broadcast = direction == "client_broadcast";
    let paced = workload != "burst";
    let producer = tokio::spawn(async move {
        for sequence in 0..count {
            if paced {
                tokio::time::sleep_until((started + Duration::from_millis(sequence as u64 * 2)).into()).await;
            }
            let mut bytes = payload(size, sequence as u64);
            bytes[8..16].copy_from_slice(&(started.elapsed().as_nanos() as u64).to_le_bytes());
            if broadcast { source.broadcast(bytes, Reliability::Reliable).unwrap(); }
            else { source.send(recipient, bytes, Reliability::Reliable).unwrap(); }
            offered_task.fetch_add(1, Ordering::Relaxed);
        }
    });
    let mut received = 0usize;
    let mut last_sample = Instant::now();
    let mut last_service = Instant::now();
    let mut service_gap_us = 0u128;
    let mut next_slow = started;
    loop {
        let elapsed = started.elapsed();
        let consume = (workload != "consumer_stall" || elapsed >= Duration::from_millis(250))
            && (workload != "slow_consumer" || Instant::now() >= next_slow);
        if consume {
            let take = if workload == "slow_consumer" { 1 } else { budget };
            for event in receiver.recv().take(take) {
                let NetworkEvent::Message(message) = event else { panic!("Unexpected control event inside steady profile") };
                assert_eq!(message.data.len(), size);
                check_payload(&message.data, received as u64);
                let sent_ns = u64::from_le_bytes(message.data[8..16].try_into().unwrap());
                let now_ns = started.elapsed().as_nanos() as u64;
                assert!(sent_ns <= now_ns);
                latencies.push((now_ns - sent_ns) / 1000);
                received += 1;
            }
            if workload == "slow_consumer" { next_slow = Instant::now() + Duration::from_millis(8); }
        }
        let now = Instant::now();
        service_gap_us = service_gap_us.max(now.duration_since(last_service).as_micros());
        last_service = now;
        if now.duration_since(last_sample) >= Duration::from_millis(10) {
            let inbound = receiver.shared.inbound_channel.1.len();
            let outbound = sender.shared.audit_outbound_queued.load(Ordering::Relaxed);
            // Capacity includes transient reserved slots, not an exact internal
            // byte/RSS count. Public queues contain fixed-sized data messages.
            let occupied = 512 - receiver.shared.internal_incoming_messages_s.capacity();
            samples.push((elapsed.as_micros(), offered.load(Ordering::Relaxed), received,
                          inbound, outbound, occupied, LIVE_BYTES.load(Ordering::Relaxed)));
            last_sample = now;
        }
        let done = if workload == "idle" { elapsed >= Duration::from_millis(800) }
                   else { received == count && offered.load(Ordering::Relaxed) == count as u64 };
        if done { break; }
        assert!(elapsed < Duration::from_secs(10), "Bounded profile did not drain");
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    producer.await.unwrap();
    let elapsed_us = started.elapsed().as_micros();
    let heap_after = allocation_snapshot();
    latencies.sort_unstable();
    let percentile = |percent: usize| -> u64 {
        if latencies.is_empty() { 0 } else { latencies[(latencies.len()-1)*percent/100] }
    };
    let peak_inbound = samples.iter().map(|sample| sample.3).max().unwrap_or(0);
    let peak_outbound = samples.iter().map(|sample| sample.4).max().unwrap_or(0);
    eprintln!("PERF_SUMMARY pid={} case={workload} direction={direction} bytes={size} budget={budget} offered={} received={received} elapsed_us={elapsed_us} alloc_ops={} alloc_bytes={} live_before={} live_after={} process_peak_before={} process_peak_after={} peak_inbound_events={peak_inbound} peak_outbound_events={peak_outbound} latency_p50_us={} latency_p95_us={} latency_p99_us={} latency_max_us={} service_gap_max_us={service_gap_us}",
              std::process::id(), offered.load(Ordering::Relaxed), heap_after.0-heap_before.0,
              heap_after.1-heap_before.1, heap_before.2, heap_after.2, heap_before.3, heap_after.3,
              percentile(50), percentile(95), percentile(99), latencies.last().copied().unwrap_or(0));
    for (elapsed, submitted, delivered, inbound, outbound, occupied, live) in samples {
        eprintln!("PERF_SAMPLE elapsed_us={elapsed} offered={submitted} received={delivered} public_inbound_events={inbound} public_inbound_payload_bytes={} public_outbound_events={outbound} public_outbound_payload_bytes={} internal_reserved_or_queued_slots={occupied} process_rust_live_requested_bytes={live}", inbound*size, outbound*size);
    }
    tokio::time::sleep(Duration::from_millis(50)).await;
    assert!(receiver.recv().all(|event| !matches!(event, NetworkEvent::Message(_))), "Unexpected duplicate suffix");
    let weak_host = Arc::downgrade(&host.shared);
    let weak_client = Arc::downgrade(&client.shared);
    drop(sender); drop(receiver); drop(host); drop(client);
    let retired = eventually(Duration::from_secs(3), || weak_host.strong_count()==0 && weak_client.strong_count()==0).await;
    eprintln!("PERF_QUIESCENT peers_retired={retired} rust_live_requested_bytes={}", LIVE_BYTES.load(Ordering::Relaxed));
    assert!(retired, "Ordinary profile left connection owners alive");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "Coordinator-only bounded persistent-process ordinary two-peer lifetime audit"]
async fn audit_repeated_two_peer_lifetimes() {
    let cycles: usize = std::env::var("EW_PERF_LIFETIME_CYCLES")
        .unwrap_or_else(|_| "12".into()).parse().unwrap();
    assert!((1..=24).contains(&cycles));
    let process_started = Instant::now();
    let mut cleanup_times = Vec::with_capacity(cycles);
    let initial = allocation_snapshot();
    eprintln!("PERF_LIFETIME_START pid={} cycles={cycles} scope=ordinary_two_peer_same_process rust_live_requested_bytes={}",
              std::process::id(), initial.2);
    for cycle in 0..cycles {
        assert!(process_started.elapsed() < Duration::from_secs(60), "Bounded lifetime audit exceeded60s");
        let before = allocation_snapshot();
        let connected_at = Instant::now();
        let probe = std::net::UdpSocket::bind("127.0.0.1:0").unwrap();
        let address = probe.local_addr().unwrap();
        drop(probe);
        let host = crate::Peer::host(address, None).unwrap();
        let client = crate::Peer::connect(address, None).unwrap();
        assert!(eventually(Duration::from_secs(5), || client.my_id().is_some()).await);
        assert!(eventually(Duration::from_secs(5), || host.shared.remote_peers.len()==2
            && client.shared.remote_peers.len()==2 && host.shared.direct_peers.len()==1).await);
        let connect_us = connected_at.elapsed().as_micros();
        assert!(host.recv().all(|event| !matches!(event, NetworkEvent::Message(_))));
        assert!(client.recv().all(|event| !matches!(event, NetworkEvent::Message(_))));
        let exchange_at = Instant::now();
        let sent = payload(256, cycle as u64 * 2);
        client.broadcast(sent.clone(), Reliability::Reliable).unwrap();
        let mut host_count = 0;
        assert!(eventually(Duration::from_secs(5), || {
            for event in host.recv() {
                if let NetworkEvent::Message(message) = event {
                    assert_eq!(message.src, client.my_id().unwrap());
                    assert_eq!(message.data, sent);
                    host_count += 1;
                } else { assert!(!matches!(event, NetworkEvent::PeerDisconnected(_))); }
            }
            host_count != 0
        }).await);
        assert_eq!(host_count, 1);
        let replied = payload(256, cycle as u64 * 2 + 1);
        host.send(client.my_id().unwrap(), replied.clone(), Reliability::Reliable).unwrap();
        let mut client_count = 0;
        assert!(eventually(Duration::from_secs(5), || {
            for event in client.recv() {
                if let NetworkEvent::Message(message) = event {
                    assert_eq!(message.src, PeerId::HOST);
                    assert_eq!(message.data, replied);
                    client_count += 1;
                } else { assert!(!matches!(event, NetworkEvent::PeerDisconnected(_))); }
            }
            client_count != 0
        }).await);
        assert_eq!(client_count, 1);
        let exchange_us = exchange_at.elapsed().as_micros();
        tokio::time::sleep(Duration::from_millis(25)).await;
        assert!(host.recv().all(|event| !matches!(event, NetworkEvent::Message(_))));
        assert!(client.recv().all(|event| !matches!(event, NetworkEvent::Message(_))));
        assert_eq!(host.shared.audit_outbound_queued.load(Ordering::Relaxed), 0);
        assert_eq!(client.shared.audit_outbound_queued.load(Ordering::Relaxed), 0);
        // Public handles are not cloned; Arc<Shared> Weak checks observe release
        // of both manager/receive/acceptor ownership trees, not just Drop calls.
        let host_weak = Arc::downgrade(&host.shared);
        let client_weak = Arc::downgrade(&client.shared);
        let cleanup_at = Instant::now();
        drop(host); drop(client); drop(sent); drop(replied);
        let retired = eventually(Duration::from_secs(3), ||
            host_weak.strong_count()==0 && client_weak.strong_count()==0).await;
        let cleanup_us = cleanup_at.elapsed().as_micros();
        cleanup_times.push(cleanup_us);
        assert!(retired, "Ordinary lifetime{cycle} left Shared owners alive");
        drop(host_weak); drop(client_weak);
        // Fixed short quiet interval is identical in baseline/candidate. QUIC/OS
        // driver retirement is not inferred from Shared ownership or heap alone.
        tokio::time::sleep(Duration::from_millis(50)).await;
        let after = allocation_snapshot();
        eprintln!("PERF_LIFETIME cycle={cycle} delivered_host={host_count} delivered_client={client_count} payload_bytes_each=256 connect_us={connect_us} exchange_us={exchange_us} cleanup_us={cleanup_us} shared_retired={retired} rust_live_before={} rust_live_after={} alloc_ops={} alloc_bytes={} process_rust_peak_requested_bytes={} host_remote_entries_at_ready=2 client_remote_entries_at_ready=2 host_direct_entries_at_ready=1",
                  before.2, after.2, after.0-before.0, after.1-before.1, after.3);
    }
    tokio::time::sleep(Duration::from_millis(1200)).await;
    let final_heap = allocation_snapshot();
    cleanup_times.sort_unstable();
    eprintln!("PERF_LIFETIME_SUMMARY cycles={cycles} delivered_each_direction={cycles} all_shared_retired=true elapsed_us={} cleanup_p50_us={} cleanup_max_us={} rust_live_initial={} rust_live_after_final_quiet={} process_rust_peak_requested_bytes={} scope=rust_requested_heap_not_rss_or_native_buffers",
              process_started.elapsed().as_micros(), cleanup_times[(cycles-1)/2], cleanup_times[cycles-1],
              initial.2, final_heap.2, final_heap.3);
}
