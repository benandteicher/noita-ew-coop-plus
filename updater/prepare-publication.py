"""Create a reviewed source snapshot in a new empty directory; never push or copy history.

Run before creating a public repository. Output contains intended project source,
licenses and updater tests, plus a path/hash inventory. Historical diagnostics,
local profiles, saves, logs, packages, game files, and inherited CI are omitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT_FILES = {".gitignore", "Justfile", "LICENSE-APACHE", "LICENSE-MIT", "THIRD-PARTY-NOTICES.txt", "contributing.md", "flake.lock", "flake.nix"}
SOURCE_ROOTS = {"quant.ew", "noita_proxy", "ewext", "noita_api", "shared", "ew_api_example", "docs", "nix", "redist", "extra", "updater"}
BUILD_SCRIPTS = {"check_pre_ci.py", "ci_make_archives.py", "ci_version.py", "extract_steam_redist.py", "make_release_assets.py", "mat_colors.py", "parse_components.py", "parse_lua_api.py", "perf.sh", "prepare_release.py", "start-linux.sh", ".gitignore"}
DENIED_COMPONENTS = {".git", ".github", ".vscode", ".idea", "__pycache__", "node_modules", "target", "dist", "diagnostics", "verification", "save00", "save_shared", "save_state", "tmp"}
DENIED_SUFFIXES = {".log", ".bin", ".wak", ".pak", ".zip", ".7z", ".mp4", ".mkv", ".avi", ".exe", ".pyc", ".dmp"}
DENIED_NAMES = {".env", "credentials", "credentials.json", "hosts.yml", "settings.ron", "proxy-settings.ron", "mod_settings.bin", "logger.txt"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
)


def intended_source(name: str) -> bool:
    parts = pathlib.PurePosixPath(name).parts
    if not parts or any(p.casefold() in DENIED_COMPONENTS for p in parts):
        return False
    if pathlib.PurePosixPath(name).suffix.casefold() in DENIED_SUFFIXES or parts[-1].casefold() in DENIED_NAMES:
        return False
    return name in ROOT_FILES or parts[0] in SOURCE_ROOTS or (parts[0] == "tests" and parts[-1].endswith((".py", ".lua"))) or (len(parts) == 2 and parts[0] == "scripts" and parts[1] in BUILD_SCRIPTS)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(root=ROOT) -> tuple[dict[str, bytes], dict]:
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root).decode("utf-8").split("\0")
    selected = {}
    excluded = 0
    for name in sorted(set(n for n in names if n)):
        if not intended_source(name):
            excluded += 1
            continue
        path = root / name
        if not path.exists():
            continue
        if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError(f"Reparse point in intended publication: {name}")
        if not path.is_file():
            raise ValueError(f"Unexpected source object: {name}")
        data = path.read_bytes()
        if len(data) > 32 * 1024 * 1024:
            raise ValueError(f"Unexpected large source file: {name}")
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Potential credential found; inspect locally before publication: {name}")
        selected[name] = data
    if not all(n in selected for n in ("LICENSE-MIT", "LICENSE-APACHE", "quant.ew/mod.xml", "updater/Engine.cs", "updater/Program.cs", "updater/Bootstrap.cs")):
        raise ValueError("Required source files are incomplete")
    if "THIRD-PARTY-NOTICES.txt" not in selected:
        baseline = root / "dist/Noita-EW-Coop-Plus-v1.9-R14-playtest-win.zip"
        if sha(baseline.read_bytes()) != "a64f055093e5188096ca4278f24e49acbf50d4d96c5399f08ce24521c8052f8e":
            raise ValueError("Cannot recover third-party notices from the pinned R14 package")
        with zipfile.ZipFile(baseline) as archive:
            names = [n for n in archive.namelist() if n.endswith("/THIRD-PARTY-NOTICES.txt")]
            if len(names) != 1:
                raise ValueError("Unexpected third-party notices structure")
            selected["THIRD-PARTY-NOTICES.txt"] = archive.read(names[0])
    # Keep original project attribution, with a public-facing Co-op+ introduction.
    selected["UPSTREAM-README.md"] = (root / "README.md").read_bytes().split(b"## Noita Entangled Worlds - Play Noita not alone", 1)[-1]
    selected["UPSTREAM-README.md"] = b"# Noita Entangled Worlds - Play Noita not alone\n" + selected["UPSTREAM-README.md"]
    selected["README.md"] = b"""# Noita Entangled Worlds: Co-op+

Personal Windows co-op fork of [IntQuant/noita_entangled_worlds](https://github.com/IntQuant/noita_entangled_worlds).
The first updater candidate wraps the unchanged official v1.6.8 proxy and preserves the frozen R14 mod runtime.

See [updater setup and review](updater/README.md) and [release workflow](updater/PUBLICATION.md).
Draft releases are for review and are not an anonymous update channel. Ordinary source commits never update clients.

This is a reviewed source snapshot, with no imported local history, credentials, game installation, saved games,
personal configuration, diagnostics, or inherited release workflow. Source and payload provenance is recorded
in PUBLICATION-INVENTORY.json and each release's provenance.json. The local canonical project record remains local.

Upstream MIT/Apache licenses, original attribution and source are retained. See LICENSE-MIT,
LICENSE-APACHE, [upstream README](UPSTREAM-README.md), and THIRD-PARTY-NOTICES.txt.
"""
    # Do not inherit local-only ignore rules that might suggest all artifacts were inspected.
    selected[".gitignore"] = b"target/\ndist/\n__pycache__/\n*.pyc\n*.log\n.env\n/quant.ew/ewext*.dll\n/quant.ew/files/system/player/tmp/\n"
    info = {"localHead": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip(),
            "r14RuntimeCheckpoint": "266b915c1e41c9c4c073d685f989b911864f5b6a",
            "snapshotPolicy": "Selected current source only; no local refs/history, inherited CI, canonical record, saves/config, diagnostics, logs, prior packages, or installed game/data archives.",
            "credentialPatternScan": "PASS: no matching private-key/GitHub-token/AWS-access-key patterns in selected files. This is a focused heuristic scan, not a general secret-audit guarantee.",
            "omittedFiles": excluded,
            "files": {name: {"size": len(data), "sha256": sha(data)} for name, data in sorted(selected.items())}}
    return selected, info


def prepare(output: pathlib.Path, root=ROOT) -> dict:
    if output.exists():
        raise ValueError("Publication destination must not exist; use a new isolated directory")
    entries, info = collect(root)
    output.mkdir(parents=True)
    for name, data in entries.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "PUBLICATION-INVENTORY.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    if {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} != set(entries) | {"PUBLICATION-INVENTORY.json"}:
        raise ValueError("Publication inventory verification failed")
    for name, data in entries.items():
        if (output / name).read_bytes() != data:
            raise ValueError(f"Publication byte parity failed: {name}")
    result = {"directory": str(output.resolve()), "sourceFiles": len(entries), "omittedFiles": info["omittedFiles"],
              "credentialPatternScan": "PASS", "inventoryByteVerification": "PASS", "gitInitializedOrPushed": False}
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    prepare(parser.parse_args().output)
