use std::{
    fs, io,
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{self, AtomicBool},
    },
};

use tracing::{error, info, warn};

use crate::net::RunInfo;

pub trait SaveStateEntry: bitcode::Encode + bitcode::DecodeOwned {
    const FILENAME: &'static str;
}

struct SaveStateInner {
    game_started: AtomicBool,
}

/// Allows persisting extra run state (like chunks). Cleared between runs.
#[derive(Clone)]
pub struct SaveState {
    inner: Arc<SaveStateInner>,
    path: PathBuf,
    has_savestate: bool,
    ignore_saved: bool,
}

impl SaveState {
    pub(crate) fn new(path: impl AsRef<Path>) -> Self {
        let path = path.as_ref();
        let has_savestate = path.join(format!("{}.bit", RunInfo::FILENAME)).exists();
        if has_savestate && crate::frozen_runtime::private_root().is_some() {
            for name in ["run_info_v2.bit", "world_chunks.bit", "des_entity_storage.bit", "flags.bit"] {
                assert!(path.join(name).is_file(), "Incomplete private host Continue state: missing {name}; preserved without reset");
            }
        }
        info!("Has savestate: {has_savestate}");
        if !has_savestate {
            if crate::frozen_runtime::private_root().is_some() && path.exists() {
                assert!(fs::read_dir(path).expect("read private proxy state").next().is_none(), "Incomplete private proxy state; preserved without reset");
            }
            // Without a run info there is no run to continue, so anything else in here is either
            // stale or from an older format. bitcode is not self describing, so bumping the run
            // info filename is what keeps old saves from being decoded as the new shapes.
            fs::remove_dir_all(path).ok();
        }
        if let Err(err) = fs::create_dir_all(path) {
            error!("Error while creating directories: {err}");
        }
        let path = path.canonicalize().unwrap_or(path.to_path_buf());
        info!("Will save to: {}", path.display());
        Self {
            path,
            inner: Arc::new(SaveStateInner {
                game_started: false.into(),
            }),
            has_savestate,
            ignore_saved: false,
        }
    }

    pub(crate) fn save<D: SaveStateEntry>(&self, data: &D) {
        if !self.inner.game_started.load(atomic::Ordering::SeqCst) {
            info!("Skipping save of {}, game not started yet", D::FILENAME);
            return;
        }

        let path = self.path_for_filename(D::FILENAME);
        let encoded = bitcode::encode(data);
        let compressed = lz4_flex::compress_prepend_size(&encoded);
        if let Err(err) = fs::write(&path, compressed) {
            error!("Error while saving to {:?}: {err}", D::FILENAME);
        }
        info!("Saved {}", path.display());
    }

    pub(crate) fn load<D: SaveStateEntry>(&self) -> Option<D> {
        if self.ignore_saved {return None;}
        let path = self.path_for_filename(D::FILENAME);
        let data = fs::read(&path)
            .inspect_err(|err| {
                assert!(crate::frozen_runtime::private_root().is_none() || !self.has_savestate, "Missing/unreadable private host Continue state; preserved: {err}");
                if err.kind() == io::ErrorKind::NotFound {
                    info!("No save for {}, no need to load anything", D::FILENAME)
                } else {
                    warn!("Could not read {:?}: {err}", D::FILENAME)
                }
            })
            .ok()?;
        let data = lz4_flex::decompress_size_prepended(&data)
            .inspect_err(|err| { assert!(crate::frozen_runtime::private_root().is_none(), "Corrupt private proxy Continue state; preserved without reset: {err}"); warn!("Could not decompress {:?}: {err}", D::FILENAME) })
            .ok()?;
        bitcode::decode(&data)
            .inspect_err(|err| { assert!(crate::frozen_runtime::private_root().is_none(), "Corrupt private proxy Continue state; preserved without reset: {err}"); error!("Could not decode {:?}: {err}", D::FILENAME) })
            .ok()
    }

    pub(crate) fn mark_game_started(&self) {
        self.inner
            .game_started
            .store(true, atomic::Ordering::SeqCst);
    }

    pub(crate) fn reset(&self) {
        fs::remove_dir_all(&self.path).ok();
        fs::create_dir_all(&self.path).ok();
    }
    pub(crate) fn reset_checked(&self)->io::Result<()> {
        crate::frozen_runtime::assert_safe(&self.path);
        if self.path.exists(){fs::remove_dir_all(&self.path)?;}
        fs::create_dir_all(&self.path)?;
        if fs::read_dir(&self.path)?.next().is_some(){return Err(io::Error::other("Proxy new-run retirement did not produce an empty state folder; stopped without accepting the world"));}
        Ok(())
    }

    /// true if had a savestate initially.
    pub(crate) fn has_savestate(&self) -> bool {
        self.has_savestate
    }
    pub(crate) fn directory(&self)->&Path {&self.path}
    pub(crate) fn load_checked<D:SaveStateEntry>(&self)->io::Result<Option<D>> {
        if self.ignore_saved{return Ok(None);}
        let path=self.path_for_filename(D::FILENAME);
        let bytes=match fs::read(path){Ok(bytes)=>bytes,Err(error)if error.kind()==io::ErrorKind::NotFound=>return Ok(None),Err(error)=>return Err(error)};
        let decoded=lz4_flex::decompress_size_prepended(&bytes).map_err(|e|io::Error::new(io::ErrorKind::InvalidData,format!("Corrupt {} checkpoint; preserved: {e}",D::FILENAME)))?;
        bitcode::decode(&decoded).map(Some).map_err(|e|io::Error::new(io::ErrorKind::InvalidData,format!("Corrupt {} checkpoint; preserved: {e}",D::FILENAME)))
    }
    pub(crate) fn has_run_info(&self)->bool {self.path_for_filename(RunInfo::FILENAME).is_file()}
    /// Explicit new-run intent uses empty memory without touching the old
    /// checkpoint. Disk reset is deferred until actual new-world agreement.
    pub(crate) fn for_new_run(&self)->Self {
        Self {path:self.path.clone(),has_savestate:false,ignore_saved:true,inner:Arc::new(SaveStateInner {game_started:false.into()})}
    }
    pub(crate) fn for_session(&self)->Self {
        Self {path:self.path.clone(),has_savestate:self.has_run_info(),ignore_saved:false,inner:Arc::new(SaveStateInner {game_started:false.into()})}
    }
    pub(crate) fn mark_game_not_started(&self) {self.inner.game_started.store(false,atomic::Ordering::SeqCst);}
    pub(crate) fn game_started(&self)->bool{self.inner.game_started.load(atomic::Ordering::SeqCst)}

    fn path_for_filename(&self, filename: &str) -> PathBuf {
        self.path.join(format!("{filename}.bit"))
    }
}
