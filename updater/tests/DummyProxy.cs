using System;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;
class DummyProxy {
    static void Main(string[] args) {
        string record = Environment.GetEnvironmentVariable("COOPPLUS_RECORD");
        if (!String.IsNullOrEmpty(record)) File.WriteAllText(record,new JavaScriptSerializer().Serialize(new { args=args,cwd=Environment.CurrentDirectory,skip=Environment.GetEnvironmentVariable("NP_SKIP_MOD_CHECK") }));
        int delay; if (Int32.TryParse(Environment.GetEnvironmentVariable("COOPPLUS_SLEEP"),out delay)) Thread.Sleep(delay);
    }
}
