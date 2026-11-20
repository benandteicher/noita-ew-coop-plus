using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using CoopPlus;

// Disposable text/metadata fixtures. No Steam, game, proxy or picker is launched.
class DetectionTests {
    static string Base;
    static int passed;
    static void Assert(bool value, string message) { if (!value) throw new Exception(message); }
    static void Run(string name, Action test) { test(); passed++; Console.WriteLine("PASS " + name); }
    static void Write(string path, string text) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllText(path, text, new UTF8Encoding(false)); }
    static string Quote(string value) { return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\""; }
    static string Game(string name) { string directory = Path.GetFullPath(Path.Combine(Base, name)); Write(Path.Combine(directory, "noita.exe"), "not an executable; discovery fixture"); return directory; }
    static string Launcher(string name) { string path = Path.Combine(Base, name, "launcher"); Directory.CreateDirectory(path); return path; }
    static string Manifest(string library, string directory, string appid) {
        string game = Path.Combine(library, "steamapps", "common", directory);
        Write(Path.Combine(game, "noita.exe"), "fixture only");
        Write(Path.Combine(library, "steamapps", "appmanifest_881100.acf"), "\"AppState\" { \"appid\" " + Quote(appid) + " \"installdir\" " + Quote(directory) + " }");
        return game;
    }
    static List<InstallationCandidate> Find(string launcher, string settings, params string[] roots) { return NoitaDetection.Discover(launcher, settings, roots); }
    static void Main() {
        Base = Path.Combine(Path.GetTempPath(), "CoopPlus detection Ω " + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(Base);
        try {
            Run("actual saved RON Some path with spaces Unicode and Windows escapes", () => {
                string launcher = Launcher("remembered"), game = Game("remembered/Noita 漢 & spaces");
                string settings = Path.Combine(Base, "remembered", "proxy.ron");
                Write(settings, "(color:(name:\"test\"),app:(),audio:(),paths:(noita_exe:Some(" + Quote(Path.Combine(game,"noita.exe")) + "),noita_save:None,steam_install:None))");
                string before = File.ReadAllText(settings);
                var found = Find(launcher, settings);
                Assert(found.Count == 1 && found[0].GameDirectory == game && found[0].Source.Contains("proxy"), "remembered path not discovered");
                Assert(File.ReadAllText(settings) == before, "settings rewritten");
            });
            Run("RON comments raw strings and exact root paths field", () => {
                string launcher = Launcher("ron comments"), game = Game("ron comments/Noita"), wrong = Game("ron comments/decoy");
                string settings = Path.Combine(Base, "ron comments", "proxy.ron");
                Write(settings, "(/* paths:(noita_exe:Some(\"decoy\")) */app:(paths:(noita_exe:Some(" + Quote(Path.Combine(wrong,"noita.exe")) + "))), paths:(other:None/*nested /* comment */ okay*/, noita_exe:Some(r#\"" + Path.Combine(game,"noita.exe") + "\"#),),)");
                var found = Find(launcher, settings);
                Assert(found.Count == 1 && found[0].GameDirectory == game, "comment or nested decoy selected");
            });
            Run("RON Unicode escape and invalid/ambiguous remembered state", () => {
                string launcher = Launcher("ron invalid"), game = Game("ron invalid/Noita Ω");
                string settings = Path.Combine(Base, "ron invalid", "proxy.ron");
                Write(settings, "(paths:(noita_exe:Some(" + Quote(Path.Combine(game,"noita.exe")).Replace("Ω", "\\u{3a9}") + ")))");
                Assert(Find(launcher,settings).Single().GameDirectory == game, "Unicode scalar escape failed");
                foreach (string text in new [] {
                    "(app:(paths:(noita_exe:Some(" + Quote(Path.Combine(game,"noita.exe")) + "))),paths:(noita_exe:None))",
                    "(paths:(noita_exe:Some(" + Quote(Path.Combine(game,"noita.exe")) + "),noita_exe:None))",
                    "(paths:(noita_exe:Some(\"relative/noita.exe\")))",
                    "(paths:(noita_exe:Some(\"C:\\bad\\q\\noita.exe\")))",
                    "(paths:(noita_exe:Some(\"unterminated)))" }) {
                    Write(settings,text);
                    Assert(Find(launcher,settings).Count == 0, "invalid/ambiguous remembered value accepted");
                }
            });
            Run("default Steam library uses Noita appmanifest881100 identity", () => {
                string launcher = Launcher("steam default"), steam = Path.Combine(Base,"steam default","Steam");
                string game = Manifest(steam,"Noita","881100");
                var found = Find(launcher,null,steam);
                Assert(found.Count == 1 && found[0].GameDirectory == game, "default library absent");
                Write(Path.Combine(steam,"steamapps","appmanifest_881100.acf"),"\"AppState\" {\"appid\" \"999999\" \"installdir\" \"Noita\"}");
                Assert(Find(launcher,null,steam).Count == 0,"wrong app identity accepted");
            });
            Run("modern secondary library and config/libraryfolders fallback", () => {
                string launcher = Launcher("secondary"), steam = Path.Combine(Base,"secondary","Steam"), library = Path.Combine(Base,"secondary","Other library 漢");
                string game = Manifest(library,"Noita with spaces","881100");
                Write(Path.Combine(steam,"config","libraryfolders.vdf"),"// Valve KeyValues\n\"libraryfolders\" { \"0\" { \"path\" " + Quote(steam) + " } \"1\" { \"path\" " + Quote(library) + " \"apps\" { \"881100\" \"1\" } } }");
                var found = Find(launcher,null,steam);
                Assert(found.Count == 1 && found[0].GameDirectory == game,"secondary library not found");
            });
            Run("C drive Steam root discovers D drive secondary library", () => {
                string launcher = Launcher("cross drive"), steam = Path.Combine(Base,"cross drive","Steam");
                string library = Path.Combine(@"D:\", "CoopPlus-detection-secondary-" + Guid.NewGuid().ToString("N"));
                string full = Path.GetFullPath(library);
                Assert(Path.GetDirectoryName(full) == @"D:\" && Path.GetFileName(full).StartsWith("CoopPlus-detection-secondary-"),"unsafe disposable library root");
                try {
                    string game = Manifest(library,"Noita 漢","881100");
                    Write(Path.Combine(steam,"steamapps","libraryfolders.vdf"),"\"libraryfolders\" { \"1\" { \"path\" " + Quote(library) + " \"apps\" {\"881100\" \"1\"} } }");
                    Assert(Find(launcher,null,steam).Single().GameDirectory == game,"other-drive library not discovered");
                } finally {
                    if (Directory.Exists(full)) { Engine.AssertNoReparse(full); Directory.Delete(full,true); }
                }
            });
            Run("legacy libraryfolders and case-insensitive dedup", () => {
                string launcher = Launcher("legacy"), steam = Path.Combine(Base,"legacy","Steam"), library = Path.Combine(Base,"legacy","Library");
                string game = Manifest(library,"Noita","881100");
                Write(Path.Combine(steam,"steamapps","libraryfolders.vdf"),"\"LibraryFolders\" { \"TimeNextStatsReport\" \"0\" \"1\" " + Quote(library) + " \"2\" " + Quote(library.ToUpperInvariant()) + " }");
                Assert(Find(launcher,null,steam,steam.ToUpperInvariant()).Single().GameDirectory == game,"legacy or dedup failed");
            });
            Run("multiple suitable installs returned with remembered first", () => {
                string launcher = Launcher("multiple"), steam = Path.Combine(Base,"multiple","Steam"), other = Path.Combine(Base,"multiple","Library");
                string first = Manifest(steam,"Noita","881100"), second = Manifest(other,"Noita","881100");
                string settings = Path.Combine(Base,"multiple","proxy.ron");
                Write(settings,"(paths:(noita_exe:Some(" + Quote(Path.Combine(second,"noita.exe")) + ")))");
                Write(Path.Combine(steam,"steamapps","libraryfolders.vdf"),"\"libraryfolders\" { \"0\" {\"path\" " + Quote(steam) + "} \"1\" {\"path\" " + Quote(other) + "} }");
                var found = Find(launcher,settings,steam);
                Assert(found.Count == 2 && found[0].GameDirectory == second && found[1].GameDirectory == first,"wrong candidate ordering/set");
            });
            Run("stale missing and malformed sources fall back without writes", () => {
                string launcher = Launcher("stale"), steam = Path.Combine(Base,"stale","Steam"), settings = Path.Combine(Base,"stale","proxy.ron");
                string game = Manifest(steam,"Noita","881100");
                Write(settings,"(paths:(noita_exe:Some(" + Quote(Path.Combine(Base,"missing","noita.exe")) + ")))");
                Write(Path.Combine(steam,"steamapps","libraryfolders.vdf"),"\"libraryfolders\" { \"1\" { \"path\" \"unclosed");
                Assert(Find(launcher,settings,steam).Single().GameDirectory == game,"stale settings blocked default library");
                Assert(Find(launcher,Path.Combine(Base,"missing.ron"),Path.Combine(Base,"missing Steam")).Count == 0,"missing install guessed");
            });
            Run("Steam install metadata rejects traversal absolute and missing executable", () => {
                string launcher = Launcher("unsafe"), steam = Path.Combine(Base,"unsafe","Steam");
                foreach (string directory in new [] {"..", @"..\..\outside", @"C:\outside", "/absolute", "Noita/elsewhere", "Noita\\elsewhere", "Noita."}) {
                    Write(Path.Combine(steam,"steamapps","appmanifest_881100.acf"),"\"AppState\" {\"appid\" \"881100\" \"installdir\" " + Quote(directory) + "}");
                    Assert(Find(launcher,null,steam).Count == 0,"unsafe manifest accepted: " + directory);
                }
                Manifest(steam,"Noita","881100");
                File.Delete(Path.Combine(steam,"steamapps","common","Noita","noita.exe"));
                Assert(Find(launcher,null,steam).Count == 0,"missing executable accepted");
            });
            Run("validation retains source and overlap protections", () => {
                string launcher = Launcher("protected"), game = Game("protected/Game");
                Assert(NoitaDetection.Validate(launcher,game),"valid fixture rejected");
                Directory.CreateDirectory(Path.Combine(game,".git"));
                Assert(!NoitaDetection.Validate(launcher,game),"source checkout accepted");
                string inside = Path.Combine(launcher,"game");
                Write(Path.Combine(inside,"noita.exe"),"fixture");
                Assert(!NoitaDetection.Validate(launcher,inside),"overlap accepted");
                Assert(!NoitaDetection.Validate(launcher,"relative"),"relative install accepted");
            });
            Run("oversized malformed metadata ignored without broad scan", () => {
                string launcher = Launcher("bounds"), settings = Path.Combine(Base,"bounds","proxy.ron");
                Write(settings,new string('x',2*1024*1024+1));
                Assert(Find(launcher,settings).Count == 0,"oversized metadata accepted");
                Write(settings,"(paths:(noita_exe:Some(\"" + new string('(',100) + "\")))");
                Assert(Find(launcher,settings).Count == 0,"malformed path accepted");
            });
            Console.WriteLine("RESULT: " + passed + " focused discovery cases passed. Read-only fixtures; no Steam/game/proxy or live state changes.");
        } finally {
            if (Directory.Exists(Base)) Directory.Delete(Base,true);
        }
    }
}
