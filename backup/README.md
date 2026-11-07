# Co-op+ Backup 0.1.2 review candidate

Small Windows Forms executable using the .NET Framework already present on the
supported Windows 11 system. Place beside the selected `CoopPlus.exe`; otherwise
one folder picker finds its installation after the app window appears. The picker
has that window as owner and starts from the existing executable directory rather
than unselected Desktop enumeration. Cancellation exits. No launcher integration, restore/delete
controls, scheduling, pruning or network requests.

`START-HERE.txt` is the friend-facing instruction sheet. Completed backups default
to `%LOCALAPPDATA%\CoopPlusBackups`, with an optional folder change in the window.

## Selection and contents

The engine requires schema-1 `coopplus.json` and uses its absolute `gameDir`,
`settingsPath` and `saveStatePath`. It reads the complete RON `paths` container and
requires an unambiguous `noita_save: Some(absolute profile)`; it never substitutes
an ordinary profile for missing custom state. The profile must include `save00`
and `save_shared`. The selected proxy state directory must exist. An empty state
requires the explicit, initially unchecked “This PC joins another player
(client)” choice before backup is enabled. Official proxy v1.6.8 normally writes
the world/run state only for the host, so an empty joining client's directory is
preserved rather than substituted or falsely identified as host recovery. The
manifest and restore instructions record this assertion and require the matching
host counterpart. Nonempty state alone makes no claim about host health. Duplicate
selection JSON/RON fields are rejected. Comments, raw strings, Unicode escapes
and punctuation inside RON strings are supported by the adapted project parser.

Contents include the complete profile (including empty directories), selected
proxy state/settings, launcher config and executable, installed release manifest,
available managed proxy/current Noita logs, exact game/proxy executable evidence,
metadata inventory and manual restore instructions. Runtime executable evidence
is kept only in the private backup; it is not part of the distributed tool.

The launcher root, Desktop and Start Menu shortcuts are inspected read-only for
known storage/launch overrides. Such overrides are refused rather than guessed.
Arbitrary prior command lines, scripts and shortcuts stored elsewhere cannot be
inferred after their processes exit; those launch methods are outside automatic
selection. A separate portable profile in the game directory is refused when
it conflicts with the RON selection. A selected profile within the game is valid.

## Verification and failure behavior

Noita, developer Noita, official proxy and launcher/updater process names are
checked before work, throughout copying, and immediately before completion.
Nothing is terminated. This is a quiescent file backup, not a filesystem snapshot;
keep those programs closed throughout. Source/ancestor reparse points are refused.

Destinations must be fully absolute and outside all selected source trees, game,
launcher installations, managed updater locations and source checkouts. Space is
checked with a 5% or 64 MB minimum margin. Each attempt uses a dated full GUID.
Files are created without overwrite in a separate `.incomplete-*` staging folder.
Pre-copy SHA-256/inventory, copied SHA-256/inventory and a complete source reread
must agree, including source modification times and empty directory inventory.
Only then is staging renamed to `CoopPlus-*`. Failure/cancellation retains an
explicit `INCOMPLETE.txt` and never modifies earlier completed backups. Users can
manually discard incomplete folders later; no delete action is provided.

`backup.json` records original paths, release identity, per-file hashes/sizes/time
and the explicit recovery limit. `RESTORE.txt` explains paired profile/proxy state,
matching runtime/identities, preserving newer files, and a separate backup for
each PC. A verified copy does not establish a healthy game Continue.

## Build and focused checks

0.1.2 changes startup picker ownership/anchor and literal ampersand display only,
plus executable/manifest version strings. The file-copy algorithm is identical
to the 0.1.1 host/client GUI backup evidence. The coordinator verifies the final
fallback picker through the native UI; the worker performed compilation only
for this UI revision, without launches or a repeated engine suite.

Developer build: `backup/build.ps1 -OutputDirectory <new output folder>`. Uses the
existing Windows Framework C# compiler; end users need no developer tools. The
project icon and MIT/Apache notices follow the existing updater packaging.

`backup/tests/run.ps1 -OutputDirectory <new folder>` compiles a source-linked
harness and a harmless process-name fixture. By default it briefly launches that
fixture hidden to verify refusal; `-ExistingProcess` instead tests against an
already active Noita/proxy without controlling it. Fixtures live under a unique
temporary directory outside the checkout. Failure seams exist only as internal
fields accessible to the source-linked harness, with no production switches.

Twenty-three focused cases passed before review, including disposable filesystem
restoration, Unicode/custom paths, repeats, missing/ambiguous sources, process
refusal, cancellation/copy failure/corruption/source changes and destination
safety. Five targeted path regressions passed after review fixes. Five additional
client-state cases passed after the coordinator observed official client save
behavior, including explicit confirmation, missing directory refusal, metadata,
empty-state restoration, a changed source refusal and no inferred host role. Coordinator
owns final GUI/native Continue evidence and integration verification.

## Small tool survey and licensing

Inspected [Psy185's batch backup](https://github.com/Psy185/Noita-Savegame-Backup/blob/main/backup.bat)
for its dated-retention idea and exit-before-copy instructions. The repository
does not identify a reuse license; no code was reused. Its fixed ordinary-profile
copy and rename approach does not cover the required paired Co-op+ state.

Inspected [NoitaSavior](https://github.com/JoshuaGessner/NoitaSavior/blob/master/src/nsav.py)
and its [GPL-3.0 license](https://github.com/JoshuaGessner/NoitaSavior/blob/master/LICENSE).
Its simple folder/status interface and game-process check support the small GUI
approach, but its slot restore/delete model and Python packaging exceed this
milestone. No source or assets were reused. This implementation uses project C#
patterns and the bounded RON lexer adapted from `updater/NoitaDetection.cs` under
the existing project MIT/Apache licensing.
