The automatic updater embeds the existing Co-op+ Backup 0.1.2 copy engine,
copied from the preserved project `backup/` directory. Original SHA-256:

* BackupEngine.cs: `67e61d50fba3ef3e7ba3eb992d853f7671666dd4929d359e38073c03b1db24ba`
* RonSelection.cs: `f11443353e690703e478c2db7a6effaf04e6b6a39e78146a98fc8436b517338a`
* LaunchOverrides.cs: `2271ca7129c71f2b7bf34718c4f12fc40119f2ccf9ddaf66174581ff7bda68f0`

MIT OR Apache-2.0, as the project. The original utility remains untouched.
The additive `RunSelected` entry accepts an effective launcher-selected plan
and reselects it for source-stability verification. Its original Run delegates
to this entry; copy/hash/staging/destination behavior is retained. The bounded
RON selector adds a normal-updater-only explicit-None return option. The original
strict Profile(text) behavior remains unchanged.
Restore instructions distinguish explicitly confirmed empty-client state from
automatic empty-state snapshots whose host/client role is unproven.
The additive `Entry.absent` and `Plan.absentSources` fields record genuine
missing sources. The shared engine checks those absences during both inventories
and never creates a payload file/directory for an absent entry.

AutomaticBackup.cs establishes effective forwarded settings/state paths, checks
the native default/portable profile relationship, uses exact self/parent process
exceptions, verifies completed payloads and manifests, and writes atomic receipts.
It executes in the new managed updater AFTER any old-updater install/redispatch,
BEFORE any upgraded proxy/game launch. Therefore it protects save/proxy state
before first upgraded gameplay, not before an older updater replaces runtime files.
Previous runtime rollback directories and their release metadata are retained.

The automatic path is `%LOCALAPPDATA%/CoopPlusBackups/Automatic`, overridable by
an absolute `automaticBackupPath` in coopplus.json, or an old-updater-safe
`automatic-backup.json` sidecar with `schema:1` and an absolute `directory`.
Conflicting destinations refuse. The sidecar is required to retain a custom
backup destination across an old updater's pending-install config rewrite.
It must be outside source,
launcher, game and selected mutable source trees. A failed or interrupted backup
retains `.incomplete-*`, writes no success receipt and blocks launch. A second
current-version check verifies the existing backup and creates no duplicate.
Missing/corrupt/ambiguous selected profile or state blocks launch. Custom launch
commands are refused except the exact native game executable with the verified
`-always_store_userdata_in_workdir` flag (or no arguments for native default).
Native portable mode uses a configured profile equal to the game
directory and backs up save00/save_shared plus an existing save_stats directory,
rather than the licensed game assets. Optional statistics selection is rechecked
by the same source-stability inventory before backup completion.

New Setup can record `first-use.json` only before its first configuration write,
with absent proxy settings/state, exact canonical paths and no prior managed
installation/mod/rollback evidence. A missing/empty Noita profile or an existing
save_shared-only preference profile is protected as observed; active save00 data
without save_shared is refused. Existing complete Noita data is copied in full.
Check-only verifies the snapshot and leaves first-use eligibility unconsumed.
At Launch, original Noita files must still match that snapshot. A durable intent
then creates only absent proxy RON and Noita preference XML, with exact RON
escaping and frozen ModEntry fields. It never creates proxy save_state, run,
world, character, .bit or player data. The real proxy creates its own state.
The intent is consumed before child dispatch. Failed proxy launch with absent
state remains fail-closed; eligibility is never automatically reissued.
Interrupted preference creation resumes only matching staged hashes with all
original files unchanged and no unexpected additions. Corrupt/present settings
or state cannot obtain this exception.

An old protocol-1 updater cannot generate the new first-use proof. Established
old installs with deleted/corrupt data remain refused. A proof genuinely captured
by new Setup can survive an old updater's configuration rewrite/install and is
evaluated only after new-updater redispatch. A wholly unproved old first setup is
not guessed from absences; a genuinely new setup must use the new complete
bootstrap. `FirstUseTests --prepare-first-use` is a source-linked coordinator
fixture helper for this precise order, not a shipped friend command.

No restore/reset is automatic. Reverting gameplay code does not revert saved-world
changes. Each player needs a corresponding backup from the same stopped checkpoint;
copy verification is not a guarantee of paired Continue.
