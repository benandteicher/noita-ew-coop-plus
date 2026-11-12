# Co-op+ private playtest launcher

The coordinator builds `Co-op+ Playtest.exe` with `build.ps1 -Output <new-file>`.
It uses Windows 11's .NET Framework; players need no compiler or interpreter.
The pinned payload is `payload/manifest.json`, schema 1, exact runtime ID
`coopplus-20261111-b11-session1`, label, and complete `{path,size,sha256}` file list.
Only `bin/` and `mod/quant.ew/` entries are accepted; no licensed game content.
The manifest also requires `ownedGame` metadata-only fingerprints for the supported
owned `noita.exe`, `data/data.wak`, `lua51.dll`, `SDL2.dll`, and `steam_api.dll`.
The launcher generates exact ASCII `steam_appid.txt` containing 881100 only in
the private game and includes it in that copy's immutable ready manifest.
The coordinator should include all native engine dependencies in this list.
The raw proxy refuses launch before log creation without the validated private
launcher context or the normal updater's explicit context and game/state paths.

Build the proxy with compile-time `COOPPLUS_RUNTIME_ID=coopplus-20261111-b11-session1`.
The normal candidate release must use those same frozen proxy/DLL/Lua bytes.
The proxy refuses official self-update/mod replacement, forces private mod/save
discovery, and adds native `-always_store_userdata_in_workdir` for the private game.
It advertises legacy-visible Steam version 77.18.7 plus its exact frozen ID so
stable clients cannot join the custom host. Frozen IP/LAN hosting/join is refused.

Developer focused harness (never shipped to players): compile Playtest.cs and
PlaytestTests.cs with `/main:PlaytestTests /target:exe` and the same references in
build.ps1. Run with one newly named absolute scratch directory outside checkouts.
It clones tiny synthetic owned-game fixtures and exercises path, payload,
interruption, settings, Continue, source-change and preservation failures.
Only the coordinator executes builds/tests and native sessions.

First open copies and verifies the owned game into `private/game`; it excludes
normal mods/run data, portable save_stats, and proxy settings/state. Only known
shipped root engine files and the data subtree are copied. All copied files are
physical copies. Safe Noita preference
attributes alone seed a fresh save_shared/config.xml. Test saves persist there.
The launcher supervises the proxy and retains a process gate while private child
identities remain. No process is killed. Incomplete/private corrupt storage is
preserved and refused; it is never silently repaired/reset.

Player setup instructions are corrected in the standalone `PLAYER-INSTRUCTIONS.txt`
sidecar. The startup correction upgrades the existing private folder in place;
keep its Continue and settings. For the first new test run the host
selects proxy **Game mode: Local health alternate**, mod **Enable Cooperative
Revival: ON**, mod **Shared income with separate wallets: ON**, and proxy **Share
Gold: OFF**. The proxy choices are under **Show host settings**; the mod choices
are under **Entangled Worlds: Co-op+ > Co-op+ main features (HOST)** in private
Noita's Mod Settings.

Fresh defaults remain ordinary Local health and the three toggles OFF. The four
mod HOST switches themselves all default OFF: Cooperative Revival (new-run
revival extension), separate-wallet shared income (new-run nugget credit), Offer
Holy Mountain return (optional current-run confirmed return after a later death),
and experimental shop-spell hover (current-run future shop stock). Keep Mountain
return and shop hover OFF unless deliberately testing those options. Saved new-run
rules stay locked; the sidecar does not ask players to reset a test Continue.

Startup/session correction (build11): compile Playtest.cs, PlaytestMigration.cs
and PlaytestSession.cs. Package payload metadata includes exact accepted old
manifests in `upgradeFrom`; runtime migration journals managed files individually
and keeps existing saves, state, settings and generated player sprites/UV maps.
Both accepted ctx1 predecessor metadata hashes are pinned explicitly: SEALED
00499a64… and READY b8d2197e…. Older rejected candidate-b7 drafts are refused.

Verification runs on a responsive worker with stage/file/MiB progress. Full warm
private-game hashing remains. Logs in package/playtest-logs retain bounded owned
session diagnostics, exact process FILETIMEs and exits. Exit42 requires a matching
intent record and authorizes at most four guardian-dispatched replacements;
ordinary closes and crashes never trigger automatic respawn. A zero exit before
any proxy window is observed is surfaced as an unproved startup failure.

Coordinator-only focused sources are PlaytestSessionTests.cs,
PlaytestMigrationTests.cs and PlaytestSessionProcessTests.cs. The process fixture
SessionDummyProxy.cs is developer test context only and must never be shipped.
