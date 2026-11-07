using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Reflection;
using System.Runtime.InteropServices;
using CoopPlusBackup;

internal static class BackupTests {
    static string sandbox; static int count;
    static void Assert(bool ok,string message){if(!ok)throw new Exception(message);}
    static void Expect(Action action,string part){try{action();}catch(Exception e){Assert(e.ToString().Contains(part),"Wrong failure: "+e);return;}throw new Exception("Expected failure: "+part);}
    static void Case(string name,Action test){BackupEngine.Fault=null;BackupEngine.BusyCheck=()=>{};BackupEngine.FreeBytes=p=>long.MaxValue;test();count++;Console.WriteLine("PASS "+name);}
    static string FileAt(string path,string content){Directory.CreateDirectory(Path.GetDirectoryName(path));File.WriteAllText(path,content,new UTF8Encoding(false));return path;}
    static string Root(string name) {
        string basePath=Path.Combine(sandbox,name);string root=Path.Combine(basePath,"launcher"),game=Path.Combine(basePath,"game"),profile=Path.Combine(basePath,"保存 профіль Ω"),state=Path.Combine(basePath,"portable EW","save_state");
        FileAt(Path.Combine(root,"CoopPlus.exe"),"launcher fixture");FileAt(Path.Combine(root,"current","proxy","noita_proxy.exe"),"proxy fixture");FileAt(Path.Combine(game,"noita.exe"),"game fixture");
        FileAt(Path.Combine(root,"current","installed.json"),"{\"version\":\"1.9.15.0\",\"build\":3,\"officialProxyVersion\":\"1.6.8\"}");
        FileAt(Path.Combine(profile,"save00","player.xml"),"<Entity wallet='32769'>gold sentinel Ω</Entity>");
        FileAt(Path.Combine(profile,"save_shared","config.xml"),"<Config/>");Directory.CreateDirectory(Path.Combine(profile,"empty folder"));
        FileAt(Path.Combine(state,"state.bin"),"paired state sentinel");
        string settings=FileAt(Path.Combine(basePath,"portable EW","proxy.ron"),"(app:(nickname:Some(\"(\")),paths:(noita_save:Some("+RonQuote(profile)+"),noita_exe:Some("+RonQuote(Path.Combine(game,"noita.exe"))+")))");
        FileAt(Path.Combine(root,"coopplus.json"),BackupEngine.Json.Serialize(new{schema=1,gameDir=game,settingsPath=settings,saveStatePath=state}));
        return root;
    }
    static string RonQuote(string value){return "\""+value.Replace("\\","\\\\").Replace("\"","\\\"")+"\"";}
    static string Destination(string root){return Path.Combine(Path.GetDirectoryName(root),"backups");}
    static string Run(string root){return BackupEngine.Run(root,Destination(root),CancellationToken.None,s=>{});}
    static void Main(string[] args){
        sandbox=Path.GetFullPath(args[0]);Directory.CreateDirectory(sandbox);LaunchOverrides.SkipForTests=true;
        if(args.Length>2 && args[2]=="--clients-only") {
            Case("empty proxy state requires explicit client confirmation",()=>{string root=Root("empty-strict"),state=BackupEngine.Discover(root).state;File.Delete(Path.Combine(state,"state.bin"));Expect(()=>Run(root),"Confirm that this PC joins");Assert(!Directory.Exists(Destination(root)),"unconfirmed empty state staged backup");});
            Case("confirmed existing empty client state is backed up and labeled",()=>{string root=Root("empty-confirmed"),state=BackupEngine.Discover(root).state;File.Delete(Path.Combine(state,"state.bin"));string done=BackupEngine.Run(root,Destination(root),CancellationToken.None,s=>{},true);var manifest=BackupEngine.Json.Deserialize<Manifest>(File.ReadAllText(Path.Combine(done,"backup.json")));Assert(manifest.utilityVersion=="0.1.2"&&manifest.selection.emptyProxyState&&manifest.selection.clientConfirmed,"client assertion not recorded");Assert(manifest.selection.role.Contains("joining client"),"client role missing");Assert(Directory.Exists(Path.Combine(done,"data","proxy-state"))&&!Directory.GetFileSystemEntries(Path.Combine(done,"data","proxy-state")).Any(),"empty paired state not preserved");Assert(File.ReadAllText(Path.Combine(done,"RESTORE.txt")).Contains("corresponding host backup"),"host counterpart requirement missing");});
            Case("client confirmation never substitutes a missing directory",()=>{string root=Root("missing-client"),state=BackupEngine.Discover(root).state;Directory.Move(state,state+"-unavailable");Expect(()=>BackupEngine.Run(root,Destination(root),CancellationToken.None,s=>{},true),"Required backup source is missing");Assert(!Directory.Exists(Destination(root)),"missing state staged backup");});
            Case("empty client state becoming nonempty refuses completion",()=>{string root=Root("client-changes"),state=BackupEngine.Discover(root).state;File.Delete(Path.Combine(state,"state.bin"));BackupEngine.Fault=point=>{if(point=="copied")FileAt(Path.Combine(state,"new-state.bin"),"changed");};Expect(()=>BackupEngine.Run(root,Destination(root),CancellationToken.None,s=>{},true),"Sources changed during backup");Assert(!Directory.GetDirectories(Destination(root),"CoopPlus-*").Any(),"client transition marked complete");});
            Case("nonempty state does not assert a healthy host",()=>{string root=Root("role-unspecified");var p=BackupEngine.Discover(root);Assert(!p.emptyProxyState&&!p.clientConfirmed&&p.role.Contains("unspecified"),"nonempty state mislabeled host");});
            Console.WriteLine("RESULT "+count+" focused client-state cases passed; no native processes launched.");return;
        }
        if(args.Length>2 && args[2]=="--paths-only") {
            Case("drive root contains descendants",()=>{Assert(BackupEngine.Within(@"D:\foo",@"D:\"),"drive root boundary escaped");Assert(!BackupEngine.Within(@"D:\foobar",@"D:\foo"),"prefix sibling accepted");});
            Case("drive relative and rooted relative selections refuse",()=>{Expect(()=>BackupEngine.Canon(@"D:relative"),"fully absolute");Expect(()=>BackupEngine.Canon(@"\relative"),"fully absolute");});
            Case("root source forbids descendant backup destination",()=>{string root=Root("root-source"),drive=Path.GetPathRoot(root);var p=BackupEngine.Discover(root);p.sources.Add(new Source{name="root",path=drive,directory=true});Expect(()=>BackupEngine.ValidateDestination(p,Destination(root)),"outside all save");});
            Case("absolute Unicode mixed separators normalize",()=>{Assert(BackupEngine.Canon(@"D:\保存/Ω/")==@"D:\保存\Ω","separator normalization failed");});
            Case("another launcher managed destination refuses",()=>{string root=Root("other-launcher-test"),other=Root("other-launcher");Expect(()=>BackupEngine.ValidateDestination(BackupEngine.Discover(root),Path.Combine(other,"current","tools-backups")),"outside every Co-op+");});
            Console.WriteLine("RESULT "+count+" focused path regressions passed.");return;
        }
        Case("custom Unicode paths and full paired profile round trip",()=>{
            string root=Root("roundtrip"),done=Run(root);var manifest=BackupEngine.Json.Deserialize<Manifest>(File.ReadAllText(Path.Combine(done,"backup.json")));
            Assert(manifest.selection.release.Contains("1.9.15.0 build 3"),"release missing");Assert(manifest.recovery.Contains("not tested"),"overstated recovery");
            string restored=Path.Combine(sandbox,"disposable restore Ω");Directory.CreateDirectory(restored);
            foreach(var entry in manifest.files){string original=Path.Combine(done,"data",entry.path.Replace('/',Path.DirectorySeparatorChar)),target=Path.Combine(restored,entry.path.Replace('/',Path.DirectorySeparatorChar));if(entry.directory)Directory.CreateDirectory(target);else{Directory.CreateDirectory(Path.GetDirectoryName(target));File.Copy(original,target);Assert(File.ReadAllBytes(original).SequenceEqual(File.ReadAllBytes(target)),"roundtrip bytes differ");}}
            Assert(Directory.Exists(Path.Combine(restored,"noita-profile","empty folder")),"empty directory lost");Assert(File.ReadAllText(Path.Combine(restored,"proxy-state","state.bin"))=="paired state sentinel","paired state missing");
            Assert(File.ReadAllText(Path.Combine(done,"RESTORE.txt")).Contains("Both PCs"),"pair instructions missing");
        });
        Case("repeat backups retain earlier bytes and unique names",()=>{string root=Root("repeat"),first=Run(root),before=File.ReadAllText(Path.Combine(first,"backup.json")),second=Run(root);Assert(first!=second&&Directory.Exists(first)&&Directory.Exists(second),"overwrote backup");Assert(File.ReadAllText(Path.Combine(first,"backup.json"))==before,"old backup changed");});
        Case("missing selected state refuses without default substitution",()=>{string root=Root("missing");var p=BackupEngine.Discover(root);Directory.Move(p.state,p.state+"-missing");Expect(()=>Run(root),"Required backup source is missing");Assert(!Directory.Exists(Destination(root)),"failed preflight created destination");});
        Case("None selected profile refuses instead of using ordinary profile",()=>{string root=Root("none"),settings=BackupEngine.Discover(root).settings;File.WriteAllText(settings,"(paths:(noita_save:None))");Expect(()=>Run(root),"No default path was substituted");});
        Case("missing shared profile refuses incomplete run",()=>{string root=Root("shared");var p=BackupEngine.Discover(root);Directory.Move(Path.Combine(p.profile,"save_shared"),Path.Combine(p.profile,"old shared"));Expect(()=>Run(root),"save00 and save_shared");});
        Case("destination inside source refuses",()=>{string root=Root("dest-source");var p=BackupEngine.Discover(root);Expect(()=>BackupEngine.Run(root,Path.Combine(p.profile,"backup"),CancellationToken.None,s=>{}),"outside all save");});
        Case("destination inside updater folder refuses",()=>{string root=Root("dest-managed");Expect(()=>BackupEngine.Run(root,Path.Combine(root,"current","backups"),CancellationToken.None,s=>{}),"outside all save");});
        Case("destination in source checkout refuses",()=>{string root=Root("dest-git"),checkout=Path.Combine(sandbox,"checkout");Directory.CreateDirectory(Path.Combine(checkout,".git"));Expect(()=>BackupEngine.Run(root,Path.Combine(checkout,"backups"),CancellationToken.None,s=>{}),"outside source checkouts");});
        Case("insufficient space refuses before staging",()=>{string root=Root("space");BackupEngine.FreeBytes=p=>0;Expect(()=>Run(root),"Not enough free space");Assert(!Directory.Exists(Destination(root)),"created stage despite no space");});
        Case("cancellation midway retains old backup and labels incomplete",()=>{string root=Root("cancel"),first=Run(root);var token=new CancellationTokenSource();BackupEngine.Fault=point=>{if(point=="copy-chunk")token.Cancel();};Expect(()=>BackupEngine.Run(root,Destination(root),token.Token,s=>{}),"Backup cancelled");Assert(Directory.Exists(first),"old backup lost");var stages=Directory.GetDirectories(Destination(root),".incomplete-*");Assert(stages.Length==1&&File.Exists(Path.Combine(stages[0],"INCOMPLETE.txt")),"incomplete mislabeled");Assert(Directory.GetDirectories(Destination(root),"CoopPlus-*").Length==1,"cancel marked complete");});
        Case("source contents change after copy refuses verified claim",()=>{string root=Root("mutated"),profile=BackupEngine.Discover(root).profile;BackupEngine.Fault=point=>{if(point=="copied")File.AppendAllText(Path.Combine(profile,"save00","player.xml"),"changed");};Expect(()=>Run(root),"Sources changed during backup");Assert(Directory.GetDirectories(Destination(root),"CoopPlus-*").Length==0,"mutation marked complete");});
        Case("new source file after copy refuses inventory mismatch",()=>{string root=Root("new-file"),profile=BackupEngine.Discover(root).profile;BackupEngine.Fault=point=>{if(point=="copied")FileAt(Path.Combine(profile,"save00","new.xml"),"new");};Expect(()=>Run(root),"Sources changed during backup");});
        Case("copy exception preserves earlier backups",()=>{string root=Root("copy-error"),first=Run(root);BackupEngine.Fault=point=>{if(point=="copy-chunk")throw new IOException("injected disk failure");};Expect(()=>Run(root),"injected disk failure");Assert(Directory.Exists(first),"old backup lost");Assert(Directory.GetDirectories(Destination(root),".incomplete-*").Length==1,"failure not staged separately");});
        Case("corrupted copied bytes refuse hash verification",()=>{string root=Root("corrupt-copy");BackupEngine.Fault=point=>{if(point=="copied"){string stage=Directory.GetDirectories(Destination(root),".incomplete-*").Single();File.AppendAllText(Path.Combine(stage,"data","noita-profile","save00","player.xml"),"corrupt");}};Expect(()=>Run(root),"Copied inventory/hash verification failed");});
        Case("process appears during copying refuses completion",()=>{string root=Root("busy-during");bool busy=false;BackupEngine.BusyCheck=()=>{if(busy)throw new IOException("process became active");};BackupEngine.Fault=point=>{if(point=="copied-file")busy=true;};Expect(()=>Run(root),"process became active");Assert(Directory.GetDirectories(Destination(root),"CoopPlus-*").Length==0,"running copy marked complete");});
        Case("RON punctuation inside strings does not alter containers",()=>{Assert(RonSelection.Profile("(app:(nickname:Some(\"(\"),other:\")\"),paths:(noita_save:Some(r#\"C:\\保存\\profile\"#)))")=="C:\\保存\\profile","literal punctuation parsed as syntax");});
        Case("RON duplicate selected fields are rejected",()=>{Expect(()=>RonSelection.Profile("(paths:(noita_save:Some(\"C:\\\\one\"),noita_save:Some(\"C:\\\\two\")))"),"Ambiguous");});
        Case("duplicate launcher selection fields are ambiguous",()=>{string root=Root("duplicate-json"),config=Path.Combine(root,"coopplus.json"),text=File.ReadAllText(config);File.WriteAllText(config,text.Substring(0,text.Length-1)+",\"settingsPath\":\"C:\\\\wrong\\\\proxy.ron\"}");Expect(()=>Run(root),"Ambiguous duplicate");});
        Case("RON nested comment and Unicode scalar supported",()=>{Assert(RonSelection.Profile("(/* x /* y */ */paths:(noita_save:Some(\"C:\\\\\\u{03A9}\")))")=="C:\\Ω","Unicode decode failed");});
        Case("portable selected profile inside game remains valid",()=>{string root=Root("portable"),p=BackupEngine.Discover(root).profile,game=BackupEngine.Discover(root).game,settings=BackupEngine.Discover(root).settings;Directory.Move(p,Path.Combine(game,"profile"));File.WriteAllText(settings,"(paths:(noita_save:Some("+RonQuote(Path.Combine(game,"profile"))+")))");Assert(Directory.Exists(Run(root)),"portable profile rejected");});
        Case("second portable profile is ambiguous",()=>{string root=Root("ambiguous");Directory.CreateDirectory(Path.Combine(BackupEngine.Discover(root).game,"save00"));Expect(()=>Run(root),"second portable");});
        Case("custom shortcut storage override refuses guessing",()=>{string root=Root("shortcut");object shell=null,link=null;
            try{shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell",true));link=shell.GetType().InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{Path.Combine(root,"fixture override.lnk")});Type t=link.GetType();t.InvokeMember("TargetPath",BindingFlags.SetProperty,null,link,new object[]{Path.Combine(root,"CoopPlus.exe")});t.InvokeMember("Arguments",BindingFlags.SetProperty,null,link,new object[]{"--save-state-path C:\\custom"});t.InvokeMember("Save",BindingFlags.InvokeMethod,null,link,new object[0]);}
            finally{if(link!=null)Marshal.FinalReleaseComObject(link);if(shell!=null)Marshal.FinalReleaseComObject(shell);}
            LaunchOverrides.Folders=p=>new[]{root};LaunchOverrides.SkipForTests=false;
            try{Expect(()=>Run(root),"shortcut overrides selected storage");}finally{LaunchOverrides.SkipForTests=true;}
        });
        Case("real process name refusal without touching a game",()=>{string root=Root("process");
            if(args[1]=="--existing-process") {BackupEngine.BusyCheck=BackupEngine.CheckNotRunning;Expect(()=>Run(root),"Still running:");Assert(!Directory.Exists(Destination(root)),"process refusal staged a backup");}
            else using(var process=Process.Start(new ProcessStartInfo{FileName=args[1],UseShellExecute=false,CreateNoWindow=true,WindowStyle=ProcessWindowStyle.Hidden})){try{Thread.Sleep(150);BackupEngine.BusyCheck=BackupEngine.CheckNotRunning;Expect(()=>Run(root),"Still running: noita");Assert(!Directory.Exists(Destination(root)),"process refusal staged a backup");}finally{process.WaitForExit(5000);}}});
        Console.WriteLine("RESULT "+count+" focused cases passed; filesystem restore verified; no native Continue claim.");
    }
}
