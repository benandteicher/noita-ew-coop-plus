//! Local Discord activity, tied exclusively to the launcher's retained game Child.
//!
//! discord-rich-presence 1.1.0 (MIT) supplies the typed activity model. Its
//! synchronous File-based client is deliberately unused: local Discord IPC is
//! transported by Tokio overlapped pipes, with bounded frames and transactions.
//! No authorization, subscription, account token, invite, join secret or HTTP is used.

use std::{collections::VecDeque, io, sync::{Arc,Mutex,Weak,atomic::{AtomicBool,Ordering}}, time::{Duration, Instant}};
use discord_rich_presence::activity::{Activity, ActivityType, Assets, Button, Timestamps};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::{io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt}, sync::watch, time};

pub(crate) const ABOUT_URL: &str = "https://github.com/benandteicher/noita-ew-coop-plus";
#[cfg(coopplus_staging)]
const BRANDING: (&str, &str) = (
    "coopplus_logo_staging", include_str!("../discord-presence-staging.json"),
);
#[cfg(not(coopplus_staging))]
const BRANDING: (&str, &str) = (
    "coopplus_logo", include_str!("../discord-presence.json"),
);
const ARTWORK_IMAGE: &str = BRANDING.0;
const BUNDLED_CONFIG: &str = BRANDING.1;
const MAX_FRAME: usize = 64 * 1024;
const MAX_REPLIES: usize = 8;
const SAMPLE_LIFETIME: Duration = Duration::from_secs(5);
const LIVENESS_POLL: Duration = Duration::from_millis(250);
const ACTIVITY_WINDOW: Duration = Duration::from_secs(20);
const ACTIVITY_ATTEMPTS: usize = 5;

#[derive(Default)]
struct ActivityBudget { attempts: VecDeque<Instant> }
impl ActivityBudget {
    fn available_at(&mut self, now: Instant) -> Instant {
        while self.attempts.front().is_some_and(|at|now.saturating_duration_since(*at)>=ACTIVITY_WINDOW) {
            self.attempts.pop_front();
        }
        if self.attempts.len()<ACTIVITY_ATTEMPTS { now } else { *self.attempts.front().unwrap()+ACTIVITY_WINDOW }
    }
    fn reserve(&mut self, now: Instant) -> bool {
        if self.available_at(now)>now { return false; }
        // Reserve before writing, including null clears and failed/partial IO.
        self.attempts.push_back(now);
        true
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum LocalContext { Menus, Exploring, HolyMountain, SpectatingTeammate, AwaitingRevival }

#[derive(Clone, Debug, Default)]
pub(crate) struct LocalStatus {
    pub received_at: Option<Instant>,
    pub context: Option<LocalContext>,
    pub area: Option<String>,
}

#[derive(Clone)]
struct CachedObservation { observed_at:Instant, count:usize, status:LocalStatus }

/// Presentation primitives only. In particular, the last Arc's destructor has
/// no NetManager, Steam, filesystem, game-state or worker ownership side effects.
#[derive(Default)]
pub(crate) struct ObservationCache {
    snapshot:Mutex<Option<CachedObservation>>,
    available:AtomicBool,
    retired:AtomicBool,
}

impl ObservationCache {
    pub(crate) fn publish(&self, observation:Option<(usize,LocalStatus)>) {
        self.publish_at(observation,Instant::now());
    }

    fn publish_at(&self, observation:Option<(usize,LocalStatus)>, now:Instant) {
        if self.retired.load(Ordering::Acquire) { return; }
        self.available.store(false,Ordering::Release);
        if let Ok(mut slot)=self.snapshot.try_lock() {
            *slot=observation.map(|(count,status)|CachedObservation{observed_at:now,count,status});
            self.available.store(slot.is_some(),Ordering::Release);
        }
    }

    pub(crate) fn retire(&self) {
        self.retired.store(true,Ordering::Release);
        self.available.store(false,Ordering::Release);
    }

    fn sample(&self, now:Instant) -> Option<(usize,LocalStatus)> {
        if self.retired.load(Ordering::Acquire) || !self.available.load(Ordering::Acquire) { return None; }
        let observation=self.snapshot.try_lock().ok()?.as_ref()?.clone();
        if self.retired.load(Ordering::Acquire) || !self.available.load(Ordering::Acquire)
            || observation.observed_at>now || now.duration_since(observation.observed_at)>SAMPLE_LIFETIME { return None; }
        Some((observation.count,observation.status))
    }
}

/// A snapshot can only originate from NoitaLauncher's retained, still-running Child.
/// A PID by itself is never used to discover, adopt or rediscover a process.
#[derive(Clone)]
pub(crate) struct OwnedGame {
    pub pid: u32,
    pub born: Instant,
    pub unix_start: i64,
    witness: Arc<ProcessWitness>,
}

enum ProcessWitness {
    #[cfg(windows)]
    Handle(std::os::windows::io::OwnedHandle),
    #[cfg(test)]
    Synthetic(std::sync::atomic::AtomicBool),
}

impl std::fmt::Debug for OwnedGame {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.debug_struct("OwnedGame").field("pid", &self.pid).field("born", &self.born).field("unix_start", &self.unix_start).finish_non_exhaustive()
    }
}
impl PartialEq for OwnedGame {
    fn eq(&self, other: &Self) -> bool { self.pid == other.pid && self.born == other.born }
}
impl Eq for OwnedGame {}

#[cfg(windows)]
mod process_handle {
    use std::{ffi::c_void, os::windows::io::{AsHandle, AsRawHandle, FromRawHandle, OwnedHandle}, process::Child};
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut c_void;
        fn DuplicateHandle(source_process: *mut c_void, source: *mut c_void, target_process: *mut c_void,
            target: *mut *mut c_void, desired_access: u32, inherit: i32, options: u32) -> i32;
        fn WaitForSingleObject(handle: *mut c_void, milliseconds: u32) -> u32;
    }

    pub(super) fn duplicate(child: &Child) -> std::io::Result<OwnedHandle> {
        let borrowed = child.as_handle();
        let mut duplicate = std::ptr::null_mut();
        // Only SYNCHRONIZE, never TERMINATE/VM/QUERY rights. The source is the
        // exact retained Child handle, not OpenProcess or a discovered PID.
        let success = unsafe {
            let current = GetCurrentProcess();
            DuplicateHandle(current, borrowed.as_raw_handle(), current, &mut duplicate, 0x0010_0000, 0, 0)
        };
        if success == 0 { return Err(std::io::Error::last_os_error()); }
        // This unique non-inheritable duplicate is closed automatically; its
        // type exposes no process mutation and Drop never terminates a process.
        Ok(unsafe { OwnedHandle::from_raw_handle(duplicate) })
    }

    pub(super) fn alive(handle: &OwnedHandle) -> bool {
        // WAIT_TIMEOUT is the only live result. Signaled and WAIT_FAILED refuse.
        unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) == 0x0000_0102 }
    }
}

impl OwnedGame {
    pub(crate) fn from_child(child: &std::process::Child) -> io::Result<Self> {
        #[cfg(windows)]
        {
            let handle = process_handle::duplicate(child)?;
            Ok(Self { pid:child.id(), born:Instant::now(),
                unix_start:std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|at|at.as_secs().min(i64::MAX as u64) as i64).unwrap_or(0),
                witness:Arc::new(ProcessWitness::Handle(handle)) })
        }
        #[cfg(not(windows))]
        { let _ = child; Err(io::Error::new(io::ErrorKind::Unsupported,"Owned game liveness requires Windows")) }
    }

    pub(crate) fn is_alive(&self) -> bool {
        match self.witness.as_ref() {
            #[cfg(windows)]
            ProcessWitness::Handle(handle) => process_handle::alive(handle),
            #[cfg(test)]
            ProcessWitness::Synthetic(alive) => alive.load(std::sync::atomic::Ordering::Acquire),
            #[cfg(all(not(windows),not(test)))]
            _ => false,
        }
    }

    #[cfg(test)]
    pub(crate) fn synthetic(pid: u32, born: Instant, unix_start: i64) -> Self {
        Self { pid,born,unix_start,witness:Arc::new(ProcessWitness::Synthetic(std::sync::atomic::AtomicBool::new(true))) }
    }

    #[cfg(test)]
    fn end_synthetic(&self) {
        if let ProcessWitness::Synthetic(alive) = self.witness.as_ref() {
            alive.store(false,std::sync::atomic::Ordering::Release);
        }
    }
}

#[derive(Clone)]
enum LiveSource {
    Cache(Weak<ObservationCache>),
    #[cfg(test)]
    Synthetic(Weak<std::sync::Mutex<(usize,LocalStatus)>>),
}

impl std::fmt::Debug for LiveSource {
    fn fmt(&self, out: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        out.write_str("WeakReadOnlySessionSource")
    }
}
impl PartialEq for LiveSource {
    fn eq(&self, other: &Self) -> bool {
        match (self,other) {
            (Self::Cache(left),Self::Cache(right)) => Weak::ptr_eq(left,right),
            #[cfg(test)]
            (Self::Synthetic(left),Self::Synthetic(right)) => Weak::ptr_eq(left,right),
            #[cfg(test)]
            _ => false,
        }
    }
}
impl Eq for LiveSource {}

impl LiveSource {
    fn sample(&self, now:Instant) -> Option<(usize,LocalStatus)> {
        match self {
            Self::Cache(weak) => {
                // This can only upgrade/drop presentation primitives. Even if
                // last, it cannot run NetManager/Steam/save-state destructors.
                weak.upgrade()?.sample(now)
            }
            #[cfg(test)]
            Self::Synthetic(weak) => {
                let source = weak.upgrade()?;
                let sample = source.try_lock().ok()?.clone();
                Some(sample)
            }
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct Snapshot {
    game: OwnedGame,
    count: Option<usize>,
    context: Option<LocalContext>,
    area: Option<String>,
    context_until: Instant,
    sampled_at: Instant,
    show_area: bool,
    source: Option<LiveSource>,
}

impl Snapshot {
    pub(crate) fn new(game: OwnedGame, count: usize, status: &LocalStatus, show_area: bool, now: Instant) -> Self {
        let (context, context_until) = match status.received_at {
            Some(at) if at >= game.born && at <= now && now.duration_since(at) <= SAMPLE_LIFETIME =>
                (status.context, at + SAMPLE_LIFETIME),
            _ => (None, game.born),
        };
        let area = if show_area && matches!(context, Some(LocalContext::Exploring | LocalContext::HolyMountain)) {
            status.area.as_deref().and_then(clean_area)
        } else { None };
        Self { game, count: Some(count.max(1)), context, area, context_until, sampled_at:now, show_area, source:None }
    }

    pub(crate) fn with_netman(mut self, net: &Arc<crate::net::NetManager>) -> Self {
        self.source = Some(LiveSource::Cache(Arc::downgrade(&net.discord_observer)));
        self
    }

    pub(crate) fn from_netman(game: OwnedGame, net: &Arc<crate::net::NetManager>, show_area: bool, now: Instant) -> Self {
        let mut snapshot=Self::new(game,1,&LocalStatus::default(),show_area,now).with_netman(net);
        // The UI performs no peer lookup; the actor observes the read-only source.
        snapshot.count=None;
        snapshot
    }

    fn refresh(&mut self, now: Instant) {
        if let Some(source) = &self.source {
            match source.sample(now) {
                Some((count,status)) => {
                    let fresh=Self::new(self.game.clone(),count,&status,self.show_area,now);
                    self.count=fresh.count;self.context=fresh.context;self.area=fresh.area;
                    self.context_until=fresh.context_until;self.sampled_at=now;
                }
                None => { self.count=None;self.context=None;self.area=None; }
            }
        } else {
            // Detached snapshots are used by formatter/fake-IPC fixtures. A
            // missing live source must never repeat an old count indefinitely.
            if now.saturating_duration_since(self.sampled_at)>SAMPLE_LIFETIME { self.count=None; }
        }
        self.expire(now);
    }

    fn expire(&mut self, now: Instant) {
        if now >= self.context_until { self.context = None; self.area = None; }
    }

    fn same_activity(&self, other: &Self) -> bool {
        self.game == other.game && self.count == other.count && self.context == other.context && self.area == other.area
    }

    fn activity(&self, artwork_image: &str) -> Value {
        let count = match self.count {
            Some(count) => format!("Entangled Worlds · {} connected {}", count, if count == 1 { "player" } else { "players" }),
            None => "Entangled Worlds multiplayer".to_owned(),
        };
        let mut activity = Activity::new().name("Noita: Co-op+").activity_type(ActivityType::Playing)
            .details(count)
            // SET_ACTIVITY RPC timestamps are Unix seconds, per Discord's RPC example.
            .timestamps(Timestamps::new().start(self.game.unix_start))
            .assets(Assets::new().large_image(artwork_image).large_text("Co-op+ · Entangled Worlds"))
            .buttons(vec![Button::new("About Co-op+", ABOUT_URL)]);
        if let Some(context) = self.context {
            let context = match context {
                LocalContext::Menus => "In the menus",
                LocalContext::Exploring => "Exploring",
                LocalContext::HolyMountain => "In the Holy Mountain",
                LocalContext::SpectatingTeammate => "Spectating a teammate",
                LocalContext::AwaitingRevival => "Awaiting revival",
            };
            activity = activity.state(match &self.area { Some(area) => format!("{context} · {area}"), None => context.to_owned() });
        }
        // The typed builder contains no fallible values (only bounded text and integers).
        serde_json::to_value(activity).unwrap_or(Value::Null)
    }
}

fn clean_area(raw: &str) -> Option<String> {
    // No names, coordinates, seed, inventory, lobby or run data are accepted here.
    let text: String = raw.chars().filter(|c| !c.is_control()).take(64).collect();
    let text = text.trim();
    (!text.is_empty()).then(|| text.to_owned())
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Config { application_id: Option<String>, artwork_image: String }

impl Config {
    fn parse(raw: &str) -> Result<Self, &'static str> {
        if raw.len() > 1024 { return Err("Bundled Discord configuration is too large."); }
        let config: Self = serde_json::from_str(raw).map_err(|_| "Bundled Discord configuration is invalid.")?;
        if config.artwork_image != ARTWORK_IMAGE {
            return Err("Bundled Discord artwork must match the approved application asset.");
        }
        if let Some(id) = &config.application_id {
            if !(17..=20).contains(&id.len()) || !id.bytes().all(|b| b.is_ascii_digit()) || id.parse::<u64>().ok().filter(|id| *id != 0).is_none() {
                return Err("Bundled Discord application ID is invalid.");
            }
        }
        Ok(config)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Availability { SetupRequired, InvalidSetup, Unsupported, WaitingForGame, WaitingForDiscord, Connected }

pub(crate) struct Presence {
    desired: watch::Sender<Option<Snapshot>>,
    status: watch::Receiver<Availability>,
}

impl Presence {
    pub(crate) fn new() -> Self {
        let (desired, rx) = watch::channel(None);
        let (status_tx, status) = watch::channel(Availability::WaitingForGame);
        match Config::parse(BUNDLED_CONFIG) {
            Ok(config) if config.application_id.is_none() => { status_tx.send_replace(Availability::SetupRequired); }
            Err(error) => { tracing::warn!("{error}"); status_tx.send_replace(Availability::InvalidSetup); }
            Ok(config) => {
                #[cfg(windows)]
                if let Ok(runtime) = tokio::runtime::Handle::try_current() {
                    runtime.spawn(run(PipeConnector, config, rx, status_tx, Timing::default()));
                } else { status_tx.send_replace(Availability::Unsupported); }
                #[cfg(not(windows))]
                { let _ = (config, rx); status_tx.send_replace(Availability::Unsupported); }
            }
        }
        Self { desired, status }
    }

    /// Latest-only channel: the UI never waits for Discord or queues stale areas.
    pub(crate) fn publish(&self, snapshot: Option<Snapshot>) {
        self.desired.send_if_modified(|current| {
            if *current == snapshot { false } else { *current = snapshot; true }
        });
    }

    pub(crate) fn availability(&self) -> Availability { self.status.borrow().clone() }
}

impl Drop for Presence {
    fn drop(&mut self) {
        // No join or synchronous IPC on UI shutdown. Closing the sender lets the
        // worker clear once within its deadline; dropping the pipe also clears RPC.
        self.desired.send_replace(None);
    }
}

#[derive(Clone, Copy)]
struct Timing { io: Duration, update: Duration, retry: Duration, heartbeat: Duration }
impl Default for Timing {
    fn default() -> Self { Self { io: Duration::from_millis(750), update: Duration::from_secs(5), retry: Duration::from_secs(10), heartbeat: Duration::from_secs(30) } }
}

trait Connector: Send {
    type Stream: AsyncRead + AsyncWrite + Unpin + Send;
    fn connect(&mut self) -> impl std::future::Future<Output = io::Result<Self::Stream>> + Send;
}

#[cfg(windows)]
struct PipeConnector;
#[cfg(windows)]
fn open_local_pipe(path: &str) -> io::Result<tokio::net::windows::named_pipe::NamedPipeClient> {
    tokio::net::windows::named_pipe::ClientOptions::new().open(path)
}
#[cfg(windows)]
impl Connector for PipeConnector {
    type Stream = tokio::net::windows::named_pipe::NamedPipeClient;
    async fn connect(&mut self) -> io::Result<Self::Stream> {
        let mut last = io::Error::new(io::ErrorKind::NotFound, "Discord is not running");
        for index in 0..10 {
            // ClientOptions::open uses overlapped IO and returns immediately for
            // absent/busy local pipes. Never WaitNamedPipe or follow remote paths.
            match open_local_pipe(&format!(r"\\?\pipe\discord-ipc-{index}")) {
                Ok(pipe) => return Ok(pipe), Err(error) => last = error,
            }
        }
        Err(last)
    }
}

fn invalid(message: &'static str) -> io::Error { io::Error::new(io::ErrorKind::InvalidData, message) }

async fn write_frame<S: AsyncWrite + Unpin>(stream: &mut S, opcode: u32, payload: &[u8]) -> io::Result<()> {
    if payload.len() > MAX_FRAME { return Err(invalid("Discord frame exceeds limit")); }
    // Discord's hard-mode wire contract requires header and body in one write.
    let mut frame = Vec::with_capacity(8 + payload.len());
    frame.extend_from_slice(&opcode.to_le_bytes());
    frame.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    frame.extend_from_slice(payload);
    stream.write_all(&frame).await
}

async fn read_frame<S: AsyncRead + Unpin>(stream: &mut S) -> io::Result<(u32, Vec<u8>)> {
    let mut header = [0u8; 8];
    stream.read_exact(&mut header).await?;
    let opcode = u32::from_le_bytes(header[..4].try_into().unwrap());
    let length = u32::from_le_bytes(header[4..].try_into().unwrap()) as usize;
    if length > MAX_FRAME { return Err(invalid("Discord frame exceeds limit")); }
    let mut body = vec![0u8; length];
    stream.read_exact(&mut body).await?;
    Ok((opcode, body))
}

async fn reply<S: AsyncRead + AsyncWrite + Unpin>(stream: &mut S, nonce: Option<&str>) -> io::Result<()> {
    for _ in 0..MAX_REPLIES {
        let (opcode, body) = read_frame(stream).await?;
        match opcode {
            3 => write_frame(stream, 4, &body).await?,
            1 => {
                let value: Value = serde_json::from_slice(&body).map_err(|_| invalid("Invalid Discord reply"))?;
                if value.get("evt").and_then(Value::as_str) == Some("ERROR") { return Err(invalid("Discord rejected activity")); }
                if let Some(nonce) = nonce {
                    if value.get("nonce").and_then(Value::as_str) == Some(nonce) && value.get("cmd").and_then(Value::as_str) == Some("SET_ACTIVITY") { return Ok(()); }
                } else if value.get("evt").and_then(Value::as_str) == Some("READY")
                    && value.get("cmd").and_then(Value::as_str) == Some("DISPATCH")
                    && value.pointer("/data/v").and_then(Value::as_u64) == Some(1) { return Ok(()); }
            }
            2 => return Err(io::Error::new(io::ErrorKind::ConnectionAborted, "Discord closed IPC")),
            _ => return Err(invalid("Unexpected Discord opcode")),
        }
    }
    Err(invalid("Too many Discord replies"))
}

async fn handshake<S: AsyncRead + AsyncWrite + Unpin>(stream: &mut S, config: &Config) -> io::Result<()> {
    let bytes = serde_json::to_vec(&json!({"v":1,"client_id":config.application_id})).map_err(|_| invalid("Invalid handshake"))?;
    write_frame(stream, 0, &bytes).await?;
    reply(stream, None).await
}

async fn set_activity<S: AsyncRead + AsyncWrite + Unpin>(stream: &mut S, pid: u32, activity: Value, sequence: u64) -> io::Result<()> {
    let nonce = format!("coopplus-{}-{sequence}", std::process::id());
    let bytes = serde_json::to_vec(&json!({"cmd":"SET_ACTIVITY","args":{"pid":pid,"activity":activity},"nonce":nonce})).map_err(|_| invalid("Invalid activity"))?;
    write_frame(stream, 1, &bytes).await?;
    reply(stream, Some(&nonce)).await
}

async fn bounded<T>(limit: Duration, future: impl std::future::Future<Output = io::Result<T>>) -> io::Result<T> {
    time::timeout(limit, future).await.map_err(|_| io::Error::new(io::ErrorKind::TimedOut, "Discord IPC timed out"))?
}

async fn bounded_alive<T>(limit: Duration, game: &OwnedGame, future: impl std::future::Future<Output = io::Result<T>>) -> io::Result<T> {
    if !game.is_alive() { return Err(io::Error::new(io::ErrorKind::ConnectionAborted,"Owned game exited")); }
    tokio::pin!(future);
    let deadline = time::sleep(limit);
    tokio::pin!(deadline);
    loop {
        tokio::select! {
            result = &mut future => {
                return if game.is_alive() { result } else { Err(io::Error::new(io::ErrorKind::ConnectionAborted,"Owned game exited")) };
            }
            _ = &mut deadline => return Err(io::Error::new(io::ErrorKind::TimedOut,"Discord IPC timed out")),
            _ = time::sleep(LIVENESS_POLL) => {
                if !game.is_alive() { return Err(io::Error::new(io::ErrorKind::ConnectionAborted,"Owned game exited")); }
            }
        }
    }
}

async fn clear<S: AsyncRead + AsyncWrite + Unpin>(stream: &mut S, sent: &Option<Snapshot>, sequence: &mut u64, timing: Timing, budget: &mut ActivityBudget) -> io::Result<()> {
    if let Some(previous) = sent {
        if !budget.reserve(Instant::now()) { return Err(io::Error::new(io::ErrorKind::WouldBlock,"Discord activity budget exhausted; disconnect instead")); }
        *sequence = sequence.wrapping_add(1);
        bounded(timing.io, set_activity(stream, previous.game.pid, Value::Null, *sequence)).await?;
    }
    Ok(())
}

async fn run<C: Connector>(mut connector: C, config: Config, mut desired: watch::Receiver<Option<Snapshot>>, status: watch::Sender<Availability>, timing: Timing) {
    let mut stream = None;
    let mut sent: Option<Snapshot> = None;
    let mut sequence = 0u64;
    let mut budget = ActivityBudget::default();
    let mut next_send = time::Instant::now();
    let mut next_retry = next_send;
    let mut heartbeat = next_send;
    loop {
        let closed = desired.has_changed().is_err();
        let mut wanted = desired.borrow_and_update().clone();
        if let Some(snapshot) = wanted.as_mut() { snapshot.refresh(Instant::now()); }
        if wanted.as_ref().is_some_and(|snapshot|!snapshot.game.is_alive()) { wanted = None; }
        if wanted.is_none() || closed {
            if let Some(mut pipe) = stream.take() { let _ = clear(&mut pipe, &sent, &mut sequence, timing, &mut budget).await; }
            sent = None;
            status.send_replace(Availability::WaitingForGame);
            let now=Instant::now();let available=budget.available_at(now);
            if available>now { next_retry=next_retry.max(time::Instant::from_std(available)).max(time::Instant::now()+timing.retry); }
            if closed || desired.changed().await.is_err() { return; }
            next_send = time::Instant::now();
            next_retry = next_retry.max(next_send);
            continue;
        }
        if stream.is_none() {
            next_retry=next_retry.max(time::Instant::from_std(budget.available_at(Instant::now())));
            tokio::select! {
                changed = desired.changed() => { if changed.is_err() { return; } continue; }
                _ = time::sleep_until(next_retry) => {}
                _ = time::sleep(LIVENESS_POLL) => continue,
            }
            // Refresh after waiting: do not connect/publish an already disabled snapshot.
            let Some(game) = desired.borrow().as_ref().filter(|snapshot|snapshot.game.is_alive()).map(|snapshot|snapshot.game.clone()) else { continue; };
            status.send_replace(Availability::WaitingForDiscord);
            match bounded_alive(timing.io, &game, async {
                let mut pipe = connector.connect().await?;
                handshake(&mut pipe, &config).await?;
                Ok(pipe)
            }).await {
                Ok(pipe) => { stream = Some(pipe); sent = None; next_send = time::Instant::now(); }
                Err(_) => { next_retry = time::Instant::now() + timing.retry; }
            }
            continue;
        }
        let now = time::Instant::now();
        let same = wanted.as_ref().zip(sent.as_ref()).is_some_and(|(wanted,sent)|wanted.same_activity(sent));
        let urgent = wanted.as_ref().zip(sent.as_ref()).is_some_and(|(wanted,sent)|
            wanted.game != sent.game || (sent.area.is_some() && wanted.area.is_none()) || (sent.context.is_some() && wanted.context.is_none())
            || (sent.count.is_some() && wanted.count.is_none()));
        if (!same && (urgent || now >= next_send)) || (sent.is_some() && now >= heartbeat) {
            let pipe = stream.as_mut().unwrap();
            if sent.as_ref().map(|s|&s.game) != wanted.as_ref().map(|s|&s.game) {
                if clear(pipe, &sent, &mut sequence, timing, &mut budget).await.is_err() {
                    stream = None; sent = None; status.send_replace(Availability::WaitingForDiscord);
                    next_retry=(time::Instant::now()+timing.retry).max(time::Instant::from_std(budget.available_at(Instant::now())));continue;
                }
                sent=None;
            }
            // A channel change during a bounded clear must not publish an old game.
            wanted = desired.borrow_and_update().clone();
            if let Some(snapshot) = wanted.as_mut() { snapshot.refresh(Instant::now()); } else { continue; }
            let snapshot = wanted.as_ref().unwrap();
            if !snapshot.game.is_alive() { continue; }
            if !budget.reserve(Instant::now()) {
                // Never delay urgent privacy/game cleanup for quota. Closing IPC
                // clears this connection's activity without another SET_ACTIVITY.
                stream=None;sent=None;status.send_replace(Availability::WaitingForDiscord);
                next_retry=(time::Instant::now()+timing.retry).max(time::Instant::from_std(budget.available_at(Instant::now())));continue;
            }
            sequence = sequence.wrapping_add(1);
            match bounded_alive(timing.io, &snapshot.game, set_activity(pipe, snapshot.game.pid, snapshot.activity(&config.artwork_image), sequence)).await {
                Ok(()) => { sent = wanted; status.send_replace(Availability::Connected); next_send = time::Instant::now() + timing.update; heartbeat = time::Instant::now() + timing.heartbeat; }
                Err(_) => { stream = None; sent = None; status.send_replace(Availability::WaitingForDiscord); next_retry = time::Instant::now() + timing.retry; }
            }
            continue;
        }
        let mut wake = if !same { next_send.min(heartbeat) } else { heartbeat };
        wake = wake.min(time::Instant::now() + LIVENESS_POLL);
        if let Some(wanted) = &wanted {
            if wanted.context.is_some() { wake = wake.min(time::Instant::from_std(wanted.context_until)); }
        }
        tokio::select! {
            _ = desired.changed() => {}
            _ = time::sleep_until(wake) => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{collections::VecDeque, sync::{Arc, atomic::{AtomicUsize, Ordering}}};
    use tokio::io::{DuplexStream, duplex};

    fn config() -> Config { Config { application_id: Some("123456789012345678".into()), artwork_image:ARTWORK_IMAGE.into() } }
    fn game() -> OwnedGame { OwnedGame::synthetic(123,Instant::now(),1_800_000_000) }
    fn snapshot() -> Snapshot { let g=game();Snapshot::new(g.clone(),2,&LocalStatus::default(),false,g.born) }
    fn fast() -> Timing { Timing { io:Duration::from_millis(30), update:Duration::from_millis(20), retry:Duration::from_millis(20), heartbeat:Duration::from_secs(1) } }

    #[test] fn bundled_configuration_is_valid_without_substitution() { Config::parse(BUNDLED_CONFIG).unwrap(); }
    #[test] fn activity_budget_uses_a_bounded_rolling_window_and_counts_attempts() {
        let start=Instant::now();let mut budget=ActivityBudget::default();
        for second in 0..ACTIVITY_ATTEMPTS {assert!(budget.reserve(start+Duration::from_secs(second as u64)));}
        assert_eq!(budget.attempts.len(),5);assert!(!budget.reserve(start+Duration::from_secs(19)));
        assert_eq!(budget.available_at(start+Duration::from_secs(19)),start+ACTIVITY_WINDOW);
        assert!(budget.reserve(start+ACTIVITY_WINDOW));assert!(!budget.reserve(start+ACTIVITY_WINDOW));
        assert_eq!(budget.attempts.len(),5);assert!(budget.reserve(start+ACTIVITY_WINDOW+Duration::from_secs(1)));
    }
    #[test] fn presentation_cache_has_a_lease_retires_and_uses_only_nonblocking_primitive_reads() {
        let cache=Arc::new(ObservationCache::default());let now=Instant::now();
        assert!(cache.sample(now).is_none());cache.publish_at(Some((2,LocalStatus::default())),now);
        assert_eq!(cache.sample(now).unwrap().0,2);assert!(cache.sample(now+SAMPLE_LIFETIME+Duration::from_millis(1)).is_none());
        let held=cache.snapshot.lock().unwrap();assert!(cache.sample(now).is_none());
        cache.publish(None);drop(held);assert!(cache.sample(now).is_none());
        cache.publish_at(Some((3,LocalStatus::default())),now);assert_eq!(cache.sample(now).unwrap().0,3);
        cache.retire();assert!(cache.sample(now).is_none());cache.publish_at(Some((4,LocalStatus::default())),now);assert!(cache.sample(now).is_none());
        let weak=Arc::downgrade(&cache);drop(cache);assert!(weak.upgrade().is_none());
    }
    #[test] fn rejects_invalid_config_without_substitution() { for id in ["", "0", "123x56789012345678", "99999999999999999999"] { assert!(Config::parse(&format!(r#"{{"application_id":"{id}","artwork_image":"{ARTWORK_IMAGE}"}}"#)).is_err()); } }
    #[test] fn rejects_unapproved_artwork_without_substitution() {
        for image in ["coopplus", "http://example.com/icon.png", "https://example.com/icon.png", "https://raw.githubusercontent.com/benandteicher/noita-ew-coop-plus/1b02c881507b6b9b1323b19417a40d78321e0e88/noita_proxy/assets/icon.png"] {
            assert!(Config::parse(&serde_json::to_string(&json!({"application_id":null,"artwork_image":image})).unwrap()).is_err());
        }
        assert!(Config::parse(&serde_json::to_string(&json!({"application_id":null,"artwork_image":ARTWORK_IMAGE})).unwrap()).is_ok());
    }
    #[test] fn bundled_asset_uses_owner_application_and_logo_key() {
        let config = Config::parse(BUNDLED_CONFIG).unwrap();
        assert_eq!(config.application_id.as_deref(), Some("1570660264435781652"));
        let activity = snapshot().activity(&config.artwork_image);
        if cfg!(coopplus_staging) {
            assert_eq!(activity["assets"]["large_image"], "coopplus_logo_staging");
            assert!(Config::parse(include_str!("../discord-presence.json")).is_err());
        } else {
            assert_eq!(activity["assets"]["large_image"], "coopplus_logo");
            assert!(Config::parse(include_str!("../discord-presence-staging.json")).is_err());
        }
    }
    #[test] fn area_private_by_default_and_no_join_or_stats() {
        let g=game();let s=LocalStatus{received_at:Some(g.born),context:Some(LocalContext::Exploring),area:Some("Secret place".into())};
        let v=Snapshot::new(g.clone(),2,&s,false,g.born).activity(ARTWORK_IMAGE);
        assert_eq!(v["state"],"Exploring");assert_eq!(v["details"],"Entangled Worlds · 2 connected players");
        for key in ["secrets","party","instance","join","stats"] { assert!(v.get(key).is_none()); }
        assert_eq!(v["buttons"].as_array().unwrap().len(),1);assert_eq!(v["buttons"][0]["url"],ABOUT_URL);
        assert_eq!(v["assets"]["large_image"],ARTWORK_IMAGE);
        assert_eq!(v["timestamps"]["start"],g.unix_start);
    }
    #[test] fn enabled_area_is_bounded_and_never_applied_to_death_or_menus() {
        let g=game();let mut s=LocalStatus{received_at:Some(g.born),context:Some(LocalContext::HolyMountain),area:Some("\n Mine\t Ω ".repeat(30))};
        let v=Snapshot::new(g.clone(),1,&s,true,g.born).activity(ARTWORK_IMAGE);assert!(v["state"].as_str().unwrap().chars().count()<90);
        s.context=Some(LocalContext::AwaitingRevival);assert_eq!(Snapshot::new(g.clone(),1,&s,true,g.born).activity(ARTWORK_IMAGE)["state"],"Awaiting revival");
    }
    #[test] fn old_future_stale_and_unknown_status_omit_context() {
        let g=game();
        for at in [g.born-Duration::from_millis(1),g.born+Duration::from_secs(1)] {
            let s=LocalStatus{received_at:Some(at),context:Some(LocalContext::Exploring),area:Some("Old".into())};assert!(Snapshot::new(g.clone(),2,&s,true,g.born).activity(ARTWORK_IMAGE).get("state").is_none());
        }
        let s=LocalStatus{received_at:Some(g.born),context:None,area:Some("Old".into())};assert!(Snapshot::new(g.clone(),2,&s,true,g.born).activity(ARTWORK_IMAGE).get("state").is_none());
        let s=LocalStatus{received_at:Some(g.born),context:Some(LocalContext::Exploring),area:Some("Old".into())};let mut s=Snapshot::new(g.clone(),2,&s,true,g.born);s.expire(g.born+Duration::from_secs(6));assert!(s.activity(ARTWORK_IMAGE).get("state").is_none());
    }
    #[test] fn all_context_labels_are_exact() {
        let g=game();for (context,label) in [(LocalContext::Menus,"In the menus"),(LocalContext::Exploring,"Exploring"),(LocalContext::HolyMountain,"In the Holy Mountain"),(LocalContext::SpectatingTeammate,"Spectating a teammate"),(LocalContext::AwaitingRevival,"Awaiting revival")] {
            let s=LocalStatus{received_at:Some(g.born),context:Some(context),area:None};assert_eq!(Snapshot::new(g.clone(),1,&s,false,g.born).activity(ARTWORK_IMAGE)["state"],label);
        }
    }
    #[test] fn owned_child_without_bridge_has_generic_activity_and_old_settings_keep_safe_defaults() {
        assert!(snapshot().activity(ARTWORK_IMAGE).get("state").is_none());
        let mut state:crate::app::AppSavedState=ron::from_str("()").unwrap();
        assert!(state.discord_activity);assert!(!state.discord_show_area_names);
        state.discord_activity=false;state.discord_show_area_names=true;
        let saved=ron::to_string(&state).unwrap();let loaded:crate::app::AppSavedState=ron::from_str(&saved).unwrap();
        assert!(!loaded.discord_activity);assert!(loaded.discord_show_area_names);
    }
    #[test] fn presentation_preview_samples() {
        let g=game();
        let scenarios = [
            ("menus",LocalContext::Menus,None,false),
            ("exploring_default",LocalContext::Exploring,Some("Mines"),false),
            ("exploring_opt_in_mines",LocalContext::Exploring,Some("Mines"),true),
            ("holy_mountain",LocalContext::HolyMountain,None,false),
            ("spectating_teammate",LocalContext::SpectatingTeammate,None,false),
            ("awaiting_revival",LocalContext::AwaitingRevival,None,false),
        ];
        let samples:Vec<Value> = scenarios.into_iter().map(|(scenario,context,area,show_area)| {
            let status=LocalStatus{received_at:Some(g.born),context:Some(context),area:area.map(str::to_owned)};
            json!({"scenario":scenario,"activity":Snapshot::new(g.clone(),2,&status,show_area,g.born).activity(ARTWORK_IMAGE)})
        }).collect();
        println!("COOPPLUS_DISCORD_MOCK_PREVIEW_JSON={}",serde_json::to_string(&json!({
            "mock":true,"notice":"Simulated preview from the production activity formatter; not a Discord screenshot or live card.",
            "synthetic_connected_players":2,"fake_unix_session_start":g.unix_start,"samples":samples
        })).unwrap());
        assert_eq!(samples.len(),6);
        assert_eq!(samples[1]["activity"]["state"],"Exploring");
        assert_eq!(samples[2]["activity"]["state"],"Exploring · Mines");
    }
    #[tokio::test] async fn oversized_frame_rejected_before_allocation() {
        let (mut client,mut server)=duplex(64);server.write_all(&[1,0,0,0,1,0,1,0]).await.unwrap();assert_eq!(read_frame(&mut client).await.unwrap_err().kind(),io::ErrorKind::InvalidData);
    }
    #[tokio::test] async fn stalled_read_has_deadline() {
        let (mut client,_server)=duplex(64);assert_eq!(bounded(fast().io,reply(&mut client,None)).await.unwrap_err().kind(),io::ErrorKind::TimedOut);
    }
    #[tokio::test] async fn stalled_write_has_deadline() {
        let (mut client,_server)=duplex(1);assert_eq!(bounded(fast().io,write_frame(&mut client,1,b"{}")).await.unwrap_err().kind(),io::ErrorKind::TimedOut);
    }
    #[tokio::test] async fn ping_is_answered_and_nonce_is_required() {
        let (mut client,mut server)=duplex(1024);
        let fake=tokio::spawn(async move { write_frame(&mut server,3,b"ping").await.unwrap();assert_eq!(read_frame(&mut server).await.unwrap(),(4,b"ping".to_vec()));write_frame(&mut server,1,br#"{"cmd":"SET_ACTIVITY","nonce":"wrong"}"#).await.unwrap();write_frame(&mut server,1,br#"{"cmd":"SET_ACTIVITY","nonce":"ok"}"#).await.unwrap(); });
        bounded(Duration::from_secs(1),reply(&mut client,Some("ok"))).await.unwrap();fake.await.unwrap();
    }
    #[cfg(windows)]
    #[tokio::test] async fn private_windows_pipe_handshake_activity_clear_and_disconnect() {
        use tokio::net::windows::named_pipe::ServerOptions;
        static NEXT_PIPE:AtomicUsize=AtomicUsize::new(0);
        // This is our own OS pipe and a simulated server. Never use Discord's
        // real pipe names or connect to a user's Discord instance in this test.
        let name=format!(r"\\.\pipe\coopplus-discord-test-{}-{}",std::process::id(),NEXT_PIPE.fetch_add(1,Ordering::Relaxed));
        assert!(!name.contains("discord-ipc-"));
        let mut server=ServerOptions::new().first_pipe_instance(true).create(&name).unwrap();
        let mut client=open_local_pipe(&name).unwrap();
        let simulated=tokio::spawn(async move {
            server.connect().await.unwrap();
            let (opcode,bytes)=read_frame(&mut server).await.unwrap();assert_eq!(opcode,0);
            let request:Value=serde_json::from_slice(&bytes).unwrap();assert_eq!(request["v"],1);assert_eq!(request["client_id"],"123456789012345678");
            write_frame(&mut server,1,br#"{"evt":"READY","cmd":"DISPATCH","data":{"v":1}}"#).await.unwrap();
            for clearing in [false,true] {
                let (opcode,bytes)=read_frame(&mut server).await.unwrap();assert_eq!(opcode,1);
                let request:Value=serde_json::from_slice(&bytes).unwrap();assert_eq!(request["cmd"],"SET_ACTIVITY");assert_eq!(request["args"]["pid"],123);
                if clearing {assert!(request["args"]["activity"].is_null());}
                else {assert_eq!(request["args"]["activity"]["details"],"Entangled Worlds · 2 connected players");}
                write_frame(&mut server,1,&serde_json::to_vec(&json!({"cmd":"SET_ACTIVITY","nonce":request["nonce"]})).unwrap()).await.unwrap();
            }
            let mut one=[0u8;1];
            match server.read(&mut one).await {
                Ok(0)=>{},Err(error) if matches!(error.kind(),io::ErrorKind::BrokenPipe|io::ErrorKind::ConnectionReset)=>{},
                other=>panic!("Expected our private client to disconnect, got {other:?}"),
            }
        });
        let limit=Duration::from_secs(2);
        bounded(limit,handshake(&mut client,&config())).await.unwrap();
        let sample=snapshot();
        bounded(limit,set_activity(&mut client,sample.game.pid,sample.activity(ARTWORK_IMAGE),1)).await.unwrap();
        bounded(limit,set_activity(&mut client,sample.game.pid,Value::Null,2)).await.unwrap();
        drop(client);
        time::timeout(limit,simulated).await.unwrap().unwrap();
    }
    #[tokio::test] async fn malformed_ready_error_and_reply_flood_are_refused() {
        for payloads in [vec![br#"{"evt":"READY"}"#.to_vec();MAX_REPLIES],vec![br#"{"evt":"ERROR","data":{"message":"rejected"}}"#.to_vec()]] {
            let (mut client,mut server)=duplex(4096);for payload in payloads {write_frame(&mut server,1,&payload).await.unwrap();}
            assert_eq!(bounded(Duration::from_secs(1),reply(&mut client,None)).await.unwrap_err().kind(),io::ErrorKind::InvalidData);
        }
    }

    struct FakeConnector { streams:Arc<std::sync::Mutex<VecDeque<DuplexStream>>>, attempts:Arc<AtomicUsize> }
    impl Connector for FakeConnector {
        type Stream=DuplexStream;
        async fn connect(&mut self)->io::Result<DuplexStream> { self.attempts.fetch_add(1,Ordering::Relaxed);self.streams.lock().unwrap().pop_front().ok_or_else(||io::Error::new(io::ErrorKind::NotFound,"absent")) }
    }
    async fn fake_discord(mut server:DuplexStream, updates:tokio::sync::mpsc::UnboundedSender<Value>) {
        let (op,_)=read_frame(&mut server).await.unwrap();assert_eq!(op,0);write_frame(&mut server,1,br#"{"evt":"READY","cmd":"DISPATCH","data":{"v":1}}"#).await.unwrap();
        while let Ok((op,bytes))=read_frame(&mut server).await {assert_eq!(op,1);let value:Value=serde_json::from_slice(&bytes).unwrap();let nonce=value["nonce"].clone();let _=updates.send(value);let _=write_frame(&mut server,1,&serde_json::to_vec(&json!({"cmd":"SET_ACTIVITY","nonce":nonce})).unwrap()).await;}
    }
    #[tokio::test] async fn actor_coalesces_clears_and_shutdown_does_not_wait_on_discord() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};let (tx,rx)=watch::channel(None);let (status,_)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(connector,config(),rx,status,fast()));
        let first=snapshot();tx.send_replace(Some(first.clone()));let one=time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap();assert_eq!(one["args"]["activity"]["details"],"Entangled Worlds · 2 connected players");
        for count in 3..100 {let mut latest=first.clone();latest.count=Some(count);tx.send_replace(Some(latest));}
        let two=time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap();assert_eq!(two["args"]["activity"]["details"],"Entangled Worlds · 99 connected players");
        tx.send_replace(None);let clear=time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap();assert!(clear["args"]["activity"].is_null());drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();fake.await.unwrap();
    }
    #[tokio::test] async fn actor_absent_then_late_discord_reconnects_without_ui_work() {
        let attempts=Arc::new(AtomicUsize::new(0));let streams=Arc::new(std::sync::Mutex::new(VecDeque::new()));let connector=FakeConnector{streams:streams.clone(),attempts:attempts.clone()};let (tx,rx)=watch::channel(Some(snapshot()));let (status,_)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(connector,config(),rx,status,fast()));
        time::timeout(Duration::from_secs(1), async {
            while attempts.load(Ordering::Relaxed) < 2 { time::sleep(Duration::from_millis(5)).await; }
        }).await.unwrap();
        let (client,server)=duplex(4096);streams.lock().unwrap().push_back(client);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        assert!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"].is_object());drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();fake.await.unwrap();
    }
    #[tokio::test] async fn actor_stalled_handshake_still_stops_within_deadline() {
        let (client,_hung_server)=duplex(4096);let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};let (tx,rx)=watch::channel(Some(snapshot()));let (status,_)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(connector,config(),rx,status,fast()));
        time::sleep(Duration::from_millis(5)).await;drop(tx);time::timeout(Duration::from_millis(250),actor).await.unwrap().unwrap();
    }
    #[tokio::test] async fn actor_expires_context_without_another_ui_update() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};
        let mut first=snapshot();first.context=Some(LocalContext::Exploring);first.area=Some("Mines".into());first.context_until=Instant::now()+Duration::from_millis(100);
        let (tx,rx)=watch::channel(Some(first));let (status,_)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(connector,config(),rx,status,fast()));
        assert_eq!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"]["state"],"Exploring · Mines");
        let expired=time::timeout(Duration::from_millis(400),got.recv()).await.unwrap().unwrap();assert!(expired["args"]["activity"].get("state").is_none());
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();fake.await.unwrap();
    }
    #[tokio::test] async fn removing_area_bypasses_throttle_and_child_replacement_clears_old_identity() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};
        let mut first=snapshot();first.context=Some(LocalContext::Exploring);first.area=Some("Mines".into());first.context_until=Instant::now()+Duration::from_secs(5);
        let (tx,rx)=watch::channel(Some(first.clone()));let (status,_)=watch::channel(Availability::WaitingForGame);let mut timing=fast();timing.update=Duration::from_secs(10);let actor=tokio::spawn(run(connector,config(),rx,status,timing));
        time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap();first.area=None;tx.send_replace(Some(first.clone()));
        assert_eq!(time::timeout(Duration::from_millis(250),got.recv()).await.unwrap().unwrap()["args"]["activity"]["state"],"Exploring");
        first.game.pid=456;first.game.born=Instant::now();first.game.unix_start+=60;first.context=None;tx.send_replace(Some(first));
        let cleared=time::timeout(Duration::from_millis(250),got.recv()).await.unwrap().unwrap();assert_eq!(cleared["args"]["pid"],123);assert!(cleared["args"]["activity"].is_null());
        let replacement=time::timeout(Duration::from_millis(250),got.recv()).await.unwrap().unwrap();assert_eq!(replacement["args"]["pid"],456);assert_eq!(replacement["args"]["activity"]["timestamps"]["start"],1_800_000_060);assert!(replacement["args"]["activity"].get("state").is_none());
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();fake.await.unwrap();
    }
    #[tokio::test] async fn discord_restart_reconnects_and_keeps_owned_child_clock() {
        let (client1,mut server1)=duplex(4096);let (client2,server2)=duplex(4096);
        let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client1,client2]))),attempts:Arc::new(AtomicUsize::new(0))};
        let first_server=tokio::spawn(async move {
            read_frame(&mut server1).await.unwrap();write_frame(&mut server1,1,br#"{"evt":"READY","cmd":"DISPATCH","data":{"v":1}}"#).await.unwrap();
            let (_,bytes)=read_frame(&mut server1).await.unwrap();let value:Value=serde_json::from_slice(&bytes).unwrap();write_frame(&mut server1,1,&serde_json::to_vec(&json!({"cmd":"SET_ACTIVITY","nonce":value["nonce"]})).unwrap()).await.unwrap();
            // Dropping this pipe models Discord exiting, not a game lifecycle signal.
        });
        let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let second_server=tokio::spawn(fake_discord(server2,updates));
        let (tx,rx)=watch::channel(Some(snapshot()));let (status,_)=watch::channel(Availability::WaitingForGame);let mut timing=fast();timing.heartbeat=Duration::from_millis(40);let actor=tokio::spawn(run(connector,config(),rx,status,timing));
        let after=time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap();assert_eq!(after["args"]["pid"],123);assert_eq!(after["args"]["activity"]["timestamps"]["start"],1_800_000_000);
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();first_server.await.unwrap();second_server.await.unwrap();
    }
    struct HungConnector;
    impl Connector for HungConnector {
        type Stream=DuplexStream;
        async fn connect(&mut self)->io::Result<DuplexStream> { std::future::pending().await }
    }
    #[tokio::test] async fn hung_connect_is_bounded_and_shutdown_never_joins_it() {
        let (tx,rx)=watch::channel(Some(snapshot()));let (status,_)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(HungConnector,config(),rx,status,fast()));
        time::sleep(Duration::from_millis(5)).await;drop(tx);time::timeout(Duration::from_millis(250),actor).await.unwrap().unwrap();
    }
    #[tokio::test] async fn actor_clears_ended_game_without_any_ui_publish_or_reconnect() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let attempts=Arc::new(AtomicUsize::new(0));let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:attempts.clone()};
        let first=snapshot();let independent=first.game.clone();let (tx,rx)=watch::channel(Some(first));let (status,mut status_rx)=watch::channel(Availability::WaitingForGame);
        let mut timing=fast();timing.heartbeat=Duration::from_secs(30);let actor=tokio::spawn(run(connector,config(),rx,status,timing));
        assert!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"].is_object());
        time::timeout(Duration::from_secs(1),status_rx.wait_for(|status|*status==Availability::Connected)).await.unwrap().unwrap();
        // The UI's watch value deliberately stays Some(first) forever here.
        independent.end_synthetic();
        let clear=time::timeout(Duration::from_millis(750),got.recv()).await.unwrap().unwrap();assert!(clear["args"]["activity"].is_null());
        time::timeout(Duration::from_secs(1),status_rx.wait_for(|status|*status==Availability::WaitingForGame)).await.unwrap().unwrap();
        time::timeout(Duration::from_secs(1),fake).await.unwrap().unwrap();assert_eq!(got.recv().await,None);
        time::sleep(Duration::from_millis(300)).await;assert_eq!(attempts.load(Ordering::Relaxed),1);
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();
    }
    #[tokio::test] async fn dead_owned_witness_cancels_stalled_handshake_before_long_io_deadline() {
        let (client,mut hung_server)=duplex(4096);let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};
        let first=snapshot();let independent=first.game.clone();let (tx,rx)=watch::channel(Some(first));let (status,mut status_rx)=watch::channel(Availability::WaitingForGame);
        let mut timing=fast();timing.io=Duration::from_secs(10);let actor=tokio::spawn(run(connector,config(),rx,status,timing));
        assert_eq!(bounded(Duration::from_secs(1),read_frame(&mut hung_server)).await.unwrap().0,0);
        independent.end_synthetic();
        time::timeout(Duration::from_millis(750),status_rx.wait_for(|status|*status==Availability::WaitingForGame)).await.unwrap().unwrap();
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();
    }
    #[tokio::test] async fn dead_owned_witness_is_checked_during_long_discord_retry_wait() {
        let attempts=Arc::new(AtomicUsize::new(0));let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::new())),attempts:attempts.clone()};
        let first=snapshot();let independent=first.game.clone();let (tx,rx)=watch::channel(Some(first));let (status,mut status_rx)=watch::channel(Availability::WaitingForGame);
        let mut timing=fast();timing.retry=Duration::from_secs(30);let actor=tokio::spawn(run(connector,config(),rx,status,timing));
        time::timeout(Duration::from_secs(1),async {while attempts.load(Ordering::Relaxed)==0 {time::sleep(Duration::from_millis(5)).await;}}).await.unwrap();
        independent.end_synthetic();
        time::timeout(Duration::from_millis(750),status_rx.wait_for(|status|*status==Availability::WaitingForGame)).await.unwrap().unwrap();
        assert_eq!(attempts.load(Ordering::Relaxed),1);drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();
    }
    #[tokio::test] async fn actor_samples_peer_and_context_changes_without_ui_and_preserves_area_privacy() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};
        let mut first=snapshot();let source=Arc::new(std::sync::Mutex::new((2,LocalStatus{received_at:Some(first.game.born),context:Some(LocalContext::Exploring),area:Some("Mines".into())})));
        first.source=Some(LiveSource::Synthetic(Arc::downgrade(&source)));
        let (tx,rx)=watch::channel(Some(first));let (status,_)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(connector,config(),rx,status,fast()));
        let initial=time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap();assert_eq!(initial["args"]["activity"]["state"],"Exploring");assert_eq!(initial["args"]["activity"]["details"],"Entangled Worlds · 2 connected players");
        // No new UI watch publication occurs after the initial snapshot.
        *source.lock().unwrap()=(3,LocalStatus{received_at:Some(Instant::now()),context:Some(LocalContext::HolyMountain),area:Some("Mines".into())});
        let joined=time::timeout(Duration::from_millis(750),got.recv()).await.unwrap().unwrap();assert_eq!(joined["args"]["activity"]["details"],"Entangled Worlds · 3 connected players");assert_eq!(joined["args"]["activity"]["state"],"In the Holy Mountain");
        *source.lock().unwrap()=(1,LocalStatus{received_at:Some(Instant::now()),context:Some(LocalContext::AwaitingRevival),area:Some("Mines".into())});
        let left=time::timeout(Duration::from_millis(750),got.recv()).await.unwrap().unwrap();assert_eq!(left["args"]["activity"]["details"],"Entangled Worlds · 1 connected player");assert_eq!(left["args"]["activity"]["state"],"Awaiting revival");
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();fake.await.unwrap();
    }
    #[tokio::test] async fn dropped_weak_source_omits_stale_count_context_and_does_not_retain_session() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:Arc::new(AtomicUsize::new(0))};
        let mut first=snapshot();let source=Arc::new(std::sync::Mutex::new((2,LocalStatus{received_at:Some(first.game.born),context:Some(LocalContext::Exploring),area:Some("Mines".into())})));
        let weak=Arc::downgrade(&source);first.source=Some(LiveSource::Synthetic(weak.clone()));first.show_area=true;
        let (tx,rx)=watch::channel(Some(first));let (status,_)=watch::channel(Availability::WaitingForGame);let mut timing=fast();timing.update=Duration::from_secs(10);let actor=tokio::spawn(run(connector,config(),rx,status,timing));
        assert_eq!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"]["state"],"Exploring · Mines");
        drop(source);
        let generic=time::timeout(Duration::from_millis(750),got.recv()).await.unwrap().unwrap();assert_eq!(generic["args"]["activity"]["details"],"Entangled Worlds multiplayer");assert!(generic["args"]["activity"].get("state").is_none());assert!(weak.upgrade().is_none());
        drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();fake.await.unwrap();
    }
    #[test] fn read_only_source_contention_is_nonblocking_and_detached_count_has_a_lease() {
        let mut first=snapshot();let source=Arc::new(std::sync::Mutex::new((2,LocalStatus{received_at:Some(first.game.born),context:Some(LocalContext::Exploring),area:Some("Mines".into())})));
        first.source=Some(LiveSource::Synthetic(Arc::downgrade(&source)));
        let held=source.lock().unwrap();first.refresh(Instant::now());assert_eq!(first.activity(ARTWORK_IMAGE)["details"],"Entangled Worlds multiplayer");assert!(first.activity(ARTWORK_IMAGE).get("state").is_none());drop(held);
        first.refresh(Instant::now());assert_eq!(first.activity(ARTWORK_IMAGE)["details"],"Entangled Worlds · 2 connected players");
        let mut detached=snapshot();detached.refresh(detached.sampled_at+SAMPLE_LIFETIME+Duration::from_millis(1));assert_eq!(detached.activity(ARTWORK_IMAGE)["details"],"Entangled Worlds multiplayer");
    }
    #[tokio::test] async fn exhausted_budget_counts_null_clears_and_drops_without_delaying_privacy_or_liveness() {
        let (client,server)=duplex(4096);let (updates,mut got)=tokio::sync::mpsc::unbounded_channel();let fake=tokio::spawn(fake_discord(server,updates));
        let attempts=Arc::new(AtomicUsize::new(0));let connector=FakeConnector{streams:Arc::new(std::sync::Mutex::new(VecDeque::from([client]))),attempts:attempts.clone()};
        let mut first=snapshot();first.context=Some(LocalContext::Exploring);first.area=Some("Mines".into());first.context_until=Instant::now()+SAMPLE_LIFETIME;
        let independent=first.game.clone();let (tx,rx)=watch::channel(Some(first.clone()));let (status,mut status_rx)=watch::channel(Availability::WaitingForGame);let actor=tokio::spawn(run(connector,config(),rx,status,fast()));
        assert!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"].is_object());
        for pid in [456,789] {
            first.game.pid=pid;first.game.born=Instant::now();first.context_until=Instant::now()+SAMPLE_LIFETIME;tx.send_replace(Some(first.clone()));
            assert!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"].is_null());
            assert!(time::timeout(Duration::from_secs(1),got.recv()).await.unwrap().unwrap()["args"]["activity"].is_object());
        }
        // Five commands were used: three cards and two null clears. The sixth
        // urgent removal must close the pipe, never send/wait for budget refill.
        first.area=None;tx.send_replace(Some(first));
        time::timeout(Duration::from_millis(750),fake).await.unwrap().unwrap();assert_eq!(got.recv().await,None);
        time::sleep(Duration::from_millis(100)).await;assert_eq!(attempts.load(Ordering::Relaxed),1);
        independent.end_synthetic();
        time::timeout(Duration::from_millis(750),status_rx.wait_for(|status|*status==Availability::WaitingForGame)).await.unwrap().unwrap();
        assert_eq!(attempts.load(Ordering::Relaxed),1);drop(tx);time::timeout(Duration::from_secs(1),actor).await.unwrap().unwrap();
    }
}
