using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;

namespace CoopPlusBackup {
    // Read-only bounded inspection. An unrecorded custom command line cannot be inferred from coopplus.json.
    internal static class LaunchOverrides {
        internal static bool SkipForTests = false;
        internal static Func<Plan,IEnumerable<string>> Folders = DefaultFolders;
        static IEnumerable<string> DefaultFolders(Plan plan) {
            return new[]{plan.launcher,
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
                Environment.GetFolderPath(Environment.SpecialFolder.StartMenu),
                Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu)};
        }
        public static void AssertUnambiguous(Plan plan) {
            if(SkipForTests)return;
            var folders=new HashSet<string>(Folders(plan),StringComparer.OrdinalIgnoreCase);
            var shortcuts=new List<string>();
            foreach(string folder in folders) if(!String.IsNullOrEmpty(folder) && Directory.Exists(folder)) Gather(folder,shortcuts,0,
                folder==Environment.GetFolderPath(Environment.SpecialFolder.StartMenu) || folder==Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu));
            object shell=null;
            try {
                shell=Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell",true));
                foreach(string path in shortcuts) {
                    object link=null;
                    try {
                        link=shell.GetType().InvokeMember("CreateShortcut",BindingFlags.InvokeMethod,null,shell,new object[]{path});
                        string target=(string)link.GetType().InvokeMember("TargetPath",BindingFlags.GetProperty,null,link,null);
                        string name=Path.GetFileName(target).ToLowerInvariant();
                        if(name!="coopplus.exe" && name!="noita_proxy.exe" && name!="noita.exe" && name!="noita_dev.exe")continue;
                        string args=(string)link.GetType().InvokeMember("Arguments",BindingFlags.GetProperty,null,link,null);
                        string lower=args.ToLowerInvariant();
                        foreach(string flag in new[]{"--settings-path","--save-state-path","--launch-cmd","--exe-path","-savepath","-save_path"})
                            if(lower.Contains(flag))throw new IOException("A Noita/Co-op+ shortcut overrides selected storage or launch commands: "+path+". This version cannot establish the effective paired paths. No default paths were substituted.");
                        if(String.Equals(target,Path.Combine(plan.launcher,"CoopPlus.exe"),StringComparison.OrdinalIgnoreCase))
                            plan.sources.Add(new Source{name="launch-shortcuts/"+shortcuts.IndexOf(path)+"-"+Path.GetFileName(path),path=path,directory=false});
                    } finally { if(link!=null)Marshal.FinalReleaseComObject(link); }
                }
            } finally { if(shell!=null)Marshal.FinalReleaseComObject(shell); }
        }
        static void Gather(string folder,List<string> result,int depth,bool recursive) {
            BackupEngine.NoLinks(folder);
            if(depth>8 || result.Count>1000)throw new IOException("Too many shortcuts to establish launch overrides safely.");
            foreach(string path in Directory.GetFiles(folder,"*.lnk").OrderBy(x=>x,StringComparer.OrdinalIgnoreCase)) { BackupEngine.NoLinks(path); result.Add(path); }
            // Launcher children include managed runtime; only recurse in Start Menu, not the launcher.
            if(!recursive)return;
            foreach(string child in Directory.GetDirectories(folder).OrderBy(x=>x,StringComparer.OrdinalIgnoreCase)) {
                if((File.GetAttributes(child)&FileAttributes.ReparsePoint)!=0)continue;
                Gather(child,result,depth+1,true);
            }
        }
    }
}
