# Co-op+ updater candidate

Windows 11; bootstrap protocol 1; complete ZIP releases. The initial mod payload is
byte-identical to the frozen R14 playtest package. Official proxy v1.6.8 and its
native components are unchanged. This candidate is for independent review.

## One-time setup on each PC

1. Back up the matching Noita and proxy saved-run state. Close Noita and every
   official proxy. Keep both PCs on the same Co-op+ release.
2. Extract the entire **bootstrap-win.zip** to a permanent, user-writable folder,
   for example `%LOCALAPPDATA%\CoopPlus`. Keep it outside the game folder and all
   development checkouts. Do not extract only the EXE.
3. Open `CoopPlus.exe`. Enter the folder containing your `noita.exe`. Press Enter
   at the portable-proxy prompt to retain the official AppData settings/saves, or
   enter the old proxy folder if it contains your portable `proxy.ron` and
   `save_state`. No settings or saves are imported, reset or merged.
4. Setup replaces `mods\quant.ew` completely, retaining its old contents at
   `<Noita>\.coopplus-backups\<transaction>\quant.ew`, outside active mods.
   Enable EW/unsafe mods in Noita as usual. Use this same `CoopPlus.exe` thereafter;
   a desktop shortcut is sufficient. No Git, authentication, Python/Node,
   developer tools or execution-policy changes are required on the friend's PC.

`CoopPlus.exe --coop-check` installs/checks without opening the proxy.
`--coop-setup` selects the paths again. Other arguments are passed to the official
proxy with Windows argument quoting, including explicit settings/save overrides.
The proxy starts from `current\proxy`, with `NP_SKIP_MOD_CHECK=1` and the configured
Noita executable. Keep the console open while the proxy is running. Use Co-op+ for
updates; the unchanged proxy's separate upstream update UI is outside this feed.

Default mutable locations were checked against the pinned v1.6.8 proxy source:
`%APPDATA%\quant\entangledworlds\config\proxy.ron` and
`%APPDATA%\quant\entangledworlds\data\save_state`. Noita's saves/mod preferences
remain in its ordinary profile. Explicit portable paths remain where selected.
The updater records those paths in local `coopplus.json`; it does not read the
settings content or upload it. Do not place mutable paths inside managed folders.

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

The first release stays draft and is not marked latest. Disposable filesystem,
fault, networking and dummy-launch tests are recorded in the review handoff. There
is no normal-game installation, official proxy/game launch, gameplay/native recovery
matrix, code signing, SmartScreen reputation, or anonymous published-download proof
in this pass. Windows may show the normal unsigned-download prompt.

Build/upload instructions: [PUBLICATION.md](PUBLICATION.md). Do not publish the
first draft until independent review is complete; then verify the public feed and
assets anonymously before treating automatic updates as enabled.
