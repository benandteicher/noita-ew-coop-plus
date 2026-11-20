using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using CoopPlus;

namespace CoopPlusPlaytest {
    internal sealed class StagingLauncher : Form {
        readonly string package;
        readonly Label version = new Label { AutoSize = true };
        readonly Label status = new Label { AutoSize = true, MaximumSize = new Size(500, 0) };
        readonly TextBox exportPath = new TextBox { ReadOnly = true, Dock = DockStyle.Fill, Visible = false };
        readonly Button launch = new Button { Text = "Launch Staging", AutoSize = true };
        readonly Button update = new Button { Text = "Check for update", AutoSize = true };
        readonly Button export = new Button { Text = "Export logs", AutoSize = true };
        readonly Button cancel = new Button { Text = "Cancel setup", AutoSize = true, Visible = false };
        readonly Button showFolder = new Button { Text = "Open export folder", AutoSize = true, Visible = false };
        readonly ProgressBar busy = new ProgressBar { Dock = DockStyle.Top, Height = 8, Style = ProgressBarStyle.Marquee, Visible = false };
        bool working, exporting;
        int preparation;

        StagingLauncher(string package) {
            this.package = package;
            Text = "Co-op+ Staging";
            using (var artwork = typeof(StagingLauncher).Assembly.GetManifestResourceStream("CoopPlus.Staging.ico"))
                Icon = new Icon(artwork);
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(556, 272);
            MinimumSize = new Size(540, 295);
            Font = new Font("Segoe UI", 10);
            AutoScaleMode = AutoScaleMode.Dpi;

            var layout = new TableLayoutPanel { Dock=DockStyle.Fill, AutoScroll=true, ColumnCount=1, RowCount=7, Padding=new Padding(18) };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            var heading = new Label { Text="Co-op+ Staging", Font=new Font(Font.FontFamily, 19, FontStyle.Bold), AutoSize=true, Margin=new Padding(0,0,0,5) };
            version.ForeColor = SystemColors.GrayText; version.Margin = new Padding(0, 0, 0, 12);
            SetVersion(Playtest.Version, Playtest.Build, "Bundled");
            var buttons = new FlowLayoutPanel { AutoSize=true, Dock=DockStyle.Fill, Margin=new Padding(0,0,0,10) };
            buttons.Controls.AddRange(new Control[]{launch, update, export, cancel});
            status.Text = "Launch a private Noita session through Steam.";
            status.Margin = new Padding(0, 8, 0, 8);
            var output = new TableLayoutPanel { Dock=DockStyle.Fill, AutoSize=true, ColumnCount=2, RowCount=1 };
            output.ColumnStyles.Add(new ColumnStyle(SizeType.Percent,100));
            output.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            output.Controls.Add(exportPath,0,0); output.Controls.Add(showFolder,1,0);
            layout.Controls.Add(heading); layout.Controls.Add(version); layout.Controls.Add(buttons);
            layout.Controls.Add(busy); layout.Controls.Add(status); layout.Controls.Add(output);
            Controls.Add(layout);

            launch.Click += async (sender, args) => await StartGame();
            update.Click += async (sender, args) => await CheckUpdate();
            export.Click += async (sender, args) => await ExportLogs();
            cancel.Click += (sender, args) => {
                if (Interlocked.CompareExchange(ref preparation, 1, 0) == 0) {
                    cancel.Enabled = false;
                    status.Text = "Finishing the current verification operation before cancelling.";
                }
            };
            showFolder.Click += (sender, args) => {
                try { Process.Start(new ProcessStartInfo(Playtest.Canon(exportPath.Text)) { UseShellExecute=true }); }
                catch (Exception error) { status.Text = error.Message; }
            };
            FormClosing += (sender, args) => {
                if (!working && !exporting) return;
                args.Cancel = true;
                status.Text = exporting ? "Wait for the log export to finish." : "Finish this operation or close the private game and proxy normally.";
            };
        }

        void SetVersion(string value, long build, string prefix = "Active") {
            version.Text = prefix + " " + value + " · build " + build + " · private saves";
        }
        void SetWorking(bool value) {
            working = value;
            busy.Visible = value;
            launch.Enabled = update.Enabled = !value;
            UseWaitCursor = false;
        }
        void Report(string message) {
            if (IsDisposed) return;
            BeginInvoke((MethodInvoker)(() => {
                if (Volatile.Read(ref preparation) == 1) return;
                status.Text = message;
            }));
        }
        T Exclusive<T>(Func<T> body) {
            // The older private launcher uses this same gate while running.
            using (var gate = new Mutex(false, "Local\\CoopPlus-Private-Playtest")) {
                bool held;
                try { held = gate.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                if (!held) throw new IOException("Close the active private launcher, game and proxy first.");
                try { Playtest.CheckStopped(); return body(); }
                finally { gate.ReleaseMutex(); }
            }
        }
        void ConnectProgress(SessionAudit audit) {
            long last = 0;
            Playtest.HashedBytes = 0; Playtest.HashedFiles = 0;
            Playtest.Progress = value => {
                long elapsed = Stopwatch.GetTimestamp();
                if (value.Contains("\n") && elapsed - last < Stopwatch.Frequency / 8) return;
                last = elapsed;
                Report(value);
            };
            Playtest.PhaseLog = (name, files, bytes) => audit.Write("phase", new {name=name, hashedFiles=files, hashedBytes=bytes});
            Engine.Progress = Report;
        }
        static void DisconnectProgress() {
            Playtest.Progress = delegate {};
            Playtest.PhaseLog = delegate {};
            Playtest.CancellationRequested = delegate { return false; };
            Engine.Progress = Console.WriteLine;
        }
        static void LogFailure(SessionAudit audit, string name, Exception error) {
            audit.Write(name, new {error=error.ToString(), hresult=error.HResult});
            audit.Finish("failure");
        }
        async Task StartGame() {
            if (working) return;
            SetWorking(true);
            preparation = 0;
            cancel.Visible = cancel.Enabled = true;
            status.Text = "Checking the private Staging setup.";
            try {
                await Task.Run(() => {
                    using (var audit = new SessionAudit(package)) {
                        ConnectProgress(audit);
                        try {
                            Exclusive(() => {
                                // Recovery always runs before cancellation can
                                // interrupt a fresh verification operation.
                                StagingUpdate.Recover(package, audit);
                                Playtest.CancellationRequested = () => Volatile.Read(ref preparation) == 1;
                                var selected = StagingUpdate.Current(package);
                                audit.Bind(selected.manifest);
                                Invoke((MethodInvoker)(() => SetVersion(selected.manifest.version, selected.manifest.build)));
                                string source = null;
                                if (!Directory.Exists(Playtest.Child(package, Playtest.PrivateName))) {
                                    Exception pickerFailure = null;
                                    Invoke((MethodInvoker)(() => {
                                        try { source = Playtest.PickSource(package, selected, this); }
                                        catch (Exception error) { pickerFailure = error; }
                                    }));
                                    if (pickerFailure != null) throw pickerFailure;
                                }
                                string root = Playtest.Prepare(package, source, selected);
                                CheckNoCancel();
                                Playtest.CheckStopped();
                                SessionController.Run(root, audit, Report);
                                return 0;
                            });
                            audit.Finish("proxy-exit-zero");
                        } catch (OperationCanceledException) {
                            audit.Finish("preparation-cancelled"); throw;
                        } catch (Exception error) {
                            LogFailure(audit, "launcher-failure", error); throw;
                        } finally { DisconnectProgress(); }
                    }
                });
                status.Text = "Staging closed. You can launch again or export the session logs.";
            } catch (OperationCanceledException) { status.Text = "Setup cancelled. The profile and any setup logs were retained."; }
            catch (Exception error) { status.Text = error.Message; }
            finally {
                preparation = 2;
                cancel.Visible = false;
                SetWorking(false);
            }
        }
        void CheckNoCancel() {
            Playtest.CheckPreparationCancellation();
            Invoke((MethodInvoker)(() => {
                if (Interlocked.CompareExchange(ref preparation, 2, 0) == 0) cancel.Visible = false;
            }));
            Playtest.CheckPreparationCancellation();
        }
        async Task CheckUpdate() {
            if (working) return;
            SetWorking(true);
            status.Text = "Checking the approved Staging release.";
            try {
                using (var audit = new SessionAudit(package)) {
                    try {
                        bool bundled = false;
                        var choice = await Task.Run(() => Exclusive(() => {
                            StagingUpdate.Recover(package, audit);
                            if (!Directory.Exists(Playtest.Child(package, Playtest.PrivateName)))
                                throw new IOException("Launch Staging once to create the private installation before updating it.");
                            var current = StagingUpdate.Current(package);
                            audit.Bind(current.manifest);
                            Invoke((MethodInvoker)(() => SetVersion(current.manifest.version, current.manifest.build)));
                            audit.Write("update-check", new {runtimeId = current.manifest.runtimeId});
                            if (current.manifest.build == StagingUpdate.PreviousBuild) {
                                Playtest.LoadPayload(package);
                                bundled = true;
                                return null;
                            }
                            var release = StagingUpdate.Check();
                            return StagingUpdate.IsNewer(release, current) ? release : null;
                        }));
                        if (!bundled && choice == null) {
                            audit.Finish("current");
                            status.Text = "Your installed Staging build is current for this feed.";
                            return;
                        }
                        string offeredVersion = bundled ? Playtest.Version : choice.version;
                        long offeredBuild = bundled ? Playtest.Build : choice.build;
                        var answer = MessageBox.Show(this,
                            "Install " + (bundled ? "bundled" : "approved") + " Staging " + offeredVersion + " build " + offeredBuild +
                            "?\n\nThe update keeps this private run, preferences and logs.",
                            "Choose Staging update", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button2);
                        if (answer != DialogResult.Yes) {
                            audit.Finish("declined");
                            status.Text = "The current Staging build is available to launch.";
                            return;
                        }
                        await Task.Run(() => Exclusive(() => {
                            ConnectProgress(audit);
                            try {
                                var current = StagingUpdate.Current(package);
                                if (bundled) StagingUpdate.InstallBundled(package, current, audit);
                                else StagingUpdate.Install(package, current, choice, audit);
                            } finally { DisconnectProgress(); }
                            return 0;
                        }));
                        SetVersion(offeredVersion, offeredBuild);
                        audit.Finish("complete");
                        status.Text = "Staging updated. Open the proxy when you are ready.";
                    } catch (Exception error) {
                        LogFailure(audit, "update-failure", error); throw;
                    }
                }
            } catch (Exception error) { status.Text = "Staging update check stopped. " + error.Message; }
            finally { SetWorking(false); }
        }
        async Task ExportLogs() {
            if (exporting) return;
            exporting = true;
            export.Enabled = false;
            try {
                var result = await Task.Run(() => StagingLogs.Export(package));
                exportPath.Text = result.Directory;
                exportPath.Visible = showFolder.Visible = true;
                string message = result.Files.Length + (result.Files.Length == 1 ? " log ZIP created." : " log ZIPs created.");
                if (result.Unreadable > 0) message += " Some sources were unavailable. See INDEX.txt for details.";
                MessageBox.Show(this, message + "\n\n" + String.Join("\n", Array.ConvertAll(result.Files, Path.GetFileName)) +
                    "\n\n" + result.Directory, "Export logs", MessageBoxButtons.OK, MessageBoxIcon.Information);
            } catch (Exception error) {
                MessageBox.Show(this, "Could not export the logs. " + error.Message, "Export logs", MessageBoxButtons.OK, MessageBoxIcon.Error);
            } finally {
                exporting = false;
                export.Enabled = true;
            }
        }
        [STAThread]
        internal static int Main(string[] args) {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try {
                if (args.Length != 0) throw new IOException("Open Co-op+ Staging.exe directly.");
                string package = Playtest.Canon(Path.GetDirectoryName(Application.ExecutablePath));
                Playtest.ValidatePackage(package);
                string instance = Playtest.HashBytes(Encoding.UTF8.GetBytes(package.ToUpperInvariant()));
                using (var gate = new Mutex(false, "Local\\CoopPlus-Staging-" + instance)) {
                    bool held;
                    try { held = gate.WaitOne(0); } catch (AbandonedMutexException) { held = true; }
                    if (!held) throw new IOException("This Staging launcher is already open.");
                    try { using (var window = new StagingLauncher(package)) Application.Run(window); }
                    finally { gate.ReleaseMutex(); }
                }
                return 0;
            } catch (Exception error) {
                MessageBox.Show(error.Message, "Co-op+ Staging", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
    }
}
