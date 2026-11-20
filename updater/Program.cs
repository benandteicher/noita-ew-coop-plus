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
    internal static class Program {
        internal const string Repository = "benandteicher/noita-ew-coop-plus";
        internal const string Feed = "https://api.github.com/repos/" + Repository + "/releases/latest";
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 8 * 1024 * 1024 };
        internal static bool ForceConsole;
        [STAThread]
        private static int Main(string[] args) {
            if (args.Length == 2 && args[0] == "--internal-handoff") return ProxyHandoff.Worker(args[1]);
            try {
                Console.InputEncoding = new UTF8Encoding(false);
                Console.OutputEncoding = new UTF8Encoding(false);
                Console.Title = "Co-op+";
                if (args.Length < 3 || args[0] != "--internal-root" || args[2] != "--")
                    throw new ArgumentException("Open CoopPlus.exe to run the updater.");
                string root = Path.GetFullPath(args[1]); Guard(root);
                string[] forwarded = args.Skip(3).ToArray();
                if (forwarded.Length == 1 && forwarded[0] == "--coop-help") {
                    Console.WriteLine("Co-op+ bootstrap protocol 1. Open without arguments for setup/update/launch. Official proxy arguments are forwarded unchanged. --coop-check performs checks/installation without launching; --coop-setup finds/selects Noita; --coop-shortcut offers a desktop shortcut; --coop-console retains deliberate console launching."); return 0;
                }
                bool checkOnly = forwarded.Contains("--coop-check");
                bool setup = forwarded.Contains("--coop-setup");
                bool shortcut = forwarded.Contains("--coop-shortcut");
                ForceConsole = forwarded.Contains("--coop-console");
                forwarded = forwarded.Where(a => a != "--coop-check" && a != "--coop-setup" && a != "--coop-shortcut" && a != "--coop-console").ToArray();
                var config = Setup.Load(root, setup, forwarded);
                if (shortcut) { Setup.OfferShortcut(root, config.gameDir); return 0; }
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
            } catch (OperationCanceledException e) { Console.WriteLine(e.Message); return 0; }
            catch (Exception e) { Console.Error.WriteLine("Co-op+: " + e.Message); return 1; }
        }
        internal static void ValidateMutable(string root, string game, string path) {
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path)) throw new IOException("Invalid mutable settings/save path; use --coop-setup.");
            Guard(path);
            if (Within(path, Path.Combine(root, "current")) || (!String.IsNullOrWhiteSpace(game) && Path.IsPathRooted(game) && Within(path, Path.Combine(game, "mods")))) throw new IOException("Settings/saves must be outside managed payloads and active mods.");
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
            bool ready = current != null && !Setup.Stored(root).pendingInstall;
            if (ready) Engine.VerifyInstalled(root, game);
            ReleaseManifest next = null;
            Console.WriteLine("Checking approved Co-op+ releases...");
            try { next = ReleaseProvider(); }
            catch (UnsupportedBootstrapException) { throw; }
            catch (Exception e) { Console.WriteLine("Update check unavailable: " + e.Message); }
            ReleaseManifest seed = null;
            string seedDirectory = Path.Combine(root, "seed");
            if (!ready && File.Exists(Path.Combine(seedDirectory, "release.json"))) {
                Guard(seedDirectory);
                seed = Engine.ParseManifest(File.ReadAllText(Path.Combine(seedDirectory, "release.json")));
            }
            // A freshly extracted review bundle must not downgrade itself to an
            // older public Latest. Installed clients still use only the fixed feed.
            bool seedEligible = CompleteTarget(seed, current);
            bool feedEligible = CompleteTarget(next, current);
            bool useSeed = !ready && seedEligible && (!feedEligible || !IsNewer(next, seed));
            bool changed = false;
            if (!useSeed && feedEligible && (!ready || IsNewer(next, current))) {
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
                if (!seedEligible) throw new IOException("This Noita folder needs a complete installation of the current or newer build, but that package is unavailable. Keep existing proxy state and retry with the matching bootstrap bundle or an available approved release.");
                Console.WriteLine("Installing bundled setup candidate " + seed.version + " (build " + seed.build + "). This does not publish a release.");
                Engine.Install(root, game, Path.Combine(seedDirectory, "package.zip"), seed); changed = true;
            }
            Engine.VerifyInstalled(root, game);
            Setup.MarkInstalled(root);
            if (changed) { Console.WriteLine("Complete installation ready; restarting the managed updater."); return 42; }
            // This is deliberately in the NEW managed updater after redispatch.
            // Protocol-1 old updaters install before they know about this gate.
            if (checkOnly) { AutomaticBackup.Ensure(root, game, forwarded); Console.WriteLine("Installation verified. Check-only mode: no proxy launched."); return 0; }
            Engine.CheckNotBusy();
            return Launch(root, game, forwarded);
        }
        private static bool CompleteTarget(ReleaseManifest candidate, ReleaseManifest current) {
            if (candidate == null) return false;
            if (current == null) return true;
            if (candidate.build == current.build) return Engine.CompareVersions(candidate.version, current.version) == 0 && String.Equals(candidate.package.sha256, current.package.sha256, StringComparison.OrdinalIgnoreCase);
            return candidate.build > current.build && IsNewer(candidate, current);
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
            AutomaticBackup.PrepareLaunch(root, game, args);
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
            if (!ConsoleMode(args)) return ProxyHandoff.Launch(root, Path.Combine(proxy, "noita_proxy.exe"), proxy, all.ToArray());
            var start = ProxyHandoff.NormalStart(root, Path.Combine(proxy, "noita_proxy.exe"), proxy, all.ToArray());
            Console.WriteLine("Opening official proxy in the requested console mode.");
            using (var process = Process.Start(start)) { process.WaitForExit(); return process.ExitCode; }
        }
        internal static bool ConsoleMode(string[] args) { return ForceConsole || HasOption(args, "--host") || HasOption(args, "--lobby") || args.Any(a => a == "--help" || a == "-h"); }
        internal static void ValidateForwarded(string root, string game, string proxy, string[] args) {
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
