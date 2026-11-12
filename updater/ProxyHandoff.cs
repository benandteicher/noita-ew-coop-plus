using System;
using System.ComponentModel;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;

namespace CoopPlus {
    // The official GUI proxy explicitly AttachConsole(parent). A short-lived
    // detached parent prevents it keeping the updater's console alive.
    internal static class ProxyHandoff {
        private sealed class Request { public string NormalRoot; public string Exe; public string Directory; public string[] Arguments; }
        private sealed class Reply { public int Pid; public string Error; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct StartupInfo { public int cb; public string reserved, desktop, title; public int x,y,xSize,ySize,xChars,yChars,fill,flags; public short show,reserved2; public IntPtr reservedPtr,input,output,error; }
        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInfo { public IntPtr process, thread; public int pid, tid; }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CreateProcess(string application, StringBuilder command, IntPtr processAttributes, IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo process);
        [DllImport("kernel32.dll")] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool GetExitCodeProcess(IntPtr handle, out uint code);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool TerminateProcess(IntPtr handle, uint code);
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        private static string LocalAbsolute(string value) {
            if(String.IsNullOrWhiteSpace(value) || value.Length<3 || !Char.IsLetter(value[0]) || value[1]!=':' || (value[2]!='\\' && value[2]!='/'))
                throw new IOException("Normal Co-op+ launch requires explicit absolute local paths; open CoopPlus.exe.");
            string path=Path.GetFullPath(value).TrimEnd(Path.DirectorySeparatorChar,Path.AltDirectorySeparatorChar);Program.Guard(path);return path;
        }
        internal static ProcessStartInfo NormalStart(string normalRoot,string executable,string directory,string[] args) {
            string root=LocalAbsolute(normalRoot),proxy=Path.Combine(root,"current","proxy");
            if(!String.Equals(LocalAbsolute(executable),Path.Combine(proxy,"noita_proxy.exe"),StringComparison.OrdinalIgnoreCase) || !String.Equals(LocalAbsolute(directory),proxy,StringComparison.OrdinalIgnoreCase))
                throw new IOException("Normal proxy context does not match this managed Co-op+ installation.");
            if(!File.Exists(Path.Combine(root,"CoopPlus.exe")) || !File.Exists(Path.Combine(root,"current","installed.json")) || !File.Exists(Path.Combine(root,"coopplus.json")))
                throw new IOException("Normal Co-op+ context is incomplete; open the installed CoopPlus.exe.");
            var config=Setup.Stored(root);
            if(config==null || String.IsNullOrWhiteSpace(config.gameDir))throw new IOException("Normal Co-op+ game selection is missing.");
            string game=LocalAbsolute(config.gameDir);Program.ValidateForwarded(root,game,proxy,args ?? new string[0]);
            var all=new List<string>(args ?? new string[0]);
            foreach(string option in new[]{"--settings-path","--save-state-path","--exe-path"}) {
                int found=0;
                for(int i=0;i<all.Count;i++) {
                    bool equals=all[i].StartsWith(option+"=",StringComparison.Ordinal);string value=null;
                    if(all[i]==option){if(++i>=all.Count)throw new IOException("Missing explicit "+option);value=all[i];}
                    else if(equals)value=all[i].Substring(option.Length+1);
                    if(value==null)continue;
                    if(++found>1 || String.IsNullOrWhiteSpace(value))throw new IOException("Ambiguous explicit "+option);
                    string absolute=LocalAbsolute(Path.IsPathRooted(value)?value:Path.Combine(proxy,value));
                    if(option=="--exe-path") {
                        if(!String.Equals(absolute,Path.Combine(game,"noita.exe"),StringComparison.OrdinalIgnoreCase) || !File.Exists(absolute))throw new IOException("Normal proxy game path differs from the managed selection.");
                    } else Program.ValidateMutable(root,game,absolute);
                    all[i]=equals?option+"="+absolute:absolute;
                }
                if(found!=1)throw new IOException("Normal proxy context requires explicit "+option);
            }
            var start=new ProcessStartInfo(executable,String.Join(" ",all.ConvertAll(Program.Quote))) {UseShellExecute=false,WorkingDirectory=proxy};
            // The proof comes only from this gated dispatch/request, never an
            // inherited normal/private context from the launcher's environment.
            start.EnvironmentVariables.Remove("COOPPLUS_PRIVATE_ROOT");
            start.EnvironmentVariables.Remove("COOPPLUS_PLAYTEST_SESSION");
            start.EnvironmentVariables.Remove("COOPPLUS_PLAYTEST_SESSION_DIR");
            start.EnvironmentVariables["COOPPLUS_NORMAL_ROOT"]=root;
            start.EnvironmentVariables["NP_SKIP_MOD_CHECK"]="1";
            return start;
        }
        internal static int Launch(string normalRoot,string executable, string directory, string[] args) {
            NormalStart(normalRoot,executable,directory,args);
            string temp = Path.Combine(Path.GetTempPath(), "CoopPlus-Handoff-" + Guid.NewGuid().ToString("N"));
            Engine.AssertNoReparse(temp); Directory.CreateDirectory(temp);
            string request = Path.Combine(temp, "request.json"), reply = Path.Combine(temp, "reply.json");
            IntPtr worker = IntPtr.Zero;
            try {
                File.WriteAllText(request, Json.Serialize(new Request { NormalRoot=normalRoot, Exe = executable, Directory = directory, Arguments = args }), new UTF8Encoding(false));
                string self = Process.GetCurrentProcess().MainModule.FileName;
                var startup = new StartupInfo { cb = Marshal.SizeOf(typeof(StartupInfo)) }; ProcessInfo info;
                if (!CreateProcess(self, new StringBuilder(Program.Quote(self) + " --internal-handoff " + Program.Quote(request)), IntPtr.Zero, IntPtr.Zero, false, 0x00000008, IntPtr.Zero, directory, ref startup, out info))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Could not start the Co-op+ proxy handoff.");
                CloseHandle(info.thread);
                worker = info.process;
                uint wait = WaitForSingleObject(worker, 5000);
                if (wait != 0) {
                    // This is our transient handoff worker, never the proxy/game.
                    // Stop it before releasing installation locks so it cannot
                    // launch late after another updater starts a transaction.
                    if (!TerminateProcess(worker, 1) || WaitForSingleObject(worker, 5000) != 0)
                        throw new IOException("Handoff worker could not be confirmed stopped. Keep Co-op+ closed until this worker exits; no proxy/game was stopped.");
                    throw new IOException("Proxy handoff timed out. The handoff worker was stopped; no proxy/game was stopped. Check whether the proxy opened before retrying.");
                }
                if (!File.Exists(reply)) throw new IOException("Proxy handoff failed before it could report startup status.");
                var status = Json.Deserialize<Reply>(File.ReadAllText(reply));
                uint exitCode;
                if (!GetExitCodeProcess(worker, out exitCode)) throw new Win32Exception(Marshal.GetLastWin32Error());
                if (exitCode != 0 || status == null || !String.IsNullOrEmpty(status.Error)) throw new IOException(status == null ? "Invalid proxy startup reply." : status.Error);
                try { using (var proxy = Process.GetProcessById(status.Pid)) { if (proxy.HasExited) throw new IOException("The official proxy closed during startup. Check its ew_log.txt."); } }
                catch (ArgumentException) { throw new IOException("The official proxy closed during startup. Check its ew_log.txt."); }
                Console.WriteLine("Official proxy opened. Co-op+ launcher is closing."); return 0;
            } finally {
                bool finished = worker == IntPtr.Zero || WaitForSingleObject(worker, 0) == 0;
                if (worker != IntPtr.Zero) CloseHandle(worker);
                if (finished) { if (File.Exists(request)) File.Delete(request); if (File.Exists(reply)) File.Delete(reply); Directory.Delete(temp); }
            }
        }
        internal static int Worker(string requestPath) {
            string reply = Path.Combine(Path.GetDirectoryName(requestPath), "reply.json");
            try {
                Engine.AssertNoReparse(requestPath);
                var request = Json.Deserialize<Request>(File.ReadAllText(requestPath));
                if(request==null || String.IsNullOrWhiteSpace(request.NormalRoot))throw new IOException("Normal proxy handoff context is missing; open CoopPlus.exe.");
                Engine.AssertNoReparse(request.Exe); Engine.AssertNoReparse(request.Directory);
                var start = NormalStart(request.NormalRoot,request.Exe,request.Directory,request.Arguments);
                using (var proxy = Process.Start(start)) {
                    if (proxy.WaitForExit(900)) throw new IOException("The official proxy closed during startup (exit " + proxy.ExitCode + "). Check its ew_log.txt and launch arguments.");
                    File.WriteAllText(reply, Json.Serialize(new Reply { Pid = proxy.Id }), new UTF8Encoding(false));
                }
                return 0;
            } catch (Exception e) {
                try { File.WriteAllText(reply, Json.Serialize(new Reply { Error = e.Message }), new UTF8Encoding(false)); } catch { }
                return 1;
            }
        }
    }
}
