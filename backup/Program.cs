using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
[assembly: AssemblyTitle("Co-op+ Backup")]
[assembly: AssemblyProduct("Co-op+ standalone backup utility")]
[assembly: AssemblyDescription("Verified paired Noita and EW state backups")]
[assembly: AssemblyVersion("0.1.2.0")]
[assembly: AssemblyFileVersion("0.1.2.0")]

namespace CoopPlusBackup {
    internal static class Program {
        [STAThread] static void Main() {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            string root=Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            Application.Run(new BackupWindow(root));
        }
    }
    internal sealed class BackupWindow:Form {
        string root; string destination; string completed;
        readonly Label summary=new Label { UseMnemonic=false }, status=new Label { UseMnemonic=false };
        readonly TextBox location=new TextBox();
        readonly Button backup=new Button(), choose=new Button(), cancel=new Button(), open=new Button();
        readonly ProgressBar progress=new ProgressBar();
        readonly CheckBox joiningClient=new CheckBox();
        CancellationTokenSource cancellation; bool active;
        public BackupWindow(string launcher) {
            root=launcher; destination=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"CoopPlusBackups");
            Text="Co-op+ Backup"; ClientSize=new Size(700,475); MinimumSize=new Size(716,514); StartPosition=FormStartPosition.CenterScreen;
            Font=new Font("Segoe UI",10); AutoScaleMode=AutoScaleMode.Dpi;
            try { Icon=Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location); }catch{}
            var title=new Label{Text="Protect your co-op run",Font=new Font("Segoe UI",18),AutoSize=true,Location=new Point(22,18),UseMnemonic=false};Controls.Add(title);
            summary.SetBounds(24,65,650,136);summary.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;Controls.Add(summary);
            joiningClient.Text="This PC joins another player (client).";joiningClient.SetBounds(24,204,650,30);joiningClient.Visible=false;joiningClient.CheckedChanged+=(s,e)=>{if(!active)backup.Enabled=joiningClient.Checked;};Controls.Add(joiningClient);
            Controls.Add(new Label{Text="Backup folder",AutoSize=true,Location=new Point(24,248),UseMnemonic=false});
            location.SetBounds(24,274,522,29);location.ReadOnly=true;location.Text=destination;location.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;Controls.Add(location);
            choose.Text="Change…";choose.SetBounds(558,271,116,32);choose.Anchor=AnchorStyles.Top|AnchorStyles.Right;choose.Click+=(s,e)=>{using(var picker=new FolderBrowserDialog{Description="Choose a backup folder outside the game, launcher and save folders",RootFolder=Environment.SpecialFolder.MyComputer,SelectedPath=Directory.Exists(destination)?destination:Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)})if(picker.ShowDialog(this)==DialogResult.OK){destination=picker.SelectedPath;location.Text=destination;}};Controls.Add(choose);
            progress.SetBounds(24,320,650,14);progress.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;Controls.Add(progress);
            status.SetBounds(24,344,650,51);status.Anchor=AnchorStyles.Top|AnchorStyles.Left|AnchorStyles.Right;status.AutoEllipsis=true;status.Text="Save & Quit Noita, then close every proxy before backing up.";Controls.Add(status);
            backup.Text="Back up now";backup.SetBounds(24,415,155,38);backup.Click+=(s,e)=>Start();Controls.Add(backup);AcceptButton=backup;
            cancel.Text="Cancel";cancel.SetBounds(191,415,95,38);cancel.Enabled=false;cancel.Click+=(s,e)=>{if(cancellation!=null)cancellation.Cancel();status.Text="Cancelling… earlier backups will be preserved.";};Controls.Add(cancel);
            open.Text="Open backup folder";open.SetBounds(474,415,200,38);open.Anchor=AnchorStyles.Top|AnchorStyles.Right;open.Enabled=false;open.Click+=(s,e)=>Process.Start(new ProcessStartInfo{FileName=completed,UseShellExecute=true});Controls.Add(open);
            FormClosing+=(s,e)=>{if(active){e.Cancel=true;cancellation.Cancel();status.Text="Cancelling safely… close again after copying stops.";}};
            if(!File.Exists(Path.Combine(root,"CoopPlus.exe")) || !File.Exists(Path.Combine(root,"coopplus.json"))) {
                backup.Enabled=false;choose.Enabled=false;
                summary.Text="Choose your Co-op+ launcher folder to find the selected save paths.";
                status.Text="Waiting for a folder containing CoopPlus.exe and coopplus.json.";
                Shown+=(s,e)=>BeginInvoke((Action)ChooseLauncher);
            } else LoadSelection();
        }
        void ChooseLauncher() {
            using(var picker=new FolderBrowserDialog {
                Description="Choose the folder containing CoopPlus.exe and coopplus.json",
                ShowNewFolderButton=false,RootFolder=Environment.SpecialFolder.MyComputer,SelectedPath=root
            }) {
                if(picker.ShowDialog(this)!=DialogResult.OK) {Close();return;}
                root=picker.SelectedPath;
            }
            choose.Enabled=true;LoadSelection();
        }
        void LoadSelection() {
            backup.Enabled=true;status.Text="Save & Quit Noita, then close every proxy before backing up.";
            try {
                var p=BackupEngine.Discover(root,true);
                summary.Text=p.release+"\r\nNoita profile: "+p.profile+"\r\nEW state: "+p.state+"\r\n\r\nBoth PCs need their own backup from the same stopping point.";
                if(p.emptyProxyState) {
                    joiningClient.Visible=true;backup.Enabled=false;
                    summary.Text=p.release+"\r\nNoita profile: "+p.profile+"\r\nEW state: "+p.state+"\r\n\r\nProxy state is empty. A client backup also requires the host counterpart.";
                }
            }catch(Exception e){summary.Text="Could not establish the selected paired save paths.\r\n"+e.Message;backup.Enabled=false;}
        }
        void Start() {
            active=true;backup.Enabled=false;choose.Enabled=false;joiningClient.Enabled=false;cancel.Enabled=true;open.Enabled=false;
            bool confirmedClient=joiningClient.Visible && joiningClient.Checked;
            cancellation=new CancellationTokenSource();progress.Style=ProgressBarStyle.Marquee;
            var worker=new BackgroundWorker();
            worker.DoWork+=(s,e)=>{e.Result=BackupEngine.Run(root,destination,cancellation.Token,message=>{
                if(!IsDisposed)BeginInvoke((Action)(()=>status.Text=message));
            },confirmedClient);};
            worker.RunWorkerCompleted+=(s,e)=>{
                active=false;backup.Enabled=!joiningClient.Visible || joiningClient.Checked;choose.Enabled=true;joiningClient.Enabled=true;cancel.Enabled=false;progress.Style=ProgressBarStyle.Blocks;
                if(e.Error!=null){status.Text="Backup did not complete. Earlier backups are safe.";MessageBox.Show(this,e.Error.Message,"Co-op+ Backup",MessageBoxButtons.OK,MessageBoxIcon.Information);}
                else {completed=(string)e.Result;open.Enabled=true;progress.Value=100;status.Text="Verified file copy complete. Game Continue has not been tested.\r\n"+completed;}
                cancellation.Dispose();cancellation=null;worker.Dispose();
            };
            worker.RunWorkerAsync();
        }
    }
}
