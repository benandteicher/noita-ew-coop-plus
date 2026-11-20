// Developer-only isolation fixtures. No shipped launcher uses this binding.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;
using System.Xml;
using CoopPlus;

namespace CoopPlusPlaytest {
    internal static class StagingBundle { internal static string ManifestSha256; }
    internal static class StagingChecks {
        static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        const string Secret = "PRIVATE-SENTINEL-A7FC13";
        static int passed;

        static void Check(bool condition, string name) {
            if (!condition) throw new Exception(name);
            Console.WriteLine("PASS " + name); passed++;
        }
        static void Refuses(Action run, string name) {
            try { run(); } catch (IOException) { Check(true, name); return; }
            catch (InvalidDataException) { Check(true, name); return; }
            catch (ArgumentException) { Check(true, name); return; }
            throw new Exception("Not refused: " + name);
        }
        static string PathAt(string root, string relative) { return Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)); }
        static void Text(string root, string name, string value) {
            string file = PathAt(root, name);
            Directory.CreateDirectory(Path.GetDirectoryName(file));
            File.WriteAllText(file, value, Utf8);
        }
        static string FileSha(string root, string name) { return Engine.Sha256(PathAt(root,name)); }
        static Entry Describe(string root, string name) {
            return new Entry { path=name, size=new FileInfo(PathAt(root,name)).Length, sha256=FileSha(root,name) };
        }
        static Dictionary<string,string> Snapshot(string root) {
            return Directory.EnumerateFiles(root, "*", SearchOption.AllDirectories)
                .ToDictionary(name => name.Substring(root.Length+1), Engine.Sha256, StringComparer.Ordinal);
        }
        static bool Same(Dictionary<string,string> before, Dictionary<string,string> after) {
            return before.Count == after.Count && before.All(row => after.ContainsKey(row.Key) && after[row.Key] == row.Value);
        }
        static void Preferences(string scratch, string actualProfile) {
            string game = PathAt(scratch,"fresh-game");
            Directory.CreateDirectory(game);
            string absent = PathAt(scratch,"absent-normal/save_shared/config.xml");
            var modes = new[]{new[]{800,600},new[]{1024,768},new[]{1366,768},new[]{1920,1080}};
            Playtest.InitializeFreshPreferences(game,actualProfile,absent,1900,1000,modes);
            var output = new XmlDocument();
            output.Load(PathAt(game,"save_shared/config.xml"));
            var el = output.DocumentElement;
            var input = (XmlElement)el.SelectSingleNode("KeyboardControls");
            Check(el.GetAttribute("vsync") == "0" && input.GetAttribute("key_kick.primary") == "-1" &&
                input.GetAttribute("key_kick.primary_name") == "$$input_mouseleft", "imports actual changed Noita VSync and Kick binding");
            Check(el.GetAttribute("fullscreen")=="0" && el.HasAttribute("backbuffer_width") &&
                el.SelectSingleNode("GamepadControls") != null, "windowed safe mode and gamepad bindings retained");
            Check(!el.HasAttribute("online_features") && !el.HasAttribute("steam_cloud_size_warning_limit_mb") &&
                !el.HasAttribute("streaming_integration_channel_name") && !el.HasAttribute("record_events"),
                "Cloud, accounts, and recording fields stay outside preference import");
            Check(StagingPreferences.ReadPing(actualProfile)=="42" && StagingPreferences.ReadPing(game)=="42",
                "reads real mod preference format and reencodes only local Ping");
            Check(!File.Exists(PathAt(game,"save00/world_state.xml")) && !File.Exists(PathAt(game,"save00/player.xml")),
                "first-use preference initialization creates no run data");
            var saved = Snapshot(game);
            Refuses(() => Playtest.InitializeFreshPreferences(game,actualProfile,absent,1900,1000,modes),
                "one-time import refuses to overwrite existing preferences");
            Check(Same(saved,Snapshot(game)), "repeated setup attempt leaves existing preferences identical");
            string malformed = PathAt(scratch,"malformed-local");
            Text(malformed,"save_shared/config.xml",
                "<Config config_format_version=\"14\" window_w=\"invalid\" audio_music_volume=\"NaN\" " +
                "vsync=\"51\"><KeyboardControls key_kick.primary=\"9944\" key_kick.primary_name=\"UNSUPPORTED\"/></Config>");
            Text(malformed,"save00/mod_settings.bin","bad binary preferences");
            string fallback = PathAt(scratch,"fallback-game");
            Playtest.InitializeFreshPreferences(fallback,malformed,absent,1280,900,modes);
            output.Load(PathAt(fallback,"save_shared/config.xml"));el = output.DocumentElement;
            Check(el.GetAttribute("window_w")=="1024" && !el.HasAttribute("vsync") && !el.HasAttribute("audio_music_volume") &&
                el.SelectSingleNode("KeyboardControls")==null && StagingPreferences.ReadPing(fallback)==null,
                "malformed settings use supported display and input defaults");
        }

        static void Exports(string installation) {
            Directory.CreateDirectory(installation);
            string old, recent, failed;
            using (var audit = new SessionAudit(installation)) {
                old = audit.DirectoryPath;
                audit.Bind(new Manifest {runtimeId=Playtest.RuntimeId});
                audit.Append("stdout.log", "first session " + Secret + " C:\\Users\\Private\\save00\n");
                audit.Finish("proxy-exit-zero");
            }
            using (var audit = new SessionAudit(installation)) {
                recent = audit.DirectoryPath;
                audit.Bind(new Manifest {runtimeId=Playtest.RuntimeId});
                audit.Write("intentional-restart-accepted", new {number=1, lobby=Secret});
                audit.Finish("proxy-exit-zero");
            }
            using (var audit = new SessionAudit(installation)) {
                failed = audit.DirectoryPath;
                audit.Write("launcher-failure", new {error=Secret, hresult=-9});
                audit.Finish("failure");
            }
            Text(recent,"proxy-7-123.jsonl",
                "{\"utcUnixMs\":1795212122000,\"event\":\"lobby-decision\",\"message\":\"role=host decision=requested code=not-a-number " + Secret + "\"}\n" +
                "{\"event\":\"local-game-detached\",\"message\":\"generation=2 was_ready=true\"}\n");
            Text(recent,"proxy-7-123.terrain.txt",
                "{\"event\":\"first-terrain-fault\",\"message\":\"reason=constructor_declined chunk_x=-4 chunk_y=56 index=16202 material=28 flags=1 " + Secret + "\"}\n");
            Text(recent,"restart-7-123.json.consumed.json","{\"arguments\":[\"" + Secret + "\"]}\n");
            Text(recent,"game-2-logger.txt","Noita shutdown " + Secret + "\n");
            Text(failed,"failure.txt","launcher-failure\n" + Secret + "\n");
            Text(installation,Playtest.PrivateName + "/game/save00/player.xml","<Save>" + Secret + "</Save>");
            Text(installation,Playtest.PrivateName + "/proxy/proxy.ron","private " + Secret);
            Text(installation,Playtest.PrivateName + "/game/logger.txt","Noita live logger\n");
            Text(old,"unapproved.xml","never include " + Secret);
            string big = PathAt(recent,"proxy-8-456.jsonl");
            const int largeCount = 225000;
            const string run = "ce37ab048524bb18";
            using (var writer = new StreamWriter(big,false,Utf8)) {
                for (int i = 1; i <= largeCount; i++)
                    writer.WriteLine("{\"utcUnixMs\":1795212122000,\"event\":\"actual-world-receipt\",\"message\":\"generation=" + i +
                                     " intent=continue kind=continue boundary=world same_stopped_world=true run_matches=true rules_match=true " + Secret + "\"}");
            }
            Text(recent,"proxy-9-789.jsonl", "{\"utcUnixMs\":1795212122000,\"event\":\"actual-world-receipt-accepted\",\"message\":\"kind=continue local_session=" +
                Secret + " run_id=" + run + " rules_id=4a58508faa6fef08 legacy_unverified=false\"}\n");
            Check(new FileInfo(big).Length>StagingLogs.ZipLimit,"export fixture exceeds the ZIP limit");
            var before = Snapshot(PathAt(installation,StagingLogs.DirectoryName));
            var result = StagingLogs.Export(installation);
            Check(result.Files.Length>1 && result.Files.All(f => new FileInfo(f).Length<StagingLogs.ZipLimit),
                "numbered ZIP set is strictly under 20 million bytes per file");
            long seen = 0;
            int generation = 0;
            var label = "R-" + Playtest.HashBytes(Utf8.GetBytes("Co-op+ shared run label v1:" + run)).Substring(0,12);
            var coverage = new StringBuilder();
            foreach (string path in result.Files) using (var zip = ZipFile.OpenRead(path)) {
                Check(zip.GetEntry("INDEX.txt") != null, "ZIP part has a readable time, build, role and correlation index");
                bool indexed = false;
                foreach (var entry in zip.Entries) using (var input = new StreamReader(entry.Open(),Utf8)) {
                    string row;
                    while ((row=input.ReadLine())!=null) {
                        if (row.Contains(Secret) || row.Contains(run) || row.Contains("C:\\Users") || row.Contains("restart-7") ||
                            row.Contains("privateRoot") || row.Contains("unapproved.xml")) throw new Exception("An export leaked private input.");
                        if (entry.FullName=="INDEX.txt") { indexed |= row.Contains(label); coverage.AppendLine(row); continue; }
                        seen++;
                        int at = row.IndexOf(" generation=",StringComparison.Ordinal);
                        if (!row.Contains(" actual-world-receipt ") || at<0) { coverage.AppendLine(row); continue; }
                        int end = row.IndexOf(' ',at+12);
                        int recorded = Int32.Parse(row.Substring(at+12,end-at-12),CultureInfo.InvariantCulture);
                        if (recorded != ++generation) throw new Exception("A large-source record disappeared or moved.");
                    }
                }
                Check(indexed, "each split index can identify the same accepted run even before its late receipt");
            }
            Check(generation==largeCount && seen==result.Entries,"all oversized-source records exported in order");
            string report = coverage.ToString();
            Check(report.Contains("lobby-decision role=host") && report.Contains("build") &&
                report.Contains("first-terrain-fault") && report.Contains("intentional-restart-accepted") &&
                report.Contains("launcher-failure hresult=-9"),"multiple sessions, restart, join, terrain and failure evidence retained");
            Check(Same(before, Snapshot(PathAt(installation,StagingLogs.DirectoryName))),"export preserves source log bytes");
        }

        static Manifest Payload(string payload, long build, string version, Entry[] game) {
            string[] items = {"bin/noita_proxy.exe","bin/steam_api64.dll","mod/quant.ew/ewext.dll","mod/quant.ew/init.lua"};
            foreach (string item in items) Text(payload,item,"inert file fixture for " + item + " build " + build);
            var manifest = new Manifest { schema=1, channel=Playtest.Channel, version=version, build=build,
                runtimeId=StagingRelease.Runtime(build,version), files=items.Select(x=>Describe(payload,x)).ToArray(), ownedGame=game };
            Text(payload,"manifest.json",Json.Serialize(manifest));
            return manifest;
        }
        static StagingRelease Package(string scratch, long build, string version, Entry[] game, out string archive, bool invalidInner=false) {
            string folder = PathAt(scratch,"asset-"+build);
            var manifest = Payload(PathAt(folder,Playtest.PayloadName),build,version,game);
            if (invalidInner) {
                manifest.channel = "stable";
                Text(folder, Playtest.PayloadName + "/manifest.json", Json.Serialize(manifest));
            }
            Text(folder,"Co-op+ Staging.exe","fixture types only; never executable");
            Text(folder,"BUILD-PROVENANCE.json","{}");
            Text(folder,"STAGING-PLAYER-INSTRUCTIONS.txt","fixture");
            archive = PathAt(scratch,"asset-"+build+".zip");
            using (var zip = ZipFile.Open(archive,ZipArchiveMode.Create))
                foreach (string file in Directory.EnumerateFiles(folder,"*",SearchOption.AllDirectories))
                    zip.CreateEntryFromFile(file,file.Substring(folder.Length+1).Replace('\\','/'),CompressionLevel.Optimal);
            return new StagingRelease { schema=1, channel=Playtest.Channel, launcherProtocol=StagingRelease.Protocol, tag="coopplus-staging-v"+version+"-b"+build,
                version=version, build=build, name="Noita-CoopPlus-"+version+"-build"+build+"-STAGING-win.zip",
                size=new FileInfo(archive).Length,sha256=Engine.Sha256(archive),manifestSha256=FileSha(folder,Playtest.PayloadName+"/manifest.json"),
                assetId=build*10+1,releaseId=build*10 };
        }
        static string Github(StagingRelease release, bool prerelease=true, bool immutable=true, bool badAsset=false) {
            return Json.Serialize(new { id=release.releaseId, tag_name=release.tag, draft=false, prerelease=prerelease, immutable=immutable,
                assets=new[]{new { id=release.assetId, state="uploaded", name=release.name,
                    size=release.size+(badAsset?1:0), digest="sha256:"+release.sha256, browser_download_url=release.AssetUrl }} });
        }
        static VerifiedPayload Stage(string package, StagingRelease release, string zip) {
            return StagingUpdate.Stage(package,release,(url,dest,size,hash) => {
                if(url!=release.AssetUrl||size!=release.size||hash!=release.sha256)throw new Exception("Wrong approved download binding.");
                File.Copy(zip,dest,false);
            });
        }
        static void Updates(string scratch, string stableAssembly, string stableReleaseJson) {
            string package = PathAt(scratch,"installation"), root = PathAt(package,Playtest.PrivateName), game = PathAt(root,"game");
            Text(game,"steam_appid.txt","881100");
            string[] engine = {"noita.exe","data/data.wak","lua51.dll","SDL2.dll","steam_api.dll"};
            foreach (string file in engine) Text(game,file,"local engine fixture");
            Entry[] gameFiles = engine.Select(x=>Describe(game,x)).ToArray();
            Text(game,"save_shared/config.xml","<Config vsync=\"0\" />");
            Text(game,"save00/mod_config.xml","<Mods />");
            Text(game,"save00/player.xml","<Entity retained=\"true\" />");
            Text(game,"save00/world_state.xml","<WorldState session=\"kept\" />");
            Text(game,"save00/mod_settings.bin","user changed choices");
            Text(root,"proxy/proxy.ron","existing proxy preferences");
            foreach(string file in new[]{"run_info_v2.bit","world_chunks.bit","des_entity_storage.bit","flags.bit","run_settings_v1.ron"})
                Text(root,"proxy/save_state/"+file,"existing "+file);
            Text(package,StagingLogs.DirectoryName+"/history.log","existing private diagnostic history");
            Directory.CreateDirectory(PathAt(root,"profile/AppData/Local"));
            Directory.CreateDirectory(PathAt(root,"temp"));
            string bundled = PathAt(package,Playtest.PayloadName);
            var initial = Payload(bundled,Playtest.Build,Playtest.Version,gameFiles);
            StagingBundle.ManifestSha256=FileSha(bundled,"manifest.json");
            var initialPayload=Playtest.LoadPayload(package);
            foreach (Entry entry in initial.files) {
                string destination = entry.path.StartsWith("bin/") ? "runtime/"+entry.path : "game/mods/"+entry.path.Substring(4);
                Text(root,destination,File.ReadAllText(PathAt(bundled,entry.path)));
            }
            var ready=new Ready {schema=1,source="fixture",game=gameFiles};
            Playtest.Bind(ready,initialPayload);Text(root,"ready.json",Json.Serialize(ready));
            string mutable = Json.Serialize(RuntimeMigration.Capture(root));
            var history = Snapshot(PathAt(package,StagingLogs.DirectoryName));
            string zip;var candidate=Package(scratch,25,"1.9.18.18",gameFiles,out zip);
            candidate.VerifyGitHub(Github(candidate));
            candidate.launcherProtocol = 1;
            Refuses(()=>candidate.Validate(), "Staging 23 distinguishes the earlier launcher protocol");
            candidate.launcherProtocol = StagingRelease.Protocol;
            Refuses(()=>candidate.VerifyGitHub(Github(candidate,prerelease:false)), "Staging refuses Stable GitHub release");
            Refuses(()=>candidate.VerifyGitHub(Github(candidate,immutable:false)), "Staging refuses a mutable release");
            Refuses(()=>candidate.VerifyGitHub(Github(candidate,badAsset:true)), "Staging refuses an incorrect immutable asset identity");
            var stableEngine=Assembly.LoadFile(stableAssembly).GetType("CoopPlus.Engine");
            var parse=stableEngine.GetMethod("ParseManifest",BindingFlags.Public|BindingFlags.Static);
            string stableJson=File.ReadAllText(stableReleaseJson);
            Check(parse.Invoke(null,new object[]{stableJson})!=null,"exact Stable6 engine accepts its own real metadata");
            try {
                parse.Invoke(null,new object[]{stableJson.Replace("\"stable\"","\"staging\"")});
                throw new Exception("Stable6 permitted a Staging channel.");
            } catch (TargetInvocationException error) {
                Check(error.InnerException is InvalidDataException,"exact Stable6 engine refuses Staging metadata");
            }
            var unchanged=StagingUpdate.Current(package);
            Check(StagingUpdate.IsNewer(candidate,unchanged),"one launcher selects a newer build with an intervening version skipped");
            Refuses(()=>StagingUpdate.Stage(package,candidate,(u,d,s,h)=>File.WriteAllText(d,"incomplete download")),
                "bad download is refused before any managed change");
            string stableZip;var wrong=Package(scratch,26,"1.9.18.19",gameFiles,out stableZip,true);
            Refuses(()=>Stage(package,wrong,stableZip),"Stable manifest inside Staging asset is refused");
            Check(Playtest.Matches(Playtest.ReadReady(root),initialPayload) && mutable==Json.Serialize(RuntimeMigration.Capture(root)),
                "failed download and cross-channel payload leave the bundled build playable");
            var next=Stage(package,candidate,zip);
            Playtest.Fault=point=>{if(point=="upgrade-old-moved")throw new IOException("interrupted file transaction");};
            Refuses(()=>StagingUpdate.Apply(package,initialPayload,next),"interruption stops after an owned file move");
            Playtest.Fault=null;
            StagingUpdate.Recover(package,null);
            Check(Playtest.Matches(Playtest.ReadReady(root),initialPayload) && Playtest.Prepare(package,null,initialPayload)==root,
                "restart recovers the earlier playable build and settings");
            StagingUpdate.Apply(package,initialPayload,next);
            var current=StagingUpdate.Current(package);
            Check(current.manifest.build==25 && !StagingUpdate.IsNewer(candidate,current),
                "skip-update commits and repeated current check has no work");
            Check(mutable==Json.Serialize(RuntimeMigration.Capture(root)) && Same(history,Snapshot(PathAt(package,StagingLogs.DirectoryName))),
                "skip-update preserves byte-exact private saves, preferences and retained logs");
            string laterZip; var later=Package(scratch,27,"1.9.18.20",gameFiles,out laterZip);
            var target=Stage(package,later,laterZip);
            Playtest.Fault=point=>{if(point=="upgrade-ready")throw new IOException("interrupted committed transaction");};
            Refuses(()=>StagingUpdate.Apply(package,current,target),"interruption after ready-file commit leaves recovery proof");
            Playtest.Fault=null;
            StagingUpdate.Recover(package,null);
            Check(StagingUpdate.Current(package).manifest.build==27 && mutable==Json.Serialize(RuntimeMigration.Capture(root)),
                "restart verifies the fully committed build and keeps Continue");
        }

        internal static int Main(string[] args) {
            try {
                if (args.Length!=4 || Directory.Exists(args[0])) throw new Exception("Supply new scratch, private native preference probe, Stable6 updater and Stable6 release.json.");
                string scratch=Playtest.Canon(args[0]); Directory.CreateDirectory(scratch);
                Preferences(PathAt(scratch,"preferences"), Playtest.Canon(args[1]));
                Exports(PathAt(scratch,"diagnostics"));
                Updates(PathAt(scratch,"updates"), Playtest.Canon(args[2]), Playtest.Canon(args[3]));
                Console.WriteLine("RESULT " + passed + " focused Windows checks passed.");
                return 0;
            } catch (Exception e) { Console.Error.WriteLine(e); return 1; }
        }
    }
}
