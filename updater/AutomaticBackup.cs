// Automatic pre-first-launch protection, using the verified standalone 0.1.2 engine.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using CoopPlusBackup;

namespace CoopPlus {
    internal static class AutomaticBackup {
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 };
        internal static Action<string> Status = Console.WriteLine;
        internal static Action<string> FirstUseFault;
        internal sealed class Receipt {
            public int schema = 1; public string packageSha256; public string selectionKey;
            public string directory; public string manifestSha256; public string restoreSha256; public string createdUtc;
        }
        internal sealed class BackupLocation { public int schema=1; public string directory; }
        internal sealed class FirstUseWrite { public string path; public string contents; public string sha256; }
        internal sealed class FirstUseProof {
            public int schema=1; public string phase="prepared"; public string root,game,settings,state,profile,createdUtc;
            public string backupDirectory,manifestSha256,restoreSha256; public FirstUseWrite[] writes;
        }
        private static string FirstUsePath(string root){string path=Path.Combine(root,"first-use.json");Program.Guard(path);return path;}
        private static string NativeProfile(){return BackupEngine.Canon(Path.Combine(BackupEngine.Canon(Environment.GetEnvironmentVariable("USERPROFILE")),"AppData","LocalLow","Nolla_Games_Noita"));}
        private static FirstUseProof ReadFirstUse(string root) {
            string path=FirstUsePath(root);if(!File.Exists(path))return null;
            if(new FileInfo(path).Length>1024*1024)throw new IOException("First-use evidence is too large; nothing was launched.");
            var proof=Json.Deserialize<FirstUseProof>(File.ReadAllText(path,new UTF8Encoding(false,true)));
            if(proof==null || proof.schema!=1 || (proof.phase!="prepared" && proof.phase!="initializing" && proof.phase!="launch-attempted"))throw new IOException("First-use evidence is corrupt; no reset was attempted.");
            return proof;
        }
        private static void WriteFirstUse(string root,FirstUseProof proof) {
            string path=FirstUsePath(root),temp=path+".new-"+Guid.NewGuid().ToString("N");
            try {
                byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(proof));
                using(var output=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None)){output.Write(bytes,0,bytes.Length);output.Flush(true);}
                if(File.Exists(path))File.Replace(temp,path,null);else File.Move(temp,path);
            } finally {if(File.Exists(temp))File.Delete(temp);}
        }
        private static bool Matches(FirstUseProof proof,string root,string game,string settings,string state,string profile) {
            return proof!=null && new[]{new[]{proof.root,root},new[]{proof.game,game},new[]{proof.settings,settings},new[]{proof.state,state},new[]{proof.profile,profile}}.All(pair=>!String.IsNullOrWhiteSpace(pair[0]) && String.Equals(BackupEngine.Canon(pair[0]),BackupEngine.Canon(pair[1]),StringComparison.OrdinalIgnoreCase));
        }
        private static void NoFreshCommands(string[] args) {
            if(args.Any(arg=>arg=="--launch-cmd" || arg.StartsWith("--launch-cmd=",StringComparison.Ordinal) || arg=="--run-noita-with-gdb"))throw new IOException("First-use setup requires the supported native launch; no custom launch command was guessed.");
        }
        private static void CheckFirstProfile(string profile) {
            BackupEngine.NoLinks(profile);if(File.Exists(profile))throw new IOException("Noita profile is not a directory.");
            foreach(string name in new[]{"save00","save_shared","save_stats"}){string path=Path.Combine(profile,name);BackupEngine.NoLinks(path);if(File.Exists(path))throw new IOException("Noita profile component is not a directory: "+name);}
            if(!Directory.Exists(profile))return;
            bool saves=Directory.Exists(Path.Combine(profile,"save00")),shared=Directory.Exists(Path.Combine(profile,"save_shared"));
            if(saves && !shared)throw new IOException("Existing Noita save00 is incomplete without save_shared; first-use setup will not repair an established run.");
            if(!saves)foreach(string entry in Directory.GetFileSystemEntries(profile))
                if(!Directory.Exists(entry) || (Path.GetFileName(entry)!="save_shared" && Path.GetFileName(entry)!="save_stats"))throw new IOException("An incomplete Noita profile contains unrecognized data; first-use setup refused.");
            ValidatePreference(Path.Combine(profile,"save_shared","config.xml"),"Config");
            ValidatePreference(Path.Combine(profile,"save00","mod_config.xml"),"Mods");
        }
        private static void ValidatePreference(string path,string rootName) {
            BackupEngine.NoLinks(path);if(Directory.Exists(path))throw new IOException("Noita preference is a directory: "+path);
            if(!File.Exists(path))return;
            if(new FileInfo(path).Length>8*1024*1024)throw new IOException("Noita preference is too large; it was preserved.");
            try {
                var settings=new System.Xml.XmlReaderSettings {DtdProcessing=System.Xml.DtdProcessing.Prohibit,XmlResolver=null};
                using(var reader=System.Xml.XmlReader.Create(path,settings)) {
                    var document=new System.Xml.XmlDocument {XmlResolver=null};document.Load(reader);if(document.DocumentElement==null || document.DocumentElement.Name!=rootName)throw new IOException("Unexpected Noita preference document.");
                    if(rootName=="Mods") {
                        var mods=document.DocumentElement.SelectNodes("Mod");if(mods.Count==0)throw new IOException("Mod preferences are missing the frozen proxy's required Mod entries.");
                        foreach(System.Xml.XmlElement mod in mods) {byte enabled,fold;ulong workshop;if(!Byte.TryParse(mod.GetAttribute("enabled"),out enabled) || String.IsNullOrEmpty(mod.GetAttribute("name")) || !Byte.TryParse(mod.GetAttribute("settings_fold_open"),out fold) || !UInt64.TryParse(mod.GetAttribute("workshop_item_id"),out workshop))throw new IOException("Mod preferences are missing required frozen-schema attributes.");}
                    }
                }
            }catch(Exception e){throw new IOException("Existing Noita preference is corrupt; it was not replaced: "+path,e);}
        }
        // Called only by NEW Setup before its first coopplus.json write. No old
        // updater can manufacture eligibility merely by redispatching into us.
        internal static bool RecordFirstUse(string root,UserConfig config,string[] args) {
            CheckStopped(root);root=BackupEngine.Canon(root);string game=BackupEngine.Canon(config.gameDir),proxy=Path.Combine(root,"current","proxy");
            Program.ValidateForwarded(root,game,proxy,args);
            string settings=Effective(args,"--settings-path",config.settingsPath,proxy),state=Effective(args,"--save-state-path",config.saveStatePath,proxy);
            string selectedExe=Effective(args,"--exe-path",Path.Combine(game,"noita.exe"),proxy);
            if(!String.Equals(selectedExe,Path.Combine(game,"noita.exe"),StringComparison.OrdinalIgnoreCase))throw new IOException("First-use executable selection does not match Noita.");
            Program.ValidateMutable(root,game,settings);Program.ValidateMutable(root,game,state);
            BackupEngine.NoLinks(settings);BackupEngine.NoLinks(state);
            if(File.Exists(Path.Combine(root,"coopplus.json")))return false;
            if(File.Exists(settings) || Directory.Exists(settings) || Directory.Exists(state) || File.Exists(state))return false;
            var prior=ReadFirstUse(root);
            if(prior==null) {
                if(File.Exists(Path.Combine(root,"current","installed.json")) || Directory.Exists(Path.Combine(root,"automatic-backup-receipts")) || Directory.Exists(Path.Combine(game,"mods","quant.ew")) || File.Exists(Path.Combine(game,"mods","quant.ew")))return false;
                foreach(string history in new[]{Path.Combine(root,"rollback"),Path.Combine(game,".coopplus-backups")}) {
                    Program.Guard(history);if(Directory.Exists(history) && Directory.GetFileSystemEntries(history).Length!=0)return false;
                }
            }
            NoFreshCommands(args);
            string profile=NativeProfile();Program.ValidateMutable(root,game,profile);CheckFirstProfile(profile);
            if(BackupEngine.Within(settings,state) || BackupEngine.Within(state,settings) || BackupEngine.Within(settings,profile) || BackupEngine.Within(profile,settings) || BackupEngine.Within(state,profile) || BackupEngine.Within(profile,state))throw new IOException("First-use settings, proxy state and Noita profile must be separate, nonoverlapping paths.");
            foreach(string name in new[]{"first-use.json","coopplus.json","automatic-backup.json","CoopPlus.exe","current","automatic-backup-receipts","rollback","seed","recovery","downloads"}) {
                string reserved=Path.Combine(root,name);
                if(new[]{settings,state}.Any(path=>BackupEngine.Within(path,reserved) || BackupEngine.Within(reserved,path)))throw new IOException("First-use mutable paths conflict with launcher metadata or runtime storage: "+name);
            }
            var absentPlan=new Plan {launcher=root,game=game,profile=profile};
            absentPlan.absentSources.Add(new Source {name="proxy-settings/proxy.ron",path=settings});absentPlan.absentSources.Add(new Source {name="proxy-state",path=state,directory=true});
            BackupEngine.ValidateDestination(absentPlan,Destination(root,config));
            if(prior!=null) {
                if(prior.phase!="prepared" || !Matches(prior,root,game,settings,state,profile))throw new IOException("Existing first-use evidence cannot be regenerated; preserve it for setup review.");
                return true;
            }
            foreach(string name in new[]{"save00","save_shared","save_stats"})if(Directory.Exists(Path.Combine(game,name)) || File.Exists(Path.Combine(game,name)))throw new IOException("First-use settings do not establish the portable game profile; no default was substituted.");
            WriteFirstUse(root,new FirstUseProof {root=root,game=game,settings=settings,state=state,profile=profile,createdUtc=DateTime.UtcNow.ToString("o")});return true;
        }
        private static string Destination(string root,UserConfig config) {
            // Old protocol-1 Setup rewrites only fields it knows. A sidecar
            // survives that real old-updater redispatch without path loss.
            string sidecar=Path.Combine(root,"automatic-backup.json");Program.Guard(sidecar);
            string configured=config.automaticBackupPath;
            if(File.Exists(sidecar)) {
                var location=Json.Deserialize<BackupLocation>(File.ReadAllText(sidecar,new UTF8Encoding(false,true)));
                if(location==null || location.schema!=1 || String.IsNullOrWhiteSpace(location.directory))throw new IOException("Automatic backup location metadata is incomplete; nothing was launched.");
                string selected=BackupEngine.Canon(location.directory);
                if(!String.IsNullOrWhiteSpace(configured) && !String.Equals(selected,BackupEngine.Canon(configured),StringComparison.OrdinalIgnoreCase))throw new IOException("Automatic backup location settings conflict; nothing was launched.");
                configured=selected;
            }
            return String.IsNullOrWhiteSpace(configured)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"CoopPlusBackups","Automatic")
                : BackupEngine.Canon(configured);
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct BasicProcessInfo { public IntPtr reserved1; public IntPtr peb; public IntPtr reserved2a; public IntPtr reserved2b; public IntPtr id; public IntPtr parent; }
        [DllImport("ntdll.dll")]
        private static extern int NtQueryInformationProcess(IntPtr process, int kind, ref BasicProcessInfo info, int length, out int returned);
        private static readonly int SelfId=Process.GetCurrentProcess().Id;
        private static readonly long SelfStarted=Process.GetCurrentProcess().StartTime.ToUniversalTime().Ticks;
        private static int ParentId() {
            var info=new BasicProcessInfo(); int length;
            using(var self=Process.GetCurrentProcess()) {
                if(NtQueryInformationProcess(self.Handle,0,ref info,Marshal.SizeOf(typeof(BasicProcessInfo)),out length)!=0)
                    throw new IOException("Cannot establish owning launcher identity; nothing was launched.");
            }
            return info.parent.ToInt32();
        }
        internal static void CheckStopped(string root) {
            Engine.CheckNotBusy();
            // The owning bootstrap + this updater are expected. Reject any OTHER
            // launcher/updater instead of stopping it or reading its active state.
            int parentId=ParentId();
            foreach (string name in new [] { "CoopPlus", "CoopUpdater" })
                foreach (var p in Process.GetProcessesByName(name)) using (p) {
                    if (p.Id == SelfId && p.StartTime.ToUniversalTime().Ticks==SelfStarted) continue;
                    string executable;
                    try { executable = p.MainModule.FileName; }
                    catch { throw new IOException("Another Co-op+ launcher cannot be identified. Close other launchers and retry. Nothing was stopped."); }
                    // Exempt only this exact parent bootstrap at this root.
                    if (name == "CoopPlus" && p.Id==parentId && p.StartTime.ToUniversalTime().Ticks<SelfStarted &&
                        String.Equals(Path.GetFullPath(executable),Path.Combine(Path.GetFullPath(root),"CoopPlus.exe"),StringComparison.OrdinalIgnoreCase)) continue;
                    throw new IOException("Another Co-op+ updater is running. Close it and retry. Nothing was stopped: " + executable);
                }
        }
        private static string HashText(string text) {
            using (var sha = System.Security.Cryptography.SHA256.Create())
                return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(text))).Replace("-", "").ToLowerInvariant();
        }
        private static string Effective(string[] args, string option, string fallback, string proxy) {
            string selected = null;
            for (int i = 0; i < args.Length; i++) {
                string value = null;
                if (args[i] == option) { if (++i >= args.Length) throw new IOException("Missing value for " + option); value = args[i]; }
                else if (args[i].StartsWith(option + "=", StringComparison.Ordinal)) value = args[i].Substring(option.Length + 1);
                if (value == null) continue;
                if (selected != null || String.IsNullOrWhiteSpace(value)) throw new IOException("Ambiguous effective " + option + "; no backup was started.");
                selected = value;
            }
            string result = selected ?? fallback;
            if (String.IsNullOrWhiteSpace(result)) throw new IOException("Missing effective " + option + "; no default path was substituted.");
            return BackupEngine.Canon(Path.IsPathRooted(result) ? result : Path.Combine(proxy, result));
        }
        // Mirrors the POSIX quoting used by Rust shlex, then accepts only the
        // native executable and the verified workdir-userdata flag. No shell runs.
        private static List<string> SplitNative(string text) {
            var result=new List<string>();var word=new StringBuilder();char quote='\0';bool active=false;
            for(int i=0;i<text.Length;i++) {
                char ch=text[i];
                if(quote=='\'') {if(ch=='\'')quote='\0';else word.Append(ch);active=true;continue;}
                if(ch=='"') {if(quote=='"')quote='\0';else quote='"';active=true;continue;}
                if(ch=='\'' && quote=='\0') {quote='\'';active=true;continue;}
                if(ch=='\\') {
                    if(i+1>=text.Length)throw new IOException("Incomplete native launch command escape.");
                    char next=text[i+1];
                    if(quote=='"' && next!='\\' && next!='"' && next!='$' && next!='`' && next!='\n')word.Append(ch);
                    else {i++;if(next!='\n')word.Append(next);}active=true;continue;
                }
                if(Char.IsWhiteSpace(ch) && quote=='\0') {if(active){result.Add(word.ToString());word.Clear();active=false;}continue;}
                if(ch=='#' && quote=='\0')throw new IOException("Comments in custom native launch commands are unsupported.");
                word.Append(ch);active=true;
            }
            if(quote!='\0')throw new IOException("Incomplete native launch command quote.");
            if(active)result.Add(word.ToString());return result;
        }
        private static string CommandProfile(string game,string[] args,string nativeDefault,string selectedProfile) {
            string command=null;
            for(int i=0;i<args.Length;i++) {
                string value=null;
                if(args[i]=="--launch-cmd") {if(++i>=args.Length)throw new IOException("Missing custom launch command.");value=args[i];}
                else if(args[i].StartsWith("--launch-cmd=",StringComparison.Ordinal))value=args[i].Substring("--launch-cmd=".Length);
                if(value!=null) {if(command!=null)throw new IOException("Ambiguous custom launch command.");command=value;}
            }
            if(command==null)return String.Equals(selectedProfile,game,StringComparison.OrdinalIgnoreCase)?game:nativeDefault;
            var words=SplitNative(command);
            if(words.Count<1 || !String.Equals(BackupEngine.Canon(words[0]),Path.Combine(game,"noita.exe"),StringComparison.OrdinalIgnoreCase))
                throw new IOException("Automatic backup cannot establish the profile of this custom launch command. Only the configured native Noita executable is supported.");
            if(words.Count==1)return nativeDefault;
            if(words.Count==2 && words[1]=="-always_store_userdata_in_workdir")return game;
            throw new IOException("Automatic backup cannot establish the profile of this custom launch command. Only the verified native workdir-userdata flag is supported.");
        }
        private static Plan Select(string root, string game, string[] args) {
            CheckStopped(root);
            var config = Setup.Stored(root);
            string proxy = Path.Combine(root, "current", "proxy");
            Program.ValidateForwarded(root, game, proxy, args);
            foreach (string arg in args)
                if (arg == "--run-noita-with-gdb")
                    throw new IOException("Automatic backup cannot establish the save profile of a custom launch command. Open CoopPlus.exe normally with its configured paths; nothing was launched or reset.");
            var p = new Plan { launcher = BackupEngine.Canon(root), game = BackupEngine.Canon(game),
                settings = Effective(args,"--settings-path",config.settingsPath,proxy),
                state = Effective(args,"--save-state-path",config.saveStatePath,proxy) };
            Program.ValidateMutable(root, game, p.settings); Program.ValidateMutable(root, game, p.state);
            BackupEngine.NoLinks(p.settings);
            BackupEngine.NoLinks(p.state);
            string nativeDefault=NativeProfile();
            var first=ReadFirstUse(root);
            bool fresh=first!=null && first.phase!="launch-attempted" && Matches(first,root,game,p.settings,p.state,nativeDefault);
            if(fresh) {
                NoFreshCommands(args);
                if(Directory.Exists(p.state) || File.Exists(p.state) || Directory.Exists(p.settings))throw new IOException("First-use proxy sources appeared unexpectedly; no initialization or reset was attempted.");
                if(first.phase=="prepared" && File.Exists(p.settings))throw new IOException("First-use settings appeared before initialization; they were preserved.");
                if(first.phase=="initializing")VerifyInitializationInputs(first);
                CheckFirstProfile(nativeDefault);p.neverInitialized=true;
            } else if(!File.Exists(p.settings))throw new IOException("Configured proxy settings are missing and first-use eligibility is unavailable or already consumed. A legacy updater cannot prove fresh setup from absences. Use the new complete bootstrap for a genuinely new setup; preserve established data for review: "+p.settings);
            string selectedProfile=fresh ? nativeDefault : RonSelection.Profile(File.ReadAllText(p.settings, new UTF8Encoding(false,true)),true);
            p.profile = selectedProfile==null ? nativeDefault : BackupEngine.Canon(selectedProfile);
            string childProfile=CommandProfile(p.game,args,nativeDefault,p.profile);
            if(!String.Equals(p.profile,childProfile,StringComparison.OrdinalIgnoreCase))
                throw new IOException("Configured proxy profile does not match the frozen native game launch (Windows profile or portable game folder). Automatic backup refused; no profile was substituted.");
            BackupEngine.NoLinks(p.profile); Program.ValidateMutable(root, game, p.profile);
            if (!fresh && (!Directory.Exists(Path.Combine(p.profile,"save00")) || !Directory.Exists(Path.Combine(p.profile,"save_shared"))))
                throw new IOException("Configured Noita profile is incomplete (save00 and save_shared required). Nothing was launched: " + p.profile);
            foreach (string alternate in new [] { Path.Combine(game,"save00"), Path.Combine(game,"save_shared") })
                if (Directory.Exists(alternate) && !BackupEngine.Within(alternate,p.profile))
                    throw new IOException("Portable and configured Noita saves conflict. Automatic backup refused; no default profile was substituted.");
            if (!fresh && !Directory.Exists(p.state)) throw new IOException("Configured proxy state folder is missing. Automatic backup refused; first-use initialization will not be reissued: " + p.state);
            p.emptyProxyState = !fresh && Directory.GetFileSystemEntries(p.state).Length == 0;
            p.role = fresh ? "Explicit never-initialized proxy sources; genuine absences recorded, no host/run state" : (p.emptyProxyState ? "Existing empty proxy state preserved; this is not a verified host checkpoint" : "Existing nonempty proxy state preserved; host health is unproven");
            var installed = Engine.ReadInstalled(root);
            p.release = "Co-op+ " + installed.version + " build " + installed.build + "; automatic backup BEFORE first gameplay launch; runtime install may already have completed in an older updater";
            if(String.Equals(p.profile,p.game,StringComparison.OrdinalIgnoreCase)) {
                p.sources.Add(new Source { name="noita-profile/save00",path=Path.Combine(p.profile,"save00"),directory=true });
                p.sources.Add(new Source { name="noita-profile/save_shared",path=Path.Combine(p.profile,"save_shared"),directory=true });
                string stats=Path.Combine(p.profile,"save_stats");BackupEngine.NoLinks(stats);
                if(File.Exists(stats))throw new IOException("Configured portable save_stats is not a directory; nothing was launched.");
                if(Directory.Exists(stats))p.sources.Add(new Source {name="noita-profile/save_stats",path=stats,directory=true});
            } else if(Directory.Exists(p.profile))p.sources.Add(new Source { name="noita-profile",path=p.profile,directory=true });
            else p.absentSources.Add(new Source {name="noita-profile",path=p.profile,directory=true});
            if(fresh) {
                if(Directory.Exists(p.profile))foreach(string name in new[]{"save00","save_shared"})if(!Directory.Exists(Path.Combine(p.profile,name)))p.absentSources.Add(new Source {name="noita-profile/"+name,path=Path.Combine(p.profile,name),directory=true});
                p.absentSources.Add(new Source {name="proxy-state",path=p.state,directory=true});
                if(File.Exists(p.settings))p.sources.Add(new Source {name="proxy-settings/proxy.ron",path=p.settings,directory=false});else p.absentSources.Add(new Source {name="proxy-settings/proxy.ron",path=p.settings,directory=false});
            } else {
                p.sources.Add(new Source { name="proxy-state",path=p.state,directory=true });
                p.sources.Add(new Source { name="proxy-settings/proxy.ron",path=p.settings,directory=false });
            }
            p.sources.Add(new Source { name="launcher/coopplus.json",path=Path.Combine(root,"coopplus.json"),directory=false });
            string backupConfig=Path.Combine(root,"automatic-backup.json");
            if(File.Exists(backupConfig))p.sources.Add(new Source {name="launcher/automatic-backup.json",path=backupConfig,directory=false});
            string firstUse=FirstUsePath(root);if(File.Exists(firstUse))p.sources.Add(new Source {name="launcher/first-use.json",path=firstUse,directory=false});
            p.sources.Add(new Source { name="runtime/installed.json",path=Path.Combine(root,"current","installed.json"),directory=false });
            // Old runtime copies are retained by Engine.Install. Capture their
            // release identities, never copy licensed executable files here.
            string rollback = Path.Combine(root,"rollback");
            if (Directory.Exists(rollback)) foreach (string dir in Directory.GetDirectories(rollback).OrderBy(x=>x,StringComparer.OrdinalIgnoreCase)) {
                BackupEngine.NoLinks(dir);
                string previous = Path.Combine(dir,"current","installed.json");
                if (File.Exists(previous)) p.sources.Add(new Source { name="prior-runtime/"+Path.GetFileName(dir)+"/installed.json",path=previous,directory=false });
            }
            return p;
        }
        private static string SelectionKey(Plan p) {
            return HashText(String.Join("\n",new [] {p.launcher,p.game,p.settings,p.state,p.profile}.Select(x=>x.ToUpperInvariant())));
        }
        private static FirstUseWrite[] ExpectedFirstWrites(FirstUseProof proof) {
            return new[]{
                new FirstUseWrite {path=proof.settings,contents="(paths:(noita_save:Some("+RonLiteral(proof.profile)+")))\n"},
                new FirstUseWrite {path=Path.Combine(proof.profile,"save_shared","config.xml"),contents="<Config mods_sandbox_enabled=\"0\" single_threaded_loading=\"0\" />\n"},
                new FirstUseWrite {path=Path.Combine(proof.profile,"save00","mod_config.xml"),contents="<Mods><Mod name=\"quant.ew\" enabled=\"1\" settings_fold_open=\"0\" workshop_item_id=\"0\" /></Mods>\n"}
            };
        }
        internal static string RonLiteral(string text) {
            var result=new StringBuilder("\"");
            for(int i=0;i<text.Length;i++) {
                char ch=text[i];
                switch(ch) {case '\\':result.Append("\\\\");break;case '"':result.Append("\\\"");break;case '\n':result.Append("\\n");break;case '\r':result.Append("\\r");break;case '\t':result.Append("\\t");break;case '\0':result.Append("\\0");break;
                    default:
                        if(Char.IsHighSurrogate(ch)) {if(i+1>=text.Length || !Char.IsLowSurrogate(text[i+1]))throw new IOException("Path contains an invalid Unicode scalar.");result.Append(ch).Append(text[++i]);}
                        else if(Char.IsLowSurrogate(ch))throw new IOException("Path contains an invalid Unicode scalar.");
                        else if(Char.IsControl(ch))result.Append("\\u{").Append(((int)ch).ToString("x")).Append('}');
                        else result.Append(ch);break;
                }
            }
            return result.Append('"').ToString();
        }
        private static string ProfilePhysical(FirstUseProof proof,string logical) {
            return logical=="noita-profile"?proof.profile:Path.Combine(proof.profile,logical.Substring("noita-profile/".Length).Replace('/',Path.DirectorySeparatorChar));
        }
        private static void VerifyInitializationInputs(FirstUseProof proof) {
            if(String.IsNullOrWhiteSpace(proof.backupDirectory) || proof.writes==null)throw new IOException("Interrupted first-use intent lacks its verified snapshot; no initialization was reissued.");
            VerifyCompleted(proof.backupDirectory,proof.manifestSha256,proof.restoreSha256);
            var manifest=Json.Deserialize<Manifest>(File.ReadAllText(Path.Combine(proof.backupDirectory,"backup.json"),new UTF8Encoding(false,true)));
            var selected=manifest.selection;
            if(selected==null || !selected.neverInitialized || !Matches(proof,selected.launcher,selected.game,selected.settings,selected.state,selected.profile) ||
                !manifest.files.Any(f=>f.path=="proxy-state" && f.absent) || !manifest.files.Any(f=>f.path=="proxy-settings/proxy.ron" && f.absent))throw new IOException("First-use intent does not match a genuine-absence snapshot.");
            var originals=new Dictionary<string,Entry>(StringComparer.OrdinalIgnoreCase);
            var allowedDirectories=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach(var entry in manifest.files.Where(f=>f.path=="noita-profile" || f.path.StartsWith("noita-profile/",StringComparison.Ordinal))) {
                string target=BackupEngine.Canon(ProfilePhysical(proof,entry.path));BackupEngine.NoLinks(target);
                if(entry.absent)continue;
                originals.Add(target,entry);
                if(entry.directory) {if(!Directory.Exists(target))throw new IOException("Original Noita directory changed after first-use backup: "+target);allowedDirectories.Add(target);}
                else if(!File.Exists(target) || new FileInfo(target).Length!=entry.size || Engine.Sha256(target)!=entry.sha256)throw new IOException("Original Noita data changed after first-use backup; no initialization was attempted: "+target);
            }
            var expected=ExpectedFirstWrites(proof).ToDictionary(w=>BackupEngine.Canon(w.path),StringComparer.OrdinalIgnoreCase);
            var writes=new Dictionary<string,FirstUseWrite>(StringComparer.OrdinalIgnoreCase);
            foreach(var write in proof.writes) {
                if(write==null || String.IsNullOrEmpty(write.path))throw new IOException("Invalid first-use write intent.");
                string target=BackupEngine.Canon(write.path);FirstUseWrite template;
                if(!expected.TryGetValue(target,out template) || write.contents!=template.contents || write.sha256!=HashText(template.contents) || originals.ContainsKey(target) || writes.ContainsKey(target))throw new IOException("First-use intent would replace original data; it was refused.");
                Program.Guard(target);writes.Add(target,write);
                if(Directory.Exists(target) || File.Exists(target) && Engine.Sha256(target)!=write.sha256)throw new IOException("Interrupted first-use preference differs from its staged hash; it was preserved: "+target);
                for(string parent=Path.GetDirectoryName(target);parent!=null && BackupEngine.Within(parent,proof.profile);parent=Path.GetDirectoryName(parent))allowedDirectories.Add(parent);
            }
            if(Directory.Exists(proof.state) || File.Exists(proof.state))throw new IOException("Proxy state appeared before the first proxy launch; no initialization was reissued.");
            if(Directory.Exists(proof.settings) || File.Exists(proof.settings) && !writes.ContainsKey(BackupEngine.Canon(proof.settings)))throw new IOException("First-use proxy settings changed outside the staged initialization.");
            if(Directory.Exists(proof.profile))VerifyFirstProfileTree(proof.profile,originals,writes,allowedDirectories);
        }
        private static void VerifyFirstProfileTree(string folder,Dictionary<string,Entry> originals,Dictionary<string,FirstUseWrite> writes,HashSet<string> directories) {
            BackupEngine.NoLinks(folder);
            if(!directories.Contains(BackupEngine.Canon(folder)))throw new IOException("Unexpected Noita directory appeared after first-use backup: "+folder);
            foreach(string entry in Directory.GetFileSystemEntries(folder)) {
                BackupEngine.NoLinks(entry);string target=BackupEngine.Canon(entry);
                if(Directory.Exists(entry))VerifyFirstProfileTree(entry,originals,writes,directories);
                else if(!originals.ContainsKey(target) && !writes.ContainsKey(target))throw new IOException("Unexpected Noita data appeared after first-use backup; it was preserved: "+entry);
            }
        }
        // Check-only never calls this. The intent is durable before absent-only
        // preference creation, and consumed before Program can dispatch a child.
        internal static string PrepareLaunch(string root,string game,string[] args) {
            string backup=Ensure(root,game,args);var proof=ReadFirstUse(root);
            if(proof==null || proof.phase=="launch-attempted")return backup;
            var config=Setup.Stored(root);string proxy=Path.Combine(root,"current","proxy");
            if(!Matches(proof,root,game,Effective(args,"--settings-path",config.settingsPath,proxy),Effective(args,"--save-state-path",config.saveStatePath,proxy),NativeProfile()))throw new IOException("First-use launch paths differ from the recorded setup; nothing was initialized.");
            CheckStopped(root);NoFreshCommands(args);
            if(proof.phase=="prepared") {
                proof.backupDirectory=backup;proof.manifestSha256=Engine.Sha256(Path.Combine(backup,"backup.json"));proof.restoreSha256=Engine.Sha256(Path.Combine(backup,"RESTORE.txt"));
                proof.writes=ExpectedFirstWrites(proof).Where(write=>!File.Exists(write.path)).Select(write=>{write.sha256=HashText(write.contents);return write;}).ToArray();
                VerifyInitializationInputs(proof);proof.phase="initializing";WriteFirstUse(root,proof);
                if(FirstUseFault!=null)FirstUseFault("first-use-intent");
            } else if(!String.Equals(BackupEngine.Canon(backup),BackupEngine.Canon(proof.backupDirectory),StringComparison.OrdinalIgnoreCase))throw new IOException("Interrupted first-use initialization no longer matches its original backup.");
            VerifyInitializationInputs(proof);
            foreach(var write in proof.writes) {
                CheckStopped(root);Program.Guard(write.path);
                if(File.Exists(write.path))continue;
                Directory.CreateDirectory(Path.GetDirectoryName(write.path));
                byte[] bytes=Encoding.UTF8.GetBytes(write.contents);
                using(var output=new FileStream(write.path,FileMode.CreateNew,FileAccess.Write,FileShare.None)){output.Write(bytes,0,bytes.Length);output.Flush(true);}
                if(Engine.Sha256(write.path)!=write.sha256)throw new IOException("First-use preference write did not verify; intent and backup retained.");
                if(FirstUseFault!=null)FirstUseFault("first-use-written");
            }
            VerifyInitializationInputs(proof);CheckStopped(root);proof.phase="launch-attempted";WriteFirstUse(root,proof);
            Status("First-use preferences initialized after verified backup. The real proxy will create its own state; first-use eligibility will not be reissued.");return backup;
        }
        internal static void VerifyCompleted(string directory, string manifestSha256, string restoreSha256) {
            BackupEngine.NoLinks(directory);
            string path = Path.Combine(directory,"backup.json"); BackupEngine.NoLinks(path);
            if (!File.Exists(path) || !String.Equals(Engine.Sha256(path),manifestSha256,StringComparison.OrdinalIgnoreCase))
                throw new IOException("Automatic backup manifest is missing or changed. Nothing was launched: " + directory);
            string restore=Path.Combine(directory,"RESTORE.txt");BackupEngine.NoLinks(restore);
            if(!File.Exists(restore) || String.IsNullOrEmpty(restoreSha256) || !String.Equals(Engine.Sha256(restore),restoreSha256,StringComparison.OrdinalIgnoreCase))
                throw new IOException("Automatic backup recovery instructions are missing or changed. Nothing was launched: "+directory);
            var manifest = Json.Deserialize<Manifest>(File.ReadAllText(path,new UTF8Encoding(false,true)));
            if (manifest == null || manifest.schema != 1 || manifest.files == null || manifest.files.Length == 0)
                throw new IOException("Automatic backup manifest is incomplete.");
            var expected = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string data = Path.Combine(directory,"data"), prefix=Path.GetFullPath(data).TrimEnd('\\')+"\\";
            foreach (var f in manifest.files) {
                if (f == null || String.IsNullOrEmpty(f.path) || f.path.Contains("\\") || f.path.Contains(":") || f.path.Split('/').Any(x=>x=="" || x=="." || x=="..")) throw new IOException("Unsafe backup manifest path.");
                string target=Path.GetFullPath(Path.Combine(data,f.path.Replace('/',Path.DirectorySeparatorChar)));
                if (!target.StartsWith(prefix,StringComparison.OrdinalIgnoreCase) || !seen.Add(target)) throw new IOException("Unsafe or duplicate backup path.");
                BackupEngine.NoLinks(target);
                if(f.absent) {if(File.Exists(target) || Directory.Exists(target))throw new IOException("Backup fabricated a recorded source absence: "+target);continue;}
                expected.Add(target);
                if (f.directory) { if(!Directory.Exists(target))throw new IOException("Backup directory is missing: "+target); }
                else if (!File.Exists(target) || new FileInfo(target).Length!=f.size || !String.Equals(Engine.Sha256(target),f.sha256,StringComparison.OrdinalIgnoreCase))
                    throw new IOException("Automatic backup copy verification failed. Nothing was launched: "+target);
            }
            var allowed=new HashSet<string>(expected,StringComparer.OrdinalIgnoreCase) {Path.GetFullPath(directory),Path.GetFullPath(data),Path.GetFullPath(path),Path.GetFullPath(Path.Combine(directory,"RESTORE.txt"))};
            foreach(string target in expected) for(string parent=Path.GetDirectoryName(target);parent!=null && parent.StartsWith(prefix,StringComparison.OrdinalIgnoreCase);parent=Path.GetDirectoryName(parent))allowed.Add(parent);
            VerifyOnlyExpected(directory,allowed);
        }
        private static void VerifyOnlyExpected(string directory,HashSet<string> allowed) {
            BackupEngine.NoLinks(directory);
            foreach(string entry in Directory.GetFileSystemEntries(directory)) {
                BackupEngine.NoLinks(entry);
                if(!allowed.Contains(Path.GetFullPath(entry)))throw new IOException("Unexpected entry in automatic backup: "+entry);
                if(Directory.Exists(entry))VerifyOnlyExpected(entry,allowed);
            }
        }
        internal static string Ensure(string root, string game, string[] args) {
            CheckStopped(root); Program.Guard(root);
            var installed=Engine.VerifyInstalled(root,game);
            Plan plan=Select(root,game,args);
            var config=Setup.Stored(root);
            string destination=Destination(root,config);
            BackupEngine.ValidateDestination(plan,destination);
            string key=SelectionKey(plan)+"-"+HashText(BackupEngine.Canon(destination).ToUpperInvariant()).Substring(0,12);
            string receipts=Path.Combine(root,"automatic-backup-receipts"), receiptPath=Path.Combine(receipts,"build-"+installed.build+"-"+key+".json");
            Program.Guard(receiptPath);
            var first=ReadFirstUse(root);
            if(first!=null && first.phase=="initializing" && !File.Exists(receiptPath))throw new IOException("Interrupted first-use initialization is missing its original backup receipt; no initialization was reissued.");
            if (File.Exists(receiptPath)) {
                var receipt=Json.Deserialize<Receipt>(File.ReadAllText(receiptPath,new UTF8Encoding(false,true)));
                if (receipt==null || receipt.schema!=1 || receipt.selectionKey!=key || receipt.packageSha256!=installed.package.sha256)
                    throw new IOException("Automatic backup receipt does not match this release and selected paths. Nothing was launched.");
                string completed=BackupEngine.Canon(receipt.directory);
                if (!BackupEngine.Within(completed,BackupEngine.Canon(destination))) throw new IOException("Automatic backup receipt points outside the configured backup location.");
                VerifyCompleted(completed,receipt.manifestSha256,receipt.restoreSha256); CheckStopped(root);
                return completed;
            }
            Status("Protecting Noita profile, proxy state and settings before first launch of build " + installed.build + "...");
            // The standalone engine normally refuses all launchers. Here the
            // current owning updater/bootstrap are expected; game/proxy refusal
            // still runs before inventory, each source/file and completion.
            var previousBusy=BackupEngine.BusyCheck;
            string completedBackup;
            try {
                BackupEngine.BusyCheck=()=>CheckStopped(root);
                completedBackup=BackupEngine.RunSelected(()=>Select(root,game,args),destination,CancellationToken.None,Status);
            } finally { BackupEngine.BusyCheck=previousBusy; }
            string manifest=Path.Combine(completedBackup,"backup.json");
            string manifestHash=Engine.Sha256(manifest),restoreHash=Engine.Sha256(Path.Combine(completedBackup,"RESTORE.txt")); VerifyCompleted(completedBackup,manifestHash,restoreHash); CheckStopped(root);
            Directory.CreateDirectory(receipts); Program.Guard(receiptPath);
            string temp=receiptPath+".new-"+Guid.NewGuid().ToString("N");
            try {
                var receipt=new Receipt {packageSha256=installed.package.sha256,selectionKey=key,directory=completedBackup,manifestSha256=manifestHash,restoreSha256=restoreHash,createdUtc=DateTime.UtcNow.ToString("o")};
                using(var stream=new FileStream(temp,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                    byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(receipt));stream.Write(bytes,0,bytes.Length);stream.Flush(true);
                }
                File.Move(temp,receiptPath);
            } finally { if(File.Exists(temp))File.Delete(temp); }
            Status("Verified automatic backup: "+completedBackup+". Code recovery does not undo saved-world changes; paired saves must be restored together only in a reviewed recovery.");
            return completedBackup;
        }
    }
}
