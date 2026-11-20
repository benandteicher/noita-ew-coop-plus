# Prepared permanent TEST download link

Prepared locally only. Proposed stable URL:
https://benandteicher.github.io/noita-ew-coop-plus/test/

The tiny static page waits three seconds, opens the approved versioned TEST ZIP,
and keeps a readable download link and SHA256 visible. It has no JavaScript,
analytics, external styles, updater, server, credentials or normal-release feed.
It selects **future downloads only**. Extracted TEST folders never auto-update.
Changing this link cannot migrate an existing folder or restore a saved world.

The only approved destination in this source is:

- TEST build11 setup-r3, `Noita-CoopPlus-1.9.18.4-build11-TEST-SETUP-r3-SEALED.zip`.
- Immutable asset URL is in `site/test/target.json`.
- ZIP SHA256: `64fa35097777fad9d79ffe807d9284abe7ab7c9d6453a45bf6faf2f4657ea3c1`.
- Size: 25,810,034 bytes.
- The script also hard-pins the manifest bytes, not just its parsed values.

## Local verification now

These commands perform only redirect-specific tests and anonymous reads. They do
not invoke Git/GitHub CLI, edit a checkout, publish a branch or configure Pages.
Python 3 is a maintainer tool; players just open the link and extract the ZIP.

```powershell
$redirect = 'C:\Projects\Noita-EW-Coop-Plus\target\discord-presence-20261112\source\test-download'
Set-Location -LiteralPath $redirect
python -B .\test_redirect.py
python -B .\manage_redirect.py
```

Default mode verifies the exact local file set, manifest and fallback HTML, then
downloads the versioned ZIP without Authorization, cookies, browser state,
credential helpers or proxy credentials. It verifies its exact size and SHA256.
The downloaded bytes are temporary; signed download redirect queries are never
printed or saved. A failed download stops the operation.

## Future deployment — requires separate publication approval

**Do not run this while publication/deployment remains forbidden.** Once explicit
approval covers this prepared page and its separate Pages branch, use the already
configured maintainer GitHub CLI account with repository content/Pages rights:

```powershell
Set-Location -LiteralPath $redirect
python -B .\manage_redirect.py
python -B .\manage_redirect.py --publish
```

`--publish` is the only publishing switch. It repeats the anonymous ZIP check
before making any public change. The only possible destination is the literal
approved repository and `coopplus-test-pages` branch. It uses a new isolated
temporary Git repository, a normal non-force push, and publishes exactly:

```text
.nojekyll
test/index.html
test/target.json
```

Existing Pages configuration for another branch, build method or custom domain
causes refusal. An existing Pages branch with unrelated tracked files also causes
refusal. If Pages is absent, the explicit publish operation creates a branch-based
site from `coopplus-test-pages:/`; subsequent matching pushes update that site.
No main/source branch, tag, release, Latest designation, updater or game install
is changed. No public assets are uploaded by this script. The temporary public
site checkout is retained for inspection; it contains no embedded credentials.

If a push succeeds but Pages configuration/build fails, do not report the URL as
live. Inspect the retained temporary checkout and the reported GitHub failure.
GitHub permissions, branch protection, Actions/Pages availability or deployment
delays can require maintainer action. No bypass is attempted.

## Final public verification

After GitHub reports the Pages deployment complete, run:

```powershell
python -B .\manage_redirect.py --verify-live
```

This anonymously rechecks the ZIP and requires exact public HTML and manifest
bytes at the fixed `/test/` URL. It does not publish. Retry after a normal Pages
deployment delay if unavailable. Then open the stable link in a signed-out
browser and confirm the fallback and actual ZIP download. Browser/CDN caching can
delay a changed page; the HTML cache hint is not a server cache guarantee.

## A later approved TEST destination

Rechecking or redeploying this same approved destination uses the commands above.
A successor requires **separate approval and a reviewed source revision**: update
the single allowlisted URL/label/size/hash, the manifest SHA256 pin and generated
HTML together. The current command deliberately accepts no URL, tag, version,
manifest path, shell command or arbitrary destination argument. It cannot select
a new build or follow a release feed automatically. Review the new immutable
asset and anonymized hash proof, run local tests/default verification, and only
then run the explicitly approved `--publish` command. Retain the previous pin for
recovery; switching the page affects later downloads, not extracted folders.

GitHub's branch-source Pages API contract is documented at
https://docs.github.com/en/rest/pages/pages?apiVersion=2026-03-10 .
