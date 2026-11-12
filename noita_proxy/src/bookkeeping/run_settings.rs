//! Run rules are separate from editable next-run preferences. No .bit format is
//! changed, and an old checkpoint without this sidecar stays legacy-unverified.
use std::{fs,io::{self,BufReader,Read},path::Path};
use bitcode::{Decode,Encode};
use serde::{Deserialize,Serialize};
use crate::{GameSettings,game_settings::{GameMode,LocalHealthMode},DefaultSettings};

pub const FILENAME:&str="run_settings_v1.ron";
#[derive(Debug,Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SavedRunSettings {
    pub schema:u32,pub settings:GameSettings,pub run_id:u64,pub rules_id:u64,
    pub world_session:String,pub legacy_unverified:bool,
}
impl SavedRunSettings {
    pub fn load(folder:&Path,seed:u64)->io::Result<Option<Self>> {
        let path=folder.join(FILENAME);crate::frozen_runtime::assert_safe(&path);
        let file=match fs::File::open(&path){Ok(file)=>file,Err(e)if e.kind()==io::ErrorKind::NotFound=>return Ok(None),Err(e)=>return Err(e)};
        if file.metadata()?.len()>1024*1024{return Err(io::Error::other("Run settings sidecar is too large; preserved without reset"));}
        let mut data=String::new();file.take(1024*1024+1).read_to_string(&mut data)?;
        if data.len()>1024*1024{return Err(io::Error::other("Run settings sidecar is too large; preserved without reset"));}
        let saved:Self=ron::from_str(&data).map_err(|e|io::Error::new(io::ErrorKind::InvalidData,format!("Corrupt run settings sidecar; preserved: {e}")))?;
        if saved.schema!=1 || saved.settings.seed!=seed || saved.world_session.is_empty() || saved.run_id==0 || saved.rules_id==0{return Err(io::Error::other("Run settings identity/seed does not match the saved checkpoint; preserved without reset"));}
        Ok(Some(saved))
    }
    pub fn save(&self,folder:&Path)->io::Result<()> {
        let text=ron::to_string(self).map_err(|e|io::Error::other(e.to_string()))?;
        super::settings::atomic_write(&folder.join(FILENAME),text.as_bytes())?;
        crate::startup_diagnostics::event("run-settings-persisted",&format!("mode={} run_id={:016x} rules_id={:016x} local_session={} legacy_unverified={}",self.settings.game_mode.unwrap_or(DefaultSettings::default().game_mode),self.run_id,self.rules_id,self.world_session,self.legacy_unverified));Ok(())
    }
}
#[derive(Debug,Clone,Decode,Encode)]
pub struct RuleAdvertisement {pub settings:GameSettings,pub run_id:u64,pub rules_id:u64,pub locked:bool,pub ready:bool,pub legacy_unverified:bool}

#[derive(Debug,Clone,Copy,PartialEq,Eq)]
pub enum RunIntent {New,Continue,Legacy,Join}
impl RunIntent {pub fn as_str(self)->&'static str{match self{Self::New=>"new",Self::Continue=>"continue",Self::Legacy=>"legacy",Self::Join=>"join"}}}
#[derive(Debug,Clone)]
pub struct SessionRules {
    pub run_id:u64,pub rules_id:u64,pub locked:bool,pub legacy_unverified:bool,
    pub intent:RunIntent,pub expected_session:Option<String>,pub verified_session:Option<String>,
    pub saved_session:Option<String>,pub launch_prepared:bool,pub game_received:bool,pub failure:Option<String>,
}
fn token()->u64 {rand::random::<u64>().max(1)}
impl Default for SessionRules {fn default()->Self{Self{run_id:token(),rules_id:token(),locked:false,legacy_unverified:false,intent:RunIntent::New,expected_session:None,verified_session:None,saved_session:None,launch_prepared:false,game_received:false,failure:None}}}
impl SessionRules {
    pub fn from_saved(saved:&SavedRunSettings)->Self {Self {run_id:saved.run_id,rules_id:saved.rules_id,locked:true,legacy_unverified:saved.legacy_unverified,intent:if saved.legacy_unverified{RunIntent::Legacy}else{RunIntent::Continue},verified_session:if saved.legacy_unverified{None}else{Some(saved.world_session.clone())},saved_session:Some(saved.world_session.clone()),..Default::default()}}
    pub fn legacy()->Self {Self {locked:true,legacy_unverified:true,intent:RunIntent::Legacy,..Default::default()}}
    pub fn refresh_draft(&mut self){if !self.locked{self.rules_id=token();}}
    pub fn prepare_stopped_identity(&mut self,expected:Option<String>,host:bool)->io::Result<()> {
        if host && matches!(self.intent,RunIntent::Continue|RunIntent::Legacy) && expected.is_none(){return Err(io::Error::other("Proxy Continue has no corresponding stopped Noita world identity. No files were reset; preserve the checkpoint."));}
        if self.saved_session.is_some() && self.saved_session!=expected{return Err(io::Error::other("Stopped Noita world identity differs from the saved proxy run settings. No files were reset."));}
        self.expected_session=expected;self.launch_prepared=true;self.game_received=false;Ok(())
    }
    pub fn accept_receipt(&mut self,receipt:&WorldReceipt,settings:&GameSettings,host:bool)->io::Result<()> {
        if let Some(error)=&self.failure{return Err(io::Error::other(format!("World agreement failure is already latched: {error}")));}
        let expected_mode=settings.game_mode.unwrap_or(DefaultSettings::default().game_mode);
        if receipt.mode!=expected_mode || receipt.no_notplayer!=(expected_mode==GameMode::LocalHealth(LocalHealthMode::Alternate)) {return Err(io::Error::other("Game-received health mode differs from authoritative host settings; no run was reset"));}
        if self.game_received && self.saved_session.as_deref()!=Some(receipt.session.as_str()){return Err(io::Error::other("Actual world identity changed after this connection already agreed; checkpoint preserved"));}
        if self.game_received && self.saved_session.as_deref()==Some(receipt.session.as_str()) &&
            (self.legacy_unverified && receipt.kind=="legacy" && receipt.run_id.is_none() && receipt.rules_id.is_none() || !self.legacy_unverified && receipt.run_id==Some(self.run_id) && receipt.rules_id==Some(self.rules_id) && matches!(receipt.kind.as_str(),"new"|"continue")) {return Ok(());}
        let same=self.expected_session.as_deref()==Some(receipt.session.as_str());
        match self.intent {
            RunIntent::New if receipt.kind=="new" && !same && receipt.run_id==Some(self.run_id) && receipt.rules_id==Some(self.rules_id)=>{},
            RunIntent::Continue if receipt.kind=="continue" && same && self.verified_session.as_deref()==Some(receipt.session.as_str()) && receipt.run_id==Some(self.run_id) && receipt.rules_id==Some(self.rules_id)=>{},
            RunIntent::Legacy if receipt.kind=="legacy" && (same || (!host && self.expected_session.is_none())) && receipt.run_id.is_none() && receipt.rules_id.is_none()=>{},
            RunIntent::Join if (receipt.kind=="new" && !same || receipt.kind=="continue" && same) && receipt.run_id==Some(self.run_id) && receipt.rules_id==Some(self.rules_id)=>{},
            _=>return Err(io::Error::other("Actual Noita world does not match proxy New/Continue intent or saved run/rules IDs. Existing proxy data was preserved; do not reset or mix checkpoints.")),
        }
        self.game_received=true;
        self.saved_session=Some(receipt.session.clone());
        if !self.legacy_unverified {self.verified_session=Some(receipt.session.clone());}
        Ok(())
    }
}
#[derive(Debug)]
pub struct WorldReceipt {pub session:String,pub kind:String,pub run_id:Option<u64>,pub rules_id:Option<u64>,pub mode:GameMode,pub no_notplayer:bool,pub boundary:String}
pub fn hex_text(text:&str)->String {text.as_bytes().iter().map(|v|format!("{v:02x}")).collect()}
fn unhex(text:&str)->io::Result<String> {
    if text.len()>4096 || text.len()%2!=0 || !text.is_ascii(){return Err(io::Error::other("Invalid world-session receipt encoding"));}
    let bytes=(0..text.len()).step_by(2).map(|i|u8::from_str_radix(&text[i..i+2],16).map_err(|_|io::Error::other("Invalid world-session receipt encoding"))).collect::<io::Result<Vec<_>>>()?;
    String::from_utf8(bytes).map_err(|_|io::Error::other("World-session receipt is not UTF-8"))
}
impl WorldReceipt {pub fn parse(text:&str)->io::Result<Self>{
    let fields:Vec<_>=text.split_ascii_whitespace().collect();
    if fields.len()!=8 || fields[0]!="1"{return Err(io::Error::other("Incomplete world/rules receipt"));}
    let id=|s:&str|if s=="none"{Ok(None)}else if s.len()==16{u64::from_str_radix(s,16).map(Some).map_err(|_|io::Error::other("Invalid rules receipt ID"))}else{Err(io::Error::other("Invalid rules receipt ID"))};
    // Field seven is a boundary name for useful evidence, never a source of
    // identity. The actual session must come from WorldStateComponent.
    if fields[7]!="world" && fields[7]!="player"{return Err(io::Error::other("World receipt lacks an actual boundary"));}
    let session=unhex(fields[1])?;if session.is_empty(){return Err(io::Error::other("Empty actual world-session identity"));}
    Ok(Self {session,kind:fields[2].into(),run_id:id(fields[3])?,rules_id:id(fields[4])?,mode:fields[5].parse().map_err(|_|io::Error::other("Invalid received game mode"))?,no_notplayer:match fields[6]{"0"=>false,"1"=>true,_=>return Err(io::Error::other("Invalid received health-mode flags"))},boundary:fields[7].into()})
}}
pub fn stopped_world_session(profile:&Path)->io::Result<Option<String>> {
    let path=profile.join("save00/world_state.xml");crate::frozen_runtime::assert_safe(&path);
    let file=match fs::File::open(&path){Ok(file)=>file,Err(e)if e.kind()==io::ErrorKind::NotFound=>return Ok(None),Err(e)=>return Err(e)};
    let mut reader=quick_xml::Reader::from_reader(BufReader::new(file));let mut buffer=Vec::new();let mut found=None;
    loop {match reader.read_event_into(&mut buffer).map_err(|e|io::Error::other(format!("Cannot validate stopped Noita world identity: {e}")))? {
        quick_xml::events::Event::Start(tag)|quick_xml::events::Event::Empty(tag) if tag.name().as_ref()==b"WorldStateComponent"=>{
            for attribute in tag.attributes(){let attribute=attribute.map_err(|e|io::Error::other(e.to_string()))?;if attribute.key.as_ref()==b"session_stat_file"{let value=attribute.decode_and_unescape_value(reader.decoder()).map_err(|e|io::Error::other(e.to_string()))?.into_owned();if value.is_empty() || found.replace(value).is_some(){return Err(io::Error::other("Ambiguous/empty stopped Noita world identity"));}}}
        },quick_xml::events::Event::DocType(_)=>return Err(io::Error::other("Unsupported Noita world XML declaration")),quick_xml::events::Event::Eof=>break,_=>{}}
        buffer.clear();
    }
    found.map(Some).ok_or_else(||io::Error::other("Stopped Noita world lacks session_stat_file; identity was not guessed"))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn settings()->GameSettings{GameSettings {game_mode:Some(GameMode::LocalHealth(LocalHealthMode::Alternate)),seed:23,..Default::default()}}
    fn receipt(session:&str,kind:&str,run:Option<u64>,rules:Option<u64>)->WorldReceipt{WorldReceipt{session:session.into(),kind:kind.into(),run_id:run,rules_id:rules,mode:GameMode::LocalHealth(LocalHealthMode::Alternate),no_notplayer:true,boundary:"world".into()}}
    #[test]fn actual_new_world_required_before_new_intent_agrees(){let mut session=SessionRules::default();session.expected_session=Some("old".into());let mut wrong=receipt("old","new",Some(session.run_id),Some(session.rules_id));assert!(session.accept_receipt(&wrong,&settings(),true).is_err());wrong.session="actual-new".into();session.accept_receipt(&wrong,&settings(),true).unwrap();assert!(session.game_received);}
    #[test]fn legacy_never_becomes_verified_or_accepts_new_world_as_continue(){let mut session=SessionRules::legacy();session.expected_session=Some("old".into());session.accept_receipt(&receipt("old","legacy",None,None),&settings(),true).unwrap();assert!(session.legacy_unverified && session.verified_session.is_none());assert!(session.accept_receipt(&receipt("new","legacy",None,None),&settings(),true).is_err());}
    #[test]fn mode_receipt_detects_local_normal_alternate_disagreement(){let mut session=SessionRules::default();let mut got=receipt("new","new",Some(session.run_id),Some(session.rules_id));got.no_notplayer=false;assert!(session.accept_receipt(&got,&settings(),true).is_err());}
    #[test]fn fresh_client_world_can_join_host_shared_rules(){let mut session=SessionRules{intent:RunIntent::Join,locked:true,expected_session:Some("previous-character".into()),..Default::default()};session.accept_receipt(&receipt("new-character","new",Some(session.run_id),Some(session.rules_id)),&settings(),false).unwrap();}
    #[test]fn legacy_stopped_identity_policy_distinguishes_host_fresh_and_retained_client(){
        let mut host=SessionRules::legacy();assert!(host.prepare_stopped_identity(None,true).is_err());host.prepare_stopped_identity(Some("host-saved-world".into()),true).unwrap();
        let mut fresh_client=SessionRules::legacy();fresh_client.prepare_stopped_identity(None,false).unwrap();fresh_client.accept_receipt(&receipt("new-client-character","legacy",None,None),&settings(),false).unwrap();assert!(fresh_client.legacy_unverified && fresh_client.verified_session.is_none());
        let saved=SavedRunSettings {schema:1,settings:settings(),run_id:1,rules_id:2,world_session:"retained-client-world".into(),legacy_unverified:true};
        let mut retained=SessionRules::from_saved(&saved);assert!(retained.prepare_stopped_identity(None,false).is_err());assert!(retained.prepare_stopped_identity(Some("different".into()),false).is_err());retained.prepare_stopped_identity(Some("retained-client-world".into()),false).unwrap();
        let mut continued=SessionRules::from_saved(&SavedRunSettings {legacy_unverified:false,..saved});assert!(continued.prepare_stopped_identity(None,true).is_err());
    }
    #[test]fn legacy_client_receipt_allows_only_fresh_absence_or_same_retained_world(){
        let mut fresh=SessionRules::legacy();fresh.prepare_stopped_identity(None,false).unwrap();fresh.accept_receipt(&receipt("fresh-character","legacy",None,None),&settings(),false).unwrap();assert!(fresh.legacy_unverified && fresh.verified_session.is_none());
        let mut retained=SessionRules::legacy();retained.prepare_stopped_identity(Some("retained-world".into()),false).unwrap();
        assert!(retained.accept_receipt(&receipt("different-new-world","legacy",None,None),&settings(),false).is_err());assert!(!retained.game_received);
        retained.accept_receipt(&receipt("retained-world","legacy",None,None),&settings(),false).unwrap();assert!(retained.legacy_unverified && retained.verified_session.is_none());
    }
    #[test]fn receipt_parser_uses_actual_hex_identity_and_boundary(){let text=format!("1 {} new 0000000000000001 0000000000000002 LocalAlternate 1 world",hex_text("actual Ω session"));let got=WorldReceipt::parse(&text).unwrap();assert_eq!(got.session,"actual Ω session");assert!(WorldReceipt::parse("1 none new 1 2 LocalAlternate 1 guessed").is_err());}
    #[test]fn saved_rules_roundtrip_and_seed_mismatch_are_strict(){let folder=std::env::temp_dir().join(format!("coopplus-run-settings-{}-{}",std::process::id(),token()));fs::create_dir_all(&folder).unwrap();let value=SavedRunSettings{schema:1,settings:settings(),run_id:1,rules_id:2,world_session:"actual".into(),legacy_unverified:false};value.save(&folder).unwrap();assert_eq!(SavedRunSettings::load(&folder,23).unwrap().unwrap().settings,value.settings);assert!(SavedRunSettings::load(&folder,24).is_err());fs::write(folder.join(FILENAME),"corrupt").unwrap();assert!(SavedRunSettings::load(&folder,23).is_err());}
    #[test]fn stopped_world_identity_is_read_not_synthesized(){let folder=std::env::temp_dir().join(format!("coopplus-world-identity-{}-{}",std::process::id(),token()));fs::create_dir_all(folder.join("save00")).unwrap();assert!(stopped_world_session(&folder).unwrap().is_none());fs::write(folder.join("save00/world_state.xml"),"<Entity><WorldStateComponent session_stat_file=\"actual&amp;one\"/></Entity>").unwrap();assert_eq!(stopped_world_session(&folder).unwrap(),Some("actual&one".into()));fs::write(folder.join("save00/world_state.xml"),"<Entity/>").unwrap();assert!(stopped_world_session(&folder).is_err());}
}
