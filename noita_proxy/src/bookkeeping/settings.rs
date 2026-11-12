use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use serde::{Deserialize, Serialize};

use crate::{
    app::AppSavedState, audio_settings::AudioSettings, paths::Paths,
    player_settings::PlayerAppearance,
};

#[derive(Debug, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Settings {
    pub color: PlayerAppearance,
    pub app: AppSavedState,
    pub audio: AudioSettings,
    pub paths: Paths,
}

impl Settings {
    pub fn load(path: &Path) -> io::Result<Settings> {
        let mut file = File::open(path)?;
        let mut s = String::new();
        file.read_to_string(&mut s)?;
        let settings = match ron::from_str::<Settings>(&s) {
            Ok(value) => value,
            Err(error) if crate::frozen_runtime::ID.is_some() => return Err(io::Error::new(io::ErrorKind::InvalidData, error.to_string())),
            Err(_) => Settings::default(),
        };
        Ok(settings)
    }

    pub fn save(&self, path: &Path) -> io::Result<()> {
        let settings = ron::to_string(&self).map_err(|e|io::Error::new(io::ErrorKind::InvalidData,e.to_string()))?;
        atomic_write(path,settings.as_bytes())?;
        crate::startup_diagnostics::event("preferences-persisted",&format!("next_run_mode={}",self.app.game_settings.game_mode.unwrap_or(crate::DefaultSettings::default().game_mode)));Ok(())
    }
}

/// Complete the new bytes before replacing settings; a failed write never
/// truncates the previous preferences or authoritative run-rules sidecar.
pub(crate) fn atomic_write(path:&Path,bytes:&[u8])->io::Result<()> {
    static NEXT:AtomicU64=AtomicU64::new(0);
    let parent=path.parent().ok_or_else(||io::Error::other("settings path has no parent"))?;
    fs::create_dir_all(parent)?;
    let temp=parent.join(format!(".coopplus-settings-{}-{}.tmp",std::process::id(),NEXT.fetch_add(1,Ordering::Relaxed)));
    let mut output=OpenOptions::new().write(true).create_new(true).open(&temp)?;
    output.write_all(bytes)?;output.sync_all()?;drop(output);
    fs::rename(&temp,path)
}
