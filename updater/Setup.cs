using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace CoopPlus {
    public sealed class UserConfig { public int schema = 1; public string gameDir; public string settingsPath; public string saveStatePath; public bool pendingInstall; public string automaticBackupPath; }
    internal sealed class SetupRequest {
        public string Root; public UserConfig Config; public string SettingsOverride; public List<InstallationCandidate> Candidates;
    }
    internal sealed class SetupSelection { public UserConfig Config; public bool Shortcut; }
    internal static class Setup {
        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
        internal static Func<SetupRequest, SetupSelection> Select = Show;
        internal static Func<string, string, List<InstallationCandidate>> Discover = (root, settings) => NoitaDetection.Discover(root, settings, NoitaDetection.GetSteamRoots());
        internal static UserConfig Load(string root, bool force, string[] arguments) {
            string path = Path.Combine(root, "coopplus.json"); Program.Guard(path);
            bool hadConfig=File.Exists(path);
            UserConfig c = File.Exists(path) ? Json.Deserialize<UserConfig>(File.ReadAllText(path)) : null;
            if (c != null) {
                ValidateState(root, c);
                if (!force && NoitaDetection.Validate(root, c.gameDir)) return c;
                Console.WriteLine("Choose an available Noita installation. Existing proxy settings/state will be retained.");
            } else {
                string appdata = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                c = new UserConfig {
                    settingsPath = Path.Combine(appdata, "quant", "entangledworlds", "config", "proxy.ron"),
                    saveStatePath = Path.Combine(appdata, "quant", "entangledworlds", "data", "save_state")
                };
                // Explicit first-setup shortcut arguments select the same state the proxy will use.
                c.settingsPath = OverridePath(root, arguments, "--settings-path", c.settingsPath);
                c.saveStatePath = OverridePath(root, arguments, "--save-state-path", c.saveStatePath);
                ValidateState(root, c);
            }
            string priorGame = c.gameDir;
            bool priorPending = c.pendingInstall;
            Engine.CheckNotBusy();
            string effectiveSettings = OverridePath(root, arguments, "--settings-path", c.settingsPath);
            var request = new SetupRequest { Root = root, Config = c, SettingsOverride = OverridePath(root, arguments, "--settings-path", null), Candidates = Discover(root, effectiveSettings) };
            var choice = Select(request);
            if (choice == null) throw new OperationCanceledException("Setup cancelled. No installation or settings were changed.");
            c = choice.Config; ValidateState(root, c);
            string reason;
            if (!NoitaDetection.Validate(root, c.gameDir, out reason)) throw new IOException("Invalid Noita installation: " + reason);
            c.pendingInstall = priorPending || c.pendingInstall || !String.Equals(priorGame, c.gameDir, StringComparison.OrdinalIgnoreCase);
            Engine.CheckNotBusy();
            if(!hadConfig)AutomaticBackup.RecordFirstUse(root,c,arguments);
            WriteConfig(path, c);
            if (choice.Shortcut) {
                try { CreateShortcut(root, c.gameDir, Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)); }
                catch (Exception e) { MessageBox.Show(e.Message + "\nSetup can continue. You can open CoopPlus.exe directly.", "Co-op+ shortcut", MessageBoxButtons.OK, MessageBoxIcon.Information); }
            }
            return c;
        }
        private static string OverridePath(string root, string[] args, string option, string fallback) {
            string value = null;
            for (int i = 0; i < args.Length; i++) {
                if (args[i] == option) { if (++i >= args.Length) throw new ArgumentException("Missing value for " + option); value = args[i]; }
                else if (args[i].StartsWith(option + "=", StringComparison.Ordinal)) value = args[i].Substring(option.Length + 1);
            }
            if (value == null) return fallback;
            if (String.IsNullOrWhiteSpace(value)) throw new ArgumentException("Empty value for " + option);
            return Path.GetFullPath(Path.IsPathRooted(value) ? value : Path.Combine(root, "current", "proxy", value));
        }
        private static void ValidateState(string root, UserConfig c) {
            if (c == null || c.schema != 1) throw new IOException("Unsupported coopplus.json. Keep a copy before selecting settings again.");
            Program.ValidateMutable(root, c.gameDir, c.settingsPath);
            Program.ValidateMutable(root, c.gameDir, c.saveStatePath);
        }
        private static void WriteConfig(string path, UserConfig c) {
            string temp = path + ".new-" + Guid.NewGuid().ToString("N");
            try {
                File.WriteAllText(temp, Json.Serialize(c), new UTF8Encoding(false));
                if (File.Exists(path)) File.Replace(temp, path, null); else File.Move(temp, path);
            } finally { if (File.Exists(temp)) File.Delete(temp); }
        }
        internal static UserConfig Stored(string root) { return Json.Deserialize<UserConfig>(File.ReadAllText(Path.Combine(root, "coopplus.json"))); }
        internal static void MarkInstalled(string root) {
            var c = Stored(root);
            if (c.pendingInstall) { c.pendingInstall = false; WriteConfig(Path.Combine(root, "coopplus.json"), c); }
        }
        private static SetupSelection Show(SetupRequest request) {
            Application.EnableVisualStyles();
            using (var form = new SetupDialog(request)) return form.ShowDialog() == DialogResult.OK ? form.Selection : null;
        }
        internal static void OfferShortcut(string root, string game) {
            if (MessageBox.Show("Create a Co-op+ shortcut on your desktop?", "Co-op+", MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes)
                CreateShortcut(root, game, Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory));
        }
        internal static void CreateShortcut(string root, string game, string desktop) {
            string reason;
            if (!NoitaDetection.Validate(root, game, out reason)) throw new IOException(reason);
            string path = Path.Combine(desktop, "Co-op+.lnk"); Engine.AssertNoReparse(path);
            if (File.Exists(path)) throw new IOException("A Co-op+ desktop shortcut already exists. Keep it or remove it before requesting a new shortcut.");
            object shell = null, shortcut = null;
            try {
                shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell", true));
                shortcut = shell.GetType().InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { path });
                Type t = shortcut.GetType();
                t.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { Path.Combine(root, "CoopPlus.exe") });
                t.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { root });
                t.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { Path.Combine(game, "noita.exe") + ",0" });
                t.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { "Co-op+ — check approved updates, then open Entangled Worlds" });
                t.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, new object[0]);
                Console.WriteLine("Created Co-op+ desktop shortcut.");
            } finally {
                if (shortcut != null) Marshal.FinalReleaseComObject(shortcut);
                if (shell != null) Marshal.FinalReleaseComObject(shell);
            }
        }
    }

    internal sealed class SetupDialog : Form {
        private readonly SetupRequest request;
        private readonly UserConfig config;
        private readonly ListBox choices = new ListBox();
        private readonly TextBox selected = new TextBox();
        private readonly Label state = new Label();
        private readonly CheckBox shortcut = new CheckBox();
        private readonly Button use = new Button();
        internal SetupSelection Selection;
        internal SetupDialog(SetupRequest r) {
            request = r; config = new UserConfig { gameDir = r.Config.gameDir, settingsPath = r.Config.settingsPath, saveStatePath = r.Config.saveStatePath };
            Text = "Co-op+ — Find Noita"; StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(680, 400); FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            AutoScaleMode = AutoScaleMode.Dpi; Font = new Font("Segoe UI", 10);
            try { Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location); } catch { }
            Controls.Add(new Label { Text = "Choose your Noita installation", AutoSize = true, Font = new Font("Segoe UI", 14), Location = new Point(20, 18) });
            Controls.Add(new Label { Text = "Close Noita and the proxy before setup or updates.", AutoSize = true, Location = new Point(22, 57) });
            choices.SetBounds(22, 88, 636, 88); choices.HorizontalScrollbar = true;
            choices.SelectedIndexChanged += (s,e) => { var c = choices.SelectedItem as InstallationCandidate; selected.Text = c == null ? "" : c.GameDirectory; use.Enabled = c != null; };
            choices.Format += (s,e) => { var c = e.ListItem as InstallationCandidate; if (c != null) e.Value = c.GameDirectory + " — " + c.Source; }; choices.FormattingEnabled = true;
            Controls.Add(choices);
            selected.SetBounds(22, 185, 636, 48); selected.Multiline = true; selected.ReadOnly = true; selected.BackColor = SystemColors.Window; Controls.Add(selected);
            state.SetBounds(22, 243, 636, 42); state.AutoEllipsis = true; Controls.Add(state);
            var portable = new Button { Text = "Choose portable proxy…", Location = new Point(22, 290), Size = new Size(220, 32) };
            portable.Click += (s,e) => ChoosePortable(); Controls.Add(portable);
            shortcut.Text = "Create a Co-op+ desktop shortcut"; shortcut.SetBounds(260, 292, 398, 30); Controls.Add(shortcut);
            use.Text = "Use selected Noita"; use.SetBounds(248, 345, 154, 34); use.Enabled = false;
            use.Click += (s,e) => {
                var c = choices.SelectedItem as InstallationCandidate; string reason;
                if (c == null || !NoitaDetection.Validate(request.Root, c.GameDirectory, out reason)) { MessageBox.Show("That installation is no longer available. Browse for noita.exe.", "Co-op+"); return; }
                config.gameDir = c.GameDirectory; Selection = new SetupSelection { Config = config, Shortcut = shortcut.Checked }; DialogResult = DialogResult.OK; Close();
            }; Controls.Add(use); AcceptButton = use;
            var browse = new Button { Text = "Browse…", Location = new Point(414, 345), Size = new Size(115, 34) }; browse.Click += (s,e) => Browse(false); Controls.Add(browse);
            var cancel = new Button { Text = "Cancel", Location = new Point(541, 345), Size = new Size(117, 34), DialogResult = DialogResult.Cancel }; Controls.Add(cancel); CancelButton = cancel;
            RefreshChoices(r.Candidates);
        }
        private void RefreshChoices(List<InstallationCandidate> candidates) {
            choices.Items.Clear(); foreach (var c in candidates) choices.Items.Add(c);
            if (choices.Items.Count > 0) choices.SelectedIndex = 0;
            else { selected.Text = "No suitable installation was found. Browse for noita.exe."; use.Enabled = false; }
            state.Text = "Proxy settings for this launch (retained in place):\n" + (request.SettingsOverride ?? config.settingsPath);
        }
        private void Browse(bool cancelSetup) {
            using (var picker = new OpenFileDialog { Title = "Co-op+ — Select Noita's noita.exe", Filter = "Noita executable (noita.exe)|noita.exe", CheckFileExists = true, Multiselect = false, RestoreDirectory = true, FileName = "noita.exe" }) {
                var choice = choices.SelectedItem as InstallationCandidate;
                picker.InitialDirectory = choice != null ? choice.GameDirectory : request.Root;
                if (picker.ShowDialog(this) != DialogResult.OK) { if (cancelSetup) { DialogResult = DialogResult.Cancel; Close(); } return; }
                string game = Path.GetDirectoryName(picker.FileName), reason;
                if (!String.Equals(Path.GetFileName(picker.FileName), "noita.exe", StringComparison.OrdinalIgnoreCase) || !NoitaDetection.Validate(request.Root, game, out reason)) {
                    MessageBox.Show("Choose an unmodified installation folder containing noita.exe, outside the launcher/source checkout and without links.", "Co-op+", MessageBoxButtons.OK, MessageBoxIcon.Information); return;
                }
                RefreshChoices(new List<InstallationCandidate> { new InstallationCandidate { GameDirectory = game, Source = "Selected with Browse" } });
            }
        }
        private void ChoosePortable() {
            using (var picker = new FolderBrowserDialog { Description = "Choose the old proxy folder containing proxy.ron and save_state. They stay in that folder.", ShowNewFolderButton = false }) {
                if (picker.ShowDialog(this) != DialogResult.OK) return;
                string settings = Path.Combine(picker.SelectedPath, "proxy.ron");
                if (!File.Exists(settings)) { MessageBox.Show("That folder does not contain proxy.ron.", "Co-op+"); return; }
                try {
                    Program.ValidateMutable(request.Root, config.gameDir, settings);
                    Program.ValidateMutable(request.Root, config.gameDir, Path.Combine(picker.SelectedPath, "save_state"));
                } catch (Exception e) { MessageBox.Show(e.Message, "Co-op+", MessageBoxButtons.OK, MessageBoxIcon.Information); return; }
                config.settingsPath = settings; config.saveStatePath = Path.Combine(picker.SelectedPath, "save_state");
                RefreshChoices(Setup.Discover(request.Root, request.SettingsOverride ?? settings));
            }
        }
    }
}
