param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
if (Test-Path -LiteralPath $Output) { throw 'Preserve earlier artifacts; choose a new output file.' }
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /optimize+ /target:winexe /platform:anycpu /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:System.Xml.dll "/out:$Output" (Join-Path $PSScriptRoot 'Playtest.cs') (Join-Path $PSScriptRoot 'PlaytestMigration.cs') (Join-Path $PSScriptRoot 'PlaytestSession.cs')
if($LASTEXITCODE -ne 0){throw 'Playtest compilation failed'}
