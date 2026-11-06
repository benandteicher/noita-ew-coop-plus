// Test executable only: injectable release provider and TLS trust never enter the shipped binaries.
using System;
using System.IO;
using System.Net;
using CoopPlus;
class IntegrationHarness {
    static int Main(string[] a) {
        if (a.Length == 2 && a[0] == "--internal-handoff") return ProxyHandoff.Worker(a[1]);
        try {
            ServicePointManager.ServerCertificateValidationCallback = (s,c,ch,e) => true;
            if (a[0] == "feed") {
                var m = Program.ReadRelease(a[1],a[2]);
                Engine.StageDownload(m.package.url,a[3],m.package.size,m.package.sha256); return 0;
            }
            if (a[0] == "run-feed") Program.ReleaseProvider = () => Program.ReadRelease(a[3],a[4]);
            else Program.ReleaseProvider = () => { throw new WebException("injected offline check"); };
            return Program.Run(a[1],a[2],new string[0],a[a.Length-1] == "check");
        } catch(Exception e) { Console.Error.WriteLine(e.ToString()); return 1; }
    }
}
