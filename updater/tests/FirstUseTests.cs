// Focused disposable first-use lifecycle tests; no game/proxy is launched.
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using CoopPlus;
using CoopPlusBackup;

class FirstUseTests {
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=64*1024*1024};
    static string Base;static int Passed;
    sealed class Fixture {public string root,game,settings,state,backup,profile;}
    static void Assert(bool value,string message){if(!value)throw new Exception(message);}
    static void Write(string path,string text){Directory.CreateDirectory(Path.GetDirectoryName(path));File.WriteAllText(path,text,new UTF8Encoding(false));}
    static void Refuse(Action action,string text){bool refused=false;try{action();}catch(Exception e){refused=true;Assert(e.ToString().IndexOf(text,StringComparison.OrdinalIgnoreCase)>=0,e.ToString());}Assert(refused,"Expected refusal: "+text);}
    static string Hash(byte[] bytes){using(var sha=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
    static void Case(string name,Action body){body();Passed++;Console.WriteLine("PASS "+name);}
    static string Tree(string folder){return Directory.Exists(folder)?String.Join("\n",Directory.GetFiles(folder,"*",SearchOption.AllDirectories).OrderBy(x=>x).Select(path=>path.Substring(folder.Length)+":"+Engine.Sha256(path))):"ABSENT";}
    static AutomaticBackup.FirstUseProof Proof(Fixture f){return Json.Deserialize<AutomaticBackup.FirstUseProof>(File.ReadAllText(Path.Combine(f.root,"first-use.json")));}
    static Manifest Manifest(string backup){return Json.Deserialize<Manifest>(File.ReadAllText(Path.Combine(backup,"backup.json")));}
    internal static void Prepare(string root,string game,string settings,string state,string backup) {
        if(File.Exists(Path.Combine(root,"coopplus.json")))throw new IOException("Prepare helper requires a genuinely new launcher configuration.");
        var previousSelect=Setup.Select;var previousDiscover=Setup.Discover;
        try {
            Setup.Select=request=>{request.Config.gameDir=game;request.Config.automaticBackupPath=backup;return new SetupSelection {Config=request.Config};};
            Setup.Discover=(r,s)=>new List<InstallationCandidate>{new InstallationCandidate {GameDirectory=game,Source="Disposable source-linked setup selection"}};
            // The old-safe location sidecar is launcher metadata, never fake
            // proxy settings/state. It survives old Setup's field rewrite.
            Write(Path.Combine(root,"automatic-backup.json"),Json.Serialize(new AutomaticBackup.BackupLocation {directory=backup}));
            Setup.Load(root,false,new[]{"--settings-path",settings,"--save-state-path",state});
            Assert(File.Exists(Path.Combine(root,"first-use.json")),"New Setup did not capture first-use proof");
            Assert(!File.Exists(settings) && !Directory.Exists(state),"Prepare created proxy source state");
        } finally {Setup.Select=previousSelect;Setup.Discover=previousDiscover;}
    }
    static Fixture New(string name,string layout,bool prepare=true) {
        string folder=Path.Combine(Base,name);var f=new Fixture {root=Path.Combine(folder,"Launcher Ω & new"),game=Path.Combine(folder,"Noita 漢 game"),settings=Path.Combine(folder,"Proxy Ω","config","proxy.ron"),state=Path.Combine(folder,"Proxy Ω","data","save_state"),backup=Path.Combine(folder,"Backups Ω")};
        Directory.CreateDirectory(f.root);Directory.CreateDirectory(f.game);Write(Path.Combine(f.root,"CoopPlus.exe"),"NOT A BOOTSTRAP");Write(Path.Combine(f.game,"noita.exe"),"NOT A GAME");
        Write(Path.Combine(f.root,"current","updater","CoopUpdater.exe"),"bootstrap updater, not an established release");
        Environment.SetEnvironmentVariable("USERPROFILE",Path.Combine(folder,"Scoped user O'Neil & Ω"));
        f.profile=Path.Combine(Environment.GetEnvironmentVariable("USERPROFILE"),"AppData","LocalLow","Nolla_Games_Noita");
        if(layout=="complete") {Write(Path.Combine(f.profile,"save00","world.xml"),"original stopped world marker");Write(Path.Combine(f.profile,"save00","mod_config.xml"),"<Mods><Mod name=\"quant.ew\" enabled=\"0\" settings_fold_open=\"0\" workshop_item_id=\"0\" /></Mods>");Write(Path.Combine(f.profile,"save_shared","config.xml"),"<Config fullscreen=\"0\" />");}
        else if(layout=="empty")Directory.CreateDirectory(f.profile);
        else if(layout=="shared-only")Write(Path.Combine(f.profile,"save_shared","config.xml"),"<Config fullscreen=\"0\" />");
        if(prepare)Prepare(f.root,f.game,f.settings,f.state,f.backup);
        return f;
    }
    static void Install(Fixture f,long build) {
        string folder=Path.GetDirectoryName(f.root),package=Path.Combine(folder,"payload-"+build+".zip");var entries=new Dictionary<string,byte[]>();
        foreach(string path in new[]{"mod/quant.ew/mod.xml","mod/quant.ew/init.lua","mod/quant.ew/ewext.dll","mod/quant.ew/NoitaPatcher/noitapatcher.dll","mod/quant.ew/NoitaPatcher/noitapatcher/nsew/nsew_native.dll","proxy/noita_proxy.exe","proxy/steam_api64.dll","updater/CoopUpdater.exe"})entries[path]=Encoding.UTF8.GetBytes("first-use fixture "+build+" "+path);
        using(var zip=ZipFile.Open(package,ZipArchiveMode.Create))foreach(var pair in entries)using(var output=zip.CreateEntry(pair.Key).Open())output.Write(pair.Value,0,pair.Value.Length);
        var release=new ReleaseManifest {schema=1,channel="stable",version="1.9.19.0",build=build,minBootstrap=1,sourceRevision=new string('b',40),officialProxyVersion="1.6.8",package=new PackageInfo {name="payload.zip",url="https://example.invalid/payload.zip",size=new FileInfo(package).Length,sha256=Engine.Sha256(package)},files=entries.Select(pair=>new FileInfoEntry {path=pair.Key,size=pair.Value.Length,sha256=Hash(pair.Value)}).ToArray()};
        Engine.Install(f.root,f.game,package,release);Setup.MarkInstalled(f.root);
    }
    static string Check(Fixture f){return AutomaticBackup.Ensure(f.root,f.game,new string[0]);}
    static string LaunchPreparation(Fixture f){return AutomaticBackup.PrepareLaunch(f.root,f.game,new string[0]);}
    static int Main(string[] args) {
        string originalUser=Environment.GetEnvironmentVariable("USERPROFILE");
        try {
            Engine.Progress=_=>{};AutomaticBackup.Status=_=>{};
            if(args.Length==6 && args[0]=="--prepare-first-use") {Prepare(Path.GetFullPath(args[1]),Path.GetFullPath(args[2]),Path.GetFullPath(args[3]),Path.GetFullPath(args[4]),Path.GetFullPath(args[5]));Console.WriteLine("Genuine new Setup captured first-use proof; settings/state remain absent.");return 0;}
            Base=Path.GetFullPath(args[0]);Assert(!Directory.Exists(Base),"Choose a fresh fixture path");Directory.CreateDirectory(Base);
            foreach(string layout in new[]{"complete","empty","missing","shared-only"})Case("fresh "+layout+" profile backup/check-only/consume",()=>{
                var f=New(layout,layout);Install(f,9);string before=Tree(f.profile),proofHash=Engine.Sha256(Path.Combine(f.root,"first-use.json"));
                string backup=Check(f);var manifest=Manifest(backup);
                Assert(manifest.selection.neverInitialized && manifest.files.Any(e=>e.path=="proxy-state" && e.absent) && manifest.files.Any(e=>e.path=="proxy-settings/proxy.ron" && e.absent),"genuine proxy absences not recorded");
                if(layout=="missing")Assert(manifest.files.Any(e=>e.path=="noita-profile" && e.absent),"Noita profile absence not recorded");
                Assert(!Directory.Exists(Path.Combine(backup,"data","proxy-state")),"fabricated proxy state in backup");
                Assert(Check(f)==backup && Tree(f.profile)==before && Proof(f).phase=="prepared" && Engine.Sha256(Path.Combine(f.root,"first-use.json"))==proofHash,"check-only mutated/consumed source state");
                Assert(!File.Exists(f.settings) && !Directory.Exists(f.state),"check-only initialized proxy data");
                Assert(LaunchPreparation(f)==backup && Proof(f).phase=="launch-attempted","launch did not consume original proof");
                Assert(File.Exists(f.settings) && File.Exists(Path.Combine(f.profile,"save00","mod_config.xml")) && File.Exists(Path.Combine(f.profile,"save_shared","config.xml")),"minimal preferences not initialized");
                Assert(RonSelection.Profile(File.ReadAllText(f.settings))==f.profile,"generated RON did not preserve apostrophe/ampersand/Unicode path");
                var modXml=new System.Xml.XmlDocument();modXml.Load(Path.Combine(f.profile,"save00","mod_config.xml"));var mod=(System.Xml.XmlElement)modXml.DocumentElement.SelectSingleNode("Mod");Assert(mod!=null && mod.HasAttribute("settings_fold_open") && mod.HasAttribute("workshop_item_id"),"generated mod preferences omit frozen proxy fields");
                Assert(!Directory.Exists(f.state),"initializer fabricated proxy save state");
                if(layout=="complete")Assert(File.ReadAllText(Path.Combine(f.profile,"save00","world.xml"))=="original stopped world marker","original run changed");
                if(layout=="complete" || layout=="shared-only")Assert(File.ReadAllText(Path.Combine(f.profile,"save_shared","config.xml"))=="<Config fullscreen=\"0\" />","existing preferences replaced");
                Refuse(()=>Check(f),"state folder is missing");
                // Fixture models the real proxy later creating its own empty
                // directory. This is not a native startup/Continue assertion.
                Directory.CreateDirectory(f.state);Assert(Check(f)==backup,"current check replaced initial snapshot");
                File.Delete(f.settings);Directory.Delete(f.state);Refuse(()=>Check(f),"already consumed");
            });
            Case("stale check-only snapshot refuses changed original Noita data",()=>{
                var f=New("stale","complete");Install(f,9);Check(f);Write(Path.Combine(f.profile,"save00","world.xml"),"external changed world");
                Refuse(()=>LaunchPreparation(f),"changed after first-use backup");Assert(!File.Exists(f.settings) && !Directory.Exists(f.state) && Proof(f).phase=="prepared","stale snapshot initialized data");
            });
            Case("established valid receipt cannot bypass missing or file-shaped Noita data",()=>{
                var f=New("established Noita missing","complete");Install(f,9);string backup=LaunchPreparation(f);Directory.CreateDirectory(f.state);Assert(Check(f)==backup,"established control failed");
                string receipt=Directory.GetFiles(Path.Combine(f.root,"automatic-backup-receipts"),"*.json").Single(),before=Engine.Sha256(receipt);
                string save00=Path.Combine(f.profile,"save00"),retained00=Path.Combine(Path.GetDirectoryName(f.root),"retained-save00");
                Directory.Move(save00,retained00);Refuse(()=>Check(f),"profile is incomplete");Directory.Move(retained00,save00);
                string shared=Path.Combine(f.profile,"save_shared"),retainedShared=Path.Combine(Path.GetDirectoryName(f.root),"retained-save_shared");
                Directory.Move(shared,retainedShared);Refuse(()=>Check(f),"profile is incomplete");Write(shared,"file-shaped Noita component");Refuse(()=>Check(f),"profile is incomplete");
                Assert(Proof(f).phase=="launch-attempted" && Engine.Sha256(receipt)==before,"established corruption regenerated proof or changed receipt");
            });
            Case("interrupted preference intent resumes exact hashes without state fabrication",()=>{
                var f=New("interrupted","missing");Install(f,9);Check(f);AutomaticBackup.FirstUseFault=point=>{if(point=="first-use-written")throw new IOException("injected first-use interruption");};
                try{Refuse(()=>LaunchPreparation(f),"injected first-use interruption");}finally{AutomaticBackup.FirstUseFault=null;}
                Assert(Proof(f).phase=="initializing" && !Directory.Exists(f.state),"interruption lost intent or fabricated state");
                LaunchPreparation(f);Assert(Proof(f).phase=="launch-attempted" && !Directory.Exists(f.state),"exact staged retry failed");
            });
            Case("corrupt interrupted preference never overwritten",()=>{
                var f=New("corrupt stage","missing");Install(f,9);Check(f);AutomaticBackup.FirstUseFault=point=>{if(point=="first-use-written")throw new IOException("injected first-use interruption");};
                try{Refuse(()=>LaunchPreparation(f),"injected first-use interruption");}finally{AutomaticBackup.FirstUseFault=null;}
                Write(f.settings,"external corrupt staged settings");Refuse(()=>LaunchPreparation(f),"staged hash");Assert(File.ReadAllText(f.settings)=="external corrupt staged settings","corrupt preference was replaced");
            });
            Case("unproved established old installation missing/corrupt sources refuses",()=>{
                var f=New("legacy missing","complete",false);Write(Path.Combine(f.root,"coopplus.json"),Json.Serialize(new UserConfig {gameDir=f.game,settingsPath=f.settings,saveStatePath=f.state,automaticBackupPath=f.backup}));Install(f,6);Install(f,9);
                Refuse(()=>Check(f),"first-use eligibility is unavailable");Assert(!File.Exists(Path.Combine(f.root,"first-use.json")),"legacy absences inferred as fresh");
                Write(f.settings,"(paths:(noita_save:Some("+AutomaticBackup.RonLiteral(f.profile)+")))");Refuse(()=>Check(f),"state folder is missing");
                Directory.CreateDirectory(f.state);Write(f.settings,"corrupt RON");Refuse(()=>Check(f),"complete RON");
            });
            Case("preference-only corrupt profile refuses before proof/config mutation",()=>{
                var f=New("corrupt prefs","shared-only",false);Write(Path.Combine(f.profile,"save_shared","config.xml"),"broken XML");Refuse(()=>Prepare(f.root,f.game,f.settings,f.state,f.backup),"preference is corrupt");Assert(!File.Exists(Path.Combine(f.root,"first-use.json")) && !File.Exists(Path.Combine(f.root,"coopplus.json")),"corrupt preference produced setup eligibility");
            });
            Case("fresh path overlap and metadata aliases refuse before proof",()=>{
                var f=New("path conflicts","empty",false);var c=new UserConfig {gameDir=f.game,settingsPath=f.settings,saveStatePath=Path.Combine(f.settings,"state"),automaticBackupPath=f.backup};
                Refuse(()=>AutomaticBackup.RecordFirstUse(f.root,c,new string[0]),"nonoverlapping");c.saveStatePath=f.state;c.settingsPath=Path.Combine(f.root,"first-use.json");Refuse(()=>AutomaticBackup.RecordFirstUse(f.root,c,new string[0]),"metadata");
                c.settingsPath=Path.Combine(f.profile,"save_shared","config.xml");Refuse(()=>AutomaticBackup.RecordFirstUse(f.root,c,new string[0]),"nonoverlapping");Assert(!File.Exists(Path.Combine(f.root,"first-use.json")),"conflict wrote proof");
            });
            Case("absent source appearing during backup fails without initialization",()=>{
                var f=New("absence changes","complete");Install(f,9);BackupEngine.Fault=point=>{if(point=="copied")Directory.CreateDirectory(f.state);};
                try{Refuse(()=>Check(f),"appeared unexpectedly");}finally{BackupEngine.Fault=null;}
                Assert(!File.Exists(f.settings) && Proof(f).phase=="prepared","failed absence snapshot initialized data");
            });
            Console.WriteLine(Passed+" first-use cases passed. Fixtures retained: "+Base);return 0;
        }catch(Exception e){Console.Error.WriteLine(e);return 1;}
        finally {Environment.SetEnvironmentVariable("USERPROFILE",originalUser);AutomaticBackup.FirstUseFault=null;BackupEngine.Fault=null;}
    }
}
