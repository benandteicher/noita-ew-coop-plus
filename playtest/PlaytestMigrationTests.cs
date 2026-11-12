// Developer-only file transaction fixtures. No proxy/game process is launched.
using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Web.Script.Serialization;
using CoopPlusPlaytest;

public static class PlaytestMigrationTests {
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer{MaxJsonLength=16*1024*1024};static int count;
    static void Check(bool value,string name){if(!value)throw new Exception(name);Console.WriteLine("PASS "+name);count++;}
    static void Refuses(Action action,string name){try{action();}catch{Check(true,name);return;}throw new Exception("Not refused: "+name);}
    static void RefusesWith(Action action,string reason,string name){try{action();}catch(IOException error){Check(error.Message.Contains(reason),name);return;}throw new Exception("Not refused: "+name);}
    static void Text(string file,string data){Directory.CreateDirectory(Path.GetDirectoryName(file));File.WriteAllText(file,data,new UTF8Encoding(false));}
    static Entry Describe(string root,string file){return new Entry{path=file.Substring(root.Length+1).Replace('\\','/'),size=new FileInfo(file).Length,sha256=Playtest.Hash(file)};}
    static string Setup(string oldPayload,string scratch,string name) {
        string package=Path.Combine(scratch,name),root=Path.Combine(package,"private"),game=Path.Combine(root,"game"),payload=Path.Combine(package,"payload");Directory.CreateDirectory(game);
        byte[] oldBytes=File.ReadAllBytes(Path.Combine(oldPayload,"manifest.json"));string oldHash=Playtest.HashBytes(oldBytes);if(!RuntimeMigration.AcceptedHash(oldHash))throw new Exception("Expected exact accepted predecessor manifest.");var old=Json.Deserialize<Manifest>(Encoding.UTF8.GetString(oldBytes));
        foreach(var e in old.files) {
            string source=Path.Combine(oldPayload,e.path.Replace('/','\\'));string dest=e.path.StartsWith("bin/")?Path.Combine(root,"runtime",e.path.Replace('/','\\')):Path.Combine(game,"mods",e.path.Substring(4).Replace('/','\\'));
            Directory.CreateDirectory(Path.GetDirectoryName(dest));File.Copy(source,dest,false);string bundled=Path.Combine(payload,e.path.Replace('/','\\'));Directory.CreateDirectory(Path.GetDirectoryName(bundled));File.Copy(source,bundled,false);
        }
        foreach(string file in new[]{"noita.exe","data/data.wak","lua51.dll","SDL2.dll","steam_api.dll","config.xml"})Text(Path.Combine(game,file.Replace('/','\\')),"synthetic owned fixture "+file);
        Text(Path.Combine(game,"steam_appid.txt"),"881100");var gameEntries=Directory.GetFiles(game,"*",SearchOption.AllDirectories).Where(f=>!f.StartsWith(Path.Combine(game,"mods")+"\\",StringComparison.OrdinalIgnoreCase)).Select(f=>Describe(game,f)).ToArray();
        Text(Path.Combine(game,"save00","player.xml"),"<Entity name=\"existing Continue\" />");Text(Path.Combine(game,"save00","mod_config.xml"),"<Mods />");Text(Path.Combine(game,"save_shared","config.xml"),"<Config />");Text(Path.Combine(game,"save_stats","stats.xml"),"<Stats count=\"11\" />");
        Text(Path.Combine(root,"proxy","proxy.ron"),"unchanged private preference fixture");Directory.CreateDirectory(Path.Combine(root,"proxy","save_state"));
        foreach(string file in new[]{"run_info_v2.bit","world_chunks.bit","des_entity_storage.bit","flags.bit"})Text(Path.Combine(root,"proxy","save_state",file),"retained opaque world fixture "+file);
        Directory.CreateDirectory(Path.Combine(root,"temp"));Directory.CreateDirectory(Path.Combine(root,"profile","AppData","Local"));
        Text(Path.Combine(game,"mods","quant.ew","files","system","player","tmp","0000000000000001.png"),"retained generated player sprite");
        foreach(string marker in new[]{"no_crown","no_amulet","no_amulet_gem"})Text(Path.Combine(game,"mods","quant.ew","files","system","player","tmp",marker),"");
        Text(Path.Combine(root,"ready.json"),Json.Serialize(new Ready{schema=1,runtimeId=RuntimeMigration.PreviousId,payloadHash=oldHash,source="synthetic stopped fixture",game=gameEntries}));
        using(var stream=new FileStream(Path.Combine(payload,"bin","noita_proxy.exe"),FileMode.Append,FileAccess.Write))stream.WriteByte(0); // File fixture only, never executed.
        var files=Directory.GetFiles(payload,"*",SearchOption.AllDirectories).Select(f=>Describe(payload,f)).ToArray();
        var owned=gameEntries.Where(e=>new[]{"noita.exe","data/data.wak","lua51.dll","SDL2.dll","steam_api.dll"}.Contains(e.path)).ToArray();
        Text(Path.Combine(payload,"manifest.json"),Json.Serialize(new Manifest{schema=1,runtimeId=Playtest.RuntimeId,label="File migration fixture",files=files,ownedGame=owned,upgradeFrom=new[]{new UpgradeFrom{runtimeId=RuntimeMigration.PreviousId,payloadHash=oldHash,manifestBase64=Convert.ToBase64String(oldBytes),files=old.files}}}));
        return package;
    }
    public static int Main(string[] args){try {
        if(args.Length!=2||Directory.Exists(args[1]))throw new Exception("Pass exact old extracted payload folder and a new safe scratch folder.");string oldPayload=Playtest.Canon(args[0]),scratch=Playtest.Canon(args[1]);Directory.CreateDirectory(scratch);
        string success=Setup(oldPayload,scratch,"Success"),root=Path.Combine(success,"private");string before=Json.Serialize(RuntimeMigration.Capture(root));Playtest.Prepare(success,null);
        Check(before==Json.Serialize(RuntimeMigration.Capture(root)),"managed upgrade retains all private save state settings and stats");Check(File.ReadAllText(Path.Combine(root,"ready.json")).Contains(Playtest.RuntimeId),"ready committed to new frozen runtime");
        Check(File.ReadAllText(Path.Combine(root,"game","mods","quant.ew","files","system","player","tmp","0000000000000001.png"))=="retained generated player sprite","generated Continue sprite retained");
        Check(new[]{"no_crown","no_amulet","no_amulet_gem"}.All(name=>{string path=Path.Combine(root,"game","mods","quant.ew","files","system","player","tmp",name);return File.Exists(path)&&new FileInfo(path).Length==0;}),"exact empty generated cosmetics markers retained");
        string again=File.ReadAllText(Path.Combine(root,"ready.json"));Playtest.Prepare(success,null);Check(again==File.ReadAllText(Path.Combine(root,"ready.json")),"second current open leaves readiness unchanged");
        string interrupted=Setup(oldPayload,scratch,"Interrupted"),interruptedRoot=Path.Combine(interrupted,"private");before=Json.Serialize(RuntimeMigration.Capture(interruptedRoot));
        Playtest.Fault=point=>{if(point=="upgrade-old-moved")throw new IOException("injected managed transaction interruption");};Refuses(()=>Playtest.Prepare(interrupted,null),"interrupted managed update fails with retained journal");Playtest.Fault=null;
        string journalPath=Path.Combine(interruptedRoot,"runtime-upgrade-journal.json");Check(File.Exists(journalPath),"interruption journal exists before restart");string originalJournal=File.ReadAllText(journalPath);
        var journal=Json.Deserialize<RuntimeMigration.Journal>(originalJournal);var first=journal.changes[0];first.path="mod/quant.ew/files/system/player/tmp/0000000000000001.png";if(first.before!=null)first.before.path=first.path;if(first.after!=null)first.after.path=first.path;File.WriteAllText(journalPath,Json.Serialize(journal));
        RefusesWith(()=>Playtest.Prepare(interrupted,null),"change list","safe-prefix journal tampering refused before any recovery move");Check(before==Json.Serialize(RuntimeMigration.Capture(interruptedRoot)),"journal tampering preserves generated sprites and progress");
        journal=Json.Deserialize<RuntimeMigration.Journal>(originalJournal);journal.toHash=new string('0',64);File.WriteAllText(journalPath,Json.Serialize(journal));RefusesWith(()=>Playtest.Prepare(interrupted,null),"different exact frozen package","wrong target journal refused before recovery moves");
        File.WriteAllText(journalPath,originalJournal);Playtest.Prepare(interrupted,null);Check(before==Json.Serialize(RuntimeMigration.Capture(interruptedRoot)),"same-folder recovery and upgrade preserve Continue");
        string mixed=Setup(oldPayload,scratch,"Unknown override");Text(Path.Combine(mixed,"private","game","mods","quant.ew","unknown.lua"),"unrecognized local gameplay");Refuses(()=>Playtest.Prepare(mixed,null),"unknown unmanaged Lua override refused");
        string badMarker=Setup(oldPayload,scratch,"Nonempty cosmetics marker"),badRoot=Path.Combine(badMarker,"private"),markerPath=Path.Combine(badRoot,"game","mods","quant.ew","files","system","player","tmp","no_amulet");Text(markerPath,"tampered marker");
        RefusesWith(()=>Playtest.Prepare(badMarker,null),"must remain empty","nonempty cosmetics marker refused before migration");Check(File.ReadAllText(markerPath)=="tampered marker"&&!File.Exists(Path.Combine(badRoot,"runtime-upgrade-journal.json")),"tampered marker retained without managed writes");
        Console.WriteLine("RESULT "+count+" focused migration cases passed");return 0;
    }catch(Exception e){Console.Error.WriteLine(e);return 1;}}
}
