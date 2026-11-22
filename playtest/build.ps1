param([Parameter(Mandatory=$true)][string]$Output,
      [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ManifestSha256,
      [string]$RefreshFrom23,
      [string]$RefreshFrom24)
$ErrorActionPreference='Stop'
if (Test-Path -LiteralPath $Output) { throw 'Preserve earlier artifacts; choose a new output file.' }
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$outputPath = [IO.Path]::GetFullPath($Output)
$binding = Join-Path ([IO.Path]::GetDirectoryName($outputPath)) ("StagingBundle." + $ManifestSha256.Substring(0,12) + ".cs")
$content = 'namespace CoopPlusPlaytest { internal static class StagingBundle { internal const string ManifestSha256 = "' + $ManifestSha256 + '"; } }'
if (Test-Path -LiteralPath $binding) {
    if ([IO.File]::ReadAllText($binding) -cne $content) { throw 'The generated Staging binding differs.' }
} else {
    [IO.File]::WriteAllText($binding, $content, [Text.UTF8Encoding]::new($false))
}
$sources = @('Playtest.cs','PlaytestMigration.cs','PlaytestSession.cs','StagingLauncher.cs',
             'StagingLogs.cs','StagingPreferences.cs','StagingUpdate.cs') | ForEach-Object { Join-Path $PSScriptRoot $_ }
$engine = Join-Path (Split-Path -Parent $PSScriptRoot) 'updater\Engine.cs'
$icon = Join-Path (Split-Path -Parent $PSScriptRoot) 'noita_proxy\assets\staging\icon.ico'
$previous = Join-Path $PSScriptRoot 'staging22-manifest.json'
if ((Get-FileHash -LiteralPath $previous).Hash.ToLowerInvariant() -ne '3f35fc14b55596d0789454d870c0f56712c5c0838770760161772c32d75d5f48') {
    throw 'The sealed Staging 22 manifest changed.'
}
$main = 'CoopPlusPlaytest.StagingLauncher'
$refreshResources = @()
if ($RefreshFrom23 -or $RefreshFrom24) {
    if (!$RefreshFrom23 -or !$RefreshFrom24) { throw 'Provide both original-bundle launcher refresh builds.' }
    $main = 'CoopPlusPlaytest.LauncherRefresh'
    $refreshBinding = Join-Path ([IO.Path]::GetDirectoryName($outputPath)) 'StagingRefreshBuild.cs'
    $refresh23 = Get-Item -LiteralPath $RefreshFrom23
    $refresh24 = Get-Item -LiteralPath $RefreshFrom24
    $hash23 = (Get-FileHash -LiteralPath $refresh23.FullName).Hash.ToLowerInvariant()
    $hash24 = (Get-FileHash -LiteralPath $refresh24.FullName).Hash.ToLowerInvariant()
    $refreshContent = 'namespace CoopPlusPlaytest { internal static class StagingRefreshBuild { internal const string Build23 = "' + $hash23 + '", Build24 = "' + $hash24 + '"; } }'
    if (Test-Path -LiteralPath $refreshBinding) { throw 'Choose a fresh launcher refresh output folder.' }
    [IO.File]::WriteAllText($refreshBinding, $refreshContent, [Text.UTF8Encoding]::new($false))
    $sources += (Join-Path $PSScriptRoot 'LauncherRefresh.cs'), $refreshBinding
    $refreshResources = @("/resource:$($refresh23.FullName),CoopPlus.Refresh23", "/resource:$($refresh24.FullName),CoopPlus.Refresh24")
}
& $compiler /nologo /optimize+ /target:winexe /platform:anycpu "/main:$main" /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:System.Xml.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll "/win32icon:$icon" "/resource:$icon,CoopPlus.Staging.ico" "/resource:$previous,CoopPlus.Staging22.manifest" "/out:$outputPath" @refreshResources @sources $engine $binding
if($LASTEXITCODE -ne 0){throw 'Playtest compilation failed'}
