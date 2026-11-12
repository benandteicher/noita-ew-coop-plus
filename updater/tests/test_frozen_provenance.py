"""Narrow public provenance regression; no builds/network/game processes."""
import importlib.util
from pathlib import Path
import sys
import unittest
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("release_builder", Path(__file__).resolve().parents[1] / "release_builder.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

class FrozenProvenanceTests(unittest.TestCase):
    def sample(self):
        return {"files": [{"path": "proxy/noita_proxy.exe", "source": r"C:\private-build\proxy.exe", "sha256": "a" * 64}], "startupBuildProvenance": {"binary": r"C:\private-build\proxy.exe", "sha256": "a" * 64}, "origin": {"gameplay": "Lua differs only by old marker", "startupSessionCorrection": {"scope": "Current new startup/session Lua hooks"}}}
    def test_nested_build_path_is_logical_and_raw_receipt_unchanged(self):
        raw = self.sample()
        public = builder.public_frozen_metadata(raw)
        self.assertNotIn("source", public["files"][0])
        self.assertEqual(public["startupBuildProvenance"]["binary"], "proxy/noita_proxy.exe")
        self.assertEqual(public["startupBuildProvenance"]["sha256"], raw["startupBuildProvenance"]["sha256"])
        self.assertEqual(raw["startupBuildProvenance"]["binary"], r"C:\private-build\proxy.exe")
    def test_historical_baseline_is_explicit_and_current_wrappers_declared(self):
        public = builder.public_frozen_metadata(self.sample())
        self.assertEqual(public["origin"]["historicalBaselineGameplay"], "Lua differs only by old marker")
        self.assertIn("Lua startup/run-session", public["origin"]["gameplay"])
    def test_unknown_nested_absolute_path_refuses_publication(self):
        raw = self.sample()
        raw["newNestedReceipt"] = {"path": r"C:\private-build\log.txt"}
        with self.assertRaisesRegex(ValueError, "Private absolute"):
            builder.public_frozen_metadata(raw)
    def test_quoted_and_equals_embedded_build_paths_refuse_without_rejecting_https(self):
        for command in [r"cargo --manifest-path=C:\private\Cargo.toml", r'cargo --manifest-path="C:\private\Cargo.toml"', r'copy "\\server\private\input"']:
            raw = self.sample()
            raw["buildCommands"] = {"proxy": command}
            with self.assertRaisesRegex(ValueError, "Private absolute"):
                builder.public_frozen_metadata(raw)
        raw = self.sample()
        raw["licenseUrl"] = "https://example.invalid/license"
        self.assertEqual(builder.public_frozen_metadata(raw)["licenseUrl"], raw["licenseUrl"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
