using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Net.Cache;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using CoopPlus;

namespace CoopPlusPlaytest {
    public sealed class StagingRelease {
        internal const string Repository = "benandteicher/noita-ew-coop-plus";
        internal const string Pages = "https://benandteicher.github.io/noita-ew-coop-plus/staging/";
        internal const string Feed = Pages + "feed.json";
        // Build 22's updater retains its installed launcher. It must decline
        // releases that require the new launcher and its export format.
        internal const int Protocol = 2;
        public int schema, launcherProtocol;
        public string channel, version, tag, name, sha256, manifestSha256;
        public long build, size, releaseId, assetId;
        internal string AssetUrl { get { return "https://github.com/" + Repository + "/releases/download/" + tag + "/" + name; } }
        internal string ReleaseApi { get { return "https://api.github.com/repos/" + Repository + "/releases/tags/" + tag; } }

        internal static bool ValidHash(string value) {
            return value != null && Regex.IsMatch(value, @"\A[0-9a-f]{64}\z");
        }
        internal static string Runtime(long build, string version) {
            Version parsed;
            if (build < 1 || build > 999999999 || version == null || version.Length > 31 ||
                !System.Version.TryParse(version, out parsed) || parsed.Revision < 0 || parsed.ToString() != version)
                throw new InvalidDataException("Invalid Staging build or version.");
            return "coopplus-staging-b" + build.ToString(CultureInfo.InvariantCulture) + "-v" + version;
        }
        internal void Validate() {
            Runtime(build, version);
            string number = build.ToString(CultureInfo.InvariantCulture);
            if (schema != 1 || launcherProtocol != Protocol || channel != Playtest.Channel ||
                tag != "coopplus-staging-v" + version + "-b" + number ||
                name != "Noita-CoopPlus-" + version + "-build" + number + "-STAGING-win.zip" ||
                size < 1 || size > Engine.MaxPackageBytes || !ValidHash(sha256) || !ValidHash(manifestSha256)
                || releaseId <= 0 || assetId <= 0)
                throw new InvalidDataException("The feed does not select one approved Staging release.");
        }
        static object Value(Dictionary<string, object> row, string key) {
            object value; return row.TryGetValue(key, out value) ? value : null;
        }
        static long Integer(Dictionary<string, object> row, string key) {
            object value = Value(row, key);
            return value is int || value is long ? Convert.ToInt64(value, CultureInfo.InvariantCulture) : -1;
        }
        internal void VerifyGitHub(string metadata) {
            Validate();
            var release = StagingUpdate.Parse<Dictionary<string, object>>(metadata);
            if (release == null || !Equals(Value(release, "tag_name"), tag) || !Equals(Value(release, "draft"), false)
                || !Equals(Value(release, "prerelease"), true) || !Equals(Value(release, "immutable"), true)
                || Integer(release, "id") != releaseId)
                throw new InvalidDataException("The selected GitHub release is not a published immutable Staging prerelease.");
            var assets = Value(release, "assets") as IEnumerable;
            if (assets == null) throw new InvalidDataException("GitHub returned no release assets.");
            var matches = assets.Cast<object>().OfType<Dictionary<string, object>>().Where(a => Equals(Value(a, "name"), name)).ToArray();
            if (matches.Length != 1) throw new InvalidDataException("The selected Staging ZIP is unavailable.");
            var asset = matches[0];
            if (Integer(asset, "id") != assetId || Integer(asset, "size") != size ||
                !Equals(Value(asset, "state"), "uploaded") || !Equals(Value(asset, "browser_download_url"), AssetUrl)
                || !Equals(Value(asset, "digest"), "sha256:" + sha256))
                throw new InvalidDataException("The immutable Staging asset does not match the selected size and SHA-256.");
        }
    }

    internal static class StagingUpdate {
        const string UpdateDir = "staging-updates";
        internal const long PreviousBuild = 22;
        const string PreviousVersion = "1.9.18.15";
        const string PreviousManifest = "3f35fc14b55596d0789454d870c0f56712c5c0838770760161772c32d75d5f48";
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);

        public sealed class Pointer {
            public long build;
            public string version, sha256;
        }
        public sealed class Pending {
            public int schema;
            public Pointer previous, next;
        }

        internal static T Parse<T>(string json) {
            return new JavaScriptSerializer { MaxJsonLength = 1024 * 1024, RecursionLimit = 24 }.Deserialize<T>(json);
        }
        static string Serialize(object value) {
            return new JavaScriptSerializer().Serialize(value);
        }
        static T Read<T>(string path) {
            Playtest.Canon(path);
            if (!File.Exists(path) || new FileInfo(path).Length > 1024 * 1024)
                throw new IOException("Missing or oversized Staging update proof.");
            return Parse<T>(File.ReadAllText(path, Utf8));
        }
        static void Put(string path, object data) {
            string temporary = Playtest.Canon(path + ".new-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            try {
                using (var file = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    byte[] bytes = Utf8.GetBytes(Serialize(data));
                    file.Write(bytes, 0, bytes.Length); file.Flush(true);
                }
                File.Move(temporary, Playtest.Canon(path));
            } finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
        static string GetText(string url) {
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            var address = new Uri(url);
            var request = (HttpWebRequest)WebRequest.Create(address);
            request.UserAgent = "CoopPlus-Staging/1";
            request.Accept = "application/json";
            request.Timeout = 12000;
            request.ReadWriteTimeout = 12000;
            request.AllowAutoRedirect = false;
            request.CachePolicy = new RequestCachePolicy(RequestCacheLevel.NoCacheNoStore);
            var timer = Stopwatch.StartNew();
            using (var response = (HttpWebResponse)request.GetResponse())
            using (var input = response.GetResponseStream())
            using (var output = new MemoryStream()) {
                if (response.StatusCode != HttpStatusCode.OK || response.ResponseUri != address)
                    throw new IOException("Unexpected Staging metadata URL.");
                var buffer = new byte[16384]; int read;
                while ((read = input.Read(buffer, 0, buffer.Length)) != 0) {
                    if (output.Length + read > 1024 * 1024 || timer.Elapsed > TimeSpan.FromSeconds(30))
                        throw new IOException("Staging metadata exceeds its size or time limit.");
                    output.Write(buffer, 0, read);
                }
                return Utf8.GetString(output.ToArray());
            }
        }
        internal static StagingRelease Check() {
            string feed;
            try { feed = GetText(StagingRelease.Feed); }
            catch (WebException error) {
                using (var response = error.Response as HttpWebResponse) {
                    if (response != null && response.StatusCode == HttpStatusCode.NotFound)
                        throw new IOException("No Staging release is selected on the download site yet.");
                    throw;
                }
            }
            var chosen = Parse<StagingRelease>(feed);
            if (chosen == null) throw new InvalidDataException("No approved Staging release is selected.");
            chosen.Validate();
            chosen.VerifyGitHub(GetText(chosen.ReleaseApi));
            return chosen;
        }
        internal static bool IsNewer(StagingRelease selected, VerifiedPayload current) {
            selected.Validate();
            var manifest = current.manifest;
            if (selected.build == manifest.build) {
                if (selected.version != manifest.version || selected.manifestSha256 != current.hash)
                    throw new InvalidDataException("The Staging build number was reused with different bytes.");
                return false;
            }
            if (selected.build < manifest.build) return false;
            if (Engine.CompareVersions(selected.version, manifest.version) <= 0)
                throw new InvalidDataException("The selected Staging version does not advance the installed version.");
            return true;
        }
        static Pointer Point(VerifiedPayload payload) {
            return new Pointer { build = payload.manifest.build, version = payload.manifest.version, sha256 = payload.hash };
        }
        static string Slot(string package, long build) {
            if (build < 1 || build > 999999999) throw new InvalidDataException("Invalid Staging slot.");
            return Playtest.Child(package, UpdateDir + "/b" + build.ToString(CultureInfo.InvariantCulture));
        }
        static StagingRelease Approval(string slot) {
            var stamp = Read<StagingRelease>(Playtest.Child(slot, "approval.json"));
            if (stamp == null) throw new IOException("Missing Staging slot approval.");
            stamp.Validate(); return stamp;
        }
        static VerifiedPayload Resolve(string package, Pointer point, bool verifyFiles = false) {
            if (point == null || !StagingRelease.ValidHash(point.sha256)) throw new IOException("Invalid Staging version pointer.");
            StagingRelease.Runtime(point.build, point.version);
            if (point.build == Playtest.Build) {
                if (point.version != Playtest.Version || point.sha256 != StagingBundle.ManifestSha256)
                    throw new IOException("Bundled Staging build disagrees with the installed profile.");
                return Playtest.LoadPayload(package, Playtest.Child(package, Playtest.PayloadName), point.build,
                    point.version, point.sha256, verifyFiles);
            }
            if (point.build == PreviousBuild) {
                if (point.version != PreviousVersion || point.sha256 != PreviousManifest)
                    throw new IOException("The private build 22 profile does not match the sealed Staging 22 release.");
                Playtest.ValidatePackage(package);
                using (var previous = typeof(StagingUpdate).Assembly.GetManifestResourceStream("CoopPlus.Staging22.manifest")) {
                    if (previous == null) throw new IOException("The sealed build 22 proof is missing.");
                    using (var memory = new MemoryStream()) {
                        previous.CopyTo(memory);
                        return new VerifiedPayload { package=package, hash=point.sha256,
                            manifest=Playtest.VerifyManifest(memory.ToArray(), point.build, point.version, point.sha256) };
                    }
                }
            }
            string slot = Slot(package, point.build);
            StagingRelease receipt = Approval(slot);
            if (receipt.build != point.build || receipt.version != point.version || receipt.manifestSha256 != point.sha256)
                throw new IOException("Approved Staging slot disagrees with the active profile.");
            return Playtest.LoadPayload(package, Playtest.Child(slot, Playtest.PayloadName), point.build,
                point.version, point.sha256, verifyFiles);
        }
        internal static VerifiedPayload Current(string package) {
            string root = Playtest.Child(package, Playtest.PrivateName);
            if (!Directory.Exists(root)) return Playtest.LoadPayload(package);
            Ready ready = Playtest.ReadReady(root);
            return Resolve(package, new Pointer { build = ready.build, version = ready.version, sha256 = ready.payloadHash });
        }
        internal static void Recover(string package, SessionAudit audit) {
            string root = Playtest.Child(package, Playtest.PrivateName);
            string pending = Playtest.Child(package, UpdateDir + "/pending.json");
            if (!File.Exists(pending)) {
                if (File.Exists(Playtest.Child(root, "runtime-upgrade-journal.json")))
                    throw new IOException("The stopped update is missing its approved package references. Keep the Staging folder for review.");
                return;
            }
            Playtest.CheckStopped();
            var job = Read<Pending>(pending);
            if (job == null || job.schema != 1) throw new IOException("Unreadable Staging update proof.");
            var before = Resolve(package, job.previous);
            var after = Resolve(package, job.next);
            RuntimeMigration.Recover(root, before, after);
            Ready ready = Playtest.ReadReady(root);
            bool committed = Playtest.Matches(ready, after);
            if (!committed && !Playtest.Matches(ready, before)) throw new IOException("The Staging profile disagrees with the pending update.");
            Playtest.VerifyRuntime(root, (committed ? after : before).manifest);
            Playtest.VerifySaveFolders(root);
            File.Delete(pending);
            if (audit != null) audit.Write("update-recovered", new { result = committed ? "complete" : "rolled-back" });
        }
        static void Extract(string archiveFile, string destination, StagingRelease release) {
            if (new FileInfo(archiveFile).Length != release.size || Engine.Sha256(archiveFile) != release.sha256)
                throw new InvalidDataException("Staging ZIP size or SHA-256 mismatch.");
            var outer = new HashSet<string>(new[]{"Co-op+ Staging.exe","STAGING-PLAYER-INSTRUCTIONS.txt","BUILD-PROVENANCE.json"}, StringComparer.Ordinal);
            using (var archive = ZipFile.OpenRead(archiveFile)) {
                if (archive.Entries.Count > 11000) throw new InvalidDataException("Staging ZIP contains too many entries.");
                long expanded = 0;
                var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                var buffer = new byte[65536];
                foreach (var entry in archive.Entries) {
                    string name = entry.FullName;
                    uint attr = unchecked((uint)entry.ExternalAttributes);
                    uint kind = (attr >> 16) & 0xf000;
                    if (!seen.Add(name) || (attr & 0x400) != 0 || kind != 0 && kind != 0x8000
                        || entry.Length > Engine.MaxExpandedBytes - expanded)
                        throw new InvalidDataException("Unsafe, duplicate or oversized Staging ZIP entry.");
                    expanded += entry.Length;
                    string target = Playtest.Child(destination, name);
                    if (outer.Remove(name)) continue; // Updaters keep their installed launcher.
                    if (!name.StartsWith(Playtest.PayloadName + "/", StringComparison.Ordinal))
                        throw new InvalidDataException("Unexpected Staging ZIP layout.");
                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                    using (var input = entry.Open())
                    using (var output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                        long total = 0; int n;
                        while ((n = input.Read(buffer, 0, buffer.Length)) != 0) {
                            if ((total += n) > entry.Length) throw new InvalidDataException("Invalid expanded Staging file size.");
                            output.Write(buffer, 0, n);
                        }
                        if (total != entry.Length) throw new InvalidDataException("Truncated Staging file.");
                        output.Flush(true);
                    }
                }
                if (outer.Count != 0) throw new InvalidDataException("Staging update did not contain the complete portable package.");
            }
        }
        internal static VerifiedPayload Stage(string package, StagingRelease release, Action<string, string, long, string> download) {
            release.Validate();
            string slot = Slot(package, release.build);
            if (Directory.Exists(slot)) {
                if (Serialize(Approval(slot)) != Serialize(release)) throw new IOException("A different package already uses this Staging build.");
                return Resolve(package, new Pointer { build=release.build, version=release.version, sha256=release.manifestSha256 }, true);
            }
            string incoming = Playtest.Child(package, UpdateDir + "/incoming-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            Directory.CreateDirectory(incoming);
            try {
                string archive = Playtest.Child(incoming, "payload.zip");
                download(release.AssetUrl, archive, release.size, release.sha256);
                Extract(archive, incoming, release);
                Playtest.LoadPayload(package, Playtest.Child(incoming, Playtest.PayloadName),
                                    release.build, release.version, release.manifestSha256);
                Put(Playtest.Child(incoming, "approval.json"), release);
                File.Delete(archive);
                Directory.Move(incoming, slot);
                return Resolve(package, new Pointer { build=release.build, version=release.version, sha256=release.manifestSha256 });
            } finally {
                if (Directory.Exists(incoming)) {
                    foreach (string file in Playtest.Files(incoming)) Playtest.Canon(file);
                    Directory.Delete(incoming, true);
                }
            }
        }
        internal static void Apply(string package, VerifiedPayload previous, VerifiedPayload next) {
            Playtest.CheckStopped();
            string root = Playtest.Child(package, Playtest.PrivateName);
            Ready ready = Playtest.ReadReady(root);
            if (!Playtest.Matches(ready, previous)) throw new IOException("The installed Staging build changed while the download was staged.");
            var job = new Pending { schema=1, previous=Point(previous), next=Point(next) };
            string updates = Playtest.Child(package, UpdateDir);
            Directory.CreateDirectory(updates);
            string file = Playtest.Child(updates, "pending.json");
            Put(file, job);
            RuntimeMigration.Upgrade(root, ready, previous, next);
            File.Delete(file);
        }
        internal static void Install(string package, VerifiedPayload current, StagingRelease choice, SessionAudit audit) {
            Playtest.CheckStopped();
            if (!IsNewer(choice, current)) throw new IOException("Select a newer Staging release.");
            // Recheck the chosen release after confirmation. Advancing the
            // Pages pointer cannot silently change this already chosen build.
            choice.VerifyGitHub(GetText(choice.ReleaseApi));
            audit.Write("update-download", new { runtimeId=StagingRelease.Runtime(choice.build, choice.version) });
            var verified = Stage(package, choice, Engine.StageDownload);
            audit.Write("update-staged", new { runtimeId=verified.manifest.runtimeId });
            ApplyAndReport(package, current, verified, audit);
        }
        internal static void InstallBundled(string package, VerifiedPayload current, SessionAudit audit) {
            Playtest.CheckStopped();
            if (current.manifest.build != PreviousBuild) throw new IOException("The installed Staging build changed before applying the bundle.");
            var verified = Playtest.LoadPayload(package);
            audit.Write("update-staged", new { runtimeId=verified.manifest.runtimeId });
            ApplyAndReport(package, current, verified, audit);
        }
        static void ApplyAndReport(string package, VerifiedPayload current, VerifiedPayload verified, SessionAudit audit) {
            try {
                Apply(package, current, verified);
            } catch {
                Recover(package, audit);
                if (Playtest.Matches(Playtest.ReadReady(Playtest.Child(package,Playtest.PrivateName)), verified)) {
                    audit.Bind(verified.manifest);
                    audit.Write("update-complete", new { result="complete" });
                    return;
                }
                throw;
            }
            audit.Bind(verified.manifest);
            audit.Write("update-complete", new { result="complete" });
        }
    }
}
