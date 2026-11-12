// Source-linked disposable tests. No game/proxy executable is launched.
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using CoopPlus;
using CoopPlusBackup;

class AutomaticBackupTests {
    static string Base;
    static int Passed;
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=64*1024*1024};
    class Fixture { public string folder,root,game,state,settings,destination; }
    static void Assert(bool condition,string message){if(!condition)throw new Exception(message);}
    static string Hash(byte[] bytes){using(var sha=System.Security.Cryptography.SHA256.Create())return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-","").ToLowerInvariant();}
    static void Write(string path,string value){Directory.CreateDirectory(Path.GetDirectoryName(path));File.WriteAllText(path,value,new UTF8Encoding(false));}
    static Fixture New(string name,long build) {
        var f=new Fixture {folder=Path.Combine(Base,name)};
        f.root=Path.Combine(f.folder,"Co-op+ Ω & launcher");f.game=Path.Combine(f.folder,"Noita 漢 portable");
        f.state=Path.Combine(f.folder,"Proxy state Ω","save_state");f.settings=Path.Combine(f.folder,"Proxy state Ω","proxy.ron");f.destination=Path.Combine(f.folder,"Backups Ω");
        Directory.CreateDirectory(f.root);Directory.CreateDirectory(f.game);Directory.CreateDirectory(f.state);
        Write(Path.Combine(f.game,"noita.exe"),"NOT A GAME");Write(Path.Combine(f.root,"CoopPlus.exe"),"NOT A BOOTSTRAP");
        Write(Path.Combine(f.game,"save00","world.xml"),"original stopped run");Write(Path.Combine(f.game,"save_shared","config.xml"),"retained preferences");
        Write(Path.Combine(f.state,"world.bin"),"paired proxy state");
        Write(f.settings,"(paths:(noita_save:Some("+Json.Serialize(f.game)+")))");
        Write(Path.Combine(f.root,"coopplus.json"),Json.Serialize(new UserConfig {gameDir=f.game,settingsPath=f.settings,saveStatePath=f.state,automaticBackupPath=f.destination}));
        var entries=new Dictionary<string,byte[]>();
        foreach(string path in new[]{"mod/quant.ew/mod.xml","mod/quant.ew/init.lua","mod/quant.ew/ewext.dll","mod/quant.ew/NoitaPatcher/noitapatcher.dll","mod/quant.ew/NoitaPatcher/noitapatcher/nsew/nsew_native.dll","proxy/noita_proxy.exe","proxy/steam_api64.dll","updater/CoopUpdater.exe"})entries[path]=Encoding.UTF8.GetBytes("fixture build "+build+" "+path);
        string package=Path.Combine(f.folder,"payload.zip");
        using(var zip=ZipFile.Open(package,ZipArchiveMode.Create))foreach(var pair in entries)using(var output=zip.CreateEntry(pair.Key).Open())output.Write(pair.Value,0,pair.Value.Length);
        var release=new ReleaseManifest {schema=1,channel="stable",version="1.9.18.0",build=build,minBootstrap=1,sourceRevision=new string('a',40),officialProxyVersion="1.6.8",package=new PackageInfo {name="payload.zip",url="https://example.invalid/payload.zip",size=new FileInfo(package).Length,sha256=Engine.Sha256(package)},files=entries.Select(x=>new FileInfoEntry {path=x.Key,size=x.Value.Length,sha256=Hash(x.Value)}).ToArray()};
        Engine.Install(f.root,f.game,package,release);return f;
    }
    static string Ensure(Fixture f,params string[] args){return AutomaticBackup.Ensure(f.root,f.game,args);}
    static void Refuse(Action action,string contains){bool refused=false;try{action();}catch(Exception e){refused=true;Assert(e.ToString().IndexOf(contains,StringComparison.OrdinalIgnoreCase)>=0,e.ToString());}Assert(refused,"Expected refusal: "+contains);}
    static void Case(string name,Action test){test();Passed++;Console.WriteLine("PASS "+name);}
    static int Main(string[] a){
        try {
            Base=Path.GetFullPath(a[0]);Assert(!Directory.Exists(Base),"Choose fresh fixture output");Directory.CreateDirectory(Base);
            Engine.Progress=_=>{};AutomaticBackup.Status=_=>{};
            Case("Unicode portable full copy, retained settings and current recheck",()=>{
                var f=New("first",7);string settings=File.ReadAllText(f.settings),world=File.ReadAllText(Path.Combine(f.game,"save00","world.xml"));
                string stats=Path.Combine(f.game,"save_stats","stats-sentinel.xml");Write(stats,"retained portable statistics");
                string backup=Ensure(f);Assert(File.ReadAllText(Path.Combine(backup,"data","noita-profile","save00","world.xml"))==world,"wrong saved copy");
                Assert(File.ReadAllText(Path.Combine(backup,"data","noita-profile","save_stats","stats-sentinel.xml"))==File.ReadAllText(stats),"portable statistics missing from backup");
                Assert(!File.Exists(Path.Combine(backup,"data","noita-profile","noita.exe")),"copied game as save profile");
                string receipt=Directory.GetFiles(Path.Combine(f.root,"automatic-backup-receipts"),"*.json").Single(),receiptHash=Engine.Sha256(receipt);
                Assert(Ensure(f)==backup,"duplicate current backup");Assert(Directory.GetDirectories(f.destination,"CoopPlus-*").Length==1,"duplicate complete backup");
                Assert(Engine.Sha256(receipt)==receiptHash,"current check changed the existing statistics backup receipt");
                Assert(File.ReadAllText(stats)=="retained portable statistics","portable statistics source changed");
                Assert(File.ReadAllText(f.settings)==settings && File.ReadAllText(Path.Combine(f.game,"save00","world.xml"))==world,"source changed");
            });
            Case("forwarded effective settings and proxy state",()=>{
                var f=New("overrides",7);string settings=Path.Combine(f.folder,"Alt Ω","proxy.ron"),state=Path.Combine(f.folder,"Alt Ω","state");
                Write(settings,File.ReadAllText(f.settings));Write(Path.Combine(state,"run.bin"),"override state");
                string backup=Ensure(f,"--settings-path="+settings,"--save-state-path",state);
                Assert(File.ReadAllText(Path.Combine(backup,"data","proxy-state","run.bin"))=="override state","did not use effective state");
            });
            Case("missing and corrupt profile refuse without defaults",()=>{
                var f=New("corrupt",7);Write(f.settings,"(paths:(noita_save:Some(\"relative\")))");Refuse(()=>Ensure(f),"absolute");
                Write(f.settings,"(paths:(noita_save:Some(\"C:\\\\missing-profile\")))");Refuse(()=>Ensure(f),"does not match");
                Write(f.settings,"(paths:(noita_save:None,noita_save:None))");Refuse(()=>Ensure(f),"Ambiguous");
            });
            Case("custom launch and duplicate overrides refuse",()=>{
                var f=New("arguments",7);Refuse(()=>Ensure(f,"--launch-cmd","C:/another/noita.exe"),"custom launch");
                Refuse(()=>Ensure(f,"--settings-path",f.settings,"--settings-path="+f.settings),"Ambiguous");
            });
            Case("recognized portable command preserves effective profile",()=>{
                var f=New("portable command",7);string command="\""+Path.Combine(f.game,"noita.exe").Replace('\\','/')+"\" -always_store_userdata_in_workdir";
                string backup=Ensure(f,"--launch-cmd",command);Assert(File.Exists(Path.Combine(backup,"data","noita-profile","save00","world.xml")),"portable data omitted");
            });
            Case("low space stops with no receipt",()=>{
                var f=New("space",7);var old=BackupEngine.FreeBytes;
                try{BackupEngine.FreeBytes=_=>0;Refuse(()=>Ensure(f),"free space");}finally{BackupEngine.FreeBytes=old;}
                Assert(!Directory.Exists(Path.Combine(f.root,"automatic-backup-receipts")),"failure receipt");
            });
            Case("interruption retains incomplete then verified retry",()=>{
                var f=New("interruption",7);BackupEngine.Fault=point=>{if(point=="copy-chunk")throw new IOException("injected interruption");};
                try{Refuse(()=>Ensure(f),"injected interruption");}finally{BackupEngine.Fault=null;}
                Assert(Directory.GetDirectories(f.destination,".incomplete-*").Length==1,"incomplete removed");
                Assert(!Directory.Exists(Path.Combine(f.root,"automatic-backup-receipts")),"failure receipt");Ensure(f);
            });
            Case("copy corruption blocks completion",()=>{
                var f=New("copy corruption",7);BackupEngine.Fault=point=>{if(point=="copied")Write(Path.Combine(Directory.GetDirectories(f.destination,".incomplete-*").Single(),"data","proxy-state","world.bin"),"corrupted");};
                try{Refuse(()=>Ensure(f),"verification failed");}finally{BackupEngine.Fault=null;}
                Assert(!Directory.Exists(Path.Combine(f.root,"automatic-backup-receipts")),"failure receipt");
            });
            Case("source mutation blocks completion",()=>{
                var f=New("source mutation",7);BackupEngine.Fault=point=>{if(point=="copied")Write(Path.Combine(f.state,"late.bin"),"concurrent external edit");};
                try{Refuse(()=>Ensure(f),"Sources changed");}finally{BackupEngine.Fault=null;}
            });
            Case("tampered completed backup refuses current check",()=>{
                var f=New("tamper",7);string backup=Ensure(f);Write(Path.Combine(backup,"data","proxy-state","world.bin"),"corrupt backup");Refuse(()=>Ensure(f),"verification failed");
            });
            Case("unexpected completed files refuse current check",()=>{
                var f=New("extra",7);string backup=Ensure(f);Write(Path.Combine(backup,"data","unexpected.bin"),"extra");Refuse(()=>Ensure(f),"Unexpected entry");
            });
            Case("unsafe destination and missing state refuse",()=>{
                var f=New("unsafe",7);var c=Setup.Stored(f.root);c.automaticBackupPath=Path.Combine(f.game,"backups");Write(Path.Combine(f.root,"coopplus.json"),Json.Serialize(c));Refuse(()=>Ensure(f),"outside");
                c.automaticBackupPath=f.destination;Write(Path.Combine(f.root,"coopplus.json"),Json.Serialize(c));Directory.Move(f.state,f.state+"-absent");Refuse(()=>Ensure(f),"state folder is missing");
            });
            Case("frozen licensed engine fingerprints verify and reject corruption",()=>{
                var f=New("engine fingerprints",7);Write(Path.Combine(f.game,"data","data.wak"),"owned-engine fixture data");Write(Path.Combine(f.game,"steam_appid.txt"),"881100\n");
                var m=Engine.ReadInstalled(f.root);
                m.gameFiles=new[]{"noita.exe","data/data.wak","steam_appid.txt"}.Select(path=>new FileInfoEntry {path=path,size=new FileInfo(Path.Combine(f.game,path.Replace('/',Path.DirectorySeparatorChar))).Length,sha256=Engine.Sha256(Path.Combine(f.game,path.Replace('/',Path.DirectorySeparatorChar)))}).ToArray();
                Write(Path.Combine(f.root,"current","installed.json"),Engine.SerializeManifest(m));
                Engine.VerifyInstalled(f.root,f.game);Ensure(f);
                Write(Path.Combine(f.game,"steam_appid.txt"),"480\n");Refuse(()=>Ensure(f),"does not match this frozen");
                Write(Path.Combine(f.game,"steam_appid.txt"),"881100\n");Engine.VerifyInstalled(f.root,f.game);
                Write(Path.Combine(f.game,"data","data.wak"),"corrupt owned-engine fixture");
                Refuse(()=>Ensure(f),"does not match this frozen");
                m.gameFiles[1].path="../outside.wak";Refuse(()=>Engine.ValidateManifest(m),"frozen game runtime fingerprint");
            });
            Case("old-safe backup destination survives old config shape",()=>{
                var f=New("old config sidecar",7);
                Write(Path.Combine(f.root,"automatic-backup.json"),Json.Serialize(new AutomaticBackup.BackupLocation {directory=f.destination}));
                // Same field set serialized by the historical updater's UserConfig.
                Write(Path.Combine(f.root,"coopplus.json"),Json.Serialize(new {schema=1,gameDir=f.game,settingsPath=f.settings,saveStatePath=f.state,pendingInstall=false}));
                string backup=Ensure(f);Assert(BackupEngine.Within(backup,f.destination),"old config rewrite lost selected backup destination");
                Assert(File.Exists(Path.Combine(backup,"data","launcher","automatic-backup.json")),"sidecar not protected");
                var c=Setup.Stored(f.root);c.automaticBackupPath=Path.Combine(f.folder,"conflicting-backups");Write(Path.Combine(f.root,"coopplus.json"),Json.Serialize(c));
                Refuse(()=>Ensure(f),"location settings conflict");
            });
            Case("normal dispatch carries explicit context and clears inherited private mode",()=>{
                var f=New("normal context",7);string proxy=Path.Combine(f.root,"current","proxy"),exe=Path.Combine(proxy,"noita_proxy.exe");
                string oldPrivate=Environment.GetEnvironmentVariable("COOPPLUS_PRIVATE_ROOT"),oldNormal=Environment.GetEnvironmentVariable("COOPPLUS_NORMAL_ROOT"),oldSession=Environment.GetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION"),oldSessionDir=Environment.GetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION_DIR");
                try {
                    Environment.SetEnvironmentVariable("COOPPLUS_PRIVATE_ROOT",Path.Combine(f.folder,"unrelated private root"));
                    Environment.SetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION",new string('a',32));
                    Environment.SetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION_DIR",Path.Combine(f.folder,"unrelated private diagnostics"));
                    Environment.SetEnvironmentVariable("COOPPLUS_NORMAL_ROOT",Path.Combine(f.folder,"unrelated normal root"));
                    var args=new[]{"--settings-path",f.settings,"--save-state-path",f.state,"--exe-path",Path.Combine(f.game,"noita.exe")};
                    var start=ProxyHandoff.NormalStart(f.root,exe,proxy,args);
                    Assert(start.EnvironmentVariables["COOPPLUS_NORMAL_ROOT"]==f.root,"normal proof root was inherited instead of explicit");
                    Assert(!start.EnvironmentVariables.ContainsKey("COOPPLUS_PRIVATE_ROOT"),"private context leaked to normal child");
                    Assert(!start.EnvironmentVariables.ContainsKey("COOPPLUS_PLAYTEST_SESSION") && !start.EnvironmentVariables.ContainsKey("COOPPLUS_PLAYTEST_SESSION_DIR"),"private diagnostic context leaked to normal child");
                    Assert(start.Arguments.Contains(Program.Quote(f.settings)) && start.Arguments.Contains(Program.Quote(f.state)) && start.Arguments.Contains(Program.Quote(Path.Combine(f.game,"noita.exe"))),"explicit child storage/game arguments lost");
                    Refuse(()=>ProxyHandoff.NormalStart(null,exe,proxy,args),"absolute local paths");
                    Refuse(()=>ProxyHandoff.NormalStart(f.root,Path.Combine(f.folder,"noita_proxy.exe"),proxy,args),"does not match");
                    Refuse(()=>ProxyHandoff.NormalStart(f.root,exe,proxy,new string[0]),"requires explicit");
                    Refuse(()=>ProxyHandoff.NormalStart(f.root,exe,proxy,args.Concat(new[]{"--settings-path",f.settings}).ToArray()),"Ambiguous explicit");
                } finally {
                    Environment.SetEnvironmentVariable("COOPPLUS_PRIVATE_ROOT",oldPrivate);
                    Environment.SetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION",oldSession);
                    Environment.SetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION_DIR",oldSessionDir);
                    Environment.SetEnvironmentVariable("COOPPLUS_NORMAL_ROOT",oldNormal);
                }
            });
            Console.WriteLine(Passed+" automatic-backup cases passed. Fixtures retained: "+Base);return 0;
        }catch(Exception e){Console.Error.WriteLine(e);return 1;}
    }
}
