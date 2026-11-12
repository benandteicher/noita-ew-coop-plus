//! Frozen custom runtime and fail-closed private playtest routing. No network/game optimization.
use std::{fs, path::{Path, PathBuf}};
use crate::{Args, paths::Paths};
pub const ID: Option<&str> = option_env!("COOPPLUS_RUNTIME_ID");
fn safe_path(path: &Path) -> Result<(), String> {
    let text = path.to_str().ok_or("The launch path is not valid Unicode.")?;
    if !path.is_absolute() || text.starts_with("\\\\") || text.get(2..).is_some_and(|s| s.contains(':')) {
        return Err("The launcher requires absolute local drive paths.".into());
    }
    for part in path.components() {
        if matches!(part, std::path::Component::ParentDir | std::path::Component::CurDir) {
            return Err("Relative path components are refused.".into());
        }
    }
    for ancestor in path.ancestors() {
        match fs::symlink_metadata(ancestor) {
            Ok(meta) => {
                if meta.file_type().is_symlink() { return Err("Linked launch paths are refused.".into()); }
                #[cfg(windows)] {
                    use std::os::windows::fs::MetadataExt;
                    if meta.file_attributes() & 0x400 != 0 { return Err("Reparse launch paths are refused.".into()); }
                }
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("Cannot inspect the launch path: {error}")),
        }
        if ancestor.join(".git").exists() { return Err("Launch from a source checkout is refused.".into()); }
    }
    Ok(())
}
fn same_existing(actual: &Path, expected: &Path) -> Result<(), String> {
    safe_path(actual)?; safe_path(expected)?;
    let actual = actual.canonicalize().map_err(|e| format!("Cannot resolve launched executable/directory: {e}"))?;
    let expected = expected.canonicalize().map_err(|e| format!("Incomplete launcher runtime: {e}"))?;
    if !actual.to_string_lossy().eq_ignore_ascii_case(&expected.to_string_lossy()) {
        return Err("The executable or working directory is outside its launcher runtime.".into());
    }
    Ok(())
}
fn metadata(path: &Path) -> Result<serde_json::Value, String> {
    safe_path(path)?;
    if !path.is_file() || fs::metadata(path).map_err(|e| e.to_string())?.len() > 16 * 1024 * 1024 {
        return Err("Missing or oversized launcher metadata.".into());
    }
    serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?).map_err(|_| "Invalid launcher metadata.".into())
}
fn guard_context(args: &Args, executable: &Path, directory: &Path, private: Option<&Path>, normal: Option<&Path>, id: Option<&str>) -> Result<(), String> {
    let Some(id) = id else { return Ok(()); };
    match (private, normal) {
        (Some(root), None) => {
            safe_path(root)?;
            same_existing(executable, &root.join("runtime/bin/noita_proxy.exe"))?;
            same_existing(directory, &root.join("runtime/bin"))?;
            let ready = metadata(&root.join("ready.json"))?;
            let hash = ready["payloadHash"].as_str().unwrap_or("");
            if ready["schema"].as_u64() != Some(1) || ready["runtimeId"].as_str() != Some(id)
                || hash.len() != 64 || !hash.bytes().all(|b| b.is_ascii_hexdigit())
                || ready["game"].as_array().is_none_or(|v| v.is_empty()) {
                return Err("The private copy is incomplete or belongs to a different frozen playtest.".into());
            }
            for required in ["game/noita.exe", "game/save00/mod_config.xml", "game/save_shared/config.xml", "proxy/proxy.ron"] {
                let path = root.join(required); safe_path(&path)?;
                if !path.is_file() { return Err(format!("Private storage is incomplete: {required}")); }
            }
            for required in ["proxy/save_state", "temp", "profile/AppData/Local"] {
                let path = root.join(required); safe_path(&path)?;
                if !path.is_dir() { return Err(format!("Private storage is incomplete: {required}")); }
            }
            Ok(())
        }
        (None, Some(root)) => {
            safe_path(root)?;
            same_existing(executable, &root.join("current/proxy/noita_proxy.exe"))?;
            same_existing(directory, &root.join("current/proxy"))?;
            let installed = metadata(&root.join("current/installed.json"))?;
            if installed["schema"].as_u64() != Some(1) || installed["files"].as_array().is_none_or(|v| v.is_empty()) {
                return Err("The normal launcher installation is incomplete.".into());
            }
            for required in ["CoopPlus.exe", "coopplus.json"] {
                let path = root.join(required); safe_path(&path)?;
                if !path.is_file() { return Err("Open the complete usual CoopPlus.exe installation.".into()); }
            }
            for path in [args.exe_path.as_deref(), args.settings_path.as_deref(), args.save_state_path.as_deref()] {
                safe_path(path.ok_or("The normal updater must supply explicit game, settings and state paths after verified backup.")?)?;
            }
            let game = args.exe_path.as_deref().unwrap();
            if !game.is_file() || game.file_name().is_none_or(|s| !s.to_string_lossy().eq_ignore_ascii_case("noita.exe")) {
                return Err("The explicit normal game executable is unavailable.".into());
            }
            Ok(())
        }
        _ => Err("The frozen runtime requires exactly one authorized launcher context.".into()),
    }
}
pub fn validate_entry_context(args: &Args) -> Result<(), String> {
    let private = std::env::var_os("COOPPLUS_PRIVATE_ROOT").map(PathBuf::from);
    let normal = std::env::var_os("COOPPLUS_NORMAL_ROOT").map(PathBuf::from);
    guard_context(args, &std::env::current_exe().map_err(|e| e.to_string())?, &std::env::current_dir().map_err(|e| e.to_string())?, private.as_deref(), normal.as_deref(), ID)?;
    if ID.is_some() {
        for name in ["NP_DISABLE_STEAM", "NP_APPID", "NP_NOITA_ADDR", "NP_ALLOW_REUSE_ADDR", "NP_WORLD_SYNC_TEST"] {
            if std::env::var_os(name).is_some() { return Err(format!("Frozen runtime refuses developer override {name}.")); }
        }
    }
    Ok(())
}
pub fn report_entry_error(reason: &str) {
    let message = format!("Open Co-op+ Playtest.exe for isolated testing, or your usual CoopPlus.exe for normal play. This frozen runtime cannot be opened directly.\n\n{reason}");
    eprintln!("{message}");
    #[cfg(windows)] {
        #[link(name = "user32")]
        unsafe extern "system" { fn MessageBoxW(window: *mut std::ffi::c_void, text: *const u16, title: *const u16, flags: u32) -> i32; }
        let text: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "Co-op+ launch refused".encode_utf16().chain(Some(0)).collect();
        unsafe { MessageBoxW(std::ptr::null_mut(), text.as_ptr(), title.as_ptr(), 0x10); }
    }
}
// The legacy-visible version rejects old clients in both host/join directions.
// The exact runtime key additionally prevents same-version custom payload mixing.
pub fn peer_version() -> crate::releases::Version {
    if ID.is_some() { crate::releases::Version { major: 77, minor: 18, patch: 7 } }
    else { crate::releases::Version::current() }
}
pub fn private_root() -> Option<PathBuf> {
    let root = std::env::var_os("COOPPLUS_PRIVATE_ROOT").map(PathBuf::from)?;
    assert!(ID.is_some(), "A private playtest requires a frozen custom runtime");
    assert!(root.is_absolute() && root.join("ready.json").is_file(), "Incomplete private playtest; open Co-op+ Playtest.exe");
    assert_safe(&root);
    Some(root)
}
pub fn assert_safe(path: &Path) {
    for ancestor in path.ancestors() {
        if let Ok(meta) = fs::symlink_metadata(ancestor) {
            assert!(!meta.file_type().is_symlink(), "Private playtest refuses links");
            #[cfg(windows)] { use std::os::windows::fs::MetadataExt; assert!(meta.file_attributes() & 0x400 == 0, "Private playtest refuses reparse paths"); }
        }
    }
}
pub fn configure(args: &mut Args) {
    if ID.is_some() {
        for name in ["NP_DISABLE_STEAM", "NP_APPID", "NP_NOITA_ADDR", "NP_ALLOW_REUSE_ADDR", "NP_WORLD_SYNC_TEST"] {
            assert!(std::env::var_os(name).is_none(), "Frozen runtime refuses developer override {name}");
        }
        assert!(args.host.as_deref().is_none_or(|x| x.eq_ignore_ascii_case("steam")), "Frozen runtime requires Steam matching-peer host/join");
        assert!(args.lobby.as_deref().is_none_or(|x| !x.contains(':')), "Frozen runtime requires Steam matching-peer host/join");
    }
    if let Some(root) = private_root() {
        let bin = root.join("runtime/bin");
        assert_eq!(std::env::current_exe().unwrap().parent().unwrap().canonicalize().unwrap(), bin.canonicalize().unwrap(), "Playtest proxy must run in its private runtime");
        args.exe_path = Some(root.join("game/noita.exe"));
        args.settings_path = Some(root.join("proxy/proxy.ron"));
        args.save_state_path = Some(root.join("proxy/save_state"));
        args.launch_cmd = None;
        args.run_noita_with_gdb = false;
        args.override_lobby_kind = Some(crate::lobby_code::LobbyKind::Steam);
    }
}
pub fn route_paths(paths: &mut Paths) {
    if let Some(root) = private_root() {
        paths.noita_exe = Some(root.join("game/noita.exe"));
        paths.noita_save = Some(root.join("game"));
        crate::paths::realize_noita_paths_from_noita_exe(paths);
    }
}
pub fn label() -> String {
    match ID { Some(id) if private_root().is_some() => format!("Co-op+ PLAYTEST · {id} · PRIVATE saves"), Some(id) => format!("Co-op+ frozen runtime · {id}"), None => "Noita Entangled Worlds Proxy".into() }
}

#[cfg(test)]
mod entry_tests {
    use super::*;
    fn args() -> Args {
        Args { launch_cmd: None, ui_zoom_factor: None, lobby: None, host: None, exe_path: None,
            language: None, settings_path: None, save_state_path: None, override_lobby_kind: None,
            auto_connect_to: None, run_noita_with_gdb: false }
    }
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("coopplus-entryguard-{}-{}", std::process::id(), std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
            fs::create_dir(&root).unwrap(); Self(root)
        }
        fn put(&self, relative: &str, data: &[u8]) {
            let path = self.0.join(relative); fs::create_dir_all(path.parent().unwrap()).unwrap(); fs::write(path, data).unwrap();
        }
        fn private(&self) {
            for path in ["runtime/bin/noita_proxy.exe", "game/noita.exe", "game/save00/mod_config.xml", "game/save_shared/config.xml", "proxy/proxy.ron"] { self.put(path, b"fixture"); }
            for path in ["proxy/save_state", "temp", "profile/AppData/Local"] { fs::create_dir_all(self.0.join(path)).unwrap(); }
            self.put("ready.json", br#"{"schema":1,"runtimeId":"unit-frozen-id","payloadHash":"1111111111111111111111111111111111111111111111111111111111111111","game":[{}]}"#);
        }
        fn normal(&self) -> Args {
            for path in ["current/proxy/noita_proxy.exe", "CoopPlus.exe", "coopplus.json", "game/noita.exe"] { self.put(path, b"fixture"); }
            self.put("current/installed.json", br#"{"schema":1,"files":[{}]}"#);
            let mut result = args(); result.exe_path = Some(self.0.join("game/noita.exe"));
            result.settings_path = Some(self.0.join("settings/proxy.ron")); result.save_state_path = Some(self.0.join("state")); result
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            // Remove only this new test identity, after checking its absolute parent.
            if self.0.parent().and_then(|p| p.canonicalize().ok()) == std::env::temp_dir().canonicalize().ok()
                && self.0.file_name().unwrap().to_string_lossy().starts_with("coopplus-entryguard-") {
                let _ = fs::remove_dir_all(&self.0);
            }
        }
    }
    #[test] fn raw_and_ambiguous_contexts_refuse_before_any_runtime_write() {
        let f = Fixture::new(); f.put("sentinel", b"preserved");
        assert!(guard_context(&args(), &f.0.join("payload/bin/noita_proxy.exe"), &f.0, None, None, Some("unit-frozen-id")).is_err());
        assert!(guard_context(&args(), &f.0, &f.0, Some(&f.0), Some(&f.0), Some("unit-frozen-id")).is_err());
        assert_eq!(fs::read(f.0.join("sentinel")).unwrap(), b"preserved");
        assert!(!f.0.join("ew_log.txt").exists());
    }
    #[test] fn private_context_requires_exact_executable_workdir_identity_and_storage() {
        let f = Fixture::new(); f.private();
        let exe = f.0.join("runtime/bin/noita_proxy.exe"); let cwd = f.0.join("runtime/bin");
        assert!(guard_context(&args(), &exe, &cwd, Some(&f.0), None, Some("unit-frozen-id")).is_ok());
        assert!(guard_context(&args(), &exe, &f.0, Some(&f.0), None, Some("unit-frozen-id")).is_err());
        assert!(guard_context(&args(), &exe, &cwd, Some(&f.0), None, Some("other-id")).is_err());
        fs::remove_file(f.0.join("proxy/proxy.ron")).unwrap();
        assert!(guard_context(&args(), &exe, &cwd, Some(&f.0), None, Some("unit-frozen-id")).is_err());
    }
    #[test] fn normal_context_requires_updater_layout_metadata_and_all_explicit_paths() {
        let f = Fixture::new(); let mut a = f.normal();
        let exe = f.0.join("current/proxy/noita_proxy.exe"); let cwd = f.0.join("current/proxy");
        assert!(guard_context(&a, &exe, &cwd, None, Some(&f.0), Some("unit-frozen-id")).is_ok());
        a.save_state_path = None;
        assert!(guard_context(&a, &exe, &cwd, None, Some(&f.0), Some("unit-frozen-id")).is_err());
        a.save_state_path = Some(PathBuf::from("relative-state"));
        assert!(guard_context(&a, &exe, &cwd, None, Some(&f.0), Some("unit-frozen-id")).is_err());
        a.save_state_path = Some(f.0.join("state")); fs::remove_file(f.0.join("current/installed.json")).unwrap();
        assert!(guard_context(&a, &exe, &cwd, None, Some(&f.0), Some("unit-frozen-id")).is_err());
    }
}
