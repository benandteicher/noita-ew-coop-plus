# Built-in Discord activity

Staging build 23 bundles the owner’s **Noita Co-op +** Discord application
(`1570660264435781652`) and sends `assets.large_image` as
`coopplus_logo_staging`. The normal build selection keeps `coopplus_logo`
and the original window artwork. Players receive the public Application ID
and artwork key with the installation. Discord desktop and its activity
privacy settings control whether other people see the activity.

The proxy publishes during its owned Noita process and requests a clear on
game exit, opt-out and proxy shutdown. Discord Activity starts on and area
names start off. `presentation-samples.json` and `presentation-preview.html`
show simulated activity from this formatter and the supplied Staging PNG.
A visible Discord card still needs a check on a PC with Discord desktop.

## Historical TEST19 workflow

The setup and publication history below refers to the original TEST19
candidate. Use the Staging build and publication handoff for build 23.

Implementation and focused local checks are prepared. This candidate deliberately
has no Application ID. It cannot claim a working Discord card until the owner
registers Co-op+ and a real desktop check succeeds. Players will receive the public
ID and approved artwork reference inside the rebuilt proxy; no player login, token, manual
game registration, side program or configuration is required.

The proxy owns the integration. Only its retained, direct Noita child enables
activity, including when that child is in menus. Opening the proxy alone does not.
A duplicated read-only handle of the actual child is polled independently of UI
repaints at most every250ms. Weak, nonblocking presentation-only cached observations keep counts and
context current while minimized. Exit/crash clears without a new UI snapshot;
disabling activity and proxy shutdown also request clear. Discord transactions
have a750ms deadline. This is a bounded local behavior, not a promise about Discord-server
visibility timing. No process-name discovery or fallback adoption occurs.

Local preferences persist in existing proxy preferences: **Discord activity ON**,
**Show area names in Discord OFF**. No run save or peer wire format changes.
Coarse Holy Mountain context is explicitly supported; the optional biome suffix
only allows the seven ordinary main-route areas. No boss/secret names, coordinates,
seed, lobby code, inventory, identity or statistics are exposed. A dead player says
Awaiting revival unless the spectator module confirms an actual committed living
teammate camera. Stale/unproven context is omitted; initial loading/menus may show
only generic Entangled Worlds presence until a game status is confirmed.

No Join Game, invites or extra statistics are implemented. The only button is
About Co-op+, opening https://github.com/benandteicher/noita-ew-coop-plus .
Discord's own activity/privacy settings remain authoritative.

## One owner setup

In https://discord.com/developers/applications create an application owned by Ben
or his team named exactly **Noita: Co-op+**, then send back only the public
**Application ID** from General Information. No bot, client secret, player token
or OAuth login integration is needed. That is the single owner setup step.

Artwork reuses the approved original proxy icon through a public immutable commit
URL already hosted by this repository. Its bytes remain unchanged; no application
asset upload or new artwork is needed. Current Discord RPC documentation permits
external image URLs, but actual client rendering still needs the real desktop check.
Friends perform none of these owner/build steps.

## Owner-bound local build, after the ID is supplied

Freeze/review this candidate first. Use the existing maintainer compiler setup;
this command installs no software and never publishes or launches a game:

```powershell
$source = 'C:\Projects\Noita-EW-Coop-Plus\target\discord-presence-20261112\source'
pwsh -NoProfile -File "$source\presence\build_owner_test.ps1" `
  -ApplicationId '<PUBLIC_OWNER_APPLICATION_ID>' `
  -OutputDirectory 'C:\Projects\Noita-EW-Coop-Plus\target\discord-presence-20261112\owner-build-b19' `
  -BuildEnvironment 'C:\Projects\Noita-EW-Coop-Plus.migration\control\enter_destination_env.ps1' `
  -CargoTargetDirectory 'C:\cp-discord-owner-b19-r2' `
  -Python 'C:\Users\CURA\AppData\Local\Programs\Python\Python314\python.exe'
```

It makes a new isolated source clone, changes only the public ID/config inventory,
records that source, runs focused Discord tests, rebuilds the proxy and launcher,
and assembles a new versioned TEST19 ZIP from the exact preserved TEST11 ZIP. It
reuses the exact accepted DLL and frozen owned-game fingerprints. It refuses a
missing ID, dirty source, reused output or changed baseline/compiled inputs. No
official proxy/DLL substitution, normal updater or live-save handling is involved.
The resulting source ZIP, provenance and checksums identify the owner-bound bytes.
A separate parentless public import commit has the identical tree; provenance maps
its ID to the local owner revision. Future publication pushes only that public
commit, never inherited local audit/review history.
The positive owner-bound build is not yet run; no TEST19 asset is publication-ready.

With the owner's Discord desktop available, use an isolated private TEST folder
to check menus/world/death/spectating, count, privacy toggle, game exit/crash and
proxy shutdown. Check the About button from another user's profile view: Discord
may hide buttons on one's own card. No visible card or two-PC gameplay acceptance
is inferred from mocks or local IPC checks.

## Future TEST-only publication, after separate approval

Do not run these public operations during preparation. First review the exact
owner-bound FROZEN-TEST19.json hashes and genuine card results. Then use an
authenticated maintainer Git/gh session (never required for players):

```powershell
$source = 'C:\Projects\Noita-EW-Coop-Plus\target\discord-presence-20261112\source'
$build = 'C:\Projects\Noita-EW-Coop-Plus\target\discord-presence-20261112\owner-build-b19'
# Read-only local frozen bytes + parentless source + stable baseline + unused tag/release:
python -B "$source\presence\publish_test.py" --build $build
# ONLY after genuine Discord acceptance and separate explicit TEST publication approval:
python -B "$source\presence\publish_test.py" --build $build --publish --discord-card-accepted --test-publication-approved
# Repeat anonymous full downloads/asset hashes, prerelease/tag/main/stable-Latest checks:
python -B "$source\presence\publish_test.py" --build $build --verify-public
```

The guarded publisher refuses every existing versioned tag/release (only HTTP404
counts as absent). Before any push it also uses authenticated GraphQL to refuse
existing pending-tag drafts, and resolves the new draft by numeric REST ID. It
refuses wrong/extra/missing local assets, changed source/notes, inherited
source parents, changed main/Latest and mismatching draft asset names/sizes/digests.
It verifies the exact draft before publishing, always uses prerelease/latest=false,
then checks every anonymous download and preserves all prior public release assets,
branches and tags. Any failure stops; it never deletes/replaces/force-pushes or
retries a partial publication as if it were absent. Inspect an owned partial draft
manually after a failure; the script deliberately refuses overwriting it.

Anonymous final verification must download the exact new ZIP/source/provenance/
checksum assets and compare every recorded SHA, confirm prerelease and unchanged
stableLatest/main/existing releases. Do not run normal17/recovery18 publication
commands or attest two-PC acceptance. The old TEST11 URL/archive remains intact.
If19/tag has been used meanwhile, stop and review an unused increasing successor;
never overwrite a versioned release.

The proposed permanent Latest TEST link is separately prepared under
`../test-download`. Its current approved destination remains TEST11, not this
unconfigured candidate. Deployment and any later destination change require their
own approval. Future downloads change; existing extracted folders never auto-update.

The pending-tag draft lookup follows the official GitHub CLI implementation:
https://github.com/cli/cli/blob/trunk/pkg/cmd/release/shared/fetch.go .
Offline publication fixtures replace every GitHub/Git operation; no real draft,
upload, tag or release is created by those checks.

Native compiler scratch paths require an explicit unused short physical root.
The helper rejects existing/linked/unsafe roots, records the actual compiled proxy
path/hash, physically stages it, and verifies identical bytes before packaging.
Release-profile focused tests and the executable use the same compiler target.
No Windows policy, junction, subst drive or previous build directory is changed.
The owner registered **Noita Co-op+** (Discord rejected the colon); the public
Application ID is the required identity, not the Public Key. Earlier exact-colon
registration instructions are superseded by this owner setup correction.
