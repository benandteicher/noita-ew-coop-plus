use audio::AudioManager;
use bitcode::{Decode, Encode};
use des::DesManager;
use image::DynamicImage::ImageRgba8;
use image::{ImageBuffer, Rgba, RgbaImage};
use messages::{MessageRequest, NetMsg};
use omni::OmniPeerId;
use proxy_opt::ProxyOpt;
use rustc_hash::{FxHashMap, FxHashSet};
use shared::message_socket::MessageSocket;
use shared::{Destination, NoitaInbound, NoitaOutbound, RemoteMessage, WorldPos};
use socket2::{Domain, Socket, Type};
use std::collections::HashMap;
use std::fs::{File, create_dir, remove_dir_all};
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU16, Ordering};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Mutex};
use std::{
    env,
    fmt::Display,
    io::{self},
    net::{SocketAddr, TcpListener},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
use world::WorldManager;

use crate::lobby_code::LobbyKind;
use crate::mod_manager::get_mods;
use crate::net::world::world_model::chunk::{Pixel, PixelFlags};
use crate::net::world::world_model::{ChunkCoord, ChunkData};
use crate::paths::Paths;
use crate::player_cosmetics::{PlayerPngDesc, create_player_png, get_player_skin};
use crate::steam_helper::LobbyExtraData;
use crate::{
    AudioSettings, DefaultSettings, GameSettings,
    bookkeeping::save_state::{SaveState, SaveStateEntry},
    bookkeeping::run_settings::{self,SessionRules,RuleAdvertisement,RunIntent,SavedRunSettings,WorldReceipt},
    game_settings::{GameMode, LocalHealthMode},
};
use shared::des::ProxyToDes;
use tangled::Reliability;
use tracing::{error, info, warn};
mod audio;
mod des;
pub mod messages;
mod proxy_opt;
#[cfg(test)]
mod terrain_delivery_tests;
#[cfg(test)]
mod network_resource_tests;
#[cfg(test)]
mod session_rules_tests;
pub mod steam_networking;
pub mod world;

pub(crate) fn ws_encode_proxy(key: &'static str, value: impl Display) -> NoitaInbound {
    let mut buf = Vec::new();
    buf.push(2);
    write!(buf, "{key} {value}").unwrap();
    NoitaInbound::RawMessage(buf)
}

pub fn ws_encode_proxy_bin(key: u8, data: &[u8]) -> NoitaInbound {
    let mut buf = Vec::new();
    buf.push(3);
    buf.push(key);
    buf.extend(data);
    NoitaInbound::RawMessage(buf)
}

pub(crate) fn ws_encode_mod(peer: OmniPeerId, data: &[u8]) -> NoitaInbound {
    let mut buf = Vec::new();
    buf.push(1u8);
    buf.extend_from_slice(&peer.0.to_le_bytes());
    buf.extend_from_slice(data);
    NoitaInbound::RawMessage(buf)
}

#[derive(Encode, Decode)]
pub(crate) struct RunInfo {
    pub(crate) seed: u64,
}

impl SaveStateEntry for RunInfo {
    /// Bump whenever any save state format changes incompatibly: `SaveState` discards a save
    /// directory that has no run info under this name.
    const FILENAME: &'static str = "run_info_v2";
}

pub(crate) struct NetInnerState {
    pub(crate) ms: Option<MessageSocket<NoitaOutbound, NoitaInbound>>,
    world: WorldManager,
    des: DesManager,
    audio: Option<AudioManager>,
    explosion_data: Vec<ExplosionData>,
    had_a_disconnect: bool,
    flags: FxHashSet<String>,
    local_world_ready: bool,
    initializing_local: bool,
    gated_outbound: Vec<Vec<u8>>,
    gated_inbound: Vec<Vec<u8>>,
    gate_bytes:usize,gate_started:Option<Instant>,gate_error:Option<String>,
}
const MAX_WORLD_GATE_BYTES:usize=64*1024*1024;
const MAX_WORLD_GATE_MESSAGES:usize=8192;
struct SessionSaveGuard {state:SaveState,successful:bool}
impl Drop for SessionSaveGuard {fn drop(&mut self){if !self.successful || thread::panicking(){self.state.mark_game_not_started();}}}

impl NetInnerState {
    fn disconnect_local(&mut self) {
        self.ms = None;
        self.had_a_disconnect = true;
        self.gated_outbound.clear();self.gated_inbound.clear();self.initializing_local=false;self.gate_bytes=0;self.gate_started=None;
        // Invalidate all transport-queued tokens before any more game input.
        // A partial write/failed flush may have delivered only a batch prefix.
        self.world.terrain_connection_reset();
    }

    fn flush_local(&mut self) {
        if let Some(ws) = &mut self.ms
            && let Err(err) = ws.flush()
        {
            warn!("Websocket flush not ok: {err}");
            self.disconnect_local();
        }
    }

    pub(crate) fn try_ms_write(&mut self, data: &NoitaInbound) {
        if self.ms.is_some() && !self.local_world_ready && !self.initializing_local {self.hold_world_message(bitcode::encode(data),false);return;}
        if let Some(ws) = &mut self.ms
            && let Err(err) = ws.write(data)
        {
            error!("Error occurred while sending to websocket: {}", err);
            self.disconnect_local();
        };
    }
    pub(crate) fn try_ws_write_option(&mut self, key: &str, value: impl ProxyOpt) {
        let mut buf = Vec::new();
        buf.push(2);
        value.write_opt(&mut buf, key);
        let message = NoitaInbound::RawMessage(buf);
        self.try_ms_write(&message);
    }
    fn hold_world_message(&mut self,bytes:Vec<u8>,inbound:bool) {
        if self.gate_bytes.saturating_add(bytes.len())>MAX_WORLD_GATE_BYTES || self.gated_inbound.len()+self.gated_outbound.len()>=MAX_WORLD_GATE_MESSAGES {
            self.gate_error=Some("World/rules receipt did not complete before the startup quarantine limit. The session stopped without accepting gameplay or resetting the checkpoint.".into());self.disconnect_local();return;
        }
        self.gate_bytes+=bytes.len();if inbound{self.gated_inbound.push(bytes);}else{self.gated_outbound.push(bytes);}
    }
}

pub mod omni;

enum FlagType {
    Normal(String),
    Slow(String, usize),
    Moon(String, i32, i32, bool),
    Stevari(String, i32, i32),
}

/// Cap on the declared uncompressed size of a peer-supplied message.
///
/// `lz4_flex::decompress_size_prepended` reads the u32 length prefix and
/// allocates it *before* decompressing, so ~8 hostile bytes could request a
/// 4 GiB allocation. Read the prefix ourselves and reject implausible sizes.
const MAX_DECOMPRESSED_LEN: usize = 64 * 1024 * 1024;

fn decompress_bounded(data: &[u8]) -> Option<Vec<u8>> {
    let (size, rest) = lz4_flex::block::uncompressed_size(data).ok()?;
    if size > MAX_DECOMPRESSED_LEN {
        warn!("Rejecting message declaring {size} decompressed bytes");
        return None;
    }
    lz4_flex::decompress(rest, size).ok()
}

fn get_flags(mut flags: String) -> Option<FlagType> {
    if flags.is_empty() {
        return None;
    }
    let tag = flags.remove(0);
    // `flags` is the payload of NetMsg::Flags, i.e. it comes from a peer. These
    // used to index c[1]..c[3] directly, so a short message panicked the net
    // thread; match on the split slice instead.
    let c: Vec<&str> = flags.split(' ').collect();
    match tag {
        '0' => Some(FlagType::Normal(flags)),
        '1' => match c.as_slice() {
            [slow, name, ..] => Some(FlagType::Slow(
                (*name).to_string(),
                slow.parse().unwrap_or_default(),
            )),
            _ => None,
        },
        '2' => match c.as_slice() {
            [x, y, flag, name, ..] => Some(FlagType::Moon(
                (*name).to_string(),
                x.parse().unwrap_or_default(),
                y.parse().unwrap_or_default(),
                *flag == "1",
            )),
            _ => None,
        },
        '3' => match c.as_slice() {
            [x, y, name, ..] => Some(FlagType::Stevari(
                (*name).to_string(),
                x.parse().unwrap_or_default(),
                y.parse().unwrap_or_default(),
            )),
            _ => None,
        },
        _ => None,
    }
}

/// A safer subset of Paths for use with NetManager
#[derive(Debug)]
pub struct NetManagerPaths {
    pub noita_quantew_install: PathBuf,
    pub noita_quantew_player_spritesheet: PathBuf,
    pub noita_save: Option<PathBuf>,
}

impl NetManagerPaths {
    pub fn try_from_paths(paths: &Paths) -> Option<NetManagerPaths> {
        let noita_quantew_install = paths.noita_quantew_install.as_ref()?;
        let noita_quantew_player_spritesheet = paths.noita_quantew_player_spritesheet.as_ref()?;
        let noita_save = paths.noita_save.as_ref();
        Some(NetManagerPaths {
            noita_quantew_install: noita_quantew_install.clone(),
            noita_quantew_player_spritesheet: noita_quantew_player_spritesheet.clone(),
            noita_save: noita_save.cloned(),
        })
    }
}

pub struct NetManagerInit {
    pub my_nickname: String,
    pub save_state: SaveState,
    pub cosmetics: (bool, bool, bool),
    pub paths: NetManagerPaths,
    pub player_png_desc: PlayerPngDesc,
    pub noita_port: u16,
}

pub struct NetManager {
    pub peer: omni::PeerVariant,
    pub pending_settings: Mutex<GameSettings>,
    pub session_rules: Mutex<SessionRules>,
    pub settings: Mutex<GameSettings>,
    pub continue_running: AtomicBool,
    pub accept_local: AtomicBool,
    pub local_connected: AtomicBool,
    pub stopped: AtomicBool,
    pub error: Mutex<Option<io::Error>>,
    pub init_settings: NetManagerInit,
    pub camera_pos: (AtomicI32, AtomicI32),
    pub player_pos: (AtomicI32, AtomicI32),
    pub enable_recorder: AtomicBool,
    pub end_run: AtomicBool,
    pub ban_list: Mutex<Vec<OmniPeerId>>,
    pub kick_list: Mutex<Vec<OmniPeerId>>,
    pub no_more_players: AtomicBool,
    pub no_chunkmap_to_players: AtomicBool,
    pub no_chunkmap: AtomicBool,
    dont_kick: Mutex<Vec<OmniPeerId>>,
    pub dirty: AtomicBool,
    pub actual_noita_port: AtomicU16,
    pub active_mods: Mutex<Vec<String>>,
    pub nicknames: Mutex<HashMap<OmniPeerId, String>>,
    pub minas: Mutex<HashMap<OmniPeerId, RgbaImage>>,
    pub new_desc: Mutex<Option<PlayerPngDesc>>,
    loopback_channel: (
        crossbeam::channel::Sender<NetMsg>,
        crossbeam::channel::Receiver<NetMsg>,
    ),
    pub audio: Mutex<AudioSettings>,
    push_to_talk: AtomicBool,
    is_dead: AtomicBool,
    is_polied: AtomicBool,
    is_cess: AtomicBool,
    duplicate: AtomicBool,
    pub back_out: AtomicBool,
    pub chunk_map: Mutex<FxHashMap<ChunkCoord, RgbaImage>>,
    #[allow(clippy::type_complexity)]
    pub players_sprite: Mutex<FxHashMap<OmniPeerId, (Option<WorldPos>, bool, bool, RgbaImage)>>,
    pub reset_map: AtomicBool,
    colors: Mutex<FxHashMap<u16, u32>>,
}

impl NetManager {
    pub fn new(peer: omni::PeerVariant, init: NetManagerInit, audio: AudioSettings) -> Arc<Self> {
        Self {
            peer,
            pending_settings: Mutex::new(GameSettings::default()),
            session_rules: Mutex::new(SessionRules::default()),
            settings: Mutex::new(GameSettings::default()),
            continue_running: AtomicBool::new(true),
            accept_local: AtomicBool::new(false),
            local_connected: AtomicBool::new(false),
            stopped: AtomicBool::new(false),
            error: Default::default(),
            init_settings: init,
            camera_pos: Default::default(),
            player_pos: Default::default(),
            enable_recorder: AtomicBool::new(false),
            end_run: AtomicBool::new(false),
            ban_list: Default::default(),
            kick_list: Default::default(),
            no_more_players: AtomicBool::new(false),
            dont_kick: Default::default(),
            dirty: AtomicBool::new(false),
            actual_noita_port: AtomicU16::new(0),
            active_mods: Default::default(),
            nicknames: Default::default(),
            minas: Default::default(),
            new_desc: Default::default(),
            loopback_channel: crossbeam::channel::unbounded(),
            audio: audio.into(),
            push_to_talk: Default::default(),
            is_dead: Default::default(),
            is_polied: Default::default(),
            is_cess: Default::default(),
            duplicate: Default::default(),
            back_out: Default::default(),
            chunk_map: Default::default(),
            players_sprite: Default::default(),
            reset_map: AtomicBool::new(false),
            no_chunkmap_to_players: AtomicBool::new(true),
            no_chunkmap: AtomicBool::new(true),
            colors: Default::default(),
        }
        .into()
    }

    pub(crate) fn update_lobby_data(&self, data: LobbyExtraData) {
        match &self.peer {
            omni::PeerVariant::Tangled(_) => {}
            omni::PeerVariant::Steam(steam_peer) => {
                steam_peer.update_lobby_data(data);
            }
        }
    }

    pub(crate) fn send(&self, peer: OmniPeerId, msg: &NetMsg, reliability: Reliability) {
        if peer == self.peer.my_id() {
            // Shortcut for sending stuff to myself
            let _ = self.loopback_channel.0.send(msg.clone());
        } else {
            let encoded = lz4_flex::compress_prepend_size(&bitcode::encode(msg));
            let len = encoded.len();
            if let Err(err) = self.peer.send(peer, encoded, reliability) {
                if cfg!(debug_assertions) {
                    warn!(
                        "Error while sending message of len {}: {} {:?}",
                        len, err, msg
                    )
                } else {
                    warn!("Error while sending message of len {}: {}", len, err)
                }
            }
        }
    }

    pub(crate) fn broadcast(&self, msg: &NetMsg, reliability: Reliability) {
        let encoded = lz4_flex::compress_prepend_size(&bitcode::encode(msg));
        let len = encoded.len();
        if let Err(err) = self.peer.broadcast(encoded, reliability) {
            warn!("Error while broadcasting message of len {}: {}", len, err)
        }
    }

    fn clean_dir(path: PathBuf) {
        let tmp = path.parent().unwrap().join("tmp");
        if tmp.exists() {
            remove_dir_all(tmp.clone()).ok();
        }
        create_dir(tmp).ok();
    }

    pub fn new_desc(&self, desc: PlayerPngDesc, player_image: RgbaImage) {
        create_player_png(
            self.peer.my_id(),
            &self.init_settings.paths.noita_quantew_install,
            &self.init_settings.paths.noita_quantew_player_spritesheet,
            &desc,
            self.is_host(),
            &mut self.players_sprite.lock().unwrap(),
        );
        self.minas
            .lock()
            .unwrap()
            .insert(self.peer.my_id(), get_player_skin(player_image, desc));
        self.broadcast(
            &NetMsg::PlayerColor(
                desc,
                self.is_host(),
                Some(self.peer.my_id()),
                self.init_settings.my_nickname.clone(),
            ),
            Reliability::Reliable,
        );
    }

    pub(crate) fn start_inner(
        self: Arc<NetManager>,
        player_path: PathBuf,
        mut kind: Option<LobbyKind>,
    ) -> io::Result<()> {
        Self::clean_dir(player_path.clone());
        if !self.init_settings.cosmetics.0 {
            File::create(player_path.parent().unwrap().join("tmp/no_crown"))?;
        }
        if !self.init_settings.cosmetics.1 {
            File::create(player_path.parent().unwrap().join("tmp/no_amulet"))?;
        }
        if !self.init_settings.cosmetics.2 {
            File::create(player_path.parent().unwrap().join("tmp/no_amulet_gem"))?;
        }

        let socket = Socket::new(Domain::IPV4, Type::STREAM, None)?;
        // This allows several proxies to listen on the same address.
        // While this works, I couldn't get Noita to reliably connect to correct proxy instances on my os (linux).
        if env::var_os("NP_ALLOW_REUSE_ADDR").is_some() {
            info!("Address reuse allowed");
            if let Err(err) = socket.set_reuse_address(true) {
                error!("Could not allow to reuse address: {}", err)
            }
            #[cfg(target_os = "linux")]
            if let Err(err) = socket.set_reuse_port(true) {
                error!("Could not allow to reuse port: {}", err)
            }
        }
        let address: SocketAddr = env::var("NP_NOITA_ADDR")
            .ok()
            .and_then(|x| x.parse().ok())
            .unwrap_or_else(|| {
                SocketAddr::new("127.0.0.1".parse().unwrap(), self.init_settings.noita_port)
            });
        info!("Listening for noita connection on {}", address);
        socket
            .bind(&address.into())
            .map_err(|e| io::Error::new(e.kind(), format!("Error binding to {address}: {e}")))?;
        socket.listen(1)?;
        socket.set_nonblocking(true)?;

        let actual_port = socket.local_addr()?.as_socket().unwrap().port();
        self.actual_noita_port.store(actual_port, Ordering::Relaxed);
        info!("Actual Noita port: {actual_port}");

        if self.is_host() {
            self.accept_local.store(true, Ordering::Relaxed);
        }

        let local_server: TcpListener = socket.into();

        let is_host = self.is_host();
        info!("Is host: {is_host}");

        let audio_settings = self.audio.lock().unwrap().clone();
        let audio_state = if !audio_settings.disabled {
            Some(AudioManager::new(audio_settings))
        } else {
            None
        };

        let (world, rx, recv, sendm, tx) = WorldManager::new(
            is_host,
            self.peer.my_id(),
            self.init_settings.save_state.clone(),
        );
        let mut state = NetInnerState {
            ms: None,
            world,
            explosion_data: Vec::new(),
            des: DesManager::new(is_host, self.init_settings.save_state.clone()),
            had_a_disconnect: false,
            flags: self.init_settings.save_state.load().unwrap_or_default(),
            local_world_ready: crate::frozen_runtime::ID.is_none(),initializing_local:false,gated_outbound:Vec::new(),gated_inbound:Vec::new(),gate_bytes:0,gate_started:None,gate_error:None,
            audio: audio_state,
        };
        let mut save_guard=SessionSaveGuard {state:self.init_settings.save_state.clone(),successful:false};
        let mut last_iter = Instant::now();
        let path = crate::player_path(self.init_settings.paths.noita_quantew_install.clone());
        let player_image = if path.exists() {
            image::open(path)
                .unwrap_or(ImageRgba8(RgbaImage::new(20, 20)))
                .crop(1, 1, 7, 16)
                .into_rgba8()
        } else {
            RgbaImage::new(7, 17)
        };
        // Create appearance files for local player.
        create_player_png(
            self.peer.my_id(),
            &self.init_settings.paths.noita_quantew_install,
            &self.init_settings.paths.noita_quantew_player_spritesheet,
            &self.init_settings.player_png_desc,
            self.is_host(),
            &mut self.players_sprite.lock().unwrap(),
        );
        self.nicknames
            .lock()
            .unwrap()
            .insert(self.peer.my_id(), self.init_settings.my_nickname.clone());
        self.minas.lock().unwrap().insert(
            self.peer.my_id(),
            get_player_skin(player_image.clone(), self.init_settings.player_png_desc),
        );

        while self.continue_running.load(Ordering::Relaxed) {
            if let Some(k) = kind
                && let Some(n) = self.peer.lobby_id()
            {
                let c = crate::lobby_code::LobbyCode { kind: k, code: n };
                info!("Lobby ID: {}", c.serialize());
                kind = None
            }
            if self.end_run.load(Ordering::Relaxed) {
                for id in self.peer.iter_peer_ids() {
                    self.send(id, &NetMsg::EndRun, Reliability::Reliable);
                }
                state.try_ms_write(&ws_encode_proxy("end_run", self.peer.my_id().to_string()));
                self.end_run(&mut state);
                self.end_run.store(false, Ordering::Relaxed);
            }
            self.local_connected
                .store(state.ms.is_some(), Ordering::Relaxed);
            if state.ms.is_none() && self.accept_local.load(Ordering::SeqCst) {
                thread::sleep(Duration::from_millis(10));
                if let Ok((mut stream, addr)) = local_server.accept() {
                    info!("New stream incoming from {}", addr);
                    stream.set_nodelay(true).ok();
                    stream.set_nonblocking(false).ok();
                    if let Err(err) = shared::terrain_protocol::negotiate(&mut stream) {
                        error!("Local protocol negotiation failed: {err}; use matching proxy/ewext/Lua");
                        continue;
                    }
                    state.world.terrain_connection_reset();
                    state.local_world_ready=crate::frozen_runtime::ID.is_none();state.gated_outbound.clear();state.gated_inbound.clear();state.gate_bytes=0;state.gate_error=None;
                    state.ms = MessageSocket::new(stream)
                        .inspect_err(|e| error!("Could not init websocket: {:?}", e))
                        .ok();
                    if state.ms.is_some() {
                        if let Err(error)=self.on_ms_connection(&mut state) {
                            if error.kind()==io::ErrorKind::WouldBlock {state.disconnect_local();}
                            else {self.fail_agreement("world-connection-preparation-failed",format!("Cannot prepare game session: {error}"),Some(&mut state));}
                        }
                    }
                }
            }
            state.flush_local();
            // Scoped so the locks are released before handling network events, which may kick.
            {
                let mut to_kick = self.kick_list.lock().unwrap();
                let mut dont_kick = self.dont_kick.lock().unwrap();
                if self.no_more_players.load(Ordering::Relaxed) {
                    if dont_kick.is_empty() {
                        dont_kick.extend(self.peer.iter_peer_ids())
                    } else {
                        for peer in self.peer.iter_peer_ids() {
                            if !dont_kick.contains(&peer) {
                                to_kick.push(peer);
                            }
                        }
                    }
                } else {
                    dont_kick.clear()
                }
                {
                    let list = self.ban_list.lock().unwrap();
                    for peer in list.iter() {
                        if self.peer.iter_peer_ids().contains(peer) {
                            to_kick.push(*peer)
                        }
                    }
                }
                for peer in to_kick.iter() {
                    info!("player kicked: {}", peer);
                    state.try_ms_write(&ws_encode_proxy("leave", peer.as_hex()));
                    state.world.handle_peer_left(*peer);
                    self.send(*peer, &NetMsg::Kick, Reliability::Reliable);
                    self.broadcast(
                        &NetMsg::PeerDisconnected { id: *peer },
                        Reliability::Reliable,
                    );
                }
                to_kick.clear();
            }
            for net_event in self.peer.recv() {
                self.clone().handle_network_event(
                    &mut state,
                    &player_image,
                    net_event,
                    &tx,
                    &sendm,
                );
            }
            for net_msg in self.loopback_channel.1.try_iter() {
                self.clone().handle_net_msg(
                    &mut state,
                    &player_image,
                    self.peer.my_id(),
                    net_msg,
                    &tx,
                    &sendm,
                );
            }
            // Handle all available messages from Noita.
            while let Some(ws) = &mut state.ms {
                let msg = ws.try_read();
                match msg {
                    Ok(Some(msg)) => {
                        self.handle_mod_message_2(msg, &mut state, &sendm);
                    }
                    Ok(None) => break,
                    Err(err) => {
                        warn!("Game closed (Lost connection to noita instance: {})", err);
                        state.disconnect_local();
                    }
                }
            }
            for msg in state.world.get_emitted_msgs() {
                self.do_message_request(msg)
            }
            state.world.update();

            if state.ms.is_some() && state.local_world_ready {
                match state.world.get_terrain_updates() {
                    Ok(updates) => for update in updates {
                        state.try_ms_write(&ws_encode_proxy_bin(2, &update.encode(state.world.world_number(), state.world.terrain_session())));
                        if state.ms.is_none() { break; }
                    },
                    Err(err) => {
                        error!("Terrain session stopped: {err}");
                        state.disconnect_local();
                    }
                }
            }

            if state.had_a_disconnect {
                self.broadcast(&NetMsg::NoitaDisconnected, Reliability::Reliable);
                if self.is_host() {
                    state.des.noita_disconnected(self.peer.my_id());
                }
                state.had_a_disconnect = false;
            }

            let des_pending = state.des.pending_messages();
            for (dest, msg) in des_pending {
                self.send(dest, &NetMsg::ForwardProxyToDes(msg), Reliability::Reliable);
            }

            let mut audio_data = Vec::new();
            while let Some(data) = state
                .audio
                .as_mut()
                .and_then(|audio| audio.recv_audio().ok())
            {
                audio_data.push(data)
            }
            if !audio_data.is_empty() {
                let audio = self.audio.lock().unwrap();
                if !audio.mute_in
                    && (!audio.mute_in_while_dead || !self.is_dead.load(Ordering::Relaxed))
                    && (!audio.mute_in_while_polied
                        || !self.is_polied.load(Ordering::Relaxed)
                        || self.is_dead.load(Ordering::Relaxed))
                    && (!audio.push_to_talk || self.push_to_talk.load(Ordering::Relaxed))
                    && !self.is_cess.load(Ordering::Relaxed)
                    && audio.global_input_volume != 0.0
                {
                    let (x, y) = if audio.player_position {
                        (
                            self.player_pos.0.load(Ordering::Relaxed),
                            self.player_pos.1.load(Ordering::Relaxed),
                        )
                    } else {
                        (
                            self.camera_pos.0.load(Ordering::Relaxed),
                            self.camera_pos.1.load(Ordering::Relaxed),
                        )
                    };
                    let data = NetMsg::AudioData(
                        audio_data,
                        audio.global,
                        x,
                        y,
                        audio.global_input_volume,
                    );
                    if audio.loopback {
                        self.send(self.peer.my_id(), &data, Reliability::Reliable)
                    }
                    self.broadcast(&data, Reliability::Reliable);
                }
            }
            let mut map = FxHashMap::default();
            while let Ok((ch, img)) = rx.try_recv() {
                map.insert(ch, img);
            }
            if !self.no_chunkmap.load(Ordering::Relaxed) && !map.is_empty() {
                let chunk_map = &mut self.chunk_map.lock().unwrap();
                for (ch, img) in map {
                    chunk_map.insert(ch, img);
                }
            }
            if self.is_host() {
                let mut map = FxHashMap::default();
                while let Ok((ch, c)) = recv.try_recv() {
                    map.insert(ch, c);
                }
                if !self.no_chunkmap_to_players.load(Ordering::Relaxed) && !map.is_empty() {
                    let data = NetMsg::MapData(map);
                    self.broadcast(&data, Reliability::Reliable)
                }
            }
            // Don't do excessive busy-waiting;
            let min_update_time = Duration::from_millis(8);
            let elapsed = last_iter.elapsed();
            if elapsed < min_update_time {
                thread::sleep(min_update_time - elapsed);
            }
            if state.ms.is_some() && !state.local_world_ready && state.gate_started.is_some_and(|started|started.elapsed()>Duration::from_secs(120)) {state.gate_error=Some("No actual world/rules receipt arrived within 120 seconds. Startup stopped without accepting gameplay or resetting the checkpoint.".into());state.disconnect_local();}
            self.consume_gate_failure(&mut state);
            last_iter = Instant::now();
        }
        save_guard.successful=true;Ok(())
    }
    fn handle_network_event(
        self: Arc<NetManager>,
        state: &mut NetInnerState,
        player_image: &ImageBuffer<Rgba<u8>, Vec<u8>>,
        net_event: omni::OmniNetworkEvent,
        tx: &Sender<(ChunkCoord, ChunkData)>,
        sendm: &Sender<FxHashMap<u16, u32>>,
    ) {
        match net_event {
            omni::OmniNetworkEvent::PeerConnected(id) => {
                self.broadcast(&NetMsg::Welcome, Reliability::Reliable);
                info!("Peer connected {id}");
                if self.peer.my_id() == self.peer.host_id() {
                    info!("Sending start game message");
                    self.send(
                        id,
                        &NetMsg::StartGame {
                            settings: self.settings.lock().unwrap().clone(),
                            init: true,
                        },
                        Reliability::Reliable,
                    );
                    if crate::frozen_runtime::ID.is_some(){self.send(id,&NetMsg::SessionRules(self.rule_advertisement()),Reliability::Reliable);}
                }
                if id != self.peer.my_id() {
                    // Create temporary appearance files for new player.
                    info!("Created temporary appearance for {id}");
                    create_player_png(
                        id,
                        &self.init_settings.paths.noita_quantew_install,
                        &self.init_settings.paths.noita_quantew_player_spritesheet,
                        &PlayerPngDesc::default(),
                        id == self.peer.host_id(),
                        &mut self.players_sprite.lock().unwrap(),
                    );
                    info!("Sending PlayerColor to {id}");
                    self.send(
                        id,
                        &NetMsg::PlayerColor(
                            self.init_settings.player_png_desc,
                            self.is_host(),
                            Some(self.peer.my_id()),
                            self.init_settings.my_nickname.clone(),
                        ),
                        Reliability::Reliable,
                    );
                    if self.is_host() && !self.no_chunkmap_to_players.load(Ordering::Relaxed) {
                        let colors = self.colors.lock().unwrap().clone();
                        info!("sending {} mat data to {id}", colors.len());
                        if !colors.is_empty() {
                            self.send(id, &NetMsg::MatData(colors), Reliability::Reliable);
                        }
                        let map = state.world.get_chunks();
                        info!("sending {} chunks to {id}", map.len());
                        if !map.is_empty() {
                            self.send(id, &NetMsg::MapData(map), Reliability::Reliable);
                        }
                    }
                }
                state.try_ms_write(&ws_encode_proxy("join", id.as_hex()));
            }
            omni::OmniNetworkEvent::PeerDisconnected(id) => {
                state.try_ms_write(&ws_encode_proxy("leave", id.as_hex()));
                state.world.handle_peer_left(id);
                state.des.noita_disconnected(id);
                state.try_ms_write(&NoitaInbound::ProxyToDes(ProxyToDes::RemoveEntities(
                    id.into(),
                )));
                if id == self.peer.host_id() {
                    self.back_out.store(true, Ordering::Relaxed)
                }
            }
            omni::OmniNetworkEvent::SendBacklogStuck(id) => {
                if self.is_host() {
                    error!(
                        "Kicking {id}: our reliable messages to them have been backed up for too long"
                    );
                    self.kick_list.lock().unwrap().push(id);
                } else {
                    error!(
                        "Leaving the lobby: our reliable messages to {id} have been backed up for too long"
                    );
                    self.back_out.store(true, Ordering::Relaxed);
                }
            }
            omni::OmniNetworkEvent::Message { src, data } => {
                let Some(net_msg) = decompress_bounded(&data)
                    .and_then(|decomp| bitcode::decode::<NetMsg>(&decomp).ok())
                else {
                    return;
                };
                self.handle_net_msg(state, player_image, src, net_msg, tx, sendm);
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn handle_net_msg(
        self: Arc<NetManager>,
        state: &mut NetInnerState,
        player_image: &ImageBuffer<Rgba<u8>, Vec<u8>>,
        src: OmniPeerId,
        net_msg: NetMsg,
        tx: &Sender<(ChunkCoord, ChunkData)>,
        sendm: &Sender<FxHashMap<u16, u32>>,
    ) {
        match net_msg {
            NetMsg::AudioData(data, global, tx, ty, vol) => {
                let Some(state_audio) = &mut state.audio else {
                    return;
                };
                if self.is_cess.load(Ordering::Relaxed) {
                    return;
                }
                let audio = self.audio.lock().unwrap().clone();
                let pos = if audio.player_position {
                    (
                        self.player_pos.0.load(Ordering::Relaxed),
                        self.player_pos.1.load(Ordering::Relaxed),
                    )
                } else {
                    (
                        self.camera_pos.0.load(Ordering::Relaxed),
                        self.camera_pos.1.load(Ordering::Relaxed),
                    )
                };
                state_audio.play_audio(audio, pos, src, data, global, (tx, ty), vol);
            }
            NetMsg::PlayerPosition(x, y, is_dead, does_exist) => {
                let map = &mut self.players_sprite.lock().unwrap();
                map.entry(src).and_modify(|(w, b, d, _)| {
                    *w = Some(WorldPos::from((x, y)));
                    *b = is_dead;
                    *d = does_exist
                });
            }
            NetMsg::MapData(chunks) => {
                for (ch, c) in chunks {
                    let _ = tx.send((ch, c));
                }
            }
            NetMsg::MatData(colors) => {
                info!("receiving mat data from {src}");
                let _ = sendm.send(colors);
            }
            NetMsg::RequestMods => {
                if let Some(n) = &self.init_settings.paths.noita_save {
                    let res = get_mods(n);
                    if let Ok(mods) = res {
                        self.send(src, &NetMsg::Mods { mods }, Reliability::Reliable)
                    }
                }
            }
            NetMsg::Mods { mods } => *self.active_mods.lock().unwrap() = mods,
            NetMsg::Welcome => {}
            NetMsg::PeerDisconnected { id } => {
                info!("player kicked: {}", id);
                state.try_ms_write(&ws_encode_proxy("leave", id.as_hex()));
                state.world.handle_peer_left(id);
            }
            NetMsg::EndRun => {
                state.try_ms_write(&ws_encode_proxy("end_run", self.peer.my_id().to_string()))
            }
            NetMsg::StartGame { settings, init } => {
                if src!=self.peer.host_id() {warn!("Ignoring non-host run settings from {src}");return;}
                *self.settings.lock().unwrap() = settings;
                info!("Settings updated");
                self.accept_local.store(crate::frozen_runtime::ID.is_none(), Ordering::SeqCst);
                state.world.reset();
                state.des.reset();
                state.flags.clear();
                if !init {
                    self.reset_map.store(true, Ordering::Relaxed);
                }
            }
            NetMsg::SessionRules(advertisement)=>{
                if src!=self.peer.host_id() || self.is_host(){warn!("Ignoring non-host session rules from {src}");return;}
                let mut session=self.session_rules.lock().unwrap();
                let changed_live=session.game_received && (session.run_id!=advertisement.run_id || session.rules_id!=advertisement.rules_id) && state.ms.is_some();
                if changed_live {state.disconnect_local();}
                let same=session.run_id==advertisement.run_id && session.rules_id==advertisement.rules_id;
                if !same {*session=SessionRules::default();}
                if changed_live {session.failure=Some("Host moved to a new run while the old local world remained open. Close that game and rejoin before starting the next run; no old checkpoint was reset.".into());}
                session.run_id=advertisement.run_id;session.rules_id=advertisement.rules_id;session.locked=advertisement.locked;session.legacy_unverified=advertisement.legacy_unverified;
                session.intent=if advertisement.legacy_unverified{RunIntent::Legacy}else{RunIntent::Join};
                *self.settings.lock().unwrap()=advertisement.settings;
                self.accept_local.store(advertisement.ready && !changed_live && session.failure.is_none(),Ordering::SeqCst);
            },
            NetMsg::ModRaw { data } => {
                state.try_ms_write(&ws_encode_mod(src, &data));
            }
            NetMsg::ModCompressed { data } => {
                if let Some(decompressed) = decompress_bounded(&data) {
                    state.try_ms_write(&ws_encode_mod(src, &decompressed));
                }
            }
            NetMsg::WorldMessage(msg) => state.world.handle_msg(src, msg),
            NetMsg::PlayerColor(rgb, host, pong, name) => {
                info!("Player appearance created for {}", src);
                // Create proper appearance files for new player.
                create_player_png(
                    src,
                    &self.init_settings.paths.noita_quantew_install,
                    &self.init_settings.paths.noita_quantew_player_spritesheet,
                    &rgb,
                    host,
                    &mut self.players_sprite.lock().unwrap(),
                );
                self.nicknames.lock().unwrap().insert(src, name);
                self.minas
                    .lock()
                    .unwrap()
                    .insert(src, get_player_skin(player_image.clone(), rgb));
                if let Some(id) = pong
                    && id != self.peer.my_id()
                {
                    self.send(
                        id,
                        &NetMsg::PlayerColor(
                            self.init_settings.player_png_desc,
                            self.is_host(),
                            None,
                            self.init_settings.my_nickname.clone(),
                        ),
                        Reliability::Reliable,
                    );
                }
            }
            NetMsg::Kick => self.back_out.store(true, Ordering::Relaxed),
            NetMsg::RemoteMsg(remote_message) => self.handle_remote_msg(state, src, remote_message),
            NetMsg::ForwardDesToProxy(des_to_proxy) => {
                state.des.handle_noita_msg(src, des_to_proxy)
            }
            NetMsg::ForwardProxyToDes(proxy_to_des) => {
                state.try_ms_write(&NoitaInbound::ProxyToDes(proxy_to_des));
            }
            NetMsg::NoitaDisconnected => {
                state.des.noita_disconnected(src);
                state.try_ms_write(&NoitaInbound::ProxyToDes(ProxyToDes::RemoveEntities(
                    src.into(),
                )));
                state.try_ms_write(&ws_encode_proxy("dc", src.as_hex()));
            }
            NetMsg::Flags(flags) => {
                if let Some(flag) = get_flags(flags) {
                    match flag {
                        FlagType::Normal(flag) => {
                            let new = self.duplicate.load(Ordering::Relaxed)
                                || state.flags.insert(flag.clone());
                            self.send(
                                src,
                                &NetMsg::RespondFlagNormal(flag, new),
                                Reliability::Reliable,
                            )
                        }
                        FlagType::Slow(flag, ent) => {
                            let new =
                                self.duplicate.load(Ordering::Relaxed) || state.flags.insert(flag);
                            self.send(
                                src,
                                &NetMsg::RespondFlagSlow(ent, new),
                                Reliability::Reliable,
                            )
                        }
                        FlagType::Moon(flag, x, y, b) => {
                            let new =
                                self.duplicate.load(Ordering::Relaxed) || state.flags.insert(flag);
                            if new {
                                self.send(
                                    src,
                                    &NetMsg::RespondFlagMoon(x, y, b),
                                    Reliability::Reliable,
                                )
                            }
                        }
                        FlagType::Stevari(flag, x, y) => {
                            let new =
                                self.duplicate.load(Ordering::Relaxed) || state.flags.insert(flag);
                            if new {
                                self.broadcast(
                                    &NetMsg::RespondFlagStevari(x, y, src),
                                    Reliability::Reliable,
                                );
                                self.send(
                                    self.peer.my_id(),
                                    &NetMsg::RespondFlagStevari(x, y, src),
                                    Reliability::Reliable,
                                )
                            }
                        }
                    }
                }
            }
            NetMsg::RespondFlagNormal(flag, new) => {
                state.try_ms_write(&ws_encode_proxy("normal_flag", format!("{flag} {new}")));
            }
            NetMsg::RespondFlagSlow(ent, new) => {
                state.try_ms_write(&ws_encode_proxy("slow_flag", format!("{ent} {new}")));
            }
            NetMsg::RespondFlagMoon(x, y, b) => {
                state.try_ms_write(&ws_encode_proxy(
                    "moon_flag",
                    format!("{x} {y}{}", if b { "1" } else { "0" }),
                ));
            }
            NetMsg::RespondFlagStevari(x, y, id) => {
                state.try_ms_write(&ws_encode_proxy(
                    "stevari_flag",
                    format!("{x} {y}{}", if id == self.peer.my_id() { "1" } else { "0" }),
                ));
            }
        }
    }
    fn handle_remote_msg(
        &self,
        state: &mut NetInnerState,
        src: OmniPeerId,
        message: RemoteMessage,
    ) {
        state.try_ms_write(&NoitaInbound::RemoteMessage {
            source: src.into(),
            message,
        });
    }

    fn do_message_request(&self, request: impl Into<MessageRequest<NetMsg>>) {
        let request: MessageRequest<NetMsg> = request.into();
        match request.dst {
            Destination::Peers(peers) => {
                for peer in peers {
                    self.send(peer, &request.msg, request.reliability);
                }
            }
            Destination::Peer(peer) => {
                self.send(peer, &request.msg, request.reliability);
            }
            Destination::Host => {
                self.send(self.peer.host_id(), &request.msg, request.reliability);
            }
            Destination::Broadcast => self.broadcast(&request.msg, request.reliability),
        }
    }

    fn rule_advertisement(&self)->RuleAdvertisement {
        let session=self.session_rules.lock().unwrap();
        RuleAdvertisement {settings:self.settings.lock().unwrap().clone(),run_id:session.run_id,rules_id:session.rules_id,locked:session.locked,ready:session.game_received && session.failure.is_none(),legacy_unverified:session.legacy_unverified}
    }
    /// Failure of an actual agreement, not an ordinary client waiting for its
    /// host. Revoke save permission before any normal shutdown can drop state.
    fn fail_agreement(&self,stage:&str,message:String,state:Option<&mut NetInnerState>) {
        self.init_settings.save_state.mark_game_not_started();
        let (message,first)={let mut session=self.session_rules.lock().unwrap_or_else(|poison|poison.into_inner());let first=session.failure.is_none();let message=session.failure.get_or_insert(message).clone();session.game_received=false;(message,first)};
        self.accept_local.store(false,Ordering::SeqCst);
        *self.error.lock().unwrap_or_else(|poison|poison.into_inner())=Some(io::Error::other(message.clone()));
        if first {error!("{message}");crate::startup_diagnostics::failure(stage,&message);}else{crate::startup_diagnostics::event("world-agreement-failure-repeat",&format!("stage={stage} first_cause={message}"));}
        if let Some(state)=state {state.initializing_local=true;state.try_ms_write(&ws_encode_proxy("coopplus_world_ack",0));state.initializing_local=false;state.flush_local();state.local_world_ready=false;state.disconnect_local();state.gate_error=None;}
        if self.is_host() && crate::frozen_runtime::ID.is_some() && !self.session_rules.is_poisoned(){self.broadcast(&NetMsg::SessionRules(self.rule_advertisement()),Reliability::Reliable);}
    }
    fn consume_gate_failure(&self,state:&mut NetInnerState) {
        if let Some(error)=state.gate_error.take(){self.fail_agreement("world-gate-failed",error,Some(state));}
    }
    fn report_preparation_failure(&self,error:&io::Error) {
        if error.kind()!=io::ErrorKind::WouldBlock {self.fail_agreement("world-preparation-failed",format!("Cannot prepare game session: {error}"),None);}
    }
    pub(crate) fn apply_host_preferences(&self,draft:&GameSettings)->bool {
        if !self.is_host(){return false;}
        let mut session=self.session_rules.lock().unwrap();let current=self.settings.lock().unwrap().clone();
        let mut pending=draft.clone();pending.world_num=current.world_num;pending.progress=current.progress.clone();
        if !pending.use_constant_seed {pending.seed=current.seed;}
        *self.pending_settings.lock().unwrap()=pending.clone();
        let applied=!session.locked;
        if applied {*self.settings.lock().unwrap()=pending.clone();session.refresh_draft();}
        self.dirty.store(!applied && current!=pending,Ordering::Relaxed);
        let advertisement=RuleAdvertisement {settings:if applied{pending}else{current},run_id:session.run_id,rules_id:session.rules_id,locked:session.locked,ready:session.game_received && session.failure.is_none(),legacy_unverified:session.legacy_unverified};
        crate::startup_diagnostics::event("host-settings-transition",&format!("current_mode={} next_run_mode={} applied_now={} locked={} run_id={:016x} rules_id={:016x}",advertisement.settings.game_mode.unwrap_or(DefaultSettings::default().game_mode),draft.game_mode.unwrap_or(DefaultSettings::default().game_mode),applied,session.locked,session.run_id,session.rules_id));
        drop(session);if applied && crate::frozen_runtime::ID.is_some(){self.broadcast(&NetMsg::SessionRules(advertisement),Reliability::Reliable);}
        applied
    }
    pub(crate) fn prepare_game_launch(&self)->io::Result<()> {
        let result=self.prepare_game_launch_inner();if let Err(error)=&result{self.report_preparation_failure(error);}result
    }
    fn prepare_game_launch_inner(&self)->io::Result<()> {
        if crate::frozen_runtime::ID.is_none(){self.session_rules.lock().unwrap().locked=true;return Ok(());}
        let mut session=self.session_rules.lock().unwrap();
        if let Some(error)=&session.failure{return Err(io::Error::other(error.clone()));}
        if !self.is_host() && (!session.locked || !self.accept_local.load(Ordering::SeqCst)){return Err(io::Error::new(io::ErrorKind::WouldBlock,"Wait for the host's actual Noita world to agree with the selected run settings."));}
        if !session.launch_prepared {
            let profile=self.init_settings.paths.noita_save.as_ref().ok_or_else(||io::Error::other("No explicit Noita profile for world identity"))?;
            let stopped=run_settings::stopped_world_session(profile).map_err(|error|io::Error::new(io::ErrorKind::InvalidData,format!("Cannot validate stopped Noita world identity: {error}")))?;
            session.prepare_stopped_identity(stopped,self.is_host())?;
        }
        session.locked=true;let advertisement=RuleAdvertisement {settings:self.settings.lock().unwrap().clone(),run_id:session.run_id,rules_id:session.rules_id,locked:true,ready:session.game_received && session.failure.is_none(),legacy_unverified:session.legacy_unverified};
        crate::startup_diagnostics::event("host-settings-locked",&format!("mode={} intent={} stopped_local_session={} run_id={:016x} rules_id={:016x} legacy_unverified={}",advertisement.settings.game_mode.unwrap_or(DefaultSettings::default().game_mode),session.intent.as_str(),session.expected_session.as_deref().unwrap_or("absent"),session.run_id,session.rules_id,session.legacy_unverified));
        drop(session);if self.is_host(){self.broadcast(&NetMsg::SessionRules(advertisement),Reliability::Reliable);}Ok(())
    }
    fn accept_world_receipt(&self,text:&str,state:&mut NetInnerState,sendm:&Sender<FxHashMap<u16,u32>>)->io::Result<()> {
        let receipt=WorldReceipt::parse(text)?;let mut session=self.session_rules.lock().unwrap();let settings=self.settings.lock().unwrap().clone();
        let already_received=session.game_received;
        session.accept_receipt(&receipt,&settings,self.is_host())?;
        if already_received {crate::startup_diagnostics::event("actual-world-receipt-repeat",&format!("kind={} boundary={} mode={} no_notplayer={} local_session={} run_id={:016x} rules_id={:016x} legacy_unverified={}",receipt.kind,receipt.boundary,receipt.mode,receipt.no_notplayer,receipt.session,session.run_id,session.rules_id,session.legacy_unverified));drop(session);state.try_ms_write(&ws_encode_proxy("coopplus_world_ack",1));return Ok(());}
        if self.is_host() {
            if session.intent==RunIntent::New {
                self.init_settings.save_state.reset_checked()?;state.world.reset();state.des.reset();state.flags.clear();
                self.chunk_map.lock().unwrap().clear();self.reset_map.store(true,Ordering::Relaxed);
                // No peer game is permitted before host agreement. Anything
                // queued outward here belongs to the retired pre-agreement
                // generation; preserve local NEW-world input for FIFO replay.
                state.gated_outbound.clear();
            }
            SavedRunSettings {schema:1,settings:settings.clone(),run_id:session.run_id,rules_id:session.rules_id,world_session:receipt.session.clone(),legacy_unverified:session.legacy_unverified}.save(self.init_settings.save_state.directory())?;
        }
        self.init_settings.save_state.mark_game_started();
        info!("Co-op+ actual world receipt accepted: kind={} mode={} no_notplayer={} session={} legacy_unverified={}",receipt.kind,receipt.mode,receipt.no_notplayer,receipt.session,session.legacy_unverified);
        crate::startup_diagnostics::event("actual-world-receipt-accepted",&format!("kind={} boundary={} mode={} no_notplayer={} local_session={} run_id={:016x} rules_id={:016x} legacy_unverified={}",receipt.kind,receipt.boundary,receipt.mode,receipt.no_notplayer,receipt.session,session.run_id,session.rules_id,session.legacy_unverified));
        if self.is_host() && !session.legacy_unverified {session.intent=RunIntent::Continue;}
        session.launch_prepared=false;
        drop(session);state.local_world_ready=true;state.gate_started=None;state.gate_bytes=0;
        if self.is_host(){self.broadcast(&NetMsg::SessionRules(self.rule_advertisement()),Reliability::Reliable);}
        state.try_ms_write(&ws_encode_proxy("coopplus_world_ack",1));
        for bytes in std::mem::take(&mut state.gated_outbound) {let message=bitcode::decode::<NoitaInbound>(&bytes).map_err(|e|io::Error::other(e.to_string()))?;state.try_ms_write(&message);}
        for bytes in std::mem::take(&mut state.gated_inbound) {let message=bitcode::decode::<NoitaOutbound>(&bytes).map_err(|e|io::Error::other(e.to_string()))?;self.handle_mod_message_2(message,state,sendm);}
        Ok(())
    }
    fn on_ms_connection(self: &Arc<NetManager>, state: &mut NetInnerState)->io::Result<()> {
        self.prepare_game_launch()?;
        if crate::frozen_runtime::ID.is_none(){self.init_settings.save_state.mark_game_started();}
        state.initializing_local=true;
        state.gate_started=Some(Instant::now());state.gate_error=None;
        info!("New stream connected");

        let settings = self.settings.lock().unwrap().clone();
        if crate::frozen_runtime::ID.is_some() {
            let session=self.session_rules.lock().unwrap();
            state.try_ws_write_option("coopplus_run_id",format!("{:016x}",session.run_id).as_str());
            state.try_ws_write_option("coopplus_rules_id",format!("{:016x}",session.rules_id).as_str());
            state.try_ws_write_option("coopplus_expected_session",session.expected_session.as_deref().map(run_settings::hex_text).as_deref().unwrap_or("none"));
            state.try_ws_write_option("coopplus_run_intent",session.intent.as_str());
            state.try_ws_write_option("coopplus_expected_mode",settings.game_mode.unwrap_or(DefaultSettings::default().game_mode).to_string().as_str());
        }
        let def = DefaultSettings::default();
        state.try_ms_write(&ws_encode_proxy("seed", settings.seed));
        let my_id = self.peer.my_id();
        state.try_ms_write(&ws_encode_proxy("peer_id", format!("{:016x}", my_id.0)));
        state.try_ms_write(&ws_encode_proxy(
            "host_id",
            format!("{:016x}", self.peer.host_id().0),
        ));
        info!("Chosen nickname: {}", self.init_settings.my_nickname);
        state.try_ws_write_option("name", self.init_settings.my_nickname.as_str());
        state.try_ws_write_option("world_num", settings.world_num as u32);
        let mode = settings.game_mode.unwrap_or(def.game_mode);
        state.try_ws_write_option(
            "friendly_fire",
            settings.friendly_fire.unwrap_or(def.friendly_fire)
                || mode == GameMode::LocalHealth(LocalHealthMode::PvP),
        );
        state.try_ws_write_option("share_gold", settings.share_gold.unwrap_or(def.share_gold));
        state.try_ws_write_option(
            "same_loadout",
            settings.same_loadout.unwrap_or(def.same_loadout),
        );
        state.try_ws_write_option(
            "home_on_players",
            settings.home_on_players.unwrap_or(def.home_on_players),
        );
        state.try_ws_write_option("debug", settings.debug_mode.unwrap_or(def.debug_mode));
        if self.is_host() {
            self.duplicate.store(
                settings.duplicate.unwrap_or(def.duplicate),
                Ordering::Relaxed,
            );
        }
        state.try_ws_write_option(
            "randomize_perks",
            settings.randomize_perks.unwrap_or(def.randomize_perks),
        );
        state.try_ws_write_option(
            "enemy_hp_scale",
            settings.enemy_hp_mult.unwrap_or(def.enemy_hp_mult),
        );
        state.try_ws_write_option("game_mode", mode);
        if let GameMode::LocalHealth(mode) = mode {
            match mode {
                LocalHealthMode::Normal => {
                    state.try_ws_write_option(
                        "global_hp_loss",
                        settings.global_hp_loss.unwrap_or(def.global_hp_loss),
                    );
                    state.try_ws_write_option(
                        "physics_damage",
                        settings.physics_damage.unwrap_or(def.physics_damage),
                    );
                    state.try_ws_write_option(
                        "no_material_damage",
                        settings
                            .no_material_damage
                            .unwrap_or(def.no_material_damage),
                    );
                    state.try_ws_write_option(
                        "health_lost_on_revive",
                        settings
                            .health_lost_on_revive
                            .unwrap_or(def.health_lost_on_revive),
                    );
                }
                LocalHealthMode::PermaDeath => state.try_ws_write_option("perma_death", true),
                LocalHealthMode::Alternate => {
                    state.try_ws_write_option("no_notplayer", true);
                    state.try_ws_write_option(
                        "global_hp_loss",
                        settings.global_hp_loss.unwrap_or(def.global_hp_loss),
                    );
                    state.try_ws_write_option(
                        "health_lost_on_revive",
                        settings
                            .health_lost_on_revive
                            .unwrap_or(def.health_lost_on_revive),
                    );
                    state.try_ws_write_option(
                        "local_health_alternate_dont_run",
                        settings
                            .local_health_alternate_dont_run
                            .unwrap_or(def.local_health_alternate_dont_run),
                    );
                    state.try_ws_write_option(
                        "revive_on_drop",
                        settings.revive_on_drop.unwrap_or(def.revive_on_drop),
                    );
                }
                LocalHealthMode::PvP => {
                    state.try_ws_write_option("pvp", true);
                    state.try_ws_write_option(
                        "pvp_kill_steal",
                        if settings.share_gold.unwrap_or(def.share_gold) {
                            0
                        } else {
                            settings.pvp_kill_steal.unwrap_or(def.pvp_kill_steal)
                        },
                    );
                    state.try_ws_write_option(
                        "dont_steal",
                        settings.dont_steal.unwrap_or(def.dont_steal),
                    );
                    state.try_ws_write_option(
                        "chest_on_win",
                        settings.chest_on_win.unwrap_or(def.chest_on_win),
                    );
                    let timed = settings.timed.unwrap_or(def.timed);
                    state.try_ws_write_option("timed", timed);
                    if timed {
                        state.try_ws_write_option(
                            "wait_for_time",
                            settings.wait_for_time.unwrap_or(def.wait_for_time),
                        );
                        state.try_ws_write_option(
                            "time_in",
                            settings.time_in_hm.unwrap_or(def.time_in_hm),
                        );
                        state.try_ws_write_option(
                            "time_out",
                            settings.time_out_hm.unwrap_or(def.time_out_hm),
                        );
                    } else {
                        state.try_ws_write_option(
                            "wait_on_players",
                            settings.wait_on_players.unwrap_or(def.wait_on_players),
                        );
                    }
                }
            }
        }
        if GameMode::SharedHealth == mode {
            state.try_ws_write_option(
                "health_per_player",
                settings.health_per_player.unwrap_or(def.health_per_player),
            );
        }
        let lst = settings.clone();
        state.try_ws_write_option(
            "perk_ban_list",
            lst.perk_ban_list.unwrap_or(def.perk_ban_list).as_str(),
        );
        state.try_ws_write_option(
            "spell_ban_list",
            lst.spell_ban_list.unwrap_or(def.spell_ban_list).as_str(),
        );
        state.try_ws_write_option(
            "disabled_globals",
            lst.disabled_globals
                .unwrap_or(def.disabled_globals)
                .as_str(),
        );
        state.try_ws_write_option(
            "disable_kummitus",
            settings.disable_kummitus.unwrap_or(def.disable_kummitus),
        );
        state.try_ws_write_option(
            "host_sampo",
            settings.give_host_sampo.unwrap_or(def.give_host_sampo),
        );
        state.world.nice_terraforming = settings.nice_terraforming.unwrap_or(def.nice_terraforming);
        let rgb = self
            .new_desc
            .lock()
            .unwrap()
            .unwrap_or(self.init_settings.player_png_desc)
            .colors
            .player_main;
        state.try_ws_write_option(
            "mina_color",
            rgb[0] as u32 + ((rgb[1] as u32) << 8) + ((rgb[2] as u32) << 16),
        );

        let rgb = self
            .new_desc
            .lock()
            .unwrap()
            .unwrap_or(self.init_settings.player_png_desc)
            .colors
            .player_alt;
        state.try_ws_write_option(
            "mina_color_alt",
            rgb[0] as u32 + ((rgb[1] as u32) << 8) + ((rgb[2] as u32) << 16),
        );

        let progress = settings.progress.join(",");
        state.try_ws_write_option("progress", progress.as_str());

        state.try_ms_write(&NoitaInbound::Ready {
            my_peer_id: self.peer.my_id().into(),
        });
        state.initializing_local=false;
        info!("Settings sent; actual world receipt {}",if state.local_world_ready{"not required by this runtime"}else{"pending"});
        Ok(())
    }

    fn handle_mod_message_2(
        &self,
        msg: NoitaOutbound,
        state: &mut NetInnerState,
        sendm: &Sender<FxHashMap<u16, u32>>,
    ) {
        if !state.local_world_ready {
            let receipt=matches!(&msg,NoitaOutbound::Raw(raw) if raw.first().is_some_and(|tag|tag&3==1) && raw.get(1..).is_some_and(|body|body.starts_with(b"coopplus_world_receipt ")));
            if !receipt {state.hold_world_message(bitcode::encode(&msg),true);return;}
        }
        match msg {
            NoitaOutbound::Raw(raw_msg) => {
                match raw_msg[0] & 0b11 {
                    // Message to proxy
                    1 => {
                        self.handle_message_to_proxy(&raw_msg[1..], state, sendm);
                    }
                    // Broadcast
                    2 => {
                        let msg_to_send = NetMsg::ModRaw {
                            data: raw_msg[1..].to_owned(),
                        };
                        let reliable = raw_msg[0] & 4 > 0;
                        self.broadcast(
                            &msg_to_send,
                            if reliable {
                                Reliability::Reliable
                            } else {
                                Reliability::Unreliable
                            },
                        );
                    }
                    // Binary message to proxy
                    3 => self.handle_bin_message_to_proxy(&raw_msg[1..], state),
                    0 => {
                        let flags = String::from_utf8_lossy(&raw_msg[1..]).into();
                        let msg = NetMsg::Flags(flags);
                        self.send(self.peer.host_id(), &msg, Reliability::Reliable)
                    }
                    msg_variant => {
                        error!("Unknown msg variant from mod: {}", msg_variant)
                    }
                }
            }
            NoitaOutbound::DesToProxy(des_to_proxy) => {
                if self.is_host() {
                    state.des.handle_noita_msg(self.peer.my_id(), des_to_proxy)
                } else {
                    self.send(
                        self.peer.host_id(),
                        &NetMsg::ForwardDesToProxy(des_to_proxy),
                        Reliability::Reliable,
                    );
                }
            }
            NoitaOutbound::RemoteMessage {
                reliable,
                destination,
                message,
            } => {
                let destination = destination.convert::<OmniPeerId>();
                let reliability = Reliability::from_reliability_bool(reliable);
                match destination {
                    Destination::Peers(peers) => {
                        if !peers.is_empty() {
                            if peers.len() == 1 {
                                self.send(peers[0], &NetMsg::RemoteMsg(message), reliability)
                            } else {
                                for peer in peers {
                                    self.send(
                                        peer,
                                        &NetMsg::RemoteMsg(message.clone()),
                                        reliability,
                                    )
                                }
                            }
                        }
                    }
                    Destination::Peer(peer) => {
                        self.send(peer, &NetMsg::RemoteMsg(message), reliability)
                    }
                    Destination::Host => self.send(
                        self.peer.host_id(),
                        &NetMsg::RemoteMsg(message),
                        reliability,
                    ),
                    Destination::Broadcast => {
                        self.broadcast(&NetMsg::RemoteMsg(message), reliability)
                    }
                }
            }
        }
    }

    pub fn start(self: Arc<NetManager>, player_path: PathBuf) -> JoinHandle<()> {
        info!("Starting netmanager");
        thread::spawn(move || {
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(||self.clone().start_inner(player_path, None)));
            let error=match result {Ok(Ok(()))=>None,Ok(Err(error))=>Some(error),Err(payload)=>{
                let text=payload.downcast_ref::<String>().map(String::as_str).or_else(||payload.downcast_ref::<&str>().copied()).unwrap_or("unknown panic payload");
                Some(io::Error::other(format!("Network session stopped after a failure: {text}. Saved files were not reset; preserve the error and logs.")))
            }};
            if let Some(error)=error {
                error!("Error in netmanager: {error}");
                crate::startup_diagnostics::failure("network-session-failed",&error.to_string());
                *self.error.lock().unwrap_or_else(|poison|poison.into_inner())=Some(error);
            }
            self.stopped.store(true, Ordering::Relaxed);
        })
    }

    fn resend_game_settings(&self) {
        let settings = self.settings.lock().unwrap().clone();
        self.broadcast(
            &NetMsg::StartGame {
                settings,
                init: false,
            },
            Reliability::Reliable,
        );
        if crate::frozen_runtime::ID.is_some(){self.broadcast(&NetMsg::SessionRules(self.rule_advertisement()),Reliability::Reliable);}
    }

    fn is_host(&self) -> bool {
        self.peer.is_host()
    }

    pub(crate) fn handle_message_to_proxy(
        &self,
        msg: &[u8],
        state: &mut NetInnerState,
        sendm: &Sender<FxHashMap<u16, u32>>,
    ) {
        let msg = String::from_utf8_lossy(msg);
        let mut msg = msg.split_ascii_whitespace();
        let key = msg.next();
        match key {
            Some("coopplus_world_receipt")=>{
                let text=msg.collect::<Vec<_>>().join(" ");
                if let Err(error)=self.accept_world_receipt(&text,state,sendm) {
                    self.fail_agreement("world-rules-refused",format!("World/rules agreement refused: {error}"),Some(state));
                }
            },
            Some("game_over") => {
                if self.is_host() {
                    info!("Game over, resending game settings");
                    self.end_run(state)
                }
            }
            Some("cam_pos") => {
                let x: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                if let (Some(x), Some(y)) = (x, y) {
                    self.camera_pos.0.store(x, Ordering::Relaxed);
                    self.camera_pos.1.store(y, Ordering::Relaxed);
                }
                let x: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let b: bool = msg
                    .next()
                    .map(|s| s.parse().ok() == Some(1))
                    .unwrap_or(false);
                let d: bool = msg
                    .next()
                    .map(|s| s.parse().ok() == Some(1))
                    .unwrap_or(false);
                if let (Some(x), Some(y)) = (x, y) {
                    self.player_pos.0.store(x, Ordering::Relaxed);
                    self.player_pos.1.store(y, Ordering::Relaxed);
                    self.broadcast(&NetMsg::PlayerPosition(x, y, b, d), Reliability::Reliable);
                    self.send(
                        self.peer.my_id(),
                        &NetMsg::PlayerPosition(x, y, b, d),
                        Reliability::Reliable,
                    );
                }
                let x: Option<u8> = msg.next().and_then(|s| s.parse().ok());
                self.push_to_talk.store(x == Some(1), Ordering::Relaxed);
                let dead = msg.next().and_then(|s| s.parse().ok()) == Some(1);
                self.is_dead.store(dead, Ordering::Relaxed);
                let polied = msg.next().and_then(|s| s.parse().ok()) == Some(1);
                self.is_polied.store(polied, Ordering::Relaxed);
                let cess = msg.next().and_then(|s| s.parse().ok()) == Some(1);
                self.is_cess.store(cess, Ordering::Relaxed);
            }
            Some("reset_world") => {
                state.world.reset();
                state.des.reset();
                state.flags.clear();
                self.reset_map.store(true, Ordering::Relaxed);
            }
            Some("material_list") => {
                state.world.materials.clear();
                let mut colors = FxHashMap::default();
                while let (
                    Some(i),
                    Some(d),
                    Some(h),
                    Some(cell_type),
                    Some(liquid_sand),
                    Some(liquid_static),
                    Some(wang_color),
                ) = (
                    msg.next().and_then(|s| s.parse().ok()),
                    msg.next().and_then(|s| s.parse().ok()),
                    msg.next().and_then(|s| s.parse().ok()),
                    msg.next(),
                    msg.next().map(|s| s == "1"),
                    msg.next().map(|s| s == "1"),
                    msg.next()
                        .and_then(|s| u32::from_str_radix(&s.to_lowercase(), 16).ok()),
                ) {
                    state.world.materials.insert(
                        i,
                        (
                            d,
                            h,
                            CellType::new(cell_type, liquid_static, liquid_sand),
                            wang_color,
                        ),
                    );
                    colors.insert(i, wang_color);
                }
                if self.is_host() {
                    *self.colors.lock().unwrap() = colors.clone();
                    self.broadcast(&NetMsg::MatData(colors.clone()), Reliability::Reliable);
                    let _ = sendm.send(colors);
                }
                let c = msg.count();
                if c != 0 {
                    error!("bad materials data {}", c);
                }
            }
            Some("cut_through_world") => {
                let x: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y_min: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y_max: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let radius: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let (Some(x), Some(y_min), Some(y_max), Some(radius)) = (x, y_min, y_max, radius)
                else {
                    error!("Missing arguments in cut_through_world message");
                    return;
                };

                state.world.cut_through_world(x, y_min, y_max, radius);
            }
            Some("cut_through_world_line") => {
                let x: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let lx: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let ly: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let (Some(x), Some(y), Some(lx), Some(ly)) = (x, y, lx, ly) else {
                    error!("Missing arguments in cut_through_world_line message");
                    return;
                };
                let r: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let chance: Option<u64> = msg.next().and_then(|s| s.parse().ok());
                state.world.cut_through_world_line(
                    x,
                    y,
                    lx,
                    ly,
                    r.unwrap_or(12),
                    chance.unwrap_or(100).min(100) as u8,
                );
            }
            Some("cut_through_world_circle") => {
                let x: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let r: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let mat: Option<u16> = msg.next().and_then(|s| s.parse().ok());
                let chance: u64 = msg
                    .next()
                    .and_then(|s| s.parse::<u64>().ok())
                    .unwrap_or(100);
                let (Some(x), Some(y), Some(r)) = (x, y, r) else {
                    error!("Missing arguments in cut_through_world_circle message");
                    return;
                };
                state
                    .world
                    .cut_through_world_circle(x, y, r, mat, chance.min(100) as u8);
            }
            Some("cut_through_world_explosion") => {
                let x: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let y: Option<i32> = msg.next().and_then(|s| s.parse().ok());
                let r: Option<u64> = msg.next().and_then(|s| s.parse().ok());
                let d: Option<u32> = msg.next().and_then(|s| s.parse().ok());
                let ray: Option<u64> = msg.next().and_then(|s| s.parse().ok());
                let hole: Option<bool> = msg.next().and_then(|s| s.parse().ok());
                let liquid: Option<bool> = msg.next().and_then(|s| s.parse().ok());
                let mat: Option<u16> = msg.next().and_then(|s| s.parse().ok());
                let prob: Option<u64> = msg.next().and_then(|s| s.parse().ok());
                let (
                    Some(x),
                    Some(y),
                    Some(r),
                    Some(d),
                    Some(ray),
                    Some(hole),
                    Some(liquid),
                    Some(mat),
                    Some(prob),
                ) = (x, y, r, d, ray, hole, liquid, mat, prob)
                else {
                    error!("Missing arguments in cut_through_world_expl message");
                    return;
                };
                state.explosion_data.push(ExplosionData::new(
                    x,
                    y,
                    r,
                    d,
                    ray,
                    hole,
                    liquid,
                    mat,
                    prob.min(100) as u8,
                ));
            }
            Some("flush_exp") => {
                state
                    .world
                    .cut_through_world_explosion(std::mem::take(&mut state.explosion_data));
            }
            Some("flush") => self.peer.flush(),
            key => {
                error!("Unknown msg from mod: {:?}", key)
            }
        }
    }

    fn handle_bin_message_to_proxy(&self, msg: &[u8], state: &mut NetInnerState) {
        if msg.is_empty() { return; }
        let key = msg[0];
        let data = &msg[1..];
        let result: eyre::Result<()> = (|| { match key {
            // world frame
            0 => {
                state.world.add_terrain_update(data)?;
            }
            // world end
            1 => {
                eyre::ensure!(!data.is_empty(), "Empty terrain END");
                let pos = data[1..]
                    .split(|b| *b == b':')
                    .map(|s| String::from_utf8_lossy(s).parse::<i32>())
                    .collect::<Result<Vec<_>,_>>()?;
                state.world.add_terrain_end(data[0], &pos)?;
            }
            2 => {
                eyre::ensure!(data.len() == 12, "Invalid terrain ACK");
                if u32::from_le_bytes(data[8..12].try_into()?) == state.world.terrain_session() {
                    state.world.terrain_ack(u64::from_le_bytes(data[..8].try_into()?));
                }
            }
            3 => {
                eyre::ensure!(data.len() == 9 && data[0] == shared::terrain_protocol::VERSION, "Incompatible Lua terrain protocol");
                state.world.terrain_hello(u32::from_le_bytes(data[1..5].try_into()?), i32::from_le_bytes(data[5..9].try_into()?))?;
            }
            4 => { eyre::bail!("Game terrain application failed: {}", String::from_utf8_lossy(data)); }
            5 | 6 => {
                eyre::ensure!(data.len() >= 12, "Invalid terrain progress receipt");
                if u32::from_le_bytes(data[8..12].try_into()?) == state.world.terrain_session() {
                    let token = u64::from_le_bytes(data[..8].try_into()?);
                    if key == 5 { state.world.terrain_progress(token, &data[12..])?; }
                    else { state.world.terrain_pause_ack(token, &data[12..])?; }
                }
            }
            key => {
                error!("Unknown bin msg from mod: {:?}", key)
            }
        }; Ok(()) })();
        if let Err(err) = result {
            error!("Terrain protocol stopped: {err}");
            state.ms = None;
            state.had_a_disconnect = true;
        }
    }

    fn end_run(&self, state: &mut NetInnerState) {
        let mut session=self.session_rules.lock().unwrap();
        self.init_settings.save_state.mark_game_not_started();
        self.init_settings.save_state.reset();
        {
            let mut settings = self.pending_settings.lock().unwrap();
            if !settings.use_constant_seed {
                settings.seed = rand::random();
            }
            info!("New seed: {}", settings.seed);
            settings.progress =
                crate::mod_manager::get_progress(self.init_settings.paths.noita_save.as_deref())
                    .unwrap_or_default();
            if settings.world_num == u16::MAX {
                settings.world_num = 0
            } else {
                settings.world_num += 1
            }
            *self.settings.lock().unwrap() = settings.clone();
            state.world.reset();
            state.des.reset();
            state.flags.clear();
            self.reset_map.store(true, Ordering::Relaxed);
            self.dirty.store(false, Ordering::Relaxed);
        }
        *session=SessionRules::default();drop(session);
        state.local_world_ready=crate::frozen_runtime::ID.is_none();state.gated_outbound.clear();state.gated_inbound.clear();
        self.resend_game_settings();
    }
}

#[derive(Clone, Copy)]
pub struct ExplosionData {
    x: i32,
    y: i32,
    r: u64,
    d: u32,
    ray: u64,
    hole: bool,
    liquid: bool,
    mat: Pixel,
    prob: u8,
}
impl ExplosionData {
    #[allow(clippy::too_many_arguments)]
    fn new(
        x: i32,
        y: i32,
        r: u64,
        d: u32,
        ray: u64,
        hole: bool,
        liquid: bool,
        mat: u16,
        prob: u8,
    ) -> ExplosionData {
        ExplosionData {
            x,
            y,
            r,
            d,
            ray,
            hole,
            liquid,
            mat: Pixel {
                flags: PixelFlags::Normal,
                material: mat,
            },
            prob,
        }
    }
}

pub enum CellType {
    Solid,
    Liquid(LiquidType),
    Gas,
    Fire,
    Invalid,
}
impl CellType {
    fn new(s: &str, stat: bool, sand: bool) -> Self {
        match s {
            "solid" => Self::Solid,
            "liquid" if stat => Self::Liquid(LiquidType::Static),
            "liquid" if sand => Self::Liquid(LiquidType::Sand),
            "liquid" => Self::Liquid(LiquidType::Liquid),
            "gas" => Self::Gas,
            "fire" => Self::Fire,
            _ => Self::Invalid,
        }
    }
    fn can_remove(&self, hole: bool, liquid: bool) -> bool {
        match self {
            Self::Liquid(LiquidType::Sand) | Self::Liquid(LiquidType::Static) | Self::Solid
                if hole =>
            {
                true
            }
            Self::Liquid(LiquidType::Liquid) if liquid => true,
            _ => false,
        }
    }
}
pub enum LiquidType {
    Static,
    Liquid,
    Sand,
}

impl Drop for NetManager {
    fn drop(&mut self) {
        if !self.init_settings.save_state.game_started(){info!("No accepted current world to save; preserving the prior checkpoint");return;}
        if self.is_host() {
            let run_info = RunInfo {
                seed: self.settings.lock().unwrap().seed,
            };
            self.init_settings.save_state.save(&run_info);
            info!("Saved run info");
        } else {
            info!("Skip saving run info: not a host");
        }
    }
}

impl SaveStateEntry for FxHashSet<String> {
    const FILENAME: &'static str = "flags";
}

impl Drop for NetInnerState {
    fn drop(&mut self) {
        if self.world.is_host {
            self.world.save_state.save(&self.flags);
            info!("Saved flag info");
        }
    }
}
