// Test-only child protocol fixture; never part of a player package.
using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

public static class SessionDummyProxy {
    sealed class QuietWindow : Form { protected override bool ShowWithoutActivation {get{return true;}} }
    static int ShowThenClose(){using(var form=new QuietWindow{Text="Session protocol fixture",Width=160,Height=80})using(var timer=new System.Windows.Forms.Timer{Interval=700}){timer.Tick+=(s,e)=>form.Close();form.Shown+=(s,e)=>timer.Start();Application.Run(form);}return 0;}
    [STAThread]
    public static int Main(string[] args) {
        Thread.Sleep(400);
        string mode=Environment.GetEnvironmentVariable("COOPPLUS_SESSION_FIXTURE");
        if(mode=="crash")return 7;
        if(mode=="early-zero")return 0;
        if(mode=="unproved42")return 42;
        if(mode=="restart-once"||mode=="restart-always") {
            string directory=Environment.GetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION_DIR");
            if(mode=="restart-always"||Directory.GetFiles(directory,"*.consumed.json").Length==0) {
                using(var self=Process.GetCurrentProcess()) {
                    long creationFileTimeUtc=self.StartTime.ToUniversalTime().ToFileTimeUtc();
                    string request=new JavaScriptSerializer().Serialize(new{schema=1,session=Environment.GetEnvironmentVariable("COOPPLUS_PLAYTEST_SESSION"),parentPid=self.Id,parentCreatedFileTimeUtc=creationFileTimeUtc,exe=self.MainModule.FileName,directory=Environment.CurrentDirectory,arguments=new string[0]});
                    File.WriteAllText(Path.Combine(directory,"restart-"+self.Id+"-"+creationFileTimeUtc+".json"),request);
                }
                return 42;
            }
        }
        return ShowThenClose();
    }
}
