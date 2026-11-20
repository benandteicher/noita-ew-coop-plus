use std::{
    ffi::OsString,
    fs::File,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command},
};

use steamworks::AppId;
use tracing::{info, warn};

use crate::{paths::Paths, steam_helper::SteamState};

struct NoitaStartCmd {
    executable: OsString,
    args: Vec<OsString>,
    steam_install: Option<PathBuf>,
    noita_compat_data: Option<PathBuf>,
    noita_install: Option<PathBuf>,
}

impl NoitaStartCmd {
    fn from_full_args(args: &[OsString]) -> Option<Self> {
        let (executable, args) = args.split_first()?;
        Some(Self {
            executable: executable.to_owned(),
            args: args.to_vec(),
            steam_install: None,
            noita_compat_data: None,
            noita_install: None,
        })
    }
}

pub enum LaunchTokenResult<'a> {
    Ok(LaunchToken<'a>),
    AlreadyStarted,
    CantStart,
}

pub struct LaunchToken<'a>(&'a mut NoitaLauncher);

pub struct NoitaLauncher {
    noita_install: PathBuf,
    start_args: Option<NoitaStartCmd>,
    noita_process: Option<Child>,
    discord_game: Option<crate::discord_presence::OwnedGame>,
    discord_direct_child: bool,
}

impl NoitaLauncher {
    pub fn new(
        paths: &Paths,
        start_args: Option<&str>,
        steam_state: Option<&mut SteamState>,
    ) -> Self {
        let noita_install = paths.noita_install();

        let default_start_args = || {
            if cfg!(target_os = "windows") {
                Some(NoitaStartCmd {
                    executable: paths.noita_exe().as_os_str().to_owned(),
                    args: Vec::new(),
                    steam_install: None,
                    noita_compat_data: None,
                    noita_install: None,
                })
            } else {
                linux_try_get_noita_start_cmd(paths, steam_state)
            }
        };

        let start_args = if crate::frozen_runtime::private_root().is_some() { None } else { start_args };
        let start_args = start_args
            .and_then(shlex::split)
            .map(|v| v.into_iter().map(OsString::from).collect::<Vec<_>>())
            .and_then(|args| NoitaStartCmd::from_full_args(&args));
        let mut start_args = start_args.or_else(default_start_args);
        if crate::frozen_runtime::private_root().is_some() {
            let cmd = start_args.as_mut().expect("private Windows launch");
            cmd.args = vec!["-always_store_userdata_in_workdir".into()];
            assert_eq!(Path::new(&cmd.executable), paths.noita_exe());
        } else if cfg!(windows) && crate::frozen_runtime::ID.is_some() && start_args.as_ref().is_some() {
            let default_profile = std::env::var_os("USERPROFILE").map(PathBuf::from).expect("Windows profile").join("AppData/LocalLow/Nolla_Games_Noita");
            if let Some(profile) = &paths.noita_save {
                if profile == paths.noita_install() {
                    let cmd = start_args.as_mut().unwrap();
                    assert_eq!(Path::new(&cmd.executable), paths.noita_exe(), "Portable Co-op+ requires direct Noita launch");
                    cmd.args = vec!["-always_store_userdata_in_workdir".into()];
                } else {
                    assert_eq!(profile, &default_profile, "Configured Noita profile does not match actual native launch; refusing fallback");
                }
            }
        }

        let discord_direct_child = cfg!(windows) && start_args.as_ref().is_some_and(|cmd|
            Path::new(&cmd.executable) == paths.noita_exe());
        Self {
            noita_install: noita_install.to_path_buf(),
            start_args,
            noita_process: None,
            discord_game: None,
            discord_direct_child,
        }
    }

    fn check_if_noita_running(&mut self) -> bool {
        match self.noita_process.as_mut().map(|child| child.try_wait()) {
            Some(Ok(Some(_))) => false, // Already exited
            Some(Ok(None)) => true,     // Not yet exited
            Some(Err(_)) => false,      // Could not wait for child.
            None => false,              // No child stored.
        }
    }

    pub fn launch_token(&mut self) -> LaunchTokenResult<'_> {
        if self.check_if_noita_running() {
            return LaunchTokenResult::AlreadyStarted;
        }

        if self.start_args.is_some() {
            LaunchTokenResult::Ok(LaunchToken(self))
        } else {
            LaunchTokenResult::CantStart
        }
    }

    pub(crate) fn discord_owned_game(&mut self) -> Option<crate::discord_presence::OwnedGame> {
        // The retained Child is the identity. No PID search, socket state, Steam
        // process, wrapper/custom launch command or unrelated game can activate RPC.
        if self.discord_direct_child && self.check_if_noita_running() {
            self.discord_game.clone()
        } else {
            self.discord_game = None;
            None
        }
    }

    fn remember_child(&mut self, child: std::io::Result<Child>) {
        if crate::frozen_runtime::staging() {
            match &child {
                Ok(_) => crate::startup_diagnostics::event("noita-launch-start", "result=accepted"),
                Err(error) => crate::startup_diagnostics::failure("noita-launch-failure",
                    &format!("result=refused code={}", error.raw_os_error().unwrap_or(-1))),
            }
        }
        self.noita_process = child.ok();
        self.discord_game = if self.discord_direct_child {
            self.noita_process.as_ref().and_then(|child| crate::discord_presence::OwnedGame::from_child(child)
                .inspect_err(|error|warn!("Discord activity disabled for this game: owned liveness handle could not be duplicated: {error}"))
                .ok())
        } else { None };
    }
}

fn linux_try_get_noita_start_cmd(
    paths: &Paths,
    steam_state: Option<&mut SteamState>,
) -> Option<NoitaStartCmd> {
    let steam_install = paths.steam_install.as_ref()?.to_owned();
    let noita_compatdata_path = steam_install.join("steamapps/compatdata/881100/");
    if !noita_compatdata_path.exists() {
        return None;
    }
    let config_info_path = noita_compatdata_path.join("config_info");
    let config_info_file = File::open(config_info_path)
        .inspect_err(|err| warn!("Couldn't open config_info : {}", err))
        .ok()?;
    let proton_fonts = BufReader::new(config_info_file)
        .lines()
        .nth(1)?
        .inspect_err(|err| warn!("Couldn't find proton fonts paths: {}", err))
        .ok()?;
    let proton_fonts = Path::new(&proton_fonts);
    let proton_install = proton_path_from_fonts(proton_fonts)?;
    let proton_binary = proton_install.join("proton").into_os_string();
    let tool_manifest = File::open(proton_install.join("toolmanifest.vdf"))
        .inspect_err(|err| warn!("Couldn't open toolmanifest.vdf file: {}", err))
        .ok()?;
    let runtime_appid = BufReader::new(tool_manifest)
        .lines()
        .map(|l| l.unwrap())
        .find(|l| l.contains("require_tool_appid"))
        .map(|a| a.split('"').nth(3).map(|b| b.parse::<u32>()));

    let noita_exe = paths.noita_exe().as_os_str().to_owned();
    let noita_install = paths.noita_install().to_owned();
    let (executable, args) = match (steam_state, runtime_appid) {
        (Some(state), Some(Some(Ok(1628350)))) => {
            let apps = state.client.apps();
            let app_id = AppId::from(1628350);
            let app_install_dir = apps.app_install_dir(app_id);
            (
                PathBuf::from(app_install_dir)
                    .join("_v2-entry-point")
                    .into(),
                vec!["--verb=run".into(), proton_binary, "run".into(), noita_exe],
            )
        }
        (_, Some(Some(Ok(_)))) => {
            let app_install_dir = proton_install.parent()?.join("SteamLinuxRuntime_sniper");
            if app_install_dir.exists() {
                (
                    app_install_dir.join("_v2-entry-point").into(),
                    vec!["--verb=run".into(), proton_binary, "run".into(), noita_exe],
                )
            } else {
                (proton_binary, vec!["run".into(), noita_exe])
            }
        }
        _ => (proton_binary, vec!["run".into(), noita_exe]),
    };
    Some(NoitaStartCmd {
        executable,
        args,
        steam_install: Some(steam_install),
        noita_compat_data: Some(noita_compatdata_path),
        noita_install: Some(noita_install),
    })
}

fn proton_path_from_fonts(proton_path_fonts: &Path) -> Option<PathBuf> {
    Some(proton_path_fonts.parent()?.parent()?.parent()?.into())
}

impl LaunchToken<'_> {
    pub fn start_game(&mut self, port: u16) {
        let addr_env = format!("127.0.0.1:{port}");
        let start_cmd = self.0.start_args.as_ref().unwrap();
        let child = if let Some(game_path) = &start_cmd.noita_install {
            let steam_install = start_cmd.steam_install.clone().unwrap();
            let compat_data = start_cmd.noita_compat_data.clone().unwrap();
            std::env::set_current_dir(game_path).unwrap();

            info!("Steam install: {}", steam_install.display());
            info!("Compat data: {}", compat_data.display());
            info!("Game path: {}", game_path.display());
            info!("Exe path: {}", start_cmd.executable.to_str().unwrap());
            info!("Args: {:?}", start_cmd.args);

            Command::new(&start_cmd.executable)
                .env("STEAM_COMPAT_CLIENT_INSTALL_PATH", steam_install)
                .env("STEAM_COMPAT_DATA_PATH", compat_data)
                .env("NP_NOITA_ADDR", &addr_env)
                .env("SteamAppId", "881100")
                .env("SteamGameId", "881100")
                .current_dir(&self.0.noita_install)
                .args(&start_cmd.args)
                .spawn()
        } else {
            Command::new(&start_cmd.executable)
                .env("NP_NOITA_ADDR", &addr_env)
                .env("SteamAppId", "881100")
                .env("SteamGameId", "881100")
                .current_dir(&self.0.noita_install)
                .args(&start_cmd.args)
                .spawn()
        };
        self.0.remember_child(child);
    }
}

#[cfg(all(test, windows))]
mod discord_child_tests {
    use super::*;
    use std::{process::Stdio, time::{Duration,Instant,SystemTime,UNIX_EPOCH}};

    const FIXTURE: &str = "bookkeeping::noita_launcher::discord_child_tests::owned_child_fixture";

    #[test]
    #[ignore = "Only spawned as an owned test helper by discord_child_tests; never launches Noita"]
    fn owned_child_fixture() {
        let mode = std::env::var("COOPPLUS_DISCORD_TEST_CHILD").expect("owned fixture mode");
        std::thread::sleep(Duration::from_millis(160));
        std::process::exit(match mode.as_str() { "normal" => 0, "nonzero" => 23, _ => 91 });
    }

    fn child(mode: &str) -> std::io::Result<Child> {
        Command::new(std::env::current_exe().unwrap())
            .args(["--exact", FIXTURE, "--ignored", "--nocapture"])
            .env("COOPPLUS_DISCORD_TEST_CHILD", mode)
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn()
    }

    fn launcher(direct: bool) -> NoitaLauncher {
        NoitaLauncher { noita_install:std::env::temp_dir(), start_args:None,
            noita_process:None, discord_game:None, discord_direct_child:direct }
    }

    fn wait_owned(child: &mut Child) -> std::process::ExitStatus {
        let limit = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(status) = child.try_wait().unwrap() { return status; }
            if Instant::now() >= limit {
                // This handle was just spawned by this test; never enumerate/stop user processes.
                let _ = child.kill(); let _ = child.wait(); panic!("Owned Discord fixture exceeded deadline");
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    #[test] fn retained_child_normal_exit_clears_even_if_status_could_remain() {
        let mut launcher = launcher(true); launcher.remember_child(child("normal"));
        let independent = launcher.discord_owned_game().unwrap(); assert!(independent.is_alive());
        assert!(wait_owned(launcher.noita_process.as_mut().unwrap()).success());
        // This check deliberately does not call any UI/launcher polling method.
        assert!(!independent.is_alive()); launcher.noita_process=None; assert!(!independent.is_alive());
        assert!(launcher.discord_owned_game().is_none()); assert!(launcher.discord_game.is_none());
    }

    #[test] fn retained_child_nonzero_exit_clears_without_relaunch_or_adoption() {
        let mut launcher = launcher(true); launcher.remember_child(child("nonzero"));
        let independent = launcher.discord_owned_game().unwrap(); assert!(independent.is_alive());
        assert_eq!(wait_owned(launcher.noita_process.as_mut().unwrap()).code(),Some(23));
        assert!(!independent.is_alive());
        assert!(launcher.discord_owned_game().is_none());
        assert!(launcher.noita_process.as_mut().unwrap().try_wait().unwrap().is_some());
    }

    #[test] fn duplicate_witness_outlives_original_child_handle_without_pid_lookup() {
        let original=child("normal").unwrap();
        let independent=crate::discord_presence::OwnedGame::from_child(&original).unwrap();
        drop(original);assert!(independent.is_alive());
        let deadline=Instant::now()+Duration::from_secs(5);
        while independent.is_alive() {
            assert!(Instant::now()<deadline,"Owned test helper did not exit");
            std::thread::sleep(Duration::from_millis(5));
        }
        assert!(!independent.is_alive());
    }

    #[test] fn clock_is_stable_per_retained_child_and_resets_for_a_new_child() {
        let mut launcher = launcher(true); launcher.remember_child(child("normal"));
        let first = launcher.discord_owned_game().unwrap();
        std::thread::sleep(Duration::from_millis(5)); assert_eq!(launcher.discord_owned_game(),Some(first.clone()));
        wait_owned(launcher.noita_process.as_mut().unwrap()); assert!(launcher.discord_owned_game().is_none());
        launcher.remember_child(child("normal")); let second = launcher.discord_owned_game().unwrap();
        assert!(second.born > first.born); assert!(second.unix_start >= first.unix_start);
        assert_eq!(launcher.discord_owned_game(),Some(second.clone())); wait_owned(launcher.noita_process.as_mut().unwrap());
    }

    #[test] fn unrelated_or_wrapper_child_and_forged_pid_do_not_activate_presence() {
        let mut stranger = child("normal").unwrap(); let mut empty = launcher(true);
        empty.discord_game = Some(crate::discord_presence::OwnedGame::synthetic(stranger.id(),Instant::now(),1));
        assert!(empty.discord_owned_game().is_none()); wait_owned(&mut stranger);
        let mut wrapper = launcher(false); wrapper.remember_child(child("normal"));
        assert!(wrapper.noita_process.is_some()); assert!(wrapper.discord_owned_game().is_none());
        wait_owned(wrapper.noita_process.as_mut().unwrap());
    }

    #[test] fn actual_spawn_failure_never_creates_presence_identity() {
        let mut launcher = launcher(true);
        let missing = std::env::temp_dir().join(format!("coopplus-discord-nonexistent-{}-{}.exe",std::process::id(),SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
        assert!(!missing.exists());
        launcher.start_args = Some(NoitaStartCmd { executable:missing.into_os_string(),args:Vec::new(),steam_install:None,noita_compat_data:None,noita_install:None });
        let LaunchTokenResult::Ok(mut token) = launcher.launch_token() else { panic!("expected owned launch token") };
        token.start_game(21251);
        assert!(launcher.noita_process.is_none()); assert!(launcher.discord_owned_game().is_none());
    }
}
