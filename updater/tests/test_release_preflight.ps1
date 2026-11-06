# Focused publisher checks; mocked GitHub reads, no release/tag mutation.
$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'release-preflight.ps1')
$expected = 'a' * 40
$other = 'b' * 40
$script:annotationOid = 'c' * 40
$script:nestedOid = 'd' * 40
$script:passed = 0
function Assert-Case([string]$Name, [scriptblock]$Test) {
    & $Test
    $script:passed++
    Write-Output "PASS $Name"
}
function Check-Target([scriptblock]$Lookup) {
    Assert-CoopPublishingTarget -GhPath 'unused-gh' -Repository 'benandteicher/noita-ew-coop-plus' -Tag 'coopplus-v1.9.14.2-b2' -SourceRevision $expected -Lookup $Lookup
}
function Response($Target, $Release = $null) {
    $ref = if ($null -eq $Target) { $null } else { @{ target = $Target } }
    @{ data = @{ repository = @{ nameWithOwner = 'benandteicher/noita-ew-coop-plus'; release = $Release; ref = $ref } } } | ConvertTo-Json -Depth 10 -Compress
}
function Must-Fail([scriptblock]$Test, [string]$Message) {
    try { & $Test | Out-Null } catch { if ($_.Exception.Message -notlike "*$Message*") { throw }; return }
    throw "Expected rejection: $Message"
}
Assert-Case 'absent tag is proven by successful nullable lookup' {
    $s = Check-Target { param($Arguments) @{ ExitCode = 0; Json = (Response $null) } }
    if ($s.TagExists) { throw 'Tag should be absent.' }
}
Assert-Case 'matching lightweight tag accepted without moving it' {
    $s = Check-Target { param($Arguments) @{ ExitCode = 0; Json = (Response @{ __typename = 'Commit'; oid = $expected }) } }
    if (-not $s.TagExists -or $s.Commit -ne $expected) { throw 'Wrong resolved commit.' }
}
Assert-Case 'conflicting lightweight tag rejected' {
    Must-Fail { Check-Target { param($Arguments) @{ ExitCode = 0; Json = (Response @{ __typename = 'Commit'; oid = $other }) } } } 'conflicting commit'
}
Assert-Case 'nested annotated matching tag peeled to commit' {
    $s = Check-Target {
        param($Arguments)
        if ($Arguments[1] -eq 'graphql') { return @{ ExitCode = 0; Json = (Response @{ __typename = 'Tag'; oid = $script:annotationOid }) } }
        if ($Arguments[1].EndsWith($script:annotationOid)) { return @{ ExitCode = 0; Json = (@{ sha = $script:annotationOid; object = @{ type = 'tag'; sha = $script:nestedOid } } | ConvertTo-Json -Compress) } }
        if ($Arguments[1].EndsWith($script:nestedOid)) { return @{ ExitCode = 0; Json = (@{ sha = $script:nestedOid; object = @{ type = 'commit'; sha = $expected } } | ConvertTo-Json -Compress) } }
        throw 'Unexpected lookup'
    }
    if ($s.Commit -ne $expected) { throw 'Annotation was not peeled.' }
}
Assert-Case 'annotated conflicting tag rejected' {
    Must-Fail { Check-Target {
        param($Arguments)
        if ($Arguments[1] -eq 'graphql') { return @{ ExitCode = 0; Json = (Response @{ __typename = 'Tag'; oid = $script:annotationOid }) } }
        @{ ExitCode = 0; Json = (@{ sha = $script:annotationOid; object = @{ type = 'commit'; sha = $other } } | ConvertTo-Json -Compress) }
    } } 'conflicting commit'
}
Assert-Case 'authentication/network exit and thrown lookup failures reject' {
    Must-Fail { Check-Target { param($Arguments) @{ ExitCode = 1; Json = '' } } } 'lookup failed'
    Must-Fail { Check-Target { param($Arguments) throw 'network unavailable' } } 'lookup failed'
}
Assert-Case 'malformed partial and GraphQL error results reject' {
    foreach ($bad in @('not json', '{}', '{"errors":[{"message":"denied"}],"data":null}')) {
        Must-Fail { Check-Target { param($Arguments) @{ ExitCode = 0; Json = $bad } } } 'refus'
    }
}
Assert-Case 'annotation lookup failure cannot mean absent tag' {
    Must-Fail { Check-Target {
        param($Arguments)
        if ($Arguments[1] -eq 'graphql') { return @{ ExitCode = 0; Json = (Response @{ __typename = 'Tag'; oid = $script:annotationOid }) } }
        @{ ExitCode = 1; Json = '' }
    } } 'lookup failed'
}
Assert-Case 'existing release rejected even with absent ref' {
    Must-Fail { Check-Target { param($Arguments) @{ ExitCode = 0; Json = (Response $null @{ id = 'already exists' }) } } } 'Release already exists'
}
Assert-Case 'draft-only and verified-tag flags remain in publisher' {
    $publisher = Get-Content -Raw (Join-Path (Split-Path -Parent $PSScriptRoot) 'build-release.ps1')
    if ($publisher -notmatch 'release create.*--draft --latest=false --verify-tag' -or $publisher -match 'release edit|--force') { throw 'Unexpected publication policy.' }
}
Write-Output "RESULT: $script:passed focused publishing cases passed; no GitHub mutations or binary rebuild."
