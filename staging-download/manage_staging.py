"""Verify frozen Staging assets or prepare a Pages selection from a published release."""

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import urlsplit
import zipfile

REPO = "benandteicher/noita-ew-coop-plus"
API = "https://api.github.com/repos/" + REPO
RELEASES = "https://github.com/" + REPO + "/releases/download/"
PAGES = "https://benandteicher.github.io/noita-ew-coop-plus/staging/"
HASH = re.compile(r"[0-9a-f]{64}")
LAUNCHER_PROTOCOL = 2


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def verify_local(path):
    proof = json.loads((path / "FROZEN-STAGING.json").read_bytes())
    choice = json.loads((path / "staging-release.json").read_bytes())
    version, build = choice["version"], choice["build"]
    tag = f"coopplus-staging-v{version}-b{build}"
    full = f"Noita-CoopPlus-{version}-build{build}-STAGING-win.zip"
    require(choice["schema"] == 1 and choice["launcherProtocol"] == LAUNCHER_PROTOCOL and choice["channel"] == "staging",
            "Incorrect staging release contract.")
    require(re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){3}", version)
            and type(build) is int and 0 < build < 1000000000
            and choice["tag"] == tag and choice["name"] == full, "Staging release identity differs.")
    require("releaseId" not in choice and "assetId" not in choice, "Frozen local metadata already has remote IDs.")
    require(proof["tag"] == tag and re.fullmatch(r"[0-9a-f]{40}", proof["sourceCommit"])
            and re.fullmatch(r"[0-9a-f]{40}", proof["sourceTree"]), "Unexpected source binding.")
    source = f"CoopPlus-Staging{build}-source-{proof['sourceTree'][:12]}.zip"
    expected_assets = {full, source, "staging-release.json", "BUILD-PROVENANCE.json", "SHA256SUMS.txt"}
    if "launcherRefresh" in proof:
        refresh = proof["launcherRefresh"]
        require(refresh == f"CoopPlus-Staging{build}-Launcher-Refresh.exe", "Unexpected launcher refresh asset.")
        expected_assets.add(refresh)
    require(set(proof["assets"]) == expected_assets,
            "Frozen draft must contain the exact package, source, provenance, metadata and checksum.")
    for name, record in proof["assets"].items():
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", name) and name not in (".", ".."), "Unsafe asset name.")
        file = path / name
        attr = getattr(file.lstat(), "st_file_attributes", 0)
        require(file.is_file() and not file.is_symlink() and not attr & 0x400, "Asset is linked or unavailable.")
        require(file.stat().st_size == record["size"] and sha(file) == record["sha256"], "Frozen asset changed: " + name)
    require(proof["assets"][full] == {"size": choice["size"], "sha256": choice["sha256"]},
            "Full ZIP and release metadata disagree.")
    checksums = "".join(f"{record['sha256']}  {name}\n" for name, record in sorted(proof["assets"].items())
                        if name != "SHA256SUMS.txt")
    require((path / "SHA256SUMS.txt").read_bytes() == checksums.encode(), "Checksum file disagrees with the frozen assets.")
    with zipfile.ZipFile(path / full) as archive:
        manifest = archive.read("staging-payload/manifest.json")
        require(archive.testzip() is None and hashlib.sha256(manifest).hexdigest() == choice["manifestSha256"],
                "Full ZIP and bundled manifest disagree.")
        parsed = json.loads(manifest)
        require((parsed["channel"], parsed["version"], parsed["build"]) == ("staging", version, build),
                "Staging payload identity differs.")
    return proof, choice


class AnonymousRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, reason, headers, newurl):
        server = urlsplit(newurl)
        require(server.scheme == "https" and server.username is None and server.hostname in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        }, "Unexpected download redirect.")
        return super().redirect_request(request, file, code, reason, headers, newurl)


def request(url):
    return urllib.request.build_opener(AnonymousRedirect()).open(
        urllib.request.Request(url, headers={"User-Agent": "CoopPlus-Staging-Verify", "Cache-Control": "no-cache"}),
        timeout=30,
    )


def public_bytes(url, limit):
    with request(url) as response:
        raw = response.read(limit + 1)
        require(response.geturl() == url and len(raw) <= limit, "Unexpected metadata response.")
        return raw


def api_json(suffix):
    return json.loads(public_bytes(API + suffix, 1024 * 1024))


def snapshot():
    current = api_json("/releases/latest")
    head = api_json("/git/ref/heads/main")
    return {"latest": current["tag_name"], "latestId": current["id"],
            "latestPrerelease": current["prerelease"], "main": head["object"]["sha"]}


def verify_release(proof, choice, release, draft):
    tag = choice["tag"]
    require(release["tag_name"] == tag and release["draft"] is draft and release["prerelease"] is True,
            "Release tag, draft status or prerelease status disagrees.")
    require(draft or release.get("immutable") is True, "GitHub has not locked this published release.")
    asset_names = [a["name"] for a in release["assets"]]
    require(len(asset_names) == len(proof["assets"]) and set(asset_names) == set(proof["assets"]),
            "GitHub asset list differs from the frozen draft.")
    for asset in release["assets"]:
        name = asset["name"]
        expected = proof["assets"][name]
        require(asset["state"] == "uploaded" and type(asset["id"]) is int and asset["id"] > 0
                and asset["size"] == expected["size"]
                and asset["digest"] == "sha256:" + expected["sha256"]
                and asset["browser_download_url"] == RELEASES + tag + "/" + name,
                "GitHub asset size, hash or identity differs: " + name)
    full = next(a for a in release["assets"] if a["name"] == choice["name"])
    require(type(release["id"]) is int and release["id"] > 0, "Missing GitHub release identity.")
    return dict(choice, releaseId=release["id"], assetId=full["id"])


def anonymous_asset(url, expected):
    checksum = hashlib.sha256()
    total = 0
    with request(url) as response:
        while block := response.read(1024 * 1024):
            total += len(block)
            require(total <= expected["size"], "Anonymous download exceeded pinned size.")
            checksum.update(block)
    require(total == expected["size"] and checksum.hexdigest() == expected["sha256"],
            "Anonymous download differs from the frozen asset.")
    return {"size": total, "sha256": checksum.hexdigest()}


def page(choice):
    # Based on the existing TEST pointer's static page and direct-link fallback.
    url = html.escape(RELEASES + choice["tag"] + "/" + choice["name"], quote=True)
    version = html.escape(choice["version"])
    build = choice["build"]
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="refresh" content="3;url={url}">
<title>Co-op+ Staging download</title>
<style>body{{font:1rem/1.6 system-ui,sans-serif;max-width:42rem;margin:9vh auto;padding:0 1.25rem}}code{{overflow-wrap:anywhere}}</style>
<main>
<h1>Co-op+ Staging download</h1>
<p>Opening Staging {version} build {build}.</p>
<p><a href="{url}" rel="noreferrer">Download the Windows ZIP</a></p>
<p>SHA-256 <code>{html.escape(choice["sha256"])}</code></p>
<p>Extract to a new folder and open <strong>Co-op+ Staging.exe</strong>. To update an existing Staging installation, close the game and proxy and use <strong>Check for update</strong>.</p>
</main>
</html>
""".encode()


def verified_public(proof, choice, before):
    release = api_json("/releases/tags/" + choice["tag"])
    selected = verify_release(proof, choice, release, False)
    tag = api_json("/git/ref/tags/" + choice["tag"])
    require(tag["object"]["type"] == "commit" and tag["object"]["sha"] == proof["sourceCommit"],
            "The immutable tag does not point to the reviewed clean source root.")
    base = RELEASES + choice["tag"] + "/"
    for name, expected in proof["assets"].items():
        anonymous_asset(base + name, expected)
    require(snapshot() == before, "Stable Latest or main differs from the pre-publication snapshot.")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("snapshot", "verify-local", "verify-draft", "select", "verify-live"))
    parser.add_argument("--sealed", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--before", type=Path)
    parser.add_argument("--api-json", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.command == "snapshot":
        print(encoded(snapshot()).decode(), end="")
        return
    require(args.sealed is not None, "Specify a frozen Staging folder.")
    proof, choice = verify_local(args.sealed)
    if args.command == "verify-local":
        print("Frozen Staging assets match the local draft.")
        return
    require(args.tag == choice["tag"], "Explicitly select the exact frozen Staging tag.")
    if args.command == "verify-draft":
        require(args.api_json is not None, "Provide the authenticated draft metadata as a local JSON file.")
        verify_release(proof, choice, json.loads(args.api_json.read_bytes()), True)
        print("The draft is a Staging prerelease and all frozen assets match.")
        return
    require(args.before is not None, "Supply the anonymous pre-publication Stable/main snapshot.")
    selected = verified_public(proof, choice, json.loads(args.before.read_bytes()))
    if args.command == "select":
        require(args.out is not None and not args.out.exists(), "Choose a new local Pages output folder.")
        site = args.out / "staging"
        site.mkdir(parents=True)
        (args.out / ".nojekyll").write_bytes(b"")
        (site / "feed.json").write_bytes(encoded(selected))
        (site / "index.html").write_bytes(page(selected))
        print(f"Prepared local Pages files for {args.tag}.")
    else:
        require(public_bytes(PAGES, 65536) == page(selected)
                and public_bytes(PAGES + "feed.json", 65536) == encoded(selected), "Pages did not select the approved tag.")
        print("Anonymous Staging download, immutable release, Pages and Stable/main match.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(str(error))
