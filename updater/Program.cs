using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace CoopPlus {
    public sealed class UserConfig { public int schema = 1; public string gameDir; public string settingsPath; public string saveStatePath; }
    internal static class Program {
        internal const string Repository = "benandteicher/noita-ew-coop-plus";
        internal const string Feed = "https://api.github.com/repos/" + Repository + "/releases/latest";
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        private static int Main(string[] args) {
            try {
                Console.InputEncoding = new UTF8Encoding(false);
                Console.OutputEncoding = new UTF8Encoding(false);
                if (args.Length < 3 || args[0] != "--internal-root" || args[2] != "--")
                    throw new ArgumentException("Open CoopPlus.exe to run the updater.");
                string root = Path.GetFullPath(args[1]); Guard(root);
                string[] forwarded = args.Skip(3).ToArray();
                if (forwarded.Length == 1 && forwarded[0] == "--coop-help") {
                    Console.WriteLine("Co-op+ bootstrap protocol 1. Open without arguments for setup/update/launch. Official proxy arguments are forwarded unchanged. --coop-check performs checks/installation without launching; --coop-setup selects a Noita folder."); return 0;
                }
                bool checkOnly = forwarded.Contains("--coop-check");
                bool setup = forwarded.Contains("--coop-setup");
                forwarded = forwarded.Where(a => a != "--coop-check" && a != "--coop-setup").ToArray();
                var config = ReadConfig(root, setup);
                Guard(config.gameDir);
                string key;
                using (var hash = SHA256.Create()) key = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Path.GetFullPath(config.gameDir).TrimEnd('\\').ToUpperInvariant()))).Replace("-", "");
                using (var gate = new Mutex(false, "Local\\CoopPlus-Game-" + key)) {
                    bool held; try { held = gate.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                    if (!held) throw new IOException("Another Co-op+ updater is using this Noita folder. Wait for it to close.");
                    try {
                        string lockPath = Path.Combine(config.gameDir, ".coopplus-update.lock"); Guard(lockPath);
                        using (var fileGate = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None))
                            return Run(root, config.gameDir, forwarded, checkOnly);
                    }
                    finally { gate.ReleaseMutex(); }
                }
            } catch (Exception e) { Console.Error.WriteLine("Co-op+: " + e.Message); return 1; }
        }
        private static UserConfig ReadConfig(string root, bool setup) {
            string path = Path.Combine(root, "coopplus.json");
            UserConfig c = !setup && File.Exists(path) ? Json.Deserialize<UserConfig>(File.ReadAllText(path)) : null;
            if (c == null) {
                Console.WriteLine("One-time Co-op+ setup. Close Noita and every official proxy first.");
                Console.WriteLine("Enter your Noita installation folder (the folder containing noita.exe).");
                Console.WriteLine("Co-op+ replaces mods/quant.ew completely and keeps a backup outside mods.");
                Console.Write("Noita folder: ");
                string input = Console.ReadLine();
                if (String.IsNullOrWhiteSpace(input)) throw new IOException("Setup cancelled; no installation changed.");
                string game = Path.GetFullPath(input.Trim().Trim('"')); Guard(game);
                if (!File.Exists(Path.Combine(game, "noita.exe"))) throw new IOException("That folder does not contain noita.exe.");
                if (Within(root, game) || Within(game, root)) throw new IOException("Keep the Co-op+ launcher folder separate from the game folder.");
                Console.WriteLine("Old portable proxy folder, if it contains proxy.ron (Enter uses the official AppData settings/saves):");
                string portable = Console.ReadLine();
                string appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                string settings = Path.Combine(appdata, "quant", "entangledworlds", "config", "proxy.ron");
                string saves = Path.Combine(appdata, "quant", "entangledworlds", "data", "save_state");
                if (!String.IsNullOrWhiteSpace(portable)) {
                    portable = Path.GetFullPath(portable.Trim().Trim('"')); Guard(portable);
                    settings = Path.Combine(portable, "proxy.ron"); saves = Path.Combine(portable, "save_state");
                    if (!File.Exists(settings)) throw new IOException("Old portable folder does not contain proxy.ron; setup cancelled.");
                }
                c = new UserConfig { gameDir = game, settingsPath = settings, saveStatePath = saves };
                ValidateMutable(root, game, c.settingsPath); ValidateMutable(root, game, c.saveStatePath);
                Engine.CheckNotBusy();
                File.WriteAllText(path, Json.Serialize(c), Encoding.UTF8);
            }
            if (c.schema != 1 || String.IsNullOrWhiteSpace(c.gameDir) || !Path.IsPathRooted(c.gameDir)) throw new IOException("Unsupported/invalid coopplus.json; use --coop-setup.");
            if (!File.Exists(Path.Combine(c.gameDir, "noita.exe"))) throw new IOException("Configured Noita folder is unavailable; use --coop-setup.");
            if (Within(root, c.gameDir) || Within(c.gameDir, root)) throw new IOException("Launcher and game folders must be separate.");
            ValidateMutable(root, c.gameDir, c.settingsPath); ValidateMutable(root, c.gameDir, c.saveStatePath);
            return c;
        }
        private static void ValidateMutable(string root, string game, string path) {
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path)) throw new IOException("Invalid mutable settings/save path; use --coop-setup.");
            Guard(path);
            if (Within(path, Path.Combine(root, "current")) || Within(path, Path.Combine(game, "mods"))) throw new IOException("Settings/saves must be outside managed payloads and active mods.");
        }
        private static bool Within(string child, string parent) {
            string a = Path.GetFullPath(child).TrimEnd('\\') + "\\", b = Path.GetFullPath(parent).TrimEnd('\\') + "\\";
            return a.StartsWith(b, StringComparison.OrdinalIgnoreCase);
        }
        internal static int Run(string root, string game, string[] forwarded, bool checkOnly) {
            Engine.CheckNotBusy();
            bool recovering = File.Exists(Path.Combine(root, "install-journal.json"));
            Engine.Recover(root, game);
            if (recovering) { Console.WriteLine("Recovery finished; restarting the current managed updater."); return 42; }
            string installed = Path.Combine(root, "current", "installed.json");
            ReleaseManifest current = File.Exists(installed) ? Engine.ParseManifest(File.ReadAllText(installed)) : null;
            bool ready = current != null;
            if (ready) Engine.VerifyInstalled(root, game);
            ReleaseManifest next = null;
            Console.WriteLine("Checking approved Co-op+ releases...");
            try { next = ReleaseProvider(); }
            catch (UnsupportedBootstrapException) { throw; }
            catch (Exception e) { Console.WriteLine("Update check unavailable: " + e.Message); }
            bool changed = false;
            if (next != null && (current == null || IsNewer(next, current))) {
                string staging = Path.Combine(root, "downloads"); Guard(staging); Directory.CreateDirectory(staging);
                string zip = Path.Combine(staging, "package-" + next.build + ".zip");
                try {
                    Console.WriteLine("Downloading complete release " + next.version + " (build " + next.build + ")...");
                    Engine.StageDownload(next.package.url, zip, next.package.size, next.package.sha256);
                    Engine.Install(root, game, zip, next); changed = true; ready = true;
                } catch (Exception e) {
                    Console.WriteLine("Update did not complete: " + e.Message);
                    if (File.Exists(Path.Combine(root, "install-journal.json"))) throw;
                    if (!ready) throw;
                    Engine.VerifyInstalled(root, game);
                    Console.WriteLine("Existing complete installation remains available.");
                } finally { if (File.Exists(zip)) File.Delete(zip); }
            } else if (current != null) Console.WriteLine("Installed release is current (" + current.version + ", build " + current.build + ").");
            if (!ready) {
                Console.WriteLine("Installing bundled review candidate. This seed is not a published update channel.");
                string seed = Path.Combine(root, "seed"); Guard(seed);
                var manifest = Engine.ParseManifest(File.ReadAllText(Path.Combine(seed, "release.json")));
                Engine.Install(root, game, Path.Combine(seed, "package.zip"), manifest); changed = true;
            }
            Engine.VerifyInstalled(root, game);
            if (changed) { Console.WriteLine("Complete installation ready; restarting the managed updater."); return 42; }
            if (checkOnly) { Console.WriteLine("Installation verified. Check-only mode: no proxy launched."); return 0; }
            Engine.CheckNotBusy();
            return Launch(root, game, forwarded);
        }
        internal static bool IsNewer(ReleaseManifest next, ReleaseManifest current) {
            int comparison = Engine.CompareVersions(next.version, current.version);
            if (next.build == current.build && comparison != 0) throw new IOException("Release build number was reused with a different version.");
            if (next.build <= current.build) return false;
            if (comparison < 0) throw new IOException("Newer build has an older version; refusing inconsistent metadata.");
            return true;
        }
        internal static Func<ReleaseManifest> ReleaseProvider = ApprovedRelease;
        private static ReleaseManifest ApprovedRelease() { return ReadRelease(Feed, "https://github.com/" + Repository + "/releases/download/"); }
        internal static ReleaseManifest ReadRelease(string feed, string downloadBase) {
            var release = Json.Deserialize<Dictionary<string, object>>(GetText(feed, 1024 * 1024));
            if ((bool)release["draft"] || (bool)release["prerelease"]) throw new IOException("Feed does not identify an approved stable release.");
            string tag = (string)release["tag_name"];
            var assets = (System.Collections.IEnumerable)release["assets"];
            var metadata = assets.Cast<Dictionary<string, object>>().SingleOrDefault(a => (string)a["name"] == "release.json");
            if (metadata == null) throw new IOException("Latest release has no protocol-1 release.json.");
            string expectedBase = downloadBase + tag + "/";
            string metadataUrl = (string)metadata["browser_download_url"];
            if (metadataUrl != expectedBase + "release.json") throw new IOException("Unexpected metadata asset location.");
            var manifest = Engine.ParseManifest(GetText(metadataUrl, 8 * 1024 * 1024));
            if (manifest.channel != "stable") throw new IOException("Release is not approved for the stable channel.");
            if (tag != "coopplus-v" + manifest.version + "-b" + manifest.build) throw new IOException("Release tag and manifest identity disagree.");
            if (manifest.package.url != expectedBase + manifest.package.name) throw new IOException("Package must be an asset of the approved release.");
            var payload = assets.Cast<Dictionary<string, object>>().SingleOrDefault(a => (string)a["name"] == manifest.package.name);
            if (payload == null || Convert.ToInt64(payload["size"]) != manifest.package.size) throw new IOException("Release payload asset/size mismatch.");
            return manifest;
        }
        private static string GetText(string url, int limit) {
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            var request = (HttpWebRequest)WebRequest.Create(url); request.UserAgent = "CoopPlus-Bootstrap/1";
            request.Timeout = 12000; request.ReadWriteTimeout = 12000;
            var timer = Stopwatch.StartNew();
            using (var response = request.GetResponse()) {
                if (response.ResponseUri.Scheme != "https") throw new IOException("Insecure metadata redirect rejected.");
                using (var stream = response.GetResponseStream()) using (var output = new MemoryStream()) {
                byte[] buffer = new byte[16384]; int n;
                while ((n = stream.Read(buffer, 0, buffer.Length)) > 0) {
                    if (timer.Elapsed > TimeSpan.FromSeconds(30) || output.Length + n > limit) throw new IOException("Metadata timeout/size limit exceeded.");
                    output.Write(buffer, 0, n);
                }
                return new UTF8Encoding(false, true).GetString(output.ToArray());
                }
            }
        }
        internal static int Launch(string root, string game, string[] args) {
            string proxy = Path.Combine(root, "current", "proxy");
            var config = Json.Deserialize<UserConfig>(File.ReadAllText(Path.Combine(root, "coopplus.json")));
            ValidateMutable(root, game, config.settingsPath); ValidateMutable(root, game, config.saveStatePath);
            Directory.CreateDirectory(Path.GetDirectoryName(config.settingsPath));
            Directory.CreateDirectory(Path.GetDirectoryName(config.saveStatePath));
            ValidateForwarded(root, game, proxy, args);
            // Explicit overrides avoid proxy's next-to-exe fallback, while preserving user-supplied overrides.
            var all = new List<string>();
            if (!HasOption(args, "--settings-path")) { all.Add("--settings-path"); all.Add(config.settingsPath); }
            if (!HasOption(args, "--save-state-path")) { all.Add("--save-state-path"); all.Add(config.saveStatePath); }
            if (!HasOption(args, "--exe-path")) { all.Add("--exe-path"); all.Add(Path.Combine(game, "noita.exe")); }
            all.AddRange(args);
            var start = new ProcessStartInfo(Path.Combine(proxy, "noita_proxy.exe"), String.Join(" ", all.Select(Quote))) { UseShellExecute = false, WorkingDirectory = proxy };
            start.EnvironmentVariables["NP_SKIP_MOD_CHECK"] = "1";
            Console.WriteLine("Opening unchanged official proxy. Keep this launcher open until the proxy closes.");
            using (var process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; }
        }
        private static void ValidateForwarded(string root, string game, string proxy, string[] args) {
            foreach (string option in new [] { "--settings-path", "--save-state-path", "--exe-path" }) {
                for (int i = 0; i < args.Length; i++) {
                    string value = null;
                    if (args[i] == option) { if (++i >= args.Length) throw new ArgumentException("Missing value for " + option); value = args[i]; }
                    else if (args[i].StartsWith(option + "=", StringComparison.Ordinal)) value = args[i].Substring(option.Length + 1);
                    if (value == null) continue;
                    if (String.IsNullOrWhiteSpace(value)) throw new ArgumentException("Empty value for " + option);
                    string absolute = Path.GetFullPath(Path.IsPathRooted(value) ? value : Path.Combine(proxy, value));
                    if (option == "--exe-path") {
                        Guard(absolute);
                        if (!String.Equals(absolute, Path.Combine(game, "noita.exe"), StringComparison.OrdinalIgnoreCase)) throw new IOException("--exe-path must match the configured managed Noita folder. Use --coop-setup to change it.");
                    } else ValidateMutable(root, game, absolute);
                }
            }
        }
        private static bool HasOption(string[] args, string option) { return args.Any(a => a == option || a.StartsWith(option + "=", StringComparison.Ordinal)); }
        internal static string Quote(string value) {
            var b = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { b.Append('\\', slashes * 2 + 1); b.Append(c); }
                else { b.Append('\\', slashes); b.Append(c); } slashes = 0;
            }
            b.Append('\\', slashes * 2); return b.Append('"').ToString();
        }
        internal static void Guard(string path) {
            for (string p = Path.GetFullPath(path); !String.IsNullOrEmpty(p); p = Path.GetDirectoryName(p)) {
                if ((Directory.Exists(p) || File.Exists(p)) && (File.GetAttributes(p) & FileAttributes.ReparsePoint) != 0) throw new IOException("Links/reparse points are unsupported: " + p);
                if (Directory.Exists(Path.Combine(p, ".git")) || File.Exists(Path.Combine(p, ".git"))) throw new IOException("Co-op+ never updates a development checkout. Extract it elsewhere.");
            }
        }
    }
}
