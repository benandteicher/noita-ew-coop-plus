// Coordinator runs real tiny child lifetimes. No Noita/Steam/gameplay is used.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using CoopPlusPlaytest;

public static class PlaytestSessionProcessTests {
    static int cases;
    static void Check(bool value,string name){if(!value)throw new Exception(name);Console.WriteLine("PASS "+name);cases++;}
    static string Case(string scratch,string dummy,string name){string package=Path.Combine(scratch,name),bin=Path.Combine(package,"private","runtime","bin");Directory.CreateDirectory(bin);File.Copy(dummy,Path.Combine(bin,"noita_proxy.exe"),false);return package;}
    static string Run(string package,string mode,bool refusal) {
        Environment.SetEnvironmentVariable("COOPPLUS_SESSION_FIXTURE",mode);
        using(var audit=new SessionAudit(package)) {
            bool failed=false;try{SessionController.Run(Path.Combine(package,"private"),audit,delegate{});}catch(IOException){failed=true;}
            Check(failed==refusal,"expected bounded result for "+mode);audit.Finish(failed?"expected-fixture-refusal":"fixture-exit-zero");return File.ReadAllText(Path.Combine(audit.DirectoryPath,"events.jsonl"));
        }
    }
    public static int Main(string[] args){string prior=Environment.GetEnvironmentVariable("COOPPLUS_SESSION_FIXTURE");try {
        if(args.Length!=2||Directory.Exists(args[1]))throw new Exception("Pass compiled dummy EXE and a new safe scratch folder.");string dummy=Playtest.Canon(args[0]),scratch=Playtest.Canon(args[1]);Directory.CreateDirectory(scratch);
        var parent=new ProcessIdentity{pid=101,createdFileTimeUtc=134000000000000000,endedFileTimeUtc=134000000000000100,role="proxy"};var child=new ProcessIdentity{pid=201,createdFileTimeUtc=134000000000000050,role="game"};bool candidateCaptured=false;int snapshots=0;
        Func<Dictionary<int,int>> snapshot=()=>{snapshots++;return candidateCaptured?new Dictionary<int,int>{{201,101}}:new Dictionary<int,int>();};
        var earlyParents=snapshot();candidateCaptured=true;SessionController.VerifyParentAfterIdentity(child,new[]{parent},snapshot,delegate{});Check(earlyParents.Count==0&&snapshots==2,"newborn game uses refreshed parent snapshot after captured identity");
        int wrongSnapshots=0;bool wrongRefused=false;try{SessionController.VerifyParentAfterIdentity(child,new[]{parent},()=>{wrongSnapshots++;return new Dictionary<int,int>{{201,999}};},delegate{});}catch(IOException){wrongRefused=true;}Check(wrongRefused&&wrongSnapshots==1,"genuinely wrong parent refuses without blanket retry");
        string ordinary=Run(Case(scratch,dummy,"Quit"),"quit",false);Check(ordinary.Split(new[]{"\"kind\":\"proxy-start\""},StringSplitOptions.None).Length-1==1,"zero quit starts no replacement");
        string crash=Run(Case(scratch,dummy,"Crash"),"crash",true);Check(crash.Contains("\"exitCode\":7")&&!crash.Contains("intentional-restart-accepted"),"nonzero exit retained without respawn");
        string early=Run(Case(scratch,dummy,"Early zero"),"early-zero",true);Check(early.Contains("proxy-exit-before-window-observed")&&early.Contains("supervision-failure"),"unproved zero startup surfaces retained failure");
        string restarted=Run(Case(scratch,dummy,"Restart"),"restart-once",false);Check(restarted.Contains("intentional-restart-accepted")&&restarted.Split(new[]{"\"kind\":\"proxy-start\""},StringSplitOptions.None).Length-1==2,"actual FILETIME bound handoff starts exactly one replacement");
        string unproved=Run(Case(scratch,dummy,"Unproved42"),"unproved42",true);Check(!unproved.Contains("intentional-restart-accepted"),"42 alone cannot authorize replacement");
        string bounded=Run(Case(scratch,dummy,"Bounded"),"restart-always",true);Check(bounded.Split(new[]{"\"kind\":\"proxy-start\""},StringSplitOptions.None).Length-1==5,"intentional restart chain is bounded at four replacements");
        SessionController.Fault=point=>{if(point=="after-process-start")throw new IOException("injected post-spawn identity failure");};
        string setup=Run(Case(scratch,dummy,"Setup failure"),"quit",true);SessionController.Fault=null;Check(setup.Contains("proxy-exit-after-supervision-failure"),"post-spawn setup failure retains handle until actual child exit");
        Console.WriteLine("RESULT "+cases+" focused process cases passed");return 0;
    }catch(Exception e){Console.Error.WriteLine(e);return 1;}finally{SessionController.Fault=null;Environment.SetEnvironmentVariable("COOPPLUS_SESSION_FIXTURE",prior);}}
}
