using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Web.Script.Serialization;
using CoopPlusPlaytest;

public static class PlaytestSessionTests {
    static int count;
    static void Check(bool okay,string name){if(!okay)throw new Exception(name);Console.WriteLine("PASS "+name);count++;}
    static void Refuses(Action action,string name){try{action();}catch(Exception){Check(true,name);return;}throw new Exception("Not refused: "+name);}
    public static int Main(string[] args) {
        try {
            if(args.Length!=1||Directory.Exists(args[0]))throw new Exception("Pass one new safe scratch directory.");
            string scratch=Playtest.Canon(args[0]);Directory.CreateDirectory(scratch);string root=Path.Combine(scratch,"private");string bin=Path.Combine(root,"runtime","bin");Directory.CreateDirectory(bin);
            var parent=new ProcessIdentity{pid=1234,createdFileTimeUtc=134000000000000000,exe=Path.Combine(bin,"noita_proxy.exe"),role="proxy"};string token=Guid.NewGuid().ToString("N");
            Func<RestartRequest> proof=()=>new RestartRequest{schema=1,session=token,parentPid=parent.pid,parentCreatedFileTimeUtc=parent.createdFileTimeUtc,exe=parent.exe,directory=bin,arguments=new[]{"--override-lobby-kind","Steam","--auto-connect-to","e0s1234w"}};
            Check(SessionController.ValidateRestart(root,token,parent,proof()).Length==4,"exact restart identity accepted");
            var wrong=proof();wrong.parentCreatedFileTimeUtc++;Refuses(()=>SessionController.ValidateRestart(root,token,parent,wrong),"reused parent PID rejected");
            wrong=proof();wrong.session=Guid.NewGuid().ToString("N");Refuses(()=>SessionController.ValidateRestart(root,token,parent,wrong),"another session token rejected");
            wrong=proof();wrong.exe=Path.Combine(scratch,"outside.exe");Refuses(()=>SessionController.ValidateRestart(root,token,parent,wrong),"outside executable rejected");
            wrong=proof();wrong.arguments=new[]{"--exe-path",Path.Combine(scratch,"normal","noita.exe")};Refuses(()=>SessionController.ValidateRestart(root,token,parent,wrong),"restart cannot override private executable");
            wrong=proof();wrong.arguments=new[]{"--override-lobby-kind","Gog"};Refuses(()=>SessionController.ValidateRestart(root,token,parent,wrong),"other Steam application rejected");
            wrong=proof();wrong.arguments=new[]{"--auto-connect-to","127.0.0.1:5123"};Refuses(()=>SessionController.ValidateRestart(root,token,parent,wrong),"IP restart target rejected");
            parent.endedFileTimeUtc=parent.createdFileTimeUtc+100;
            Check(SessionController.ParentLifetimeContains(parent,parent.createdFileTimeUtc+50)&&!SessionController.ParentLifetimeContains(parent,parent.endedFileTimeUtc+1),"reused historical parent PID cannot adopt a later child");
            string protectedFile=Path.Combine(scratch,"save-sentinel.txt");File.WriteAllText(protectedFile,"preserved");
            using(var audit=new SessionAudit(scratch)) {
                audit.Append("stdout.log",new string('x',2*1024*1024));audit.Append("stdout.log","later output");
                Check(new FileInfo(Path.Combine(audit.DirectoryPath,"stdout.log")).Length==1024*1024,"captured output is bounded");
                audit.Write("test-identity",parent);audit.Finish("focused-fixture");
                Check(File.ReadAllText(Path.Combine(audit.DirectoryPath,"events.jsonl")).Contains("parent")||File.ReadAllText(Path.Combine(audit.DirectoryPath,"events.jsonl")).Contains("test-identity"),"structured lifecycle record retained");
            }
            Check(File.ReadAllText(protectedFile)=="preserved","session diagnostics never alter save sentinel");
            string retention=Path.Combine(scratch,"Retention fixture"),logRoot=Path.Combine(retention,"playtest-logs");Directory.CreateDirectory(logRoot);
            var json=new JavaScriptSerializer();for(int i=0;i<15;i++){string id=Guid.NewGuid().ToString("N"),directory=Path.Combine(logRoot,id);Directory.CreateDirectory(directory);File.WriteAllText(Path.Combine(directory,"session.json"),json.Serialize(new SessionStamp{session=id,launcherPid=int.MaxValue,launcherCreatedFileTimeUtc=1,startedUtc=DateTime.UtcNow.AddHours(-i-1).ToString("o"),finishedUtc=DateTime.UtcNow.AddHours(-i-1).ToString("o"),result="fixture-completed"}));File.WriteAllText(Path.Combine(directory,"proxy-123-456.jsonl"),"{}");File.WriteAllText(Path.Combine(directory,"proxy-123-456.failure.txt"),"retained failure fixture");File.WriteAllText(Path.Combine(directory,"prior-123-ew_log.txt"),"retained prior fixture");Directory.SetCreationTimeUtc(directory,DateTime.UtcNow.AddHours(-i-1));}
            using(var audit=new SessionAudit(retention)){Check(Directory.GetDirectories(logRoot).Length==12,"newest12 retention includes exact Rust diagnostic names");audit.Finish("retention-fixture");}
            string unknown=Path.Combine(logRoot,"unrecognized-folder");Directory.CreateDirectory(unknown);File.WriteAllText(Path.Combine(unknown,"do-not-delete.txt"),"unknown retained");
            using(var audit=new SessionAudit(retention)){Check(File.ReadAllText(Path.Combine(unknown,"do-not-delete.txt"))=="unknown retained","unknown diagnostic siblings are never deleted");audit.Finish("unknown-fixture");}
            Console.WriteLine("RESULT "+count+" focused cases passed");return 0;
        }catch(Exception e){Console.Error.WriteLine(e);return 1;}
    }
}
