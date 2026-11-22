using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;

namespace CoopPlusPlaytest {
    // This helper only receives exact private child handles from SessionController.
    // Keep the HICONs alive until those windows have closed.
    internal sealed class NoitaWindowIcon : IDisposable {
        internal const string RelativePath = "data/icon.bmp";
        const uint GetIcon = 0x007f, SetIcon = 0x0080;
        const uint AbortOnHangOrExit = 0x0022;
        const int MaximumAttempts = 4;

        readonly string root;
        readonly SessionAudit audit;
        readonly Dictionary<Size, IntPtr> icons = new Dictionary<Size, IntPtr>();
        readonly Dictionary<Process, WindowAttempt> windows = new Dictionary<Process, WindowAttempt>();
        Bitmap artwork;
        bool loaded;

        sealed class WindowAttempt {
            internal IntPtr window;
            internal int count;
            internal bool applied;
        }

        internal NoitaWindowIcon(string root, SessionAudit audit) {
            this.root = root;
            this.audit = audit;
        }

        [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
        [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
        [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr window);
        [DllImport("user32.dll")] static extern int GetSystemMetricsForDpi(int metric, uint dpi);
        [DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr icon);
        [DllImport("user32.dll", EntryPoint = "SendMessageTimeoutW")]
        static extern IntPtr SendMessageTimeout(IntPtr window, uint message, UIntPtr kind, IntPtr value,
            uint flags, uint timeoutMs, out IntPtr result);

        internal static Bitmap ReadArtwork(Stream input) {
            // The owned game supplies a small uncompressed 24-bit BMP.
            if (input.Length < 54 || input.Length > 1024 * 1024) throw new InvalidDataException("Unsupported game icon size.");
            using (var reader = new BinaryReader(input, System.Text.Encoding.UTF8, true)) {
                if (reader.ReadUInt16() != 0x4d42) throw new InvalidDataException("Unsupported game icon type.");
                input.Position = 10;
                uint offset = reader.ReadUInt32(), header = reader.ReadUInt32();
                int width = reader.ReadInt32(), height = reader.ReadInt32();
                if (header < 40 || header > 124 || offset < 14 + header || width < 1 || width > 256 ||
                    (height != width && height != -width) || reader.ReadUInt16() != 1 ||
                    reader.ReadUInt16() != 24 || reader.ReadUInt32() != 0)
                    throw new InvalidDataException("Unsupported game icon format.");

                int stride = (width * 3 + 3) & ~3;
                if (offset + stride * width > input.Length) throw new InvalidDataException("Truncated game icon.");
                input.Position = offset;
                var pixels = new Color[width * width];
                for (int row = 0; row < width; row++) {
                    byte[] bytes = reader.ReadBytes(stride);
                    if (bytes.Length != stride) throw new InvalidDataException("Truncated game icon row.");
                    int y = height < 0 ? row : width - row - 1;
                    for (int x = 0; x < width; x++)
                        pixels[y * width + x] = Color.FromArgb(bytes[x * 3 + 2], bytes[x * 3 + 1], bytes[x * 3]);
                }
                return RemoveBackground(pixels, width);
            }
        }

        static Bitmap RemoveBackground(Color[] pixels, int width) {
            var background = new Queue<int>();
            Action<int> mark = index => {
                if (pixels[index].ToArgb() != Color.Black.ToArgb()) return;
                pixels[index] = Color.FromArgb(0, 0, 0, 0);
                background.Enqueue(index);
            };
            for (int i = 0; i < width; i++) {
                mark(i);
                mark((width - 1) * width + i);
                mark(i * width);
                mark(i * width + width - 1);
            }
            while (background.Count != 0) {
                int index = background.Dequeue();
                int x = index % width, y = index / width;
                if (x > 0) mark(index - 1);
                if (x < width - 1) mark(index + 1);
                if (y > 0) mark(index - width);
                if (y < width - 1) mark(index + width);
            }
            var result = new Bitmap(width, width, PixelFormat.Format32bppArgb);
            for (int y = 0; y < width; y++)
                for (int x = 0; x < width; x++)
                    result.SetPixel(x, y, pixels[y * width + x]);
            return result;
        }

        IntPtr IconAt(Size size) {
            IntPtr icon;
            if (icons.TryGetValue(size, out icon)) return icon;
            if (size.Width < 1 || size.Width > 256 || size.Height < 1 || size.Height > 256)
                return IntPtr.Zero;
            using (var frame = new Bitmap(size.Width, size.Height, PixelFormat.Format32bppArgb)) {
                for (int y = 0; y < size.Height; y++)
                    for (int x = 0; x < size.Width; x++)
                        frame.SetPixel(x, y, artwork.GetPixel((2 * x + 1) * artwork.Width / (2 * size.Width),
                            (2 * y + 1) * artwork.Height / (2 * size.Height)));
                icon = frame.GetHicon();
            }
            if (icon != IntPtr.Zero) icons.Add(size, icon);
            return icon;
        }

        static bool OwnsWindow(Process game, IntPtr window) {
            uint pid;
            return window != IntPtr.Zero && IsWindowVisible(window) && !game.HasExited &&
                GetWindowThreadProcessId(window, out pid) != 0 && pid == (uint)game.Id;
        }

        bool SetSize(Process game, IntPtr window, uint dpi, uint kind, Size size) {
            IntPtr icon = IconAt(size), actual;
            if (icon == IntPtr.Zero || !OwnsWindow(game, window)) return false;
            return SendMessageTimeout(window, SetIcon, (UIntPtr)kind, icon, AbortOnHangOrExit, 80, out actual) != IntPtr.Zero &&
                SendMessageTimeout(window, GetIcon, (UIntPtr)kind, (IntPtr)dpi, AbortOnHangOrExit, 80, out actual) != IntPtr.Zero &&
                actual == icon;
        }

        void Load() {
            loaded = true;
            string path = Playtest.Child(root, "game/" + RelativePath);
            if (!File.Exists(path)) {
                audit.Write("game-window-icon", new { result = "missing" });
                return;
            }
            using (var input = File.OpenRead(path)) artwork = ReadArtwork(input);
        }

        internal void Observe(ICollection<Process> games) {
            foreach (var ended in windows.Keys.Where(game => !games.Contains(game)).ToArray()) windows.Remove(ended);
            if (loaded && artwork == null) return;
            foreach (var game in games) {
                try {
                    game.Refresh();
                    IntPtr window = game.MainWindowHandle;
                    if (!OwnsWindow(game, window)) continue;
                    if (!loaded) Load();
                    if (artwork == null) return;
                    WindowAttempt attempt;
                    if (!windows.TryGetValue(game, out attempt) || attempt.window != window)
                        windows[game] = attempt = new WindowAttempt { window = window };
                    if (attempt.applied || attempt.count >= MaximumAttempts) continue;
                    attempt.count++;
                    uint dpi = GetDpiForWindow(window);
                    if (dpi == 0) dpi = 96;
                    var large = new Size(GetSystemMetricsForDpi(11, dpi), GetSystemMetricsForDpi(12, dpi));
                    var small = new Size(GetSystemMetricsForDpi(49, dpi), GetSystemMetricsForDpi(50, dpi));
                    attempt.applied = SetSize(game, window, dpi, 1, large) && SetSize(game, window, dpi, 0, small);
                    if (attempt.applied || attempt.count == MaximumAttempts)
                        audit.Write("game-window-icon", new { result = attempt.applied ? "applied" : "window unresponsive", dpi = dpi });
                } catch (Exception error) {
                    // Artwork and Win32 icon messages are optional. A failed
                    // attempt must never change game ownership or session state.
                    if (artwork == null) loaded = true;
                    audit.Write("game-window-icon", new { result = "unavailable", reason = error.GetType().Name });
                }
            }
        }

        public void Dispose() {
            foreach (IntPtr icon in icons.Values) DestroyIcon(icon);
            if (artwork != null) artwork.Dispose();
        }
    }
}
