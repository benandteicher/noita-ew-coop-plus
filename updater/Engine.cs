// Co-op+ update format 1. Uses only Windows 11's .NET Framework 4.8 libraries.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace CoopPlus {
    public sealed class PackageInfo { public string name; public long size; public string sha256; public string url; }
    public sealed class FileInfoEntry { public string path; public long size; public string sha256; }
    public sealed class ReleaseManifest {
        public int schema; public string channel; public string version; public long build;
        public int minBootstrap; public string sourceRevision; public string officialProxyVersion;
        public PackageInfo package; public FileInfoEntry[] files;
    }
    public sealed class UnsupportedBootstrapException : Exception { public UnsupportedBootstrapException(string s) : base(s) {} }

    public static class Engine {
        public const int BootstrapVersion = 1;
        public const long MaxPackageBytes = 256L * 1024 * 1024;
        public const long MaxExpandedBytes = 1024L * 1024 * 1024;
        // Tests inject failures without adding a production command-line fault switch.
        public static Action<string> Fault;
        public static Action<string> Progress = Console.WriteLine;
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024, RecursionLimit = 30 };
        static readonly Regex HashPattern = new Regex("^[a-fA-F0-9]{64}$");
        static readonly Regex RevisionPattern = new Regex("^[a-fA-F0-9]{40}$");
        static readonly Regex VersionPattern = new Regex("^[0-9]+(\\.[0-9]+){1,3}$");
        static readonly Regex Reserved = new Regex("^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])($|\\.)", RegexOptions.IgnoreCase);
        sealed class Journal { public int schema; public string root; public string game; public string id; public bool hadMod; public bool hadCurrent; public bool committed; }

        public static ReleaseManifest ParseManifest(string json) {
            if (json == null || json.Length > 8 * 1024 * 1024) throw new InvalidDataException("Release metadata is too large.");
            ReleaseManifest m;
            try { m = Json.Deserialize<ReleaseManifest>(json); }
            catch (Exception e) { throw new InvalidDataException("Invalid release metadata JSON.", e); }
            ValidateManifest(m); return m;
        }
        public static string SerializeManifest(ReleaseManifest m) { ValidateManifest(m); return Json.Serialize(m); }
        public static int CompareVersions(string a, string b) {
            return ParseVersion(a).CompareTo(ParseVersion(b));
        }
        static Version ParseVersion(string s) {
            Version v;
            if (s == null || !VersionPattern.IsMatch(s) || !Version.TryParse(s, out v)) throw new InvalidDataException("Version must contain two to four numeric components.");
            return new Version(v.Major, v.Minor, Math.Max(0,v.Build), Math.Max(0,v.Revision));
        }
        public static void ValidateManifest(ReleaseManifest m) {
            if (m == null || m.schema != 1) throw new InvalidDataException("Unsupported release metadata format. Obtain a newer Co-op+ bootstrap.");
            if (m.minBootstrap < 1) throw new InvalidDataException("Invalid minimum bootstrap version.");
            if (m.minBootstrap > BootstrapVersion) throw new UnsupportedBootstrapException("This release requires Co-op+ bootstrap " + m.minBootstrap + "; installed bootstrap is " + BootstrapVersion + ". Obtain the supported bootstrap before updating.");
            ParseVersion(m.version);
            if (m.build < 1 || (m.channel != "stable" && m.channel != "review") || m.sourceRevision == null || !RevisionPattern.IsMatch(m.sourceRevision)) throw new InvalidDataException("Invalid release identity.");
            ParseVersion(m.officialProxyVersion);
            if (m.package == null || !SafeLeaf(m.package.name) || !m.package.name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) || m.package.size < 1 || m.package.size > MaxPackageBytes || !ValidHash(m.package.sha256)) throw new InvalidDataException("Invalid package metadata.");
            Uri url;
            if (!Uri.TryCreate(m.package.url, UriKind.Absolute, out url) || url.Scheme != Uri.UriSchemeHttps || !String.IsNullOrEmpty(url.UserInfo)) throw new InvalidDataException("Package URL must use HTTPS without credentials.");
            if (m.files == null || m.files.Length == 0 || m.files.Length > 20000) throw new InvalidDataException("Invalid file manifest.");
            var paths = new HashSet<string>(StringComparer.OrdinalIgnoreCase); long total = 0;
            foreach (var f in m.files) {
                if (f == null || !SafePayloadPath(f.path) || f.size < 0 || f.size > MaxExpandedBytes || !ValidHash(f.sha256) || !paths.Add(f.path)) throw new InvalidDataException("Invalid or duplicate managed file path.");
                total = checked(total + f.size);
                if (total > MaxExpandedBytes) throw new InvalidDataException("Expanded package is too large.");
            }
            foreach (string required in new [] { "mod/quant.ew/mod.xml", "mod/quant.ew/init.lua", "mod/quant.ew/ewext.dll", "mod/quant.ew/NoitaPatcher/noitapatcher.dll", "mod/quant.ew/NoitaPatcher/noitapatcher/nsew/nsew_native.dll", "proxy/noita_proxy.exe", "proxy/steam_api64.dll", "updater/CoopUpdater.exe" }) if (!paths.Contains(required)) throw new InvalidDataException("Missing required managed file: " + required);
            // Files cannot also be a parent directory of another entry.
            foreach (string path in paths) {
                int slash = path.IndexOf('/');
                while (slash >= 0) { if (paths.Contains(path.Substring(0, slash))) throw new InvalidDataException("File/directory path collision."); slash = path.IndexOf('/', slash + 1); }
            }
        }
        static bool ValidHash(string s) { return s != null && HashPattern.IsMatch(s); }
        static bool SafeLeaf(string s) { return s != null && s.Length > 0 && s.Length <= 180 && s.IndexOf('/') < 0 && s.IndexOf('\\') < 0 && SafeSegments(s); }
        static bool SafeSegments(string s) {
            if (String.IsNullOrEmpty(s) || s.Length > 240 || s.IndexOf('\\') >= 0 || s.IndexOf(':') >= 0 || s.IndexOfAny(Path.GetInvalidPathChars()) >= 0 || s.StartsWith("/")) return false;
            foreach (string p in s.Split('/')) if (p.Length == 0 || p == "." || p == ".." || p.EndsWith(".") || p.EndsWith(" ") || p.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || Reserved.IsMatch(p)) return false;
            return true;
        }
        static bool SafePayloadPath(string s) {
            return SafeSegments(s) && (s.StartsWith("mod/quant.ew/", StringComparison.Ordinal) || s.StartsWith("proxy/", StringComparison.Ordinal) || s == "updater/CoopUpdater.exe");
        }
        public static string Sha256(string path) { using (var input = File.OpenRead(path)) using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "").ToLowerInvariant(); }
        static bool Same(string a, string b) { return String.Equals(a,b,StringComparison.OrdinalIgnoreCase); }
        static string Canon(string path) { string p=Path.GetFullPath(path); string volume=Path.GetPathRoot(p); return p.Length>volume.Length?p.TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar):p; }
        static bool Inside(string child, string parent) { return child.StartsWith(parent + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase); }
        public static void AssertNoReparse(string path) {
            string p = Path.GetFullPath(path);
            while (!String.IsNullOrEmpty(p)) {
                if ((File.Exists(p) || Directory.Exists(p)) && (File.GetAttributes(p) & FileAttributes.ReparsePoint) != 0) throw new IOException("Links/reparse points are not permitted in managed installation paths: " + p);
                string parent = Path.GetDirectoryName(p); if (Same(parent,p)) break; p=parent;
            }
        }
        static void InspectTree(string dir) {
            AssertNoReparse(dir); if (!Directory.Exists(dir)) return;
            foreach (string p in Directory.EnumerateFileSystemEntries(dir)) {
                var a = File.GetAttributes(p);
                if ((a & FileAttributes.ReparsePoint) != 0) throw new IOException("Managed directory contains a link/reparse point: " + p);
                if ((a & FileAttributes.Directory) != 0) InspectTree(p);
            }
        }
        public static void ValidateInstallPaths(string root, string gameDir) {
            root = Canon(root); gameDir = Canon(gameDir);
            if (Same(root,Path.GetPathRoot(root)) || Same(gameDir,Path.GetPathRoot(gameDir))) throw new IOException("Choose a named installation folder, not a drive root.");
            if (Same(root, gameDir) || Inside(root, gameDir) || Inside(gameDir, root)) throw new IOException("Co-op+ data must be separate from the game directory.");
            foreach (string start in new [] {root,gameDir}) {
                AssertNoReparse(start);
                string p=start;
                while (!String.IsNullOrEmpty(p)) {
                    if (Directory.Exists(Path.Combine(p,".git")) || File.Exists(Path.Combine(p,".git"))) throw new IOException("Updating inside a source checkout is forbidden: " + start);
                    p=Path.GetDirectoryName(p);
                }
            }
            InspectTree(Path.Combine(root,"current")); InspectTree(Path.Combine(gameDir,"mods","quant.ew"));
            AssertNoReparse(Path.Combine(gameDir,"mods")); AssertNoReparse(Path.Combine(gameDir,".coopplus-backups")); AssertNoReparse(Path.Combine(root,"rollback"));
        }
        public static bool IsBusy() {
            foreach (string name in new [] {"noita","noita_dev","noita_proxy"}) {
                foreach (var p in Process.GetProcessesByName(name)) { using(p) { try { if (!p.HasExited) return true; } catch { return true; } } }
            }
            return false;
        }
        public static void CheckNotBusy() { if (IsBusy()) throw new IOException("Close Noita and the official proxy before using Co-op+. No processes were stopped."); }
        static void CheckUnlocked(string path) {
            if (!Directory.Exists(path)) return;
            foreach (string f in Directory.EnumerateFiles(path,"*",SearchOption.AllDirectories)) using (File.Open(f,FileMode.Open,FileAccess.Read,FileShare.None)) {}
        }
        public static void StageDownload(string url, string dest, long expectedSize, string expectedHash) {
            Uri uri;
            if (!Uri.TryCreate(url, UriKind.Absolute,out uri) || uri.Scheme != "https" || !String.IsNullOrEmpty(uri.UserInfo) || expectedSize < 1 || expectedSize > MaxPackageBytes || !ValidHash(expectedHash)) throw new InvalidDataException("Invalid download metadata.");
            AssertNoReparse(dest); Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(dest)));
            if (File.Exists(dest)) throw new IOException("Download staging destination already exists.");
            string partial=dest+".partial-"+Guid.NewGuid().ToString("N");
            try {
                ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
                var watch = Stopwatch.StartNew();
                var request=(HttpWebRequest)WebRequest.Create(uri); request.Timeout=15000; request.ReadWriteTimeout=15000; request.AllowAutoRedirect=true; request.MaximumAutomaticRedirections=5; request.UserAgent="Noita-CoopPlus-Updater/1";
                using(var response=(HttpWebResponse)request.GetResponse()) {
                    if (response.ResponseUri.Scheme != "https") throw new InvalidDataException("Insecure download redirect rejected.");
                    if(response.ContentLength >= 0 && response.ContentLength != expectedSize) throw new InvalidDataException("Download size differs from release metadata.");
                    using(var input=response.GetResponseStream()) using(var output=new FileStream(partial,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                        byte[] buf=new byte[64*1024]; long total=0; int n; long last=0;
                        while((n=input.Read(buf,0,buf.Length)) > 0) {
                            total+=n; if(total>expectedSize) throw new InvalidDataException("Download exceeds expected size.");
                            if(watch.ElapsedMilliseconds>180000) throw new TimeoutException("Download exceeded the three-minute limit.");
                            output.Write(buf,0,n);
                            if (total-last >= 4*1024*1024) { if(Progress!=null) Progress("Downloaded " + (total/(1024*1024)) + " / " + (expectedSize/(1024*1024)) + " MiB"); last=total; }
                        }
                        output.Flush(true); if(total != expectedSize) throw new InvalidDataException("Download was interrupted or truncated.");
                    }
                }
                if(!Same(Sha256(partial),expectedHash)) throw new InvalidDataException("Download SHA-256 does not match release metadata.");
                File.Move(partial,dest);
            } finally { if(File.Exists(partial)) File.Delete(partial); }
        }
        static void VerifyFile(string path,FileInfoEntry f) {
            AssertNoReparse(path);
            if(!File.Exists(path) || new System.IO.FileInfo(path).Length != f.size || !Same(Sha256(path),f.sha256)) throw new InvalidDataException("Managed file is missing or has incorrect size/hash: " + f.path);
        }
        static bool GeneratedModFile(string relative) {
            // Official 1.6.8 creates these per-peer cosmetics on every session (net.rs / player_cosmetics.rs).
            return Regex.IsMatch(relative,@"^files/system/player/tmp/([a-fA-F0-9]{16}(_(icon|dc|lukki|arrow|ping|cursor|knee|limb_a|limb_b|map|arm|cape|base))?\.(png|xml)|[a-fA-F0-9]{16}_ragdoll(\.txt|_(head|left_hand|left_arm|left_thigh|right_hand|right_arm|right_thigh|torso)\.png)|no_(crown|amulet|amulet_gem))$")
                || Regex.IsMatch(relative,@"^data/generated/sprite_uv_maps/mods\.quant\.ew\.files\.system\.player\.tmp\.[a-fA-F0-9]{16}\.png$");
        }
        static void Extract(string package,string stage,ReleaseManifest m) {
            if(new System.IO.FileInfo(package).Length != m.package.size || !Same(Sha256(package),m.package.sha256)) throw new InvalidDataException("Package size/SHA-256 mismatch.");
            var wanted=m.files.ToDictionary(f=>f.path,StringComparer.OrdinalIgnoreCase); var seen=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Directory.CreateDirectory(stage);
            using(var zip=ZipFile.OpenRead(package)) {
                if(zip.Entries.Count>40000) throw new InvalidDataException("Too many archive entries.");
                foreach(var entry in zip.Entries) {
                    string p=entry.FullName; bool directory=p.EndsWith("/",StringComparison.Ordinal);
                    string check=directory?p.Substring(0,p.Length-1):p;
                    uint attr=unchecked((uint)entry.ExternalAttributes); uint unixKind=(attr>>16)&0xF000;
                    if(!SafeSegments(check) || (attr&0x400)!=0 || unixKind==0xA000 || (unixKind!=0 && unixKind!=0x8000 && unixKind!=0x4000)) throw new InvalidDataException("Unsafe archive path/link: " + p);
                    if(directory) {
                        if(entry.Length!=0 || !wanted.Keys.Any(x=>x.StartsWith(p,StringComparison.OrdinalIgnoreCase))) throw new InvalidDataException("Unexpected archive directory: " + p);
                        continue;
                    }
                    FileInfoEntry f;
                    if(!wanted.TryGetValue(p,out f) || f.path != p || !seen.Add(p) || entry.Length != f.size) throw new InvalidDataException("Unexpected, duplicate or incorrectly sized archive file: " + p);
                    string target=Path.Combine(stage,p.Replace('/',Path.DirectorySeparatorChar));
                    if(!Inside(Canon(target),Canon(stage))) throw new InvalidDataException("Archive extraction escaped staging.");
                    Directory.CreateDirectory(Path.GetDirectoryName(target)); AssertNoReparse(target);
                    using(var input=entry.Open()) using(var output=new FileStream(target,FileMode.CreateNew,FileAccess.Write,FileShare.None)) {
                        byte[] buffer=new byte[65536]; long total=0; int n;
                        while((n=input.Read(buffer,0,buffer.Length))>0) { total+=n; if(total>f.size) throw new InvalidDataException("Expanded file exceeds manifest size."); output.Write(buffer,0,n); }
                        output.Flush(true); if(total != f.size) throw new InvalidDataException("Expanded file was truncated.");
                    }
                    VerifyFile(target,f);
                }
            }
            if(seen.Count != wanted.Count) throw new InvalidDataException("Package is incomplete.");
        }
        static void WriteJson(string path,object value) {
            AssertNoReparse(path); string tmp=path+".tmp-"+Guid.NewGuid().ToString("N");
            byte[] bytes=Encoding.UTF8.GetBytes(Json.Serialize(value));
            try { using(var f=new FileStream(tmp,FileMode.CreateNew,FileAccess.Write,FileShare.None)) { f.Write(bytes,0,bytes.Length); f.Flush(true); } if(File.Exists(path)) File.Replace(tmp,path,null); else File.Move(tmp,path); }
            finally { if(File.Exists(tmp)) File.Delete(tmp); }
        }
        static string Mod(string game) { return Path.Combine(game,"mods","quant.ew"); }
        static string Current(string root) { return Path.Combine(root,"current"); }
        static string StageRoot(string root,string id) { return Path.Combine(root,".stage-"+id); }
        static string StageMod(string game,string id) { return Path.Combine(game,".coopplus-stage-"+id,"quant.ew"); }
        static string BackupRoot(string root,string id) { return Path.Combine(root,"rollback",id); }
        static string BackupMod(string game,string id) { return Path.Combine(game,".coopplus-backups",id,"quant.ew"); }
        static void Trigger(string point) { if(Fault!=null) Fault(point); }
        public static ReleaseManifest ReadInstalled(string root) {
            string file=Path.Combine(Current(Canon(root)),"installed.json"); AssertNoReparse(file);
            return File.Exists(file)?ParseManifest(File.ReadAllText(file)):null;
        }
        public static ReleaseManifest VerifyInstalled(string root,string gameDir) {
            root=Canon(root); gameDir=Canon(gameDir); ValidateInstallPaths(root,gameDir);
            var m=ReadInstalled(root); if(m==null) throw new InvalidDataException("No complete Co-op+ installation is available.");
            var expectedMod=new HashSet<string>(StringComparer.OrdinalIgnoreCase); var expectedCurrent=new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "installed.json", "proxy/ew_log.txt", "proxy/ew_log_old.txt" };
            foreach(var f in m.files) {
                bool mod=f.path.StartsWith("mod/quant.ew/",StringComparison.Ordinal); string rel=mod?f.path.Substring("mod/quant.ew/".Length):f.path;
                VerifyFile(Path.Combine(mod?Mod(gameDir):Current(root),rel.Replace('/',Path.DirectorySeparatorChar)),f);
                (mod?expectedMod:expectedCurrent).Add(rel);
            }
            foreach(var pair in new [] {new {dir=Mod(gameDir), expected=expectedMod},new {dir=Current(root),expected=expectedCurrent}})
                foreach(string path in Directory.EnumerateFiles(pair.dir,"*",SearchOption.AllDirectories)) { string rel=path.Substring(pair.dir.Length+1).Replace('\\','/'); if(!pair.expected.Contains(rel) && !(Same(pair.dir,Mod(gameDir)) && GeneratedModFile(rel))) throw new InvalidDataException("Unexpected managed file: " + rel); }
            return m;
        }
        public static void Install(string root,string gameDir,string packagePath,ReleaseManifest manifest) {
            ValidateManifest(manifest); root=Canon(root); gameDir=Canon(gameDir); ValidateInstallPaths(root,gameDir); CheckNotBusy();
            Directory.CreateDirectory(root); Directory.CreateDirectory(gameDir);
            Recover(root,gameDir);
            string id=Guid.NewGuid().ToString("N"),stage=StageRoot(root,id),stageMod=StageMod(gameDir,id),stageCurrent=Path.Combine(stage,"current"),journalPath=Path.Combine(root,"install-journal.json");
            bool journalWritten=false;
            try {
                if(Progress!=null) Progress("Verifying and staging complete release " + manifest.version + " (build " + manifest.build + ")...");
                Extract(packagePath,Path.Combine(stage,"payload"),manifest);
                // Launcher data and Steam game installations commonly live on different volumes.
                // Copy only the verified manifest files to staging on the GAME volume, then hash again.
                Directory.CreateDirectory(stageMod);
                foreach(var f in manifest.files.Where(x=>x.path.StartsWith("mod/quant.ew/",StringComparison.Ordinal))) {
                    string source=Path.Combine(stage,"payload",f.path.Replace('/',Path.DirectorySeparatorChar));
                    string dest=Path.Combine(stageMod,f.path.Substring("mod/quant.ew/".Length).Replace('/',Path.DirectorySeparatorChar));
                    Directory.CreateDirectory(Path.GetDirectoryName(dest)); AssertNoReparse(dest);
                    using(var input=File.OpenRead(source)) using(var output=new FileStream(dest,FileMode.CreateNew,FileAccess.Write,FileShare.None)) { input.CopyTo(output); output.Flush(true); }
                    VerifyFile(dest,f);
                }
                Directory.CreateDirectory(stageCurrent);
                Directory.Move(Path.Combine(stage,"payload","proxy"),Path.Combine(stageCurrent,"proxy"));
                Directory.Move(Path.Combine(stage,"payload","updater"),Path.Combine(stageCurrent,"updater"));
                WriteJson(Path.Combine(stageCurrent,"installed.json"),manifest);
                ValidateInstallPaths(root,gameDir); InspectTree(stage); InspectTree(stageMod); CheckNotBusy(); CheckUnlocked(Mod(gameDir)); CheckUnlocked(Current(root));
                Directory.CreateDirectory(Path.Combine(gameDir,"mods")); Directory.CreateDirectory(Path.GetDirectoryName(BackupMod(gameDir,id))); Directory.CreateDirectory(BackupRoot(root,id));
                var j=new Journal { schema=1,root=root,game=gameDir,id=id,hadMod=Directory.Exists(Mod(gameDir)),hadCurrent=Directory.Exists(Current(root)) };
                WriteJson(journalPath,j); journalWritten=true;
                if(j.hadMod) Directory.Move(Mod(gameDir),BackupMod(gameDir,id)); Trigger("after-mod-backup");
                Directory.Move(stageMod,Mod(gameDir)); Trigger("after-mod-install");
                if(j.hadCurrent) Directory.Move(Current(root),Path.Combine(BackupRoot(root,id),"current")); Trigger("after-current-backup");
                Directory.Move(stageCurrent,Current(root)); Trigger("after-current-install");
                VerifyInstalled(root,gameDir); Trigger("before-commit");
                j.committed=true; WriteJson(journalPath,j); File.Delete(journalPath); journalWritten=false;
                if(Progress!=null) Progress("Complete installation ready. Prior managed files retained outside active mods.");
            } catch(Exception installError) {
                if(journalWritten) {
                    try { Recover(root,gameDir); }
                    catch(Exception recoveryError) { throw new IOException("Installation failed and automatic recovery could not finish. Do not launch. Keep install-journal.json and rollback directories; close any locking programs and run Co-op+ again. Install: " + installError.Message + " Recovery: " + recoveryError.Message,installError); }
                    throw new IOException("Update failed; the prior complete installation was restored. " + installError.Message,installError);
                }
                throw;
            } finally {
                // Only clean known, validated staging trees; never follow links during cleanup.
                if(!File.Exists(journalPath)) { SafeDeleteTree(stage); SafeDeleteTree(Path.GetDirectoryName(stageMod)); }
            }
        }
        static void SafeDeleteTree(string path) { if(Directory.Exists(path)) { InspectTree(path); Directory.Delete(path,true); } }
        static void RestoreOne(string active,string backup,string stage,bool hadOld,string failed) {
            if(Directory.Exists(backup)) {
                if(Directory.Exists(active)) { Directory.CreateDirectory(Path.GetDirectoryName(failed)); Directory.Move(active,failed); }
                Directory.CreateDirectory(Path.GetDirectoryName(active)); Directory.Move(backup,active);
            } else if(!hadOld && !Directory.Exists(stage) && Directory.Exists(active)) {
                Directory.CreateDirectory(Path.GetDirectoryName(failed)); Directory.Move(active,failed);
            } else if(hadOld && !Directory.Exists(active)) throw new IOException("Prior installation is missing both its active directory and rollback copy: " + active);
        }
        public static void Recover(string root,string gameDir) {
            root=Canon(root);gameDir=Canon(gameDir); ValidateInstallPaths(root,gameDir);
            string path=Path.Combine(root,"install-journal.json"); AssertNoReparse(path); if(!File.Exists(path)) return;
            CheckNotBusy(); Journal j;
            try { j=Json.Deserialize<Journal>(File.ReadAllText(path)); } catch(Exception e) { throw new IOException("Cannot read recovery journal. Keep installation closed for manual review.",e); }
            if(j==null || j.schema!=1 || !Same(j.root,root) || !Same(j.game,gameDir) || j.id==null || !Regex.IsMatch(j.id,"^[a-f0-9]{32}$")) throw new IOException("Recovery journal does not match this installation. Manual review required.");
            string stage=StageRoot(root,j.id),stageMod=StageMod(gameDir,j.id),backup=BackupRoot(root,j.id),backupMod=BackupMod(gameDir,j.id);
            InspectTree(stage);InspectTree(Path.GetDirectoryName(stageMod));InspectTree(backup);InspectTree(Path.GetDirectoryName(backupMod));
            if(j.committed) { VerifyInstalled(root,gameDir); File.Delete(path); SafeDeleteTree(stage); SafeDeleteTree(Path.GetDirectoryName(stageMod)); return; }
            CheckUnlocked(Mod(gameDir)); CheckUnlocked(Current(root));
            RestoreOne(Current(root),Path.Combine(backup,"current"),Path.Combine(stage,"current"),j.hadCurrent,Path.Combine(backup,"failed-current"));
            RestoreOne(Mod(gameDir),backupMod,stageMod,j.hadMod,Path.Combine(Path.GetDirectoryName(backupMod),"failed-quant.ew"));
            File.Delete(path); SafeDeleteTree(stage); SafeDeleteTree(Path.GetDirectoryName(stageMod));
            if(Progress!=null) Progress("Interrupted installation rolled back. The prior managed directories are restored.");
        }
    }
}
