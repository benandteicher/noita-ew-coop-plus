using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace CoopPlusPlaytest {
    internal static class LauncherRefresh {
        const string Title = "Co-op+ Staging Launcher Refresh";
        const string Launcher = "Co-op+ Staging.exe";
        const string Backup = "Co-op+ Staging.before-refresh.exe.bak";

        sealed class Bundle {
            public readonly string Stock, Refreshed, Manifest, Resource;

            public Bundle(string stock, string refreshed, string manifest, string resource) {
                Stock = stock;
                Refreshed = refreshed;
                Manifest = manifest;
                Resource = resource;
            }
        }

        static readonly Bundle[] Bundles = {
            new Bundle("3440d0a2aa0ae4d22526137629d6a01eece722f318a718c6aa8e083813abedff",
                StagingRefreshBuild.Build23, "1dd8d847447e879551d923c26fee41f271f8cf201796da24747da59edbffeba5", "CoopPlus.Refresh23"),
            new Bundle("db0f0215c8d5db1bec6c044f9ceec808b951b56fac09cc7d5039c94f322c6412",
                StagingRefreshBuild.Build24, "eb3dad8834d1d945311259b92a17b0f21c56add5a05287a120a0a96b44c6dcdc", "CoopPlus.Refresh24")
        };

        static void WithGate(string name, Action action) {
            using (var gate = new Mutex(false, name)) {
                bool held;
                try { held = gate.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                if (!held) throw new IOException("Close the Staging launcher, Noita and the proxy before refreshing.");
                try { action(); }
                finally { gate.ReleaseMutex(); }
            }
        }

        static byte[] Replacement(Bundle bundle) {
            using (var source = typeof(LauncherRefresh).Assembly.GetManifestResourceStream(bundle.Resource))
            using (var memory = new MemoryStream()) {
                if (source == null) throw new IOException("The replacement launcher is missing.");
                source.CopyTo(memory);
                byte[] bytes = memory.ToArray();
                if (Playtest.HashBytes(bytes) != bundle.Refreshed)
                    throw new IOException("The replacement launcher failed verification.");
                return bytes;
            }
        }

        static void Refresh(string package) {
            Playtest.CheckStopped();
            string launcher = Playtest.Child(package, Launcher);
            if (!File.Exists(launcher))
                throw new IOException("Put this refresh program beside Co-op+ Staging.exe and open it there.");

            string current = Playtest.Hash(launcher);
            var bundle = Bundles.SingleOrDefault(b => b.Stock == current || b.Refreshed == current);
            if (bundle == null)
                throw new IOException("This launcher does not match a stock Staging build 23 or 24 launcher. No files were changed.");
            string manifest = Playtest.Child(package, "staging-payload/manifest.json");
            if (!File.Exists(manifest) || Playtest.Hash(manifest) != bundle.Manifest)
                throw new IOException("The launcher and its original bundled files disagree. Keep this Staging folder for review.");
            if (current == bundle.Refreshed) {
                MessageBox.Show("This launcher is already refreshed. Open Co-op+ Staging.exe and select Check for update.",
                    Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            string backup = Playtest.Child(package, Backup);
            if (File.Exists(backup) || Directory.Exists(backup))
                throw new IOException("A refresh backup already exists. Keep it and the current launcher for review.");
            if (MessageBox.Show("Refresh the Staging launcher in this folder? A copy of the original launcher will be kept.",
                Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2) != DialogResult.Yes)
                return;

            byte[] replacement = Replacement(bundle);
            string temporary = Playtest.Child(package, "staging-launcher-refresh-" + Guid.NewGuid().ToString("N") + ".tmp");
            bool created = false;
            try {
                using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                    created = true;
                    output.Write(replacement, 0, replacement.Length);
                    output.Flush(true);
                }
                if (Playtest.Hash(temporary) != bundle.Refreshed)
                    throw new IOException("The prepared launcher failed verification.");
                Playtest.CheckStopped();
                if (Playtest.Hash(launcher) != current || Playtest.Hash(manifest) != bundle.Manifest ||
                    File.Exists(Playtest.Canon(backup)) || Directory.Exists(backup))
                    throw new IOException("The Staging folder changed during preparation.");
                File.Replace(temporary, launcher, backup);
                if (Playtest.Hash(launcher) != bundle.Refreshed || Playtest.Hash(backup) != current)
                    throw new IOException("Launcher verification failed after replacement. Keep both launcher files for review.");
            } finally {
                if (created && File.Exists(Playtest.Canon(temporary))) File.Delete(temporary);
            }
            MessageBox.Show("Launcher refreshed. Open Co-op+ Staging.exe and select Check for update.",
                Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
        }

        [STAThread]
        static int Main(string[] args) {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try {
                if (args.Length != 0) throw new IOException("Open the refresh program directly from the existing Staging folder.");
                string package = Playtest.Canon(Path.GetDirectoryName(Application.ExecutablePath));
                Playtest.ValidatePackage(package);
                string instance = Playtest.HashBytes(Encoding.UTF8.GetBytes(package.ToUpperInvariant()));
                WithGate("Local\\CoopPlus-Staging-" + instance,
                    () => WithGate("Local\\CoopPlus-Private-Playtest", () => Refresh(package)));
                return 0;
            } catch (Exception error) {
                MessageBox.Show(error.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
    }
}
