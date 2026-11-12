//! An ACK is a FIFO fence, not evidence inferred from sending a packet.
//! Lua emits it only after synchronous verified application, before capture.
use super::*;
use shared::terrain_protocol::{MAX_PENDING_CHUNKS, validate_chunk};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub(crate) enum TerrainCommand {
    Apply {
        chunk: ChunkCoord,
        token: u64,
        data: Arc<Vec<u8>>,
    },
    Retire {
        chunk: ChunkCoord,
        token: u64,
    },
    Reset {
        token: u64,
    },
    Pause {
        chunk: ChunkCoord,
        token: u64,
        application: u64,
    },
}

impl TerrainCommand {
    pub(crate) fn encode(&self, world: i32, session: u32) -> Vec<u8> {
        let (kind, token, chunk, data): (_, _, _, &[u8]) = match self {
            Self::Apply { chunk, token, data } => (0, *token, *chunk, data),
            Self::Retire { chunk, token } => (1, *token, *chunk, &[]),
            Self::Reset { token } => (2, *token, ChunkCoord(0, 0), &[]),
            Self::Pause { chunk, token, .. } => (3, *token, *chunk, &[]),
        };
        let mut out = vec![kind];
        out.extend(token.to_le_bytes());
        out.extend(world.to_le_bytes());
        out.extend(session.to_le_bytes());
        out.extend(chunk.0.to_le_bytes());
        out.extend(chunk.1.to_le_bytes());
        out.extend(data);
        if let Self::Pause { application, .. } = self {
            out.extend(application.to_le_bytes());
        }
        out
    }
}

struct Flight {
    token: u64,
    data: Option<Arc<Vec<u8>>>,
    sent: bool,
    receipt: bool,
    pause: Option<(u64, bool, bool)>, // token, sent, frozen receipt
}

impl Flight {
    fn new(token: u64, data: Option<Arc<Vec<u8>>>, sent: bool) -> Self {
        Self {
            token,
            data,
            sent,
            receipt: false,
            pause: None,
        }
    }
}

#[derive(Default)]
pub(super) struct TerrainBarrier {
    enabled: bool,
    serial: u64,
    reset: Option<(u64, bool)>,
    flights: FxHashMap<ChunkCoord, Flight>,
    fault: Option<String>,
    client_session: u32,
}

impl TerrainBarrier {
    pub(super) fn is_enabled(&self) -> bool {
        self.enabled
    }

    pub(super) fn world_reset(&mut self) {
        self.flights.clear();
        if self.enabled {
            let token = self.token();
            self.reset = Some((token, false));
        }
    }
    fn token(&mut self) -> u64 {
        // Never reset, including world reset and local reconnect.
        self.serial = self.serial.checked_add(1).expect("Terrain token exhausted");
        self.serial
    }

    pub(super) fn retire(&mut self, chunk: ChunkCoord) -> bool {
        if !self.enabled && !self.flights.contains_key(&chunk) {
            return true;
        }
        // Disconnect retains old applications. If ordinary cleanup unloads
        // one before HELLO, it must still become a retirement: the unloaded
        // game cannot complete its Apply, and after cleanup no state remains
        // to request this retirement later. Replacing it needs no new slot.
        if !self.flights.contains_key(&chunk) && self.flights.len() >= MAX_PENDING_CHUNKS {
            // Keep the ordinary UnloadPending state and both models until a
            // delivered retirement/application frees a slot. Capacity is
            // backpressure, not a session fault requiring another reconnect.
            return false;
        }
        let token = self.token();
        self.flights.insert(chunk, Flight::new(token, None, false));
        true
    }
}

impl WorldManager {
    pub(crate) fn world_number(&self) -> i32 {
        self.world_num
    }
    pub(crate) fn terrain_session(&self) -> u32 {
        self.terrain.client_session
    }
    pub(crate) fn terrain_hello(&mut self, session: u32, world: i32) -> eyre::Result<()> {
        eyre::ensure!(
            session > self.terrain.client_session,
            "Reused game terrain session"
        );
        self.terrain.client_session = session;
        self.terrain.enabled = true;
        if world != self.world_num {
            self.world_num = world;
            self.reset();
        }
        let token = self.terrain.token();
        self.terrain.reset = Some((token, false));
        let chunks: Vec<_> = self.terrain.flights.keys().copied().collect();
        for chunk in chunks {
            let token = self.terrain.token();
            let flight = self.terrain.flights.get_mut(&chunk).unwrap();
            flight.token = token;
            flight.sent = false;
            flight.receipt = false;
            flight.pause = None;
        }
        Ok(())
    }
    /// A new socket cannot inherit acknowledgements from its predecessor.
    pub(crate) fn terrain_connection_reset(&mut self) {
        self.terrain.enabled = false;
        self.terrain.client_session = 0;
        self.terrain.fault = None;
        let token = self.terrain.token();
        self.terrain.reset = Some((token, false));
        let chunks: Vec<_> = self.terrain.flights.keys().copied().collect();
        for chunk in chunks {
            let token = self.terrain.token();
            let flight = self.terrain.flights.get_mut(&chunk).unwrap();
            flight.token = token;
            flight.sent = false;
            flight.receipt = false;
            flight.pause = None;
        }
    }

    pub(crate) fn terrain_ack(&mut self, token: u64) {
        if self.terrain.reset.is_some_and(|r| r.0 == token && r.1) {
            self.terrain.reset = None;
            return;
        }
        self.terrain
            .flights
            .retain(|_, f| f.token != token || !f.sent);
    }

    pub(crate) fn add_terrain_update(&mut self, data: &[u8]) -> eyre::Result<()> {
        eyre::ensure!(
            self.terrain.enabled,
            "Lua terrain protocol v2 capability required; use matching Lua/ewext/proxy"
        );
        eyre::ensure!(self.terrain.fault.is_none(), "Terrain session faulted");
        let (x, y) = validate_chunk(data)?;
        let chunk = ChunkCoord(x, y);
        if self.terrain.reset.is_some() {
            return Ok(());
        }
        if !self.terrain.flights.contains_key(&chunk)
            && !self.inbound_model.updated_chunks().contains(&chunk)
        {
            self.add_update(NoitaWorldUpdate::load(data));
            return Ok(());
        }
        let mut protected = vec![false; CHUNK_SIZE * CHUNK_SIZE];
        let mut mark = |data: &[u8]| {
            let update = NoitaWorldUpdate::load(data);
            let mut offset = 0;
            for run in update.runs {
                let end = (offset + run.length as usize).min(protected.len());
                if run.data.material != u16::MAX {
                    protected[offset..end].fill(true);
                }
                offset = end;
            }
        };
        if let Some(flight) = self.terrain.flights.get(&chunk) {
            match &flight.data {
                Some(data) => mark(data),
                None => return Ok(()),
            }
        }
        if let Some(data) = self.inbound_model.peek_noita_update(chunk) {
            mark(&data);
        }
        if protected.iter().all(|v| *v) {
            return Ok(());
        }
        let update = NoitaWorldUpdate::load(data);
        if protected.iter().any(|v| *v) {
            let mut runner = world_model::encoding::PixelRunner::new();
            let mut offset = 0;
            for run in update.runs {
                for _ in 0..run.length {
                    runner.put_pixel(if protected[offset] {
                        world_model::encoding::RawPixel {
                            material: u16::MAX,
                            flags: 0,
                        }
                    } else {
                        run.data
                    });
                    offset += 1;
                }
            }
            self.add_update(runner.into_noita_update(update.header.x, update.header.y, 127, 127));
        } else {
            self.add_update(update);
        }
        Ok(())
    }

    /// END metadata must advance even when every sample in a batch was fenced.
    pub(crate) fn add_terrain_end(&mut self, priority: u8, pos: &[i32]) -> eyre::Result<()> {
        eyre::ensure!(
            self.terrain.enabled && self.terrain.fault.is_none(),
            "Compatible active terrain session required for END"
        );
        eyre::ensure!(
            pos.len() == 1 || pos.len() == 6,
            "Invalid terrain END metadata"
        );
        if pos.len() == 6 {
            self.my_pos = (pos[0], pos[1]);
            self.cam_pos = (pos[2], pos[3]);
            self.is_notplayer = pos[4] == 1;
        }
        let world = *pos.last().unwrap();
        if world != self.world_num {
            self.world_num = world;
            self.reset();
            return Ok(());
        }
        self.add_end(priority, pos);
        Ok(())
    }

    /// Progress is an ordered, per-cell application fence. Intersect masks so
    /// a duplicate/late report cannot re-close a completed cell after an edit.
    pub(crate) fn terrain_progress(&mut self, token: u64, remaining: &[u8]) -> eyre::Result<()> {
        let Some(chunk) = self
            .terrain
            .flights
            .iter()
            .find_map(|(c, f)| (f.token == token && f.sent && f.data.is_some()).then_some(*c))
        else {
            return Ok(());
        };
        let data = self.terrain.flights[&chunk].data.as_ref().unwrap();
        let reduced = shrink_required(chunk, data, remaining)?;
        if !has_required(&reduced) {
            self.terrain.flights.remove(&chunk);
        } else {
            let flight = self.terrain.flights.get_mut(&chunk).unwrap();
            flight.data = Some(Arc::new(reduced));
            flight.receipt = true;
        }
        Ok(())
    }

    pub(crate) fn terrain_pause_ack(&mut self, token: u64, remaining: &[u8]) -> eyre::Result<()> {
        let Some(chunk) = self
            .terrain
            .flights
            .iter()
            .find_map(|(c, f)| f.pause.filter(|p| p.0 == token && p.1).map(|_| *c))
        else {
            return Ok(());
        };
        if remaining.is_empty() {
            self.terrain.flights.remove(&chunk);
            return Ok(());
        }
        let data = self.terrain.flights[&chunk].data.as_ref().unwrap();
        let reduced = shrink_required(chunk, data, remaining)?;
        if !has_required(&reduced) {
            self.terrain.flights.remove(&chunk);
        } else {
            let flight = self.terrain.flights.get_mut(&chunk).unwrap();
            flight.data = Some(Arc::new(reduced));
            flight.receipt = true;
            flight.pause.as_mut().unwrap().2 = true;
        }
        Ok(())
    }

    pub(crate) fn get_terrain_updates(&mut self) -> eyre::Result<Vec<TerrainCommand>> {
        if let Some(fault) = &self.terrain.fault {
            eyre::bail!("{fault}");
        }
        if !self.terrain.enabled {
            return Ok(Vec::new());
        }
        let mut out = Vec::new();
        if let Some((token, sent)) = &self.terrain.reset {
            if !*sent {
                out.push(TerrainCommand::Reset { token: *token });
            }
        }
        for (&chunk, flight) in &self.terrain.flights {
            if !flight.sent {
                out.push(match &flight.data {
                    Some(data) => TerrainCommand::Apply {
                        chunk,
                        token: flight.token,
                        data: data.clone(),
                    },
                    None => TerrainCommand::Retire {
                        chunk,
                        token: flight.token,
                    },
                });
            }
        }
        let mut pending = self.inbound_model.updated_chunks().clone();
        pending.extend(
            self.terrain
                .flights
                .iter()
                .filter_map(|(chunk, flight)| flight.pause.is_some_and(|p| p.2).then_some(*chunk)),
        );
        for chunk in pending {
            // Do not encode waiting chunks on every tick at capacity. Existing
            // work (including Pause/frozen replacement) needs no extra slot.
            if !self.terrain.flights.contains_key(&chunk)
                && self.terrain.flights.len() >= MAX_PENDING_CHUNKS
            {
                continue;
            }
            if let Some(flight) = self.terrain.flights.get(&chunk) {
                if flight.data.is_none() || !flight.receipt
                    || flight.pause.is_some_and(|p| p.1 && !p.2) {
                    continue;
                }
            }
            let newer = self
                .inbound_model
                .peek_noita_update(chunk)
                .filter(|data| has_required(data));
            let frozen = self
                .terrain
                .flights
                .get(&chunk)
                .is_some_and(|f| f.pause.is_some_and(|p| p.2));
            if newer.is_none() && !frozen {
                self.inbound_model.clear_noita_update(chunk);
                continue;
            }
            if let Some(flight) = self.terrain.flights.get(&chunk) {
                if flight.data.is_none() || !flight.receipt {
                    continue;
                }
                if flight.pause.is_none() {
                    let token = self.terrain.token();
                    self.terrain.flights.get_mut(&chunk).unwrap().pause =
                        Some((token, false, false));
                }
                let flight = self.terrain.flights.get_mut(&chunk).unwrap();
                let pause = flight.pause.as_mut().unwrap();
                if !pause.1 {
                    out.push(TerrainCommand::Pause {
                        chunk,
                        token: pause.0,
                        application: flight.token,
                    });
                }
                // Never carry proxy-stale requirements into a new generation.
                // The game freezes old pending checks before this exact receipt.
                if !pause.2 {
                    continue;
                }
                let old = flight.data.as_ref().unwrap();
                let data = match newer {
                    Some(new) => Arc::new(compose_required(old, &new)?),
                    // Resume a frozen game even if a no-op consumed the
                    // pending change tracking while its receipt was in flight.
                    None => old.clone(),
                };
                // Composition can fail validation. Consume tracking only once
                // its exact replacement is ready to retain and replay.
                self.inbound_model.clear_noita_update(chunk);
                let token = self.terrain.token();
                self.terrain
                    .flights
                    .insert(chunk, Flight::new(token, Some(data.clone()), false));
                out.push(TerrainCommand::Apply { chunk, token, data });
            } else {
                if let Some(data) = newer {
                    let data = Arc::new(data);
                    let token = self.terrain.token();
                    self.terrain
                        .flights
                        .insert(chunk, Flight::new(token, Some(data.clone()), false));
                    self.inbound_model.clear_noita_update(chunk);
                    out.push(TerrainCommand::Apply { chunk, token, data });
                }
            }
        }
        out.sort_by_key(|command| match command {
            TerrainCommand::Apply { token, .. }
            | TerrainCommand::Retire { token, .. }
            | TerrainCommand::Reset { token }
            | TerrainCommand::Pause { token, .. } => *token,
        });
        // No fallible work follows this commit. If construction failed, all
        // retained unsent work stays eligible for replay. 'sent' means handed
        // to the transport, never applied: only a matching FIFO ACK proves it.
        // The production writer resets immediately on write/flush failure.
        for command in &out {
            match command {
                TerrainCommand::Reset { token } => {
                    if let Some(reset) = &mut self.terrain.reset {
                        if reset.0 == *token { reset.1 = true; }
                    }
                }
                TerrainCommand::Apply { chunk, token, .. }
                | TerrainCommand::Retire { chunk, token } => {
                    if let Some(flight) = self.terrain.flights.get_mut(chunk) {
                        if flight.token == *token { flight.sent = true; }
                    }
                }
                TerrainCommand::Pause { chunk, token, .. } => {
                    if let Some(pause) = self.terrain.flights.get_mut(chunk)
                        .and_then(|f| f.pause.as_mut()) {
                        if pause.0 == *token { pause.1 = true; }
                    }
                }
            }
        }
        Ok(out)
    }
}

fn pixels(data: &[u8]) -> Vec<world_model::encoding::RawPixel> {
    NoitaWorldUpdate::load(data)
        .runs
        .into_iter()
        .flat_map(|r| std::iter::repeat_n(r.data, r.length as usize))
        .collect()
}

fn encode_pixels(data: &[u8], values: Vec<world_model::encoding::RawPixel>) -> Vec<u8> {
    let header = NoitaWorldUpdate::load(data).header;
    let mut runner = world_model::encoding::PixelRunner::new();
    for pixel in values {
        runner.put_pixel(pixel);
    }
    runner
        .into_noita_update(header.x, header.y, 127, 127)
        .save()
}

fn has_required(data: &[u8]) -> bool {
    NoitaWorldUpdate::load(data)
        .runs
        .iter()
        .any(|r| r.data.material != u16::MAX)
}

fn shrink_required(chunk: ChunkCoord, current: &[u8], report: &[u8]) -> eyre::Result<Vec<u8>> {
    eyre::ensure!(
        validate_chunk(report)? == (chunk.0, chunk.1),
        "Terrain progress chunk mismatch"
    );
    let mut values = pixels(current);
    for (old, new) in values.iter_mut().zip(pixels(report)) {
        if old.material == u16::MAX {
            continue;
        }
        if new.material == u16::MAX {
            old.material = u16::MAX;
            old.flags = 0;
        } else {
            eyre::ensure!(
                *old == new,
                "Terrain progress changed a still-required target"
            );
        }
    }
    Ok(encode_pixels(current, values))
}

fn compose_required(current: &[u8], newer: &[u8]) -> eyre::Result<Vec<u8>> {
    eyre::ensure!(
        validate_chunk(current)? == validate_chunk(newer)?,
        "Terrain composition chunk mismatch"
    );
    let mut values = pixels(current);
    for (old, new) in values.iter_mut().zip(pixels(newer)) {
        if new.material != u16::MAX {
            *old = new;
        }
    }
    Ok(encode_pixels(current, values))
}
