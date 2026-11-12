using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

namespace CoopPlusPlaytest {
    // Only managed code changes here. Save/state/settings roots are inventoried,
    // compared and never used as file-operation destinations.
    internal static class RuntimeMigration {
        internal const string PreviousId = "coopplus-20261110-b7-ctx1-23791d3a";
        internal const string PreviousHash = "00499a64b5361e42e5b4bb1870596a43e16dd5191f23e517dc71aff71305afff";
        internal const string PreviousReadyHash = "b8d2197e173228b3d189b6926f62b7fb7ab4594545c3f544064f7ca6d1d8933e";
        internal static bool AcceptedHash(string hash){return hash==PreviousHash||hash==PreviousReadyHash;}
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16*1024*1024 };
        static readonly StringComparer CI = StringComparer.OrdinalIgnoreCase;
        internal sealed class Mutable { public Entry[] files; public string[] directories; public string[] absent; }
        internal sealed class Change { public string path; public Entry before; public Entry after; }
        internal sealed class Journal { public int schema=1; public string id; public string root; public string fromHash; public string toHash; public string readyHash; public Mutable mutable; public Change[] changes; }
        static string Destination(string root,string path) {
            if(path.StartsWith("bin/",StringComparison.Ordinal))return Playtest.Child(root,"runtime/"+path);
            if(path.StartsWith("mod/quant.ew/",StringComparison.Ordinal))return Playtest.Child(root,"game/mods/"+path.Substring(4));
            throw new IOException("Unsafe managed migration destination.");
        }
        static void Put(string path,byte[] bytes) { Playtest.Canon(path);Directory.CreateDirectory(Path.GetDirectoryName(path));using(var stream=new FileStream(path,FileMode.CreateNew,FileAccess.Write,FileShare.None)){stream.Write(bytes,0,bytes.Length);stream.Flush(true);} }
        static bool Matches(string path,Entry e) { return e!=null&&File.Exists(path)&&new FileInfo(path).Length==e.size&&Playtest.Hash(path)==e.sha256; }
        static string Signature(Entry[] files) { return Json.Serialize(files.OrderBy(x=>x.path,CI).Select(x=>new Entry{path=x.path,size=x.size,sha256=x.sha256}).ToArray()); }
        static Change[] Delta(Manifest old,Manifest next) {
            var before=old.files.ToDictionary(x=>x.path,CI);var after=next.files.ToDictionary(x=>x.path,CI);
            return before.Keys.Union(after.Keys,CI).OrderBy(x=>x,CI).Select(path=>new Change{path=path,before=before.ContainsKey(path)?before[path]:null,after=after.ContainsKey(path)?after[path]:null}).Where(c=>c.before==null||c.after==null||c.before.size!=c.after.size||c.before.sha256!=c.after.sha256).ToArray();
        }
        static void BoundTransaction(string root,string tx,Journal journal,Manifest next,string nextHash) {
            if(journal.toHash!=nextHash)throw new IOException("Interrupted update belongs to a different exact frozen package.");
            string original=Playtest.Child(tx,"ready.before.json");
            if(!File.Exists(original)||Playtest.Hash(original)!=journal.readyHash)throw new IOException("Original readiness proof is unavailable.");
            var ready=Json.Deserialize<Ready>(File.ReadAllText(original,new UTF8Encoding(false,true)));if(ready==null||ready.payloadHash!=journal.fromHash)throw new IOException("Journal predecessor identity disagrees with original readiness.");var old=Predecessor(next,ready);
            if(Json.Serialize(Delta(old,next))!=Json.Serialize(journal.changes))throw new IOException("Interrupted migration change list does not match the exact pinned old/new managed inventories.");
            var allowed=new System.Collections.Generic.Dictionary<string,string>(CI);
            allowed[original]=journal.readyHash;allowed[Playtest.Child(tx,"ready.replaced.json")]=journal.readyHash;
            ready.runtimeId=next.runtimeId;ready.payloadHash=nextHash;allowed[Playtest.Child(tx,"ready.next.json")]=Playtest.HashBytes(Encoding.UTF8.GetBytes(Json.Serialize(ready)));
            foreach(var c in journal.changes){if(c.before!=null)allowed[Playtest.Child(tx,"old/"+c.path)]=c.before.sha256;if(c.after!=null){allowed[Playtest.Child(tx,"new/"+c.path)]=c.after.sha256;allowed[Playtest.Child(tx,"retained-new/"+c.path)]=c.after.sha256;}}
            var directories=new System.Collections.Generic.HashSet<string>(CI){tx};foreach(string file in allowed.Keys){string parent=Path.GetDirectoryName(file);while(Playtest.Within(parent,tx)){directories.Add(parent);if(CI.Equals(parent,tx))break;parent=Path.GetDirectoryName(parent);}}
            var pending=new System.Collections.Generic.Stack<string>();pending.Push(tx);
            while(pending.Count>0){string path=pending.Pop();Playtest.Canon(path);foreach(string directory in Directory.GetDirectories(path)){Playtest.Canon(directory);if(!directories.Contains(directory))throw new IOException("Unexpected transaction directory; it was retained.");pending.Push(directory);}foreach(string file in Directory.GetFiles(path)){Playtest.Canon(file);string hash;if(!allowed.TryGetValue(file,out hash)||Playtest.Hash(file)!=hash)throw new IOException("Unexpected or corrupt transaction file; it was retained.");}}
        }
        internal static Mutable Capture(string root) {
            var files=new List<Entry>();var dirs=new List<string>();var absent=new List<string>();
            foreach(string name in new[]{"game/save00","game/save_shared","game/save_stats","proxy","game/mods/quant.ew/files/system/player/tmp","game/mods/quant.ew/data/generated/sprite_uv_maps"}) {
                string path=Playtest.Child(root,name);
                if(!Directory.Exists(path)){if(File.Exists(path))throw new IOException("File-shaped private mutable root.");absent.Add(name);continue;}
                dirs.Add(name);
                foreach(string f in Playtest.Files(path)) { files.Add(new Entry{path=f.Substring(root.Length+1).Replace('\\','/'),size=new FileInfo(f).Length,sha256=Playtest.Hash(f)}); }
                var pending=new Stack<string>();pending.Push(path);
                while(pending.Count>0)foreach(string d in Directory.GetDirectories(pending.Pop())){Playtest.Canon(d);dirs.Add(d.Substring(root.Length+1).Replace('\\','/'));pending.Push(d);}
            }
            return new Mutable{files=files.OrderBy(x=>x.path,CI).ToArray(),directories=dirs.OrderBy(x=>x,CI).ToArray(),absent=absent.OrderBy(x=>x,CI).ToArray()};
        }
        static void Unchanged(string root,Mutable expected) {
            if(expected==null||Json.Serialize(Capture(root))!=Json.Serialize(expected))throw new IOException("Private saves/settings/state changed during managed migration. Everything is retained; no run was reset.");
        }
        static Manifest Predecessor(Manifest next,Ready ready) {
            if(ready==null||ready.schema!=1||ready.game==null||ready.runtimeId!=PreviousId||!AcceptedHash(ready.payloadHash))throw new IOException("This private runtime is not an accepted predecessor. Keep its progress and logs for review; no reset or replacement folder is required.");
            var from=(next.upgradeFrom??new UpgradeFrom[0]).SingleOrDefault(x=>x.runtimeId==PreviousId&&x.payloadHash==ready.payloadHash);
            if(from==null||String.IsNullOrEmpty(from.manifestBase64)||from.files==null)throw new IOException("The new package lacks the exact predecessor metadata required to upgrade this private run.");
            byte[] bytes;try{bytes=Convert.FromBase64String(from.manifestBase64);}catch(FormatException){throw new IOException("Invalid predecessor metadata encoding.");}
            if(bytes.Length>8*1024*1024||Playtest.HashBytes(bytes)!=ready.payloadHash)throw new IOException("Predecessor metadata hash does not match the accepted frozen TEST.");
            var old=Json.Deserialize<Manifest>(new UTF8Encoding(false,true).GetString(bytes));
            if(old==null||old.schema!=1||old.runtimeId!=PreviousId||old.files==null||Signature(old.files)!=Signature(from.files))throw new IOException("Predecessor managed inventory is incomplete or inconsistent.");
            return old;
        }
        static void CheckUnmanaged(string root,Manifest old) {
            var expected=new HashSet<string>(old.files.Select(x=>x.path),CI);
            string mod=Playtest.Child(root,"game/mods/quant.ew");
            foreach(string f in Playtest.Files(mod)) {
                string rel=f.Substring(mod.Length+1).Replace('\\','/');if(expected.Contains("mod/quant.ew/"+rel))continue;
                bool player=Regex.IsMatch(rel,@"^files/system/player/tmp/[0-9a-f]{16}(_[a-z0-9_]+)?\.(png|xml|txt)$",RegexOptions.IgnoreCase);
                bool uv=Regex.IsMatch(rel,@"^data/generated/sprite_uv_maps/mods\.quant\.ew\.files\.system\.player\.tmp\.[0-9a-f]{16}\.png$",RegexOptions.IgnoreCase);
                bool marker=rel=="files/system/player/tmp/no_crown"||rel=="files/system/player/tmp/no_amulet"||rel=="files/system/player/tmp/no_amulet_gem";
                if(marker&&new FileInfo(f).Length!=0)throw new IOException("Generated cosmetics marker must remain empty; it was retained without migration: "+rel);
                if(!player&&!uv&&!marker)throw new IOException("Unrecognized unmanaged mod file; mixed gameplay was not carried forward: "+rel);
            }
        }
        static Journal ReadJournal(string root) {
            string path=Playtest.Child(root,"runtime-upgrade-journal.json");
            if(new FileInfo(path).Length>16*1024*1024)throw new IOException("Oversized private migration journal.");
            var j=Json.Deserialize<Journal>(File.ReadAllText(path,new UTF8Encoding(false,true)));
            Guid id;
            if(j==null||j.schema!=1||!Guid.TryParseExact(j.id,"N",out id)||!CI.Equals(j.root,root)||!AcceptedHash(j.fromHash)||j.changes==null||j.mutable==null)throw new IOException("Private migration journal is invalid; preserve this folder for review.");
            var seen=new HashSet<string>(CI);
            foreach(var c in j.changes){if(c==null||!seen.Add(c.path)||(c.before==null&&c.after==null))throw new IOException("Invalid migration change list.");Destination(root,c.path);if(c.before!=null&&c.before.path!=c.path||c.after!=null&&c.after.path!=c.path)throw new IOException("Migration file identity mismatch.");}
            return j;
        }
        static string Transaction(string root,Journal j){return Playtest.Child(root,"runtime-upgrades/"+j.id);}
        internal static void Recover(string root,Manifest next,string nextHash) {
            string journal=Playtest.Child(root,"runtime-upgrade-journal.json");if(!File.Exists(journal))return;
            Playtest.CheckStopped();Playtest.Phase("Recovering interrupted private runtime update");
            var j=ReadJournal(root);string tx=Transaction(root,j);BoundTransaction(root,tx,j,next,nextHash);Unchanged(root,j.mutable);
            string readyPath=Playtest.Child(root,"ready.json");var ready=File.Exists(readyPath)?Json.Deserialize<Ready>(File.ReadAllText(readyPath)):null;
            if(ready!=null&&ready.payloadHash==j.toHash&&ready.runtimeId==next.runtimeId) {
                if(j.toHash!=nextHash)throw new IOException("Interrupted update belongs to a different frozen package.");
                Playtest.VerifyRuntime(root,next);Playtest.VerifySaveFolders(root);
                File.Move(journal,Playtest.Child(tx,"journal.completed.json"));return;
            }
            string before=Playtest.Child(tx,"ready.before.json");
            if(!File.Exists(before)||Playtest.Hash(before)!=j.readyHash)throw new IOException("Original readiness proof is unavailable; no automatic state repair was attempted.");
            if(ready==null||ready.payloadHash!=j.fromHash||Playtest.Hash(readyPath)!=j.readyHash)throw new IOException("Readiness metadata changed during interrupted migration; everything is retained.");
            // Prove every active/backup state before moving even the first file.
            foreach(var c in j.changes){string active=Destination(root,c.path),backup=Playtest.Child(tx,"old/"+c.path);bool original=Matches(active,c.before);if(File.Exists(active)&&!original&&!Matches(active,c.after))throw new IOException("Unexpected active managed file; recovery did not move anything.");if(c.before!=null&&!original&&!Matches(backup,c.before))throw new IOException("Verified rollback source is missing; recovery did not move anything.");if(File.Exists(active)&&!original&&File.Exists(Playtest.Child(tx,"retained-new/"+c.path)))throw new IOException("Ambiguous duplicate recovery file; nothing was moved.");}
            foreach(var c in j.changes.Reverse()) {
                string active=Destination(root,c.path),backup=Playtest.Child(tx,"old/"+c.path);
                if(c.before!=null&&Matches(active,c.before))continue;
                if(File.Exists(active)) {
                    if(!Matches(active,c.after))throw new IOException("Unexpected managed file during interrupted-update recovery: "+c.path);
                    string failed=Playtest.Child(tx,"retained-new/"+c.path);Directory.CreateDirectory(Path.GetDirectoryName(failed));File.Move(active,failed);
                }
                if(c.before!=null) {
                    if(!Matches(backup,c.before))throw new IOException("Verified old managed file is missing: "+c.path);
                    Directory.CreateDirectory(Path.GetDirectoryName(active));File.Move(backup,active);
                }
            }
            Unchanged(root,j.mutable);File.Move(journal,Playtest.Child(tx,"journal.rolled-back.json"));
            Playtest.Phase("Interrupted managed update rolled back; private progress retained");
        }
        internal static void Upgrade(string package,string root,Ready ready,VerifiedPayload verified) {
            Playtest.CheckStopped();var old=Predecessor(verified.manifest,ready);
            Playtest.Phase("Verifying accepted predecessor before private runtime upgrade");Playtest.VerifyRuntime(root,old);CheckUnmanaged(root,old);Playtest.VerifySaveFolders(root);
            var changes=Delta(old,verified.manifest);
            string readyPath=Playtest.Child(root,"ready.json");byte[] readyBytes=File.ReadAllBytes(readyPath);
            var j=new Journal{id=Guid.NewGuid().ToString("N"),root=root,fromHash=ready.payloadHash,toHash=verified.hash,readyHash=Playtest.HashBytes(readyBytes),mutable=Capture(root),changes=changes};
            string tx=Transaction(root,j);Directory.CreateDirectory(tx);Put(Playtest.Child(tx,"ready.before.json"),readyBytes);
            foreach(var c in changes)if(c.after!=null){string staged=Playtest.Child(tx,"new/"+c.path);Directory.CreateDirectory(Path.GetDirectoryName(staged));File.Copy(Playtest.Child(Path.Combine(package,"payload"),c.path),staged,false);if(!Matches(staged,c.after))throw new IOException("Staged private runtime verification failed.");}
            Put(Playtest.Child(root,"runtime-upgrade-journal.json"),Encoding.UTF8.GetBytes(Json.Serialize(j)));
            if(Playtest.Fault!=null)Playtest.Fault("upgrade-prepared");
            Playtest.Phase("Updating private runtime while retaining saves and settings");
            foreach(var c in changes) {
                Playtest.CheckStopped();string active=Destination(root,c.path);
                if(c.before!=null){if(!Matches(active,c.before))throw new IOException("Old managed file changed during upgrade.");string backup=Playtest.Child(tx,"old/"+c.path);Directory.CreateDirectory(Path.GetDirectoryName(backup));File.Move(active,backup);}
                else if(File.Exists(active))throw new IOException("Unexpected new managed destination already exists.");
                if(Playtest.Fault!=null)Playtest.Fault("upgrade-old-moved");
                if(c.after!=null){Directory.CreateDirectory(Path.GetDirectoryName(active));File.Move(Playtest.Child(tx,"new/"+c.path),active);}
                if(Playtest.Fault!=null)Playtest.Fault("upgrade-new-moved");
            }
            Playtest.VerifyRuntime(root,verified.manifest);Unchanged(root,j.mutable);Playtest.CheckStopped();
            if(Playtest.Hash(readyPath)!=j.readyHash)throw new IOException("Readiness metadata changed before update commit.");
            ready.runtimeId=verified.manifest.runtimeId;ready.payloadHash=verified.hash;
            string temp=Playtest.Child(tx,"ready.next.json");Put(temp,Encoding.UTF8.GetBytes(Json.Serialize(ready)));
            File.Replace(temp,readyPath,Playtest.Child(tx,"ready.replaced.json"));
            if(Playtest.Fault!=null)Playtest.Fault("upgrade-ready");
            Unchanged(root,j.mutable);File.Move(Playtest.Child(root,"runtime-upgrade-journal.json"),Playtest.Child(tx,"journal.completed.json"));
            Playtest.Phase("Private runtime upgraded; existing Continue and settings retained");
        }
    }
}
