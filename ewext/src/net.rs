use std::{env, net::SocketAddr};

use shared::{NoitaInbound, NoitaOutbound, message_socket::MessageSocket};

pub(crate) struct NetManager {
    socket: Option<MessageSocket<NoitaInbound, NoitaOutbound>>,
    terminal_cause: Option<String>,
}

#[cfg(test)]
mod transport_resource_tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{Shutdown, TcpListener, TcpStream};
    use std::time::Duration;

    fn pair() -> (TcpStream, NetManager) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let peer = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (local, _) = listener.accept().unwrap();
        (peer, NetManager { socket: Some(MessageSocket::new(local).unwrap()), terminal_cause: None })
    }

    fn deliver(peer: &mut TcpStream, message: &NoitaInbound) {
        let bytes = bitcode_frame(message);
        peer.write_all(&bytes).unwrap();
    }

    fn bitcode_frame(message: &NoitaInbound) -> Vec<u8> {
        // Use the exact production writer, since ewext has no direct bitcode dependency.
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let stream = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (mut sink, _) = listener.accept().unwrap();
        let mut sender = MessageSocket::<NoitaOutbound, NoitaInbound>::new(stream).unwrap();
        sender.write(message).unwrap();sender.flush().unwrap();
        let mut length = [0;4];sink.read_exact(&mut length).unwrap();
        let mut body = vec![0;u32::from_le_bytes(length)as usize];sink.read_exact(&mut body).unwrap();
        drop(sender);[length.as_slice(),body.as_slice()].concat()
    }

    #[test]
    fn transport_netmanager_drains_valid_fifo_before_latching_terminal_error() {
        let (mut peer, mut manager) = pair();
        deliver(&mut peer,&NoitaInbound::RawMessage(vec![1,2,3]));
        deliver(&mut peer,&NoitaInbound::RawMessage(vec![4,5,6]));
        peer.write_all(&[20,0,0,0,1,2,3]).unwrap();
        peer.shutdown(Shutdown::Write).unwrap();
        for expected in [vec![1,2,3],vec![4,5,6]] {
            let NoitaInbound::RawMessage(actual)=manager.recv().unwrap()else{panic!("wrong frame")};
            assert_eq!(actual,expected);assert!(!manager.is_failed());
        }
        let original=format!("{:#}",manager.recv().err().expect("terminal frame must fail"));
        assert!(original.contains("Couldn't read message body"),"{original}");
        assert_eq!(manager.terminal_cause(),Some(original.as_str()));
        assert!(manager.is_failed());assert!(manager.socket.is_none());
        for _ in 0..128 {
            let error=manager.try_recv().err().unwrap();
            assert!(error.to_string().contains("connection is closed"));
            assert_eq!(manager.terminal_cause(),Some(original.as_str()));
        }
        assert!(manager.send(&NoitaOutbound::Raw(vec![7])).is_err());
        assert!(manager.flush().is_err());
        peer.set_read_timeout(Some(Duration::from_secs(1))).unwrap();
        assert_eq!(peer.read(&mut[0;1]).unwrap(),0,"failed session wrote new bytes");
    }

    #[test]
    fn transport_netmanager_application_decision_does_not_mark_transport_failed() {
        let (mut peer,mut manager)=pair();
        assert!(manager.try_recv().unwrap().is_none());
        deliver(&mut peer,&NoitaInbound::Ready{my_peer_id:shared::PeerId(3)});
        assert!(matches!(manager.recv().unwrap(),NoitaInbound::Ready{..}));
        // Runtime code may reject an unexpected Ready. Receiving that valid
        // frame is not itself a socket failure and must not poison the adapter.
        assert!(!manager.is_failed());assert_eq!(manager.terminal_cause(),None);
        manager.send(&NoitaOutbound::Raw(vec![8,9])).unwrap();manager.flush().unwrap();
        let mut length=[0;4];peer.read_exact(&mut length).unwrap();
        let mut body=vec![0;u32::from_le_bytes(length)as usize];peer.read_exact(&mut body).unwrap();
        assert!(!body.is_empty());assert!(!manager.is_failed());
    }

    #[derive(Clone, Copy)]
    enum OutputFailure {
        Write,
        Flush,
    }

    fn output_failure_releases_stalled_reader(failure: OutputFailure) {
        use std::sync::mpsc;
        use std::thread;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let mut peer = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (local, _) = listener.accept().unwrap();
        // A real shutdown of only the outgoing half makes the next OS write
        // fail deterministically, without EOF/close from the input producer.
        let write_control = local.try_clone().unwrap();
        let mut manager = NetManager {
            socket: Some(MessageSocket::new(local).unwrap()),
            terminal_cause: None,
        };
        deliver(&mut peer, &NoitaInbound::RawMessage(vec![11, 22, 33]));
        assert!(matches!(manager.recv().unwrap(), NoitaInbound::RawMessage(_)));
        peer.write_all(&[20, 0, 0, 0, 1, 2, 3]).unwrap();
        thread::sleep(Duration::from_millis(150));
        assert!(manager.try_recv().unwrap().is_none());
        assert!(!manager.is_failed());
        write_control.shutdown(Shutdown::Write).unwrap();
        drop(write_control);

        let (complete, observed) = mpsc::channel();
        let operation = thread::spawn(move || {
            let error = match failure {
                OutputFailure::Write => {
                    // Full-range bytes avoid bit packing to a tiny body. The
                    // encoded body exceeds the production 256KiB BufWriter,
                    // forcing send itself through a genuine socket write.
                    let payload = (0..1024 * 1024).map(|i| (i % 256) as u8).collect();
                    manager.send(&NoitaOutbound::Raw(payload)).unwrap_err()
                }
                OutputFailure::Flush => {
                    // Small sends can still enter BufWriter after write-half
                    // shutdown. Only flush reaches the failed OS transport.
                    manager.send(&NoitaOutbound::Raw(vec![44, 55, 66])).unwrap();
                    assert!(!manager.is_failed());
                    manager.flush().unwrap_err()
                }
            };
            // with_socket has already dropped MessageSocket and joined its
            // reader before this result can reach the test's owner.
            assert!(complete.send((manager, error)).is_ok());
        });

        let (mut manager, error) = match observed.recv_timeout(Duration::from_secs(1)) {
            Ok(outcome) => outcome,
            Err(_) => {
                // Always release the still-open producer before reporting a
                // cancellation regression, so the test cannot strand a reader.
                drop(peer);
                let _ = observed.recv_timeout(Duration::from_secs(2));
                operation.join().unwrap();
                panic!("terminal output failure waited for the input producer to close");
            }
        };
        operation.join().unwrap();
        assert!(error.downcast_ref::<std::io::Error>().is_some(), "{error:#}");
        let original = format!("{error:#}");
        if matches!(failure, OutputFailure::Write) {
            assert!(original.contains("Couldn't write message body"), "{original}");
        }
        assert!(!original.contains("connection is closed"), "first cause was replaced");
        assert_eq!(manager.terminal_cause(), Some(original.as_str()));
        assert!(manager.is_failed());
        assert!(manager.socket.is_none());

        for _ in 0..8 {
            let receive = manager.recv().err().expect("closed receive must fail");
            let poll = manager.try_recv().err().expect("closed poll must fail");
            let send = manager.send(&NoitaOutbound::Raw(vec![77])).unwrap_err();
            let flush = manager.flush().unwrap_err();
            for error in [receive, poll, send, flush] {
                assert!(error.to_string().contains("connection is closed"));
            }
            assert_eq!(manager.terminal_cause(), Some(original.as_str()));
        }
        peer.set_read_timeout(Some(Duration::from_secs(1))).unwrap();
        assert_eq!(peer.read(&mut [0; 1]).unwrap(), 0, "failed output reached the peer");
    }

    #[test]
    fn transport_netmanager_write_failure_releases_stalled_reader_and_retains_cause() {
        output_failure_releases_stalled_reader(OutputFailure::Write);
    }

    #[test]
    fn transport_netmanager_flush_failure_releases_stalled_reader_and_retains_cause() {
        output_failure_releases_stalled_reader(OutputFailure::Flush);
    }
}

impl NetManager {
    pub(crate) fn new() -> eyre::Result<Self> {
        let address: SocketAddr = env::var("NP_NOITA_ADDR")
            .ok()
            .and_then(|x| x.parse().ok())
            .unwrap_or_else(|| SocketAddr::new("127.0.0.1".parse().unwrap(), 21251));
        #[cfg(debug_assertions)]
        println!("Connecting to {address:?}");
        let mut stream = std::net::TcpStream::connect(address)?;
        shared::terrain_protocol::negotiate(&mut stream)?;
        let socket = MessageSocket::new(stream)?;

        Ok(NetManager {
            socket: Some(socket),
            terminal_cause: None,
        })
    }

    /// Transport failure ends this connection. The caller owns presentation and
    /// any later explicit connection attempt; failed writes never report success.
    fn with_socket<T>(
        &mut self,
        operation: impl FnOnce(&mut MessageSocket<NoitaInbound, NoitaOutbound>) -> eyre::Result<T>,
    ) -> eyre::Result<T> {
        let Some(socket) = self.socket.as_mut() else {
            eyre::bail!(
                "Local game/proxy connection is closed: {}",
                self.terminal_cause.as_deref().unwrap_or("not connected")
            );
        };
        match operation(socket) {
            Ok(value) => Ok(value),
            Err(error) => {
                self.terminal_cause = Some(format!("{error:#}"));
                // Release its reader and any queued messages only after the
                // transport has failed. Normal receive FIFO is unchanged.
                self.socket.take();
                Err(error)
            }
        }
    }

    pub(crate) fn is_failed(&self) -> bool {
        self.terminal_cause.is_some()
    }

    pub(crate) fn terminal_cause(&self) -> Option<&str> {
        self.terminal_cause.as_deref()
    }

    pub(crate) fn send(&mut self, msg: &NoitaOutbound) -> eyre::Result<()> {
        self.with_socket(|socket| socket.write(msg))
    }

    pub(crate) fn recv(&mut self) -> eyre::Result<NoitaInbound> {
        self.with_socket(MessageSocket::read)
    }

    pub(crate) fn try_recv(&mut self) -> eyre::Result<Option<NoitaInbound>> {
        self.with_socket(MessageSocket::try_read)
    }

    pub(crate) fn flush(&mut self) -> eyre::Result<()> {
        self.with_socket(MessageSocket::flush)
    }
}
