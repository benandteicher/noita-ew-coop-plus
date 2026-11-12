//! Local game/proxy protocol. Both binaries must opt in before bitcode traffic.
use std::{
    io::{Read, Write},
    net::TcpStream,
    time::Duration,
};

pub const VERSION: u8 = 2;
pub const MAX_PENDING_CHUNKS: usize = 256;
const MAGIC: &[u8] = b"EW-local-terrain-application-v2\0";

pub fn negotiate(stream: &mut TcpStream) -> eyre::Result<()> {
    // A timeout bounds incompatible/abandoned handshakes, never terrain freshness.
    stream.set_read_timeout(Some(Duration::from_secs(5)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    stream.write_all(MAGIC)?;
    stream.flush()?;
    let mut remote = [0; MAGIC.len()];
    stream.read_exact(&mut remote)?;
    eyre::ensure!(
        &remote == MAGIC,
        "Incompatible local terrain protocol; use matching proxy, ewext and Lua sources"
    );
    stream.set_read_timeout(None)?;
    stream.set_write_timeout(None)?;
    Ok(())
}

/// The only accepted capture shape: the production encoder emits aligned128 chunks.
/// Validate before the legacy parser, which assumes trusted buffer lengths.
pub fn validate_chunk(data: &[u8]) -> eyre::Result<(i32, i32)> {
    eyre::ensure!(data.len() >= 12, "Truncated terrain header");
    let x = i32::from_le_bytes(data[0..4].try_into()?);
    let y = i32::from_le_bytes(data[4..8].try_into()?);
    eyre::ensure!(
        x % 128 == 0 && y % 128 == 0 && data[8] == 127 && data[9] == 127,
        "Terrain capture must be an aligned128 chunk"
    );
    let count = u16::from_le_bytes(data[10..12].try_into()?) as usize;
    eyre::ensure!(
        count > 0 && count <= 16384 && data.len() == 12 + count * 5,
        "Invalid terrain run buffer"
    );
    let mut pixels = 0usize;
    for run in data[12..].chunks_exact(5) {
        pixels += usize::from(u16::from_le_bytes([run[0], run[1]])) + 1;
        eyre::ensure!(run[4] <= 1, "Invalid terrain flags");
    }
    eyre::ensure!(
        pixels == 16384,
        "Terrain runs do not cover exactly one chunk"
    );
    Ok((x / 128, y / 128))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{net::TcpListener, thread};

    #[test]
    fn matching_binary_protocol_completes_before_message_socket() {
        let server = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = server.local_addr().unwrap();
        let peer = thread::spawn(move || {
            let (mut socket, _) = server.accept().unwrap();
            negotiate(&mut socket).unwrap();
            socket.write_all(b"framed-traffic").unwrap();
        });
        let mut socket = TcpStream::connect(address).unwrap();
        negotiate(&mut socket).unwrap();
        assert_eq!(socket.read_timeout().unwrap(), None);
        let mut body = [0; 14];
        socket.read_exact(&mut body).unwrap();
        assert_eq!(&body, b"framed-traffic");
        peer.join().unwrap();
    }

    #[test]
    fn legacy_or_different_binary_protocol_is_rejected() {
        let server = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = server.local_addr().unwrap();
        let peer = thread::spawn(move || {
            let (mut socket, _) = server.accept().unwrap();
            socket.write_all(&[0; MAGIC.len()]).unwrap();
            let mut offered = vec![0; MAGIC.len()];
            socket.read_exact(&mut offered).unwrap();
            assert_eq!(offered, MAGIC);
        });
        let mut socket = TcpStream::connect(address).unwrap();
        assert!(
            negotiate(&mut socket)
                .unwrap_err()
                .to_string()
                .contains("Incompatible local terrain protocol")
        );
        peer.join().unwrap();
    }
}
