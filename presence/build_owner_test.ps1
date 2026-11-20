[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][ValidatePattern('^[0-9]{17,20}$')][string]$ApplicationId,
 [Parameter(Mandatory=$true)][string]$OutputDirectory,
 [Parameter(Mandatory=$true)][string]$BuildEnvironment,
 [Parameter(Mandatory=$true)][string]$CargoTargetDirectory,
 [string]$Python='python'
)
$ErrorActionPreference='Stop'
$env:GIT_CONFIG_GLOBAL='NUL'
$env:GIT_CONFIG_NOSYSTEM='1'
$source=Split-Path $PSScriptRoot -Parent
function Assert-FrozenSourceBytes([string]$Root) {
 $inv=Get-Content -LiteralPath (Join-Path $Root 'PUBLICATION-INVENTORY.json') -Raw|ConvertFrom-Json
 foreach($row in $inv.files.PSObject.Properties){
  if([IO.Path]::IsPathFullyQualified($row.Name) -or $row.Name.Split('/') -contains '..'){throw 'Unsafe source inventory path'}
  $file=Join-Path $Root $row.Name
  if(-not(Test-Path -LiteralPath $file -PathType Leaf) -or (Get-Item -LiteralPath $file).Length -ne $row.Value.size -or (Get-FileHash -LiteralPath $file).Hash.ToLowerInvariant() -ne $row.Value.sha256){throw ('Frozen source working bytes differ: '+$row.Name)}
 }
}
$output=[IO.Path]::GetFullPath($OutputDirectory)
$workspace=[IO.Path]::GetFullPath((Split-Path $source -Parent)).TrimEnd('\')+'\'
if(-not $output.StartsWith($workspace,[StringComparison]::OrdinalIgnoreCase) -or $output.StartsWith(([IO.Path]::GetFullPath($source).TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)){throw 'Owner build output must be a new sibling of source inside this isolated candidate workspace'}
for($parent=[IO.DirectoryInfo]::new($output);$null -ne $parent;$parent=$parent.Parent){if($parent.Exists -and ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked/reparse build paths are refused'}}
if(Test-Path -LiteralPath $output){throw 'Preserve prior artifacts; choose a new output directory'}
# Native MSBuild scratch paths exceed MAX_PATH under the long source/output root.
# Use one explicit new physical short build root, never a junction/subst or policy change.
if(-not[IO.Path]::IsPathFullyQualified($CargoTargetDirectory)){throw 'Provide a new absolute short Cargo target directory'}
$compileTarget=[IO.Path]::GetFullPath($CargoTargetDirectory).TrimEnd('\')
if((Split-Path $compileTarget -Parent) -ne 'C:\' -or (Split-Path $compileTarget -Leaf) -notmatch '^cp-discord-owner-b19-[a-z0-9-]{1,20}$'){throw 'Cargo target must be a new C:\cp-discord-owner-b19-<suffix> directory'}
for($parent=[IO.DirectoryInfo]::new($compileTarget);$null -ne $parent;$parent=$parent.Parent){if($parent.Exists -and ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Linked/reparse compiler paths are refused'}}
if(Test-Path -LiteralPath $compileTarget){throw 'Preserve existing compiler outputs; choose a new short Cargo target directory'}

if([UInt64]::Parse($ApplicationId) -eq 0){throw 'Use only the public ID of the owner-registered Noita: Co-op+ application'}
if(-not(Test-Path -LiteralPath $BuildEnvironment -PathType Leaf)){throw 'Provide the existing reviewed maintainer build-environment script; no software is installed by this command'}
if((& git --no-optional-locks -C $source status --porcelain)){throw 'Freeze/review source first; original source must be clean'}
Assert-FrozenSourceBytes $source
$reviewed=(& git --no-optional-locks -C $source rev-parse HEAD).Trim()
$null=New-Item -ItemType Directory -Path $output
$empty=Join-Path $output 'empty-git-template';$null=New-Item -ItemType Directory -Path $empty
$env:GIT_TEMPLATE_DIR=$empty
$ownedSource=Join-Path $output 'owner-source'
& git -c core.attributesFile=NUL clone --no-hardlinks --config core.autocrlf=false --config core.attributesFile=NUL --config ('core.hooksPath='+$empty) -- $source $ownedSource
if($LASTEXITCODE -ne 0){throw 'Cannot make isolated owner configuration build'}
Assert-FrozenSourceBytes $ownedSource
$config=Join-Path $ownedSource 'noita_proxy\discord-presence.json'
$utf8=[Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText($config,('{"application_id":"'+$ApplicationId+'","artwork_image":"coopplus_logo"}'+[Environment]::NewLine),$utf8)
# One public configuration value and its inventory row change; gameplay source is retained.
$inventoryPath=Join-Path $ownedSource 'PUBLICATION-INVENTORY.json'
$inventory=Get-Content -LiteralPath $inventoryPath -Raw|ConvertFrom-Json
$inventory.files.'noita_proxy/discord-presence.json'.size=(Get-Item -LiteralPath $config).Length
$inventory.files.'noita_proxy/discord-presence.json'.sha256=(Get-FileHash -LiteralPath $config).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($inventoryPath,($inventory|ConvertTo-Json -Depth 20),$utf8)
Assert-FrozenSourceBytes $ownedSource
$changed=@(& git --no-optional-locks -C $ownedSource diff --name-only)
if($changed.Count -ne 2 -or @($changed|Where-Object {$_ -notin @('noita_proxy/discord-presence.json','PUBLICATION-INVENTORY.json')}).Count){throw 'Owner build changed something beyond the public application binding/inventory'}
& git -C $ownedSource add -- noita_proxy/discord-presence.json PUBLICATION-INVENTORY.json
& git -C $ownedSource -c commit.gpgSign=false -c user.name='CoopPlus Local Release Preparation' -c user.email='local-release@example.invalid' commit -m 'Bind owner-provided public Discord application ID for reviewed TEST19'
if($LASTEXITCODE -ne 0){throw 'Cannot record isolated owner source configuration'}
$ownerRevision=(& git --no-optional-locks -C $ownedSource rev-parse HEAD).Trim()
$buildInfo=@{reviewedSourceRevision=$reviewed;ownerSourceRevision=$ownerRevision;applicationId=$ApplicationId;runtimeId='coopplus-20261112-b19-presence1';publicMutation=$false;gameplaySourceChanges=0}
[IO.File]::WriteAllText((Join-Path $output 'OWNER-BUILD-INPUTS.json'),($buildInfo|ConvertTo-Json),$utf8)
# The environment script only selects existing compiler/dependency paths.
. $BuildEnvironment
$null=New-Item -ItemType Directory -Path $compileTarget
$env:CARGO_TARGET_DIR=$compileTarget
$env:COOPPLUS_RUNTIME_ID='coopplus-20261112-b19-presence1'
Push-Location (Join-Path $ownedSource 'noita_proxy')
try{
 & cargo test --locked --release --lib discord_ -- --test-threads=1
 if($LASTEXITCODE -ne 0){throw 'Focused Discord checks failed; no package is sealed'}
 & cargo build --locked --release --bin noita_proxy
 if($LASTEXITCODE -ne 0){throw 'Owner-bound proxy build failed'}
}finally{Pop-Location}
& (Join-Path $ownedSource 'playtest\build.ps1') -Output (Join-Path $output 'Co-op+ Playtest.exe')
if($LASTEXITCODE -ne 0){throw 'Owner-bound TEST launcher build failed'}
$compiledProxy=Join-Path $compileTarget 'release\noita_proxy.exe'
$staging=Join-Path $output 'build-target\release';$null=New-Item -ItemType Directory -Path $staging
$stagedProxy=Join-Path $staging 'noita_proxy.exe'
Copy-Item -LiteralPath $compiledProxy -Destination $stagedProxy
$buildInfo.cargoTargetDirectory=$compileTarget
$buildInfo.compiledProxyPath=$compiledProxy
$buildInfo.stagedProxyPath=$stagedProxy
$buildInfo.compiledProxySha256=(Get-FileHash -LiteralPath $compiledProxy).Hash.ToLowerInvariant()
$buildInfo.proxySha256=(Get-FileHash -LiteralPath $stagedProxy).Hash.ToLowerInvariant()
if($buildInfo.compiledProxySha256 -ne $buildInfo.proxySha256){throw 'Compiled/staged proxy hash differs'}
$buildInfo.launcherSha256=(Get-FileHash -LiteralPath (Join-Path $output 'Co-op+ Playtest.exe')).Hash.ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $output 'OWNER-BUILD-INPUTS.json'),($buildInfo|ConvertTo-Json),$utf8)
& $Python -B (Join-Path $ownedSource 'presence\package_test.py') --source $ownedSource --proxy $stagedProxy --launcher (Join-Path $output 'Co-op+ Playtest.exe') --output $output
if($LASTEXITCODE -ne 0){throw 'Owner package verification failed'}
Write-Host 'Local owner-bound TEST19 prepared. No game launched or public action taken. Genuine Discord card/privacy/exit checks still need the owner desktop before publication approval.'
