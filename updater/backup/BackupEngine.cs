// Co-op+ standalone backup utility. Project MIT / Apache-2.0 licensing.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace CoopPlusBackup {
    public sealed class Source { public string name; public string path; public bool directory; }
    public sealed class Entry { public string path; public long size; public string sha256; public long modifiedUtc; public bool directory; public bool absent; }
    public sealed class Plan {
        public string launcher; public string game; public string profile; public string settings; public string state; public string release;
        public string role = "unspecified; nonempty state does not establish host health";
        public bool emptyProxyState; public bool clientConfirmed; public bool neverInitialized;
        public List<Source> sources = new List<Source>();
        public List<Source> absentSources = new List<Source>();
    }
    public sealed class Manifest {
        public int schema = 1; public string utilityVersion = "0.1.2"; public string createdUtc; public string verification;
        public string recovery = "File copy verified; game Continue not tested by this program.";
        public Plan selection; public Entry[] files;
    }
    public static class BackupEngine {
        public static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 };
        // Injection is accessible only to a source-linked test harness, never command-line switches.
        internal static Action<string> Fault = null;
        internal static Action BusyCheck = CheckNotRunning;
        internal static Func<string,long> FreeBytes = path => new DriveInfo(Path.GetPathRoot(path)).AvailableFreeSpace;
        public static string Canon(string path) {
            if (String.IsNullOrWhiteSpace(path)) throw new IOException("A selected path is missing or is not absolute: " + path);
            string windows=path.Replace('/','\\');
            bool drive=windows.Length>=3 && Char.IsLetter(windows[0]) && windows[1]==':' && windows[2]=='\\';
            bool unc=windows.StartsWith("\\\\",StringComparison.Ordinal) && !windows.StartsWith("\\\\?\\",StringComparison.Ordinal) && !windows.StartsWith("\\\\.\\",StringComparison.Ordinal) && windows.Substring(2).Split(new[]{'\\'},StringSplitOptions.RemoveEmptyEntries).Length>=2;
            if(!drive && !unc)throw new IOException("A selected path is missing or is not fully absolute: " + path);
            string full=Path.GetFullPath(windows);
            return full.Length>Path.GetPathRoot(full).Length ? full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) : full;
        }
        public static bool Within(string path, string parent) {
            string prefix=parent.EndsWith(Path.DirectorySeparatorChar.ToString(),StringComparison.Ordinal)?parent:parent+Path.DirectorySeparatorChar;
            return String.Equals(path,parent,StringComparison.OrdinalIgnoreCase) || path.StartsWith(prefix,StringComparison.OrdinalIgnoreCase);
        }
        public static void NoLinks(string path) {
            for (string p = Path.GetFullPath(path); p != null; p = Path.GetDirectoryName(p)) {
                if ((File.Exists(p) || Directory.Exists(p)) && (File.GetAttributes(p) & FileAttributes.ReparsePoint) != 0) throw new IOException("Linked folders/files are not supported: " + p);
            }
        }
        static string Text(string path) {
            NoLinks(path);
            using (var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read)) {
                if (f.Length > 8*1024*1024) throw new IOException("Selection metadata is too large: " + path);
                using (var r = new StreamReader(f, new UTF8Encoding(false,true), true)) return r.ReadToEnd();
            }
        }
        static string Required(Dictionary<string,object> d,string key) {
            object v; if (!d.TryGetValue(key,out v) || !(v is string) || String.IsNullOrWhiteSpace((string)v)) throw new IOException("coopplus.json is missing " + key + ".");
            return Canon((string)v);
        }
        static Dictionary<string,object> Config(string text) {
            // JavaScriptSerializer otherwise silently accepts duplicate JSON fields.
            var names=new HashSet<string>(StringComparer.Ordinal); int depth=0;
            for(int i=0;i<text.Length;i++) {
                char ch=text[i];
                if(ch=='"') {
                    int begin=i; bool escaped=false;
                    while(++i<text.Length) { if(escaped){escaped=false;continue;} if(text[i]=='\\'){escaped=true;continue;} if(text[i]=='"')break; }
                    if(i>=text.Length)throw new IOException("Invalid coopplus.json string.");
                    int next=i+1;while(next<text.Length && Char.IsWhiteSpace(text[next]))next++;
                    if(depth==1 && next<text.Length && text[next]==':') {
                        string key=Json.Deserialize<string>(text.Substring(begin,i-begin+1));
                        if(!names.Add(key))throw new IOException("Ambiguous duplicate coopplus.json field: "+key);
                    }
                } else if(ch=='{' || ch=='[') depth++;
                else if(ch=='}' || ch==']')depth--;
            }
            return Json.Deserialize<Dictionary<string,object>>(text);
        }
        public static Plan Discover(string root) { return Discover(root,false); }
        public static Plan Discover(string root,bool confirmedJoiningClient) {
            root = Canon(root); NoLinks(root);
            if (!File.Exists(Path.Combine(root,"CoopPlus.exe"))) throw new IOException("Select the folder containing CoopPlus.exe and coopplus.json.");
            var c = Config(Text(Path.Combine(root,"coopplus.json")));
            object schema;
            if (c == null || !c.TryGetValue("schema",out schema) || Convert.ToInt32(schema) != 1) throw new IOException("Unsupported coopplus.json format.");
            var p = new Plan { launcher=root, game=Required(c,"gameDir"), settings=Required(c,"settingsPath"), state=Required(c,"saveStatePath") };
            p.profile = Canon(RonSelection.Profile(Text(p.settings)));
            // A portable game profile alongside the executable is ambiguous unless RON selects it.
            foreach (string alternate in new [] { Path.Combine(p.game,"save00"), Path.Combine(p.game,"save_shared") })
                if (Directory.Exists(alternate) && !Within(alternate,p.profile)) throw new IOException("A second portable Noita profile exists in the game folder. The effective launch profile is ambiguous; no backup was started.");
            LaunchOverrides.AssertUnambiguous(p);
            string installed = Path.Combine(root,"current","installed.json");
            var release = Json.Deserialize<Dictionary<string,object>>(Text(installed));
            object version, build, proxy;
            if (!release.TryGetValue("version",out version) || !release.TryGetValue("build",out build) || !release.TryGetValue("officialProxyVersion",out proxy)) throw new IOException("Installed release identity is incomplete.");
            p.release = "Co-op+ " + version + " build " + build + "; official proxy " + proxy;
            Add(p,"noita-profile",p.profile,true);
            if (!Directory.Exists(Path.Combine(p.profile,"save00")) || !Directory.Exists(Path.Combine(p.profile,"save_shared"))) throw new IOException("Selected Noita profile must contain save00 and save_shared: " + p.profile);
            Add(p,"proxy-state",p.state,true);
            p.emptyProxyState=Directory.GetFileSystemEntries(p.state).Length==0;
            if(p.emptyProxyState) {
                if(!confirmedJoiningClient)throw new IOException("Selected proxy state is empty. Confirm that this PC joins another player (client). An empty host state cannot provide host recovery.");
                p.clientConfirmed=true;p.role="joining client (explicitly confirmed); proxy state empty";
            }
            Add(p,"proxy-settings/proxy.ron",p.settings,false);
            Add(p,"launcher/coopplus.json",Path.Combine(root,"coopplus.json"),false);
            Add(p,"runtime/installed.json",installed,false);
            Add(p,"runtime/CoopPlus.exe",Path.Combine(root,"CoopPlus.exe"),false);
            foreach (string log in new [] { "ew_log.txt", "ew_log_old.txt" })
                Optional(p,"diagnostics/"+log,Path.Combine(root,"current","proxy",log));
            Optional(p,"diagnostics/logger.txt",Path.Combine(p.game,"logger.txt"));
            foreach (string runtime in new [] { Path.Combine(p.game,"noita.exe"),Path.Combine(root,"current","proxy","noita_proxy.exe") })
                if (!File.Exists(runtime)) throw new IOException("Required runtime is missing: " + runtime);
            // Record exact executable identities without distributing the game executable.
            Add(p,"runtime-fingerprints/noita.exe.sha256-source",Path.Combine(p.game,"noita.exe"),false);
            Add(p,"runtime-fingerprints/noita_proxy.exe.sha256-source",Path.Combine(root,"current","proxy","noita_proxy.exe"),false);
            return p;
        }
        static void Add(Plan p,string name,string path,bool dir) {
            path=Canon(path); NoLinks(path);
            if (dir ? !Directory.Exists(path) : !File.Exists(path)) throw new IOException("Required backup source is missing: " + path);
            p.sources.Add(new Source { name=name,path=path,directory=dir });
        }
        static void Optional(Plan p,string name,string path) { if (File.Exists(path)) Add(p,name,path,false); }
        public static void CheckNotRunning() {
            foreach (var process in Process.GetProcesses()) using(process) {
                string name;
                try { name=process.ProcessName.ToLowerInvariant(); } catch (InvalidOperationException) { continue; }
                if (name=="noita" || name=="noita_dev" || name=="noita_proxy" || name=="coopplus" || name=="coopupdater")
                    throw new IOException("Save & Quit Noita, then close every proxy and Co-op+ launcher before backing up. Still running: " + name + ". Nothing was stopped.");
            }
        }
        static void Cancel(CancellationToken token) { token.ThrowIfCancellationRequested(); }
        static void Tick(string point) { if (Fault!=null) Fault(point); }
        static Entry Inspect(string logical,string file,bool directory,CancellationToken token) {
            Cancel(token); NoLinks(file);
            if (directory) return new Entry {path=logical,directory=true};
            var before=new FileInfo(file); long length=before.Length, modified=before.LastWriteTimeUtc.Ticks;
            string hash;
            using (var f=new FileStream(file,FileMode.Open,FileAccess.Read,FileShare.Read)) using (var sha=SHA256.Create()) {
                var block=new byte[1024*1024]; int n;
                while((n=f.Read(block,0,block.Length))>0) { Cancel(token); sha.TransformBlock(block,0,n,null,0); }
                sha.TransformFinalBlock(new byte[0],0,0); hash=BitConverter.ToString(sha.Hash).Replace("-","").ToLowerInvariant();
            }
            var after=new FileInfo(file);
            if (after.Length!=length || after.LastWriteTimeUtc.Ticks!=modified) throw new IOException("Source changed while reading: " + file);
            return new Entry {path=logical,size=length,modifiedUtc=modified,sha256=hash};
        }
        static void Walk(string physical,string logical,List<Entry> result,CancellationToken token,Action<string> status) {
            NoLinks(physical); Cancel(token); result.Add(Inspect(logical,physical,true,token));
            foreach (string sub in Directory.GetDirectories(physical).OrderBy(x=>x,StringComparer.OrdinalIgnoreCase)) Walk(sub,logical+"/"+Path.GetFileName(sub),result,token,status);
            foreach (string file in Directory.GetFiles(physical).OrderBy(x=>x,StringComparer.OrdinalIgnoreCase)) { status("Checking " + logical+"/"+Path.GetFileName(file)); result.Add(Inspect(logical+"/"+Path.GetFileName(file),file,false,token)); }
        }
        static List<Entry> Inventory(Plan plan,CancellationToken token,Action<string> status) {
            var entries=new List<Entry>();
            foreach(var s in plan.sources) {
                BusyCheck();
                if(s.directory) Walk(s.path,s.name,entries,token,status); else entries.Add(Inspect(s.name,s.path,false,token));
            }
            foreach(var s in plan.absentSources) {
                BusyCheck();Cancel(token);NoLinks(s.path);
                if(File.Exists(s.path) || Directory.Exists(s.path))throw new IOException("Source absence changed during backup: "+s.path);
                entries.Add(new Entry {path=s.name,directory=s.directory,absent=true});
            }
            if(entries.Select(e=>e.path).Distinct(StringComparer.OrdinalIgnoreCase).Count()!=entries.Count) throw new IOException("Backup source paths collide.");
            return entries.OrderBy(e=>e.path,StringComparer.OrdinalIgnoreCase).ToList();
        }
        static bool Same(List<Entry> a,List<Entry> b,bool times) {
            if(a.Count!=b.Count)return false;
            for(int i=0;i<a.Count;i++)if(a[i].path!=b[i].path || a[i].directory!=b[i].directory || a[i].absent!=b[i].absent || a[i].size!=b[i].size || a[i].sha256!=b[i].sha256 || times && a[i].modifiedUtc!=b[i].modifiedUtc)return false;
            return true;
        }
        static string Physical(Plan p,Entry e) {
            var source=p.sources.Single(s=>e.path==s.name || e.path.StartsWith(s.name+"/",StringComparison.Ordinal));
            return e.path==source.name ? source.path : Path.Combine(source.path,e.path.Substring(source.name.Length+1).Replace('/',Path.DirectorySeparatorChar));
        }
        public static void ValidateDestination(Plan plan,string destination) {
            destination=Canon(destination); NoLinks(destination);
            foreach(string source in plan.sources.Concat(plan.absentSources).Select(s=>s.directory?s.path:Path.GetDirectoryName(s.path)).Concat(new[]{plan.game,plan.launcher,plan.profile}))
                if(Within(destination,Canon(source)) || Within(Canon(source),destination)) throw new IOException("Choose a backup folder outside all save, game, launcher and managed folders: " + destination);
            for(string p=destination;p!=null;p=Path.GetDirectoryName(p)) {
                if(Directory.Exists(Path.Combine(p,".git")) || File.Exists(Path.Combine(p,".git"))) throw new IOException("Backups must be outside source checkouts.");
                if(File.Exists(Path.Combine(p,"coopplus.json")) || File.Exists(Path.Combine(p,"CoopPlus.exe")) || String.Equals(Path.GetFileName(p),".coopplus-backups",StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Backups must be outside every Co-op+ installation and its managed updater folders.");
            }
        }
        public static string Run(string root,string destination,CancellationToken token,Action<string> status) { return Run(root,destination,token,status,false); }
        public static string Run(string root,string destination,CancellationToken token,Action<string> status,bool confirmedJoiningClient) {
            return RunSelected(() => Discover(root,confirmedJoiningClient),destination,token,status);
        }
        // Launcher integration supplies its effective, validated paths directly. The
        // copy, hash, source-stability and atomic-completion engine is shared with 0.1.2.
        public static string RunSelected(Func<Plan> select,string destination,CancellationToken token,Action<string> status) {
            BusyCheck(); Cancel(token);
            destination=Canon(destination);
            Plan plan=select(); ValidateDestination(plan,destination);
            var baseline=Inventory(plan,token,status); Tick("inventoried");
            long total=baseline.Sum(e=>e.size), required=checked(total+Math.Max(64L*1024*1024,total/20));
            if(FreeBytes(destination)<required)throw new IOException("Not enough free space. Need " + (required/1024/1024+1) + " MB including a safety margin.");
            Cancel(token); BusyCheck(); NoLinks(destination); Directory.CreateDirectory(destination);
            string id=DateTime.Now.ToString("yyyy-MM-dd_HHmmss")+"-"+Guid.NewGuid().ToString("N");
            string stage=Path.Combine(destination,".incomplete-"+id), finished=Path.Combine(destination,"CoopPlus-"+id);
            if(Directory.Exists(stage) || File.Exists(stage) || Directory.Exists(finished) || File.Exists(finished)) throw new IOException("Unique backup name already exists. Retry to allocate another name.");
            Directory.CreateDirectory(stage); string data=Path.Combine(stage,"data"); Directory.CreateDirectory(data);
            try {
                long copied=0; var progressTimer=Stopwatch.StartNew();
                foreach(var e in baseline) {
                    Cancel(token); BusyCheck(); string target=Path.Combine(data,e.path.Replace('/',Path.DirectorySeparatorChar)); NoLinks(target);
                    if(e.absent)continue;
                    if(e.directory) { Directory.CreateDirectory(target); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    status("Copying " + (total==0?100:copied*100/total) + "% — " + e.path);
                    using(var input=new FileStream(Physical(plan,e),FileMode.Open,FileAccess.Read,FileShare.Read))
                    using(var output=new FileStream(target,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                        byte[] buffer=new byte[1024*1024]; int n;
                        while((n=input.Read(buffer,0,buffer.Length))>0) {
                            Cancel(token); output.Write(buffer,0,n); copied+=n; Tick("copy-chunk");
                            if(progressTimer.ElapsedMilliseconds>=250) { status("Copying "+(total==0?100:copied*100/total)+"% — "+(copied/1024/1024)+" of "+(total/1024/1024)+" MB");progressTimer.Restart(); }
                        }
                        output.Flush(true);
                    }
                    File.SetLastWriteTimeUtc(target,new DateTime(e.modifiedUtc,DateTimeKind.Utc)); Tick("copied-file");
                }
                Tick("copied"); status("Verifying copied files…");
                var copiedEntries=new List<Entry>();
                foreach(var s in plan.sources) {
                    string target=Path.Combine(data,s.name.Replace('/',Path.DirectorySeparatorChar));
                    if(s.directory)Walk(target,s.name,copiedEntries,token,status);else copiedEntries.Add(Inspect(s.name,target,false,token));
                }
                foreach(var s in plan.absentSources) {
                    string target=Path.Combine(data,s.name.Replace('/',Path.DirectorySeparatorChar));NoLinks(target);
                    if(File.Exists(target) || Directory.Exists(target))throw new IOException("An absent source was fabricated in the copied payload: "+s.name);
                    copiedEntries.Add(new Entry {path=s.name,directory=s.directory,absent=true});
                }
                copiedEntries=copiedEntries.OrderBy(e=>e.path,StringComparer.OrdinalIgnoreCase).ToList();
                if(!Same(baseline,copiedEntries,false))throw new IOException("Copied inventory/hash verification failed. This is not a completed backup.");
                status("Checking sources stayed unchanged…");
                if(!Same(baseline,Inventory(select(),token,status),true))throw new IOException("Sources changed during backup. Close Noita/proxies and retry. This copy is incomplete.");
                BusyCheck(); Cancel(token); Tick("verified");
                var manifest=new Manifest {createdUtc=DateTime.UtcNow.ToString("o"),verification="Inventory, SHA-256 and source stability verified",selection=plan,files=baseline.ToArray()};
                File.WriteAllText(Path.Combine(stage,"backup.json"),Json.Serialize(manifest),new UTF8Encoding(false));
                File.WriteAllText(Path.Combine(stage,"RESTORE.txt"),RestoreText(plan),new UTF8Encoding(false));
                Cancel(token); BusyCheck(); NoLinks(destination);
                Directory.Move(stage,finished); status("Verified backup complete."); return finished;
            } catch(Exception e) {
                // Retain incomplete work without touching any earlier completed backup.
                try { File.WriteAllText(Path.Combine(stage,"INCOMPLETE.txt"),"NOT A VERIFIED BACKUP\r\n"+e.Message+"\r\nRetry after resolving the error. Earlier backups were not changed.",new UTF8Encoding(false)); } catch { }
                throw new IOException((e is OperationCanceledException ? "Backup cancelled." : e.Message)+"\r\nIncomplete files: "+stage+"\r\nEarlier backups were preserved.",e);
            }
        }
        static string RestoreText(Plan p) {
            return "Co-op+ paired backup — manual recovery reference\r\n\r\n"+p.release+"\r\n"+
                "Recorded role: "+p.role+"\r\n"+
                (p.neverInitialized ? "Explicit first-use evidence records genuine source absences in backup.json (absent:true). No proxy world/run state was fabricated. An absent entry has no payload file or directory; do not mistake that recorded absence for a corrupt copy. This snapshot cannot supply a host checkpoint or prove paired Continue.\r\n" : (p.emptyProxyState ? (p.clientConfirmed ? "This PC was explicitly confirmed as a joining client. Its existing empty proxy-state directory was preserved. The corresponding host backup from the same stopping point is also required; this client backup cannot supply host world/run state.\r\n" : "The existing proxy-state directory was empty. This automatic snapshot does not establish the PC's host/client role or a healthy host checkpoint. A matching stopped host backup is required; this empty state cannot supply host world/run recovery.\r\n") : "Nonempty proxy state alone does not establish a healthy host or a successful Continue.\r\n"))+
                "Verification covers file copying and source stability. It does not prove that the run will Continue.\r\n"+
                "Keep this original backup unchanged. Use a separate copy for any investigation/recovery.\r\n\r\n"+
                "Both PCs must Save & Quit and close Noita and every proxy at the same agreed stopping point. Each PC needs its own paired backup. This backup protects only this PC.\r\n"+
                "Restore only while all those programs are closed, with the same host/player identities, mode and matching runtime release on both PCs. Preserve any newer live files first.\r\n"+
                "Manually restore the COMPLETE noita-profile and proxy-state from data together, plus the selected proxy-settings/proxy.ron. Do not merge saves from different stopping points.\r\n"+
                "Original Noita profile: "+p.profile+"\r\nOriginal EW state: "+p.state+"\r\nOriginal EW settings: "+p.settings+"\r\n"+
                "Launcher configuration is under data/launcher; runtime identity is in data/runtime/installed.json and backup.json. The game executable fingerprint source is evidence, not an installer.\r\n"+
                "There is no restore/delete button. Have a knowledgeable reviewer establish isolated paths before trying a copied Continue. Never let an autosave/relaunch point at ordinary live saves.\r\n"+
                "Keep saves and diagnostics private; do not upload them to public GitHub.\r\n";
        }
    }
}
