use std::{
    io::{BufReader, BufWriter, Read, Write},
    marker::PhantomData,
    net::{SocketAddr, TcpStream},
    sync::mpsc::{self, RecvError, TryRecvError},
    thread::{self, JoinHandle},
    time::Duration,
};

use bitcode::{DecodeOwned, Encode};
use eyre::{Context, bail};
use tracing::info;

#[cfg(windows)]
use std::os::windows::io::AsRawSocket;
#[cfg(windows)]
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
#[cfg(windows)]
use windows_sys::Win32::Networking::WinSock::{
    POLLERR, POLLHUP, POLLNVAL, POLLRDNORM, SOCKET, SOCKET_ERROR, WSAGetLastError, WSAPOLLFD,
    WSAPoll,
};

#[cfg(windows)]
const READER_POLL_MILLISECONDS: i32 = 100;

/// Windows shutdown does not wake a receive that is already blocked. Poll only
/// the reader's local stop request; a healthy stalled frame has no time limit.
#[cfg(windows)]
struct StoppableReader {
    socket: TcpStream,
    stop: Arc<AtomicBool>,
}

#[cfg(windows)]
impl Read for StoppableReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        if buffer.is_empty() {
            return Ok(0);
        }
        loop {
            if self.stop.load(Ordering::Acquire) {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::ConnectionAborted,
                    "Message socket reader stopped",
                ));
            }
            let mut descriptor = WSAPOLLFD {
                fd: self.socket.as_raw_socket() as SOCKET,
                events: POLLRDNORM,
                revents: 0,
            };
            // SAFETY: descriptor is writable for one entry and its socket is
            // owned by this reader. WSAPoll does not retain the pointer.
            let result = unsafe { WSAPoll(&mut descriptor, 1, READER_POLL_MILLISECONDS) };
            if result == SOCKET_ERROR {
                return Err(std::io::Error::from_raw_os_error(unsafe {
                    WSAGetLastError()
                }));
            }
            if self.stop.load(Ordering::Acquire) {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::ConnectionAborted,
                    "Message socket reader stopped",
                ));
            }
            if result == 0 {
                continue;
            }
            if descriptor.revents & POLLNVAL != 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::NotConnected,
                    "Message socket reader has an invalid socket",
                ));
            }
            if descriptor.revents & (POLLRDNORM | POLLHUP) != 0 {
                // This is the only reader. Readiness guarantees that recv
                // returns available bytes/EOF without waiting for a full frame.
                // read_exact above this adapter retains partial-frame offsets.
                return self.socket.read(buffer);
            }
            if descriptor.revents & POLLERR != 0 {
                return Err(self.socket.take_error()?.unwrap_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::ConnectionAborted,
                        "Message socket readiness reported an error",
                    )
                }));
            }
            return Err(std::io::Error::other(format!(
                "Unexpected message socket readiness status {}",
                descriptor.revents
            )));
        }
    }
}

/// Upper bound on a single framed message, to stop a corrupt or hostile length
/// prefix from turning into a multi-gigabyte allocation. The largest legitimate
/// message is a world update (~82 KB, see PIXEL_RUN_MAX in world_sync/world.lua)
/// plus entity-sync batches, so this leaves a very wide margin.
pub const MAX_MESSAGE_LEN: u32 = 64 * 1024 * 1024;

// Reuse only the ordinary frame body allocation. A rare large valid frame must
// not leave a large buffer behind for the lifetime of a healthy connection.
const MAX_RETAINED_READ_CAPACITY: usize = 256 * 1024;

#[derive(Default)]
struct ReadScratch {
    bytes: Vec<u8>,
}

impl ReadScratch {
    fn read_one<T: DecodeOwned>(&mut self, mut buf: impl Read) -> eyre::Result<T> {
        let mut len_buf = [0u8; 4];
        buf.read_exact(&mut len_buf)
            .wrap_err("Couldn't receive the length from stream")?;
        let len = u32::from_le_bytes(len_buf);
        if len > MAX_MESSAGE_LEN {
            bail!("Message length {len} exceeds the maximum of {MAX_MESSAGE_LEN}");
        }
        let len = usize::try_from(len)?;
        // Avoid amortized spare capacity before a body read that may stall.
        // reserve_exact uses the existing length when computing its request.
        if len <= MAX_RETAINED_READ_CAPACITY && self.bytes.capacity() < len {
            self.bytes.reserve_exact(len - self.bytes.len());
        }
        // Be conservative if an allocator still supplies extra capacity.
        if self.bytes.capacity() > MAX_RETAINED_READ_CAPACITY {
            self.bytes = Vec::new();
        }
        let result = if len > MAX_RETAINED_READ_CAPACITY || self.bytes.capacity() < len {
            let mut transient = vec![0; len];
            buf.read_exact(&mut transient)
                .wrap_err("Couldn't read message body")?;
            bitcode::decode(&transient).wrap_err("Failed to decode message body")
        } else {
            self.bytes.resize(len, 0);
            buf.read_exact(&mut self.bytes)
                .wrap_err("Couldn't read message body")?;
            bitcode::decode(&self.bytes).wrap_err("Failed to decode message body")
        };
        // Keep the completed-frame invariant explicit too.
        if self.bytes.capacity() > MAX_RETAINED_READ_CAPACITY {
            self.bytes = Vec::new();
        }
        result
    }
}

pub struct MessageSocket<Inbound, Outbound> {
    socket: BufWriter<TcpStream>,
    recv_messages: mpsc::Receiver<eyre::Result<Inbound>>,
    reader_thread: Option<JoinHandle<()>>,
    #[cfg(windows)]
    reader_stop: Arc<AtomicBool>,
    _phantom: PhantomData<fn() -> Outbound>,
}

impl<Inbound: DecodeOwned + Send + 'static, Outbound: Encode> MessageSocket<Inbound, Outbound> {
    pub fn new(socket: TcpStream) -> eyre::Result<Self> {
        socket.set_write_timeout(Some(Duration::from_secs(10)))?;
        #[cfg(windows)]
        let reader_stop = Arc::new(AtomicBool::new(false));
        // The proxy sets this on its accepted sockets, but the ewext side (which
        // connects) never did - leaving Nagle enabled on the game -> proxy
        // direction, which is the one on the frame-critical path.
        socket.set_nodelay(true)?;
        let (sender, recv_messages) = mpsc::channel();
        let reader_thread = Some(thread::spawn({
            let socket = socket.try_clone()?;
            #[cfg(windows)]
            let socket = StoppableReader {
                socket,
                stop: reader_stop.clone(),
            };
            move || {
                let mut socket = BufReader::new(socket);
                let mut scratch = ReadScratch::default();
                loop {
                    let res = scratch.read_one(&mut socket);
                    let res_was_error = res.is_err();
                    if sender.send(res).is_err() {
                        break;
                    }
                    if res_was_error {
                        break;
                    }
                }
            }
        }));

        Ok(Self {
            // A single world-sync chunk can reach ~82 KB; with the default 8 KB
            // BufWriter capacity every such message went straight to the kernel
            // and could block the game thread. Size the buffer past that.
            socket: BufWriter::with_capacity(256 * 1024, socket),
            recv_messages,
            reader_thread,
            #[cfg(windows)]
            reader_stop,
            _phantom: PhantomData,
        })
    }

    pub fn connect(addr: &SocketAddr) -> eyre::Result<Self> {
        let stream = TcpStream::connect_timeout(addr, Duration::from_secs(1))?;
        Self::new(stream).wrap_err("Failed to wrap socket")
    }

    pub fn read(&mut self) -> eyre::Result<Inbound> {
        match self.recv_messages.recv() {
            Ok(msg) => msg,
            Err(RecvError) => {
                bail!("Channel disconnected");
            }
        }
    }

    pub fn try_read(&mut self) -> eyre::Result<Option<Inbound>> {
        match self.recv_messages.try_recv() {
            Ok(msg) => Some(msg).transpose(),
            Err(TryRecvError::Disconnected) => {
                bail!("Channel disconnected");
            }
            Err(TryRecvError::Empty) => Ok(None),
        }
    }

    // Surely doing a blocking write won't be a problem over a loopback interface.
    pub fn write(&mut self, value: &Outbound) -> eyre::Result<()> {
        let encoded = bitcode::encode(value);
        self.socket
            .write_all(&u32::to_le_bytes(
                u32::try_from(encoded.len()).wrap_err("Message too large to be sent")?,
            ))
            .wrap_err("Couldn't write length to stream")?;
        self.socket
            .write_all(&encoded)
            .wrap_err("Couldn't write message body to stream")?;
        Ok(())
    }

    pub fn flush(&mut self) -> eyre::Result<()> {
        self.socket.flush()?;
        Ok(())
    }
}

impl<Inbound, Outbound> Drop for MessageSocket<Inbound, Outbound> {
    fn drop(&mut self) {
        #[cfg(windows)]
        self.reader_stop.store(true, Ordering::Release);
        self.socket
            .get_mut()
            .shutdown(std::net::Shutdown::Both)
            .ok();
        if let Some(handle) = self.reader_thread.take() {
            handle.join().ok();
        }
        info!("Message socket dropped");
    }
}

#[cfg(test)]
#[path = "message_socket_load_tests.rs"]
mod load_tests;

#[cfg(test)]
mod scratch_tests {
    use super::*;

    fn framed(value: &Vec<u8>) -> Vec<u8> {
        let body = bitcode::encode(value);
        [u32::try_from(body.len()).unwrap().to_le_bytes().as_slice(), &body].concat()
    }

    #[test]
    fn transport_read_scratch_reuses_normal_frames_and_releases_large_temporary() {
        let mut scratch = ReadScratch::default();
        let ordinary: Vec<u8> = (0..82 * 1024).map(|i| (i % 251) as u8).collect();
        let wire = framed(&ordinary);
        assert_eq!(scratch.read_one::<Vec<u8>>(wire.as_slice()).unwrap(), ordinary);
        let pointer = scratch.bytes.as_ptr();
        let capacity = scratch.bytes.capacity();
        for length in [512, 8192, 82 * 1024, 512] {
            let value: Vec<u8> = (0..length).map(|i| ((i + length) % 251) as u8).collect();
            assert_eq!(scratch.read_one::<Vec<u8>>(framed(&value).as_slice()).unwrap(), value);
            assert_eq!(scratch.bytes.as_ptr(), pointer);
            assert_eq!(scratch.bytes.capacity(), capacity);
        }
        let large: Vec<u8> = (0..MAX_RETAINED_READ_CAPACITY * 3).map(|i| (i % 251) as u8).collect();
        assert!(framed(&large).len() > MAX_RETAINED_READ_CAPACITY);
        assert_eq!(scratch.read_one::<Vec<u8>>(framed(&large).as_slice()).unwrap(), large);
        assert_eq!(scratch.bytes.as_ptr(), pointer);
        assert_eq!(scratch.bytes.capacity(), capacity);
        assert!(capacity <= MAX_RETAINED_READ_CAPACITY);
    }

    #[test]
    fn transport_read_scratch_keeps_frame_limit_and_real_partial_errors() {
        let mut scratch = ReadScratch::default();
        let excessive = (MAX_MESSAGE_LEN + 1).to_le_bytes();
        assert!(format!("{:#}",scratch.read_one::<Vec<u8>>(excessive.as_slice()).unwrap_err()).contains("exceeds the maximum"));
        assert!(format!("{:#}",scratch.read_one::<Vec<u8>>(&[1u8,2][..]).unwrap_err()).contains("Couldn't receive the length"));
        let partial = [20u32.to_le_bytes().as_slice(), &[1u8,2,3]].concat();
        assert!(format!("{:#}",scratch.read_one::<Vec<u8>>(partial.as_slice()).unwrap_err()).contains("Couldn't read message body"));
        let valid = framed(&vec![4;512]);
        assert_eq!(scratch.read_one::<Vec<u8>>(valid.as_slice()).unwrap(),vec![4;512]);
        // A cached last byte is not evidence that this new body arrived.
        assert!(format!("{:#}",scratch.read_one::<Vec<u8>>(&valid[..valid.len()-1]).unwrap_err()).contains("Couldn't read message body"));
    }

    #[test]
    fn transport_read_scratch_mixed_growth_is_capped_before_stalled_body_read() {
        use std::sync::mpsc;
        struct StalledBody {
            header: std::io::Cursor<[u8;4]>,
            entered: mpsc::Sender<()>,
            release: mpsc::Receiver<()>,
        }
        impl Read for StalledBody {
            fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
                if self.header.position()<4 {return self.header.read(bytes);}
                self.entered.send(()).unwrap();
                self.release.recv_timeout(Duration::from_secs(2)).unwrap();
                Err(std::io::Error::new(std::io::ErrorKind::WouldBlock,"controlled stalled body"))
            }
        }
        let mut scratch=ReadScratch::default();
        let first:Vec<u8>=(0..170*1024).map(|i|(i%251)as u8).collect();
        assert_eq!(scratch.read_one::<Vec<u8>>(framed(&first).as_slice()).unwrap(),first);
        let later:Vec<u8>=(0..225*1024).map(|i|(i%251)as u8).collect();
        let declared=bitcode::encode(&later).len();
        assert!(declared<=MAX_RETAINED_READ_CAPACITY);
        let (entered,observed)=mpsc::channel();let (release,wait)=mpsc::channel();
        let reader=thread::spawn(move||{
            let source=StalledBody{header:std::io::Cursor::new((declared as u32).to_le_bytes()),entered,release:wait};
            let error=scratch.read_one::<Vec<u8>>(source).unwrap_err();
            (scratch,format!("{error:#}"))
        });
        observed.recv_timeout(Duration::from_secs(1)).unwrap();
        assert!(!reader.is_finished(),"body must be waiting for explicit release");
        release.send(()).unwrap();
        let (scratch,error)=reader.join().unwrap();
        // Error exits before post-decode cleanup: this is the allocation that
        // was held during the controlled body stall, not a later shrink.
        assert!(scratch.bytes.capacity()<=MAX_RETAINED_READ_CAPACITY);
        assert!(error.contains("Couldn't read message body"));
        assert!(error.contains("controlled stalled body"));
    }
}
