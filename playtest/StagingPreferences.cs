using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;

namespace CoopPlusPlaytest {
    internal static class StagingPreferences {
        const int MaximumPreferences = 1024 * 1024;
        static readonly CultureInfo Invariant = CultureInfo.InvariantCulture;
        static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);
        static readonly string[] Toggles = {
            "rendering_filmgrain", "rendering_low_quality", "rendering_low_resolution",
            "rendering_pixel_art_antialiasing", "gamepad_analog_flying", "application_pause_when_unfocused",
            "ui_allow_shooting_while_inventory_open", "ui_inventory_icons_always_clickable",
            "ui_show_world_hover_info_next_to_mouse", "ui_snappy_hover_boxes", "ui_report_damage",
        };
        static readonly string[] Actions = (
            "aim_stick key_up key_down key_left key_right key_use_wand key_spray_flask key_throw key_kick " +
            "key_inventory key_interact key_item_next key_item_prev key_ui_quick_drag key_ui_confirm key_ui_drag " +
            "key_drop_item key_drink_potion key_takescreenshot key_replayedit_open"
        ).Split(' ').Concat(Enumerable.Range(1, 10).Select(n => "key_item_slot" + n)).ToArray();
        static readonly HashSet<string> Locales = new HashSet<string>(new[]{
            "en","de","fr","es","it","pt-br","ru","zh-cn","zh-tw","ja","ko","fi","pl",
        }, StringComparer.Ordinal);
        static readonly string[] FreshRules = {
            "quant.ew.coop_enabled", "quant.ew.coop_statue", "quant.ew.coop_mountain",
            "quant.ew.shared_income", "quant.ew.shop_hover",
        };
        internal const string Ping = "quant.ew.rebind_ping";

        static XmlElement ReadConfig(string file) {
            try {
                Playtest.Canon(file);
                if (!File.Exists(file) || new FileInfo(file).Length > MaximumPreferences) return null;
                var document = new XmlDocument { XmlResolver = null };
                var readerSettings = new XmlReaderSettings {
                    DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, MaxCharactersInDocument = MaximumPreferences,
                };
                using (var reader = XmlReader.Create(file, readerSettings)) document.Load(reader);
                var config = document.DocumentElement;
                return config != null && config.Name == "Config" && config.GetAttribute("config_format_version") == "14" ? config : null;
            } catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
            catch (XmlException) { return null; }
        }
        static void Copy(XmlElement from, XmlElement to, string key, Func<string, bool> valid) {
            string value = from.GetAttribute(key);
            if (value.Length > 0 && value.Length < 128 && valid(value)) to.SetAttribute(key, value);
        }
        static bool Number(string value, double first, double last) {
            double parsed;
            return Double.TryParse(value, NumberStyles.Float, Invariant, out parsed) && parsed >= first && parsed <= last;
        }
        static bool Toggle(string value) { return value == "0" || value == "1"; }
        static bool BindingName(string value) {
            return value.Length <= 48 && Regex.IsMatch(value, @"^(?:\$\$input_[a-z0-9_]+|[\p{L}\p{N} +\-_/.,;:'""\[\]\\=]*)$");
        }
        static bool KeyCode(int value) {
            // Assigned SDL2 scancodes supported by this engine's config format.
            return value == 0 || value >= 4 && value <= 129 || value >= 133 && value <= 164
                || value >= 176 && value <= 221 || value >= 224 && value <= 231 || value >= 257 && value <= 286;
        }
        static void CopyControls(XmlElement root, XmlElement target, string section) {
            var candidates = root.ChildNodes.OfType<XmlElement>().Where(el => el.Name == section).ToArray();
            if (candidates.Length != 1) return;
            var source = candidates[0];
            var controls = target.OwnerDocument.CreateElement(section);
            bool gamepad = section == "GamepadControls";
            foreach (string action in Actions) foreach (string slot in new[]{"primary", "secondary"}) {
                string key = action + "." + slot, name = source.GetAttribute(key + "_name");
                int code;
                if (!Int32.TryParse(source.GetAttribute(key), NumberStyles.AllowLeadingSign, Invariant, out code)
                    || (gamepad ? code < 0 || code > 64 : !(KeyCode(code) || code >= -8 && code <= -1))
                    || !BindingName(name) || code != 0 && name.Length == 0
                    || (gamepad && code != 0 && !name.StartsWith("$$input_xbox", StringComparison.Ordinal))) continue;
                controls.SetAttribute(key, code.ToString(Invariant));
                controls.SetAttribute(key + "_name", name);
            }
            foreach (string key in new[]{"gamepad_analog_buttons_threshold", "gamepad_analog_sticks_threshold"})
                Copy(source, controls, key, value => Number(value, 0, 1));
            if (controls.HasAttributes) target.AppendChild(controls);
        }
        internal static void PopulateConfig(XmlElement source, XmlElement output) {
            if (source == null) return;
            foreach (string name in Toggles) Copy(source, output, name, Toggle);
            foreach (string name in new[]{"audio_music_volume", "audio_effects_volume", "screenshake_intensity",
                                         "rendering_cosmetic_particle_count_coeff", "rendering_teleport_flash_brightness",
                                         "joystick_rumble_intensity"})
                Copy(source, output, name, value => Number(value, 0, 1));
            foreach (string name in new[]{"rendering_brightness_delta", "rendering_contrast_delta", "rendering_gamma_delta"})
                Copy(source, output, name, value => Number(value, -1, 1));
            Copy(source, output, "language", Locales.Contains);
            Copy(source, output, "vsync", v => v == "-1" || Toggle(v));
            Copy(source, output, "framerate", v => v == "30" || v == "60" || v == "120" || v == "144" || v == "240");
            CopyControls(source, output, "KeyboardControls");
            CopyControls(source, output, "GamepadControls");
        }
        internal static XmlElement Select(string ownedSource, string normalConfig, out string normalRoot) {
            string portable = Path.Combine(ownedSource, "save_shared", "config.xml");
            var profile = ReadConfig(portable);
            if (profile != null) { normalRoot = ownedSource; return profile; }
            normalRoot = Path.GetDirectoryName(Path.GetDirectoryName(normalConfig));
            return ReadConfig(normalConfig);
        }

        // Owned Noita format 14 saves the mod settings table with an eight-byte
        // length envelope and a FastLZ block. Decode into bounded memory. Only
        // the exact, typed local Ping setting can cross the profile boundary.
        internal static byte[] Decode(byte[] file) {
            if (file.Length < 10 || file.Length > MaximumPreferences || BitConverter.ToUInt32(file, 0) != file.Length - 8)
                throw new InvalidDataException("Invalid mod preference envelope.");
            uint size = BitConverter.ToUInt32(file, 4);
            int level = (file[8] >> 5) + 1;
            if (size < 8 || size > MaximumPreferences || level > 2) throw new InvalidDataException("Unsupported mod preference envelope.");
            byte[] output = new byte[size];
            int input = 8, written = 0;
            Func<int> read = () => {
                if (input >= file.Length) throw new EndOfStreamException();
                return file[input++];
            };
            while (input < file.Length) {
                int control = input == 8 ? read() & 31 : read();
                if (control < 32) {
                    int length = control + 1;
                    if (input + length > file.Length || written + length > output.Length) throw new InvalidDataException("Invalid preference literal.");
                    Buffer.BlockCopy(file, input, output, written, length);
                    input += length; written += length;
                    continue;
                }
                int count = (control >> 5) + 2;
                if (count == 9) {
                    int extra;
                    do {
                        extra = read();
                        count += extra;
                        if (count > output.Length - written) throw new InvalidDataException("Invalid preference match length.");
                    } while (level == 2 && extra == 255);
                }
                int low = read();
                int offset = (control & 31) * 256 + low + 1;
                if (level == 2 && offset == 8192) offset = 8192 + 256 * read() + read();
                if (offset > written || written + count > output.Length) throw new InvalidDataException("Invalid preference match.");
                for (int i = 0; i < count; i++) {
                    output[written] = output[written - offset];
                    written++;
                }
            }
            if (written != output.Length) throw new InvalidDataException("Incomplete preference data.");
            return output;
        }
        static int ReadInt(BinaryReader reader) {
            uint value = 0;
            for (int i = 0; i < 4; i++) value = (value << 8) | reader.ReadByte();
            return unchecked((int)value);
        }
        static string ReadString(BinaryReader reader) {
            int length = ReadInt(reader);
            if (length < 0 || length > 16384 || length > reader.BaseStream.Length - reader.BaseStream.Position)
                throw new InvalidDataException("Invalid preference string.");
            return StrictUtf8.GetString(reader.ReadBytes(length));
        }
        static string ReadValue(BinaryReader reader, int kind) {
            switch (kind) {
                case 0: return null;
                case 1:
                    int boolean = ReadInt(reader);
                    if (boolean != 0 && boolean != 1) throw new InvalidDataException("Invalid preference boolean.");
                    return null;
                case 2: reader.ReadDouble(); return null; // consume the stored eight-byte number
                case 3: return ReadString(reader);
                default: throw new InvalidDataException("Unknown preference type.");
            }
        }
        static bool PingCode(string text) {
            int code;
            return text != null && Int32.TryParse(text, NumberStyles.None, Invariant, out code)
                && code.ToString(Invariant) == text && KeyCode(code);
        }
        internal static string ReadPing(string root) {
            try {
                string path = Playtest.Child(root, "save00/mod_settings.bin");
                if (!File.Exists(path) || new FileInfo(path).Length > MaximumPreferences) return null;
                using (var reader = new BinaryReader(new MemoryStream(Decode(File.ReadAllBytes(path)), false))) {
                    if (ReadInt(reader) != 0) return null;
                    int count = ReadInt(reader);
                    if (count < 0 || count > 4096) return null;
                    string chosen = null;
                    bool found = false;
                    for (int i = 0; i < count; i++) {
                        string key = ReadString(reader);
                        int current = ReadInt(reader), next = ReadInt(reader);
                        string a = ReadValue(reader, current), b = ReadValue(reader, next);
                        if (key != Ping) continue;
                        if (found) return null;
                        found = true;
                        if (next == 3 && PingCode(b)) chosen = b;
                        else if (current == 3 && PingCode(a)) chosen = a;
                    }
                    return reader.BaseStream.Position == reader.BaseStream.Length ? chosen : null;
                }
            } catch (IOException) { return null; }
            catch (InvalidDataException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
            catch (DecoderFallbackException) { return null; }
        }
        static void WriteInt(BinaryWriter writer, int value) {
            for (int shift = 24; shift >= 0; shift -= 8) writer.Write((byte)(value >> shift));
        }
        static void WriteString(BinaryWriter writer, string text) {
            byte[] bytes = StrictUtf8.GetBytes(text);
            WriteInt(writer, bytes.Length); writer.Write(bytes);
        }
        internal static void WriteFreshModSettings(string game, string ping) {
            bool importPing = PingCode(ping);
            byte[] raw;
            using (var buffer = new MemoryStream())
            using (var writer = new BinaryWriter(buffer, StrictUtf8)) {
                WriteInt(writer, 0); WriteInt(writer, FreshRules.Length + (importPing ? 1 : 0));
                foreach (string key in FreshRules) {
                    WriteString(writer, key);
                    WriteInt(writer, 1); WriteInt(writer, 1);
                    WriteInt(writer, 1); WriteInt(writer, 1);
                }
                if (importPing) {
                    WriteString(writer, Ping);
                    WriteInt(writer, 3); WriteInt(writer, 3);
                    WriteString(writer, ping); WriteString(writer, ping);
                }
                writer.Flush(); raw = buffer.ToArray();
            }
            // A small literal-only level-one block avoids any native codec.
            using (var file = new FileStream(Playtest.Child(game, "save00/mod_settings.bin"), FileMode.CreateNew, FileAccess.Write, FileShare.None))
            using (var writer = new BinaryWriter(file, StrictUtf8)) {
                writer.Write(raw.Length + (raw.Length + 31) / 32);
                writer.Write(raw.Length);
                for (int offset = 0; offset < raw.Length; offset += 32) {
                    int span = Math.Min(32, raw.Length - offset);
                    writer.Write((byte)(span - 1));
                    writer.Write(raw, offset, span);
                }
                writer.Flush(); file.Flush(true);
            }
        }
    }
}
