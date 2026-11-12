//! Bounded, synchronous lifecycle evidence. Never moves input or repairs state.
use std::{fs::{self, OpenOptions}, io::{Read, Seek, SeekFrom, Write}, path::{Path, PathBuf}, sync::{Mutex, OnceLock}, time::{SystemTime, UNIX_EPOCH}};
use serde_json::json;

const LIMIT: u64 = 512 * 1024;
static LOG: OnceLock<Option<Mutex<Log>>> = OnceLock::new();
struct Log { path: PathBuf, bytes: u64, capped: bool }

#[derive(serde::Serialize,serde::Deserialize)]
struct NormalStamp { schema:u32, owner:String, pid:u32, created_filetime:u64 }
#[cfg(windows)]
fn process_created(pid:u32)->Result<Option<u64>,String> {
    unsafe {
        #[link(name="kernel32")] unsafe extern "system" {
            fn OpenProcess(access:u32,inherit:i32,pid:u32)->*mut std::ffi::c_void;
            fn GetProcessTimes(handle:*mut std::ffi::c_void,created:*mut u64,exit:*mut u64,kernel:*mut u64,user:*mut u64)->i32;
            fn CloseHandle(handle:*mut std::ffi::c_void)->i32;
        }
        let handle=OpenProcess(0x1000,0,pid);
        if handle.is_null() {
            let error=std::io::Error::last_os_error();
            return if error.raw_os_error()==Some(87){Ok(None)}else{Err(error.to_string())};
        }
        let (mut created,mut exit,mut kernel,mut user)=(0,0,0,0);
        let ok=GetProcessTimes(handle,&mut created,&mut exit,&mut kernel,&mut user);CloseHandle(handle);
        if ok==0 {Err("Cannot establish diagnostic owner lifetime".into())} else {Ok(Some(created))}
    }
}
#[cfg(not(windows))]
fn process_created(_:u32)->Result<Option<u64>,String>{Err("Normal lifecycle diagnostics require Windows process identity".into())}

fn known_normal_file(name:&str,pid:u32)->bool {
    if name=="normal-session.json" || name==format!("prior-{pid}-ew_log.txt"){return true;}
    let prefix=format!("proxy-{pid}-");
    let Some(rest)=name.strip_prefix(&prefix) else{return false;};
    let Some(stamp)=rest.strip_suffix(".jsonl").or_else(||rest.strip_suffix(".failure.txt")) else{return false;};
    !stamp.is_empty() && stamp.bytes().all(|v|v.is_ascii_digit())
}

fn prune_normal(directory:&Path)->Result<(),String> {
    if !directory.exists(){return Ok(());}
    let mut owned=Vec::new();
    for entry in fs::read_dir(directory).map_err(|e|e.to_string())? {
        let path=entry.map_err(|e|e.to_string())?.path();
        let name=path.file_name().unwrap_or_default().to_string_lossy();
        if name.len()!=32 || !name.bytes().all(|b|b.is_ascii_hexdigit()) || plain(&path).is_err() || !path.is_dir(){continue;}
        let marker=path.join("normal-session.json");
        if plain(&marker).is_err() || fs::metadata(&marker).map(|m|m.len()>65536).unwrap_or(true){continue;}
        let Some(stamp)=fs::read(&marker).ok().and_then(|bytes|serde_json::from_slice::<NormalStamp>(&bytes).ok()) else{continue;};
        if stamp.schema!=1 || stamp.owner!="CoopPlusNormalDiagnostics" || process_created(stamp.pid).map(|live|live==Some(stamp.created_filetime)).unwrap_or(true){continue;}
        let mut files=Vec::new();let mut known=true;
        for leaf in fs::read_dir(&path).map_err(|e|e.to_string())? {
            let leaf=leaf.map_err(|e|e.to_string())?.path();let name=leaf.file_name().unwrap_or_default().to_string_lossy();
            if !leaf.is_file() || plain(&leaf).is_err() || !known_normal_file(&name,stamp.pid){known=false;break;}
            files.push(leaf);
        }
        if known {owned.push((path,files));}
    }
    owned.sort_by_key(|(path,_)|fs::metadata(path).and_then(|m|m.created()).ok());
    let remove=owned.len().saturating_sub(11);
    for (path,files) in owned.into_iter().take(remove) {
        plain(&path)?;
        for file in files {plain(&file)?;fs::remove_file(file).map_err(|e|e.to_string())?;}
        fs::remove_dir(path).map_err(|e|e.to_string())?;
    }
    Ok(())
}

fn plain(path: &Path) -> Result<(), String> {
    if !path.is_absolute() { return Err("Diagnostic path is not absolute".into()); }
    for ancestor in path.ancestors() {
        if let Ok(meta) = fs::symlink_metadata(ancestor) {
            if meta.file_type().is_symlink() { return Err("Diagnostic links refused".into()); }
            #[cfg(windows)] { use std::os::windows::fs::MetadataExt; if meta.file_attributes() & 0x400 != 0 { return Err("Diagnostic reparse path refused".into()); } }
        }
    }
    Ok(())
}
fn directory() -> Result<Option<PathBuf>, String> {
    if crate::frozen_runtime::ID.is_none() { return Ok(None); }
    if let Some(root) = std::env::var_os("COOPPLUS_PRIVATE_ROOT").map(PathBuf::from) {
        plain(&root)?;
        if let Some(path) = std::env::var_os("COOPPLUS_PLAYTEST_SESSION_DIR").map(PathBuf::from) {
            let token = std::env::var("COOPPLUS_PLAYTEST_SESSION").map_err(|_| "Missing diagnostic session token")?;
            if token.len() != 32 || !token.bytes().all(|b| b.is_ascii_hexdigit()) { return Err("Invalid diagnostic session token".into()); }
            let expected = root.parent().ok_or("Missing private package parent")?.join("playtest-logs").join(token);
            if path != expected { return Err("Diagnostic session path does not match the private package".into()); }
            plain(&path)?; return Ok(Some(path));
        }
        return Ok(Some(root.join("logs/proxy")));
    }
    if let Some(root) = std::env::var_os("COOPPLUS_NORMAL_ROOT").map(PathBuf::from) {
        plain(&root)?;
        // The validated normal context never follows inherited private log vars.
        return Ok(Some(root.join("logs/proxy")));
    }
    Err("No authorized diagnostic context".into())
}
pub fn init() -> Result<(), String> {
    let Some(mut dir) = directory()? else { let _ = LOG.set(None); return Ok(()); };
    plain(&dir)?; fs::create_dir_all(&dir).map_err(|e| e.to_string())?; plain(&dir)?;
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|e| e.to_string())?.as_nanos();
    if std::env::var_os("COOPPLUS_NORMAL_ROOT").is_some() {
        prune_normal(&dir)?;
        dir=dir.join(format!("{:032x}",stamp));plain(&dir)?;fs::create_dir(&dir).map_err(|e|e.to_string())?;
        let marker=NormalStamp{schema:1,owner:"CoopPlusNormalDiagnostics".into(),pid:std::process::id(),created_filetime:process_created(std::process::id())?.ok_or("Missing current process identity")?};
        fs::write(dir.join("normal-session.json"),serde_json::to_vec(&marker).map_err(|e|e.to_string())?).map_err(|e|e.to_string())?;
    }
    let path = dir.join(format!("proxy-{}-{stamp}.jsonl", std::process::id()));
    OpenOptions::new().write(true).create_new(true).open(&path).map_err(|e| e.to_string())?;
    LOG.set(Some(Mutex::new(Log { path, bytes: 0, capped: false }))).map_err(|_| "Diagnostics already initialized")?;
    event("process-entry", &json!({"pid":std::process::id(),"createdFiletime":process_created(std::process::id()).ok().flatten(),"runtimeId":crate::frozen_runtime::ID,"exe":std::env::current_exe().ok(),"cwd":std::env::current_dir().ok(),"arguments":std::env::args().skip(1).collect::<Vec<_>>(),"privateRoot":std::env::var("COOPPLUS_PRIVATE_ROOT").ok(),"normalRoot":std::env::var("COOPPLUS_NORMAL_ROOT").ok(),"session":std::env::var("COOPPLUS_PLAYTEST_SESSION").ok()}).to_string());
    Ok(())
}
pub fn event(name: &str, message: &str) {
    let Some(Some(lock)) = LOG.get() else { return; };
    let Ok(mut log) = lock.lock() else { return; };
    if log.capped { return; }
    let message: String = message.chars().take(8192).collect();
    let line = format!("{}\n", json!({"utcUnixMs":SystemTime::now().duration_since(UNIX_EPOCH).map(|t|t.as_millis()).unwrap_or(0),"pid":std::process::id(),"event":name,"message":message}));
    if let Ok(mut file) = OpenOptions::new().append(true).open(&log.path) {
        if log.bytes + line.len() as u64 > LIMIT {
            let _ = file.write_all(b"{\"event\":\"diagnostic-limit\",\"message\":\"512KiB lifecycle log limit reached; first failure retained separately\"}\n"); log.capped = true;
        } else { let _ = file.write_all(line.as_bytes()); log.bytes += line.len() as u64; }
        let _ = file.flush();
    }
}
pub fn failure(stage: &str, message: &str) {
    event(stage, message);
    let Some(Some(lock)) = LOG.get() else { return; };
    let Ok(log) = lock.lock() else { return; };
    let path = log.path.with_extension("failure.txt");
    // Preserve the FIRST underlying failure, including before an abort/exit.
    if let Ok(mut file) = OpenOptions::new().write(true).create_new(true).open(path) {
        let text: String = format!("{stage}\n{message}\n").chars().take(16384).collect();
        let _ = file.write_all(text.as_bytes()); let _ = file.flush(); let _ = file.sync_all();
    }
}
pub fn archive_previous_log(source: &Path) {
    let Some(Some(lock)) = LOG.get() else { return; };
    let Ok(log) = lock.lock() else { return; };
    let Some(dir) = log.path.parent() else { return; };
    if !source.is_file() || plain(source).is_err() { return; }
    let name = source.file_name().unwrap_or_default().to_string_lossy();
    let target = dir.join(format!("prior-{}-{name}", std::process::id()));
    if let (Ok(mut input), Ok(mut output)) = (fs::File::open(source), OpenOptions::new().create_new(true).write(true).open(target)) {
        if let Ok(meta) = input.metadata() {
            if meta.len() > LIMIT {
                let _ = output.write_all(b"[Prior log exceeds512KiB; this bounded copy retains its final512KiB below.]\n");
                let _ = input.seek(SeekFrom::End(-(LIMIT as i64)));
            }
            let _ = std::io::copy(&mut input.take(LIMIT), &mut output); let _ = output.flush();
        }
    }
}
pub fn report_normal_failure(message: &str) {
    if std::env::var_os("COOPPLUS_PRIVATE_ROOT").is_some() { return; } // guardian owns visible private failure
    #[cfg(windows)] unsafe {
        #[link(name="user32")] unsafe extern "system" { fn MessageBoxW(hwnd:*mut std::ffi::c_void,text:*const u16,title:*const u16,flags:u32)->i32; }
        let text: Vec<u16> = format!("Co-op+ stopped. No automatic retry or save reset was attempted.\n\n{message}\n\nRetained details: logs/proxy in the usual launcher folder.").encode_utf16().chain(Some(0)).collect();
        let title: Vec<u16> = "Co-op+ proxy failure".encode_utf16().chain(Some(0)).collect();
        MessageBoxW(std::ptr::null_mut(),text.as_ptr(),title.as_ptr(),0x10);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn scratch(label:&str)->PathBuf {let path=std::env::temp_dir().join(format!("coopplus-{label}-{}-{}",std::process::id(),SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));fs::create_dir(&path).unwrap();path}
    #[test]
    fn completed_normal_diagnostics_are_bounded_without_removing_unknown_or_live_owners() {
        let directory=scratch("diagnostic-retention");
        for index in 0..14 {
            let path=directory.join(format!("{index:032x}"));fs::create_dir(&path).unwrap();
            let marker=NormalStamp {schema:1,owner:"CoopPlusNormalDiagnostics".into(),pid:0,created_filetime:1};
            fs::write(path.join("normal-session.json"),serde_json::to_vec(&marker).unwrap()).unwrap();
            fs::write(path.join("proxy-0-123.jsonl"),"retained bounded log").unwrap();
        }
        let unknown=directory.join(format!("{:032x}",99));fs::create_dir(&unknown).unwrap();fs::write(unknown.join("keep-world.bit"),"unknown sentinel").unwrap();
        let live=directory.join(format!("{:032x}",100));fs::create_dir(&live).unwrap();
        let marker=NormalStamp{schema:1,owner:"CoopPlusNormalDiagnostics".into(),pid:std::process::id(),created_filetime:process_created(std::process::id()).unwrap().unwrap()};fs::write(live.join("normal-session.json"),serde_json::to_vec(&marker).unwrap()).unwrap();
        prune_normal(&directory).unwrap();
        assert_eq!(fs::read_dir(&directory).unwrap().count(),13);
        assert_eq!(fs::read_to_string(unknown.join("keep-world.bit")).unwrap(),"unknown sentinel");assert!(live.is_dir());
    }
    #[test]
    fn synchronous_log_cap_still_preserves_the_first_underlying_failure() {
        let directory=scratch("diagnostic-cap");let path=directory.join("proxy-fixture.jsonl");fs::write(&path,"").unwrap();
        LOG.set(Some(Mutex::new(Log{path:path.clone(),bytes:0,capped:false}))).ok().unwrap();
        for _ in 0..100 {event("bounded-fixture",&"x".repeat(8192));}
        failure("first-cause","first retained underlying failure");failure("later-cause","must not replace first cause");
        assert!(fs::metadata(&path).unwrap().len()<=LIMIT+256);
        assert_eq!(fs::read_to_string(path.with_extension("failure.txt")).unwrap(),"first-cause\nfirst retained underlying failure\n");
    }
}
