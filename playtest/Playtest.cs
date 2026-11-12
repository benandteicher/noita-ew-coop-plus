// Windows 11 / .NET Framework 4.8. No download, updater, login or licensed payload.
using System;
using System.Collections.Generic;
using System.ComponentModel;
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
    public sealed class UpgradeFrom { public string runtimeId; public string payloadHash; public string manifestBase64; public Entry[] files; }
    public sealed class Manifest { public int schema; public string runtimeId; public string label; public Entry[] files; public Entry[] ownedGame; public UpgradeFrom[] upgradeFrom; }
    public sealed class Ready { public int schema; public string runtimeId; public string payloadHash; public string source; public Entry[] game; }
    public sealed class VerifiedPayload { public string package; public Manifest manifest; public string hash; }
    internal sealed class PreparationCancellation { public string message; }
    public static class Playtest {
        public const string RuntimeId = "coopplus-20261111-b11-session1";
        public const string LauncherRevision = "setup-r3";
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
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
            Phase("Verifying frozen package");
            ValidatePackage(package);
            package = Canon(package);
            string payload = Canon(Path.Combine(package, "payload"));
            string metadata = Path.Combine(payload, "manifest.json");
            if (!File.Exists(metadata) || new FileInfo(metadata).Length > 16 * 1024 * 1024) throw new IOException("Missing payload manifest.");
            byte[] metadataBytes = File.ReadAllBytes(metadata);
            var m = Json.Deserialize<Manifest>(new UTF8Encoding(false,true).GetString(metadataBytes));
            if (m == null || m.schema != 1 || m.runtimeId != RuntimeId || String.IsNullOrWhiteSpace(m.label)) throw new IOException("This launcher requires its exact frozen playtest payload.");
            if (m.files == null || m.files.Any(e => !(e.path.StartsWith("bin/", StringComparison.Ordinal) || e.path.StartsWith("mod/quant.ew/", StringComparison.Ordinal)))) throw new IOException("Unexpected payload layout.");
            Verify(payload, m.files);
            var actual = Files(payload).Select(x => x.Substring(payload.Length + 1).Replace('\\', '/')).Where(x => x != "manifest.json").ToArray();
            if (!new HashSet<string>(actual, CI).SetEquals(m.files.Select(x => x.path))) throw new IOException("Unlisted payload file.");
            foreach (string required in new[] { "bin/noita_proxy.exe", "bin/steam_api64.dll", "mod/quant.ew/ewext.dll", "mod/quant.ew/init.lua" })
                if (!m.files.Any(x => x.path == required)) throw new IOException("Missing matching component: " + required);
            foreach (string required in new[] { "noita.exe", "data/data.wak", "lua51.dll", "SDL2.dll", "steam_api.dll" })
                if (m.ownedGame == null || !m.ownedGame.Any(x => x.path == required)) throw new IOException("Missing supported owned-game fingerprint: " + required);
            Phase("Frozen package verified");
            return new VerifiedPayload { package = package, manifest = m, hash = HashBytes(metadataBytes) };
        }
        public static void ValidatePackage(string package) {
            package = Canon(package);
            for (string a = package; a != null; a = Path.GetDirectoryName(a)) {
                if (File.Exists(Path.Combine(a, ".git")) || Directory.Exists(Path.Combine(a, ".git"))) throw new IOException("Extract the playtest outside a source checkout.");
                if (File.Exists(Path.Combine(a, "CoopPlus.exe")) || File.Exists(Path.Combine(a, "coopplus.json"))) throw new IOException("Extract the playtest outside the usual Co-op+ launcher folder.");
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
            long dimension;if(!Int64.TryParse(value.Value,System.Globalization.NumberStyles.Integer,System.Globalization.CultureInfo.InvariantCulture,out dimension)||dimension<=0)throw new InvalidDataException("Invalid Noita preference dimension: "+name);
            return dimension;
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
            // No run, mod-settings, controls, controller/cursor policy or lobby data.
            game=Canon(game);string output=Canon(Path.Combine(game,"save_shared","config.xml")),mods=Canon(Path.Combine(game,"save00","mod_config.xml"));
            if(File.Exists(output)||Directory.Exists(output)||File.Exists(mods)||Directory.Exists(mods))throw new IOException("Fresh preference initialization will not replace existing private settings.");
            string portable=Canon(Path.Combine(Canon(ownedSource),"save_shared","config.xml"));
            string selected=File.Exists(portable)?portable:Canon(defaultPreferences);XmlElement source=null;
            if(File.Exists(selected)) {
                if(new FileInfo(selected).Length>1024*1024)throw new IOException("Selected Noita preference file is unexpectedly large.");
                var readerSettings=new XmlReaderSettings{DtdProcessing=DtdProcessing.Prohibit,XmlResolver=null,MaxCharactersInDocument=1024*1024};var old=new XmlDocument{XmlResolver=null};
                using(var reader=XmlReader.Create(selected,readerSettings))old.Load(reader);
                if(old.DocumentElement==null||old.DocumentElement.Name!="Config")throw new InvalidDataException("Selected Noita preference file has an invalid root.");source=old.DocumentElement;
            }
            var document=new XmlDocument{XmlResolver=null};document.LoadXml("<Config mods_sandbox_enabled=\"0\" single_threaded_loading=\"0\" />");
            foreach(string key in new[]{"audio_music_volume","audio_effects_volume","language","vsync","screenshake_intensity"}) {
                var value=source==null?null:source.GetAttributeNode(key);if(value==null)continue;
                if(value.Value.Length>=128)throw new InvalidDataException("Oversized Noita preference attribute: "+key);
                if(key=="audio_music_volume"||key=="audio_effects_volume"){double volume;if(!Double.TryParse(value.Value,System.Globalization.NumberStyles.Float,System.Globalization.CultureInfo.InvariantCulture,out volume)||Double.IsNaN(volume)||Double.IsInfinity(volume)||volume<0||volume>1)throw new InvalidDataException("Invalid Noita audio preference: "+key);}
                document.DocumentElement.SetAttribute(key,value.Value);
            }
            int[] size=FreshWindowDimensions(source,workingWidth,workingHeight,supportedModes);
            foreach(string key in new[]{"window_w","backbuffer_width"})document.DocumentElement.SetAttribute(key,size[0].ToString(System.Globalization.CultureInfo.InvariantCulture));
            foreach(string key in new[]{"window_h","backbuffer_height"})document.DocumentElement.SetAttribute(key,size[1].ToString(System.Globalization.CultureInfo.InvariantCulture));
            document.DocumentElement.SetAttribute("fullscreen","0");
            document.DocumentElement.SetAttribute("is_default_config","0");
            document.DocumentElement.SetAttribute("config_format_version","14");
            Directory.CreateDirectory(Path.GetDirectoryName(output));Directory.CreateDirectory(Path.GetDirectoryName(mods));
            using(var stream=new FileStream(output,FileMode.CreateNew,FileAccess.Write,FileShare.None)){document.Save(stream);stream.Flush(true);}
            using(var stream=new FileStream(mods,FileMode.CreateNew,FileAccess.Write,FileShare.None)){byte[] bytes=Encoding.UTF8.GetBytes("<Mods><Mod enabled=\"1\" name=\"quant.ew\" settings_fold_open=\"0\" workshop_item_id=\"0\" /></Mods>");stream.Write(bytes,0,bytes.Length);stream.Flush(true);}
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
            string root = Canon(Path.Combine(package, "private"));
            string readyFile = Path.Combine(root, "ready.json");
            if (Directory.Exists(root)) {
                RuntimeMigration.Recover(root, m, verified.hash);
                if (!File.Exists(readyFile)) throw new IOException("The private copy is incomplete and is preserved. Keep this folder and its logs for review; no progress was reset.");
                var r = Json.Deserialize<Ready>(File.ReadAllText(readyFile));
                if (r == null || r.schema != 1) throw new IOException("Private readiness metadata is incomplete; preserved without resetting.");
                Phase("Verifying private game and retained Continue");
                Verify(Path.Combine(root, "game"), r.game);
                foreach(var expected in m.ownedGame)if(!r.game.Any(x=>CI.Equals(x.path,expected.path)&&x.size==expected.size&&x.sha256==expected.sha256))throw new IOException("The retained private engine does not match this frozen package; no progress was changed.");
                VerifySaveFolders(root);
                if (r.runtimeId != RuntimeId || r.payloadHash != verified.hash) RuntimeMigration.Upgrade(package, root, r, verified);
                Phase("Verifying private frozen runtime"); VerifyRuntime(root, m); VerifySaveFolders(root);
                if (Hash(Path.Combine(package,"payload","manifest.json")) != verified.hash) throw new IOException("Package metadata changed during verification.");
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
                CopyFile(Child(Path.Combine(package, "payload"), e.path), dest);
            }
            foreach (string d in new[] { "game/save00", "game/save_shared", "proxy/save_state", "temp", "profile/AppData/Roaming", "profile/AppData/Local", "profile/AppData/LocalLow" }) Directory.CreateDirectory(Path.Combine(root, d.Replace('/', '\\')));
            Prefs(game,source);
            File.WriteAllText(Path.Combine(root, "proxy", "proxy.ron"), "(app:(lang_id:Some(\"en-US\"),spacewars:false,start_game_automatically:false),paths:())", new UTF8Encoding(false));
            Phase("Verifying complete private copy"); Verify(game, gameEntries.ToArray()); VerifyRuntime(root, m); CheckStopped();
            var finalSource = GameFiles(source).Select(f => Describe(source, f)).OrderBy(x => x.path, CI).ToArray();
            var copiedSource = gameEntries.Where(x => !CI.Equals(x.path, "steam_appid.txt")).OrderBy(x => x.path, CI).ToArray();
            if (finalSource.Length != copiedSource.Length || finalSource.Where((entry, i) => entry.path != copiedSource[i].path || entry.size != copiedSource[i].size || entry.sha256 != copiedSource[i].sha256).Any()) throw new IOException("The owned game changed during copying. The incomplete private copy is preserved; retry in a new extracted folder after Steam finishes updating.");
            if (Hash(Path.Combine(package,"payload","manifest.json")) != verified.hash) throw new IOException("Package metadata changed while preparing the private copy.");
            File.WriteAllText(readyFile, Json.Serialize(new Ready { schema = 1, runtimeId = RuntimeId, payloadHash = verified.hash, source = source, game = gameEntries.ToArray() }), new UTF8Encoding(false));
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
        static string PickSource(string package, VerifiedPayload verified, IWin32Window owner) {
            var payload = verified.manifest;
            foreach (string source in Discover()) { try { ValidateSource(source, package); Verify(Canon(source), payload.ownedGame); return Canon(source); } catch (IOException) { } }
            using (var picker = new OpenFileDialog { Title = "Select noita.exe in your owned Noita installation", Filter = "Noita|noita.exe", CheckFileExists = true }) {
                if (picker.ShowDialog(owner) != DialogResult.OK) throw new OperationCanceledException("No private game copy was created.");
                string source = Path.GetDirectoryName(picker.FileName); ValidateSource(source, package); Verify(Canon(source), payload.ownedGame); return Canon(source);
            }
        }
        [STAThread] public static int Main(string[] args) {
            Application.EnableVisualStyles();
            try {
                if (args.Length != 0) throw new IOException("Open Co-op+ Playtest.exe without flags.");
                string package = Canon(Path.GetDirectoryName(Application.ExecutablePath));
                using (var gate = new Mutex(false, "Local\\CoopPlus-Private-Playtest")) {
                    bool held; try { held = gate.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                    if (!held) throw new IOException("A playtest launcher is already open. Close its test game and proxy first.");
                    try {
                        CheckStopped(); ValidatePackage(package);
                        int result = 0;
                        using (var audit = new SessionAudit(package))
                        using (var progress = new Form { Text = "Co-op+ Playtest · PRIVATE · session build 11 · " + LauncherRevision, Width = 680, Height = 190, StartPosition = FormStartPosition.CenterScreen, ControlBox = false })
                        using (var worker = new BackgroundWorker { WorkerReportsProgress = true }) {
                            var text = new Label { Dock = DockStyle.Fill, Padding = new Padding(16), Text = "Verifying the frozen package. Progress and failures are retained under playtest-logs." }; progress.Controls.Add(text);
                            progress.Controls.Add(new ProgressBar{Dock=DockStyle.Bottom,Height=18,Style=ProgressBarStyle.Marquee,MarqueeAnimationSpeed=30});
                            var cancel = new Button { Dock=DockStyle.Bottom,Height=30,Text="Cancel verification / setup" };progress.Controls.Add(cancel);
                            // 0=preparing, 1=cancel requested, 2=session supervision.
                            // The UI transition and Cancel click are serialized; a
                            // pending cancel cannot be lost before child dispatch.
                            int preparationState=0;
                            CancellationRequested=()=>Interlocked.CompareExchange(ref preparationState,0,0)==1;
                            Action requestCancellation=()=>{if(Interlocked.CompareExchange(ref preparationState,1,0)==0){cancel.Enabled=false;text.Text="Cancellation requested. Finishing the current file operation; no game or proxy will be launched.";audit.Write("preparation-cancel-requested",new{phase=currentPhase});}};
                            cancel.Click+=(sender,e)=>requestCancellation();
                            HashedBytes=0;HashedFiles=0;long lastReport=0;
                            Progress = value => { long now=Stopwatch.GetTimestamp(); if((value.Contains("\n")||value.StartsWith("Copying ",StringComparison.Ordinal))&&now-lastReport<Stopwatch.Frequency/8)return;lastReport=now;worker.ReportProgress(0,value); };
                            PhaseLog = (name,files,bytes) => audit.Write("phase",new{name=name,hashedFiles=files,hashedBytes=bytes});
                            worker.ProgressChanged += (sender,e) => { text.Text=Interlocked.CompareExchange(ref preparationState,0,0)==1?"Cancellation requested. Finishing the current file operation; no game or proxy will be launched.":(string)e.UserState;if(text.Text.StartsWith("Co-op+ Playtest is open.",StringComparison.Ordinal)){if(progress.Visible)progress.Hide();}else if(e.ProgressPercentage==1&&!progress.Visible)progress.Show(); };
                            worker.DoWork += (sender,e) => {
                                try {
                                    var verified=LoadPayload(package);string source=null;
                                    if(!Directory.Exists(Path.Combine(package,"private"))){Exception pickerError=null;progress.Invoke((MethodInvoker)(()=>{try{source=PickSource(package,verified,progress);}catch(Exception error){pickerError=error;}}));if(pickerError!=null)throw pickerError;}
                                    string root=Prepare(package,source,verified);CheckStopped();
                                    CheckPreparationCancellation();
                                    progress.Invoke((MethodInvoker)(()=>{if(Interlocked.CompareExchange(ref preparationState,2,0)==0){cancel.Enabled=false;cancel.Text="Close the game and proxy normally to finish";}}));
                                    CheckPreparationCancellation();
                                    SessionController.Run(root,audit,value=>worker.ReportProgress(value.StartsWith("Co-op+ Playtest is open.",StringComparison.Ordinal)?0:1,value));
                                    audit.Finish("proxy-exit-zero");
                                } catch(OperationCanceledException){
                                    audit.Finish("preparation-cancelled");string privateRoot=Path.Combine(package,"private");string message;
                                    if(File.Exists(Path.Combine(privateRoot,"runtime-upgrade-journal.json")))message="Preparation cancelled. The managed-update journal and existing Continue/settings are retained. Reopen this same launcher to recover and retry in the same folder.";
                                    else if(Directory.Exists(privateRoot)&&!File.Exists(Path.Combine(privateRoot,"ready.json")))message="Setup cancelled after part of the private copy was created. That incomplete copy and its logs are retained. Reopening will refuse the incomplete copy until it is reviewed. No existing run was reset; keep this folder for review.";
                                    else message="Verification/setup cancelled. Existing private progress and settings are unchanged. Open this launcher again whenever you are ready.";
                                    e.Result=new PreparationCancellation{message=message+"\n\nNo game or proxy was launched."};
                                }
                                catch(Exception error){audit.Write("launcher-failure",new{error=error.ToString()});audit.Finish("failure");e.Result=error;}
                            };
                            worker.RunWorkerCompleted += (sender,e) => {
                                Exception error=e.Error??(e.Cancelled?null:e.Result as Exception);
                                var cancelled=e.Error==null&&!e.Cancelled?e.Result as PreparationCancellation:null;
                                if(cancelled!=null){progress.Show();MessageBox.Show(progress,cancelled.message+"\n\nRetained session logs: "+audit.DirectoryPath,"Co-op+ Playtest · preparation cancelled",MessageBoxButtons.OK,MessageBoxIcon.Information);}
                                else if(error!=null){result=1;progress.Show();MessageBox.Show(progress,error.Message+"\n\nRetained session logs: "+audit.DirectoryPath,"Co-op+ Playtest · stopped safely",MessageBoxButtons.OK,MessageBoxIcon.Error);}
                                progress.Close();
                            };
                            progress.FormClosing += (sender,e) => { if(worker.IsBusy){e.Cancel=true;requestCancellation();} };
                            progress.Shown += (sender,e) => worker.RunWorkerAsync();
                            Application.Run(progress);
                            Progress=delegate{};PhaseLog=delegate{};CancellationRequested=delegate{return false;};
                        }
                        return result;
                    } finally { gate.ReleaseMutex(); }
                }
                return 0;
            } catch (OperationCanceledException) { return 0; }
            catch (Exception e) { MessageBox.Show(e.Message, "Co-op+ Playtest · stopped safely", MessageBoxButtons.OK, MessageBoxIcon.Error); return 1; }
        }
    }
}
