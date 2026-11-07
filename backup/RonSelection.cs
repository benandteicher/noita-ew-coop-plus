// Bounded RON reader adapted from this project updater/NoitaDetection.cs.
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
namespace CoopPlusBackup {
 public static class RonSelection {
        public static string Profile(string text) {
            var tokens=Lexer.Read(text,true); var pairs=MatchContainers(tokens);
            if(tokens.Count==0 || tokens[0].Literal || tokens[0].Text!="(" || !pairs.ContainsKey(0) || pairs[0]!=tokens.Count-1) throw new IOException("Proxy settings are not a complete RON document.");
            var paths=Field(tokens,pairs,0,"paths");
            if(paths==null || tokens[paths[0]].Literal || tokens[paths[0]].Text!="(" || !pairs.ContainsKey(paths[0]) || pairs[paths[0]]!=paths[1]-1) throw new IOException("Proxy settings have no unambiguous paths section.");
            var value=Field(tokens,pairs,paths[0],"noita_save");
            if(value==null || value[1]-value[0]!=4 || tokens[value[0]].Literal || tokens[value[0]].Text!="Some" || tokens[value[0]+1].Literal || tokens[value[0]+1].Text!="(" || !tokens[value[0]+2].Literal || tokens[value[0]+3].Literal || tokens[value[0]+3].Text!=")") throw new IOException("Proxy settings have no selected Noita profile (paths.noita_save). No default path was substituted.");
            return tokens[value[0]+2].Text;
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


 }
}
