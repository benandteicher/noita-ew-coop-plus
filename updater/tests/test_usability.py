"""Focused Windows fixtures: production setup/launch code, windowed dummy only."""
from pathlib import Path
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CSC = r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
SOURCES = ["Engine.cs", "Program.cs", "Setup.cs", "NoitaDetection.cs", "ProxyHandoff.cs"]
SHA = lambda b: hashlib.sha256(b).hexdigest()
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]

def alive(pid):
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)

class UsabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="CoopPlus usability Ω ")
        cls.base = Path(cls.temp.name)
        cls.bin = cls.base / "bin"
        cls.bin.mkdir()
        refs = ["/r:System.Web.Extensions.dll", "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"]
        def compile(name, sources, extra=()):
            result = subprocess.run([CSC, "/nologo", "/optimize+", "/out:" + str(cls.bin / name), *refs, *extra, *[str(ROOT / s) for s in sources]], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
        compile("CoopPlus.exe", ["updater/Bootstrap.cs"])
        compile("CoopUpdater.exe", ["updater/" + n for n in SOURCES])
        compile("harness.exe", ["updater/" + n for n in SOURCES] + ["updater/tests/UsabilityHarness.cs"], ["/main:UsabilityHarness"])
        compile("dummy.exe", ["updater/tests/WindowedDummyProxy.cs"], ["/target:winexe"])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def fixture(self, name, old_bootstrap=False):
        root, game = self.base / name / "Launcher & co-op", self.base / name / "Noita 漢 game"
        (root / "current/updater").mkdir(parents=True)
        game.mkdir(parents=True)
        (game / "noita.exe").write_bytes(b"not a game executable")
        if old_bootstrap:
            with zipfile.ZipFile(ROOT / "dist/updater-1.9.14.1-build1/Co-op-Plus-1.9.14.1-build1-bootstrap-win.zip") as z:
                (root / "CoopPlus.exe").write_bytes(z.read("CoopPlus.exe"))
        else:
            shutil.copyfile(self.bin / "CoopPlus.exe", root / "CoopPlus.exe")
        shutil.copyfile(self.bin / "CoopUpdater.exe", root / "current/updater/CoopUpdater.exe")
        state = self.base / name / "Portable state Ω"
        (state / "save_state").mkdir(parents=True)
        (state / "proxy.ron").write_bytes(b"preserve settings sentinel")
        (state / "save_state/run.bin").write_bytes(b"preserve paired state")
        config = dict(schema=1, gameDir=str(game), settingsPath=str(state / "proxy.ron"), saveStatePath=str(state / "save_state"))
        (root / "coopplus.json").write_text(json.dumps(config), encoding="utf8")
        entries = {"mod/quant.ew/mod.xml": b"<Mod/>", "mod/quant.ew/init.lua": b"-- fixture",
                   "mod/quant.ew/ewext.dll": b"fixture", "mod/quant.ew/NoitaPatcher/noitapatcher.dll": b"fixture",
                   "mod/quant.ew/NoitaPatcher/noitapatcher/nsew/nsew_native.dll": b"fixture",
                   "proxy/steam_api64.dll": b"fixture", "proxy/noita_proxy.exe": (self.bin / "dummy.exe").read_bytes(),
                   "updater/CoopUpdater.exe": (self.bin / "CoopUpdater.exe").read_bytes()}
        (root / "seed").mkdir()
        with zipfile.ZipFile(root / "seed/package.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for n, data in entries.items(): z.writestr(n, data)
        package = (root / "seed/package.zip").read_bytes()
        manifest = dict(schema=1, channel="stable", version="1.9.14.2", build=2, minBootstrap=1, sourceRevision="a" * 40, officialProxyVersion="1.6.8",
                        package=dict(name="package.zip", size=len(package), sha256=SHA(package), url="https://github.com/benandteicher/noita-ew-coop-plus/releases/download/fixture/package.zip"),
                        files=[dict(path=n, size=len(data), sha256=SHA(data)) for n, data in entries.items()])
        (root / "seed/release.json").write_text(json.dumps(manifest), encoding="utf8")
        result = self.boot(root, ["--coop-check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Installing bundled setup candidate 1.9.14.2 (build 2)", result.stdout)
        self.assertFalse(json.loads((root / "coopplus.json").read_text(encoding="utf-8-sig")).get("pendingInstall", False))
        return root, game, state

    def boot(self, root, args, env=None, console=False):
        return subprocess.run([str(root / "CoopPlus.exe"), *args], input="", capture_output=True, text=True, encoding="utf8", env=env, timeout=50,
                              creationflags=subprocess.CREATE_NEW_CONSOLE if console else 0)

    def wait_child(self, pid):
        until = time.monotonic() + 10
        while alive(pid) and time.monotonic() < until: time.sleep(.05)
        self.assertFalse(alive(pid), "Owned dummy proxy did not finish")

    def test_graphical_handoff_old_and_new_bootstrap_child_survives_repeat_blocked(self):
        for old in (False, True):
            with self.subTest(publishedBuild1Bootstrap=old):
                root, game, state = self.fixture("handoff" + str(old), old)
                record = root / "record.json"
                args = ["--language", "日本語", "", 'quote"value', "tail\\", "A&B"]
                env = {**os.environ, "COOPPLUS_RECORD": str(record), "COOPPLUS_SLEEP": "4500"}
                result = self.boot(root, args, env, console=True)
                data = json.loads(record.read_text(encoding="utf8")) if record.exists() else None
                try:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertTrue(alive(data["pid"]), "Proxy ended with bootstrap")
                    self.assertFalse(data["consoleAttached"], "Proxy attached to launcher console")
                    self.assertEqual(data["args"][-len(args):], args)
                    self.assertEqual(data["cwd"], str(root / "current/proxy"))
                    self.assertEqual(data["skip"], "1")
                    self.assertIn(str(state / "proxy.ron"), data["args"])
                    second = self.boot(root, ["--coop-check"])
                    self.assertNotEqual(second.returncode, 0)
                    self.assertIn("Close Noita", second.stdout + second.stderr)
                    self.assertTrue(alive(data["pid"]), "Second launch stopped child")
                finally:
                    if data: self.wait_child(data["pid"])

    def test_immediate_failure_is_reported_without_successful_handoff(self):
        root, game, state = self.fixture("failure")
        result = self.boot(root, [], {**os.environ, "COOPPLUS_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("closed during startup (exit 7)", result.stdout + result.stderr)
        self.assertEqual((state / "proxy.ron").read_bytes(), b"preserve settings sentinel")

    def test_cli_waits_and_preserves_console_mode(self):
        root, game, state = self.fixture("cli")
        record = root / "record.json"
        start = time.monotonic()
        result = self.boot(root, ["--host", "steam"], {**os.environ, "COOPPLUS_RECORD": str(record), "COOPPLUS_SLEEP": "2200"}, console=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(time.monotonic() - start, 2.0)
        self.assertIn("requested console mode", result.stdout)
        data = json.loads(record.read_text(encoding="utf8"))
        self.assertTrue(data["consoleAttached"])
        self.assertFalse(alive(data["pid"]))

    def test_check_and_help_do_not_launch_proxy(self):
        root, game, state = self.fixture("check")
        record = root / "record.json"
        for args in (["--coop-check"], ["--coop-help"]):
            result = self.boot(root, args, {**os.environ, "COOPPLUS_RECORD": str(record)})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(record.exists())

    def test_retarget_installs_complete_mod_and_preserves_state(self):
        root, game, state = self.fixture("retarget")
        target = self.base / "retarget/Replacement Ω"
        target.mkdir()
        (target / "noita.exe").write_bytes(b"not executable")
        (target / "mods/quant.ew").mkdir(parents=True)
        (target / "mods/quant.ew/obsolete.lua").write_bytes(b"old mod")
        config = json.loads((root / "coopplus.json").read_text(encoding="utf-8-sig"))
        config.update(gameDir=str(target), pendingInstall=True)
        (root / "coopplus.json").write_text(json.dumps(config), encoding="utf8")
        result = self.boot(root, ["--coop-check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((target / "mods/quant.ew/mod.xml").exists())
        self.assertFalse((target / "mods/quant.ew/obsolete.lua").exists())
        self.assertTrue((game / "mods/quant.ew/mod.xml").exists())
        self.assertEqual((state / "save_state/run.bin").read_bytes(), b"preserve paired state")
        self.assertFalse(json.loads((root / "coopplus.json").read_text(encoding="utf-8-sig"))["pendingInstall"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
