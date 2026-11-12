//! Follow-up reproductions through real WorldManager routing/update paths.
use super::*;
use std::collections::VecDeque;
use world_model::encoding::{Header, PixelRun, RawPixel};

const CHUNK: ChunkCoord = ChunkCoord(0, 0);

struct Network {
    nodes: Vec<WorldManager>,
    queue: VecDeque<(usize, usize, WorldNetMessage)>,
}

impl Network {
    fn new(count: usize) -> Self {
        Self {
            nodes: (0..count)
                .map(|i| {
                    let mut node = super::authority_transfer_tests::world();
                    node.my_peer_id = OmniPeerId(i as u64 + 1);
                    node.is_host = i == 0;
                    node
                })
                .collect(),
            queue: Default::default(),
        }
    }

    fn collect(&mut self, source: usize) {
        for message in self.nodes[source].get_emitted_msgs() {
            assert!(matches!(
                message.reliability,
                tangled::Reliability::Reliable
            ));
            let destinations = match message.dst {
                Destination::Host => vec![0],
                Destination::Peer(id) => {
                    vec![self.nodes.iter().position(|n| n.my_peer_id == id).unwrap()]
                }
                Destination::Peers(ids) => ids
                    .into_iter()
                    .map(|id| self.nodes.iter().position(|n| n.my_peer_id == id).unwrap())
                    .collect(),
                Destination::Broadcast => (0..self.nodes.len()).filter(|i| *i != source).collect(),
            };
            for destination in destinations {
                self.queue
                    .push_back((source, destination, message.msg.clone()));
            }
        }
    }

    fn deliver(&mut self, index: usize) {
        let (source, destination, message) = self.queue.remove(index).unwrap();
        println!("route {}→{} {:?}", source, destination, message);
        let id = self.nodes[source].my_peer_id;
        self.nodes[destination].handle_msg(id, message);
        self.collect(destination);
    }

    // Only selects the oldest item on this sender→receiver stream. Other
    // sender/destination streams may be delayed independently (Steam mesh).
    fn deliver_stream(&mut self, source: usize, destination: usize) {
        let index = self
            .queue
            .iter()
            .position(|m| m.0 == source && m.1 == destination)
            .unwrap();
        self.deliver(index);
    }

    fn drain(&mut self) {
        for _ in 0..128 {
            if self.queue.is_empty() {
                return;
            }
            self.deliver(0);
        }
        panic!("unexpected unbounded routing cycle");
    }

    fn update(&mut self, index: usize) {
        self.nodes[index].update();
        self.collect(index);
    }

    fn sample(&mut self, index: usize, material: u16, priority: u8) {
        self.nodes[index].add_update(NoitaWorldUpdate {
            header: Header {
                x: 0,
                y: 0,
                w: 127,
                h: 127,
                run_count: 1,
            },
            runs: vec![PixelRun {
                length: (CHUNK_SIZE * CHUNK_SIZE) as u32,
                data: RawPixel { material, flags: 0 },
            }],
        });
        self.nodes[index].add_end(priority, &[0, 0, 0, 0, 0, 0]);
        self.collect(index);
    }

    fn arrange(&mut self, owner: usize, next: usize, next_material: u16) {
        let owner_id = self.nodes[owner].my_peer_id;
        let next_id = self.nodes[next].my_peer_id;
        self.nodes[0].authority_map.insert(CHUNK, (owner_id, 8));
        self.nodes[0].chunk_storage.insert(CHUNK, ChunkData::new(4));
        self.nodes[owner]
            .outbound_model
            .apply_chunk_data(CHUNK, &ChunkData::new(6));
        self.nodes[owner].chunk_state.insert(
            CHUNK,
            ChunkState::Authority {
                listeners: [next_id].into_iter().collect(),
                priority: 8,
                new_authority: None,
                stop_sending: false,
            },
        );
        self.nodes[next].chunk_state.insert(
            CHUNK,
            ChunkState::Listening {
                authority: owner_id,
                priority: 8,
            },
        );
        self.nodes[next]
            .inbound_model
            .apply_chunk_data(CHUNK, &ChunkData::new(6));
        self.nodes[next].get_noita_updates();
        // A real local sample drives Listening→WantToGetAuth and LoseAuthority.
        self.sample(next, next_material, 1);
        self.drain();
        assert!(matches!(self.nodes[owner].chunk_state.get(&CHUNK),
            Some(ChunkState::Authority { new_authority: Some((peer, 1)), .. }) if *peer == next_id));
    }

    fn finish(&mut self, next: usize, sample: u16) {
        self.drain();
        self.update(next);
        self.drain();
        // Normal release broadcasts may unload the listening model first;
        // the next game sample then enrolls through normal arbitration.
        if !matches!(
            self.nodes[next].chunk_state.get(&CHUNK),
            Some(ChunkState::Authority { .. })
        ) {
            self.update(next);
            self.sample(next, sample, 1);
            self.update(next);
            self.drain();
        }
    }

    fn assert_final_six(&mut self, next: usize) {
        let data = self.nodes[next]
            .outbound_model
            .get_chunk_data(CHUNK)
            .unwrap();
        println!(
            "final owner_state={:?}, outbound={}, storage={}",
            self.nodes[next].chunk_state.get(&CHUNK),
            material(&data),
            material(self.nodes[0].chunk_storage.get(&CHUNK).unwrap())
        );
        let updates = self.nodes[next].get_noita_updates();
        let materials: Vec<_> = updates
            .iter()
            .flat_map(|b| NoitaWorldUpdate::load(b).runs)
            .map(|r| r.data.material)
            .filter(|m| *m != u16::MAX)
            .collect();
        println!("game-facing materials={materials:?}");
        assert_eq!(
            material(&data),
            6,
            "handoff must not restore the older material4 snapshot"
        );
        assert_eq!(
            material(self.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
            6
        );
        assert!(matches!(
            self.nodes[next].chunk_state.get(&CHUNK),
            Some(ChunkState::Authority { .. })
        ));
        assert!(
            !materials.is_empty(),
            "the final snapshot must reach the game-facing path"
        );
        assert!(materials.iter().all(|m| *m == 6));
    }
}

fn material(data: &ChunkData) -> u16 {
    let mut chunk = Chunk::default();
    data.apply_to_chunk(&mut chunk);
    chunk.pixel(0).material
}

#[test]
fn unload_client_owner_keeps_final_snapshot_instead_of_older_storage() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 6);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    assert!(!network.nodes[1].chunk_state.contains_key(&CHUNK));
    network.finish(0, 6);
    network.assert_final_six(0);
}

#[test]
fn unload_host_owner_keeps_final_snapshot() {
    let mut network = Network::new(2);
    network.arrange(0, 1, 6);
    network.nodes[0].my_pos = (20, 20);
    network.nodes[0].cam_pos = (20, 20);
    network.update(0);
    network.finish(1, 6);
    network.assert_final_six(1);
}

#[test]
fn unload_with_stale_requester_sample_keeps_real_owners_final_snapshot() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 5);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.finish(0, 5);
    network.assert_final_six(0);
}

#[test]
fn characterize_unresolved_three_peer_unload_cross_streams() {
    let mut network = Network::new(3);
    network.arrange(1, 2, 6);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.deliver_stream(1, 2); // Ask can arrive before old→host release.
    network.deliver_stream(2, 0); // New→host request reserves the chunk.
    network.deliver_stream(1, 0); // Old release is later on an independent stream.
    network.finish(2, 6);
    let outbound = material(
        &network.nodes[2]
            .outbound_model
            .get_chunk_data(CHUNK)
            .unwrap(),
    );
    let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
    let game_materials: Vec<_> = network.nodes[2]
        .get_noita_updates()
        .iter()
        .flat_map(|b| NoitaWorldUpdate::load(b).runs)
        .map(|r| r.data.material)
        .filter(|m| *m != u16::MAX)
        .collect();
    println!(
        "UNRESOLVED three-peer cross-stream boundary: outbound={outbound}, storage={stored}, game={game_materials:?}"
    );
    // This is an explicit counterexample to any general terrain-fix claim.
    // The host cannot prove another requester's live Transfer state locally.
    assert_eq!((outbound, stored), (4, 4));
    assert!(!game_materials.is_empty());
    assert!(game_materials.iter().all(|m| *m == 4));
}

#[test]
fn three_peer_release_reaching_host_first_keeps_final_snapshot() {
    let mut network = Network::new(3);
    network.arrange(1, 2, 6);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.finish(2, 6);
    network.assert_final_six(2);
}

#[test]
fn third_listener_resubscribes_after_host_successor_accepts_final_release() {
    let mut network = Network::new(3);
    network.arrange(1, 0, 5);
    let third = network.nodes[2].my_peer_id;
    network.nodes[1].handle_msg(third, WorldNetMessage::ListenRequest { chunk: CHUNK });
    network.collect(1);
    network.drain();
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.finish(0, 5);
    network.assert_final_six(0);
    network.update(2);
    network.sample(2, 6, 4);
    network.update(2);
    network.drain();
    assert_eq!(
        network.nodes[2].chunk_state.get(&CHUNK),
        Some(&ChunkState::Listening {
            authority: network.nodes[0].my_peer_id,
            priority: 1
        })
    );
    assert_eq!(
        material(
            &network.nodes[2]
                .inbound_model
                .get_chunk_data(CHUNK)
                .unwrap()
        ),
        6
    );
}

#[test]
fn offer_sent_before_unload_preserves_final_owner_snapshot() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 5);
    // A previously sampled update may authorize takeover before the owner
    // moves away. Its final release follows on the same old→host stream.
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.finish(0, 5);
    network.assert_final_six(0);
}

#[test]
fn characterize_departure_before_delayed_get_authority_from() {
    let mut network = Network::new(3);
    network.arrange(1, 2, 6);
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.deliver_stream(1, 2); // Ordered permission from old authority.
    network.deliver_stream(2, 0); // Host reserves for the requester.
    assert!(matches!(
        network.nodes[2].chunk_state.get(&CHUNK),
        Some(ChunkState::WaitingForAuthority)
    ));
    let old = network.nodes[1].my_peer_id;
    network.nodes[2].handle_peer_left(old);
    network.nodes[0].handle_peer_left(old);
    network.deliver_stream(0, 2); // A host message can lag the departure event.
    for _ in 0..8 {
        network.nodes[2].update();
    }
    println!(
        "known gap: requester={:?}, host reservation={:?}",
        network.nodes[2].chunk_state.get(&CHUNK),
        network.nodes[0].authority_map.get(&CHUNK)
    );
    assert_eq!(
        network.nodes[2].chunk_state.get(&CHUNK),
        Some(&ChunkState::Transfer { authority: old })
    );
    // This characterizes the unresolved boundary, not successful recovery.
    // No further old-authority reply is possible after its departure.
}

#[test]
fn unload_handoff_then_revisit_uses_newest_stored_snapshot() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 6);
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.finish(0, 6);
    network.assert_final_six(0);
    network.nodes[0].my_pos = (20, 20);
    network.nodes[0].cam_pos = (20, 20);
    network.update(0);
    network.drain();
    network.nodes[1].my_pos = (0, 0);
    network.nodes[1].cam_pos = (0, 0);
    network.sample(1, 6, 1);
    network.update(1);
    network.drain();
    // The release marked this former listener UnloadPending. A sample in
    // that same cleanup cycle is intentionally forgotten; normal periodic
    // sampling in the next cycle must then re-enroll from stored material6.
    assert!(!network.nodes[1].chunk_state.contains_key(&CHUNK));
    assert!(
        network.nodes[1]
            .outbound_model
            .get_chunk_data(CHUNK)
            .is_none()
    );
    assert_eq!(
        material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
        6
    );
    network.sample(1, 6, 1);
    network.update(1);
    network.drain();
    network.assert_final_six(1);
}

#[test]
fn obsolete_owner_release_after_successful_transfer_cannot_replace_snapshot() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 5);
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.drain();
    assert!(matches!(
        network.nodes[0].chunk_state.get(&CHUNK),
        Some(ChunkState::Authority { .. })
    ));
    let old = network.nodes[1].my_peer_id;
    // Fault injection: an obsolete/duplicate terminal snapshot must not be
    // admitted once matching TransferOk has established the new authority.
    network.nodes[0].handle_msg(
        old,
        WorldNetMessage::RelinquishAuthority {
            chunk: CHUNK,
            chunk_data: Some(ChunkData::new(4)),
            world_num: 0,
        },
    );
    network.assert_final_six(0);
}

#[test]
fn wrong_world_release_cannot_complete_pending_transfer() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 5);
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.deliver_stream(1, 0);
    let old = network.nodes[1].my_peer_id;
    network.nodes[0].handle_msg(
        old,
        WorldNetMessage::RelinquishAuthority {
            chunk: CHUNK,
            chunk_data: Some(ChunkData::new(9)),
            world_num: 1,
        },
    );
    assert_eq!(
        network.nodes[0].chunk_state.get(&CHUNK),
        Some(&ChunkState::Transfer { authority: old })
    );
    assert_eq!(
        material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
        4
    );
    network.drain();
    network.assert_final_six(0);
}

#[test]
fn unrelated_owner_release_cannot_complete_pending_host_transfer() {
    let mut network = Network::new(3);
    network.arrange(1, 0, 5);
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.deliver_stream(1, 0);
    let unrelated = network.nodes[2].my_peer_id;
    network.nodes[0].handle_msg(
        unrelated,
        WorldNetMessage::RelinquishAuthority {
            chunk: CHUNK,
            chunk_data: Some(ChunkData::new(9)),
            world_num: 0,
        },
    );
    assert_eq!(
        material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
        4
    );
    network.drain();
    network.assert_final_six(0);
}

#[test]
fn terminal_old_release_cannot_clear_reservation_for_a_different_peer() {
    let mut network = Network::new(3);
    network.arrange(1, 0, 5);
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.deliver_stream(1, 0);
    let old = network.nodes[1].my_peer_id;
    let third = network.nodes[2].my_peer_id;
    assert_eq!(
        network.nodes[0].chunk_state.get(&CHUNK),
        Some(&ChunkState::Transfer { authority: old })
    );
    // Drive the host's ordinary higher-priority reservation path for another
    // authenticated requester before A's final release reaches this host.
    network.nodes[0].handle_msg(
        third,
        WorldNetMessage::RequestAuthority {
            chunk: CHUNK,
            priority: 0,
            can_wait: false,
        },
    );
    network.collect(0);
    assert_eq!(
        network.nodes[0].authority_map.get(&CHUNK),
        Some(&(third, 0))
    );
    network.nodes[1].my_pos = (20, 20);
    network.nodes[1].cam_pos = (20, 20);
    network.update(1);
    network.deliver_stream(1, 0); // Actual terminal Relinquish(snapshot6).
    assert_eq!(
        network.nodes[0].authority_map.get(&CHUNK),
        Some(&(third, 0))
    );
    assert_eq!(
        material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
        4
    );
    assert_eq!(
        network.nodes[0].chunk_state.get(&CHUNK),
        Some(&ChunkState::Transfer { authority: old })
    );
}

#[test]
fn empty_old_release_cannot_complete_pending_host_transfer() {
    let mut network = Network::new(2);
    network.arrange(1, 0, 5);
    network.nodes[1].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(1);
    network.deliver_stream(1, 0);
    let old = network.nodes[1].my_peer_id;
    let host = network.nodes[0].my_peer_id;
    network.nodes[0].handle_msg(
        old,
        WorldNetMessage::RelinquishAuthority {
            chunk: CHUNK,
            chunk_data: None,
            world_num: 0,
        },
    );
    assert_eq!(network.nodes[0].authority_map.get(&CHUNK), Some(&(host, 1)));
    assert_eq!(
        network.nodes[0].chunk_state.get(&CHUNK),
        Some(&ChunkState::Transfer { authority: old })
    );
    assert_eq!(
        material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
        4
    );
    network.drain();
    network.assert_final_six(0);
}

#[test]
fn hosts_own_obsolete_release_after_transfer_ok_is_rejected() {
    let mut network = Network::new(2);
    network.arrange(0, 1, 6);
    network.nodes[0].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(0);
    network.drain();
    let host = network.nodes[0].my_peer_id;
    let client = network.nodes[1].my_peer_id;
    assert_eq!(
        network.nodes[0].chunk_state.get(&CHUNK),
        Some(&ChunkState::UnloadPending)
    );
    assert_eq!(
        network.nodes[0].authority_map.get(&CHUNK),
        Some(&(client, 1))
    );
    network.nodes[0].handle_msg(
        host,
        WorldNetMessage::RelinquishAuthority {
            chunk: CHUNK,
            chunk_data: Some(ChunkData::new(4)),
            world_num: 0,
        },
    );
    assert_eq!(
        network.nodes[0].authority_map.get(&CHUNK),
        Some(&(client, 1))
    );
    network.assert_final_six(1);
}

#[test]
fn host_authority_without_snapshot_still_unloads() {
    let mut network = Network::new(2);
    network.arrange(0, 1, 6);
    network.nodes[0].outbound_model.forget_chunk(CHUNK);
    network.nodes[0].my_pos = (20, 20);
    network.nodes[0].cam_pos = (20, 20);
    network.update(0);
    assert!(!network.nodes[0].chunk_state.contains_key(&CHUNK));
    assert!(
        network.nodes[0]
            .outbound_model
            .get_chunk_data(CHUNK)
            .is_none()
    );
    assert!(!network.nodes[0].authority_map.contains_key(&CHUNK));
}

#[test]
fn review_destruction_during_pending_handoff_survives_unload_in_both_roles() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let mut network = Network::new(2);
        network.arrange(owner, next, 6);
        network.nodes[owner].add_end(8, &[0, 0, 0, 0, 0, 0]);
        network.collect(owner);
        network.deliver_stream(owner, next); // Deliver the real take_auth offer.
        if next != 0 {
            network.deliver_stream(next, 0); // Request reserves the client.
            network.deliver_stream(0, next); // Host grants the transfer attempt.
        }
        let old = network.nodes[owner].my_peer_id;
        assert_eq!(
            network.nodes[next].chunk_state.get(&CHUNK),
            Some(&ChunkState::Transfer { authority: old })
        );
        // Local destruction is legitimate while the owner is waiting for the
        // transfer request. stop_sending retains it for the terminal snapshot.
        network.sample(owner, 0, 8);
        network.nodes[owner].my_pos = (20, 20);
        network.nodes[owner].cam_pos = (20, 20);
        network.update(owner);
        network.finish(next, 6);
        let outbound = material(
            &network.nodes[next]
                .outbound_model
                .get_chunk_data(CHUNK)
                .unwrap(),
        );
        let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
        let game: Vec<_> = network.nodes[next]
            .get_noita_updates()
            .iter()
            .flat_map(|bytes| NoitaWorldUpdate::load(bytes).runs)
            .map(|run| run.data.material)
            .filter(|m| *m != u16::MAX)
            .collect();
        println!(
            "review pending destruction owner={owner} next={next}: outbound={outbound}, stored={stored}, game={game:?}"
        );
        assert_eq!(
            (outbound, stored),
            (0, 0),
            "latest owner destruction must survive either owner role"
        );
        assert!(matches!(
            network.nodes[next].chunk_state.get(&CHUNK),
            Some(ChunkState::Authority { .. })
        ));
        assert!(!game.is_empty() && game.iter().all(|m| *m == 0));
    }
}

#[test]
fn review_obsolete_host_release_cannot_replace_new_clients_legitimate_edits() {
    let mut network = Network::new(3);
    network.arrange(0, 1, 6);
    let third = network.nodes[2].my_peer_id;
    network.nodes[0].handle_msg(third, WorldNetMessage::ListenRequest { chunk: CHUNK });
    network.collect(0);
    network.deliver_stream(0, 2);

    // Reserve B while its request to the actual old host remains delayed.
    network.sample(0, 6, 8);
    network.deliver_stream(0, 1);
    network.deliver_stream(1, 0);
    network.deliver_stream(0, 1);
    assert!(matches!(
        network.nodes[1].chunk_state.get(&CHUNK),
        Some(ChunkState::Transfer { .. })
    ));

    // A genuine priority change cancels H's old offer without changing the
    // already-reserved host map. Returning to priority8 permits a new C offer.
    network.sample(0, 5, 0);
    network.sample(0, 6, 8);
    while network
        .queue
        .iter()
        .any(|message| message.0 == 0 && message.1 == 2)
    {
        network.deliver_stream(0, 2);
    }
    network.sample(2, 6, 0);
    network.deliver_stream(2, 0);
    network.sample(0, 5, 8);
    network.sample(0, 6, 8); // New local edits remain pending behind stop_sending.
    network.deliver_stream(0, 2);
    network.deliver_stream(2, 0);
    network.deliver_stream(0, 2);
    assert_eq!(
        network.nodes[2].chunk_state.get(&CHUNK),
        Some(&ChunkState::Transfer {
            authority: network.nodes[1].my_peer_id
        })
    );

    // B was only reserved, never Authority. Its actual failure takes C through
    // the normal host grant, while H's stale local Authority is still present.
    network.deliver_stream(2, 1);
    network.deliver_stream(1, 2);
    network.deliver_stream(2, 0);
    network.update(2);
    network.deliver_stream(2, 0);
    network.deliver_stream(0, 2);
    assert!(matches!(
        network.nodes[2].chunk_state.get(&CHUNK),
        Some(ChunkState::Authority { .. })
    ));
    assert_eq!(
        material(
            &network.nodes[2]
                .outbound_model
                .get_chunk_data(CHUNK)
                .unwrap()
        ),
        4
    );
    network.nodes[2].get_noita_updates(); // C actually consumes the host grant.
    network.sample(2, 7, 0); // Legitimate simulation after becoming Authority.
    assert_eq!(
        network.nodes[0].authority_map.get(&CHUNK),
        Some(&(third, 0))
    );

    // H's stale local state is insufficient proof to replace C's newer edits.
    network.nodes[0].my_pos = (20, 20);
    network.nodes[0].cam_pos = (20, 20);
    network.update(0);
    network.finish(2, 7);
    let outbound = material(
        &network.nodes[2]
            .outbound_model
            .get_chunk_data(CHUNK)
            .unwrap(),
    );
    let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
    let game: Vec<_> = network.nodes[2]
        .get_noita_updates()
        .iter()
        .flat_map(|bytes| NoitaWorldUpdate::load(bytes).runs)
        .map(|run| run.data.material)
        .filter(|material| *material != u16::MAX)
        .collect();
    println!(
        "review obsolete-host release: C outbound={outbound}, host storage={stored}, game={game:?}"
    );
    assert_eq!(
        outbound, 7,
        "old host snapshot must not overwrite legitimate current-owner edits"
    );
    assert_eq!(
        network.nodes[0].authority_map.get(&CHUNK),
        Some(&(third, 0))
    );
    assert!(
        !game.contains(&6),
        "old host snapshot must not become a new game-facing write"
    );
}

// The rectangle bytes deliberately remain identical for stale and legitimate
// samples. Freshness comes from the production application boundary, never the
// material value or a test-only interpretation of the rectangle.
fn queued_noita_blob(material: u16) -> Vec<u8> {
    queued_noita_blob_at(CHUNK, material)
}

fn queued_noita_blob_at(chunk: ChunkCoord, material: u16) -> Vec<u8> {
    NoitaWorldUpdate {
        header: Header {
            x: chunk.0 * CHUNK_SIZE as i32,
            y: chunk.1 * CHUNK_SIZE as i32,
            w: 127,
            h: 127,
            run_count: 1,
        },
        runs: vec![PixelRun {
            length: (CHUNK_SIZE * CHUNK_SIZE) as u32,
            data: RawPixel { material, flags: 0 },
        }],
    }
    .save()
}

fn terrain_test_blob_pixels(at: ChunkCoord, default: u16, overrides: &[(usize, u16)]) -> Vec<u8> {
    let mut pixels = vec![default; CHUNK_SIZE * CHUNK_SIZE];
    for &(offset, material) in overrides {
        pixels[offset] = material;
    }
    let mut runner = world_model::encoding::PixelRunner::new();
    for material in pixels {
        runner.put_pixel(RawPixel { material, flags: 0 });
    }
    runner.into_noita_update(at.0 * CHUNK_SIZE as i32, at.1 * CHUNK_SIZE as i32, 127, 127).save()
}

fn terrain_test_expanded(blob: &[u8]) -> Vec<u16> {
    NoitaWorldUpdate::load(blob).runs.into_iter()
        .flat_map(|run| std::iter::repeat_n(run.data.material, run.length as usize))
        .collect()
}

fn queued_noita_grant_air_without_game_drain(owner: usize, next: usize) -> Network {
    let mut network = Network::new(2);
    for node in &mut network.nodes {
        node.terrain_hello(1, 0).unwrap();
        // This models a fresh compatible game completing its startup reset,
        // before any terrain is captured. An initial reset is not a chunk ACK.
        for command in node.get_terrain_updates().unwrap() {
            match command {
                TerrainCommand::Reset { token } => node.terrain_ack(token),
                other => panic!("unexpected terrain startup command: {other:?}"),
            }
        }
    }
    network.arrange(owner, next, 6);
    network.nodes[owner].add_end(8, &[0, 0, 0, 0, 0, 0]);
    network.collect(owner);
    network.deliver_stream(owner, next);
    if next != 0 {
        network.deliver_stream(next, 0);
        network.deliver_stream(0, next);
    }
    network.sample(owner, 0, 8);
    network.nodes[owner].my_pos = (20, 20);
    network.nodes[owner].cam_pos = (20, 20);
    network.update(owner);
    network.finish(next, 6);
    assert_eq!(
        material(
            &network.nodes[next]
                .outbound_model
                .get_chunk_data(CHUNK)
                .unwrap()
        ),
        0
    );
    assert_eq!(
        material(
            &network.nodes[next]
                .inbound_model
                .get_chunk_data(CHUNK)
                .unwrap()
        ),
        0
    );
    assert!(
        network.nodes[next]
            .inbound_model
            .updated_chunks()
            .contains(&CHUNK)
    );
    assert_eq!(
        material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()),
        0
    );
    network
}

fn queued_noita_deliver_blob(network: &mut Network, next: usize, bytes: &[u8]) {
    // The exact terrain ingress/end adapters used by net.rs after remote
    // events. These bytes can already be waiting in MessageSocket's channel.
    network.nodes[next].add_terrain_update(bytes).unwrap();
    network.nodes[next]
        .add_terrain_end(1, &[0, 0, 0, 0, 0, 0])
        .unwrap();
    network.collect(next);
}

fn queued_noita_origin_materials(updates: &[TerrainCommand]) -> Vec<u16> {
    updates
        .iter()
        .filter_map(|command| match command {
            TerrainCommand::Apply { data, .. } => Some(data),
            _ => None,
        })
        .map(|bytes| NoitaWorldUpdate::load(bytes))
        .filter(|update| update.header.x == 0 && update.header.y == 0)
        .flat_map(|update| update.runs)
        .map(|run| run.data.material)
        .filter(|material| *material != u16::MAX)
        .collect()
}

fn queued_noita_ack_applications(node: &mut WorldManager, commands: &[TerrainCommand]) {
    for command in commands {
        if let TerrainCommand::Apply { token, .. } = command {
            // The Rust fixture models successful game-side application here.
            // Game-side failure/no-ACK behavior is verified separately in Lua.
            node.terrain_ack(*token);
        }
    }
}

fn queued_noita_unload_egress(network: &mut Network, next: usize) -> Vec<TerrainCommand> {
    // Another actual local frame supplies the far player/camera metadata;
    // changing a fresh far chunk makes add_end consume those positions. This
    // avoids assigning chunk_state or player positions to force the result.
    network.nodes[next]
        .add_terrain_update(&queued_noita_blob_at(ChunkCoord(20, 20), 1))
        .unwrap();
    network.nodes[next]
        .add_terrain_end(1, &[20, 20, 20, 20, 0, 0])
        .unwrap();
    network.collect(next);
    network.drain(); // Flush messages emitted before this update, as net.rs does.
    network.nodes[next].update();
    // Production net.rs calls this egress after update's unload cleanup. No
    // successful application ACK is invented for writes forgotten on unload.
    let returned = network.nodes[next].get_terrain_updates().unwrap();
    assert!(!network.nodes[next].chunk_state.contains_key(&CHUNK));
    assert!(
        network.nodes[next]
            .outbound_model
            .get_chunk_data(CHUNK)
            .is_none()
    );
    // update's newly emitted release is sent on the next proxy iteration.
    network.collect(next);
    network.drain();
    returned
}

fn queued_noita_unload_before_return(network: &mut Network, next: usize) -> Vec<u16> {
    queued_noita_origin_materials(&queued_noita_unload_egress(network, next))
}

#[test]
fn queued_noita_pending_air_drain_then_fresh_feedback_converges() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let old_sample = queued_noita_blob(6); // Captured before the grant.
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        queued_noita_deliver_blob(&mut network, next, &old_sample);
        assert_eq!(
            material(
                &network.nodes[next]
                    .outbound_model
                    .get_chunk_data(CHUNK)
                    .unwrap()
            ),
            0,
            "pre-application sample must stay behind the terrain barrier"
        );
        let writes = network.nodes[next].get_terrain_updates().unwrap();
        let materials = queued_noita_origin_materials(&writes);
        assert!(!materials.is_empty() && materials.iter().all(|m| *m == 0));
        // Positive control: assume the engine applies the serialized air0 and
        // supplies the resulting fresh sample. This is simulated feedback,
        // not an observation of native engine cell application.
        for command in writes {
            if let TerrainCommand::Apply { token, data, .. } = command {
                network.nodes[next].terrain_ack(token);
                queued_noita_deliver_blob(&mut network, next, &data);
            }
        }
        assert_eq!(
            material(
                &network.nodes[next]
                    .outbound_model
                    .get_chunk_data(CHUNK)
                    .unwrap()
            ),
            0
        );
        let after_unload = queued_noita_unload_before_return(&mut network, next);
        let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
        println!(
            "queued feedback positive owner={owner} next={next}: returned={materials:?}, stored_after_fresh_feedback={stored}, later_unload_writes={after_unload:?}"
        );
        assert_eq!(stored, 0);
    }
}

#[test]
fn queued_noita_stale_before_drain_unload_must_preserve_authority_air() {
    let mut results = Vec::new();
    for (owner, next) in [(1, 0), (0, 1)] {
        let old_sample = queued_noita_blob(6); // Produced before GotAuthority0.
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        queued_noita_deliver_blob(&mut network, next, &old_sample);
        let before_unload = material(
            &network.nodes[next]
                .outbound_model
                .get_chunk_data(CHUNK)
                .unwrap(),
        );
        let returned = queued_noita_unload_before_return(&mut network, next);
        let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
        println!(
            "queued-before-drain owner={owner} next={next}: outbound_before_unload={before_unload}, game_return={returned:?}, final_storage={stored}, required_authority_storage=0"
        );
        results.push(stored);
    }
    assert_eq!(
        results,
        vec![0, 0],
        "an older queued sample must not replace final authority air0"
    );
}

#[test]
fn queued_noita_stale_after_drain_unload_must_preserve_authority_air() {
    let mut results = Vec::new();
    for (owner, next) in [(1, 0), (0, 1)] {
        let old_sample = queued_noita_blob(6); // Still produced before the grant.
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        let writes = network.nodes[next].get_terrain_updates().unwrap();
        let returned_air = queued_noita_origin_materials(&writes);
        assert!(!returned_air.is_empty() && returned_air.iter().all(|m| *m == 0));
        // A local frame produced earlier can arrive after this outbound drain;
        // the independent TCP directions have no engine-application barrier.
        queued_noita_deliver_blob(&mut network, next, &old_sample);
        let after_unload = queued_noita_unload_before_return(&mut network, next);
        let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
        println!(
            "queued-after-drain owner={owner} next={next}: prior_game_return={returned_air:?}, later_game_return={after_unload:?}, final_storage={stored}, required_authority_storage=0"
        );
        results.push(stored);
    }
    assert_eq!(
        results,
        vec![0, 0],
        "outbound drain does not establish that later feedback is fresh"
    );
}

#[test]
fn queued_noita_fresh_post_apply_edits_share_the_old_feedback_wire_format() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let old_sample = queued_noita_blob(6);
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        let writes = network.nodes[next].get_terrain_updates().unwrap();
        let returned_air = queued_noita_origin_materials(&writes);
        assert!(!returned_air.is_empty() && returned_air.iter().all(|m| *m == 0));
        // Successful application finishes before the ACK is queued, and then
        // simulation creates material6. The sample bytes still match stale6;
        // only the ordered application boundary distinguishes these histories.
        queued_noita_ack_applications(&mut network.nodes[next], &writes);
        let legitimate_new_edit = queued_noita_blob(6);
        assert_eq!(legitimate_new_edit, old_sample);
        queued_noita_deliver_blob(&mut network, next, &legitimate_new_edit);
        queued_noita_unload_before_return(&mut network, next);
        let stored = material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap());
        println!(
            "fresh legitimate edit control owner={owner} next={next}: byte_identical_old_feedback=true, stored={stored}, required_new_edit_storage=6"
        );
        assert_eq!(
            stored, 6,
            "legitimate post-application simulation must remain possible"
        );
    }
}

fn terrain_test_world(is_host: bool) -> WorldManager {
    let mut node = super::authority_transfer_tests::world();
    node.is_host = is_host;
    node.terrain_hello(1, 0).unwrap();
    for command in node.get_terrain_updates().unwrap() {
        match command {
            TerrainCommand::Reset { token } => node.terrain_ack(token),
            other => panic!("unexpected terrain startup command: {other:?}"),
        }
    }
    node
}

fn terrain_test_grant(node: &mut WorldManager, chunk: ChunkCoord, value: u16) {
    // Drive the production remote authority installation, not the model alone.
    node.handle_msg(
        OmniPeerId(1),
        WorldNetMessage::GotAuthority {
            chunk,
            chunk_data: Some(ChunkData::new(value)),
            priority: 1,
        },
    );
}

fn terrain_test_apply_token(commands: &[TerrainCommand], at: ChunkCoord) -> u64 {
    let tokens: Vec<_> = commands
        .iter()
        .filter_map(|command| match command {
            TerrainCommand::Apply { chunk, token, .. } if *chunk == at => Some(*token),
            _ => None,
        })
        .collect();
    assert_eq!(tokens.len(), 1, "expected one application for {at:?}");
    tokens[0]
}

fn terrain_test_pause_token(commands: &[TerrainCommand], at: ChunkCoord, parent: u64) -> u64 {
    let tokens: Vec<_> = commands.iter().filter_map(|command| match command {
        TerrainCommand::Pause { chunk, token, application } if *chunk == at => {
            assert_eq!(*application, parent);
            Some(*token)
        }
        _ => None,
    }).collect();
    assert_eq!(tokens.len(), 1, "expected one application pause for {at:?}");
    assert!(!commands.iter().any(|command| matches!(command,
        TerrainCommand::Apply { chunk, .. } if *chunk == at)), "replacement escaped before the pause receipt");
    tokens[0]
}

fn terrain_test_sample(node: &mut WorldManager, chunk: ChunkCoord, value: u16) {
    node.add_terrain_update(&queued_noita_blob_at(chunk, value))
        .unwrap();
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
}

fn terrain_test_assert_full(node: &WorldManager, at: ChunkCoord, value: u16) {
    let data = node.outbound_model.get_chunk_data(at).unwrap();
    let mut chunk = Chunk::default();
    data.apply_to_chunk(&mut chunk);
    for pixel in 0..CHUNK_SIZE * CHUNK_SIZE {
        assert_eq!(
            chunk.pixel(pixel).material,
            value,
            "terrain mismatch at chunk {at:?} pixel {pixel}"
        );
    }
}

#[test]
fn queued_noita_old_duplicate_ack_cannot_unlock_a_newer_application() {
    for is_host in [true, false] {
        let mut node = terrain_test_world(is_host);
        terrain_test_grant(&mut node, CHUNK, 0);
        let first = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);

        terrain_test_grant(&mut node, CHUNK, 3);
        // Completion of an earlier application does not complete the newer
        // authority snapshot, even before that newer output is drained.
        node.terrain_ack(first);
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 3);
        let second = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
        assert_ne!(first, second);

        // Duplicate and unrelated ACKs must not open the current barrier.
        node.terrain_ack(first);
        node.terrain_ack(u64::MAX);
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 3);
        node.terrain_ack(second);
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 6);
    }
}

#[test]
fn queued_noita_batched_feedback_only_unlocks_the_acknowledged_chunk() {
    for is_host in [true, false] {
        let mut node = terrain_test_world(is_host);
        let other = ChunkCoord(1, 0);
        terrain_test_grant(&mut node, CHUNK, 0);
        terrain_test_grant(&mut node, other, 3);
        let commands = node.get_terrain_updates().unwrap();
        let first = terrain_test_apply_token(&commands, CHUNK);
        let second = terrain_test_apply_token(&commands, other);
        assert_ne!(first, second);
        node.terrain_ack(first);
        node.add_terrain_update(&queued_noita_blob(6)).unwrap();
        node.add_terrain_update(&queued_noita_blob_at(other, 6)).unwrap();
        node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
        terrain_test_assert_full(&node, CHUNK, 6);
        terrain_test_assert_full(&node, other, 3);

        // Model an incomplete game application: it sends no completion ACK.
        // Drain/time/end markers are not substitutes for that missing ACK.
        for _ in 0..8 {
            node.terrain_ack(first);
            terrain_test_sample(&mut node, other, 6);
            assert!(node.get_terrain_updates().unwrap().is_empty());
            terrain_test_assert_full(&node, other, 3);
        }
        node.terrain_ack(second);
        terrain_test_sample(&mut node, other, 6);
        terrain_test_assert_full(&node, other, 6);
    }
}

#[test]
fn queued_noita_many_successive_grants_never_reuse_an_application_token() {
    let mut node = terrain_test_world(true);
    let mut tokens = std::collections::HashSet::new();
    let mut previous = None;
    for generation in 0..64 {
        let value = if generation % 2 == 0 { 0 } else { 3 };
        terrain_test_grant(&mut node, CHUNK, value);
        let commands = node.get_terrain_updates().unwrap();
        let token = terrain_test_apply_token(&commands, CHUNK);
        assert!(tokens.insert(token), "application token was reused");
        if let Some(old) = previous {
            node.terrain_ack(old);
        }
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, value);
        node.terrain_ack(token);
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 6);
        assert!(node.get_terrain_updates().unwrap().is_empty());
        previous = Some(token);
    }
}

#[test]
fn queued_noita_rapid_unload_revisit_requires_the_new_application_boundary() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        let commands = network.nodes[next].get_terrain_updates().unwrap();
        let old_apply = terrain_test_apply_token(&commands, CHUNK);
        let retirement = queued_noita_unload_egress(&mut network, next);
        let retire_tokens: Vec<_> = retirement
            .iter()
            .filter_map(|command| match command {
                TerrainCommand::Retire { chunk, token } if *chunk == CHUNK => Some(*token),
                _ => None,
            })
            .collect();
        assert_eq!(retire_tokens.len(), 1, "unloaded application needs retirement");
        let retire = retire_tokens[0];
        assert_ne!(old_apply, retire);
        network.nodes[next].terrain_ack(old_apply);
        queued_noita_deliver_blob(&mut network, next, &queued_noita_blob(6));
        assert!(network.nodes[next].outbound_model.get_chunk_data(CHUNK).is_none());
        assert_eq!(material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()), 0);

        // The game retires the old pending application, then ordinary capture
        // revisits the chunk and normal arbitration reinstalls stored air0.
        network.nodes[next].terrain_ack(retire);
        // The release broadcast can leave the releasing role UnloadPending
        // after its first cleanup. Complete that existing transaction cleanup
        // before the next periodic revisit capture, as the retained lifecycle
        // fixture already requires for this authority protocol.
        network.update(next);
        network.drain();
        for command in network.nodes[next].get_terrain_updates().unwrap() {
            match command {
                TerrainCommand::Retire { token, .. } | TerrainCommand::Reset { token } => {
                    network.nodes[next].terrain_ack(token);
                }
                TerrainCommand::Apply { chunk, token, .. } => {
                    assert_ne!(chunk, CHUNK, "retired origin retained a required application");
                    network.nodes[next].terrain_ack(token);
                }
                TerrainCommand::Pause { .. } => panic!("unexpected paused application during cleanup"),
            }
        }
        queued_noita_deliver_blob(&mut network, next, &queued_noita_blob(6));
        network.update(next);
        network.drain();
        terrain_test_assert_full(&network.nodes[next], CHUNK, 0);
        let commands = network.nodes[next].get_terrain_updates().unwrap();
        let new_apply = terrain_test_apply_token(&commands, CHUNK);
        assert_ne!(new_apply, old_apply);
        assert_ne!(new_apply, retire);
        network.nodes[next].terrain_ack(retire);
        network.nodes[next].terrain_ack(old_apply);
        queued_noita_deliver_blob(&mut network, next, &queued_noita_blob(6));
        terrain_test_assert_full(&network.nodes[next], CHUNK, 0);
        network.nodes[next].terrain_ack(new_apply);
        queued_noita_deliver_blob(&mut network, next, &queued_noita_blob(6));
        terrain_test_assert_full(&network.nodes[next], CHUNK, 6);
        queued_noita_unload_before_return(&mut network, next);
        assert_eq!(material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()), 6);
    }
}

#[test]
fn queued_noita_repeated_unload_handoffs_between_both_roles_keep_latest_terrain() {
    for (owner, mut current) in [(1, 0), (0, 1)] {
        let mut network = queued_noita_grant_air_without_game_drain(owner, current);
        let mut seen_tokens = std::collections::HashSet::new();
        let mut obsolete: [Vec<u64>; 2] = [Vec::new(), Vec::new()];
        let mut expected = 0;
        for cycle in 0..6 {
            let commands = network.nodes[current].get_terrain_updates().unwrap();
            let token = terrain_test_apply_token(&commands, CHUNK);
            assert!(seen_tokens.insert((current, token)));
            for command in &commands {
                if let TerrainCommand::Retire { token, .. } | TerrainCommand::Reset { token } = command {
                    network.nodes[current].terrain_ack(*token);
                }
            }
            for old in &obsolete[current] {
                network.nodes[current].terrain_ack(*old);
            }
            queued_noita_deliver_blob(&mut network, current, &queued_noita_blob(6));
            terrain_test_assert_full(&network.nodes[current], CHUNK, expected);
            network.nodes[current].terrain_ack(token);
            // Actual post-application simulation alternates removal/material.
            expected = if cycle % 2 == 0 { 6 } else { 0 };
            queued_noita_deliver_blob(&mut network, current, &queued_noita_blob(expected));
            terrain_test_assert_full(&network.nodes[current], CHUNK, expected);
            let retirement = queued_noita_unload_egress(&mut network, current);
            assert_eq!(material(network.nodes[0].chunk_storage.get(&CHUNK).unwrap()), expected);
            for command in retirement {
                match command {
                    TerrainCommand::Retire { token, .. } | TerrainCommand::Reset { token } => {
                        network.nodes[current].terrain_ack(token);
                        obsolete[current].push(token);
                    }
                    TerrainCommand::Apply { chunk, token, .. } => {
                        assert_ne!(chunk, CHUNK, "origin re-applied after unload");
                        network.nodes[current].terrain_ack(token);
                    }
                    TerrainCommand::Pause { .. } => panic!("unexpected paused application after unload"),
                }
            }
            obsolete[current].push(token);
            current = 1 - current;
            // Cleanup the former owner's normal UnloadPending before its
            // next periodic capture; no chunk state is assigned by the test.
            network.update(current);
            network.drain();
            for command in network.nodes[current].get_terrain_updates().unwrap() {
                match command {
                    TerrainCommand::Retire { token, .. } | TerrainCommand::Reset { token } => {
                        network.nodes[current].terrain_ack(token);
                    }
                    TerrainCommand::Apply { chunk, token, .. } => {
                        assert_ne!(chunk, CHUNK, "retired origin retained an application");
                        // A separate far metadata chunk may legitimately have
                        // a pending listener write from normal arbitration.
                        network.nodes[current].terrain_ack(token);
                    }
                    TerrainCommand::Pause { .. } => panic!("unexpected paused application during cleanup"),
                }
            }
            queued_noita_deliver_blob(&mut network, current, &queued_noita_blob(6));
            network.update(current);
            network.drain();
            terrain_test_assert_full(&network.nodes[current], CHUNK, expected);
        }
    }
}

#[test]
fn queued_noita_world_reset_is_observed_even_when_all_samples_are_blocked() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 0);
    let old = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
    node.add_terrain_update(&queued_noita_blob(6)).unwrap();
    node.add_terrain_end(1, &[20, 20, 19, 19, 1, 1]).unwrap();
    assert_eq!(node.world_num, 1);
    assert_eq!(node.my_pos, (20, 20));
    assert_eq!(node.cam_pos, (19, 19));
    assert!(node.is_notplayer);
    assert!(node.outbound_model.get_chunk_data(CHUNK).is_none());
    let commands = node.get_terrain_updates().unwrap();
    let resets: Vec<_> = commands.iter().filter_map(|command| match command {
        TerrainCommand::Reset { token } => Some(*token),
        _ => None,
    }).collect();
    assert_eq!(resets.len(), 1, "world reset must establish a new stream boundary");
    assert_ne!(resets[0], old);
    node.terrain_ack(old);
    node.add_terrain_update(&queued_noita_blob(6)).unwrap();
    node.add_terrain_end(1, &[1]).unwrap();
    assert!(node.outbound_model.get_chunk_data(CHUNK).is_none());
    node.terrain_ack(resets[0]);
    node.add_terrain_update(&queued_noita_blob(6)).unwrap();
    node.add_terrain_end(1, &[1]).unwrap();
    terrain_test_assert_full(&node, CHUNK, 6);
}

#[test]
fn queued_noita_disconnect_requires_a_new_compatible_game_reset() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 0);
    let old = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
    node.terrain_connection_reset();
    assert!(node.add_terrain_update(&queued_noita_blob(6)).is_err());
    node.terrain_hello(1, 0).unwrap();
    let commands = node.get_terrain_updates().unwrap();
    let resets: Vec<_> = commands.iter().filter_map(|command| match command {
        TerrainCommand::Reset { token } => Some(*token),
        _ => None,
    }).collect();
    assert_eq!(resets.len(), 1);
    assert_ne!(resets[0], old);
    node.terrain_ack(old);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 0);
    // Reconnection cannot silently forget the still-required terrain write.
    node.terrain_ack(resets[0]);
    let mut applications: Vec<_> = commands.into_iter().filter(|command| {
        matches!(command, TerrainCommand::Apply { .. })
    }).collect();
    applications.extend(node.get_terrain_updates().unwrap());
    let new_apply = terrain_test_apply_token(&applications, CHUNK);
    assert_ne!(new_apply, old);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 0);
    node.terrain_ack(new_apply);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 6);
}

#[test]
fn queued_noita_malformed_or_unaligned_capture_never_mutates_terrain() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 0);
    let commands = node.get_terrain_updates().unwrap();
    queued_noita_ack_applications(&mut node, &commands);
    let good = queued_noita_blob(6);
    let mut bad_packets = vec![Vec::new(), good[..11].to_vec(), good[..good.len()-1].to_vec()];
    let mut trailing = good.clone();
    trailing.push(0);
    bad_packets.push(trailing);
    let mut short = NoitaWorldUpdate::load(&good);
    short.header.w = 0;
    short.header.h = 0;
    bad_packets.push(short.save());
    let mut crossing = NoitaWorldUpdate::load(&good);
    crossing.header.x = 1;
    bad_packets.push(crossing.save());
    let mut incomplete = NoitaWorldUpdate::load(&good);
    incomplete.runs[0].length -= 1;
    bad_packets.push(incomplete.save());
    for packet in bad_packets {
        assert!(node.add_terrain_update(&packet).is_err(), "invalid capture accepted");
        terrain_test_assert_full(&node, CHUNK, 0);
    }
}

#[test]
fn queued_noita_sparse_inbound_delta_preserves_queued_edits_outside_its_mask_on_unload() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        let commands = network.nodes[next].get_terrain_updates().unwrap();
        queued_noita_ack_applications(&mut network.nodes[next], &commands);
        queued_noita_deliver_blob(&mut network, next, &queued_noita_blob(6));
        terrain_test_assert_full(&network.nodes[next], CHUNK, 6);

        let mut delta_model = WorldModel::default();
        delta_model.apply_chunk_data(CHUNK, &ChunkData::new(6));
        delta_model.reset_change_tracking();
        delta_model.apply_noita_update(
            &NoitaWorldUpdate {
                header: Header { x: 0, y: 0, w: 0, h: 0, run_count: 1 },
                runs: vec![PixelRun { length: 1, data: RawPixel { material: 0, flags: 0 } }],
            },
            &mut FxHashSet::default(),
        );
        let delta = delta_model.get_chunk_delta(CHUNK, false).unwrap();
        // Controlled inbound-delta injection into an existing Authority.
        // This exercises the production pending-mask/capture adapter without
        // pretending a remote-listener message is a valid authority transaction.
        network.nodes[next].inbound_model.apply_chunk_delta(&delta);
        network.nodes[next].outbound_model.mirror_chunk_delta(&delta);
        let mut queued = NoitaWorldUpdate::load(&queued_noita_blob(6));
        queued.runs = vec![
            PixelRun { length: 1, data: RawPixel { material: 6, flags: 0 } },
            PixelRun { length: 1, data: RawPixel { material: 7, flags: 0 } },
            PixelRun { length: (CHUNK_SIZE * CHUNK_SIZE - 2) as u32, data: RawPixel { material: 6, flags: 0 } },
        ];
        queued_noita_deliver_blob(&mut network, next, &queued.save());
        let outbound = network.nodes[next].outbound_model.get_chunk_data(CHUNK).unwrap();
        let mut chunk = Chunk::default();
        outbound.apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 0, "queued stale cell defeated sparse remote removal");
        assert_eq!(chunk.pixel(1).material, 7, "whole-chunk gating lost an unrelated legitimate edit");
        for offset in 2..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, 6);
        }
        // No game ACK is provided before unload: only the protected cell may
        // be held back, and both required0 and the independent edit7 persist.
        queued_noita_unload_before_return(&mut network, next);
        let stored = network.nodes[0].chunk_storage.get(&CHUNK).unwrap();
        stored.apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 0);
        assert_eq!(chunk.pixel(1).material, 7);
        for offset in 2..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, 6);
        }
    }
}

#[test]
fn queued_noita_multichunk_retirements_encode_in_monotonic_game_stream_order() {
    let mut node = terrain_test_world(true);
    // Retirements are assigned serials before hash-map serialization. A game
    // decoder with one stream floor must never receive those out of order.
    for i in 0..32 {
        node.terrain.retire(ChunkCoord(i, -i));
    }
    let commands = node.get_terrain_updates().unwrap();
    assert_eq!(commands.len(), 32);
    let mut previous = 0;
    for command in commands {
        let wire = command.encode(node.world_number(), node.terrain_session());
        assert_eq!(wire.len(), 25);
        assert_eq!(wire[0], 1);
        let token = u64::from_le_bytes(wire[1..9].try_into().unwrap());
        assert!(token > previous, "terrain command hash order defeated game token floor");
        assert_eq!(i32::from_le_bytes(wire[9..13].try_into().unwrap()), 0);
        assert_eq!(u32::from_le_bytes(wire[13..17].try_into().unwrap()), 1);
        previous = token;
    }
}

#[test]
fn queued_noita_same_socket_new_session_replays_required_write_after_new_reset() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 0);
    let old = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
    node.terrain_hello(2, 0).unwrap();
    let commands = node.get_terrain_updates().unwrap();
    let resets: Vec<_> = commands.iter().filter_map(|command| match command {
        TerrainCommand::Reset { token } => Some(*token),
        _ => None,
    }).collect();
    assert_eq!(resets.len(), 1);
    let reset = resets[0];
    let new_apply = terrain_test_apply_token(&commands, CHUNK);
    assert!(old < reset && reset < new_apply, "new-session stream floor would reject replayed terrain");
    let mut previous = 0;
    for command in &commands {
        let wire = command.encode(node.world_number(), node.terrain_session());
        assert_eq!(u32::from_le_bytes(wire[13..17].try_into().unwrap()), 2);
        let token = u64::from_le_bytes(wire[1..9].try_into().unwrap());
        assert!(previous < token);
        previous = token;
    }
    node.terrain_ack(old);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 0);
    node.terrain_ack(reset);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 0);
    node.terrain_ack(new_apply);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 6);
}

#[test]
fn queued_noita_pending_capacity_is_explicit_and_preserves_every_required_chunk() {
    let mut node = terrain_test_world(true);
    for x in 0..256 {
        terrain_test_grant(&mut node, ChunkCoord(x, 0), 0);
    }
    let commands = node.get_terrain_updates().unwrap();
    assert_eq!(commands.len(), 256);
    let release = terrain_test_apply_token(&commands, CHUNK);
    let extra = ChunkCoord(256, 0);
    terrain_test_grant(&mut node, extra, 3);
    assert!(node.get_terrain_updates().unwrap().is_empty(), "capacity pressure must retain waiting terrain without closing the socket");
    terrain_test_assert_full(&node, extra, 3);
    assert!(node.inbound_model.updated_chunks().contains(&extra));
    // Existing work stays deliverable under bounded backpressure. A receipt
    // for an actually delivered application frees one slot without evicting
    // waiting terrain or re-emitting the other applications.
    node.terrain_ack(release);
    let next = node.get_terrain_updates().unwrap();
    assert_eq!(next.len(), 1);
    let token = terrain_test_apply_token(&next, extra);
    assert_ne!(token, release);
    let data = match &next[0] {
        TerrainCommand::Apply { data, .. } => data,
        other => panic!("required chunk was replaced by control {other:?}"),
    };
    let update = NoitaWorldUpdate::load(data);
    assert!(update.runs.iter().all(|run| run.data.material == 3));
}

#[test]
fn queued_noita_departed_listener_authority_cannot_orphan_a_pending_application() {
    for wanting_authority in [false, true] {
        let mut node = terrain_test_world(true);
        let old = OmniPeerId(2);
        node.handle_msg(old, WorldNetMessage::ListenInitialResponse {
            chunk: CHUNK,
            chunk_data: Some(ChunkData::new(6)),
            priority: 8,
        });
        let initial = node.get_terrain_updates().unwrap();
        let expected;
        let apply;
        if wanting_authority {
            queued_noita_ack_applications(&mut node, &initial);
            terrain_test_sample(&mut node, CHUNK, 7);
            assert!(matches!(node.chunk_state.get(&CHUNK), Some(ChunkState::WantToGetAuth { .. })));
            let mut source = WorldModel::default();
            source.apply_chunk_data(CHUNK, &ChunkData::new(0));
            node.handle_msg(old, WorldNetMessage::ListenUpdate {
                delta: source.get_chunk_delta(CHUNK, true).unwrap(),
                priority: 8,
                take_auth: false,
            });
            assert!(matches!(node.chunk_state.get(&CHUNK), Some(ChunkState::WantToGetAuth { .. })));
            apply = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
            expected = 0;
        } else {
            apply = terrain_test_apply_token(&initial, CHUNK);
            expected = 6;
        }
        // Model a pending unloaded game application: no completion ACK exists.
        node.handle_peer_left(old);
        assert!(node.chunk_state.contains_key(&CHUNK), "peer departure orphaned the terrain flight");
        terrain_test_assert_full(&node, CHUNK, expected);
        node.add_terrain_end(1, &[20, 20, 20, 20, 0, 0]).unwrap();
        // The first ordinary update re-arbitrates the cached chunk through
        // host loopback; the next unloads/persists it outside local interest.
        node.update();
        node.update();
        assert!(!node.chunk_state.contains_key(&CHUNK));
        assert!(node.outbound_model.get_chunk_data(CHUNK).is_none());
        let stored = node.chunk_storage.get(&CHUNK).expect("departed authority cache was lost");
        let mut chunk = Chunk::default();
        stored.apply_to_chunk(&mut chunk);
        for offset in 0..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, expected);
        }
        let commands = node.get_terrain_updates().unwrap();
        let retire: Vec<_> = commands.iter().filter_map(|command| match command {
            TerrainCommand::Retire { chunk, token } if *chunk == CHUNK => Some(*token),
            _ => None,
        }).collect();
        assert_eq!(retire.len(), 1, "departed authority application was not retired");
        assert!(retire[0] > apply);
        node.terrain_ack(apply);
        terrain_test_sample(&mut node, CHUNK, 7);
        assert!(node.outbound_model.get_chunk_data(CHUNK).is_none(), "late ACK reopened retired chunk");
    }
}

#[test]
fn queued_noita_retirement_capacity_cannot_discard_a_required_unloaded_chunk() {
    let mut node = terrain_test_world(true);
    // Seed the host's matching ownership map as well as each received grant,
    // so ordinary terminal releases persist their real snapshots at unload.
    for x in 0..256 {
        let at = ChunkCoord(x, 0);
        node.authority_map.insert(at, (node.my_peer_id, 1));
        terrain_test_grant(&mut node, at, 0);
    }
    let commands = node.get_terrain_updates().unwrap();
    assert_eq!(commands.len(), 256);
    let release = terrain_test_apply_token(&commands, CHUNK);
    let extra = ChunkCoord(1000, 1000);
    node.authority_map.insert(extra, (node.my_peer_id, 1));
    terrain_test_grant(&mut node, extra, 3);
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    node.update();

    // Retirement backpressure must retain the required bytes and
    // UnloadPending state instead of forgetting a chunk without a game-side
    // retirement fence. Existing retirements must still be deliverable.
    assert_eq!(node.chunk_state.get(&extra), Some(&ChunkState::UnloadPending));
    terrain_test_assert_full(&node, extra, 3);
    assert!(node.inbound_model.updated_chunks().contains(&extra));
    let inbound = node.inbound_model.get_chunk_data(extra).unwrap();
    let mut chunk = Chunk::default();
    inbound.apply_to_chunk(&mut chunk);
    for offset in 0..CHUNK_SIZE * CHUNK_SIZE {
        assert_eq!(chunk.pixel(offset).material, 3);
    }
    let retirement_backlog = node.get_terrain_updates().unwrap();
    assert!(!retirement_backlog.is_empty());
    assert!(retirement_backlog.len() <= shared::terrain_protocol::MAX_PENDING_CHUNKS);
    assert!(retirement_backlog.iter().all(|command| matches!(command, TerrainCommand::Retire { .. })));
    for x in 0..256 {
        let at = ChunkCoord(x, 0);
        let data = node.outbound_model.get_chunk_data(at)
            .or_else(|| node.chunk_storage.get(&at).cloned())
            .expect("retirement overflow lost an existing required snapshot");
        data.apply_to_chunk(&mut chunk);
        for offset in 0..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, 0);
        }
    }

    // CHUNK remains in local interest, so its original sent application is
    // still current. Completing it frees one slot; the next ordinary update
    // can enqueue the retained chunk's retirement and finish its cleanup.
    node.terrain_ack(release);
    node.update();
    assert!(!node.chunk_state.contains_key(&extra));
    assert!(node.inbound_model.get_chunk_data(extra).is_none());
    assert!(node.outbound_model.get_chunk_data(extra).is_none());
    let stored = node.chunk_storage.get(&extra).expect("retained required snapshot did not persist");
    stored.apply_to_chunk(&mut chunk);
    for offset in 0..CHUNK_SIZE * CHUNK_SIZE {
        assert_eq!(chunk.pixel(offset).material, 3);
    }
    let next = node.get_terrain_updates().unwrap();
    assert!(next.iter().any(|command| matches!(command,
        TerrainCommand::Retire { chunk, .. } if *chunk == extra)),
        "retained retirement did not progress after a delivered ACK freed capacity");
}

#[test]
fn queued_noita_transfer_ok_keeps_changed_pixels_for_existing_listeners() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 6);
    let commands = node.get_terrain_updates().unwrap();
    queued_noita_ack_applications(&mut node, &commands);
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    let _ = node.get_emitted_msgs();
    let old = OmniPeerId(2);
    let listener = OmniPeerId(3);
    node.handle_msg(node.my_peer_id, WorldNetMessage::GetAuthorityFrom {
        chunk: CHUNK,
        current_authority: old,
    });
    assert_eq!(node.chunk_state.get(&CHUNK), Some(&ChunkState::Transfer { authority: old }));
    node.handle_msg(old, WorldNetMessage::TransferOk {
        chunk: CHUNK,
        chunk_data: Some(ChunkData::new(0)),
        listeners: [listener].into_iter().collect(),
    });
    assert!(matches!(node.chunk_state.get(&CHUNK),
        Some(ChunkState::Authority { listeners, .. }) if listeners.contains(&listener)));

    let assert_removal_delta = |delta: &ChunkDelta| {
        // A real listener that still holds material6 must receive actual air0
        // cells, not an all-Unknown delta created by an equality-only mirror.
        let mut observer = WorldModel::default();
        observer.apply_chunk_data(CHUNK, &ChunkData::new(6));
        observer.reset_change_tracking();
        observer.apply_chunk_delta(delta);
        let mut chunk = Chunk::default();
        observer.get_chunk_data(CHUNK).unwrap().apply_to_chunk(&mut chunk);
        for offset in 0..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, 0, "accepted transfer lost changed-cell tracking");
        }
    };
    let delta = node.outbound_model.get_chunk_delta(CHUNK, false).unwrap();
    assert_removal_delta(&delta);

    // No new game sample is supplied to hide missing transfer tracking. The
    // next production END must relay that accepted snapshot to its listeners.
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    let mut relayed = 0;
    for message in node.get_emitted_msgs() {
        if !matches!(message.dst, Destination::Peer(id) if id == listener) {
            continue;
        }
        if let WorldNetMessage::ChunkPacket { chunkpacket } = message.msg {
            for (delta, _) in chunkpacket {
                if delta.chunk_coord == CHUNK {
                    assert_removal_delta(&delta);
                    relayed += 1;
                }
            }
        }
    }
    assert_eq!(relayed, 1, "accepted transfer snapshot was not relayed to its listener");
}

#[test]
fn queued_noita_partial_progress_preserves_completed_edits_and_required_cells_on_unload() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        let token = terrain_test_apply_token(&network.nodes[next].get_terrain_updates().unwrap(), CHUNK);
        // Model verified removal of every cell except offset0, which the
        // engine could not replace (for example a native immutable solid).
        let remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0)]);
        network.nodes[next].terrain_progress(token, &remaining).unwrap();
        network.nodes[next].terrain_progress(token, &remaining).unwrap(); // duplicate receipt
        // A completed cell can legitimately become6 again, exactly matching
        // old feedback bytes. Its neighboring new7 must also remain possible.
        let sample = terrain_test_blob_pixels(CHUNK, 6, &[(1, 7)]);
        queued_noita_deliver_blob(&mut network, next, &sample);
        let data = network.nodes[next].outbound_model.get_chunk_data(CHUNK).unwrap();
        let mut chunk = Chunk::default();
        data.apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 0);
        assert_eq!(chunk.pixel(1).material, 7);
        for offset in 2..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, 6);
        }
        // No full completion ACK exists before unload. The remaining required
        // removal and all independently captured completed-cell edits persist.
        queued_noita_unload_before_return(&mut network, next);
        network.nodes[0].chunk_storage.get(&CHUNK).unwrap().apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 0);
        assert_eq!(chunk.pixel(1).material, 7);
        for offset in 2..CHUNK_SIZE * CHUNK_SIZE {
            assert_eq!(chunk.pixel(offset).material, 6);
        }
    }
}

#[test]
fn queued_noita_progress_rejects_changed_targets_and_cannot_reclose_completed_cells() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 0);
    let token = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
    let remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0)]);
    node.terrain_progress(token, &remaining).unwrap();
    let mut wrong_flags = NoitaWorldUpdate::load(&remaining);
    wrong_flags.runs[0].data.flags = 1;
    let invalid = [
        terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 3)]),
        terrain_test_blob_pixels(ChunkCoord(1, 0), u16::MAX, &[(0, 0)]),
        wrong_flags.save(),
        remaining[..remaining.len() - 1].to_vec(),
        Vec::new(),
    ];
    for packet in invalid {
        assert!(node.terrain_progress(token, &packet).is_err(), "invalid progress changed the current obligation");
    }
    // A delayed obsolete receipt is ignored before decoding its old payload.
    node.terrain_progress(u64::MAX, &[]).unwrap();
    // An older/superset receipt can repeat previously completed targets. It
    // must be intersected with the current obligation instead of re-closing
    // those cells or treating stale progress as new work.
    node.terrain_progress(token, &terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0), (1, 0)])).unwrap();
    node.terrain_progress(token, &remaining).unwrap();
    terrain_test_sample(&mut node, CHUNK, 6);
    let mut chunk = Chunk::default();
    node.outbound_model.get_chunk_data(CHUNK).unwrap().apply_to_chunk(&mut chunk);
    assert_eq!(chunk.pixel(0).material, 0, "invalid receipt removed the required target");
    for offset in 1..CHUNK_SIZE * CHUNK_SIZE {
        assert_eq!(chunk.pixel(offset).material, 6, "duplicate receipt re-blocked completed cells");
    }
}

#[test]
fn queued_noita_new_generation_composes_only_remaining_and_newly_required_cells() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 6);
    let first = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
    let old_remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 6), (2, 6)]);
    node.terrain_progress(first, &old_remaining).unwrap();
    let sample = terrain_test_blob_pixels(CHUNK, 6, &[(1, 7)]);
    node.add_terrain_update(&sample).unwrap();
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();

    // Controlled new inbound write over the unresolved solid6 target. The
    // production composition must prefer new0, retain other unresolved6,
    // and leave the already-completed/legitimately edited cell1 untouched.
    let mut source = WorldModel::default();
    source.apply_chunk_data(CHUNK, &ChunkData::new(6));
    source.reset_change_tracking();
    source.apply_noita_update(&NoitaWorldUpdate::load(&terrain_test_blob_pixels(CHUNK, 6, &[(0, 0)])),
        &mut FxHashSet::default());
    let delta = source.get_chunk_delta(CHUNK, false).unwrap();
    node.inbound_model.apply_chunk_delta(&delta);
    node.outbound_model.mirror_chunk_delta(&delta);
    let paused = node.get_terrain_updates().unwrap();
    let pause = terrain_test_pause_token(&paused, CHUNK, first);
    assert!(node.get_terrain_updates().unwrap().is_empty(), "replacement preceded the exact frozen remaining mask");
    node.terrain_pause_ack(pause, &old_remaining).unwrap();
    let commands = node.get_terrain_updates().unwrap();
    let second = terrain_test_apply_token(&commands, CHUNK);
    assert!(second > first);
    let blob = commands.iter().find_map(|command| match command {
        TerrainCommand::Apply { chunk, data, .. } if *chunk == CHUNK => Some(data),
        _ => None,
    }).unwrap();
    let pixels = terrain_test_expanded(blob);
    assert_eq!(pixels.len(), CHUNK_SIZE * CHUNK_SIZE);
    assert_eq!(pixels[0], 0);
    assert_eq!(pixels[2], 6);
    for (offset, material) in pixels.iter().enumerate() {
        if offset != 0 && offset != 2 {
            assert_eq!(*material, u16::MAX, "new generation re-touched a completed cell");
        }
    }
    node.terrain_ack(first);
    node.terrain_progress(first, &old_remaining).unwrap();
    node.terrain_pause_ack(pause, &[]).unwrap(); // delayed duplicate cannot open the child
    node.add_terrain_update(&sample).unwrap();
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    let mut chunk = Chunk::default();
    node.outbound_model.get_chunk_data(CHUNK).unwrap().apply_to_chunk(&mut chunk);
    assert_eq!(chunk.pixel(0).material, 0);
    assert_eq!(chunk.pixel(1).material, 7);
    assert_eq!(chunk.pixel(2).material, 6);
    node.terrain_ack(second);
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 6);
    node.add_terrain_end(1, &[1]).unwrap();
    node.terrain_pause_ack(pause, &old_remaining).unwrap();
    node.terrain_progress(first, &old_remaining).unwrap();
    assert!(node.outbound_model.get_chunk_data(CHUNK).is_none(), "late pause receipt restored old-world terrain");
    node.add_terrain_update(&queued_noita_blob(6)).unwrap();
    node.add_terrain_end(1, &[1]).unwrap();
    assert!(node.outbound_model.get_chunk_data(CHUNK).is_none(), "late pause receipt opened the world-reset gate");
}

#[test]
fn queued_noita_partial_reconnect_and_world_reset_cannot_restore_old_completed_cells() {
    let mut node = terrain_test_world(true);
    terrain_test_grant(&mut node, CHUNK, 0);
    let old = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
    let remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0)]);
    node.terrain_progress(old, &remaining).unwrap();
    node.add_terrain_update(&terrain_test_blob_pixels(CHUNK, 6, &[(1, 7)])).unwrap();
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    node.terrain_connection_reset();
    node.terrain_hello(1, 0).unwrap();
    let commands = node.get_terrain_updates().unwrap();
    let reset = commands.iter().find_map(|command| match command {
        TerrainCommand::Reset { token } => Some(*token), _ => None,
    }).unwrap();
    let new = terrain_test_apply_token(&commands, CHUNK);
    let data = commands.iter().find_map(|command| match command {
        TerrainCommand::Apply { chunk, data, .. } if *chunk == CHUNK => Some(data), _ => None,
    }).unwrap();
    assert_eq!(terrain_test_expanded(data), terrain_test_expanded(&remaining));
    node.terrain_progress(old, &remaining).unwrap();
    node.terrain_ack(old);
    node.terrain_ack(reset);
    node.add_terrain_update(&terrain_test_blob_pixels(CHUNK, 6, &[(1, 7)])).unwrap();
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    let mut chunk = Chunk::default();
    node.outbound_model.get_chunk_data(CHUNK).unwrap().apply_to_chunk(&mut chunk);
    assert_eq!(chunk.pixel(0).material, 0);
    assert_eq!(chunk.pixel(1).material, 7);
    node.terrain_ack(new);
    node.add_terrain_end(1, &[1]).unwrap();
    node.terrain_progress(new, &remaining).unwrap();
    assert!(node.outbound_model.get_chunk_data(CHUNK).is_none());
    let resets = node.get_terrain_updates().unwrap();
    for command in resets {
        if let TerrainCommand::Reset { token } = command { node.terrain_ack(token); }
    }
    node.add_terrain_update(&queued_noita_blob(6)).unwrap();
    node.add_terrain_end(1, &[1]).unwrap();
    terrain_test_assert_full(&node, CHUNK, 6);
}

#[test]
fn queued_noita_parent_completion_before_pause_receipt_cannot_be_reasserted_by_child() {
    for (owner, next) in [(1, 0), (0, 1)] {
        let mut network = queued_noita_grant_air_without_game_drain(owner, next);
        let parent = terrain_test_apply_token(&network.nodes[next].get_terrain_updates().unwrap(), CHUNK);
        let remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0)]);
        network.nodes[next].terrain_progress(parent, &remaining).unwrap();

        // A controlled new inbound target arrives while the proxy only knows
        // an older partial receipt. It must request a pause, not copy that
        // stale remaining mask into an immediately queued child generation.
        let mut source = WorldModel::default();
        source.apply_chunk_data(CHUNK, &ChunkData::new(0));
        source.reset_change_tracking();
        source.apply_noita_update(&NoitaWorldUpdate::load(&terrain_test_blob_pixels(CHUNK, 0, &[(2, 3)])),
            &mut FxHashSet::default());
        let delta = source.get_chunk_delta(CHUNK, false).unwrap();
        network.nodes[next].inbound_model.apply_chunk_delta(&delta);
        network.nodes[next].outbound_model.mirror_chunk_delta(&delta);
        let paused = network.nodes[next].get_terrain_updates().unwrap();
        let pause = terrain_test_pause_token(&paused, CHUNK, parent);

        // In the independent directions, Noita can finish the old removal,
        // ACK it, then create legitimate6 before receiving the proxy's Pause.
        // These ACK/sample messages precede the later PauseACK on one FIFO.
        network.nodes[next].terrain_ack(parent);
        let legitimate = terrain_test_blob_pixels(CHUNK, 0, &[(0, 6), (1, 7)]);
        queued_noita_deliver_blob(&mut network, next, &legitimate);
        network.nodes[next].terrain_pause_ack(pause, &[]).unwrap();
        let commands = network.nodes[next].get_terrain_updates().unwrap();
        let child = terrain_test_apply_token(&commands, CHUNK);
        let blob = commands.iter().find_map(|command| match command {
            TerrainCommand::Apply { chunk, data, .. } if *chunk == CHUNK => Some(data), _ => None,
        }).unwrap();
        let pixels = terrain_test_expanded(blob);
        assert_eq!(pixels[0], u16::MAX, "child reasserted an obsolete completed removal over legitimate6");
        assert_eq!(pixels[1], u16::MAX);
        assert_eq!(pixels[2], 3);
        for (offset, value) in pixels.iter().enumerate() {
            if offset != 2 { assert_eq!(*value, u16::MAX); }
        }
        network.nodes[next].terrain_progress(parent, &remaining).unwrap();
        network.nodes[next].terrain_ack(parent);
        network.nodes[next].terrain_pause_ack(pause, &remaining).unwrap();
        queued_noita_deliver_blob(&mut network, next, &legitimate);
        let mut chunk = Chunk::default();
        network.nodes[next].outbound_model.get_chunk_data(CHUNK).unwrap().apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 6);
        assert_eq!(chunk.pixel(1).material, 7);
        assert_eq!(chunk.pixel(2).material, 3);
        network.nodes[next].terrain_ack(child);
        queued_noita_deliver_blob(&mut network, next,
            &terrain_test_blob_pixels(CHUNK, 0, &[(0, 6), (1, 7), (2, 3)]));
        queued_noita_unload_before_return(&mut network, next);
        network.nodes[0].chunk_storage.get(&CHUNK).unwrap().apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 6, "pause race lost the legitimate post-application edit at storage");
        assert_eq!(chunk.pixel(1).material, 7);
        assert_eq!(chunk.pixel(2).material, 3);
    }
}

#[test]
fn queued_noita_noop_delta_cannot_strand_partial_work_or_a_frozen_identity_resume() {
    for consume_tracking_after_pause in [false, true] {
        let mut node = terrain_test_world(true);
        terrain_test_grant(&mut node, CHUNK, 0);
        let parent = terrain_test_apply_token(&node.get_terrain_updates().unwrap(), CHUNK);
        let remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0)]);
        node.terrain_progress(parent, &remaining).unwrap();
        let mut source = WorldModel::default();
        source.apply_chunk_data(CHUNK, &ChunkData::new(6));
        source.reset_change_tracking();
        if consume_tracking_after_pause {
            // A redundant known0 assertion over the exact same required cell
            // legitimately triggers Pause. Consuming its bookkeeping later
            // loses no additional target beyond the parent's remaining0.
            source.apply_noita_update(&NoitaWorldUpdate {
                header: Header { x: 0, y: 0, w: 0, h: 0, run_count: 1 },
                runs: vec![PixelRun { length: 1, data: RawPixel { material: 0, flags: 0 } }],
            }, &mut FxHashSet::default());
        }
        let delta = source.get_chunk_delta(CHUNK, false).unwrap();
        node.inbound_model.apply_chunk_delta(&delta);
        node.outbound_model.mirror_chunk_delta(&delta);
        if !consume_tracking_after_pause {
            // The actual helper yields an all-Unknown delta. It marks the
            // inbound chunk updated but cannot produce a game-facing write.
            assert!(node.get_terrain_updates().unwrap().is_empty(), "no-op delta paused live partial work");
            node.terrain_ack(parent);
            terrain_test_sample(&mut node, CHUNK, 6);
            terrain_test_assert_full(&node, CHUNK, 6);
            assert!(node.get_terrain_updates().unwrap().is_empty());
            continue;
        }
        let paused = node.get_terrain_updates().unwrap();
        let pause = terrain_test_pause_token(&paused, CHUNK, parent);
        // Defensive liveness control: some other consumer may have drained
        // change bookkeeping after Pause was emitted. Frozen required work
        // still needs an identity child so Noita can eventually complete it.
        node.inbound_model.reset_change_tracking();
        node.terrain_pause_ack(pause, &remaining).unwrap();
        let commands = node.get_terrain_updates().unwrap();
        let child = terrain_test_apply_token(&commands, CHUNK);
        assert!(child > pause);
        let data = commands.iter().find_map(|command| match command {
            TerrainCommand::Apply { chunk, data, .. } if *chunk == CHUNK => Some(data), _ => None,
        }).unwrap();
        assert_eq!(terrain_test_expanded(data), terrain_test_expanded(&remaining));
        node.terrain_ack(parent);
        terrain_test_sample(&mut node, CHUNK, 6);
        let mut chunk = Chunk::default();
        node.outbound_model.get_chunk_data(CHUNK).unwrap().apply_to_chunk(&mut chunk);
        assert_eq!(chunk.pixel(0).material, 0);
        node.terrain_ack(child);
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 6);
    }
}


fn terrain_test_command_token(command: &TerrainCommand) -> u64 {
    match command {
        TerrainCommand::Apply { token, .. }
        | TerrainCommand::Retire { token, .. }
        | TerrainCommand::Reset { token }
        | TerrainCommand::Pause { token, .. } => *token,
    }
}

fn terrain_test_reconnect_at_capacity(node: &mut WorldManager) -> Vec<TerrainCommand> {
    // This is net.rs's real lifecycle: a newly accepted compatible socket
    // resets the local boundary; an ordinary update can run before Lua HELLO;
    // then HELLO retokens all retained flights before the next output drain.
    node.terrain_connection_reset();
    node.update();
    assert!(node.get_terrain_updates().unwrap().is_empty());
    node.terrain_hello(1, 0).unwrap();
    node.update();
    let commands = node.get_terrain_updates().expect(
        "capacity pressure discarded Reset/replay and would disconnect the production socket again",
    );
    assert_eq!(commands.len(), shared::terrain_protocol::MAX_PENDING_CHUNKS + 1);
    let reset = match commands.first().unwrap() {
        TerrainCommand::Reset { token } => *token,
        other => panic!("fresh Reset must precede retained replay: {other:?}"),
    };
    let mut previous = 0;
    for command in &commands {
        // A successful vector is actually serialized/delivered by this
        // adapter fixture. No token from a discarded/failed drain is ACKed.
        let bytes = command.encode(node.world_number(), node.terrain_session());
        assert_eq!(i32::from_le_bytes(bytes[9..13].try_into().unwrap()), 0);
        assert_eq!(u32::from_le_bytes(bytes[13..17].try_into().unwrap()), 1);
        let token = u64::from_le_bytes(bytes[1..9].try_into().unwrap());
        assert_eq!(token, terrain_test_command_token(command));
        assert!(token > previous, "replay violates the game stream token floor");
        previous = token;
    }
    node.terrain_ack(reset);
    commands
}

#[test]
fn queued_noita_capacity_recovery_saturated_applications_reconnect_without_invented_acks() {
    let limit = shared::terrain_protocol::MAX_PENDING_CHUNKS;
    let mut node = terrain_test_world(true);
    for x in 0..limit {
        let at = ChunkCoord(x as i32, 0);
        node.authority_map.insert(at, (node.my_peer_id, 1));
        terrain_test_grant(&mut node, at, 0);
    }
    let first = node.get_terrain_updates().unwrap();
    assert_eq!(first.len(), limit);
    assert!(first.iter().all(|command| matches!(command, TerrainCommand::Apply { .. })));
    // Only still-loaded applications can plausibly have a delayed completion
    // ACK. Never acknowledge an unloaded far application to recover capacity.
    let mut obsolete: Vec<_> = first.iter().filter_map(|command| match command {
        TerrainCommand::Apply { chunk, token, .. } if chunk.0 <= 3 => Some(*token),
        _ => None,
    }).collect();
    let extra = ChunkCoord(0, 1); // Additional requirement remains in local interest.
    terrain_test_grant(&mut node, extra, 3);

    let mut replay = Vec::new();
    for _ in 0..3 {
        replay = terrain_test_reconnect_at_capacity(&mut node);
        // Ordinary pre-HELLO cleanup retires the 252 applications now outside
        // local interest. Only the four loaded chunks can replay Apply; an
        // unloaded application must never need an invented completion ACK.
        assert_eq!(replay.iter().filter(|command| matches!(command,
            TerrainCommand::Apply { .. })).count(), 4);
        assert_eq!(replay.iter().filter(|command| matches!(command,
            TerrainCommand::Retire { .. })).count(), limit - 4);
        for command in replay.iter().skip(1) {
            match command {
                TerrainCommand::Apply { chunk, .. } => assert!(chunk.1 == 0 && (0..=3).contains(&chunk.0)),
                TerrainCommand::Retire { chunk, .. } => assert!(chunk.1 == 0 && chunk.0 >= 4),
                other => panic!("unexpected capacity replay command: {other:?}"),
            }
        }
        assert!(replay.iter().skip(1).all(|command|
            !obsolete.contains(&terrain_test_command_token(command))));
        // These were delivered only on predecessor sockets. They cannot free
        // a slot belonging to any freshly retokened current application.
        for &token in &obsolete {
            node.terrain_ack(token);
            node.terrain_ack(token);
        }
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 0);
        terrain_test_sample(&mut node, extra, 6);
        terrain_test_assert_full(&node, extra, 3);
        assert!(node.inbound_model.updated_chunks().contains(&extra));
        assert!(node.get_terrain_updates().unwrap().is_empty(),
            "old ACKs unlocked saturated current-token capacity");
        obsolete.extend(replay.iter().map(terrain_test_command_token));
    }

    // One real current completion opens exactly one bounded application slot.
    let release = terrain_test_apply_token(&replay, CHUNK);
    node.terrain_ack(release);
    node.terrain_ack(release);
    let admitted = node.get_terrain_updates().unwrap();
    assert_eq!(admitted.len(), 1);
    let extra_token = terrain_test_apply_token(&admitted, extra);
    assert!(!obsolete.contains(&extra_token));
    let TerrainCommand::Apply { data, .. } = &admitted[0] else { unreachable!() };
    assert!(terrain_test_expanded(data).iter().all(|&value| value == 3));
    assert!(!node.inbound_model.updated_chunks().contains(&extra));
    for &token in &obsolete {
        node.terrain_ack(token);
    }
    terrain_test_sample(&mut node, extra, 6);
    terrain_test_assert_full(&node, extra, 3);
    node.terrain_ack(extra_token);
    terrain_test_sample(&mut node, extra, 6);
    terrain_test_assert_full(&node, extra, 6);
    assert!(node.get_terrain_updates().unwrap().is_empty());
}

#[test]
fn queued_noita_capacity_recovery_retire_backlog_survives_prehello_update_and_repeated_reconnect() {
    let limit = shared::terrain_protocol::MAX_PENDING_CHUNKS;
    let mut node = terrain_test_world(true);
    let far = |x| ChunkCoord(100 + x as i32, 100);
    for x in 0..limit {
        let at = far(x);
        // The normal host release must persist each authoritative snapshot.
        node.authority_map.insert(at, (node.my_peer_id, 1));
        terrain_test_grant(&mut node, at, 0);
    }
    let applications = node.get_terrain_updates().unwrap();
    assert_eq!(applications.len(), limit);
    let original_applications: Vec<_> = applications.iter().map(terrain_test_command_token).collect();
    let mut obsolete = Vec::new();
    node.add_terrain_end(1, &[0, 0, 0, 0, 0, 0]).unwrap();
    node.update();
    for x in 0..limit {
        let at = far(x);
        assert!(!node.chunk_state.contains_key(&at), "fixture did not remove ordinary retirement state");
        assert!(node.inbound_model.get_chunk_data(at).is_none());
        assert!(node.outbound_model.get_chunk_data(at).is_none());
        assert_eq!(material(node.chunk_storage.get(&at).expect("unload lost its authoritative snapshot")), 0);
    }
    let retirements = node.get_terrain_updates().unwrap();
    assert_eq!(retirements.len(), limit);
    assert!(retirements.iter().all(|command| matches!(command, TerrainCommand::Retire { .. })));
    assert!(retirements.iter().all(|command| !original_applications.contains(&terrain_test_command_token(command))));
    // Subsequent stale ACKs concern delivered Retire frames only. No ACK is
    // invented for the earlier applications after their chunks unloaded.
    obsolete.extend(retirements.iter().map(terrain_test_command_token));
    terrain_test_grant(&mut node, CHUNK, 3);

    let mut replay = Vec::new();
    for _ in 0..3 {
        replay = terrain_test_reconnect_at_capacity(&mut node);
        assert!(replay.iter().skip(1).all(|command| matches!(command, TerrainCommand::Retire { .. })),
            "pre-HELLO update silently cleared or replaced the retirement backlog");
        for &token in &obsolete {
            node.terrain_ack(token);
            node.terrain_ack(token);
        }
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 3);
        assert!(node.inbound_model.updated_chunks().contains(&CHUNK));
        assert!(node.get_terrain_updates().unwrap().is_empty());
        obsolete.extend(replay.iter().map(terrain_test_command_token));
    }

    let TerrainCommand::Retire { token: freed, .. } = &replay[1] else { unreachable!() };
    node.terrain_ack(*freed);
    node.terrain_ack(*freed);
    let admitted = node.get_terrain_updates().unwrap();
    assert_eq!(admitted.len(), 1, "one retirement ACK must admit one waiting application");
    let extra_token = terrain_test_apply_token(&admitted, CHUNK);
    let TerrainCommand::Apply { data, .. } = &admitted[0] else { unreachable!() };
    assert!(terrain_test_expanded(data).iter().all(|&value| value == 3));
    assert!(!obsolete.contains(&extra_token));

    // Reconnecting again with the newly admitted application still retains
    // exactly the original bound: 255 retirements plus one application.
    let final_replay = terrain_test_reconnect_at_capacity(&mut node);
    assert_eq!(final_replay.iter().filter(|command| matches!(command, TerrainCommand::Retire { .. })).count(), limit - 1);
    assert_eq!(final_replay.iter().filter(|command| matches!(command, TerrainCommand::Apply { .. })).count(), 1);
    node.terrain_ack(extra_token); // Delivered on the immediately prior socket.
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 3);
    for command in &final_replay {
        node.terrain_ack(terrain_test_command_token(command));
        node.terrain_ack(terrain_test_command_token(command));
    }
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 6);
    assert!(node.get_terrain_updates().unwrap().is_empty());
    for x in 0..limit {
        assert_eq!(material(node.chunk_storage.get(&far(x)).unwrap()), 0);
    }
}


#[test]
fn queued_noita_capacity_recovery_pause_and_frozen_replacement_progress_while_full() {
    let limit = shared::terrain_protocol::MAX_PENDING_CHUNKS;
    let mut node = terrain_test_world(true);
    for x in 0..limit {
        terrain_test_grant(&mut node, ChunkCoord(x as i32, 0), 0);
    }
    let delivered = node.get_terrain_updates().unwrap();
    assert_eq!(delivered.len(), limit);
    let parent = terrain_test_apply_token(&delivered, CHUNK);
    let remaining = terrain_test_blob_pixels(CHUNK, u16::MAX, &[(0, 0)]);
    node.terrain_progress(parent, &remaining).unwrap();
    let extra = ChunkCoord(0, 1);
    terrain_test_grant(&mut node, extra, 7);
    terrain_test_grant(&mut node, CHUNK, 3);

    // A required extra chunk cannot consume a new slot yet, but it must not
    // discard the control work needed to finish an existing application.
    let pause_commands = node.get_terrain_updates().expect(
        "capacity pressure discarded a required PAUSE for an existing application",
    );
    assert_eq!(pause_commands.len(), 1);
    let pause = terrain_test_pause_token(&pause_commands, CHUNK, parent);
    node.terrain_pause_ack(pause, &remaining).unwrap();
    let child_commands = node.get_terrain_updates().unwrap();
    assert_eq!(child_commands.len(), 1);
    let child = terrain_test_apply_token(&child_commands, CHUNK);
    let TerrainCommand::Apply { data, .. } = &child_commands[0] else { unreachable!() };
    assert!(terrain_test_expanded(data).iter().all(|&value| value == 3));
    assert!(child > pause && pause > parent);
    assert!(node.inbound_model.updated_chunks().contains(&extra));
    node.terrain_ack(parent);
    node.terrain_ack(parent);
    node.terrain_pause_ack(pause, &remaining).unwrap();
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 3);
    assert!(node.get_terrain_updates().unwrap().is_empty());

    node.terrain_ack(child); // Only the actually delivered replacement frees a slot.
    let admitted = node.get_terrain_updates().unwrap();
    assert_eq!(admitted.len(), 1);
    let token = terrain_test_apply_token(&admitted, extra);
    let TerrainCommand::Apply { data, .. } = &admitted[0] else { unreachable!() };
    assert!(terrain_test_expanded(data).iter().all(|&value| value == 7));
    node.terrain_ack(parent);
    node.terrain_ack(child);
    terrain_test_sample(&mut node, extra, 6);
    terrain_test_assert_full(&node, extra, 7);
    node.terrain_ack(token);
    terrain_test_sample(&mut node, extra, 6);
    terrain_test_assert_full(&node, extra, 6);
}


#[test]
fn queued_noita_capacity_recovery_disconnect_before_unload_retires_existing_applications_while_disabled() {
    let limit = shared::terrain_protocol::MAX_PENDING_CHUNKS;
    let mut node = terrain_test_world(true);
    let far = |x| ChunkCoord(100 + x as i32, 100);
    for x in 0..limit {
        let at = far(x);
        node.authority_map.insert(at, (node.my_peer_id, 1));
        terrain_test_grant(&mut node, at, 0);
    }
    let delivered = node.get_terrain_updates().unwrap();
    assert_eq!(delivered.len(), limit);
    assert!(delivered.iter().all(|command| matches!(command, TerrainCommand::Apply { .. })));
    let original_applications: Vec<_> = delivered.iter().map(terrain_test_command_token).collect();
    // These far chunks were still ordinary authorities when the prior Apply
    // batch was delivered. Disconnect happens BEFORE the first unload update;
    // no completion ACK exists for their unloaded application work.
    assert!(node.chunk_state.contains_key(&far(0)));
    terrain_test_grant(&mut node, CHUNK, 3);
    let mut old_retirements = Vec::new();
    let mut replay = Vec::new();
    for _ in 0..3 {
        replay = terrain_test_reconnect_at_capacity(&mut node);
        assert!(replay.iter().skip(1).all(|command| matches!(command, TerrainCommand::Retire { .. })),
            "pre-HELLO cleanup removed ordinary state but stranded an unloaded Apply instead of retiring it");
        assert!(replay.iter().all(|command| !original_applications.contains(&terrain_test_command_token(command))));
        for x in 0..limit {
            let at = far(x);
            assert!(!node.chunk_state.contains_key(&at));
            assert!(node.inbound_model.get_chunk_data(at).is_none());
            assert!(node.outbound_model.get_chunk_data(at).is_none());
            assert_eq!(material(node.chunk_storage.get(&at).expect("disabled cleanup lost authoritative terrain")), 0);
        }
        // Only earlier delivered Retire commands can have delayed ACKs in
        // this fixture. Never invent an ACK for an unloaded Apply command.
        for &old in &old_retirements {
            node.terrain_ack(old);
            node.terrain_ack(old);
        }
        terrain_test_sample(&mut node, CHUNK, 6);
        terrain_test_assert_full(&node, CHUNK, 3);
        assert!(node.inbound_model.updated_chunks().contains(&CHUNK));
        assert!(node.get_terrain_updates().unwrap().is_empty());
        old_retirements.extend(replay.iter().filter_map(|command| match command {
            TerrainCommand::Retire { token, .. } => Some(*token),
            _ => None,
        }));
    }

    let TerrainCommand::Retire { token: freed, .. } = &replay[1] else { unreachable!() };
    node.terrain_ack(*freed);
    node.terrain_ack(*freed);
    let admitted = node.get_terrain_updates().unwrap();
    assert_eq!(admitted.len(), 1, "a delivered retirement ACK must free a slot without completing an unloaded Apply");
    let child = terrain_test_apply_token(&admitted, CHUNK);
    let TerrainCommand::Apply { data, .. } = &admitted[0] else { unreachable!() };
    assert!(terrain_test_expanded(data).iter().all(|&value| value == 3));
    assert!(!node.inbound_model.updated_chunks().contains(&CHUNK));

    let final_replay = terrain_test_reconnect_at_capacity(&mut node);
    assert_eq!(final_replay.iter().filter(|command| matches!(command, TerrainCommand::Retire { .. })).count(), limit - 1);
    assert_eq!(final_replay.iter().filter(|command| matches!(command, TerrainCommand::Apply { .. })).count(), 1);
    node.terrain_ack(child); // This near-chunk frame was delivered on the prior connection.
    for &old in &old_retirements {
        node.terrain_ack(old);
    }
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 3);
    for command in &final_replay {
        // Only the near in-interest chunk is actually applied; every far
        // command is a delivered Retire, which needs no loaded native chunk.
        node.terrain_ack(terrain_test_command_token(command));
    }
    terrain_test_sample(&mut node, CHUNK, 6);
    terrain_test_assert_full(&node, CHUNK, 6);
    assert!(node.get_terrain_updates().unwrap().is_empty());
    for x in 0..limit {
        assert_eq!(material(node.chunk_storage.get(&far(x)).unwrap()), 0);
    }
}
