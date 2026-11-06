# Release workflow

The approved repository is `benandteicher/noita-ew-coop-plus`. Source commits are
not client updates. No inherited upstream release workflow is installed in the
publication snapshot. Only the coordinator uploads repositories/releases.

The public source is an isolated snapshot of intended current source. The local
checkout, index, remotes, history, canonical project record, diagnostics, saves,
personal configuration and previous packages are preserved. `prepare-publication.py`
creates a new directory, excludes those private/runtime inputs, scans selected
bytes for common credential patterns and produces a complete path/hash inventory.
Review that inventory before committing/pushing. The scan is heuristic.

```powershell
python .\updater\prepare-publication.py --output 'D:\CoopPlus-publication-new'
# Coordinator reviews, commits and pushes this isolated source snapshot.
```

Build from that publication checkout at its exact source commit on Windows with
the OS .NET Framework compiler and Python (build computer
only). The friend's Windows 11 PC needs neither Python, Git, GitHub sign-in nor
developer tools. Pass the exact publication commit SHA, then run:

```powershell
.\updater\build-release.ps1 -SourceRevision 'PUBLICATION_COMMIT_40_HEX' -Version '1.9.14.2' -Build 2 -BaselineArchive 'D:\Entangled Worlds FORK\dist\Noita-EW-Coop-Plus-v1.9-R14-playtest-win.zip' -OutputDirectory 'D:\CoopPlus-artifacts-new'
```

The wrapper requires that commit to be HEAD and the source checkout to be clean.
The tag is `coopplus-v1.9.14.1-b1`; the updater verifies this exact tag convention.

The updater-only candidates copy **all 580 mod files byte-for-byte** from the preserved
R14 playtest archive. The builder verifies its pinned SHA-256 and embedded
manifest. It retains the official v1.6.8 proxy, DLLs, MIT/Apache licenses and
third-party notices. The bootstrap ZIP includes a full seed package for initial
offline review/setup. Release output includes the complete update ZIP,
`release.json`, bootstrap ZIP, `provenance.json`, `SHA256SUMS.txt` and draft notes.
Builds reject an existing output directory to preserve earlier assets. ZIP
ordering/timestamps are fixed; compiler-generated executable identities can vary
between rebuilds, so hashes identify the actual delivered build.

For a later approved runtime change, increment the monotonic build and version,
pass `-RuntimeSource checkout`, and use the commit containing that exact source.
This packages the complete current `quant.ew` tree plus the explicitly pinned
native/version dependencies. Deleted source files are removed from the payload.
It never generates or requires a chain of patch releases. The R14 baseline archive
remains a pinned build input; a new build computer must obtain it from Ben's
preserved artifacts and verify the embedded pinned hash. Changes to official proxy
or native dependency versions require an explicit builder/configuration review.

After review, the coordinator may repeat the build command with `-UploadDraft`
(or upload the already-reviewed files with `gh release create`). That switch checks
the local authenticated personal login/repository, creates a **draft** with
`--latest=false`, targets the exact source revision and never overwrites a tag.
It never publishes a release. Authenticate through the official `gh auth login
--hostname github.com --web` handoff if needed; never extract or paste tokens.

Build 1 is published Latest; build 2 usability candidate remains draft. Draft assets are **not** available to anonymous
updater clients. Publication after independent review is a separate deliberate
action: approve the payload, publish the release as a non-prerelease with the
required assets, then enable/designate it as the approved release feed candidate.
Verify the public metadata/downloads anonymously before distributing the final
one-time setup link. `channel: stable` inside metadata alone does not publish or
activate a draft. Saved-run compatibility is recorded separately in provenance;
file-update support does not promise arbitrary saved-run migration.

Focused packaging checks:

```powershell
python -m unittest discover -s updater/tests -p test_packaging.py -v
```

These checks use disposable directories and dummy executable bytes. They verify
full inventory/hashes, R14 byte parity, licenses, seed packaging, source deletion
and publication boundaries. They never install into a game or launch a proxy.

The one-time setup retains the proxy's verified v1.6.8 Windows mutable locations:
`%APPDATA%\quant\entangledworlds\config\proxy.ron` and
`%APPDATA%\quant\entangledworlds\data\save_state`. When an old portable proxy
folder is supplied during setup, its existing `proxy.ron` and `save_state` paths
are kept in configuration. Neither is copied into replaceable payload folders.
Explicit official proxy settings/save arguments are forwarded.

Build 2 compiles the read-only Noita discovery, native Windows setup, and short-lived
graphical proxy handoff into the single managed updater EXE. Protocol 1 and its
package roots remain unchanged. Embedded bootstrap icon/file metadata apply only
to new bootstrap builds; root CoopPlus.exe is never replaced by routine updates.
Keep the published-build-1 to next-published-build test pending until approval.
