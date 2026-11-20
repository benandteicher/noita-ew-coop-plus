"""Packaging boundary checks only; does not start the game, proxy or full suite."""
import argparse
import contextlib
import importlib.util
import io
import json
import pathlib
import tempfile
import unittest
import zipfile

UPDATER = pathlib.Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, UPDATER / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = load("release_builder", "release_builder.py")
publication = load("prepare_publication", "prepare-publication.py")


class PackagingTests(unittest.TestCase):
    def test_safe_archive_paths(self):
        for name in ("mod/quant.ew/mod.xml", "proxy/THIRD-PARTY-NOTICES.txt", "路径/space here.txt"):
            self.assertTrue(builder.safe_name(name), name)
        for name in ("../escape", "/absolute", "C:/escape", "a\\b", "a//b", "a/./b", "a/CON.txt", "a/LPT1", "a/trailing.", "a/trailing "):
            self.assertFalse(builder.safe_name(name), name)

    def test_baseline_hash_rejects_unapproved_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.zip"
            path.write_bytes(b"not R14")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                builder.read_baseline(path)

    def test_complete_candidate_inventory_baseline_parity_and_seed(self):
        baseline = builder.ROOT / "dist/Noita-EW-Coop-Plus-v1.9-R14-playtest-win.zip"
        if not baseline.exists():
            self.skipTest("Pinned historical R14 archive not available on this build computer")
        with tempfile.TemporaryDirectory(prefix="Co-op+ 检查 ") as tmp:
            root = pathlib.Path(tmp)
            (root / "bootstrap.exe").write_bytes(b"dummy bootstrap: never executed")
            (root / "updater.exe").write_bytes(b"dummy updater: never executed")
            args = argparse.Namespace(source_revision="a" * 40, version="1.9.14.1", build=1,
                                      repository=builder.REPOSITORY, runtime_source="r14", baseline=str(baseline),
                                      bootstrap=str(root / "bootstrap.exe"), updater=str(root / "updater.exe"), output=str(root / "release"))
            with contextlib.redirect_stdout(io.StringIO()):
                result = builder.build(args)
            self.assertTrue(result["runtimeIdenticalToR14"])
            output = root / "release"
            manifest = json.loads((output / "release.json").read_text(encoding="utf-8"))
            with zipfile.ZipFile(output / manifest["package"]["name"]) as archive:
                self.assertEqual(set(archive.namelist()), {f["path"] for f in manifest["files"]})
                for entry in manifest["files"]:
                    data = archive.read(entry["path"])
                    self.assertEqual((len(data), builder.sha(data)), (entry["size"], entry["sha256"]))
                old, _ = builder.read_baseline(baseline)
                mod = {n: archive.read(n) for n in archive.namelist() if n.startswith("mod/quant.ew/")}
                self.assertEqual(mod, {n: data for n, data in old.items() if n.startswith("mod/quant.ew/")})
                self.assertEqual(len(mod), 580)
                self.assertEqual(archive.read("proxy/LICENSE-MIT"), old["LICENSE-MIT"])
                self.assertEqual(archive.read("proxy/THIRD-PARTY-NOTICES.txt"), old["THIRD-PARTY-NOTICES.txt"])
            with zipfile.ZipFile(next(output.glob("*bootstrap-win.zip"))) as archive:
                self.assertEqual(archive.read("seed/release.json"), (output / "release.json").read_bytes())
                self.assertEqual(archive.read("seed/package.zip"), (output / manifest["package"]["name"]).read_bytes())
            for line in (output / "SHA256SUMS.txt").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(builder.sha((output / name).read_bytes()), digest)

    def test_checkout_complete_tree_removes_deleted_old_source(self):
        baseline = {"mod/quant.ew/old.lua": b"delete me", "mod/quant.ew/ewext.dll": b"official", "mod/quant.ew/files/version.lua": b"1.6.8",
                    "launcher/noita_proxy.exe": b"official proxy", **{n: n.encode() for n in builder.LICENSE_NAMES}}
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "quant.ew").mkdir()
            (root / "quant.ew/mod.xml").write_bytes(b"current")
            files = builder.payload_entries(baseline, b"updater", "checkout", root)
            self.assertNotIn("mod/quant.ew/old.lua", files)
            self.assertEqual(files["mod/quant.ew/ewext.dll"], b"official")
            self.assertEqual(files["mod/quant.ew/mod.xml"], b"current")

    def test_publication_excludes_personal_runtime_and_inherited_release_workflow(self):
        for name in (".github/workflows/release.yml", ".vscode/settings.json", "NOITA_EW_PROJECT_RECORD.md", "diagnostics/probe.lua", "verification/log.txt", "target/noita.exe", "dist/package.zip", "quant.ew/save00/world.bin", "updater/__pycache__/thing.pyc", "scripts/build_ping_review.py"):
            self.assertFalse(publication.intended_source(name), name)
        for name in ("quant.ew/init.lua", "noita_proxy/src/cli.rs", "updater/Engine.cs", "updater/tests/test_packaging.py", "LICENSE-MIT", "scripts/ci_make_archives.py"):
            self.assertTrue(publication.intended_source(name), name)

    def test_private_key_and_token_heuristic(self):
        for content in (b"-----BEGIN " + b"PRIVATE KEY-----", b"ghp_" + b"a" * 36, b"github_pat_" + b"b" * 44):
            self.assertTrue(any(p.search(content) for p in publication.SECRET_PATTERNS))


if __name__ == "__main__":
    unittest.main()
