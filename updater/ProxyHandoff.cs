using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;

namespace CoopPlus {
    // The official GUI proxy explicitly AttachConsole(parent). A short-lived
    // detached parent prevents it keeping the updater's console alive.
    internal static class ProxyHandoff {
        private sealed class Request { public string Exe; public string Directory; public string[] Arguments; }
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
        internal static int Launch(string executable, string directory, string[] args) {
            string temp = Path.Combine(Path.GetTempPath(), "CoopPlus-Handoff-" + Guid.NewGuid().ToString("N"));
            Engine.AssertNoReparse(temp); Directory.CreateDirectory(temp);
            string request = Path.Combine(temp, "request.json"), reply = Path.Combine(temp, "reply.json");
            IntPtr worker = IntPtr.Zero;
            try {
                File.WriteAllText(request, Json.Serialize(new Request { Exe = executable, Directory = directory, Arguments = args }), new UTF8Encoding(false));
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
                Engine.AssertNoReparse(request.Exe); Engine.AssertNoReparse(request.Directory);
                var start = new ProcessStartInfo(request.Exe, String.Join(" ", Array.ConvertAll(request.Arguments, Program.Quote))) { UseShellExecute = false, WorkingDirectory = request.Directory };
                start.EnvironmentVariables["NP_SKIP_MOD_CHECK"] = "1";
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
