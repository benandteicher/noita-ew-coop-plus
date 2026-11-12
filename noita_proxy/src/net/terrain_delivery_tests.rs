//! Actual buffered MessageSocket failure checks around the production writer.
//! Cell application is simulated only after a complete frame has been read;
//! this exercises local TCP delivery/reset adapters, not the native decoder.
use super::*;
use shared::terrain_protocol::validate_chunk;
use std::io::Read;
use std::net::{Shutdown, TcpStream};
use world::TerrainCommand;
use world::world_model::encoding::{Header, PixelRun, RawPixel};

const REQUIRED: [(ChunkCoord, u16); 3] = [
    (ChunkCoord(0, 0), 0),
    (ChunkCoord(1, 0), 3),
    (ChunkCoord(2, 0), 7),
];

struct Peer {
    stream: TcpStream,
    sender_shutdown: TcpStream,
}

fn connect(state: &mut NetInnerState) -> Peer {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let sender = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let (receiver, _) = listener.accept().unwrap();
    receiver.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
    let sender_shutdown = sender.try_clone().unwrap();
    // Same boundary invalidation as a newly accepted production socket.
    state.world.terrain_connection_reset();
    state.ms = Some(MessageSocket::new(sender).unwrap());
    Peer { stream: receiver, sender_shutdown }
}

fn state() -> NetInnerState {
    let world = world::authority_transfer_tests::world();
    let des = DesManager::new(true, world.save_state.clone());
    NetInnerState {
        ms: None,
        world,
        des,
        audio: None,
        explosion_data: Vec::new(),
        had_a_disconnect: false,
        flags: Default::default(),
        local_world_ready:true,initializing_local:false,gated_outbound:Vec::new(),gated_inbound:Vec::new(),gate_bytes:0,gate_started:None,gate_error:None,
    }
}

fn token(command: &TerrainCommand) -> u64 {
    match command {
        TerrainCommand::Apply { token, .. }
        | TerrainCommand::Retire { token, .. }
        | TerrainCommand::Reset { token }
        | TerrainCommand::Pause { token, .. } => *token,
    }
}

fn capture(chunk: ChunkCoord) -> Vec<u8> {
    world::NoitaWorldUpdate {
        header: Header {
            x: chunk.0 * 128,
            y: chunk.1 * 128,
            w: 127,
            h: 127,
            run_count: 1,
        },
        runs: vec![PixelRun {
            length: 16384,
            data: RawPixel { material: 6, flags: 0 },
        }],
    }.save()
}

fn deliver(state: &mut NetInnerState, peer: &mut Peer, commands: &[TerrainCommand]) {
    let expected: Vec<_> = commands.iter().map(|command| {
        let mut bytes = vec![3, 2]; // ws_encode_proxy_bin(2, encoded command)
        bytes.extend(command.encode(state.world.world_number(), state.world.terrain_session()));
        bytes
    }).collect();
    for command in commands {
        let bytes = command.encode(state.world.world_number(), state.world.terrain_session());
        state.try_ms_write(&ws_encode_proxy_bin(2, &bytes));
        assert!(state.ms.is_some(), "healthy loopback command write failed");
    }
    state.flush_local();
    assert!(state.ms.is_some(), "healthy loopback flush failed");
    for expected in expected {
        let mut prefix = [0; 4];
        peer.stream.read_exact(&mut prefix).unwrap();
        let mut frame = vec![0; u32::from_le_bytes(prefix) as usize];
        peer.stream.read_exact(&mut frame).unwrap();
        let actual: NoitaInbound = bitcode::decode(&frame).unwrap();
        match actual {
            NoitaInbound::RawMessage(actual) => assert_eq!(actual, expected),
            _ => panic!("unexpected framed message on terrain delivery socket"),
        }
    }
}

fn prepared() -> (NetInnerState, Peer, Vec<u64>) {
    let mut state = state();
    let mut peer = connect(&mut state);
    state.world.terrain_hello(1, 0).unwrap();
    let startup = state.world.get_terrain_updates().unwrap();
    assert_eq!(startup.len(), 1);
    assert!(matches!(&startup[0], TerrainCommand::Reset { .. }));
    deliver(&mut state, &mut peer, &startup);
    state.world.terrain_ack(token(&startup[0]));
    for (chunk, value) in REQUIRED {
        state.world.handle_msg(OmniPeerId(2), world::WorldNetMessage::GotAuthority {
            chunk, chunk_data: Some(ChunkData::new(value)), priority: 1,
        });
    }
    let applications = state.world.get_terrain_updates().unwrap();
    assert_eq!(applications.len(), REQUIRED.len());
    assert!(applications.iter().all(|command| matches!(command, TerrainCommand::Apply { .. })));
    deliver(&mut state, &mut peer, &applications);
    // Deliberately withhold completion: every obsolete token below names an
    // actual completely received frame, never a buffered or discarded one.
    let delivered_tokens = applications.iter().map(token).collect();
    (state, peer, delivered_tokens)
}

fn assert_disabled(state: &mut NetInnerState) {
    assert!(state.ms.is_none());
    assert!(state.had_a_disconnect);
    assert_eq!(state.world.terrain_session(), 0);
    assert!(state.world.get_terrain_updates().unwrap().is_empty());
    for (chunk, _) in REQUIRED {
        assert!(state.world.add_terrain_update(&capture(chunk)).is_err(),
            "failed socket left game terrain capture enabled");
    }
    assert!(state.world.add_terrain_end(1, &[0]).is_err());
}

fn check_replay(commands: &[TerrainCommand], required: &[(ChunkCoord, u16)]) {
    assert_eq!(commands.len(), required.len() + 1);
    assert!(matches!(commands.first(), Some(TerrainCommand::Reset { .. })));
    let mut seen = FxHashSet::default();
    let mut previous = 0;
    for command in commands {
        assert!(token(command) > previous);
        previous = token(command);
        if let TerrainCommand::Apply { chunk, data, .. } = command {
            let expected = required.iter().find(|(at, _)| at == chunk)
                .expect("replay invented an application for a completed chunk").1;
            assert!(seen.insert(*chunk));
            assert_eq!(validate_chunk(data).unwrap(), (chunk.0, chunk.1));
            let decoded = world::NoitaWorldUpdate::load(data);
            assert!(decoded.runs.iter().all(|run| run.data.material == expected),
                "failed transport lost or changed an exact required target");
        } else {
            assert!(matches!(command, TerrainCommand::Reset { .. }));
        }
    }
    assert_eq!(seen.len(), required.len());
}

fn reconnect_and_complete(state: &mut NetInnerState, required: &[(ChunkCoord, u16)], old: &[u64]) {
    let mut peer = connect(state);
    state.world.terrain_hello(1, 0).unwrap();
    for &obsolete in old {
        state.world.terrain_ack(obsolete);
        state.world.terrain_ack(obsolete);
    }
    let replay = state.world.get_terrain_updates().unwrap();
    check_replay(&replay, required);
    assert!(replay.iter().all(|command| !old.contains(&token(command))));
    deliver(state, &mut peer, &replay);
    state.world.terrain_ack(token(&replay[0])); // Reset only; applications remain pending.
    for &obsolete in old {
        state.world.terrain_ack(obsolete);
    }
    for &(chunk, _) in required {
        state.world.add_terrain_update(&capture(chunk)).unwrap();
    }
    // Another HELLO exposes any application incorrectly unlocked by a stale
    // delivered-prefix ACK; each required target must still replay exactly.
    state.world.terrain_hello(2, 0).unwrap();
    let final_replay = state.world.get_terrain_updates().unwrap();
    check_replay(&final_replay, required);
    deliver(state, &mut peer, &final_replay);
    for command in &final_replay {
        state.world.terrain_ack(token(command));
    }
    assert!(state.world.get_terrain_updates().unwrap().is_empty());
}

#[test]
fn terrain_delivery_buffered_flush_failure_invalidates_tokens_and_replays_required_data() {
    let (mut state, mut peer, delivered_old) = prepared();
    state.world.terrain_hello(2, 0).unwrap();
    let staged = state.world.get_terrain_updates().unwrap();
    check_replay(&staged, &REQUIRED);
    peer.sender_shutdown.shutdown(Shutdown::Both).unwrap();
    for command in &staged {
        state.try_ms_write(&ws_encode_proxy_bin(2,
            &command.encode(state.world.world_number(), state.world.terrain_session())));
    }
    // These small frames fit in MessageSocket's BufWriter even after its
    // underlying socket has failed. The production flush must close/reset it.
    assert!(state.ms.is_some(), "fixture did not isolate a buffered flush failure");
    state.flush_local();
    assert_disabled(&mut state);
    let mut extra = [0; 1];
    assert_eq!(peer.stream.read(&mut extra).unwrap(), 0,
        "an intentionally unflushed command reached the peer");
    reconnect_and_complete(&mut state, &REQUIRED, &delivered_old);
}

#[test]
fn terrain_delivery_write_failure_after_received_prefix_preserves_unsent_batch_tail() {
    let (mut state, mut peer, mut delivered_old) = prepared();
    state.world.terrain_hello(2, 0).unwrap();
    let staged = state.world.get_terrain_updates().unwrap();
    check_replay(&staged, &REQUIRED);
    // Only this Reset/application prefix is really written, read and
    // simulated as applied. The remaining application commands are unsent.
    deliver(&mut state, &mut peer, &staged[..2]);
    for command in &staged[..2] {
        state.world.terrain_ack(token(command));
        delivered_old.push(token(command));
    }
    let completed = match &staged[1] {
        TerrainCommand::Apply { chunk, .. } => *chunk,
        _ => panic!("expected an application after Reset"),
    };
    let remaining: Vec<_> = REQUIRED.iter().copied().filter(|(chunk, _)| *chunk != completed).collect();
    assert_eq!(remaining.len(), 2);
    peer.sender_shutdown.shutdown(Shutdown::Both).unwrap();
    // Incompressible byte coverage keeps bitcode above the 256KiB writer
    // capacity and forces write_all into the shut-down TCP socket now.
    let oversized = NoitaInbound::RawMessage((0..1024 * 1024).map(|n| n as u8).collect());
    assert!(bitcode::encode(&oversized).len() > 256 * 1024);
    state.try_ms_write(&oversized);
    assert_disabled(&mut state);
    reconnect_and_complete(&mut state, &remaining, &delivered_old);
}
