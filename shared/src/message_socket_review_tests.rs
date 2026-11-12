//! Independent adjacent-path checks for the actual framed socket.
use super::{pair, read_frame_with_deadline, terminal_error, watchdog};
use crate::message_socket::MessageSocket;
use std::{
    io::{Read, Write},
    net::Shutdown,
    sync::atomic::Ordering,
    thread,
    time::Duration,
};

fn raw_frame(peer: &mut std::net::TcpStream) -> Vec<u8> {
    let mut length = [0; 4];
    peer.read_exact(&mut length).unwrap();
    let mut bytes = vec![0; u32::from_le_bytes(length) as usize];
    peer.read_exact(&mut bytes).unwrap();
    bitcode::decode(&bytes).unwrap()
}

#[test]
fn review_peer_write_half_close_keeps_local_writer_usable() {
    let complete = watchdog();
    let (mut peer, receiver) = pair();
    peer.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
    let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    let expected = vec![10u8, 20, 30];
    let body = bitcode::encode(&expected);
    peer.write_all(&(body.len() as u32).to_le_bytes()).unwrap();
    peer.write_all(&body).unwrap();
    peer.shutdown(Shutdown::Write).unwrap();
    assert_eq!(read_frame_with_deadline(&mut socket), expected);
    let first = terminal_error(&mut socket);
    assert!(
        first.contains("Couldn't receive the length from stream"),
        "{first}"
    );
    assert!(first.contains("failed to fill whole buffer"), "{first}");
    // EOF in the inbound half must not shut down the independent writer.
    let response = vec![7u8, 8, 9, 10];
    socket.write(&response).unwrap();
    socket.flush().unwrap();
    assert_eq!(raw_frame(&mut peer), response);
    assert!(terminal_error(&mut socket).contains("Channel disconnected"));
    drop(peer);
    drop(socket);
    complete.store(true, Ordering::SeqCst);
}

#[test]
fn review_coalesced_frames_after_idle_arrive_before_terminal_eof() {
    let complete = watchdog();
    let (mut peer, receiver) = pair();
    let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    thread::sleep(Duration::from_millis(220));
    assert!(socket.try_read().unwrap().is_none());
    let mut wire = Vec::new();
    for sequence in 0..64u8 {
        let body = bitcode::encode(&vec![sequence; 8]);
        wire.extend((body.len() as u32).to_le_bytes());
        wire.extend(body);
    }
    peer.write_all(&wire).unwrap();
    peer.shutdown(Shutdown::Write).unwrap();
    for sequence in 0..64u8 {
        assert_eq!(read_frame_with_deadline(&mut socket), vec![sequence; 8]);
    }
    assert!(terminal_error(&mut socket).contains("Couldn't receive the length from stream"));
    assert!(terminal_error(&mut socket).contains("Channel disconnected"));
    drop(peer);
    drop(socket);
    complete.store(true, Ordering::SeqCst);
}

#[test]
fn review_outbound_frame_remains_live_during_partial_inbound_body() {
    let complete = watchdog();
    let (mut peer, receiver) = pair();
    peer.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
    let mut socket = MessageSocket::<Vec<u8>, Vec<u8>>::new(receiver).unwrap();
    let expected: Vec<u8> = (0..32768).map(|i| (i % 251) as u8).collect();
    let body = bitcode::encode(&expected);
    peer.write_all(&(body.len() as u32).to_le_bytes()).unwrap();
    peer.write_all(&body[..2]).unwrap();
    thread::sleep(Duration::from_millis(220));
    assert!(socket.try_read().unwrap().is_none());
    let response = vec![77u8, 88, 99];
    socket.write(&response).unwrap();
    socket.flush().unwrap();
    assert_eq!(raw_frame(&mut peer), response);
    peer.write_all(&body[2..]).unwrap();
    assert_eq!(read_frame_with_deadline(&mut socket), expected);
    drop(peer);
    drop(socket);
    complete.store(true, Ordering::SeqCst);
}
