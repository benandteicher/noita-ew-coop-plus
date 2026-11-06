using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
class WindowedDummyProxy {
    [DllImport("kernel32.dll")] static extern bool AttachConsole(uint pid);
    [DllImport("kernel32.dll")] static extern IntPtr GetConsoleWindow();
    [STAThread]
    static int Main(string[] args) {
        // Mirrors the official GUI executable's explicit parent-console attach.
        AttachConsole(0xffffffff);
        string record = Environment.GetEnvironmentVariable("COOPPLUS_RECORD");
        if (!String.IsNullOrEmpty(record)) File.WriteAllText(record, new JavaScriptSerializer().Serialize(new { pid=Process.GetCurrentProcess().Id, args=args, cwd=Environment.CurrentDirectory, skip=Environment.GetEnvironmentVariable("NP_SKIP_MOD_CHECK"), consoleAttached=GetConsoleWindow()!=IntPtr.Zero }));
        if (Environment.GetEnvironmentVariable("COOPPLUS_FAIL") == "1") return 7;
        int delay; if (!Int32.TryParse(Environment.GetEnvironmentVariable("COOPPLUS_SLEEP"),out delay)) delay=3500;
        if (Array.Exists(args,a=>a=="--host"||a=="--lobby"||a=="--help")) { Thread.Sleep(delay); return 0; }
        Application.EnableVisualStyles();
        using (var f = new Form { Text="Co-op+ disposable dummy proxy", Width=420, Height=155, StartPosition=FormStartPosition.CenterScreen }) {
            f.Controls.Add(new Label { Text="Windowed dummy proxy — no Noita/game/native proxy.\nThe updater has handed off and can close.", Dock=DockStyle.Fill, Padding=new Padding(14) });
            var timer=new System.Windows.Forms.Timer { Interval=delay }; timer.Tick+=(s,e)=>f.Close(); timer.Start(); Application.Run(f); timer.Dispose();
        }
        return 0;
    }
}
