# Co-op+ updater candidate

Windows 11; bootstrap protocol 1; complete ZIP releases. Build 2 is a draft
usability candidate; published build 1 remains Latest until separate approval. The initial mod payload is
byte-identical to the frozen R14 playtest package. Official proxy v1.6.8 and its
native components are unchanged. This candidate is for independent review.

## One-time setup on each PC

1. Back up the matching Noita and proxy saved-run state. Close Noita and every
   official proxy. Keep both PCs on the same Co-op+ release.
2. Extract the entire **bootstrap-win.zip** to a permanent, user-writable folder,
   for example `%LOCALAPPDATA%\CoopPlus`. Keep it outside the game folder and all
   development checkouts. Do not extract only the EXE.
3. Open `CoopPlus.exe`. A valid existing selection is reused. Otherwise Co-op+ reads
   the selected proxy's remembered `paths.noita_exe` from RON and Steam app **881100**
   manifests/library lists, including secondary libraries on other drives. Use a
   detected installation, select another result, or Browse for `noita.exe`. Cancel
   leaves configuration and installation unchanged. No drive scan or Steam launch.
4. Existing settings/state paths remain selected. On first setup the official
   AppData paths are used, or choose **Choose portable proxy…** for existing
   `proxy.ron`/`save_state` beside an old proxy. Keep that folder in place; files
   are referenced without importing/resetting them. Explicit shortcut path
   overrides are read for discovery and forwarded without silently changing an
   existing stored state selection.
5. Optionally check **Create a Co-op+ desktop shortcut**. It targets this launcher,
   uses the selected game's icon, and writes only after opt-in. An unavailable
   configured game can be selected again; its complete managed mod is installed
   into the replacement folder while external proxy state is retained.
6. Setup replaces `mods\quant.ew` completely and keeps its prior contents outside
   active mods at `<Noita>\.coopplus-backups\<transaction>\quant.ew`. Enable EW/unsafe
   mods normally. Thereafter use the same launcher/shortcut. Your friend needs no
   Git/authentication, Python/Node, developer tools or execution-policy changes.

`CoopPlus.exe --coop-check` installs/checks without opening the proxy.
`--coop-setup` selects the game/state paths again. `--coop-shortcut` offers the
desktop shortcut separately; it does not launch the proxy. Other arguments are passed to the official
proxy with Windows argument quoting, including explicit settings/save overrides.
The proxy starts from `current\proxy`, with `NP_SKIP_MOD_CHECK=1` and the configured
Noita executable. The graphical proxy is handed off through a short-lived
detached parent, so both updater and bootstrap exit while the proxy stays alive.
A bounded startup check reports immediate failures. No terminal opened by the
user is hidden or closed. Official `--host`, `--lobby`, `--help` and deliberate
`--coop-console` usage keep attached console/wait behavior. Use Co-op+ for
updates; the unchanged proxy's separate upstream update UI is outside this feed.

Default mutable locations were checked against the pinned v1.6.8 proxy source:
`%APPDATA%\quant\entangledworlds\config\proxy.ron` and
`%APPDATA%\quant\entangledworlds\data\save_state`. Noita's saves/mod preferences
remain in its ordinary profile. Explicit portable paths remain where selected.
The updater records those paths in local `coopplus.json`; discovery reads only
the bounded remembered game-location metadata and never rewrites or uploads the
proxy settings. Do not place mutable paths inside managed folders.

## Approval and update rules

The only automatic feed is
`https://api.github.com/repos/benandteicher/noita-ew-coop-plus/releases/latest`.
A release must be public, non-draft, non-prerelease, explicitly designated latest,
have `channel: stable`, and have a matching `coopplus-v<VERSION>-b<BUILD>` tag.
Its `release.json` and full package must be assets of that exact release. Ordinary
commits and tags do not distribute updates. Publishing/marking latest is a separate
human-approved action. **Draft assets cannot be downloaded anonymously.** The
bundled seed supports local review/setup without pretending the draft is public.

Versions use normalized numerical components; build numbers increase monotonically.
Every release carries source revision, official proxy version, full file inventory,
sizes and SHA-256 hashes. HTTPS and the fixed GitHub repository are the publisher
trust boundary; SHA-256 detects corruption, not a compromised publisher account.
There is no separate signing key in protocol 1. Unsupported bootstrap/format
requirements produce explicit errors. The immutable bootstrap dispatches a fresh
temporary copy of the managed updater, which routine full releases replace.

Downloads have bounded network deadlines. The complete package is staged, checked
against size/hash and every manifest entry, then swapped into place. The game-side
mod stage supports a different volume from the launcher. Links, traversal,
case collisions and unexpected archive entries are rejected. Existing source
checkouts and overlapping game/launcher directories are refused.

Updates never stop game/proxy processes. Launch locks serialize each launcher and
game installation; running Noita/proxy or locked managed files prevent mutation.
The journal coordinates the mod and launcher swaps; rollback copies sit outside
active mods. A retained recovery updater can restore an interrupted swap even if
`current` is temporarily absent. Recovery that cannot finish reports the retained
journal/backups and blocks launch. Keep those files and retry after closing the
locking program; do not merge backup directories manually.

Offline/failed checks and downloads use an already verified installation. A failed
installation restores the prior complete directories or reports recovery required.
No official proxy is opened until a complete installation verifies. Only the
official proxy's known generated cosmetics and two log files are exempt from
installed immutable-file verification; all package files are verified at staging.
Backups are retained rather than automatically pruned.

## Compatibility and review limits

Any protocol-1 managed installation can upgrade directly to a later complete build;
intermediate releases are unnecessary. Initial setup can replace an old manual EW
mod installation. These are **file-update guarantees**, not arbitrary saved-run
migration guarantees. Matching R14 Continue behavior is unchanged, with the same
host/identities/mode and paired saves expected. Shared-income OFF to ON still
requires a new run. Both peers must use matching runtime files.

Build 1 is published. Build 2 remains draft and does not replace Latest. The actual
published-build-1 to next-published-build upgrade remains pending publication
approval. Fixture compatibility with the unchanged build-1 bootstrap and managed
updater replacement is tested separately. No gameplay/native recovery matrix or
live game installation is performed for this usability candidate.

Newly built bootstrap/updater EXEs use the existing upstream project icon and
Co-op+ application metadata, retaining its MIT/Apache attribution. Protocol 1
never replaces root `CoopPlus.exe`: existing EXE icons/file metadata do **not**
change automatically. Once build 2 is approved/published, existing build-1 users
receive discovery/setup, console title and clean graphical handoff through the
managed updater, without manually replacing their launcher. `--coop-shortcut`
provides the game's icon even with the original bootstrap. New bootstrap ZIPs get
the embedded project icon; there is no new bootstrap replacement mechanism.

Build/upload instructions: [PUBLICATION.md](PUBLICATION.md). Future uploads stay
draft/`--latest=false` until separately approved. Ordinary commits do not distribute
updates. File-update compatibility is separate from saved-run compatibility.
