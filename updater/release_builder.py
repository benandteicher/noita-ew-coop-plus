"""Build complete updater releases; standard library only, no installation or launch.

The initial candidate reads every mod byte from the pinned R14 package. Future
approved checkout builds replace the entire source tree and retain only explicit
native/version dependencies from that baseline. No intermediate patches exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPOSITORY = "benandteicher/noita-ew-coop-plus"
BASELINE_SHA256 = "a64f055093e5188096ca4278f24e49acbf50d4d96c5399f08ce24521c8052f8e"
R14_CHECKPOINT = "266b915c1e41c9c4c073d685f989b911864f5b6a"
PROXY_SHA256 = "2bd212724f3b8e662339b23d5b5b13e32ea55c76bf4d725b139529fbd54c6985"
OFFICIAL_VERSION = "1.6.8"
LICENSE_NAMES = ("LICENSE-MIT", "LICENSE-APACHE", "THIRD-PARTY-NOTICES.txt")
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def safe_name(name: str) -> bool:
    parts = name.split("/")
    return bool(name) and "\\" not in name and ":" not in name and all(
        p not in ("", ".", "..") and not p.endswith((".", " "))
        and not re.match(r"(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)", p)
        for p in parts
    )


def write_zip(path: pathlib.Path, entries: dict[str, bytes]) -> None:
    if len({n.casefold() for n in entries}) != len(entries):
        raise ValueError("Case-insensitive duplicate archive paths")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(entries.items()):
            if not safe_name(name):
                raise ValueError(f"Unsafe package path: {name}")
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 6, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0  # plain Windows files, never Unix links
            info.external_attr = 0x20
            archive.writestr(info, data)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC verification failed")
        if set(archive.namelist()) != set(entries):
            raise ValueError("ZIP inventory mismatch")
        for name, data in entries.items():
            if archive.read(name) != data:
                raise ValueError(f"ZIP byte mismatch: {name}")


def read_baseline(path: pathlib.Path) -> tuple[dict[str, bytes], dict]:
    raw = path.read_bytes()
    if sha(raw) != BASELINE_SHA256:
        raise ValueError("Pinned R14 baseline archive hash mismatch")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("R14 baseline ZIP corruption")
        roots = {n.split("/")[0] for n in archive.namelist()}
        if len(roots) != 1:
            raise ValueError("Unexpected baseline structure")
        prefix = next(iter(roots)) + "/"
        baseline = {n[len(prefix):]: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
    manifest = json.loads(baseline["MANIFEST.json"])
    for name, digest in manifest["files"].items():
        if sha(baseline[name]) != digest:
            raise ValueError(f"Baseline manifest mismatch: {name}")
    identity = json.loads(baseline["BUILD-IDENTITY.json"])
    if identity["runtime_checkpoint"] != R14_CHECKPOINT:
        raise ValueError("Unexpected R14 source checkpoint")
    if sha(baseline["launcher/noita_proxy.exe"]) != PROXY_SHA256:
        raise ValueError("Official proxy identity mismatch")
    return baseline, identity


def payload_entries(baseline: dict[str, bytes], updater: bytes, runtime_source="r14", root=ROOT) -> dict[str, bytes]:
    entries = {n: data for n, data in baseline.items() if n.startswith("mod/quant.ew/")}
    if runtime_source == "checkout":
        # Old source/assets are not inherited: deleted source files disappear.
        dependencies = {
            n: data for n, data in entries.items()
            if n.lower().endswith((".dll", ".pdb")) or n == "mod/quant.ew/files/version.lua"
        }
        entries = dependencies
        modroot = root / "quant.ew"
        for path in sorted(modroot.rglob("*")):
            if path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
                raise ValueError(f"Reparse point in runtime source: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(modroot).as_posix()
            if "tmp" in path.relative_to(modroot).parts or path.suffix.lower() in (".log", ".bin", ".wak", ".zip", ".7z", ".exe"):
                raise ValueError(f"Unexpected mutable/archive/runtime file: {relative}")
            entries["mod/quant.ew/" + relative] = path.read_bytes()
    entries.update({"proxy/" + n[len("launcher/"):]: data for n, data in baseline.items() if n.startswith("launcher/")})
    entries.update({"proxy/" + n: baseline[n] for n in LICENSE_NAMES})
    entries["updater/CoopUpdater.exe"] = updater
    if not all(safe_name(n) for n in entries):
        raise ValueError("Unsafe payload path")
    if len(entries) > 20000 or sum(map(len, entries.values())) > MAX_EXPANDED:
        raise ValueError("Payload exceeds v1 format bounds")
    return entries


def build(args) -> dict:
    if args.repository != REPOSITORY:
        raise ValueError("Repository must be the approved personal repository")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_revision):
        raise ValueError("Source revision must be the exact 40-character publication commit")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", args.version) or any(int(x) > 2147483647 for x in args.version.split(".")):
        raise ValueError("Release version must contain four supported numeric components")
    if args.build < 1:
        raise ValueError("Release build must be a positive integer")
    output = pathlib.Path(args.output)
    if output.exists():
        raise ValueError("Output already exists; preserve previous packages")
    baseline, identity = read_baseline(pathlib.Path(args.baseline))
    entries = payload_entries(baseline, pathlib.Path(args.updater).read_bytes(), args.runtime_source)
    output.mkdir(parents=True)
    tag = f"coopplus-v{args.version}-b{args.build}"
    package_name = f"Co-op-Plus-{args.version}-build{args.build}-full.zip"
    package_path = output / package_name
    write_zip(package_path, entries)
    package_bytes = package_path.read_bytes()
    if len(package_bytes) > MAX_ARCHIVE:
        raise ValueError("Package exceeds v1 compressed size limit")
    manifest = {
        "schema": 1, "channel": "stable", "version": args.version,
        "build": args.build, "minBootstrap": 1, "sourceRevision": args.source_revision,
        "officialProxyVersion": OFFICIAL_VERSION,
        "package": {"name": package_name, "size": len(package_bytes), "sha256": sha(package_bytes),
                    "url": f"https://github.com/{REPOSITORY}/releases/download/{tag}/{package_name}"},
        "files": [{"path": n, "size": len(data), "sha256": sha(data)} for n, data in sorted(entries.items())],
    }
    manifest_raw = json_bytes(manifest)
    (output / "release.json").write_bytes(manifest_raw)
    mod_entries = {n: sha(data) for n, data in entries.items() if n.startswith("mod/quant.ew/")}
    baseline_mod = {n: sha(data) for n, data in baseline.items() if n.startswith("mod/quant.ew/")}
    if args.runtime_source == "r14" and mod_entries != baseline_mod:
        raise ValueError("Initial payload mod runtime differs from R14")
    provenance = {
        "sourceRevision": args.source_revision, "sourceMode": args.runtime_source,
        "releaseTag": tag, "version": args.version, "build": args.build,
        "baselineArchive": {"name": pathlib.Path(args.baseline).name, "sha256": BASELINE_SHA256},
        "r14RuntimeCheckpoint": R14_CHECKPOINT, "requiredOfficialProxyVersion": OFFICIAL_VERSION,
        "officialProxySha256": PROXY_SHA256, "officialPayloadHashes": identity["official_payload_hashes"],
        "modFileCount": len(mod_entries), "modSha256": mod_entries,
        "runtimeIdenticalToR14": mod_entries == baseline_mod,
        "bootstrapSha256": sha(pathlib.Path(args.bootstrap).read_bytes()),
        "bootstrapProtocol": 1,
        "bootstrapReplacement": "Protocol 1 updates the managed updater, not root CoopPlus.exe. New bootstrap builds have the embedded project icon/metadata; existing clients get managed usability changes after approval and can opt into a game-icon desktop shortcut.",
        "updaterSha256": sha(entries["updater/CoopUpdater.exe"]),
        "updaterSourceSha256": {p.name: sha(p.read_bytes()) for p in sorted((ROOT / "updater").glob("*.cs"))},
        "packageSha256": sha(package_bytes), "releaseMetadataSha256": sha(manifest_raw),
        "savedRunCompatibility": "File update supports complete v1 managed installs independently of skipped builds. Matching R14 Continue is expected; broader saved-run migration is not established. Both peers must use the same runtime. Income OFF to ON still requires a New Game.",
        "publicationState": "DRAFT: anonymous GitHub release downloads are unavailable until separately approved publication.",
    }
    (output / "provenance.json").write_bytes(json_bytes(provenance))
    bootstrap_entries = {"CoopPlus.exe": pathlib.Path(args.bootstrap).read_bytes(),
                         "current/updater/CoopUpdater.exe": entries["updater/CoopUpdater.exe"],
                         "seed/release.json": manifest_raw, "seed/package.zip": package_bytes}
    bootstrap_entries.update({n: baseline[n] for n in LICENSE_NAMES})
    bootstrap_entries["SETUP.txt"] = (
        "Co-op+ updater candidate -- draft review build\r\n\r\n"
        "Extract this entire archive to a permanent user-writable folder outside Noita, mods, and any source checkout.\r\n"
        "Close Noita and all official proxies before setup or updates. Open CoopPlus.exe for setup.\r\n"
        "Co-op+ detects Noita from remembered proxy settings and Steam libraries. Use the detected folder or Browse for noita.exe. Cancel makes no installation changes.\r\n"
        "Existing valid selections and proxy settings/state are retained. Choose portable proxy if using proxy.ron/save_state beside the old proxy.\r\n"
        "Optionally create the Co-op+ desktop shortcut, or run CoopPlus.exe --coop-shortcut later.\r\n"
        "The existing mods/quant.ew is backed up outside active mods. The launcher closes after opening the graphical proxy; CLI modes keep console behavior.\r\n"
        "Settings/saves remain in the official proxy/game profile locations. Do not move them into managed mod/proxy folders.\r\n"
        "Review updater/README.md in the source repository for candidate limitations and setup command options.\r\n"
        "This bundle includes the complete R14 payload. Draft assets are not an anonymous public update feed.\r\n"
    ).encode("utf-8")
    bootstrap_name = f"Co-op-Plus-{args.version}-build{args.build}-bootstrap-win.zip"
    write_zip(output / bootstrap_name, bootstrap_entries)
    notes = (
        f"Co-op+ {args.version}, build {args.build}: Windows updater candidate.\n\n"
        f"Source: `{args.source_revision}`. Official proxy remains v{OFFICIAL_VERSION}. "
        f"The complete mod payload {'is byte-identical to frozen R14' if provenance['runtimeIdenticalToR14'] else 'uses the explicitly approved checkout source'}.\n\n"
        "DRAFT for independent review. Do not publish or mark latest until approved. "
        "This release is not available to anonymous updater clients while draft. "
        "Ordinary commits do not update clients.\n\n"
        f"One-time setup asset: `{bootstrap_name}`. Subsequent releases use `release.json` and `{package_name}`. "
        "See provenance.json and SHA256SUMS.txt for the exact source/runtime/payload mapping.\n\n"
        "File replacement compatibility is distinct from saved-run compatibility; see provenance.json.\n"
    )
    (output / "DRAFT-NOTES.md").write_text(notes, encoding="utf-8")
    sums = "".join(f"{sha(p.read_bytes())}  {p.name}\n" for p in sorted(output.iterdir()) if p.name != "SHA256SUMS.txt")
    (output / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    result = {"output": str(output.resolve()), "tag": tag, "package": manifest["package"],
              "modFiles": len(mod_entries), "runtimeIdenticalToR14": provenance["runtimeIdenticalToR14"],
              "zipIntegrity": "PASS", "fullInventoryAndHashes": "PASS", "installedOrLaunched": False}
    print(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--build", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--bootstrap", required=True)
    parser.add_argument("--updater", required=True)
    parser.add_argument("--runtime-source", choices=("r14", "checkout"), default="r14")
    parser.add_argument("--repository", default=REPOSITORY)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
