//! Focused session policy and quarantine checks. No game/Steam proxy is launched.
use super::*;
use std::fs;

fn folder()->PathBuf {let path=std::env::temp_dir().join(format!("coopplus-session-rules-{}-{}",std::process::id(),rand::random::<u64>()));fs::create_dir_all(&path).unwrap();path}
fn manager()->Arc<NetManager> {
    let folder=folder();let peer=tangled::Peer::host("127.0.0.1:0".parse().unwrap(),None).unwrap();
    NetManager::new(omni::PeerVariant::Tangled(peer),NetManagerInit {my_nickname:"session-policy-fixture".into(),save_state:SaveState::new(folder.join("state")),cosmetics:(false,false,false),paths:NetManagerPaths {noita_quantew_install:folder.join("unused-mod"),noita_quantew_player_spritesheet:folder.join("unused-player.png"),noita_save:Some(folder.join("profile"))},player_png_desc:PlayerPngDesc::default(),noita_port:0},AudioSettings{disabled:true,..Default::default()})
}
fn inner(manager:&Arc<NetManager>)->NetInnerState {
    let (world,_,_,_,_)=WorldManager::new(true,manager.peer.my_id(),manager.init_settings.save_state.clone());
    NetInnerState {ms:None,world,des:DesManager::new(true,manager.init_settings.save_state.clone()),audio:None,explosion_data:Vec::new(),had_a_disconnect:false,flags:Default::default(),local_world_ready:false,initializing_local:false,gated_outbound:Vec::new(),gated_inbound:Vec::new(),gate_bytes:0,gate_started:None,gate_error:None}
}
fn alternate()->GameSettings {GameSettings{game_mode:Some(GameMode::LocalHealth(LocalHealthMode::Alternate)),..Default::default()}}

#[tokio::test]
async fn session_rules_apply_before_first_game_then_queue_without_changing_authority() {
    let manager=manager();manager.settings.lock().unwrap().seed=152;
    assert!(manager.apply_host_preferences(&alternate()));assert_eq!(manager.settings.lock().unwrap().game_mode,alternate().game_mode);
    manager.session_rules.lock().unwrap().locked=true;
    assert!(!manager.apply_host_preferences(&GameSettings::default()));
    assert_eq!(manager.settings.lock().unwrap().game_mode,alternate().game_mode);
    assert_eq!(manager.pending_settings.lock().unwrap().game_mode,None);assert!(manager.dirty.load(Ordering::Relaxed));
    let authority=manager.settings.lock().unwrap().clone();let root=manager.init_settings.save_state.directory();
    SavedRunSettings{schema:1,settings:authority.clone(),run_id:1,rules_id:2,world_session:"actual-world".into(),legacy_unverified:false}.save(root).unwrap();
    let preferences=crate::bookkeeping::settings::Settings{app:crate::app::AppSavedState{game_settings:GameSettings::default(),..Default::default()},..Default::default()};
    preferences.save(&root.join("proxy.ron")).unwrap();
    assert_eq!(SavedRunSettings::load(root,authority.seed).unwrap().unwrap().settings.game_mode,alternate().game_mode,"queued RON preferences must not become Continue authority");
}
#[tokio::test]
async fn session_rules_mismatch_does_not_reset_old_checkpoint_or_release_quarantine() {
    let manager=manager();let mut state=inner(&manager);*manager.settings.lock().unwrap()=alternate();
    manager.session_rules.lock().unwrap().expected_session=Some("old-world".into());
    let protected=manager.init_settings.save_state.directory().join("protected-old-checkpoint.bit");fs::write(&protected,b"unchanged old checkpoint").unwrap();
    state.hold_world_message(bitcode::encode(&NoitaInbound::RawMessage(vec![1,2,3])),false);
    let session=manager.session_rules.lock().unwrap().clone();let text=format!("1 {} new {:016x} {:016x} LocalAlternate 1 world",run_settings::hex_text("old-world"),session.run_id,session.rules_id);
    let (send,_)=std::sync::mpsc::channel();assert!(manager.accept_world_receipt(&text,&mut state,&send).is_err());
    assert_eq!(fs::read(protected).unwrap(),b"unchanged old checkpoint");assert!(!state.local_world_ready && state.gated_outbound.len()==1);
}
#[tokio::test]
async fn session_rules_quarantine_preserves_fifo_and_fails_visibly_on_bound() {
    let manager=manager();let mut state=inner(&manager);
    for sequence in[7,8,9] {state.hold_world_message(bitcode::encode(&NoitaInbound::RawMessage(vec![sequence])),false);}
    let values:Vec<u8>=state.gated_outbound.iter().map(|bytes|match bitcode::decode::<NoitaInbound>(bytes).unwrap(){NoitaInbound::RawMessage(bytes)=>bytes[0],_=>panic!("wrong message")}).collect();assert_eq!(values,vec![7,8,9]);
    state.gate_bytes=MAX_WORLD_GATE_BYTES;state.hold_world_message(vec![1],true);
    assert!(state.gate_error.as_deref().unwrap().contains("startup quarantine limit"));assert!(state.gated_outbound.is_empty() && state.gated_inbound.is_empty());
}
#[tokio::test]
async fn session_rules_reconnect_gate_failure_revokes_saves_even_on_normal_shutdown() {
    let manager=manager();let checkpoint=manager.init_settings.save_state.clone();checkpoint.mark_game_started();checkpoint.save(&RunInfo{seed:82});
    let active=GameSettings{seed:82,..alternate()};*manager.settings.lock().unwrap()=active.clone();
    {let mut session=manager.session_rules.lock().unwrap();session.locked=true;session.game_received=true;session.intent=RunIntent::Continue;session.expected_session=Some("accepted-world".into());session.saved_session=Some("accepted-world".into());session.verified_session=Some("accepted-world".into());}
    let accepted=manager.session_rules.lock().unwrap().clone();
    SavedRunSettings{schema:1,settings:active,run_id:accepted.run_id,rules_id:accepted.rules_id,world_session:"accepted-world".into(),legacy_unverified:false}.save(checkpoint.directory()).unwrap();
    let flags:FxHashSet<String>=["last-good-flag".to_owned()].into_iter().collect();checkpoint.save(&flags);
    let before:Vec<_>=fs::read_dir(checkpoint.directory()).unwrap().map(|entry|{let path=entry.unwrap().path();(path.clone(),fs::read(path).unwrap())}).collect();
    manager.accept_local.store(true,Ordering::SeqCst);
    // The only production WouldBlock is the expected client host-ready wait;
    // it must remain retryable rather than becoming a failed agreement.
    manager.report_preparation_failure(&io::Error::new(io::ErrorKind::WouldBlock,"waiting for host"));
    assert!(checkpoint.game_started() && manager.session_rules.lock().unwrap().failure.is_none());
    let mut state=inner(&manager);let successful_shutdown=SessionSaveGuard {state:checkpoint.clone(),successful:true};
    state.local_world_ready=false;state.gate_bytes=MAX_WORLD_GATE_BYTES;state.hold_world_message(vec![1],true);manager.consume_gate_failure(&mut state);
    assert!(!checkpoint.game_started() && !manager.session_rules.lock().unwrap().game_received && !manager.rule_advertisement().ready && !manager.accept_local.load(Ordering::SeqCst));
    assert!(manager.error.lock().unwrap().is_some());let first=manager.session_rules.lock().unwrap().failure.clone();
    manager.fail_agreement("repeat", "later preparation error".into(),Some(&mut state));assert_eq!(manager.session_rules.lock().unwrap().failure,first,"first failure cause changed");
    let late=format!("1 {} continue {:016x} {:016x} LocalAlternate 1 player",run_settings::hex_text("accepted-world"),accepted.run_id,accepted.rules_id);let(send,_)=std::sync::mpsc::channel();
    assert!(manager.accept_world_receipt(&late,&mut state,&send).is_err(),"late otherwise-valid receipt escaped the failure latch");
    assert!(!checkpoint.game_started() && !manager.rule_advertisement().ready && !manager.session_rules.lock().unwrap().game_received);
    manager.settings.lock().unwrap().seed=99;manager.continue_running.store(false,Ordering::Relaxed);
    drop(successful_shutdown);drop(state);drop(manager);
    assert_eq!(fs::read_dir(checkpoint.directory()).unwrap().count(),before.len());for(path,bytes)in before{assert_eq!(fs::read(path).unwrap(),bytes,"normal shutdown overwrote last good checkpoint after failed reconnect agreement");}
}
#[test]
fn session_rules_failed_or_panicking_owner_cannot_save_partial_checkpoint() {
    let folder=folder();let state=SaveState::new(&folder);state.mark_game_started();state.save(&RunInfo{seed:82});let before=fs::read(folder.join("run_info_v2.bit")).unwrap();
    {let _guard=SessionSaveGuard{state:state.clone(),successful:false};}
    state.save(&RunInfo{seed:99});assert_eq!(fs::read(folder.join("run_info_v2.bit")).unwrap(),before);
    state.mark_game_started();let observed=std::panic::catch_unwind(||{let _guard=SessionSaveGuard{state:state.clone(),successful:true};panic!("controlled session failure");});assert!(observed.is_err());
    state.save(&RunInfo{seed:100});assert_eq!(fs::read(folder.join("run_info_v2.bit")).unwrap(),before);
    let new_view=state.for_new_run();assert!(new_view.load_checked::<RunInfo>().unwrap().is_none());assert_eq!(state.load_checked::<RunInfo>().unwrap().unwrap().seed,82);
}
#[tokio::test]
async fn session_rules_explicit_new_keeps_old_disk_until_agreement_then_saves_empty_memory() {
    let mut manager=manager();let old=manager.init_settings.save_state.clone();old.mark_game_started();old.save(&RunInfo{seed:13});
    let chunks:FxHashMap<ChunkCoord,ChunkData>=[(ChunkCoord(8,9),ChunkData::new(425))].into_iter().collect();old.save(&chunks);
    let flags:FxHashSet<String>=["old-run-only-flag".to_owned()].into_iter().collect();old.save(&flags);
    // A fresh view must not even decode an old DES payload while explicit New
    // remains unconfirmed. Its bytes stay recoverable until actual agreement.
    fs::write(old.directory().join("des_entity_storage.bit"),b"old-DES-sentinel").unwrap();
    let before:Vec<_>=fs::read_dir(old.directory()).unwrap().map(|entry|{let path=entry.unwrap().path();(path.clone(),fs::read(path).unwrap())}).collect();
    let fresh=old.for_new_run();assert!(fresh.load_checked::<FxHashMap<ChunkCoord,ChunkData>>().unwrap().is_none());
    Arc::get_mut(&mut manager).unwrap().init_settings.save_state=fresh;
    *manager.settings.lock().unwrap()=alternate();let mut state=inner(&manager);assert!(state.flags.is_empty());
    for(path,bytes)in &before{assert_eq!(&fs::read(path).unwrap(),bytes,"explicit New changed old files before actual receipt");}
    let session=manager.session_rules.lock().unwrap().clone();let text=format!("1 {} new {:016x} {:016x} LocalAlternate 1 world",run_settings::hex_text("actual-new-world"),session.run_id,session.rules_id);let(send,_)=std::sync::mpsc::channel();
    manager.accept_world_receipt(&text,&mut state,&send).unwrap();
    assert_eq!(manager.settings.lock().unwrap().seed,0,"boundary retirement must not call end_run or randomize the selected seed");
    assert_eq!(manager.session_rules.lock().unwrap().run_id,session.run_id);
    drop(state);
    assert!(old.load_checked::<FxHashMap<ChunkCoord,ChunkData>>().unwrap().unwrap().is_empty());
    assert!(old.load_checked::<FxHashSet<String>>().unwrap().unwrap().is_empty());
}
