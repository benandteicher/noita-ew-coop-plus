// Read-only, bounded discovery for the official proxy's saved RON and Steam metadata.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Microsoft.Win32;

namespace CoopPlus {
    public sealed class InstallationCandidate {
        public string GameDirectory;
        public string Source;
    }

    public static class NoitaDetection {
        public const string SteamAppId = "881100";
        const int MaxMetadataBytes = 2 * 1024 * 1024;

        public static bool Validate(string launcherRoot, string gameDir) {
            string reason;
            return Validate(launcherRoot, gameDir, out reason);
        }
        public static bool Validate(string launcherRoot, string gameDir, out string reason) {
            try {
                if (String.IsNullOrWhiteSpace(gameDir) || !Path.IsPathRooted(gameDir)) throw new IOException("Choose an absolute Noita installation folder.");
                string full = Canon(gameDir);
                string exe = Path.Combine(full, "noita.exe");
                if (!File.Exists(exe)) throw new IOException("This folder does not contain noita.exe.");
                Engine.AssertNoReparse(exe);
                Engine.ValidateInstallPaths(launcherRoot, full);
                reason = null;
                return true;
            } catch (Exception e) {
                if (!ExpectedMetadataFailure(e)) throw;
                reason = e.Message;
                return false;
            }
        }

        public static List<InstallationCandidate> Discover(string launcherRoot, string settingsPath, IEnumerable<string> steamRoots) {
            var found = new List<InstallationCandidate>();
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Action<string,string> add = (directory, source) => {
                if (!Validate(launcherRoot, directory)) return;
                string full = Canon(directory);
                if (seen.Add(full)) found.Add(new InstallationCandidate { GameDirectory = full, Source = source });
            };
            string remembered = RememberedDirectory(settingsPath);
            if (remembered != null) add(remembered, "Remembered by proxy settings");

            var libraries = new List<string>();
            var librarySet = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            Action<string> addLibrary = path => {
                try {
                    if (libraries.Count >= 128 || String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path)) return;
                    string full = Canon(path);
                    Engine.AssertNoReparse(full);
                    if (Directory.Exists(full) && librarySet.Add(full)) libraries.Add(full);
                } catch (Exception e) { if (!ExpectedMetadataFailure(e)) throw; }
            };
            foreach (string root in (steamRoots ?? new string[0]).Take(32)) {
                addLibrary(root);
                foreach (string relative in new [] { "steamapps/libraryfolders.vdf", "config/libraryfolders.vdf" }) {
                    try {
                        if (String.IsNullOrWhiteSpace(root) || !Path.IsPathRooted(root)) continue;
                        var document = Vdf.Parse(ReadBounded(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar))));
                        object foldersValue;
                        if (!document.TryGetValue("libraryfolders", out foldersValue)) continue;
                        var folders = foldersValue as Dictionary<string,object>;
                        if (folders == null) continue;
                        foreach (var entry in folders) {
                            int number;
                            if (!Int32.TryParse(entry.Key, out number) || number < 0) continue;
                            string path = entry.Value as string;
                            var modern = entry.Value as Dictionary<string,object>;
                            object pathValue;
                            if (modern != null && modern.TryGetValue("path", out pathValue)) path = pathValue as string;
                            addLibrary(path);
                        }
                    } catch (Exception e) { if (!ExpectedMetadataFailure(e)) throw; }
                }
            }
            foreach (string library in libraries) {
                try {
                    var document = Vdf.Parse(ReadBounded(Path.Combine(library, "steamapps", "appmanifest_" + SteamAppId + ".acf")));
                    object stateValue, appValue, directoryValue;
                    if (!document.TryGetValue("AppState", out stateValue)) continue;
                    var state = stateValue as Dictionary<string,object>;
                    if (state == null || !state.TryGetValue("appid", out appValue) || (appValue as string) != SteamAppId || !state.TryGetValue("installdir", out directoryValue)) continue;
                    string directory = directoryValue as string;
                    if (!SafeInstallName(directory)) continue;
                    add(Path.Combine(library, "steamapps", "common", directory), "Steam library");
                } catch (Exception e) { if (!ExpectedMetadataFailure(e)) throw; }
            }
            return found;
        }

        public static List<string> GetSteamRoots() {
            var roots = new List<string>();
            Action<string> add = value => {
                try {
                    if (String.IsNullOrWhiteSpace(value) || !Path.IsPathRooted(value)) return;
                    string path = Canon(value);
                    if (!roots.Contains(path, StringComparer.OrdinalIgnoreCase)) roots.Add(path);
                } catch (Exception e) { if (!ExpectedMetadataFailure(e)) throw; }
            };
            foreach (RegistryHive hive in new [] { RegistryHive.CurrentUser, RegistryHive.LocalMachine }) {
                foreach (RegistryView view in new [] { RegistryView.Registry64, RegistryView.Registry32 }) {
                    try {
                        using (var registry = RegistryKey.OpenBaseKey(hive, view))
                        using (var key = registry.OpenSubKey(@"Software\Valve\Steam", false)) {
                            if (key == null) continue;
                            add(key.GetValue("SteamPath") as string);
                            add(key.GetValue("InstallPath") as string);
                            string exe = key.GetValue("SteamExe") as string;
                            if (!String.IsNullOrWhiteSpace(exe) && Path.IsPathRooted(exe)) add(Path.GetDirectoryName(exe));
                        }
                    } catch (Exception e) { if (!ExpectedMetadataFailure(e)) throw; }
                }
            }
            foreach (Environment.SpecialFolder folder in new [] { Environment.SpecialFolder.ProgramFilesX86, Environment.SpecialFolder.ProgramFiles }) {
                string programFiles = Environment.GetFolderPath(folder);
                if (!String.IsNullOrEmpty(programFiles)) add(Path.Combine(programFiles, "Steam"));
            }
            return roots;
        }

        static bool ExpectedMetadataFailure(Exception e) {
            return e is IOException || e is UnauthorizedAccessException || e is ArgumentException || e is NotSupportedException || e is FormatException || e is System.Security.SecurityException;
        }
        static string Canon(string path) {
            string full = Path.GetFullPath(path);
            return full.Length > Path.GetPathRoot(full).Length ? full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) : full;
        }
        static bool SafeInstallName(string name) {
            return !String.IsNullOrWhiteSpace(name) && name != "." && name != ".." && name.Length <= 150 &&
                !Path.IsPathRooted(name) && name.IndexOfAny(Path.GetInvalidFileNameChars()) < 0 &&
                name.IndexOf('/') < 0 && name.IndexOf('\\') < 0 && !name.EndsWith(".") && !name.EndsWith(" ");
        }
        static string ReadBounded(string path) {
            Engine.AssertNoReparse(path);
            using (var input = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite)) {
                if (input.Length > MaxMetadataBytes) throw new FormatException("Discovery metadata is too large.");
                byte[] bytes = new byte[MaxMetadataBytes + 1];
                int count = 0, n;
                while (count < bytes.Length && (n = input.Read(bytes, count, bytes.Length - count)) > 0) count += n;
                if (count > MaxMetadataBytes) throw new FormatException("Discovery metadata is too large.");
                return new UTF8Encoding(false, true).GetString(bytes, 0, count).TrimStart('\uFEFF');
            }
        }

        static string RememberedDirectory(string settingsPath) {
            try {
                if (String.IsNullOrWhiteSpace(settingsPath) || !Path.IsPathRooted(settingsPath)) return null;
                var tokens = Lexer.Read(ReadBounded(settingsPath), true);
                var pairs = MatchContainers(tokens);
                if (tokens.Count == 0 || tokens[0].Text != "(" || pairs[0] != tokens.Count - 1) return null;
                int[] paths = Field(tokens, pairs, 0, "paths");
                if (paths == null || tokens[paths[0]].Text != "(" || pairs[paths[0]] != paths[1] - 1) return null;
                int[] exe = Field(tokens, pairs, paths[0], "noita_exe");
                if (exe == null || exe[1] - exe[0] != 4 || tokens[exe[0]].Text != "Some" || tokens[exe[0]+1].Text != "(" || !tokens[exe[0]+2].Literal || tokens[exe[0]+3].Text != ")") return null;
                string file = tokens[exe[0]+2].Text;
                if (!Path.IsPathRooted(file) || !String.Equals(Path.GetFileName(file), "noita.exe", StringComparison.OrdinalIgnoreCase)) return null;
                return Path.GetDirectoryName(file);
            } catch (Exception e) { if (!ExpectedMetadataFailure(e)) throw; return null; }
        }
        static Dictionary<int,int> MatchContainers(List<Token> tokens) {
            var result = new Dictionary<int,int>();
            var stack = new Stack<int>();
            for (int i = 0; i < tokens.Count; i++) {
                if (tokens[i].Literal) continue;
                string text = tokens[i].Text;
                if (text == "(" || text == "[" || text == "{") { if (stack.Count >= 64) throw new FormatException("Metadata nesting is too deep."); stack.Push(i); }
                if (text == ")" || text == "]" || text == "}") {
                    if (stack.Count == 0) throw new FormatException("Unmatched metadata container.");
                    int start = stack.Pop();
                    if ((tokens[start].Text == "(" && text != ")") || (tokens[start].Text == "[" && text != "]") || (tokens[start].Text == "{" && text != "}")) throw new FormatException("Mismatched metadata container.");
                    result.Add(start, i);
                }
            }
            if (stack.Count != 0) throw new FormatException("Unclosed metadata container.");
            return result;
        }
        static int[] Field(List<Token> tokens, Dictionary<int,int> pairs, int open, string wanted) {
            int close = pairs[open];
            int[] found = null;
            for (int i = open + 1; i < close;) {
                if (tokens[i].Text == "," && !tokens[i].Literal) { i++; continue; }
                if (tokens[i].Literal || i + 2 >= close || tokens[i+1].Text != ":") throw new FormatException("Invalid saved RON field.");
                string name = tokens[i].Text;
                int value = i + 2;
                i = value;
                while (i < close && (tokens[i].Literal || tokens[i].Text != ",")) {
                    int end;
                    if (!tokens[i].Literal && pairs.TryGetValue(i, out end)) i = end + 1;
                    else i++;
                }
                if (name == wanted) {
                    if (found != null || value == i) throw new FormatException("Ambiguous saved RON field.");
                    found = new [] { value, i };
                }
            }
            return found;
        }

        sealed class Token { public string Text; public bool Literal; }
        sealed class Lexer {
            readonly string text;
            readonly bool ron;
            int at;
            Lexer(string text, bool ron) { this.text = text; this.ron = ron; }
            public static List<Token> Read(string text, bool ron) {
                var reader = new Lexer(text, ron);
                var tokens = new List<Token>();
                while (reader.More()) {
                    if (tokens.Count >= 100000) throw new FormatException("Too many metadata tokens.");
                    tokens.Add(reader.Next());
                }
                return tokens;
            }
            bool More() {
                while (at < text.Length) {
                    if (Char.IsWhiteSpace(text[at])) { at++; continue; }
                    if (at + 1 < text.Length && text[at] == '/' && text[at+1] == '/') { at += 2; while (at < text.Length && text[at] != '\n') at++; continue; }
                    if (at + 1 < text.Length && text[at] == '/' && text[at+1] == '*') {
                        int depth = 1; at += 2;
                        while (at < text.Length && depth > 0) {
                            if (at+1 < text.Length && text[at] == '/' && text[at+1] == '*') { if (++depth > 64) throw new FormatException("Comments too deeply nested."); at += 2; }
                            else if (at+1 < text.Length && text[at] == '*' && text[at+1] == '/') { depth--; at += 2; }
                            else at++;
                        }
                        if (depth != 0) throw new FormatException("Unclosed metadata comment.");
                        continue;
                    }
                    return true;
                }
                return false;
            }
            Token Next() {
                if (text[at] == '"') return new Token { Text = Quoted(), Literal = true };
                int raw = at;
                if (ron && text[at] == 'r') {
                    int cursor = at + 1; while (cursor < text.Length && text[cursor] == '#') cursor++;
                    if (cursor < text.Length && text[cursor] == '"') {
                        string ending = "\"" + new string('#', cursor - at - 1);
                        int end = text.IndexOf(ending, cursor + 1, StringComparison.Ordinal);
                        if (end < 0) throw new FormatException("Unclosed raw RON string.");
                        at = end + ending.Length;
                        return new Token { Text = text.Substring(cursor + 1, end - cursor - 1), Literal = true };
                    }
                }
                if ("()[]{}:,".IndexOf(text[at]) >= 0) return new Token { Text = text[at++].ToString() };
                while (at < text.Length && !Char.IsWhiteSpace(text[at]) && "()[]{}:,\"".IndexOf(text[at]) < 0) {
                    if (at + 1 < text.Length && text[at] == '/' && (text[at+1] == '/' || text[at+1] == '*')) break;
                    at++;
                }
                if (at == raw) throw new FormatException("Invalid metadata token.");
                return new Token { Text = text.Substring(raw, at - raw) };
            }
            string Quoted() {
                at++;
                var value = new StringBuilder();
                while (at < text.Length) {
                    char c = text[at++];
                    if (c == '"') return value.ToString();
                    if (c != '\\') { value.Append(c); continue; }
                    if (at >= text.Length) throw new FormatException("Incomplete string escape.");
                    c = text[at++];
                    switch (c) {
                        case '\\': value.Append('\\'); break;
                        case '"': value.Append('"'); break;
                        case 'n': value.Append('\n'); break;
                        case 'r': value.Append('\r'); break;
                        case 't': value.Append('\t'); break;
                        case '0': if (!ron) throw new FormatException("Invalid VDF escape."); value.Append('\0'); break;
                        case 'u':
                            if (!ron || at >= text.Length || text[at++] != '{') throw new FormatException("Invalid Unicode escape.");
                            int begin = at; while (at < text.Length && text[at] != '}') at++;
                            int code;
                            if (at >= text.Length || at - begin < 1 || at - begin > 6 || !Int32.TryParse(text.Substring(begin, at - begin), System.Globalization.NumberStyles.HexNumber, System.Globalization.CultureInfo.InvariantCulture, out code) || code > 0x10FFFF || code >= 0xD800 && code <= 0xDFFF) throw new FormatException("Invalid Unicode scalar.");
                            at++; value.Append(Char.ConvertFromUtf32(code)); break;
                        default:
                            if (ron) throw new FormatException("Unsupported saved RON escape.");
                            value.Append('\\').Append(c); break;
                    }
                }
                throw new FormatException("Unclosed metadata string.");
            }
        }

        sealed class Vdf {
            readonly List<Token> tokens;
            int at;
            Vdf(string text) { tokens = Lexer.Read(text, false); }
            public static Dictionary<string,object> Parse(string text) {
                var parser = new Vdf(text);
                var result = parser.Object(false, 0);
                if (parser.at != parser.tokens.Count) throw new FormatException("Unexpected VDF tokens.");
                return result;
            }
            Dictionary<string,object> Object(bool nested, int depth) {
                if (depth > 32) throw new FormatException("VDF nesting too deep.");
                var result = new Dictionary<string,object>(StringComparer.OrdinalIgnoreCase);
                while (at < tokens.Count) {
                    Token key = tokens[at++];
                    if (!key.Literal && key.Text == "}") { if (!nested) throw new FormatException("Unexpected VDF closing brace."); return result; }
                    if (!key.Literal && "{}()[]:,".IndexOf(key.Text, StringComparison.Ordinal) >= 0 || at >= tokens.Count) throw new FormatException("Invalid VDF key.");
                    Token token = tokens[at++];
                    object value;
                    if (!token.Literal && token.Text == "{") value = Object(true, depth + 1);
                    else if (!token.Literal && "{}()[]:,".IndexOf(token.Text, StringComparison.Ordinal) >= 0) throw new FormatException("Invalid VDF value.");
                    else value = token.Text;
                    if (result.ContainsKey(key.Text)) throw new FormatException("Duplicate VDF key.");
                    result.Add(key.Text, value);
                }
                if (nested) throw new FormatException("Unclosed VDF object.");
                return result;
            }
        }
    }
}
