using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Windows.Forms;
using CoopPlus;
class UsabilityHarness {
    [STAThread]
    static int Main(string[] a) {
        if (a.Length == 2 && a[0] == "--internal-handoff") return ProxyHandoff.Worker(a[1]);
        try {
            if (a[0] == "setup-native") {
                Setup.Discover = (root,settings) => NoitaDetection.Discover(root,settings,new string[0]);
                try { Setup.Load(a[1], false, a.Length > 2 ? new [] { "--settings-path", a[2], "--save-state-path", Path.Combine(Path.GetDirectoryName(a[2]), "save_state") } : new string[0]); } catch (OperationCanceledException) { Console.WriteLine("Native setup cancelled; no config/installation changes."); }
                return 0;
            }
            if (a[0] == "setup-ui") {
                var c = new UserConfig { settingsPath = Path.Combine(a[1],"portable","proxy.ron"), saveStatePath = Path.Combine(a[1],"portable","save_state") };
                Application.EnableVisualStyles();
                using (var f = new SetupDialog(new SetupRequest { Root = Path.Combine(a[1],"launcher"), Config = c, Candidates = new List<InstallationCandidate> { new InstallationCandidate { GameDirectory = Path.Combine(a[1],"Noita Ω game"), Source = "Remembered by proxy settings" } } })) {
                    Console.WriteLine(f.ShowDialog()); return 0;
                }
            }
            if (a[0] == "install") { Engine.Install(a[1],a[2],a[3],Engine.ParseManifest(File.ReadAllText(a[4]))); return 0; }
            Program.ReleaseProvider = () => { throw new WebException("offline fixture; no public candidate feed"); };
            return Program.Run(a[1],a[2],new string[0],true);
        } catch (Exception e) { Console.Error.WriteLine(e); return 1; }
    }
}
