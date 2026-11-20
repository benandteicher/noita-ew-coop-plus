"""Verify the approved TEST redirect; publishing requires explicit --publish.

No updater or release operations. Only a separate, fixed Pages branch may be
written, in a new isolated temporary repository. Default execution is read-only
apart from a temporary anonymous ZIP download which is removed on close.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
REPOSITORY = "benandteicher/noita-ew-coop-plus"
REMOTE = "https://github.com/" + REPOSITORY + ".git"
PAGES_BRANCH = "coopplus-test-pages"
STABLE_URL = "https://benandteicher.github.io/noita-ew-coop-plus/test/"
ASSET_URL = "https://github.com/benandteicher/noita-ew-coop-plus/releases/download/coopplus-test-v1.9.18.4-b11-setup-r3/Noita-CoopPlus-1.9.18.4-build11-TEST-SETUP-r3-SEALED.zip"
ASSET_SHA256 = "64fa35097777fad9d79ffe807d9284abe7ab7c9d6453a45bf6faf2f4657ea3c1"
ASSET_BYTES = 25810034
MANIFEST_SHA256 = "b71ee4d0d0ba5539a87c5b785a2892bc5e6ee8defb11f16ed34bb9acdb816d3c"
SITE_FILES = {".nojekyll", "test/index.html", "test/target.json"}
ASSET_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com", "github-releases.githubusercontent.com"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def render_page(manifest):
    return ('''<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="refresh" content="3;url={url}">
<title>Latest approved Co-op+ TEST download</title>
<style>body{{font:1rem/1.6 system-ui,sans-serif;max-width:44rem;margin:10vh auto;padding:0 1.2rem;color:#18232a;background:#f8fafb}}a{{color:#1555a0}}code{{overflow-wrap:anywhere}}h1{{line-height:1.2}}</style>
<main>
<h1>Co-op+ TEST download</h1>
<p>Opening the latest approved TEST ZIP: <strong>{label}</strong>.</p>
<p><a href="{url}" rel="noreferrer">Download {label}</a> if the download does not begin.</p>
<p>SHA256: <code>{sha256}</code></p>
<p>Extract the ZIP and open <strong>Co-op+ Playtest.exe</strong>. Read the included player instructions.</p>
<p>This link selects future downloads. Extracted TEST folders never update automatically. Keep existing private progress and use the matching player instructions when an update is approved.</p>
</main>
</html>
'''.format(**manifest)).encode("utf-8")


def validate_local(raw=None):
    site = ROOT / "site"
    files = set()
    for directory, directories, entries in os.walk(site, followlinks=False):
        for path in [Path(directory)] + [Path(directory) / name for name in directories + entries]:
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400, "Local Pages reparse/link entry refused")
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Local Pages special/hardlinked entry refused")
        files.update((Path(directory) / name).relative_to(site).as_posix() for name in entries)
    require(files == SITE_FILES, "Unexpected Pages publication file set")
    raw = (ROOT / "site/test/target.json").read_bytes() if raw is None else raw
    require(digest(raw) == MANIFEST_SHA256, "Target manifest is not the single reviewed pin")
    manifest = json.loads(raw)
    require(manifest == {"schema": 1, "label": "Co-op+ TEST build11 setup-r3", "url": ASSET_URL, "sha256": ASSET_SHA256, "bytes": ASSET_BYTES}, "Destination is outside the approved immutable TEST allowlist")
    require((ROOT / "site/test/index.html").read_bytes() == render_page(manifest), "HTML differs from the reviewed manifest/fallback template")
    require((ROOT / "site/.nojekyll").read_bytes() == b"", "Unexpected .nojekyll content")
    return manifest


class AnonymousRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        url = urlsplit(new_url)
        require(url.scheme == "https" and url.hostname in ASSET_HOSTS and url.username is None and url.password is None, "Unsafe asset download redirect")
        return super().redirect_request(request, response, code, message, headers, new_url)


def anonymous_opener():
    # No cookies, authorization, credential helper, browser, or proxy credentials.
    return build_opener(ProxyHandler({}), AnonymousRedirects())


def verify_stream(stream):
    digestor, count = hashlib.sha256(), 0
    with tempfile.TemporaryFile() as local:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            count += len(chunk)
            require(count <= ASSET_BYTES, "Anonymous download exceeds the exact pinned size")
            digestor.update(chunk)
            local.write(chunk)
    require(count == ASSET_BYTES and digestor.hexdigest() == ASSET_SHA256, "Anonymous download differs from approved TEST ZIP")
    return {"bytes": count, "sha256": digestor.hexdigest(), "anonymous": True}


def verify_download():
    request = Request(ASSET_URL, headers={"User-Agent": "CoopPlus-Test-Redirect-Verify", "Accept": "application/octet-stream"})
    with anonymous_opener().open(request, timeout=60) as response:
        require(response.status == 200, "Anonymous download did not return HTTP 200")
        return verify_stream(response)


def verify_live(manifest):
    # Exact fixed Pages paths only; never follow a page destination from input.
    opener = anonymous_opener()
    for url, expected in ((STABLE_URL, render_page(manifest)), (STABLE_URL + "target.json", (ROOT / "site/test/target.json").read_bytes())):
        with opener.open(Request(url, headers={"User-Agent": "CoopPlus-Test-Redirect-Verify", "Cache-Control": "no-cache"}), timeout=30) as response:
            require(response.status == 200 and response.geturl() == url, "Published Pages URL unavailable or unexpectedly redirected")
            require(response.read(len(expected) + 1) == expected, "Published Pages bytes differ from frozen local files")


def command(arguments, cwd=None):
    environment = None
    if arguments[0] == "git":
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(arguments, cwd=cwd, env=environment, capture_output=True, text=True, check=False)
    require(result.returncode == 0, "Command failed: " + arguments[0] + "; " + result.stderr.strip())
    return result.stdout.strip()


def pages_state():
    result = subprocess.run(["gh", "api", "-H", "X-GitHub-Api-Version: 2026-03-10", "repos/" + REPOSITORY + "/pages"], capture_output=True, text=True, check=False)
    if result.returncode:
        require("HTTP 404" in result.stderr, "Cannot establish GitHub Pages state; no change attempted")
        return None
    state = json.loads(result.stdout)
    require(state.get("source") == {"branch": PAGES_BRANCH, "path": "/"} and state.get("build_type", "legacy") == "legacy" and not state.get("cname"), "Existing Pages configuration belongs to another site; refuse replacement")
    return state


def publish(manifest, verified):
    require(verified == {"bytes": ASSET_BYTES, "sha256": ASSET_SHA256, "anonymous": True}, "Fresh anonymous verification required before publishing")
    validate_local()
    manifest_bytes = (ROOT / "site/test/target.json").read_bytes()
    require(digest(manifest_bytes) == MANIFEST_SHA256, "Manifest changed after verification")
    publication = {".nojekyll": b"", "test/index.html": render_page(manifest), "test/target.json": manifest_bytes}
    prior_pages = pages_state()
    workspace = Path(tempfile.mkdtemp(prefix="coopplus-test-pages-"))
    git = ["git", "-c", "core.hooksPath=" + str(workspace / ".disabled-hooks"), "-c", "core.attributesFile=" + str(workspace / ".disabled-attributes"), "-c", "core.autocrlf=false", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential", "-C", str(workspace)]
    command(git + ["init", "--initial-branch=" + PAGES_BRANCH])
    command(git + ["remote", "add", "origin", REMOTE])
    advertised = command(git + ["ls-remote", "--heads", "origin", "refs/heads/" + PAGES_BRANCH])
    if advertised:
        require(len(advertised.splitlines()) == 1 and advertised.split()[1] == "refs/heads/" + PAGES_BRANCH, "Ambiguous Pages branch")
        command(git + ["fetch", "--depth=1", "origin", "refs/heads/" + PAGES_BRANCH])
        # Inspect raw Git objects before any working-tree files are materialized.
        tracked = set(command(git + ["ls-tree", "-r", "--name-only", "FETCH_HEAD"]).splitlines())
        require(tracked == SITE_FILES, "Existing Pages branch contains unrelated files; refuse replacement")
        modes = command(git + ["ls-tree", "-r", "--format=%(objectmode)", "FETCH_HEAD"]).splitlines()
        require(modes == ["100644"] * len(SITE_FILES), "Existing Pages branch has links or special file modes")
        command(git + ["update-ref", "HEAD", "FETCH_HEAD"])
        command(git + ["read-tree", "FETCH_HEAD"])  # Index only; no checkout/filter execution.
    for name in SITE_FILES:
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(publication[name])
    command(git + ["add", "--", *sorted(SITE_FILES)])
    if command(git + ["status", "--porcelain"]):
        command(git + ["-c", "user.name=Co-op+ TEST Pages publisher", "-c", "user.email=coopplus-test-pages@invalid", "commit", "-m", "Pin approved Co-op+ TEST download"])
        # Normal non-force push protects concurrent changes; no source/main ref.
        command(git + ["push", "origin", "HEAD:refs/heads/" + PAGES_BRANCH])
    if prior_pages is None:
        request = workspace / "pages-create.json"
        request.write_text(json.dumps({"build_type": "legacy", "source": {"branch": PAGES_BRANCH, "path": "/"}}), encoding="utf-8")
        command(["gh", "api", "-H", "X-GitHub-Api-Version: 2026-03-10", "--method", "POST", "repos/" + REPOSITORY + "/pages", "--input", str(request)])
    return {"pagesBranch": PAGES_BRANCH, "isolatedWorkspaceRetained": str(workspace), "publicVerificationRequired": True}


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--publish", action="store_true", help="Explicit future authorization to push only the fixed Pages branch and configure its site")
    parser.add_argument("--verify-live", action="store_true", help="Read-only exact public Pages verification after a separately approved deployment")
    return parser.parse_args(argv)


def main(argv=None):
    options = arguments(argv)
    require(not (options.publish and options.verify_live), "Publish and final public verification are separate steps")
    manifest = validate_local()
    verified = verify_download()  # Always BEFORE any publication or destination change.
    result = {"mode": "verify-only", "targetUrl": ASSET_URL, "manifestSha256": MANIFEST_SHA256, "download": verified, "publicMutation": False}
    if options.verify_live:
        verify_live(manifest)
        result["pagesVerified"] = True
    if options.publish:
        result.update(publish(manifest, verified))
        result.update(mode="publish", publicMutation=True)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
