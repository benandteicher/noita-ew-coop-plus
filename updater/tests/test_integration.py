"""Disposable compiled-launcher and HTTPS integration. Never starts an official binary.

Uses Windows/.NET certificate APIs for an ephemeral local TLS fixture. The test
harness alone accepts its certificate; shipped sources retain normal TLS checks.
"""
from pathlib import Path
import hashlib
import http.server
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / r"Microsoft.NET\Framework64\v4.0.30319\csc.exe"
SHA = lambda data: hashlib.sha256(data).hexdigest()


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="CoopPlus integration Ω ")
        cls.base = Path(cls.temp.name)
        cls.bin = cls.base / "bin"
        cls.bin.mkdir()
        refs = ["/r:System.Web.Extensions.dll", "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll"]
        def compile(name, files, main=None):
            args = [str(CSC), "/nologo", "/optimize+", "/out:" + str(cls.bin / name), *refs]
            if main:
                args += ["/main:" + main]
            subprocess.run(args + [str(ROOT / f) for f in files], check=True, capture_output=True)
        compile("CoopPlus.exe", ["updater/Bootstrap.cs"])
        compile("CoopUpdater.exe", ["updater/Engine.cs", "updater/Program.cs", "updater/Setup.cs", "updater/NoitaDetection.cs", "updater/ProxyHandoff.cs"])
        compile("harness.exe", ["updater/Engine.cs", "updater/Program.cs", "updater/Setup.cs", "updater/NoitaDetection.cs", "updater/ProxyHandoff.cs", "updater/tests/IntegrationHarness.cs"], "IntegrationHarness")
        compile("dummy.exe", ["updater/tests/DummyProxy.cs"])
        cls.routes = {}
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                code, data, declared = cls.routes.get(self.path, (404, b"missing", None))
                self.send_response(code)
                self.send_header("Content-Length", str(len(data) if declared is None else declared))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                self.close_connection = True
            def log_message(self, *args):
                pass
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        script = r'''
$rsa=[System.Security.Cryptography.RSA]::Create(2048)
$req=[System.Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=localhost',$rsa,[System.Security.Cryptography.HashAlgorithmName]::SHA256,[System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
$cert=$req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),[DateTimeOffset]::UtcNow.AddDays(1))
[IO.File]::WriteAllText($env:COOPPLUS_CERT,$cert.ExportCertificatePem())
[IO.File]::WriteAllText($env:COOPPLUS_KEY,$rsa.ExportPkcs8PrivateKeyPem())
'''
        cert, key = cls.base / "cert.pem", cls.base / "key.pem"
        subprocess.run(["pwsh", "-NoProfile", "-Command", script], check=True, capture_output=True,
                       env={**os.environ, "COOPPLUS_CERT": str(cert), "COOPPLUS_KEY": str(key)})
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "https://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def fixture(self, name, build=1):
        folder = self.base / name
        root, game = folder / "Coop+ Ω & launcher", folder / "Noita 漢 game"
        (root / "current/updater").mkdir(parents=True)
        game.mkdir(parents=True)
        (game / "noita.exe").write_bytes(b"NOT A GAME EXECUTABLE")
        shutil.copyfile(self.bin / "CoopPlus.exe", root / "CoopPlus.exe")
        shutil.copyfile(self.bin / "CoopUpdater.exe", root / "current/updater/CoopUpdater.exe")
        package, manifest = self.payload(folder, build)
        (root / "seed").mkdir()
        (root / "seed/package.zip").write_bytes(package)
        (root / "seed/release.json").write_text(json.dumps(manifest), encoding="utf8")
        (root / "coopplus.json").write_text(json.dumps(dict(schema=1, gameDir=str(game), settingsPath=str(root / "user/proxy.ron"), saveStatePath=str(root / "user/save_state"))), encoding="utf8")
        return root, game

    def payload(self, folder, build):
        folder.mkdir(exist_ok=True, parents=True)
        entries = {"mod/quant.ew/mod.xml": b"<Mod/>", "mod/quant.ew/init.lua": b"-- dummy runtime\n",
                   "mod/quant.ew/ewext.dll": b"dummy", "mod/quant.ew/NoitaPatcher/noitapatcher.dll": b"dummy",
                   "mod/quant.ew/NoitaPatcher/noitapatcher/nsew/nsew_native.dll": b"dummy", "proxy/steam_api64.dll": b"dummy",
                   "proxy/noita_proxy.exe": (self.bin / "dummy.exe").read_bytes(),
                   "updater/CoopUpdater.exe": (self.bin / "CoopUpdater.exe").read_bytes() + (b"" if build == 1 else b"build-overlay-" + str(build).encode()),
                   "mod/quant.ew/build.txt": str(build).encode()}
        p = folder / ("payload" + str(build) + ".zip")
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
            for n, data in entries.items():
                z.writestr(n, data)
        package = p.read_bytes()
        tag = f"coopplus-v1.9.14.{build}-b{build}"
        m = dict(schema=1, channel="stable", version=f"1.9.14.{build}", build=build, minBootstrap=1,
                 sourceRevision="a" * 40, officialProxyVersion="1.6.8",
                 package=dict(name="package.zip", size=len(package), sha256=SHA(package), url=self.url + "/download/" + tag + "/package.zip"),
                 files=[dict(path=n, size=len(d), sha256=SHA(d)) for n, d in entries.items()])
        return package, m

    def run_boot(self, root, args=(), text="", env=None):
        return subprocess.run([str(root / "CoopPlus.exe"), *args], input=text, capture_output=True, text=True,
                              encoding="utf8", errors="replace", env=env, timeout=65)

    def setup(self, root, game):
        result = self.run_boot(root, ["--coop-check"], str(game) + "\n\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        config = json.loads((root / "coopplus.json").read_text(encoding="utf-8-sig"))
        config["settingsPath"] = str(root / "user/proxy.ron")
        config["saveStatePath"] = str(root / "user/save_state")
        (root / "coopplus.json").write_text(json.dumps(config), encoding="utf8")
        return result

    def register(self, m, package, **overrides):
        tag = "coopplus-v" + m["version"] + "-b" + str(m["build"])
        base = "/download/" + tag + "/"
        release = dict(draft=False, prerelease=False, tag_name=tag,
                       assets=[dict(name="release.json", browser_download_url=self.url + base + "release.json"),
                               dict(name="package.zip", browser_download_url=self.url + base + "package.zip", size=len(package))])
        release.update(overrides)
        self.routes["/latest"] = (200, json.dumps(release).encode(), None)
        self.routes[base + "release.json"] = (200, json.dumps(m).encode(), None)
        self.routes[base + "package.zip"] = (200, package, None)
        return base

    def harness(self, args, env=None):
        return subprocess.run([str(self.bin / "harness.exe"), *map(str, args)], capture_output=True, text=True,
                              encoding="utf8", errors="replace", timeout=35, env=env)

    def test_bootstrap_first_setup_current_args_and_concurrency(self):
        root, game = self.fixture("bootstrap")
        self.setup(root, game)
        record = root / "record.json"
        args = ["--language", "日本語", "", 'quoted"value', "ends\\", "A&B", "--settings-path=" + str(root / "user/custom.ron")]
        env = {**os.environ, "COOPPLUS_RECORD": str(record), "COOPPLUS_SLEEP": "2500"}
        first = subprocess.Popen([str(root / "CoopPlus.exe"), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            until = time.monotonic() + 30
            while not record.exists() and time.monotonic() < until:
                time.sleep(.05)
            self.assertTrue(record.exists(), "dummy proxy did not start")
            second = self.run_boot(root)
            self.assertIn(second.returncode, (1, 3), second.stdout + second.stderr)
            data = json.loads(record.read_text(encoding="utf8"))
            self.assertEqual(data["args"][-len(args):], args)
            self.assertEqual(data["cwd"], str(root / "current/proxy"))
            self.assertEqual(data["skip"], "1")
            first.communicate(timeout=10)
            self.assertEqual(first.returncode, 0)
        finally:
            if first.poll() is None:
                first.terminate()
                first.communicate(timeout=5)

    def test_https_full_download_and_updater_redispatch(self):
        root, game = self.fixture("update")
        self.setup(root, game)
        old = (root / "current/updater/CoopUpdater.exe").read_bytes()
        package, manifest = self.payload(self.base / "new release", 10)
        self.register(manifest, package)
        result = self.harness(["run-feed", root, game, self.url + "/latest", self.url + "/download/", "check"])
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertNotEqual(old, (root / "current/updater/CoopUpdater.exe").read_bytes())
        result = self.run_boot(root, ["--coop-check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((game / "mods/quant.ew/build.txt").read_text(), "10")

    def test_offline_check_and_corrupt_download_keep_existing_usable(self):
        root, game = self.fixture("offline")
        self.setup(root, game)
        result = self.harness(["offline", root, game, "check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        package, manifest = self.payload(self.base / "corrupt release", 3)
        base = self.register(manifest, package)
        self.routes[base + "package.zip"] = (200, b"x" * len(package), None)
        result = self.harness(["run-feed", root, game, self.url + "/latest", self.url + "/download/", "check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Existing complete installation remains available", result.stdout)
        self.assertEqual((game / "mods/quant.ew/build.txt").read_text(), "1")

    def test_interrupted_https_download_and_draft_metadata_rejection(self):
        package, m = self.payload(self.base / "network", 4)
        base = self.register(m, package)
        self.routes[base + "package.zip"] = (200, package[:100], len(package))
        dest = self.base / "network/download.zip"
        result = self.harness(["feed", self.url + "/latest", self.url + "/download/", dest])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(dest.exists())
        self.assertFalse(list(dest.parent.glob("*.partial-*")))
        self.register(m, package, draft=True)
        result = self.harness(["feed", self.url + "/latest", self.url + "/download/", dest])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("approved stable", result.stderr)
        m["minBootstrap"] = 2
        self.register(m, package)
        result = self.harness(["feed", self.url + "/latest", self.url + "/download/", dest])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Co-op+ bootstrap 2", result.stderr)

    def test_bootstrap_recovers_when_current_is_absent(self):
        root, game = self.fixture("crash recovery")
        self.setup(root, game)
        ident = "a" * 32
        backup = root / "rollback" / ident
        backup.mkdir(parents=True)
        (root / "current").rename(backup / "current")
        (root / (".stage-" + ident) / "current").mkdir(parents=True)
        journal = dict(schema=1, root=str(root), game=str(game), id=ident, hadMod=True, hadCurrent=True, committed=False)
        (root / "install-journal.json").write_text(json.dumps(journal), encoding="utf8")
        result = self.run_boot(root, ["--coop-check"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Recovery finished", result.stdout)
        self.assertFalse((root / "install-journal.json").exists())
        self.assertTrue((root / "current/installed.json").exists())

    def test_forwarded_mutable_paths_and_different_game_rejected(self):
        root, game = self.fixture("argument safety")
        self.setup(root, game)
        for args in [["--settings-path", str(root / "current/config.ron")], ["--save-state-path=" + str(game / "mods/saves")], ["--exe-path", str(game / "other.exe")]]:
            result = self.run_boot(root, args)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_running_game_detection_does_not_stop_process(self):
        root, game = self.fixture("running detection")
        self.setup(root, game)
        dummy = self.base / "dummy running game"
        dummy.mkdir()
        shutil.copyfile(self.bin / "dummy.exe", dummy / "noita.exe")
        record = dummy / "started.json"
        process = subprocess.Popen([str(dummy / "noita.exe")], env={**os.environ, "COOPPLUS_SLEEP": "4000", "COOPPLUS_RECORD": str(record)})
        try:
            until = time.monotonic() + 3
            while not record.exists() and time.monotonic() < until:
                time.sleep(.03)
            self.assertTrue(record.exists())
            result = self.run_boot(root, ["--coop-check"])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Close Noita", result.stdout + result.stderr)
            self.assertIsNone(process.poll(), "updater stopped the dummy game process")
            process.wait(timeout=8)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
