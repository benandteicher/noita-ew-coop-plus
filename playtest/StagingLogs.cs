using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace CoopPlusPlaytest {
    internal static class StagingLogs {
        internal const string DirectoryName = "staging-logs";
        internal const int ZipLimit = 20000000;
        const int PartBudget = 18500000;
        const int ChunkBudget = 512 * 1024;
        const int MaximumRecord = 64 * 1024;
        const int MaximumIndexedRuns = 8;
        const string AcceptedWorld = "actual-world-receipt-accepted";
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);
        static readonly Regex AcceptedIdentity = new Regex(@"(?:^| )run_id=([0-9a-fA-F]{16}) rules_id=[0-9a-fA-F]{16} legacy_unverified=false\z");
        static readonly Regex LobbyRole = new Regex(@"(?:^| )role=(host|guest)(?: |$)");
        static readonly Regex Runtime = new Regex(@"^coopplus-staging-b[1-9][0-9]{0,8}-v[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$");
        static readonly HashSet<string> Events = new HashSet<string>((
            "launcher-start launcher-finish runtime-selected diagnostic-retention phase preparation-cancel-requested " +
            "launcher-failure update-check update-download update-staged update-failure update-complete update-recovered " +
            "proxy-start proxy-window-visible proxy-window-wait proxy-exit proxy-exit-before-window-observed " +
            "proxy-exit-after-supervision-failure game-start game-exit game-exit-after-supervision-failure " +
            "game-log-retained game-log-retention session-processes-ended supervision-failure output-capture-ended " +
            "foreign-conflict-not-owned unresolved-private-identities-retained intentional-restart-accepted " +
            "process-entry proxy-ui-ready steam-init-start steam-init-success steam-init-failure startup-failure " +
            "noita-prefs noita-launch-start noita-launch-exit noita-launch-failure noita-launch-request " +
            "local-game-connected local-game-detached host-settings-selected host-settings-locked " +
            "world-rules-refused world-rules-quarantine-failed actual-world-receipt actual-world-receipt-repeat " +
            "actual-world-receipt-accepted new-world-old-proxy-checkpoint-preserved host-checkpoint-write-failed " +
            "lobby-decision lobby-result peer-transition host-readiness first-terrain-fault " +
            "terrain-invalid-capture terrain-constructor-blocked terrain-protocol-stopped diagnostic-limit fresh-host-settings " +
            "host-settings-transition intentional-restart-requested native-window-error network-session-failed network-worker-join-failed " +
            "panic preferences-persisted process-return run-settings-persisted settings-save-failed ui-error user-close-request world-agreement-failure-repeat"
        ).Split(' '), StringComparer.Ordinal);
        static readonly HashSet<string> Phases = new HashSet<string>(new[]{
            "Verifying frozen package", "Frozen package verified", "Verifying private game and retained Continue",
            "Verifying private frozen runtime", "Verifying owned Noita source", "Copying owned Noita into private storage",
            "Verifying complete private copy", "Private copy prepared", "Private game and runtime ready",
            "Verifying accepted predecessor before private runtime upgrade",
            "Updating private runtime while retaining saves and settings",
            "Private runtime upgraded; existing Continue and settings retained",
            "Recovering interrupted private runtime update", "Interrupted managed update rolled back; private progress retained",
        }, StringComparer.Ordinal);
        static readonly Regex KnownLuaFrame = new Regex(@"\bmods/quant\.ew/files/(?:core/run_session|system/world_sync/(?:terrain_feedback|world_sync)|system/coop_revival/coop_revival|system/shared_income/shared_income|system/shop_hover/shop_hover)\.lua:[0-9]{1,6}\b");
        static readonly Regex MessageFields = new Regex(
            @"\b(generation|intent|kind|boundary|same_stopped_world|run_matches|rules_match|was_ready|mode|legacy_unverified|role|decision|result|reason|world|chunk_x|chunk_y|cell_x|cell_y|material|flags|index|code|peer_count|ready|coop_enabled|statue|portal|shared_income|shop_hover|share_gold)=(-?[0-9]{1,10}(?![a-zA-Z0-9._-])|[a-zA-Z][a-zA-Z0-9_-]{0,32})(?![a-zA-Z0-9._-])");
        static readonly HashSet<string> Values = new HashSet<string>((
            "host guest new continue legacy world player fresh matching waiting ready true false alternate normal " +
            "LocalAlternate LocalNormal accepted refused connected disconnected reset requested closed timeout " +
            "invalid_flags constructor_declined protocol persistence incompatible wrong_world wrong_rules wrong_intent missing_receipt"
        ).Split(' '), StringComparer.Ordinal);
        static readonly HashSet<string> NumericFields = new HashSet<string>((
            "generation world chunk_x chunk_y cell_x cell_y material flags index code peer_count"
        ).Split(' '), StringComparer.Ordinal);
        static readonly HashSet<string> Results = new HashSet<string>(new[]{
            "failure","proxy-exit-zero","preparation-cancelled","complete","unavailable","current","declined","rolled-back",
        }, StringComparer.Ordinal);

        sealed class Session {
            internal string Label, Build = "unknown", Time = "unknown";
            internal readonly SortedSet<string> Roles = new SortedSet<string>(StringComparer.Ordinal);
            internal readonly SortedSet<string> SharedRuns = new SortedSet<string>(StringComparer.Ordinal);
            internal bool MoreRuns;
        }
        sealed class Source {
            internal string File, Label, Kind;
            internal Session Session;
            internal long? SnapshotLength;
        }
        internal sealed class ExportResult {
            internal string Directory;
            internal string[] Files;
            internal long Entries;
            internal int Unreadable;
        }
        sealed class Record {
            internal string Content;
            internal long Bytes;
        }

        static bool Plain(string path) {
            try { Playtest.Canon(path); return (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0; }
            catch (IOException) { return false; }
            catch (UnauthorizedAccessException) { return false; }
        }
        static IEnumerable<Source> Discover(string package, List<string> issues) {
            string root = Path.Combine(package, DirectoryName);
            int sessionNumber = 0, sourceNumber = 0;
            if (Directory.Exists(root) && !Plain(root)) issues.Add("The Staging log directory could not be read safely.");
            if (Directory.Exists(root) && Plain(root)) {
                foreach (string folder in Directory.EnumerateDirectories(root).OrderBy(x => x, StringComparer.Ordinal)) {
                    Guid id;
                    if (!Guid.TryParseExact(Path.GetFileName(folder), "N", out id)) continue;
                    var session = new Session { Label = "S" + (++sessionNumber).ToString("D3") };
                    if (!Plain(folder)) { issues.Add(session.Label + " is unavailable."); continue; }
                    session.Time = Iso(Directory.GetCreationTimeUtc(folder));
                    foreach (string path in Directory.EnumerateFiles(folder).OrderBy(SourceOrder).ThenBy(x => x, StringComparer.Ordinal)) {
                        string kind = SessionAudit.DiagnosticKind(Path.GetFileName(path));
                        if (kind == null) continue;
                        yield return new Source { File = path, Kind = kind, Session = session, Label = "log-" + (++sourceNumber).ToString("D4") };
                    }
                }
            }
            // These are the only live paths the private Noita and proxy use.
            string privateRoot = Path.Combine(package, Playtest.PrivateName);
            var current = new Session { Label = "current-private", Build = "unknown" };
            foreach (var item in new[]{
                new[]{"game/logger.txt", "Noita live"}, new[]{"runtime/bin/ew_log.txt", "Proxy live"},
                new[]{"runtime/bin/ew_log_old.txt", "Proxy previous"},
            }) {
                string path = Path.Combine(privateRoot, item[0].Replace('/', Path.DirectorySeparatorChar));
                if (File.Exists(path)) yield return new Source { File = path, Kind = item[1], Session = current, Label = "log-" + (++sourceNumber).ToString("D4") };
            }
        }
        static int SourceOrder(string path) {
            string name = Path.GetFileName(path);
            return name == "session.json" ? 0 : name == "events.jsonl" ? 1 : name.EndsWith(".jsonl", StringComparison.Ordinal) ? 2 : 3;
        }
        static string Iso(DateTime value) {
            return value.Year >= 2000 && value.Year < 2100 ? value.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss.fff 'UTC'", CultureInfo.InvariantCulture) : "unknown";
        }
        static Dictionary<string, object> Object(object value) { return value as Dictionary<string, object>; }
        static object Field(Dictionary<string, object> row, string key) { object value; return row != null && row.TryGetValue(key, out value) ? value : null; }
        static string Str(Dictionary<string, object> row, string key) { return Field(row, key) as string; }
        static string Time(Dictionary<string, object> row, string field) {
            DateTimeOffset stamp;
            if (DateTimeOffset.TryParse(Str(row, field), CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out stamp)) return Iso(stamp.UtcDateTime);
            object value = Field(row, "utcUnixMs");
            long ms;
            if ((value is int || value is long) && Int64.TryParse(value.ToString(), out ms) && ms >= 946684800000L && ms < 4102444800000L)
                return Iso(new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc).AddMilliseconds(ms));
            return "unknown";
        }
        static void Bind(Session session, Dictionary<string, object> row) {
            string runtime = Str(row, "runtimeId");
            if (runtime != null && runtime.Length < 65 && Runtime.IsMatch(runtime)) session.Build = runtime;
            if (session.Build == "unknown") {
                object build = Field(row, "launcherBuild");
                if (build is int && (int)build > 0) session.Build = "launcher build " + ((int)build).ToString(CultureInfo.InvariantCulture);
            }
        }
        static Dictionary<string, object> ReadObject(string text, JavaScriptSerializer json) {
            if (String.IsNullOrEmpty(text) || text[0] != '{') return null;
            try { return Object(json.DeserializeObject(text)); }
            catch (ArgumentException) { return null; }
            catch (InvalidOperationException) { return null; }
        }
        static string SharedRun(string kind, string message) {
            if (kind != AcceptedWorld || message == null) return null;
            Match identity = AcceptedIdentity.Match(message);
            if (!identity.Success || identity.Groups[1].Value == "0000000000000000") return null;
            // The existing ID is a random host token. Hash it in an export-only
            // domain without local session data, rules, Steam or build IDs.
            string input = "Co-op+ shared run label v1:" + identity.Groups[1].Value.ToLowerInvariant();
            return "R-" + Playtest.HashBytes(Utf8.GetBytes(input)).Substring(0, 12);
        }
        static void ReadProxyMetadata(Source source, JavaScriptSerializer json, List<string> issues) {
            if (!Plain(source.File)) return;
            try {
                foreach (Record record in ReadSnapshot(source)) {
                    var row = ReadObject(record.Content, json);
                    string evt = Str(row, "event"), message = Str(row, "message");
                    if (evt == "process-entry") Bind(source.Session, ReadObject(message, json));
                    if ((evt == "lobby-decision" || evt == "lobby-result") && message != null) {
                        Match role = LobbyRole.Match(message);
                        if (role.Success) source.Session.Roles.Add(role.Groups[1].Value);
                    }
                    string label = SharedRun(evt, message);
                    if (label != null && !source.Session.SharedRuns.Contains(label)) {
                        if (source.Session.SharedRuns.Count < MaximumIndexedRuns) source.Session.SharedRuns.Add(label);
                        else source.Session.MoreRuns = true;
                    }
                }
            } catch (UnauthorizedAccessException) { issues.Add(source.Label + " correlation metadata became unreadable."); }
            catch (IOException) { issues.Add(source.Label + " correlation metadata could not be read completely."); }
        }
        static string ProjectRecord(Source source, Record record, JavaScriptSerializer json) {
            string text = record.Content;
            if (text == null) return "Oversized record withheld; " + record.Bytes + " source bytes.";
            var row = ReadObject(text, json);
            if (row != null) {
                bool proxy = source.Kind == "Proxy events";
                string evt = proxy ? Str(row, "event") : Str(row, "kind") ?? Str(row, "event");
                if (source.Kind == "Launcher session") {
                    Bind(source.Session, row);
                    string started = Time(row, "startedUtc"), ended = Time(row, "finishedUtc");
                    if (started != "unknown") source.Session.Time = started;
                    string result = Str(row, "result");
                    return "Launcher session started=" + started + " finished=" + ended + (result != null && Results.Contains(result) ? " result=" + result : "");
                }
                if (evt != null && Events.Contains(evt)) {
                    string time = Time(row, "utc"), message = Str(row, "message");
                    var detail = Object(Field(row, "detail"));
                    Bind(source.Session, detail);
                    if (evt == "process-entry" && message != null) Bind(source.Session, ReadObject(message, json));
                    var fields = new List<string>();
                    if (evt == "phase" && Phases.Contains(Str(detail, "name"))) fields.Add(Str(detail, "name"));
                    foreach (string key in new[]{"exitCode","proxyExitCode","restarts","number","hashedFiles","hashedBytes","capturedBytes","sourceBytes","hresult"}) {
                        object number = Field(detail, key);
                        if (number is int || number is long) fields.Add(key + "=" + Convert.ToString(number, CultureInfo.InvariantCulture));
                    }
                    string result = Str(detail, "result");
                    if (result != null && Results.Contains(result)) fields.Add("result=" + result);
                    if (message != null) foreach (Match match in MessageFields.Matches(message)) {
                        string name = match.Groups[1].Value, value = match.Groups[2].Value;
                        int ignored;
                        if (Values.Contains(value) || NumericFields.Contains(name) && Int32.TryParse(value, out ignored)) {
                            if (name == "role" && (value == "host" || value == "guest")) source.Session.Roles.Add(value);
                            fields.Add(name + "=" + value);
                        }
                        if (fields.Count >= 24) break;
                    }
                    // Offered or refused IDs cannot establish correlation.
                    string label = proxy ? SharedRun(evt, message) : null;
                    if (label != null) fields.Add("shared_run=" + label);
                    return time + " " + evt + (fields.Count == 0 ? "" : " " + String.Join(" ", fields.ToArray()));
                }
            }
            // Free-form output can contain chat, lobby codes, identities and
            // file locations. Keep every record position and known fault class
            // without copying arbitrary console text to an export.
            string trimmed = text.Trim();
            if (Events.Contains(trimmed)) return trimmed;
            if (trimmed.Contains("Invalid terrain flags")) return "Invalid terrain flags";
            if (trimmed.Contains("constructor returned no cell")) return "Terrain constructor declined a cell";
            Match lua = KnownLuaFrame.Match(trimmed);
            if (lua.Success) return "Co-op+ Lua frame " + lua.Value;
            if (trimmed.StartsWith("[Prior log exceeds", StringComparison.Ordinal)) return "Previous capture reached its retention limit";
            return "Record content withheld; " + record.Bytes + " source bytes.";
        }

        static IEnumerable<Record> ReadSnapshot(Source source) {
            using (var input = new FileStream(source.File, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            using (var line = new MemoryStream()) {
                long remaining = source.SnapshotLength ?? input.Length, length = 0;
                source.SnapshotLength = remaining;
                byte[] block = new byte[65536];
                while (remaining > 0) {
                    int count = input.Read(block, 0, (int)Math.Min(remaining, block.Length));
                    if (count == 0) throw new IOException("A diagnostic file was shortened during export.");
                    remaining -= count;
                    int start = 0;
                    while (start < count) {
                        int end = Array.IndexOf(block, (byte)10, start, count - start);
                        int n = (end < 0 ? count : end) - start;
                        length += n;
                        if (length <= MaximumRecord) line.Write(block, start, n);
                        if (end < 0) break;
                        yield return new Record { Bytes = length, Content = length <= MaximumRecord ? Utf8.GetString(line.GetBuffer(), 0, (int)line.Length).TrimEnd('\r') : null };
                        line.SetLength(0); length = 0; start = end + 1;
                    }
                }
                if (length > 0) yield return new Record { Bytes = length, Content = length <= MaximumRecord ? Utf8.GetString(line.GetBuffer(), 0, (int)line.Length).TrimEnd('\r') : null };
            }
        }

        sealed class Part : IDisposable {
            readonly FileStream stream;
            readonly ZipArchive zip;
            readonly List<string> index = new List<string>();
            internal long Bytes;
            internal int Entries { get { return index.Count; } }
            internal readonly string Path;
            internal Part(string path) {
                Path = path;
                stream = new FileStream(path, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
                zip = new ZipArchive(stream, ZipArchiveMode.Create, true);
            }
            internal void Add(Source source, int chunk, long first, long last, byte[] bytes) {
                string name = source.Session.Label + "/" + source.Label + "-" + chunk.ToString("D4") + ".txt";
                using (var entry = zip.CreateEntry(name, CompressionLevel.Optimal).Open()) entry.Write(bytes, 0, bytes.Length);
                index.Add(name + " | " + source.Kind + " | " + source.Session.Time + " | " + source.Session.Build +
                    " | " + (source.Session.Roles.Count == 0 ? "role unknown" : String.Join("/", source.Session.Roles.ToArray())) +
                    " | " + (source.Session.SharedRuns.Count == 0 ? "none recorded" : String.Join(", ", source.Session.SharedRuns)) +
                    (source.Session.MoreRuns ? " (more in accepted events)" : "") +
                    " | records " + first + "-" + last);
                Bytes += bytes.Length;
            }
            internal void Finish(List<string> issues) {
                using (var entry = new StreamWriter(zip.CreateEntry("INDEX.txt", CompressionLevel.Optimal).Open(), Utf8)) {
                    entry.WriteLine("Co-op+ Staging diagnostic export");
                    entry.WriteLine("Created " + Iso(DateTime.UtcNow));
                    entry.WriteLine("Session folders are local to each export. Match accepted runs across PCs by their R- labels.");
                    entry.WriteLine("A run label is shown on its accepted event. Failed attempts can be matched by UTC time and role.");
                    entry.WriteLine("Each retained source record has an entry or a privacy marker. Live files are read to their opening length.");
                    entry.WriteLine("Untrusted text, identities, paths, saved games and configurations are excluded.");
                    entry.WriteLine();
                    entry.WriteLine("Entry | Source | Time | Runtime build | Roles | Accepted shared runs | Source record range");
                    foreach (string row in index) entry.WriteLine(row);
                    if (index.Count == 0) entry.WriteLine("This part has no readable diagnostic records.");
                    foreach (string issue in issues.Take(100)) entry.WriteLine(issue);
                    if (issues.Count > 100) entry.WriteLine((issues.Count - 100) + " additional source warnings.");
                }
            }
            public void Dispose() { try { zip.Dispose(); } finally { stream.Dispose(); } }
        }

        internal static ExportResult Export(string package) {
            package = Playtest.Canon(package);
            string directory = Playtest.Child(package, "diagnostic-exports");
            System.IO.Directory.CreateDirectory(directory);
            string name = "CoopPlus-Staging-Logs-" + DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + "-" + Guid.NewGuid().ToString("N").Substring(0, 8);
            var staged = new List<string>();
            var result = new ExportResult { Directory = directory };
            var issues = new List<string>();
            var json = new JavaScriptSerializer { MaxJsonLength = MaximumRecord, RecursionLimit = 20 };
            Part part = null;
            Action next = () => {
                if (part != null) { part.Finish(new List<string>()); part.Dispose(); part = null; }
                string temporary = Playtest.Child(directory, name + "-" + (staged.Count + 1).ToString("D3") + ".partial");
                part = new Part(temporary); staged.Add(temporary);
            };
            try {
                next();
                var sources = Discover(package, issues).ToArray();
                // Prime the indexes before writing any split part. Reuse each
                // proxy file's opening length so an append cannot disagree with its index.
                foreach (Source proxy in sources.Where(source => source.Kind == "Proxy events")) ReadProxyMetadata(proxy, json, issues);
                foreach (Source source in sources) {
                    if (!Plain(source.File)) { issues.Add(source.Label + " is unreadable or linked."); continue; }
                    if (source.Session.Time == "unknown") source.Session.Time = Iso(File.GetLastWriteTimeUtc(source.File));
                    long line = 0, first = 1; int chunk = 0;
                    using (var buffer = new MemoryStream()) {
                        Action flush = () => {
                            if (buffer.Length == 0) return;
                            if (part.Bytes + buffer.Length > PartBudget || part.Entries >= 128) next();
                            part.Add(source, ++chunk, first, line, buffer.ToArray());
                            first = line + 1; buffer.SetLength(0);
                        };
                        try {
                            foreach (Record record in ReadSnapshot(source)) {
                                line++; result.Entries++;
                                byte[] row = Utf8.GetBytes(line.ToString(CultureInfo.InvariantCulture) + " " + ProjectRecord(source, record, json) + "\n");
                                buffer.Write(row, 0, row.Length);
                                if (buffer.Length >= ChunkBudget) flush();
                            }
                        } catch (UnauthorizedAccessException) { issues.Add(source.Label + " became unreadable."); }
                        catch (IOException) { issues.Add(source.Label + " could not be read completely."); }
                        flush();
                    }
                }
                part.Finish(issues); part.Dispose(); part = null;
                var complete = new List<string>();
                foreach (string temporary in staged) {
                    if (new FileInfo(temporary).Length >= ZipLimit) throw new IOException("Diagnostic archive exceeded its size limit.");
                }
                for (int i = 0; i < staged.Count; i++) {
                    string target = Playtest.Child(directory, name + (staged.Count == 1 ? "" : "-part" + (i + 1).ToString("D3")) + ".zip");
                    File.Move(staged[i], target); complete.Add(target);
                }
                result.Files = complete.ToArray(); result.Unreadable = issues.Count;
                return result;
            } finally {
                if (part != null) part.Dispose();
                foreach (string temporary in staged) if (File.Exists(temporary)) File.Delete(temporary);
            }
        }
    }
}
