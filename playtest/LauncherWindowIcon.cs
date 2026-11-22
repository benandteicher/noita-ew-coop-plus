using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace CoopPlusPlaytest {
    // The EXE's normal Win32 icon remains the shortcut icon. Give the
    // running window a separate icon source for its Windows taskbar group.
    internal sealed class LauncherWindowIcon : IDisposable {
        internal const string Resource = "CoopPlus.Staging.Window.ico";
        static readonly Guid AppProperties = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        const uint RelaunchCommand = 2, RelaunchIcon = 3, RelaunchName = 4, AppId = 5;
        readonly IPropertyStore store;

        [StructLayout(LayoutKind.Sequential)]
        struct PropertyKey { internal Guid format; internal uint id; }

        [StructLayout(LayoutKind.Sequential)]
        struct CountedArray { internal uint count; internal IntPtr values; }

        [StructLayout(LayoutKind.Explicit)]
        struct PropertyValue {
            [FieldOffset(0)] internal ushort type;
            [FieldOffset(8)] internal IntPtr text;
            [FieldOffset(8)] internal CountedArray reservedForNativeUnionSize;
        }

        [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IPropertyStore {
            [PreserveSig] int GetCount(out uint count);
            [PreserveSig] int GetAt(uint index, out PropertyKey key);
            [PreserveSig] int GetValue(ref PropertyKey key, out PropertyValue value);
            [PreserveSig] int SetValue(ref PropertyKey key, ref PropertyValue value);
            [PreserveSig] int Commit();
        }

        [DllImport("shell32.dll")]
        static extern int SHGetPropertyStoreForWindow(IntPtr window, ref Guid iid,
            [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);

        LauncherWindowIcon(IPropertyStore store) { this.store = store; }

        void Set(uint id, string value) {
            var key = new PropertyKey { format = AppProperties, id = id };
            var property = new PropertyValue();
            if (value != null) {
                property.type = 31; // VT_LPWSTR
                property.text = Marshal.StringToCoTaskMemUni(value);
            }
            try { Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref property)); }
            finally { if (property.text != IntPtr.Zero) Marshal.FreeCoTaskMem(property.text); }
        }

        static string IconFile(string package) {
            byte[] bytes;
            using (var source = typeof(LauncherWindowIcon).Assembly.GetManifestResourceStream(Resource))
            using (var buffer = new MemoryStream()) {
                source.CopyTo(buffer);
                bytes = buffer.ToArray();
            }
            string folder = Playtest.Child(package, "staging-appearance");
            Directory.CreateDirectory(folder);
            string path = Playtest.Child(folder, "launcher-" + Playtest.HashBytes(bytes).Substring(0, 16) + ".ico");
            if (!File.Exists(path)) {
                string temporary = Playtest.Child(folder, Guid.NewGuid().ToString("N") + ".tmp");
                bool created = false;
                try {
                    using (var output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None)) {
                        created = true;
                        output.Write(bytes, 0, bytes.Length);
                        output.Flush(true);
                    }
                    File.Move(temporary, path);
                } finally { if (created && File.Exists(temporary)) File.Delete(temporary); }
            }
            if (new FileInfo(path).Length != bytes.Length || !File.ReadAllBytes(path).SequenceEqual(bytes))
                throw new IOException("The local taskbar artwork does not match the launcher.");
            return path;
        }

        internal static LauncherWindowIcon TryAttach(IntPtr window, string package, string name) {
            LauncherWindowIcon result = null;
            try {
                string path = IconFile(package);
                var iid = typeof(IPropertyStore).GUID;
                IPropertyStore store;
                Marshal.ThrowExceptionForHR(SHGetPropertyStoreForWindow(window, ref iid, out store));
                result = new LauncherWindowIcon(store);
                result.Set(RelaunchCommand, Playtest.Quote(Application.ExecutablePath));
                result.Set(RelaunchName, name);
                result.Set(RelaunchIcon, path + ",0");
                result.Set(AppId, "CoopPlus.Staging.Launcher." +
                    Playtest.HashBytes(Encoding.UTF8.GetBytes(package.ToUpperInvariant())).Substring(0, 24));
                return result;
            } catch (Exception) {
                // Keep the window icon and normal launcher behavior if the
                // optional shell metadata is unavailable.
                if (result != null) result.Dispose();
                return null;
            }
        }

        public void Dispose() {
            foreach (uint id in new[] { AppId, RelaunchIcon, RelaunchName, RelaunchCommand }) {
                try { Set(id, null); } catch (COMException) { }
            }
            Marshal.ReleaseComObject(store);
        }
    }
}
