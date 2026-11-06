// Stable protocol-1 Windows entry point. Keep this binary small and independent of releases.
using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;

namespace CoopPlus {
    internal static class Bootstrap {
        internal static string Quote(string value) {
            var b = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { b.Append('\\', slashes * 2 + 1); b.Append(c); }
                else { b.Append('\\', slashes); b.Append(c); }
                slashes = 0;
            }
            b.Append('\\', slashes * 2); return b.Append('"').ToString();
        }
        private static int Main(string[] args) {
            string temp = null;
            try {
                Console.InputEncoding = new UTF8Encoding(false);
                Console.OutputEncoding = new UTF8Encoding(false);
                string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
                Guard(root);
                string key;
                using (var h = SHA256.Create()) key = BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "");
                using (var gate = new Mutex(false, "Local\\CoopPlus-Bootstrap-" + key)) {
                    bool locked;
                    try { locked = gate.WaitOne(0); } catch (AbandonedMutexException) { locked = true; }
                    if (!locked) { Console.Error.WriteLine("Co-op+ is already open for this installation."); return 3; }
                    string lockPath = Path.Combine(root, ".coopplus-launch.lock"); Guard(lockPath);
                    try { using (var fileGate = new FileStream(lockPath, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None)) {
                        for (int attempt = 0; attempt < 3; attempt++) {
                            string updater = Path.Combine(root, "current", "updater", "CoopUpdater.exe");
                            string recovery = Path.Combine(root, "recovery", "CoopUpdater.exe");
                            Guard(recovery);
                            Guard(Path.Combine(root, "install-journal.json"));
                            if (File.Exists(Path.Combine(root, "install-journal.json"))) {
                                if (!File.Exists(recovery)) throw new IOException("Interrupted installation requires recovery, but the recovery updater is missing. Keep rollback folders for manual review.");
                                updater = recovery;
                            } else {
                                Guard(updater);
                                Directory.CreateDirectory(Path.GetDirectoryName(recovery));
                                string pending = recovery + ".pending-" + Guid.NewGuid().ToString("N");
                                File.Copy(updater, pending);
                                if (File.Exists(recovery)) File.Replace(pending, recovery, null); else File.Move(pending, recovery);
                            }
                            Guard(updater);
                            temp = Path.Combine(Path.GetTempPath(), "CoopPlus-" + Guid.NewGuid().ToString("N"));
                            Directory.CreateDirectory(temp); Guard(temp);
                            string worker = Path.Combine(temp, "CoopUpdater.exe"); File.Copy(updater, worker);
                            var command = new StringBuilder("--internal-root ").Append(Quote(root)).Append(" --");
                            foreach (string a in args) command.Append(' ').Append(Quote(a));
                            var start = new ProcessStartInfo(worker, command.ToString()) { UseShellExecute = false, WorkingDirectory = root };
                            int result; using (var process = Process.Start(start)) { process.WaitForExit(); result = process.ExitCode; }
                            File.Delete(worker); Directory.Delete(temp); temp = null;
                            if (result != 42) return Finish(result);
                        }
                        throw new InvalidOperationException("Updater restart limit reached. Reopen Co-op+; no proxy was launched.");
                    } } finally { gate.ReleaseMutex(); }
                }
            } catch (Exception e) { Console.Error.WriteLine("Co-op+: " + e.Message); return Finish(1); }
            finally { if (temp != null) { try { File.Delete(Path.Combine(temp, "CoopUpdater.exe")); Directory.Delete(temp); } catch { } } }
        }
        private static int Finish(int result) {
            if (result != 0 && !Console.IsInputRedirected) { Console.WriteLine("Press Enter to close."); Console.ReadLine(); }
            return result;
        }
        private static void Guard(string path) {
            for (string p = Path.GetFullPath(path); !String.IsNullOrEmpty(p); p = Path.GetDirectoryName(p)) {
                if ((File.Exists(p) || Directory.Exists(p)) && (File.GetAttributes(p) & FileAttributes.ReparsePoint) != 0)
                    throw new IOException("Links/reparse points are unsupported: " + p);
                if (Directory.Exists(Path.Combine(p, ".git")) || File.Exists(Path.Combine(p, ".git")))
                    throw new IOException("Extract Co-op+ outside a development checkout before running it.");
            }
        }
    }
}
