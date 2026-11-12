using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Microsoft.Win32.SafeHandles;

namespace CoopPlusPlaytest {
    public sealed class ProcessIdentity { public int pid; public long createdFileTimeUtc; public long endedFileTimeUtc; public string exe; public string role; }
    public sealed class RestartRequest { public int schema; public string session; public int parentPid; public long parentCreatedFileTimeUtc; public string exe; public string directory; public string[] arguments; }
    public sealed class SessionStamp { public int schema=1; public string owner="CoopPlusPlaytestSession"; public string session; public int launcherPid; public long launcherCreatedFileTimeUtc; public string startedUtc; public string finishedUtc; public string result; }
    internal sealed class SessionAudit : IDisposable {
        const long Limit=1024*1024;
        internal readonly string Id, DirectoryPath;
        readonly object sync=new object();readonly Stopwatch clock=Stopwatch.StartNew();
        readonly JavaScriptSerializer json=new JavaScriptSerializer();readonly SessionStamp stamp;
        internal SessionAudit(string package) {
            Id=Guid.NewGuid().ToString("N");DirectoryPath=Playtest.Canon(Path.Combine(package,"playtest-logs",Id));
            string[] removed=Prune(Path.GetDirectoryName(DirectoryPath));System.IO.Directory.CreateDirectory(DirectoryPath);
            using(var own=Process.GetCurrentProcess())stamp=new SessionStamp{session=Id,launcherPid=own.Id,launcherCreatedFileTimeUtc=own.StartTime.ToUniversalTime().ToFileTimeUtc(),startedUtc=DateTime.UtcNow.ToString("o")};
            File.WriteAllText(Path.Combine(DirectoryPath,"session.json"),json.Serialize(stamp),new UTF8Encoding(false));
            Write("launcher-start",new {runtimeId=Playtest.RuntimeId,launcherRevision=Playtest.LauncherRevision});
            Write("diagnostic-retention",new{maximumSessions=12,maximumBytesPerGuardianLog=Limit,removedOwnedDiagnosticSessions=removed,scope="Only validated owned diagnostic leaves; no save/state/settings paths"});
        }
        static string[] Prune(string logs) {
            Playtest.Canon(logs);if(!System.IO.Directory.Exists(logs))return new string[0];
            var owned=new List<string>();
            foreach(string directory in System.IO.Directory.GetDirectories(logs)) {
                Guid token;if(!Guid.TryParseExact(Path.GetFileName(directory),"N",out token))continue;Playtest.Canon(directory);
                string marker=Path.Combine(directory,"session.json");if(!File.Exists(marker)||new FileInfo(marker).Length>65536)continue;
                SessionStamp old;try{old=new JavaScriptSerializer().Deserialize<SessionStamp>(File.ReadAllText(marker));}catch{continue;}
                if(old==null||old.schema!=1||old.owner!="CoopPlusPlaytestSession"||old.session!=Path.GetFileName(directory))continue;
                bool alive=false;
                try{using(var p=Process.GetProcessById(old.launcherPid))alive=!p.HasExited&&p.StartTime.ToUniversalTime().ToFileTimeUtc()==old.launcherCreatedFileTimeUtc;}catch(ArgumentException){}catch{alive=true;}
                if(alive)continue;
                if(System.IO.Directory.GetDirectories(directory).Length!=0)continue;
                var files=System.IO.Directory.GetFiles(directory);
                if(files.Any(f=>(File.GetAttributes(f)&FileAttributes.ReparsePoint)!=0||!KnownLog(Path.GetFileName(f))))continue;
                owned.Add(directory);
            }
            var removed=new List<string>();foreach(string directory in owned.OrderByDescending(x=>System.IO.Directory.GetCreationTimeUtc(x)).Skip(11)) {
                // Only proven diagnostic session leaves under this exact log root.
                if(!Playtest.Within(directory,logs))throw new IOException("Unsafe diagnostic retention target.");
                foreach(string file in System.IO.Directory.GetFiles(directory)){Playtest.Canon(file);File.Delete(file);}System.IO.Directory.Delete(directory,false);
                removed.Add(Path.GetFileName(directory));
            }
            return removed.ToArray();
        }
        static bool KnownLog(string name){return name=="session.json"||name=="events.jsonl"||name=="stdout.log"||name=="stderr.log"||name=="failure.txt"||System.Text.RegularExpressions.Regex.IsMatch(name,@"^(proxy-[0-9]+-[0-9]+(\.jsonl|\.failure\.txt)|prior-[0-9]+-ew_log\.txt|restart-[0-9]+-[0-9]+\.(new|json(\.consumed\.json)?))$");}
        internal string DiagnosticWriteError;
        internal void Append(string name,string value) {
            if(name!="stdout.log"&&name!="stderr.log"&&name!="events.jsonl")throw new IOException("Invalid session log name.");
            try{lock(sync){string file=Path.Combine(DirectoryPath,name);Playtest.Canon(file);long current=File.Exists(file)?new FileInfo(file).Length:0;if(current>=Limit)return;byte[] bytes=Encoding.UTF8.GetBytes(value);int count=(int)Math.Min(bytes.Length,Limit-current);using(var s=new FileStream(file,FileMode.Append,FileAccess.Write,FileShare.Read)){s.Write(bytes,0,count);s.Flush(true);}}}catch(Exception error){DiagnosticWriteError=error.Message;}
        }
        internal void Write(string kind,object detail){try{Append("events.jsonl",json.Serialize(new{utc=DateTime.UtcNow.ToString("o"),elapsedMs=clock.Elapsed.TotalMilliseconds,kind=kind,detail=detail})+"\n");}catch(Exception error){DiagnosticWriteError=error.Message;}}
        internal void Finish(string result){stamp.result=result;stamp.finishedUtc=DateTime.UtcNow.ToString("o");Write("launcher-finish",new{result=result});File.WriteAllText(Path.Combine(DirectoryPath,"session.json"),json.Serialize(stamp),new UTF8Encoding(false));}
        public void Dispose(){}
    }
    internal static class SessionController {
        internal static Action<string> Fault;
        [DllImport("kernel32.dll",SetLastError=true)]static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
        [DllImport("user32.dll")]static extern bool IsWindowVisible(IntPtr window);
        [DllImport("kernel32.dll",SetLastError=true)]static extern SafeFileHandle CreateToolhelp32Snapshot(uint flags,uint pid);
        [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]struct ProcessEntry {public uint size,usage,pid;public UIntPtr heap;public uint module,threads,parent;public int priority;public uint flags;[MarshalAs(UnmanagedType.ByValTStr,SizeConst=260)]public string name;}
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true)]static extern bool Process32FirstW(SafeFileHandle snapshot,ref ProcessEntry entry);
        [DllImport("kernel32.dll",CharSet=CharSet.Unicode,ExactSpelling=true)]static extern bool Process32NextW(SafeFileHandle snapshot,ref ProcessEntry entry);
        static readonly JavaScriptSerializer Json=new JavaScriptSerializer{MaxJsonLength=1024*1024};
        static ProcessIdentity Identity(Process p,string exe,string role) {
            long created,exit,kernel,user;if(!GetProcessTimes(p.Handle,out created,out exit,out kernel,out user))throw new IOException("Cannot establish the exact owned process creation identity.");
            return new ProcessIdentity{pid=p.Id,createdFileTimeUtc=created,endedFileTimeUtc=p.HasExited?exit:0,exe=Playtest.Canon(exe),role=role};
        }
        static void EndIdentity(Process process,ProcessIdentity identity){if(identity==null)return;long created,ended,kernel,user;if(!GetProcessTimes(process.Handle,out created,out ended,out kernel,out user)||created!=identity.createdFileTimeUtc)throw new IOException("Cannot establish the exact ended proxy identity.");identity.endedFileTimeUtc=process.HasExited?ended:0;}
        internal static bool ParentLifetimeContains(ProcessIdentity parent,long childCreated){return parent.createdFileTimeUtc>0&&parent.createdFileTimeUtc<=childCreated&&(parent.endedFileTimeUtc==0||childCreated<=parent.endedFileTimeUtc);}
        internal static void VerifyParentAfterIdentity(ProcessIdentity child,IList<ProcessIdentity> proxies,Func<Dictionary<int,int>> readParents,Action refreshParent) {
            // The candidate's actual identity already exists before this snapshot.
            // A snapshot taken before GetProcessesByName can omit a just-born game.
            if(refreshParent!=null)refreshParent();var parents=readParents();int parent;
            if(!parents.TryGetValue(child.pid,out parent)||!proxies.Any(x=>x.pid==parent&&ParentLifetimeContains(x,child.createdFileTimeUtc)))throw new IOException("Cannot prove this private game belongs to the supervised proxy lifetime.");
        }
        static void Status(Action<string> status,string message){try{status(message);}catch{}}
        static bool PlausiblyPrivate(string root,SessionAudit audit) {
            bool unresolved=false;
            foreach(string name in new[]{"noita","noita_dev","noita_proxy"})foreach(var process in Process.GetProcessesByName(name))using(process) {
                try{if(process.HasExited)continue;string exe=Playtest.Canon(process.MainModule.FileName);if(Playtest.Within(exe,root))unresolved=true;else audit.Write("foreign-conflict-not-owned",new{pid=process.Id,exe=exe});}
                catch(InvalidOperationException){try{if(!process.HasExited)unresolved=true;}catch{unresolved=true;}}
                catch(System.ComponentModel.Win32Exception){unresolved=true;}
                catch(IOException){unresolved=true;}
            }
            return unresolved;
        }
        static Dictionary<int,int> Parents() {
            var result=new Dictionary<int,int>();using(var snapshot=CreateToolhelp32Snapshot(2,0)){if(snapshot.IsInvalid)throw new IOException("Cannot inspect private child ownership.");var entry=new ProcessEntry{size=(uint)Marshal.SizeOf(typeof(ProcessEntry))};if(!Process32FirstW(snapshot,ref entry))throw new IOException("Cannot enumerate private child ownership.");do{result[(int)entry.pid]=(int)entry.parent;}while(Process32NextW(snapshot,ref entry));}return result;
        }
        static List<ProcessIdentity> Games(string root,IList<ProcessIdentity> proxies,IDictionary<int,ProcessIdentity> seen,IDictionary<int,Process> handles,SessionAudit audit,Action refreshParent) {
            var result=new List<ProcessIdentity>();string expected=Playtest.Canon(Path.Combine(root,"game","noita.exe"));
            foreach(var p in Process.GetProcessesByName("noita"))using(p) {
                try {
                    if(p.HasExited)continue;string exe=Playtest.Canon(p.MainModule.FileName);
                    if(!String.Equals(exe,expected,StringComparison.OrdinalIgnoreCase))throw new IOException("A conflicting Noita session appeared; close it before opening another session.");
                    var identity=Identity(p,exe,"game");
                    if(!seen.ContainsKey(identity.pid)) {
                        VerifyParentAfterIdentity(identity,proxies,Parents,refreshParent);
                        var held=Process.GetProcessById(identity.pid);var exact=Identity(held,exe,"game");if(exact.createdFileTimeUtc!=identity.createdFileTimeUtc){held.Dispose();throw new IOException("Game identity changed during adoption.");}handles[identity.pid]=held;
                        seen[identity.pid]=identity;audit.Write("game-start",identity);
                    } else if(seen[identity.pid].createdFileTimeUtc!=identity.createdFileTimeUtc)throw new IOException("Private game PID was reused unexpectedly.");
                    result.Add(identity);
                } catch(InvalidOperationException){if(!p.HasExited)throw;}catch(System.ComponentModel.Win32Exception e){throw new IOException("Cannot inspect a plausible Noita identity; ownership is not assumed absent: "+e.Message);}
            }
            foreach(var old in seen.Values.ToArray())if(!result.Any(x=>x.pid==old.pid&&x.createdFileTimeUtc==old.createdFileTimeUtc)){int? code=null;Process held;if(handles.TryGetValue(old.pid,out held)){if(!held.HasExited)throw new IOException("A recorded private game is still alive; ownership is retained.");code=held.ExitCode;held.Dispose();handles.Remove(old.pid);}audit.Write("game-exit",new{identity=old,exitCode=code});seen.Remove(old.pid);}
            return result;
        }
        static Task Capture(StreamReader reader,string log,SessionAudit audit) {
            return Task.Factory.StartNew(()=>{var chars=new char[2048];int count;try{while((count=reader.Read(chars,0,chars.Length))>0)audit.Append(log,new String(chars,0,count));}catch(Exception e){audit.Write("output-capture-ended",new{log=log,error=e.Message});}},TaskCreationOptions.LongRunning);
        }
        internal static string[] ValidateRestart(string root,string session,ProcessIdentity parent,RestartRequest request) {
            if(request==null||request.schema!=1||request.session!=session||request.parentPid!=parent.pid||request.parentCreatedFileTimeUtc!=parent.createdFileTimeUtc||request.arguments==null||request.arguments.Length>16
                ||!String.Equals(Playtest.Canon(request.exe),parent.exe,StringComparison.OrdinalIgnoreCase)||!String.Equals(Playtest.Canon(request.directory),Playtest.Canon(Path.Combine(root,"runtime","bin")),StringComparison.OrdinalIgnoreCase))throw new IOException("Intentional proxy restart proof does not match this exact session identity.");
            // The private launcher supplies all game/settings/state paths itself.
            var result=new List<string>();
            for(int i=0;i<request.arguments.Length;i++) {
                string option=request.arguments[i];if(option!="--override-lobby-kind"&&option!="--auto-connect-to")throw new IOException("Unexpected private restart option.");
                if(++i>=request.arguments.Length)throw new IOException("Incomplete private restart option.");string value=request.arguments[i];
                if(option=="--override-lobby-kind"&&value!="Steam")throw new IOException("This playtest requires the owned Noita Steam application; a different Steam application is not selected.");
                if(option=="--auto-connect-to"&&!System.Text.RegularExpressions.Regex.IsMatch(value??"",@"^e0s[0-9a-f]+w$"))throw new IOException("Invalid matching Steam lobby restart target.");
                if(result.Contains(option))throw new IOException("Duplicate private restart option.");result.Add(option);result.Add(value);
            }
            return result.ToArray();
        }
        internal static int Run(string root,SessionAudit audit,Action<string> status) {
            var proxies=new List<ProcessIdentity>();var games=new Dictionary<int,ProcessIdentity>();var gameHandles=new Dictionary<int,Process>();var captures=new List<Task>();string[] forwarded=new string[0];int restarts=0;int result=0;
            while(true) {
                var start=Playtest.StartInfo(root);start.EnvironmentVariables["COOPPLUS_PLAYTEST_SESSION"]=audit.Id;start.EnvironmentVariables["COOPPLUS_PLAYTEST_SESSION_DIR"]=audit.DirectoryPath;
                if(forwarded.Length>0)start.Arguments+=" "+String.Join(" ",forwarded.Select(Playtest.Quote));
                start.RedirectStandardOutput=true;start.RedirectStandardError=true;
                using(var proxy=Process.Start(start)) {
                    ProcessIdentity identity=null;
                    try {
                    if(Fault!=null)Fault("after-process-start");
                    identity=Identity(proxy,start.FileName,"proxy");proxies.Add(identity);audit.Write("proxy-start",new{identity=identity,arguments=start.Arguments,cwd=start.WorkingDirectory,privateRoot=root});
                    if(Fault!=null)Fault("before-output-capture");
                    captures.Add(Capture(proxy.StandardOutput,"stdout.log",audit));captures.Add(Capture(proxy.StandardError,"stderr.log",audit));Status(status,"Starting the private proxy. Startup and session logs are being retained.");
                    var startup=Stopwatch.StartNew();bool visible=false,waitingNotice=false,diagnosticsNotice=false;
                    while(!proxy.WaitForExit(250)) {
                        EndIdentity(proxy,identity);
                        Games(root,proxies,games,gameHandles,audit,()=>EndIdentity(proxy,identity));proxy.Refresh();
                        if(!visible&&proxy.MainWindowHandle!=IntPtr.Zero&&IsWindowVisible(proxy.MainWindowHandle)){visible=true;audit.Write("proxy-window-visible",new{identity=identity,startupMs=startup.Elapsed.TotalMilliseconds});Status(status,"Co-op+ Playtest is open. Close the private game and proxy when finished.");}
                        if(!visible&&!waitingNotice&&startup.ElapsedMilliseconds>=15000){waitingNotice=true;audit.Write("proxy-window-wait",new{identity=identity,startupMs=startup.Elapsed.TotalMilliseconds});Status(status,"The private proxy is running but has not shown a window yet. Logs are retained; it will not be killed or automatically restarted.");}
                        if(!diagnosticsNotice&&audit.DiagnosticWriteError!=null){diagnosticsNotice=true;Status(status,"Session diagnostics could not be retained. Close the private game and proxy normally; no automatic retry or reset is attempted. "+audit.DiagnosticWriteError);}
                    }
                    EndIdentity(proxy,identity);
                    int code=proxy.ExitCode;audit.Write("proxy-exit",new{identity=identity,exitCode=code});
                    if(!visible)audit.Write("proxy-exit-before-window-observed",new{identity=identity,exitCode=code});
                    var liveGames=Games(root,proxies,games,gameHandles,audit,()=>EndIdentity(proxy,identity));
                    if(code==42) {
                        if(liveGames.Count!=0)throw new IOException("A requested proxy restart was refused while its private game remains open. Close that game normally; no process was stopped.");
                        if(++restarts>4)throw new IOException("The bounded intentional-restart limit was reached. Inspect the retained session log; no automatic restart loop was started.");
                        string requestPath=Playtest.Child(audit.DirectoryPath,"restart-"+identity.pid+"-"+identity.createdFileTimeUtc+".json");
                        if(!File.Exists(requestPath)||new FileInfo(requestPath).Length>65536)throw new IOException("The proxy requested restart without complete retained handoff proof.");
                        forwarded=ValidateRestart(root,audit.Id,identity,Json.Deserialize<RestartRequest>(File.ReadAllText(requestPath)));
                        File.Move(requestPath,requestPath+".consumed.json");audit.Write("intentional-restart-accepted",new{from=identity,number=restarts});continue;
                    }
                    if(code==0&&!visible)throw new IOException("The private proxy ended before a window was observed. Exit0 alone does not prove successful startup. Inspect the retained startup log; nothing was reset or automatically restarted.");
                    result=code;
                    if(code!=0)Status(status,"The private proxy ended with exit code "+code+". Its logs are retained. Close any remaining private game normally.");
                    while(Games(root,proxies,games,gameHandles,audit,()=>EndIdentity(proxy,identity)).Count!=0)Thread.Sleep(250);
                    break;
                    } catch(Exception error) {
                        audit.Write("supervision-failure",new{error=error.ToString(),identity=identity});Status(status,"Session supervision stopped safely. Close the private game and proxy normally; logs are retained. "+error.Message);
                        // Keep the ownership gate until our exact process handles
                        // end. Never kill, adopt an unknown process, or respawn.
                        while(!proxy.WaitForExit(250)) { try{Games(root,proxies,games,gameHandles,audit,()=>EndIdentity(proxy,identity));}catch{} }
                        try{EndIdentity(proxy,identity);}catch{}audit.Write("proxy-exit-after-supervision-failure",new{identity=identity,exitCode=proxy.ExitCode});
                        while(gameHandles.Values.Any(p=>!p.HasExited))Thread.Sleep(250);
                        var unresolvedWait=Stopwatch.StartNew();while(PlausiblyPrivate(root,audit)&&unresolvedWait.ElapsedMilliseconds<5000)Thread.Sleep(500);
                        if(PlausiblyPrivate(root,audit))audit.Write("unresolved-private-identities-retained",new{waitBoundMs=5000,message="Some plausible private identities could not be proved ended. They were not adopted or stopped. A new launch still refuses conflicting names."});
                        foreach(var item in gameHandles){ProcessIdentity known;if(games.TryGetValue(item.Key,out known))audit.Write("game-exit-after-supervision-failure",new{identity=known,exitCode=item.Value.ExitCode});}
                        foreach(var held in gameHandles.Values)held.Dispose();gameHandles.Clear();
                        throw;
                    }
                }
            }
            Task.WaitAll(captures.ToArray(),2000);audit.Write("session-processes-ended",new{proxyExitCode=result,restarts=restarts,exitZeroIsNotGameplayAcceptance=true});
            if(audit.DiagnosticWriteError!=null)throw new IOException("Session diagnostic writes failed; successful completion is not claimed. "+audit.DiagnosticWriteError);
            if(result!=0)throw new IOException("The private proxy ended with exit code "+result+". Open the retained logs at "+audit.DirectoryPath+". It was not automatically restarted.");
            return result;
        }
    }
}
