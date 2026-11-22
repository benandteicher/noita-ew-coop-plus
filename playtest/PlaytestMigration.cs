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
        static bool ValidFingerprint(Entry entry) {
            return entry != null && !String.IsNullOrWhiteSpace(entry.path) &&
                entry.size >= 0 && StagingRelease.ValidHash(entry.sha256);
        }
        internal static bool SameOwnedGame(Entry[] installed, Entry[] offered) {
            if (installed == null || offered == null || installed.Length == 0 || installed.Length != offered.Length)
                return false;
            var remaining = new Dictionary<string, Entry>(CI);
            foreach (var entry in installed) {
                if (!ValidFingerprint(entry) || remaining.ContainsKey(entry.path)) return false;
                remaining.Add(entry.path, entry);
            }
            foreach (var entry in offered) {
                Entry previous;
                if (!ValidFingerprint(entry) || !remaining.TryGetValue(entry.path, out previous) ||
                    entry.size != previous.size || entry.sha256 != previous.sha256)
                    return false;
                remaining.Remove(entry.path);
            }
            return remaining.Count == 0;
        }
        static Change[] Delta(Manifest old,Manifest next) {
            var before=old.files.ToDictionary(x=>x.path,CI);var after=next.files.ToDictionary(x=>x.path,CI);
            return before.Keys.Union(after.Keys,CI).OrderBy(x=>x,CI).Select(path=>new Change{path=path,before=before.ContainsKey(path)?before[path]:null,after=after.ContainsKey(path)?after[path]:null}).Where(c=>c.before==null||c.after==null||c.before.size!=c.after.size||c.before.sha256!=c.after.sha256).ToArray();
        }
        static void BoundTransaction(string tx, Journal journal, VerifiedPayload previous, VerifiedPayload next) {
            if(journal.fromHash!=previous.hash||journal.toHash!=next.hash)throw new IOException("Interrupted update belongs to different approved Staging packages.");
            string original=Playtest.Child(tx,"ready.before.json");
            if(!File.Exists(original)||Playtest.Hash(original)!=journal.readyHash)throw new IOException("Original readiness proof is unavailable.");
            var ready=Json.Deserialize<Ready>(File.ReadAllText(original,new UTF8Encoding(false,true)));
            var old=Predecessor(previous,next,ready);
            if(Json.Serialize(Delta(old,next.manifest))!=Json.Serialize(journal.changes))throw new IOException("Interrupted migration change list does not match the exact pinned old/new managed inventories.");
            var allowed=new System.Collections.Generic.Dictionary<string,string>(CI);
            allowed[original]=journal.readyHash;allowed[Playtest.Child(tx,"ready.replaced.json")]=journal.readyHash;
            Playtest.Bind(ready,next);allowed[Playtest.Child(tx,"ready.next.json")]=Playtest.HashBytes(Encoding.UTF8.GetBytes(Json.Serialize(ready)));
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
        static Manifest Predecessor(VerifiedPayload previous, VerifiedPayload next, Ready ready) {
            if (!Playtest.Matches(ready, previous)) throw new IOException("The installed Staging runtime does not match its approved manifest.");
            var old = previous.manifest;
            if (next.manifest.channel != Playtest.Channel || next.manifest.build <= old.build ||
                new Version(next.manifest.version) <= new Version(old.version))
                throw new IOException("Choose a newer approved Staging build.");
            if (!SameOwnedGame(old.ownedGame, next.manifest.ownedGame))
                throw new IOException("This update requires a different local Noita version. The current Staging installation is still available.");
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
            if(j==null||j.schema!=1||!Guid.TryParseExact(j.id,"N",out id)||!CI.Equals(j.root,root)||!StagingRelease.ValidHash(j.fromHash)||!StagingRelease.ValidHash(j.toHash)||j.changes==null||j.mutable==null)throw new IOException("Private migration journal is invalid; preserve this folder for review.");
            var seen=new HashSet<string>(CI);
            foreach(var c in j.changes){if(c==null||!seen.Add(c.path)||(c.before==null&&c.after==null))throw new IOException("Invalid migration change list.");Destination(root,c.path);if(c.before!=null&&c.before.path!=c.path||c.after!=null&&c.after.path!=c.path)throw new IOException("Migration file identity mismatch.");}
            return j;
        }
        static string Transaction(string root,Journal j){return Playtest.Child(root,"runtime-upgrades/"+j.id);}
        internal static void Recover(string root, VerifiedPayload previous, VerifiedPayload next) {
            string journal=Playtest.Child(root,"runtime-upgrade-journal.json");if(!File.Exists(journal))return;
            Playtest.CheckStopped();Playtest.Phase("Recovering interrupted private runtime update");
            var j=ReadJournal(root);string tx=Transaction(root,j);BoundTransaction(tx,j,previous,next);Unchanged(root,j.mutable);
            string readyPath=Playtest.Child(root,"ready.json");var ready=Playtest.ReadReady(root);
            if(Playtest.Matches(ready,next)) {
                Playtest.VerifyRuntime(root,next.manifest);Playtest.VerifySaveFolders(root);
                File.Move(journal,Playtest.Child(tx,"journal.completed.json"));return;
            }
            string before=Playtest.Child(tx,"ready.before.json");
            if(!File.Exists(before)||Playtest.Hash(before)!=j.readyHash)throw new IOException("Original readiness proof is unavailable; no automatic state repair was attempted.");
            if(!Playtest.Matches(ready,previous)||Playtest.Hash(readyPath)!=j.readyHash)throw new IOException("Readiness metadata changed during interrupted migration; everything is retained.");
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
            Playtest.VerifyRuntime(root,previous.manifest);Playtest.VerifySaveFolders(root);
            Unchanged(root,j.mutable);File.Move(journal,Playtest.Child(tx,"journal.rolled-back.json"));
            Playtest.Phase("Interrupted managed update rolled back; private progress retained");
        }
        internal static void Upgrade(string root, Ready ready, VerifiedPayload previous, VerifiedPayload verified) {
            Playtest.CheckStopped();var old=Predecessor(previous,verified,ready);
            Playtest.Phase("Verifying accepted predecessor before private runtime upgrade");
            Playtest.Verify(Playtest.Child(root, "game"), old.ownedGame);
            Playtest.VerifyRuntime(root,old);CheckUnmanaged(root,old);Playtest.VerifySaveFolders(root);
            var changes=Delta(old,verified.manifest);
            string readyPath=Playtest.Child(root,"ready.json");byte[] readyBytes=File.ReadAllBytes(readyPath);
            var j=new Journal{id=Guid.NewGuid().ToString("N"),root=root,fromHash=ready.payloadHash,toHash=verified.hash,readyHash=Playtest.HashBytes(readyBytes),mutable=Capture(root),changes=changes};
            string tx=Transaction(root,j);Directory.CreateDirectory(tx);Put(Playtest.Child(tx,"ready.before.json"),readyBytes);
            foreach(var c in changes)if(c.after!=null){string staged=Playtest.Child(tx,"new/"+c.path);Directory.CreateDirectory(Path.GetDirectoryName(staged));File.Copy(Playtest.Child(verified.payload,c.path),staged,false);if(!Matches(staged,c.after))throw new IOException("Staged private runtime verification failed.");}
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
            Playtest.Bind(ready,verified);
            string temp=Playtest.Child(tx,"ready.next.json");Put(temp,Encoding.UTF8.GetBytes(Json.Serialize(ready)));
            File.Replace(temp,readyPath,Playtest.Child(tx,"ready.replaced.json"));
            if(Playtest.Fault!=null)Playtest.Fault("upgrade-ready");
            Unchanged(root,j.mutable);File.Move(Playtest.Child(root,"runtime-upgrade-journal.json"),Playtest.Child(tx,"journal.completed.json"));
            Playtest.Phase("Private runtime upgraded; existing Continue and settings retained");
        }
    }
}
