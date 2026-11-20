"""Actual frozen old bootstrap/updater -> candidate -> recovery, check-only.

This exercises bundled-seed redispatch with the supported pending-install state.
It does not claim a public-feed promotion/download rehearsal. No game/proxy is
launched, no certificates/proxy/system policy are changed, and source copies are
read-only inputs. All fixture/evidence paths are retained for coordinator review.
"""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import zipfile


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def no_links(path):
    path = path.absolute()
    for parent in (path, *path.parents):
        if parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0) & 0x400:
            raise RuntimeError(f"Reparse point refused: {parent}")


def inventory(folder):
    no_links(folder)
    result = {}
    for current, directories, files in os.walk(folder, followlinks=False):
        for name in directories:
            no_links(Path(current) / name)
        for name in files:
            path = Path(current) / name
            no_links(path)
            result[path.relative_to(folder).as_posix()] = dict(size=path.stat().st_size, sha256=sha(path))
    return result


def safe_member(name):
    path = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or path.is_absolute() or any(x in ("", ".", "..") for x in name.rstrip("/").split("/")):
        raise RuntimeError(f"Unsafe archive member: {name}")
    return path


def seed(archive):
    no_links(archive)
    with zipfile.ZipFile(archive) as source:
        if source.testzip():
            raise RuntimeError("Bootstrap CRC failed")
        release = json.loads(source.read("seed/release.json"))
        package = source.read("seed/package.zip")
    if len(package) != release["package"]["size"] or hashlib.sha256(package).hexdigest() != release["package"]["sha256"]:
        raise RuntimeError("Seed package bytes do not match frozen release metadata")
    return release, package


def verify_installed(root, game, release):
    expected = {}
    for entry in release["files"]:
        relative = entry["path"]
        safe_member(relative)
        if relative.startswith("mod/quant.ew/"):
            target = game / "mods/quant.ew" / relative.removeprefix("mod/quant.ew/")
        else:
            target = root / "current" / relative
        no_links(target)
        if target.stat().st_size != entry["size"] or sha(target) != entry["sha256"]:
            raise RuntimeError(f"Installed managed hash mismatch: {relative}")
        expected[relative] = entry["sha256"]
    actual = json.loads((root / "current/installed.json").read_text(encoding="utf-8-sig"))
    if actual["build"] != release["build"] or actual["package"]["sha256"] != release["package"]["sha256"]:
        raise RuntimeError("Installed manifest identity mismatch")
    return expected


def verify_backup(root, build, source_expected):
    receipts = list((root / "automatic-backup-receipts").glob(f"build-{build}-*.json"))
    if len(receipts) != 1:
        raise RuntimeError("Expected exactly one automatic backup receipt for this build")
    receipt = json.loads(receipts[0].read_text(encoding="utf-8-sig"))
    directory = Path(receipt["directory"])
    if sha(directory / "backup.json") != receipt["manifestSha256"]:
        raise RuntimeError("Backup manifest receipt hash mismatch")
    if sha(directory / "RESTORE.txt") != receipt["restoreSha256"]:
        raise RuntimeError("Backup recovery instructions receipt hash mismatch")
    manifest = json.loads((directory / "backup.json").read_text(encoding="utf-8-sig"))
    for entry in manifest["files"]:
        safe_member(entry["path"])
        target = directory / "data" / entry["path"]
        no_links(target)
        if entry.get("absent", False):
            if target.exists():
                raise RuntimeError("Recorded absence was fabricated in backup payload")
        elif entry["directory"]:
            if not target.is_dir():
                raise RuntimeError("Missing copied directory")
        elif target.stat().st_size != entry["size"] or sha(target) != entry["sha256"]:
            raise RuntimeError("Independent copied file hash mismatch")
    for relative, expected in source_expected.items():
        target = directory / "data" / relative
        if not target.exists() or sha(target) != expected:
            raise RuntimeError(f"Actual stopped save/settings were not backed up: {relative}")
    return dict(receipt=str(receipts[0]), directory=str(directory), manifestSha256=receipt["manifestSha256"], files=len(manifest["files"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stable-bootstrap", type=Path, required=True)
    parser.add_argument("--candidate-bootstrap", type=Path, required=True)
    parser.add_argument("--recovery-bootstrap", type=Path, required=True)
    parser.add_argument("--game-source", type=Path, required=True, help="Owned stopped runtime, read only; only fingerprinted engine files are copied")
    parser.add_argument("--profile-copy", type=Path, help="Optional already-stopped COPIED Noita profile; never pass live originals")
    parser.add_argument("--state-copy", type=Path, help="Optional already-stopped COPIED proxy state; never pass live originals")
    parser.add_argument("--output", type=Path, required=True, help="Fresh disposable path outside every checkout/install/profile")
    parser.add_argument("--stop-after-candidate", action="store_true", help="Coordinator-only: retain the verified candidate stage for ordinary native Continue assessment before a separate recovery stage")
    args = parser.parse_args()
    output = args.output.absolute()
    no_links(output)
    if output.exists():
        raise RuntimeError("Output already exists; preserve it and choose a new path")
    for parent in (output, *output.parents):
        if (parent / ".git").exists():
            raise RuntimeError("Fixture cannot be inside a source checkout")
    sources = [p.absolute() for p in (args.game_source, args.profile_copy, args.state_copy) if p is not None]
    for source in sources:
        no_links(source)
        if output == source or source in output.parents or output in source.parents:
            raise RuntimeError("Fixture output overlaps an input source")
    releases = [seed(p) for p in (args.stable_bootstrap, args.candidate_bootstrap, args.recovery_bootstrap)]
    if not releases[0][0]["build"] < releases[1][0]["build"] < releases[2][0]["build"]:
        raise RuntimeError("Stable/candidate/recovery IDs must increase")
    if releases[1][0].get("gameFiles") != releases[2][0].get("gameFiles"):
        raise RuntimeError("Candidate and recovery must pin the same frozen local game engine")
    copied_source_before = {str(p): inventory(p) for p in sources[1:]}
    output.mkdir(parents=True)
    root, game = output / "Launcher Ω & stable6", output / "Noita 漢 portable game"
    root.mkdir(); game.mkdir()
    with zipfile.ZipFile(args.stable_bootstrap) as source:
        for entry in source.infolist():
            path = root / str(safe_member(entry.filename))
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.open(entry) as read, path.open("xb") as write:
                    shutil.copyfileobj(read, write)
    engine_files = releases[1][0].get("gameFiles")
    if not engine_files:
        raise RuntimeError("Candidate runtime fingerprints are required for this final rehearsal")
    game_source_hashes = {}
    for entry in engine_files:
        safe_member(entry["path"])
        source, target = args.game_source / entry["path"], game / entry["path"]
        no_links(source)
        if source.stat().st_size != entry["size"] or sha(source) != entry["sha256"]:
            raise RuntimeError("Owned source engine does not match the frozen candidate")
        game_source_hashes[entry["path"]] = entry["sha256"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    profile_components = ["save00", "save_shared"]
    if args.profile_copy:
        if (args.profile_copy / "save_stats").exists():
            profile_components.append("save_stats")
        for name in profile_components:
            shutil.copytree(args.profile_copy / name, game / name)
    else:
        (game / "save00").mkdir(); (game / "save_shared").mkdir()
        (game / "save00/stopped-sentinel.txt").write_text("disposable saved-run marker", encoding="utf8")
        (game / "save_shared/config.xml").write_text('<Config fullscreen="0" />', encoding="utf8")
        (game / "save_stats").mkdir();profile_components.append("save_stats")
        (game / "save_stats/stopped-stats-sentinel.txt").write_text("disposable portable statistics",encoding="utf8")
    state = output / "Proxy state Ω"
    state.mkdir()
    if args.state_copy:
        shutil.copytree(args.state_copy, state / "save_state")
    else:
        (state / "save_state").mkdir()
        (state / "save_state/stopped-sentinel.bin").write_bytes(b"disposable paired proxy marker")
    settings = state / "proxy.ron"
    settings.write_text("(paths:(noita_save:Some(" + json.dumps(str(game), ensure_ascii=False) + ")))", encoding="utf8")
    config = dict(schema=1, gameDir=str(game), settingsPath=str(settings), saveStatePath=str(state / "save_state"), automaticBackupPath=str(output / "Verified backups Ω"))
    config_path = root / "coopplus.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf8")
    # This old-safe sidecar survives stable6 Setup.MarkInstalled rewriting only
    # the fields known to stable6's UserConfig class during redispatch.
    (root / "automatic-backup.json").write_text(json.dumps(dict(schema=1,directory=str(output / "Verified backups Ω")),ensure_ascii=False),encoding="utf8")
    bootstrap_hash = sha(root / "CoopPlus.exe")
    mutable_before = {**{"game-"+name:inventory(game / name) for name in profile_components}, "proxy-state": inventory(state / "save_state"), "settings": sha(settings)}
    backup_expected = {"proxy-settings/proxy.ron": sha(settings)}
    for name in profile_components:
        for relative, entry in inventory(game / name).items():
            backup_expected[f"noita-profile/{name}/{relative}"] = entry["sha256"]
    for relative, entry in inventory(state / "save_state").items():
        backup_expected[f"proxy-state/{relative}"] = entry["sha256"]
    evidence = dict(method="Actual frozen old bootstrap/updater using bundled higher seed and pendingInstall; no public-feed promotion claim", inputs={str(p):sha(p) for p in (args.stable_bootstrap,args.candidate_bootstrap,args.recovery_bootstrap)}, bootstrapSha256=bootstrap_hash, cases=[])

    def check(label):
        result = subprocess.run([str(root / "CoopPlus.exe"), "--coop-check"], input="", capture_output=True, text=True, encoding="utf8", errors="replace", timeout=120)
        (output / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf8")
        (output / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf8")
        if result.returncode:
            raise RuntimeError(f"{label} failed ({result.returncode}): {result.stdout}\n{result.stderr}")
        return result.stdout

    for index, (release, package) in enumerate(releases):
        label = ("stable6", "candidate", "known-good-recovery")[index]
        old_updater = sha(root / "current/updater/CoopUpdater.exe")
        if index:
            (root / "seed/package.zip").write_bytes(package)
            (root / "seed/release.json").write_text(json.dumps(release, ensure_ascii=False), encoding="utf8")
            config["pendingInstall"] = True
            config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf8")
        stdout = check(label + "-first")
        managed = verify_installed(root, game, release)
        if sha(root / "CoopPlus.exe") != bootstrap_hash:
            raise RuntimeError("Actual old bootstrap was replaced")
        case = dict(label=label,build=release["build"],oldUpdaterSha256=old_updater,newUpdaterSha256=sha(root / "current/updater/CoopUpdater.exe"),managedFiles=len(managed))
        if index:
            install_pos = stdout.find("Complete installation ready; restarting the managed updater.")
            backup_pos = stdout.find("Protecting Noita profile, proxy state and settings before first launch")
            if install_pos < 0 or backup_pos < install_pos:
                raise RuntimeError("Actual output did not establish old install -> redispatch -> new backup order")
            case["automaticBackup"] = verify_backup(root, release["build"], backup_expected)
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        before_current = inventory(root / "current")
        before_receipts = inventory(root / "automatic-backup-receipts") if index else None
        check(label + "-second-current")
        if inventory(root / "current") != before_current:
            raise RuntimeError("Second current-version check changed managed installation")
        if index and inventory(root / "automatic-backup-receipts") != before_receipts:
            raise RuntimeError("Second current-version check created/changed backup receipts")
        now = {**{"game-"+name:inventory(game / name) for name in profile_components}, "proxy-state": inventory(state / "save_state"), "settings": sha(settings)}
        if now != mutable_before:
            raise RuntimeError("Updater changed saved profile/proxy state/settings")
        evidence["cases"].append(case)
        (output / "rehearsal.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf8")
        if args.stop_after_candidate and index == 1:
            evidence["retainedCandidateForNativeAssessment"] = True
            break
    if any(sha(args.game_source / path) != expected for path, expected in game_source_hashes.items()):
        raise RuntimeError("Owned runtime input changed during read-only copy")
    for path, before in copied_source_before.items():
        if inventory(Path(path)) != before:
            raise RuntimeError("Stopped copied run input changed")
    evidence["sourceInputsUnchanged"] = True
    evidence["savedWorldRollbackWarning"] = "Gameplay-code recovery does not reverse saved-world changes. This check-only rehearsal made none. Paired Continue remains a separate native assessment."
    (output / "rehearsal.json").write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf8")
    print(json.dumps(evidence, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
