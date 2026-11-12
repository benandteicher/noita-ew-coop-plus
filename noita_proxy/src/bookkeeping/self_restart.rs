use std::{
    convert::Infallible,
    io,
    process::{Command, exit},
};

use crate::lobby_code::{LobbyCode, LobbyKind};

pub struct SelfRestarter {
    command: Command,
}

impl SelfRestarter {
    pub fn new() -> io::Result<Self> {
        // Hopefully fine to restart ourselves?
        let exe_path = std::env::current_exe()?;
        let mut command = Command::new(exe_path);
        if crate::frozen_runtime::ID.is_some() && std::env::var_os("COOPPLUS_NORMAL_ROOT").is_some() {
            // Keep the updater's explicit paths across a Steam invite restart;
            // missing proof still fails in the entry guard before any log/write.
            let args: Vec<_> = std::env::args_os().skip(1).collect();
            let mut index = 0;
            while index < args.len() {
                let text = args[index].to_string_lossy();
                for option in ["--exe-path", "--settings-path", "--save-state-path"] {
                    if text == option {
                        command.arg(&args[index]);
                        if let Some(value) = args.get(index + 1) { command.arg(value); }
                    } else if text.starts_with(&format!("{option}=")) { command.arg(&args[index]); }
                }
                index += 1;
            }
        }
        Ok(Self { command })
    }

    pub fn override_lobby_kind(&mut self, lobby_mode: LobbyKind) -> &mut Self {
        self.command.arg("--override-lobby-kind");
        self.command.arg(format!("{lobby_mode:?}"));
        self
    }

    pub fn connect_to(&mut self, lobby: LobbyCode) -> &mut Self {
        self.command.arg("--auto-connect-to");
        self.command.arg(lobby.serialize());
        self
    }

    pub fn restart(&mut self) -> io::Result<Infallible> {
        if let (Some(root), Some(session), Some(directory)) = (
            std::env::var_os("COOPPLUS_PRIVATE_ROOT"),
            std::env::var_os("COOPPLUS_PLAYTEST_SESSION"),
            std::env::var_os("COOPPLUS_PLAYTEST_SESSION_DIR"),
        ) {
            let root = std::path::PathBuf::from(root);
            let session = session.to_str().ok_or_else(|| io::Error::other("Invalid private session token"))?;
            if session.len() != 32 || !session.bytes().all(|b| b.is_ascii_hexdigit()) {
                return Err(io::Error::other("Invalid private session token"));
            }
            let directory = std::path::PathBuf::from(directory);
            crate::frozen_runtime::assert_safe(&directory);
            let expected = root.parent().ok_or_else(|| io::Error::other("Private session root is invalid"))?.join("playtest-logs").join(session);
            if directory != expected || !directory.is_dir() {
                return Err(io::Error::other("Private session handoff directory is invalid"));
            }
            #[cfg(windows)] {
                use std::io::Write;
                unsafe extern "system" {
                    fn GetCurrentProcess() -> *mut std::ffi::c_void;
                    fn GetProcessTimes(process: *mut std::ffi::c_void, created: *mut u64, exited: *mut u64, kernel: *mut u64, user: *mut u64) -> i32;
                }
                let (mut created, mut exited, mut kernel, mut user) = (0,0,0,0);
                if unsafe { GetProcessTimes(GetCurrentProcess(), &mut created, &mut exited, &mut kernel, &mut user) } == 0 {
                    return Err(io::Error::last_os_error());
                }
                let arguments: Vec<_> = self.command.get_args().map(|arg| arg.to_string_lossy().into_owned()).collect();
                let request = serde_json::json!({"schema":1,"session":session,"parentPid":std::process::id(),"parentCreatedFileTimeUtc":created,
                    "exe":std::env::current_exe()?,"directory":std::env::current_dir()?,"arguments":arguments});
                let path = directory.join(format!("restart-{}-{created}.json",std::process::id()));
                let temp = path.with_extension("new");
                let mut file = std::fs::OpenOptions::new().write(true).create_new(true).open(&temp)?;
                file.write_all(serde_json::to_vec(&request)?.as_slice())?;file.sync_all()?;drop(file);
                std::fs::rename(temp,path)?;
                crate::startup_diagnostics::event("intentional-restart-requested", &request.to_string());
                // The owning launcher dispatches the replacement only after this
                // exact process has exited, eliminating the Steam shutdown race.
                exit(42)
            }
        }
        self.command.spawn()?;
        exit(0)
    }
}
