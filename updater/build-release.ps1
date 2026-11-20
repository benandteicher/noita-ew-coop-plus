[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceRevision,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version = '1.9.14.2',
    [ValidateRange(1,2147483647)][int]$Build = 2,
    [string]$OutputDirectory,
    [string]$BaselineArchive,
    [ValidateSet('r14','checkout')][string]$RuntimeSource = 'r14',
    [string]$FrozenRuntime,
    [string]$Repository = 'benandteicher/noita-ew-coop-plus',
    [switch]$UploadDraft
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'release-preflight.ps1')
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot "dist\updater-$Version-build$Build" }
if (-not $BaselineArchive) { $BaselineArchive = Join-Path $projectRoot 'dist\Noita-EW-Coop-Plus-v1.9-R14-playtest-win.zip' }
if ($Repository -ne 'benandteicher/noita-ew-coop-plus') { throw 'This release workflow is pinned to the approved personal repository.' }
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output directory already exists; preserve previous packages and choose a new directory.' }
$sourceHead = & git -C $projectRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $sourceHead.Trim() -ne $SourceRevision) { throw 'Build from the isolated publication checkout at the exact SourceRevision commit.' }
$sourceStatus = & git -C $projectRoot status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0 -or $sourceStatus) { throw 'Publication source checkout must be clean before building. Commit approved source changes first.' }
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'Build computer requires the Windows .NET Framework C# compiler.' }
$buildTemp = Join-Path ([IO.Path]::GetTempPath()) ('coopplus-build-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $buildTemp | Out-Null
try {
    $metadata = @"
using System.Reflection;
[assembly: AssemblyTitle("Co-op+")]
[assembly: AssemblyProduct("Noita Entangled Worlds: Co-op+")]
[assembly: AssemblyDescription("Co-op+ approved-release launcher and updater")]
[assembly: AssemblyCompany("Co-op+ contributors")]
[assembly: AssemblyCopyright("IntQuant and contributors; MIT or Apache-2.0; see bundled notices")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: AssemblyFileVersion("$Version")]
[assembly: AssemblyInformationalVersion("$Version (build $Build)")]
"@
    $metadataPath = Join-Path $buildTemp 'AssemblyInfo.cs'
    [IO.File]::WriteAllText($metadataPath, $metadata)
    $icon = Join-Path $projectRoot 'noita_proxy\assets\icon.ico'
    & $compiler /nologo /optimize+ /target:exe /platform:anycpu "/out:$buildTemp\CoopPlus.exe" "/win32icon:$icon" (Join-Path $PSScriptRoot 'Bootstrap.cs') $metadataPath
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap compilation failed.' }
    $updaterSources = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.cs' -File | ForEach-Object { $_.FullName } | Where-Object { (Split-Path -Leaf $_) -ne 'Bootstrap.cs' })
    $backupSource = Join-Path $PSScriptRoot 'backup'
    if (Test-Path -LiteralPath $backupSource) { $updaterSources += @(Get-ChildItem -LiteralPath $backupSource -Filter '*.cs' -File | ForEach-Object { $_.FullName }) }
    & $compiler /nologo /optimize+ /target:exe /platform:anycpu "/out:$buildTemp\CoopUpdater.exe" "/win32icon:$icon" /r:System.Web.Extensions.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll $updaterSources $metadataPath
    if ($LASTEXITCODE -ne 0) { throw 'Updater compilation failed.' }
    $builderArguments = @('--source-revision', $SourceRevision, '--version', $Version, '--build', $Build, '--output', $OutputDirectory, '--baseline', $BaselineArchive, '--runtime-source', $RuntimeSource, '--bootstrap', (Join-Path $buildTemp 'CoopPlus.exe'), '--updater', (Join-Path $buildTemp 'CoopUpdater.exe'), '--repository', $Repository)
    if ($FrozenRuntime) { $builderArguments += @('--frozen-runtime', $FrozenRuntime) }
    & python (Join-Path $PSScriptRoot 'release_builder.py') @builderArguments
    if ($LASTEXITCODE -ne 0) { throw 'Release packaging or verification failed.' }
    if ($UploadDraft) {
        $gh = Get-Command gh -ErrorAction Stop
        & $gh.Source auth status --hostname github.com 1>$null 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'GitHub CLI sign-in required; run gh auth login --hostname github.com --web. Never paste a token into chat.' }
        $login = & $gh.Source api user --jq .login
        if ($LASTEXITCODE -ne 0 -or $login.Trim() -ne 'benandteicher') { throw 'Expected authenticated personal account benandteicher.' }
        $repoOwner = & $gh.Source repo view $Repository --json owner --jq .owner.login
        if ($LASTEXITCODE -ne 0 -or $repoOwner.Trim() -ne 'benandteicher') { throw 'Expected the approved personal repository.' }
        $tag = "coopplus-v$Version-b$Build"
        $targetState = Assert-CoopPublishingTarget -GhPath $gh.Source -Repository $Repository -Tag $tag -SourceRevision $SourceRevision
        if (-not $targetState.TagExists) {
            # Create only an absent ref, never force/update an existing tag. A
            # concurrent creation or any API failure stops before release upload.
            & $gh.Source api --method POST "repos/$Repository/git/refs" -f "ref=refs/tags/$tag" -f "sha=$SourceRevision" 1>$null 2>$null
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the release tag safely; no release was uploaded. Recheck GitHub state before retrying.' }
        }
        $verifiedTarget = Assert-CoopPublishingTarget -GhPath $gh.Source -Repository $Repository -Tag $tag -SourceRevision $SourceRevision
        if (-not $verifiedTarget.TagExists) { throw 'Release tag is unexpectedly missing; refusing to upload.' }
        $assets = @(Get-ChildItem -LiteralPath $OutputDirectory -File | Where-Object { $_.Name -ne 'DRAFT-NOTES.md' } | ForEach-Object { $_.FullName })
        & $gh.Source release create $tag @assets --repo $Repository --draft --latest=false --verify-tag --target $SourceRevision --title "Co-op+ $Version (build $Build) updater candidate" --notes-file (Join-Path $OutputDirectory 'DRAFT-NOTES.md')
        if ($LASTEXITCODE -ne 0) { throw 'Draft upload failed; local assets remain available.' }
    }
} finally {
    # The target is a fixed child of the OS temp directory created above.
    $resolvedBuildTemp = [IO.Path]::GetFullPath($buildTemp)
    $resolvedTempParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedBuildTemp.StartsWith($resolvedTempParent, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path -Leaf $resolvedBuildTemp).StartsWith('coopplus-build-')) {
        Remove-Item -LiteralPath $resolvedBuildTemp -Recurse -Force
    }
}
