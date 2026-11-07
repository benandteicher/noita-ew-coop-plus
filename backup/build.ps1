[CmdletBinding()]
param([string]$OutputDirectory = (Join-Path $PSScriptRoot '..\dist\CoopPlusBackup-0.1.2'))
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Output already exists. Choose a fresh directory to preserve prior packages.' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$icon = Join-Path $PSScriptRoot '..\noita_proxy\assets\icon.ico'
& $compiler /nologo /optimize+ /target:winexe /platform:anycpu "/out:$OutputDirectory\CoopPlusBackup.exe" "/win32icon:$icon" /r:System.Web.Extensions.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll (Join-Path $PSScriptRoot 'BackupEngine.cs') (Join-Path $PSScriptRoot 'RonSelection.cs') (Join-Path $PSScriptRoot 'LaunchOverrides.cs') (Join-Path $PSScriptRoot 'Program.cs')
if ($LASTEXITCODE -ne 0) { throw 'Backup compilation failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'START-HERE.txt') -Destination $OutputDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\LICENSE-MIT') -Destination $OutputDirectory
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\LICENSE-APACHE') -Destination $OutputDirectory
Get-FileHash -LiteralPath (Join-Path $OutputDirectory 'CoopPlusBackup.exe') -Algorithm SHA256 | Format-List
