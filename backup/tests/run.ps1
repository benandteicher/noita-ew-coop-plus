[CmdletBinding()]
param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\target\backup-tests'), [switch]$ExistingProcess)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Test output already exists. Choose a fresh directory.' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:exe "/out:$OutputDirectory\noita.exe" (Join-Path $PSScriptRoot 'DummyNoita.cs')
if ($LASTEXITCODE -ne 0) { throw 'Dummy compile failed' }
& $compiler /nologo /target:exe "/out:$OutputDirectory\BackupTests.exe" /r:System.Web.Extensions.dll (Join-Path $PSScriptRoot '..\BackupEngine.cs') (Join-Path $PSScriptRoot '..\RonSelection.cs') (Join-Path $PSScriptRoot '..\LaunchOverrides.cs') (Join-Path $PSScriptRoot 'BackupTests.cs')
if ($LASTEXITCODE -ne 0) { throw 'Test compile failed' }
$fixtures = Join-Path ([IO.Path]::GetTempPath()) ('CoopPlusBackup-Fixtures-' + [Guid]::NewGuid().ToString('N'))
$processArgument = if ($ExistingProcess) { '--existing-process' } else { Join-Path $OutputDirectory 'noita.exe' }
Write-Output "Disposable fixtures: $fixtures"
& (Join-Path $OutputDirectory 'BackupTests.exe') $fixtures $processArgument
if ($LASTEXITCODE -ne 0) { throw 'Focused backup tests failed' }
