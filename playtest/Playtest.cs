// Windows 11 / .NET Framework 4.8. Private Staging setup and run verification.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using System.Xml;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

namespace CoopPlusPlaytest {
    public sealed class Entry { public string path; public long size; public string sha256; }
    public sealed class Manifest {
        public int schema;
        public string channel, version, runtimeId;
        public long build;
        public Entry[] files, ownedGame;
    }
    public sealed class Ready {
        public int schema;
        public string channel, version, runtimeId, payloadHash, source;
        public long build;
        public Entry[] game;
    }
    public sealed class VerifiedPayload { public string package, payload; public Manifest manifest; public string hash; }
    public static class Playtest {
        public const string RuntimeId = "coopplus-staging-b23-v1.9.18.16";
        public const string Version = "1.9.18.16";
        public const long Build = 23;
        public const string Channel = "staging";
        internal const string PrivateName = "staging-private";
        internal const string PayloadName = "staging-payload";
        const int PayloadManifestLimit = 4 * 1024 * 1024;
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = PayloadManifestLimit };
        static readonly StringComparer CI = StringComparer.OrdinalIgnoreCase;
        public static Action<string> Progress = delegate { };
        public static Action<string> Fault;
        public static Func<bool> CancellationRequested = delegate { return false; };
        internal static void CheckPreparationCancellation() { if(CancellationRequested!=null&&CancellationRequested())throw new OperationCanceledException("Private preparation cancelled at a file boundary."); }
        public static long HashedBytes, HashedFiles;
        static string currentPhase = "Verifying files";
        public static Action<string,long,long> PhaseLog = delegate { };
        internal static void Phase(string name) { CheckPreparationCancellation(); currentPhase=name; Progress(name); PhaseLog(name, Interlocked.Read(ref HashedFiles), Interlocked.Read(ref HashedBytes)); }
        static readonly string[] MutableRoots = { "mods", "save00", "save_shared", "save_stats", "save_state", "proxy-save", "proxy", "private", "runtime", "userdata", "logs", "temp", "tmp", "crashes", "crash_reports", ".git", "tools_modding" };
        static readonly string[] EngineRootFiles = { "noita.exe", "noita_dev.exe", "config.xml", "fmod.dll", "fmodstudio.dll", "lua51.dll", "msvcp120.dll", "msvcr120.dll", "SDL2.dll", "steam_api.dll", "magic_numbers.xml", "_branch.txt", "_release_notes.txt", "_version_hash.txt", "README.txt" };
        public static string Canon(string p) {
            if (String.IsNullOrWhiteSpace(p) || !Regex.IsMatch(p, @"^[A-Za-z]:[\\/]")) throw new IOException("Choose an absolute local drive path.");
            if (p.Substring(2).Contains(":") || p.Contains("\0")) throw new IOException("Unsafe path.");
            string full = Path.GetFullPath(p).TrimEnd('\\');
            if (full.Length < 4) throw new IOException("A drive root is unsafe.");
            if (full.Substring(3).Split('\\').Any(x => x.EndsWith(".") || x.EndsWith(" ") || Regex.IsMatch(x, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])($|\.)", RegexOptions.IgnoreCase))) throw new IOException("Unsafe Windows path component.");
            for (string a = full; a != null; a = Path.GetDirectoryName(a)) {
                if ((File.Exists(a) || Directory.Exists(a)) && (File.GetAttributes(a) & FileAttributes.ReparsePoint) != 0) throw new IOException("Links, junctions and redirected folders are not supported: " + a);
            }
            return full;
        }
        public static bool Within(string child, string parent) { return (Canon(child) + "\\").StartsWith(Canon(parent) + "\\", StringComparison.OrdinalIgnoreCase); }
        internal static string Child(string root, string relative) {
            if (String.IsNullOrWhiteSpace(relative) || relative.Contains('\\') || relative.Contains(':') || relative.StartsWith("/") || relative.Split('/').Any(x => x == "" || x == "." || x == ".." || x.EndsWith(".") || x.EndsWith(" ") || Regex.IsMatch(x, @"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])($|\.)", RegexOptions.IgnoreCase))) throw new IOException("Unsafe manifest entry.");
            string result = Canon(Path.Combine(root, relative.Replace('/', '\\')));
            if (!Within(result, root)) throw new IOException("Escaping manifest entry.");
            return result;
        }
        public static string Hash(string path) { CheckPreparationCancellation(); Progress(currentPhase+"\n"+Path.GetFileName(path)+" · "+Interlocked.Read(ref HashedFiles)+" files · "+(Interlocked.Read(ref HashedBytes)/(1024*1024))+" MiB checked");using (var s = File.OpenRead(path)) using (var h = SHA256.Create()) { var result = BitConverter.ToString(h.ComputeHash(s)).Replace("-", "").ToLowerInvariant(); Interlocked.Add(ref HashedBytes, s.Position); Interlocked.Increment(ref HashedFiles); CheckPreparationCancellation(); return result; } }
        internal static string HashBytes(byte[] data) { using (var h = SHA256.Create()) return BitConverter.ToString(h.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
        static Entry Describe(string root, string file) { return new Entry { path = file.Substring(root.Length + 1).Replace('\\', '/'), size = new FileInfo(file).Length, sha256 = Hash(file) }; }
        internal static void Verify(string root, Entry[] entries) {
            if (entries == null || entries.Length == 0) throw new IOException("Incomplete manifest.");
            var seen = new HashSet<string>(CI);
            foreach (var e in entries) {
                string path = Child(root, e.path);
                if (!seen.Add(e.path) || e.size < 0 || !Regex.IsMatch(e.sha256 ?? "", "^[a-f0-9]{64}$") || !File.Exists(path) || new FileInfo(path).Length != e.size || Hash(path) != e.sha256) throw new IOException("Incomplete or changed private copy/payload: " + e.path + ". Nothing has been reset. Keep this folder and its logs for review.");
            }
        }
        public static Manifest ReadPayload(string package) {
            return LoadPayload(package).manifest;
        }
        public static VerifiedPayload LoadPayload(string package) {
            return LoadPayload(package, Child(package, PayloadName), Build, Version, StagingBundle.ManifestSha256);
        }
        internal static VerifiedPayload LoadPayload(string package, string payload, long build, string version, string manifestHash, bool verifyFiles = true) {
            Phase("Verifying frozen package");
            ValidatePackage(package);
            package = Canon(package);
            payload = Canon(payload);
            if (payload == package || !Within(payload, package)) throw new IOException("Staging payload is outside its launcher folder.");
            string metadata = Child(payload, "manifest.json");
            if (!File.Exists(metadata) || new FileInfo(metadata).Length > PayloadManifestLimit) throw new IOException("Missing payload manifest.");
            var m = VerifyManifest(File.ReadAllBytes(metadata), build, version, manifestHash);
            if (verifyFiles) {
                Verify(payload, m.files);
                var actual = Files(payload).Select(x => x.Substring(payload.Length + 1).Replace('\\', '/')).Where(x => x != "manifest.json");
                if (!new HashSet<string>(actual, CI).SetEquals(m.files.Select(x => x.path))) throw new IOException("Unlisted payload file.");
            }
            Phase("Frozen package verified");
            return new VerifiedPayload { package = package, payload = payload, manifest = m, hash = manifestHash };
        }
        internal static Manifest VerifyManifest(byte[] bytes, long build, string version, string manifestHash) {
            if (bytes.Length > PayloadManifestLimit || HashBytes(bytes) != manifestHash)
                throw new IOException("Staging payload manifest does not match the approved package.");
            var m = Json.Deserialize<Manifest>(new UTF8Encoding(false,true).GetString(bytes));
            if (m == null || m.schema != 1 || m.channel != Channel || m.build != build || m.version != version || m.runtimeId != StagingRelease.Runtime(m.build, m.version)) throw new IOException("This package is not the approved Staging release.");
            if (m.files == null || m.files.Length > 10000 || m.files.Any(e => e == null || e.path == null || !(e.path.StartsWith("bin/", StringComparison.Ordinal) || e.path.StartsWith("mod/quant.ew/", StringComparison.Ordinal)))) throw new IOException("Unexpected payload layout.");
            foreach (string required in new[] { "bin/noita_proxy.exe", "bin/steam_api64.dll", "mod/quant.ew/ewext.dll", "mod/quant.ew/init.lua" })
                if (!m.files.Any(x => x.path == required)) throw new IOException("Missing matching component: " + required);
            foreach (string required in new[] { "noita.exe", "data/data.wak", "lua51.dll", "SDL2.dll", "steam_api.dll" })
                if (m.ownedGame == null || !m.ownedGame.Any(x => x.path == required)) throw new IOException("Missing supported owned-game fingerprint: " + required);
            return m;
        }
        public static void ValidatePackage(string package) {
            package = Canon(package);
            for (string a = package; a != null; a = Path.GetDirectoryName(a)) {
                if (File.Exists(Path.Combine(a, ".git")) || Directory.Exists(Path.Combine(a, ".git"))) throw new IOException("Extract the playtest outside a source checkout.");
                if (File.Exists(Path.Combine(a, "CoopPlus.exe")) || File.Exists(Path.Combine(a, "coopplus.json")) || File.Exists(Path.Combine(a, "Co-op+ Playtest.exe"))) throw new IOException("Extract Co-op+ Staging into its own folder.");
            }
            string user = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            foreach (string normal in new[] { Path.Combine(user, "AppData", "LocalLow", "Nolla_Games_Noita"), Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "quant", "entangledworlds") })
                if (Within(package, normal) || Within(normal, package)) throw new IOException("Extract the playtest outside normal save/proxy profile folders.");
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint capacity, uint flags);
        static bool SkipPrivateCompatibilityAlias(string path, string privateRoot) {
            if (privateRoot == null || (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0) return false;
            string expected = Path.Combine(privateRoot, "profile", "AppData", "Local", "Microsoft", "Windows", "INetCache", "Content.IE5");
            if (!CI.Equals(Path.GetFullPath(path), Path.GetFullPath(expected))) return false;
            string parent = Canon(Path.GetDirectoryName(expected));
            string target = Canon(Path.Combine(parent, "IE"));
            if (!Directory.Exists(target) || (File.GetAttributes(target) & (FileAttributes.Directory | FileAttributes.ReparsePoint)) != FileAttributes.Directory) throw new IOException("Private cache alias has no plain private IE target.");
            using (var handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
                if (handle.IsInvalid) throw new IOException("Cannot inspect the private compatibility cache alias.");
                var buffer = new StringBuilder(32768);
                uint length = GetFinalPathNameByHandleW(handle, buffer, (uint)buffer.Capacity, 0);
                if (length == 0 || length >= buffer.Capacity) throw new IOException("Cannot resolve the private compatibility cache alias.");
                string resolved = buffer.ToString();
                if (resolved.StartsWith(@"\\?\", StringComparison.Ordinal)) resolved = resolved.Substring(4);
                if (!CI.Equals(Canon(resolved), target)) throw new IOException("The private compatibility cache alias points outside its exact private IE target.");
            }
            // Inspect the ordinary IE sibling normally; never traverse this alias.
            return true;
        }
        internal static IEnumerable<string> Files(string root, string privateRoot = null) {
            CheckPreparationCancellation();
            Canon(root);
            foreach (string f in Directory.GetFiles(root)) { Canon(f); yield return f; }
            foreach (string d in Directory.GetDirectories(root)) {
                if (SkipPrivateCompatibilityAlias(d, privateRoot)) continue;
                foreach (string f in Files(Canon(d), privateRoot)) yield return f;
            }
        }
        static IEnumerable<string> GameFiles(string source) {
            foreach (string f in Directory.GetFiles(source)) {
                string n = Path.GetFileName(f);
                if (!EngineRootFiles.Contains(n, CI)) continue;
                Canon(f); yield return f;
            }
            foreach (string d in Directory.GetDirectories(source)) {
                string name = Path.GetFileName(d);
                if (MutableRoots.Contains(name, CI) || name.StartsWith("save", StringComparison.OrdinalIgnoreCase) || !CI.Equals(name, "data")) continue;
                foreach (string f in Files(Canon(d))) yield return f;
            }
        }
        public static void ValidateSource(string source, string package) {
            source = Canon(source); package = Canon(package);
            if (Within(source, package) || Within(package, source)) throw new IOException("Keep the extracted playtest outside the owned Noita installation.");
            foreach (string name in new[] { "noita.exe", "data/data.wak", "config.xml", "lua51.dll", "SDL2.dll", "steam_api.dll" }) {
                string p = Canon(Path.Combine(source, name));
                if (!File.Exists(p) || new FileInfo(p).Length == 0 || ((name == "noita.exe" || name == "data/data.wak") && new FileInfo(p).Length < 1024 * 1024)) throw new IOException("Select a complete owned Noita installation containing noita.exe and data/data.wak plus its runtime dependencies.");
            }
            using (var f = File.OpenRead(Path.Combine(source, "noita.exe"))) if (f.ReadByte() != 'M' || f.ReadByte() != 'Z') throw new IOException("Invalid Noita executable.");
        }
        public static void CheckStopped() {
            foreach (var p in Process.GetProcesses()) using (p) {
                string n;
                try { n = p.ProcessName.ToLowerInvariant(); } catch { continue; }
                if (n == "noita" || n == "noita_dev" || n == "noita_proxy" || n == "noita_proxy.x86_64" || n == "coopupdater" || n == "coopplus") throw new IOException("Close Noita and all Co-op+ / EW proxies and updaters, then reopen the playtest. No process was stopped.");
            }
        }
        static string CopyFile(string source, string dest) {
            CheckPreparationCancellation();
            Canon(source); Canon(dest); Directory.CreateDirectory(Path.GetDirectoryName(dest));
            File.Copy(source, dest, false); File.SetAttributes(dest, FileAttributes.Normal);
            string sourceHash = Hash(source), destinationHash = Hash(dest);
            if (sourceHash != destinationHash) throw new IOException("Copy verification failed.");
            if (Fault != null) Fault("copied");
            return destinationHash;
        }
        static long? PreferenceDimension(XmlElement config,string name) {
            var value=config==null?null:config.GetAttributeNode(name);if(value==null)return null;
            long dimension;
            return Int64.TryParse(value.Value,System.Globalization.NumberStyles.Integer,System.Globalization.CultureInfo.InvariantCulture,out dimension) && dimension > 0 ? (long?)dimension : null;
        }
        // DEVMODEW's Unicode fixed strings and 16-byte display union total220B.
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
        struct PreferenceDisplayMode {
            [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)]public string deviceName;
            public ushort specVersion,driverVersion,size,driverExtra;
            public uint fields;public int positionX,positionY;public uint displayOrientation,displayFixedOutput;
            public short color,duplex,yResolution,ttOption,collate;
            [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)]public string formName;
            public ushort logPixels;public uint bitsPerPixel,pelsWidth,pelsHeight,displayFlags,displayFrequency,icmMethod,icmIntent,mediaType,ditherType,reserved1,reserved2,panningWidth,panningHeight;
        }
        [DllImport("user32.dll",CharSet=CharSet.Unicode,ExactSpelling=true)]
        static extern bool EnumDisplaySettingsW(string deviceName,int modeNumber,ref PreferenceDisplayMode mode);
        internal static int PreferenceDisplayModeSize(){return Marshal.SizeOf(typeof(PreferenceDisplayMode));}
        internal static int[][] ReadSupportedPreferenceModes(string deviceName) {
            if(String.IsNullOrWhiteSpace(deviceName)||PreferenceDisplayModeSize()!=220)throw new IOException("Cannot establish the supported display-mode layout.");
            var modes=new List<int[]>();var seen=new HashSet<string>();
            for(int index=0;index<1024;index++) {
                var mode=new PreferenceDisplayMode{size=220};if(!EnumDisplaySettingsW(deviceName,index,ref mode))return modes.ToArray();
                if(mode.pelsWidth==0||mode.pelsHeight==0||mode.pelsWidth>Int32.MaxValue||mode.pelsHeight>Int32.MaxValue)continue;
                string key=mode.pelsWidth+"x"+mode.pelsHeight;if(seen.Add(key))modes.Add(new[]{(int)mode.pelsWidth,(int)mode.pelsHeight});
            }
            throw new IOException("Supported display-mode enumeration exceeded its bounded limit.");
        }
        internal static int[] FreshWindowDimensions(XmlElement config,int workingWidth,int workingHeight,IEnumerable<int[]> supportedModes) {
            if(workingWidth<=96||workingHeight<=96)throw new IOException("The display working area is too small for a visible private Noita window.");
            int maximumWidth=workingWidth-96,maximumHeight=workingHeight-96;
            long? windowWidth=PreferenceDimension(config,"window_w"),windowHeight=PreferenceDimension(config,"window_h"),backWidth=PreferenceDimension(config,"backbuffer_width"),backHeight=PreferenceDimension(config,"backbuffer_height");
            if(supportedModes==null)throw new IOException("Supported display modes are unavailable.");
            var visible=new List<int[]>();var seen=new HashSet<string>();int count=0;
            foreach(var mode in supportedModes) {
                if(++count>1024)throw new IOException("Supported display-mode input exceeded its bounded limit.");
                if(mode==null||mode.Length!=2||mode[0]<=0||mode[1]<=0)throw new InvalidDataException("Invalid supported display mode.");
                if(mode[0]<640||mode[1]<360||mode[0]>maximumWidth||mode[1]>maximumHeight)continue;
                if(seen.Add(mode[0]+"x"+mode[1]))visible.Add(new[]{mode[0],mode[1]});
            }
            if(visible.Count==0)throw new IOException("No supported Noita display mode fits the visible working area. No display policy or mode was changed.");
            var imported=visible.FirstOrDefault(mode=>windowWidth.HasValue&&windowHeight.HasValue&&mode[0]==windowWidth.Value&&mode[1]==windowHeight.Value)
                ??visible.FirstOrDefault(mode=>backWidth.HasValue&&backHeight.HasValue&&mode[0]==backWidth.Value&&mode[1]==backHeight.Value);
            if(imported!=null)return imported;
            var soft=visible.Where(mode=>mode[0]<=1600&&mode[1]<=900).ToArray();var candidates=soft.Length>0?soft:visible.ToArray();
            return candidates.OrderBy(mode=>Math.Pow(mode[0]-1600.0,2)+Math.Pow(mode[1]-900.0,2)).ThenBy(mode=>mode[0]).ThenBy(mode=>mode[1]).First();
        }
        internal static void InitializeFreshPreferences(string game,string ownedSource,string defaultPreferences,int workingWidth,int workingHeight,IEnumerable<int[]> supportedModes) {
            // First clone only: retained ready profiles never enter this method.
            game=Canon(game);string output=Canon(Path.Combine(game,"save_shared","config.xml")),mods=Canon(Path.Combine(game,"save00","mod_config.xml"));
            string settings = Child(game, "save00/mod_settings.bin");
            if (new[]{output, mods, settings}.Any(path => File.Exists(path) || Directory.Exists(path))) throw new IOException("Fresh preference initialization requires an empty Staging profile.");
            string normalRoot;
            XmlElement source = StagingPreferences.Select(ownedSource, defaultPreferences, out normalRoot);
            string ping = StagingPreferences.ReadPing(normalRoot);
            var document=new XmlDocument{XmlResolver=null};document.LoadXml("<Config mods_sandbox_enabled=\"0\" single_threaded_loading=\"0\" />");
            StagingPreferences.PopulateConfig(source, document.DocumentElement);
            int[] size=FreshWindowDimensions(source,workingWidth,workingHeight,supportedModes);
            foreach(string key in new[]{"window_w","backbuffer_width"})document.DocumentElement.SetAttribute(key,size[0].ToString(System.Globalization.CultureInfo.InvariantCulture));
            foreach(string key in new[]{"window_h","backbuffer_height"})document.DocumentElement.SetAttribute(key,size[1].ToString(System.Globalization.CultureInfo.InvariantCulture));
            document.DocumentElement.SetAttribute("fullscreen","0");
            document.DocumentElement.SetAttribute("is_default_config","0");
            document.DocumentElement.SetAttribute("config_format_version","14");
            Directory.CreateDirectory(Path.GetDirectoryName(output));Directory.CreateDirectory(Path.GetDirectoryName(mods));
            using(var stream=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.None)){document.Save(stream);stream.Flush(true);}
            using(var stream=new FileStream(mods,FileMode.CreateNew,FileAccess.Write,FileShare.None)){byte[] bytes=Encoding.UTF8.GetBytes("<Mods><Mod enabled=\"1\" name=\"quant.ew\" settings_fold_open=\"0\" workshop_item_id=\"0\" /></Mods>");stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
            StagingPreferences.WriteFreshModSettings(game, ping);
        }
        static void Prefs(string game,string ownedSource) {
            var screen=Screen.PrimaryScreen;if(screen==null)throw new IOException("No display working area is available for private first-use preferences.");
            string normal=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"AppData","LocalLow","Nolla_Games_Noita","save_shared","config.xml");
            InitializeFreshPreferences(game,ownedSource,normal,screen.WorkingArea.Width,screen.WorkingArea.Height,ReadSupportedPreferenceModes(screen.DeviceName));
        }
        public static string Prepare(string package, string source) {
            return Prepare(package, source, LoadPayload(package));
        }
        public static string Prepare(string package, string source, VerifiedPayload verified) {
            package = Canon(package); CheckStopped();
            if (verified == null || !CI.Equals(package, verified.package)) throw new IOException("Verified payload belongs to another extracted folder.");
            var m = verified.manifest;
            string root = Child(package, PrivateName);
            string readyFile = Path.Combine(root, "ready.json");
            if (Directory.Exists(root)) {
                var r = ReadReady(root);
                if (!Matches(r, verified)) throw new IOException("The Staging profile and selected runtime disagree. Keep the folder for review.");
                Phase("Verifying private game and retained Continue");
                Verify(Path.Combine(root, "game"), r.game);
                foreach(var expected in m.ownedGame)if(!r.game.Any(x=>CI.Equals(x.path,expected.path)&&x.size==expected.size&&x.sha256==expected.sha256))throw new IOException("The retained private engine does not match this frozen package; no progress was changed.");
                VerifySaveFolders(root);
                Phase("Verifying private frozen runtime"); VerifyRuntime(root, m); VerifySaveFolders(root);
                // The sealed predecessor reads its manifest from this launcher.
                if (verified.payload != null && Hash(Child(verified.payload, "manifest.json")) != verified.hash)
                    throw new IOException("Package metadata changed during verification.");
                Phase("Private game and runtime ready"); return root;
            }
            ValidateSource(source, package);
            Phase("Verifying owned Noita source");
            try { Verify(source, m.ownedGame); } catch (IOException e) { throw new IOException("Your owned Noita version does not match this frozen playtest. Select the supported owned installation; no game files were repaired or downloaded. " + e.Message); }
            CheckStopped();
            Directory.CreateDirectory(root); // Never reuse a partial copy or overwrite any prior private save.
            var gameEntries = new List<Entry>();
            Phase("Copying owned Noita into private storage");
            string game = Path.Combine(root, "game"); Directory.CreateDirectory(game);
            foreach (string f in GameFiles(source).OrderBy(x => x, CI)) {
                string rel = f.Substring(source.Length + 1); Progress("Copying owned Noita: " + rel);
                string copied = Path.Combine(game, rel); string copiedHash = CopyFile(f, copied);
                gameEntries.Add(new Entry { path = rel.Replace('\\','/'), size = new FileInfo(copied).Length, sha256 = copiedHash });
            }
            // This public app identifier is launcher data, not a licensed game file.
            // Always create it privately; a stock owned install need not contain it.
            File.WriteAllText(Path.Combine(game, "steam_appid.txt"), "881100", Encoding.ASCII);
            gameEntries.Add(Describe(game, Path.Combine(game, "steam_appid.txt")));
            foreach (var e in m.files) {
                string dest = e.path.StartsWith("bin/") ? Path.Combine(root, "runtime", e.path.Replace('/', '\\')) : Path.Combine(game, "mods", e.path.Substring("mod/".Length).Replace('/', '\\'));
                CopyFile(Child(verified.payload, e.path), dest);
            }
            foreach (string d in new[] { "game/save00", "game/save_shared", "proxy/save_state", "temp", "profile/AppData/Roaming", "profile/AppData/Local", "profile/AppData/LocalLow" }) Directory.CreateDirectory(Path.Combine(root, d.Replace('/', '\\')));
            Prefs(game,source);
            File.WriteAllText(Path.Combine(root, "proxy", "proxy.ron"),
                "(app:(lang_id:Some(\"en-US\"),spacewars:false,start_game_automatically:false,discord_activity:true,discord_show_area_names:false,game_settings:(game_mode:Some(LocalHealth(Alternate)),share_gold:Some(false))),paths:())", new UTF8Encoding(false));
            Phase("Verifying complete private copy"); Verify(game, gameEntries.ToArray()); VerifyRuntime(root, m); CheckStopped();
            var finalSource = GameFiles(source).Select(f => Describe(source, f)).OrderBy(x => x.path, CI).ToArray();
            var copiedSource = gameEntries.Where(x => !CI.Equals(x.path, "steam_appid.txt")).OrderBy(x => x.path, CI).ToArray();
            if (finalSource.Length != copiedSource.Length || finalSource.Where((entry, i) => entry.path != copiedSource[i].path || entry.size != copiedSource[i].size || entry.sha256 != copiedSource[i].sha256).Any()) throw new IOException("The owned game changed during copying. The incomplete private copy is preserved; retry in a new extracted folder after Steam finishes updating.");
            if (Hash(Child(verified.payload, "manifest.json")) != verified.hash) throw new IOException("Package metadata changed while preparing the private copy.");
            var ready = new Ready { schema = 1, source = source, game = gameEntries.ToArray() };
            Bind(ready, verified);
            File.WriteAllText(readyFile, Json.Serialize(ready), new UTF8Encoding(false));
            Phase("Private copy prepared");
            return root;
        }
        internal static void VerifyRuntime(string root, Manifest m) {
            foreach (var e in m.files) {
                string target = e.path.StartsWith("bin/") ? Path.Combine(root, "runtime") : Path.Combine(root, "game", "mods");
                string relative = e.path.StartsWith("bin/") ? e.path : e.path.Substring(4);
                Verify(target, new[] { new Entry { path = relative, size = e.size, sha256 = e.sha256 } });
            }
            // Inspect every existing mutable descendant for links too, without changing it.
            foreach (string f in Files(root, root)) Canon(f);
            string bin = Path.Combine(root, "runtime", "bin");
            var allowed = new HashSet<string>(m.files.Where(e => e.path.StartsWith("bin/")).Select(e => e.path.Substring(4)), CI);
            allowed.Add("ew_log.txt"); allowed.Add("ew_log_old.txt");
            foreach (string file in Files(bin)) if (!allowed.Contains(file.Substring(bin.Length + 1).Replace('\\', '/'))) throw new IOException("Unexpected file in private executable runtime; preserved: " + file);
        }
        internal static void VerifySaveFolders(string root) {
            string appId = Path.Combine(root, "game", "steam_appid.txt");
            if (!File.Exists(appId) || new FileInfo(appId).Length != 6 || !File.ReadAllBytes(appId).SequenceEqual(Encoding.ASCII.GetBytes("881100"))) throw new IOException("Private Steam application identity is missing or changed; preserved without launching.");
            foreach (string relative in new[] { "game/save00", "game/save_shared", "proxy/save_state", "temp", "profile/AppData/Local" })
                if (!Directory.Exists(Path.Combine(root, relative.Replace('/', '\\')))) throw new IOException("Private storage is incomplete; preserved without resetting: " + relative);
            string state = Path.Combine(root, "proxy", "save_state");
            if (File.Exists(Path.Combine(state, "run_info_v2.bit"))) {
                foreach (string name in new[] { "run_info_v2.bit", "world_chunks.bit", "des_entity_storage.bit", "flags.bit" })
                    if (!File.Exists(Path.Combine(state, name)) || new FileInfo(Path.Combine(state, name)).Length == 0) throw new IOException("Incomplete private host Continue state; preserved: " + name);
            } else if (Directory.EnumerateFileSystemEntries(state).Any()) throw new IOException("Unknown/incomplete private proxy Continue state; preserved without reset.");
            foreach (string relative in new[] { "game/save00/mod_config.xml", "game/save_shared/config.xml", "proxy/proxy.ron" })
                if (!File.Exists(Path.Combine(root, relative.Replace('/', '\\')))) throw new IOException("Private settings/save scaffolding is incomplete; preserved without resetting: " + relative);
            foreach (string relative in new[] { "game/save00/mod_config.xml", "game/save_shared/config.xml", "game/save00/player.xml", "game/save00/world_state.xml" }) {
                string file = Path.Combine(root, relative.Replace('/', '\\')); if (!File.Exists(file)) continue;
                if (new FileInfo(file).Length == 0 || new FileInfo(file).Length > 128 * 1024 * 1024) throw new IOException("Invalid private saved XML; preserved: " + relative);
                using (var reader = XmlReader.Create(file, new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null })) while (reader.Read()) { }
            }
        }
        public static ProcessStartInfo StartInfo(string root) {
            Canon(root);
            var start = new ProcessStartInfo(Path.Combine(root, "runtime", "bin", "noita_proxy.exe")) { UseShellExecute = false, WorkingDirectory = Path.Combine(root, "runtime", "bin") };
            start.EnvironmentVariables["COOPPLUS_PRIVATE_ROOT"] = root;
            start.EnvironmentVariables.Remove("COOPPLUS_NORMAL_ROOT");
            start.EnvironmentVariables["USERPROFILE"] = Path.Combine(root, "profile");
            start.EnvironmentVariables["APPDATA"] = Path.Combine(root, "profile", "AppData", "Roaming");
            start.EnvironmentVariables["LOCALAPPDATA"] = Path.Combine(root, "profile", "AppData", "Local");
            start.EnvironmentVariables["TEMP"] = Path.Combine(root, "temp"); start.EnvironmentVariables["TMP"] = Path.Combine(root, "temp");
            foreach (string name in new[] { "NP_DISABLE_STEAM", "NP_APPID", "NP_NOITA_ADDR", "NP_ALLOW_REUSE_ADDR", "NP_SKIP_MOD_CHECK", "NP_WORLD_SYNC_TEST" }) start.EnvironmentVariables.Remove(name);
            start.EnvironmentVariables["SteamAppId"] = "881100"; start.EnvironmentVariables["SteamGameId"] = "881100";
            start.Arguments = "--exe-path " + Quote(Path.Combine(root, "game", "noita.exe")) + " --settings-path " + Quote(Path.Combine(root, "proxy", "proxy.ron")) + " --save-state-path " + Quote(Path.Combine(root, "proxy", "save_state"));
            return start;
        }
        public static string Quote(string value) {
            var b = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { b.Append('\\', slashes * 2 + 1); b.Append(c); }
                else { b.Append('\\', slashes); b.Append(c); } slashes = 0;
            }
            b.Append('\\', slashes * 2); return b.Append('"').ToString();
        }
        public static IEnumerable<string> Discover() {
            var roots = new HashSet<string>(CI);
            using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam")) if (key != null) { var p = key.GetValue("SteamPath") as string; if (p != null) roots.Add(p.Replace('/', '\\')); }
            foreach (string p in new[] { Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "Steam"), @"C:\Program Files (x86)\Steam" }) if (Directory.Exists(p)) roots.Add(p);
            foreach (string r in roots.ToArray()) {
                string libraries = Path.Combine(r, "steamapps", "libraryfolders.vdf");
                if (!File.Exists(libraries) || new FileInfo(libraries).Length > 2 * 1024 * 1024) continue;
                foreach (Match match in Regex.Matches(File.ReadAllText(libraries), "\\\"path\\\"\\s*\\\"([^\\\"]+)\\\"")) roots.Add(match.Groups[1].Value.Replace("\\\\", "\\"));
            }
            foreach (string r in roots) { string p = Path.Combine(r, "steamapps", "common", "Noita"); if (File.Exists(Path.Combine(p, "noita.exe"))) yield return p; }
        }
        internal static string PickSource(string package, VerifiedPayload verified, IWin32Window owner) {
            var payload = verified.manifest;
            foreach (string source in Discover()) { try { ValidateSource(source, package); Verify(Canon(source), payload.ownedGame); return Canon(source); } catch (IOException) { } }
            using (var picker = new OpenFileDialog { Title = "Select noita.exe in your owned Noita installation", Filter = "Noita|noita.exe", CheckFileExists = true }) {
                if (picker.ShowDialog(owner) != DialogResult.OK) throw new OperationCanceledException("No private game copy was created.");
                string source = Path.GetDirectoryName(picker.FileName); ValidateSource(source, package); Verify(Canon(source), payload.ownedGame); return Canon(source);
            }
        }
        internal static Ready ReadReady(string root) {
            string file = Child(root, "ready.json");
            if (!File.Exists(file) || new FileInfo(file).Length > PayloadManifestLimit)
                throw new IOException("The Staging copy is incomplete and has been retained.");
            var ready = Json.Deserialize<Ready>(File.ReadAllText(file, new UTF8Encoding(false,true)));
            if (ready == null || ready.schema != 1 || ready.channel != Channel || ready.game == null ||
                ready.runtimeId != StagingRelease.Runtime(ready.build, ready.version) || !StagingRelease.ValidHash(ready.payloadHash))
                throw new IOException("Staging readiness metadata is incomplete or belongs to another channel.");
            return ready;
        }
        internal static void Bind(Ready ready, VerifiedPayload verified) {
            var manifest = verified.manifest;
            ready.channel = manifest.channel;
            ready.version = manifest.version;
            ready.build = manifest.build;
            ready.runtimeId = manifest.runtimeId;
            ready.payloadHash = verified.hash;
        }
        internal static bool Matches(Ready ready, VerifiedPayload verified) {
            return ready != null && ready.schema == 1 && ready.channel == Channel && ready.game != null &&
                ready.build == verified.manifest.build && ready.version == verified.manifest.version &&
                ready.runtimeId == verified.manifest.runtimeId && ready.payloadHash == verified.hash;
        }
    }
}
