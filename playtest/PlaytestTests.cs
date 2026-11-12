using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using CoopPlusPlaytest;

public static class PlaytestTests {
    static int passed;
    static string ownedSource;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 };
    static void Check(bool value, string label) { if (!value) throw new Exception(label); Console.WriteLine("PASS " + label); passed++; }
    static void Refuses(Action action, string label) { try { action(); } catch (Exception e) { Console.WriteLine("PASS " + label + " (" + e.GetType().Name + ")"); passed++; return; } throw new Exception("Did not refuse: " + label); }
    static void Cancelled(Action action,string label){try{action();}catch(OperationCanceledException){Check(true,label);return;}throw new Exception("Did not cancel: "+label);}
    static void Put(string path, byte[] contents) { Directory.CreateDirectory(Path.GetDirectoryName(path)); File.WriteAllBytes(path, contents); }
    static void Text(string path, string contents) { Put(path, Encoding.UTF8.GetBytes(contents)); }
    static void Junction(string alias, string target) {
        Directory.CreateDirectory(Path.GetDirectoryName(alias)); Directory.CreateDirectory(target);
        string script = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path '" + alias.Replace("'", "''") + "' -Target '" + target.Replace("'", "''") + "' | Out-Null";
        using (var process = Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -NonInteractive -Command " + Playtest.Quote(script)) { UseShellExecute = false, CreateNoWindow = true })) {
            process.WaitForExit(); if (process.ExitCode != 0) throw new Exception("Could not create this new focused junction fixture.");
        }
    }
    static string Package(string scratch, string name) {
        string p = Path.Combine(scratch, name); Directory.CreateDirectory(p);
        foreach (string relative in new[] { "bin/noita_proxy.exe", "bin/steam_api64.dll", "mod/quant.ew/ewext.dll", "mod/quant.ew/init.lua" }) Text(Path.Combine(p, "payload", relative.Replace('/', '\\')), "frozen " + relative);
        var root = Path.Combine(p, "payload");
        var entries = Directory.GetFiles(root, "*", SearchOption.AllDirectories).Select(f => new Entry { path = f.Substring(root.Length + 1).Replace('\\', '/'), size = new FileInfo(f).Length, sha256 = Playtest.Hash(f) }).ToArray();
        var owned = new[] { "noita.exe", "data/data.wak", "lua51.dll", "SDL2.dll", "steam_api.dll" }.Select(relative => { string f = Path.Combine(ownedSource, relative.Replace('/', '\\')); return new Entry { path = relative, size = new FileInfo(f).Length, sha256 = Playtest.Hash(f) }; }).ToArray();
        Text(Path.Combine(root, "manifest.json"), Json.Serialize(new Manifest { schema = 1, runtimeId = Playtest.RuntimeId, label = "Co-op+ PRIVATE Playtest build7", files = entries, ownedGame = owned }));
        return p;
    }
    [STAThread] public static int Main(string[] args) {
        try {
            if (args.Length != 1 || Directory.Exists(args[0])) throw new Exception("Pass a new absolute safe local scratch folder outside source checkouts.");
            string scratch = Playtest.Canon(args[0]); Directory.CreateDirectory(scratch);
            string source = Path.Combine(scratch, "Owned Noita Ω"); Directory.CreateDirectory(source);
            ownedSource = source;
            var pe = new byte[1024 * 1024 + 17]; pe[0] = (byte)'M'; pe[1] = (byte)'Z';
            Put(Path.Combine(source, "noita.exe"), pe); Put(Path.Combine(source, "data", "data.wak"), new byte[1024 * 1024 + 19]);
            foreach (string file in new[] { "config.xml", "lua51.dll", "SDL2.dll", "steam_api.dll" }) Text(Path.Combine(source, file), "owned fixture");
            Text(Path.Combine(source, "steam_appid.txt"), "480");
            Text(Path.Combine(source, "mods", "unrelated", "private.txt"), "not copied"); Text(Path.Combine(source, "save00", "player.xml"), "original active run");
            Text(Path.Combine(source, "save_stats", "persistent.xml"), "original stats");
            Text(Path.Combine(source, "proxy.ron"), "original proxy settings and saved lobby");
            Text(Path.Combine(source, "proxy-settings.ron"), "original proxy settings");
            Text(Path.Combine(source, "proxy-save", "run_info_v2.bit"), "original proxy world state");
            string p = Package(scratch, "Test Unicode Ω spaces");
            string normalBefore = Playtest.Hash(Path.Combine(source, "save00", "player.xml"));
            string root = Playtest.Prepare(p, source);
            Check(File.Exists(Path.Combine(root, "ready.json")), "complete private copy commits ready marker");
            Check(!Directory.Exists(Path.Combine(root, "game", "mods", "unrelated")) && !File.Exists(Path.Combine(root, "game", "save00", "player.xml")), "normal mods and active run omitted");
            Check(!Directory.Exists(Path.Combine(root, "game", "save_stats")) && !Directory.Exists(Path.Combine(root, "game", "proxy-save")) && !File.Exists(Path.Combine(root, "game", "proxy.ron")) && !File.Exists(Path.Combine(root, "game", "proxy-settings.ron")), "portable stats proxy settings lobby and state omitted");
            Check(File.ReadAllText(Path.Combine(root, "game", "steam_appid.txt")) == "881100" && File.ReadAllText(Path.Combine(source, "steam_appid.txt")) == "480", "Steam application ID generated only in private copy");
            Check(Playtest.Hash(Path.Combine(source, "save00", "player.xml")) == normalBefore, "normal active-run fixture unchanged");
            string cancelledBefore = Package(scratch,"Cancelled before private creation");
            try{Playtest.CancellationRequested=delegate{return true;};Cancelled(()=>Playtest.Prepare(cancelledBefore,source),"cancel before private creation is cooperative");}
            finally{Playtest.CancellationRequested=delegate{return false;};}
            Check(!Directory.Exists(Path.Combine(cancelledBefore,"private")),"cancel before private creation leaves no incomplete copy");
            string reopened=Playtest.Prepare(cancelledBefore,source);Check(File.Exists(Path.Combine(reopened,"ready.json")),"cancelled verification can reopen normally");
            string readyBefore=File.ReadAllText(Path.Combine(reopened,"ready.json"));
            try{Playtest.CancellationRequested=delegate{return true;};Cancelled(()=>Playtest.Prepare(cancelledBefore,null),"existing private verification can be cancelled without a launch");}
            finally{Playtest.CancellationRequested=delegate{return false;};}
            Check(readyBefore==File.ReadAllText(Path.Combine(reopened,"ready.json")),"cancel retains existing readiness and progress");
            string cancelledCopy=Package(scratch,"Cancelled partial copy");bool requestCancel=false;
            try{Playtest.CancellationRequested=()=>requestCancel;Playtest.Fault=point=>{if(point=="copied")requestCancel=true;};Cancelled(()=>Playtest.Prepare(cancelledCopy,source),"mid-copy cancellation stops at the next file boundary");}
            finally{Playtest.CancellationRequested=delegate{return false;};Playtest.Fault=null;}
            Check(Directory.Exists(Path.Combine(cancelledCopy,"private"))&&!File.Exists(Path.Combine(cancelledCopy,"private","ready.json")),"cancelled partial copy is retained incomplete");
            Refuses(()=>Playtest.Prepare(cancelledCopy,source),"reopening cancelled incomplete copy still refuses without reset");
            Check(Playtest.Prepare(p, null) == root, "second open retains settings and private copy");
            var start = Playtest.StartInfo(root);
            string inheritedNormal = Environment.GetEnvironmentVariable("COOPPLUS_NORMAL_ROOT");
            try { Environment.SetEnvironmentVariable("COOPPLUS_NORMAL_ROOT", Path.Combine(scratch, "untrusted normal context")); start = Playtest.StartInfo(root); }
            finally { Environment.SetEnvironmentVariable("COOPPLUS_NORMAL_ROOT", inheritedNormal); }
            Check(start.EnvironmentVariables["COOPPLUS_NORMAL_ROOT"] == null, "inherited normal context removed from private child");
            Check(start.FileName == Path.Combine(root, "runtime", "bin", "noita_proxy.exe") && start.Arguments.Contains("--settings-path") && start.Arguments.Contains("--save-state-path") && start.Arguments.Contains("--exe-path"), "actual proxy paths forced privately");
            Check(start.EnvironmentVariables["COOPPLUS_PRIVATE_ROOT"] == root && start.EnvironmentVariables["TEMP"] == Path.Combine(root, "temp") && start.EnvironmentVariables["NP_DISABLE_STEAM"] == null, "private environment preserves Steam-capable mode");
            Text(Path.Combine(root, "game", "save00", "player.xml"), "<Entity name=\"test Continue\" />");
            string saved = Playtest.Hash(Path.Combine(root, "game", "save00", "player.xml")); Playtest.Prepare(p, null);
            Check(saved == Playtest.Hash(Path.Combine(root, "game", "save00", "player.xml")), "test Continue retained byte-for-byte");
            Text(Path.Combine(root, "game", "save00", "player.xml"), "broken XML"); Refuses(() => Playtest.Prepare(p, null), "corrupt test save refused without reset");
            string missing = Package(scratch, "Missing settings"); string missingRoot = Playtest.Prepare(missing, source); File.Delete(Path.Combine(missingRoot, "proxy", "proxy.ron")); Refuses(() => Playtest.Prepare(missing, null), "missing test proxy settings refused");
            string compatible = Package(scratch, "Private compatibility junction"); string compatibleRoot = Playtest.Prepare(compatible, source);
            string cache = Path.Combine(compatibleRoot, "profile", "AppData", "Local", "Microsoft", "Windows", "INetCache");
            string privateIE = Path.Combine(cache, "IE"); Text(Path.Combine(privateIE, "cache-fixture.txt"), "private cache retained"); Junction(Path.Combine(cache, "Content.IE5"), privateIE);
            Check(Playtest.Prepare(compatible, null) == compatibleRoot && File.ReadAllText(Path.Combine(privateIE, "cache-fixture.txt")) == "private cache retained", "exact private Windows compatibility junction accepted without traversing alias");
            Junction(Path.Combine(cache, "OtherAlias"), privateIE); Refuses(() => Playtest.Prepare(compatible, null), "other private reparse leaf still refused");
            string redirected = Package(scratch, "Redirected compatibility junction"); string redirectedRoot = Playtest.Prepare(redirected, source);
            string redirectedCache = Path.Combine(redirectedRoot, "profile", "AppData", "Local", "Microsoft", "Windows", "INetCache"); Directory.CreateDirectory(Path.Combine(redirectedCache, "IE"));
            string unrelatedIE = Path.Combine(scratch, "Unrelated cache fixture"); Text(Path.Combine(unrelatedIE, "sentinel.txt"), "unrelated retained"); Junction(Path.Combine(redirectedCache, "Content.IE5"), unrelatedIE);
            Refuses(() => Playtest.Prepare(redirected, null), "compatibility junction redirected outside private IE refused"); Check(File.ReadAllText(Path.Combine(unrelatedIE, "sentinel.txt")) == "unrelated retained", "external junction target unchanged");
            string interrupted = Package(scratch, "Interrupted"); Playtest.Fault = point => { throw new IOException("injected copy interruption"); }; Refuses(() => Playtest.Prepare(interrupted, source), "copy interruption leaves no success marker"); Playtest.Fault = null; Refuses(() => Playtest.Prepare(interrupted, source), "partial copy refused on reopening");
            string corrupt = Package(scratch, "Corrupt payload"); Text(Path.Combine(corrupt, "payload", "bin", "steam_api64.dll"), "altered"); Refuses(() => Playtest.ReadPayload(corrupt), "corrupt payload refused");
            string extra = Package(scratch, "Extra payload"); Text(Path.Combine(extra, "payload", "unlisted.txt"), "x"); Refuses(() => Playtest.ReadPayload(extra), "unlisted payload refused");
            string changing = Package(scratch, "Changing source"); bool changed = false; Playtest.Fault = point => { if (!changed) { changed = true; Text(Path.Combine(source, "config.xml"), "changed shipped config during Steam update fixture"); } }; Refuses(() => Playtest.Prepare(changing, source), "source change during clone refused"); Playtest.Fault = null;
            Refuses(() => Playtest.Canon("C:relative"), "drive-relative paths refused"); Refuses(() => Playtest.Canon(@"\\server\share\test"), "UNC paths refused"); Refuses(() => Playtest.Canon(@"C:\safe\file:stream"), "alternate streams refused"); Refuses(() => Playtest.Canon(@"C:\safe\CON.txt"), "device names refused");
            Refuses(() => Playtest.ValidateSource(source, Path.Combine(source, "nested")), "source destination containment refused");
            File.Delete(Path.Combine(source, "steam_appid.txt")); Playtest.ValidateSource(source, p);
            Check(true, "stock owned game without Steam app ID file accepted");
            Console.WriteLine("RESULT " + passed + " focused checks passed; retained scratch " + scratch); return 0;
        } catch (Exception e) { Console.Error.WriteLine(e); return 1; }
    }
}
