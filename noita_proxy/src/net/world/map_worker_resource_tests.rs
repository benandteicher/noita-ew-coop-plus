//! Actual map worker lifetime and output controls; no native game or live saves.
use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::mpsc::RecvTimeoutError;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

static ACTIVE: AtomicUsize = AtomicUsize::new(0);
pub(super) struct WorkerLifetime;
impl WorkerLifetime {
    pub(super) fn enter() -> Self { ACTIVE.fetch_add(1,Ordering::SeqCst);Self }
}
impl Drop for WorkerLifetime { fn drop(&mut self){ACTIVE.fetch_sub(1,Ordering::SeqCst);} }

fn save(label:&str)->SaveState {
    let root=std::path::PathBuf::from(r"C:\Projects\Noita-EW-Coop-Plus\target\network-resource-20261110\map-worker-fixtures");
    std::fs::create_dir_all(&root).unwrap();let root=root.canonicalize().unwrap();
    let path=root.join(format!("{label}-{}-{}",std::process::id(),SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
    assert_eq!(path.parent(),Some(root.as_path()));assert!(!path.exists());SaveState::new(path)
}
fn wait_for(mut condition:impl FnMut()->bool,timeout:Duration)->bool {
    let end=Instant::now()+timeout;
    while Instant::now()<end {if condition(){return true}thread::sleep(Duration::from_millis(2));}
    condition()
}

#[test]
fn map_worker_repeated_default_lifetimes_release_background_owner() {
    let before=ACTIVE.load(Ordering::SeqCst);assert_eq!(before,0,"serialize worker resource tests");
    let mut rows=Vec::new();
    for cycle in 0..12 {
        let (world,images,forwarded,materials,extra)=WorldManager::new(true,OmniPeerId(0),save("idle"));
        assert!(wait_for(||ACTIVE.load(Ordering::SeqCst)>before,Duration::from_secs(2)));
        // Same always-created worker as default map-disabled sessions, without
        // introducing any rendering work or a map material configuration.
        drop(world);drop(images);drop(forwarded);drop(materials);drop(extra);
        let released=wait_for(||ACTIVE.load(Ordering::SeqCst)==before,Duration::from_millis(100));
        rows.push((cycle,released,ACTIVE.load(Ordering::SeqCst)));
    }
    for row in &rows {eprintln!("MAP_LIFETIME cycle={} released={} active_workers={}",row.0,row.1,row.2);}
    assert!(wait_for(||ACTIVE.load(Ordering::SeqCst)==before,Duration::from_millis(200)),"closed lobby inputs retained map workers: {rows:?}");
}

#[test]
fn map_worker_late_materials_and_final_fifo_outputs_survive_producer_close() {
    assert_eq!(ACTIVE.load(Ordering::SeqCst),0,"serialize worker resource tests");
    let (world,images,forwarded,materials,extra)=WorldManager::new(true,OmniPeerId(0),save("late"));
    let chunks=[(ChunkCoord(1,-2),ChunkData::new(6)),(ChunkCoord(2,-2),ChunkData::new(7))];
    for (coord,data)in &chunks {world.tx.send((*coord,data.clone())).unwrap();}
    // Host forwarding is independent from rendering/material availability.
    for (coord,data)in &chunks {
        let (actual_coord,actual)=forwarded.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(actual_coord,*coord);assert_eq!(bitcode::encode(&actual),bitcode::encode(data));
    }
    drop(world);drop(extra);
    thread::sleep(Duration::from_millis(40));
    assert_eq!(ACTIVE.load(Ordering::SeqCst),1,"a still-live material producer must retain render obligations");
    assert!(matches!(images.try_recv(),Err(mpsc::TryRecvError::Empty)));
    let metadata=FxHashMap::from_iter([(6,0xff0000ff),(7,0x00ff00ff)]);
    let expected:Vec<_>=chunks.iter().map(|(_,data)|create_image(data.clone(),&metadata)).collect();
    materials.send(metadata).unwrap();drop(materials);
    for ((coord,_),expected)in chunks.iter().zip(expected) {
        let (actual_coord,image)=images.recv_timeout(Duration::from_secs(2)).unwrap();
        assert_eq!(actual_coord,*coord);assert_eq!(image,expected);
    }
    assert!(matches!(images.recv_timeout(Duration::from_millis(250)),Err(RecvTimeoutError::Disconnected)),"drained worker output stayed open after all producers closed");
    assert!(matches!(forwarded.recv_timeout(Duration::from_millis(250)),Err(RecvTimeoutError::Disconnected)));
    assert!(wait_for(||ACTIVE.load(Ordering::SeqCst)==0,Duration::from_millis(250)));
    eprintln!("MAP_LATE_METADATA forwarded=2 rendered=2 exact_fifo=true worker_released=true");
}
