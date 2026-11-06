[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceRevision,
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version = '1.9.14.1',
    [ValidateRange(1,2147483647)][int]$Build = 1,
    [string]$OutputDirectory,
    [string]$BaselineArchive,
    [ValidateSet('r14','checkout')][string]$RuntimeSource = 'r14',
    [string]$Repository = 'benandteicher/noita-ew-coop-plus',
    [switch]$UploadDraft
)
$ErrorActionPreference = 'Stop'
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
    & $compiler /nologo /optimize+ /target:exe /platform:anycpu "/out:$buildTemp\CoopPlus.exe" (Join-Path $PSScriptRoot 'Bootstrap.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Bootstrap compilation failed.' }
    & $compiler /nologo /optimize+ /target:exe /platform:anycpu "/out:$buildTemp\CoopUpdater.exe" /r:System.Web.Extensions.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll (Join-Path $PSScriptRoot 'Engine.cs') (Join-Path $PSScriptRoot 'Program.cs')
    if ($LASTEXITCODE -ne 0) { throw 'Updater compilation failed.' }
    & python (Join-Path $PSScriptRoot 'release_builder.py') --source-revision $SourceRevision --version $Version --build $Build --output $OutputDirectory --baseline $BaselineArchive --runtime-source $RuntimeSource --bootstrap (Join-Path $buildTemp 'CoopPlus.exe') --updater (Join-Path $buildTemp 'CoopUpdater.exe') --repository $Repository
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
        & $gh.Source release view $tag --repo $Repository --json isDraft 2>$null
        if ($LASTEXITCODE -eq 0) { throw 'Release tag already exists; never overwrite a previous release.' }
        $assets = @(Get-ChildItem -LiteralPath $OutputDirectory -File | Where-Object { $_.Name -ne 'DRAFT-NOTES.md' } | ForEach-Object { $_.FullName })
        & $gh.Source release create $tag @assets --repo $Repository --draft --latest=false --target $SourceRevision --title "Co-op+ $Version (build $Build) updater candidate" --notes-file (Join-Path $OutputDirectory 'DRAFT-NOTES.md')
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
