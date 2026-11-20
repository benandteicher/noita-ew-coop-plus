# Read-only GitHub checks shared by the draft publisher and its focused tests.
function Invoke-CoopGitHubJson {
    param([string]$GhPath, [string[]]$Arguments, [scriptblock]$Lookup)
    try {
        if ($Lookup) { $result = & $Lookup $Arguments }
        else {
            $output = & $GhPath @Arguments 2>$null
            $result = [pscustomobject]@{ ExitCode = $LASTEXITCODE; Json = ($output -join "`n") }
        }
        if ($null -eq $result -or $result.ExitCode -ne 0) { throw 'GitHub command failed.' }
        $json = $result.Json | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $json -or $json.errors) { throw 'Missing response or GraphQL errors.' }
        return $json
    } catch {
        throw 'GitHub lookup failed or returned an uncertain result; refusing to upload. Check authentication/network access and retry.'
    }
}

function Assert-CoopPublishingTarget {
    param(
        [Parameter(Mandatory=$true)][string]$GhPath,
        [Parameter(Mandatory=$true)][string]$Repository,
        [Parameter(Mandatory=$true)][string]$Tag,
        [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceRevision,
        [scriptblock]$Lookup
    )
    if ($Repository -ne 'benandteicher/noita-ew-coop-plus' -or $Tag -notmatch '^coopplus-v[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+-b[0-9]+$') {
        throw 'Unexpected publishing repository/tag.'
    }
    if ($Tag -in @('coopplus-v1.9.18.0-b7','coopplus-v1.9.18.1-b8','coopplus-v1.9.18.2-b9','coopplus-v1.9.18.3-b10','coopplus-v1.9.18.4-b11','coopplus-v1.9.18.5-b12','coopplus-v1.9.18.6-b13','coopplus-v1.9.18.7-b14','coopplus-v1.9.18.8-b15','coopplus-v1.9.18.9-b16')) {
        throw 'Superseded unpublished assets are not publishable. Use the current startup/session correction sealed normal/recovery assets and frozen publication commands.'
    }
    # A successful nullable lookup proves absence. Nonzero exit, errors, missing
    # fields, or inaccessible repository are never interpreted as a missing tag.
    $query = 'query($owner:String!,$name:String!,$tag:String!,$ref:String!){repository(owner:$owner,name:$name){nameWithOwner release(tagName:$tag){id} ref(qualifiedName:$ref){target{__typename oid}}}}'
    $parts = $Repository.Split('/')
    $response = Invoke-CoopGitHubJson -GhPath $GhPath -Lookup $Lookup -Arguments @('api','graphql','-f',"query=$query",'-f',"owner=$($parts[0])",'-f',"name=$($parts[1])",'-f',"tag=$Tag",'-f',"ref=refs/tags/$Tag")
    $repo = $response.data.repository
    if ($null -eq $repo -or $repo.nameWithOwner -cne $Repository -or
        $repo.PSObject.Properties.Name -notcontains 'release' -or $repo.PSObject.Properties.Name -notcontains 'ref') {
        throw 'GitHub repository/release/tag lookup is incomplete; refusing to upload.'
    }
    if ($null -ne $repo.release) { throw 'Release already exists; never overwrite a previous release.' }
    if ($null -eq $repo.ref) { return [pscustomobject]@{ TagExists = $false; Commit = $null } }
    $target = $repo.ref.target
    $kind = $target.__typename
    $oid = $target.oid
    $seen = @{}
    # Git tags may point to annotated tags. Peel each object, refusing cycles,
    # malformed/non-commit targets and excessive nesting instead of guessing.
    for ($depth = 0; $depth -lt 16; $depth++) {
        if ($oid -notmatch '^[0-9a-f]{40}$' -or $seen.ContainsKey($oid)) { throw 'Invalid/cyclic Git tag target; refusing to upload.' }
        $seen[$oid] = $true
        if ($kind -ceq 'Commit') {
            if ($oid -cne $SourceRevision) { throw "Existing tag resolves to conflicting commit $oid; expected $SourceRevision. Tags are never moved." }
            return [pscustomobject]@{ TagExists = $true; Commit = $oid }
        }
        if ($kind -cne 'Tag') { throw 'Git tag does not resolve to a commit; refusing to upload.' }
        $annotation = Invoke-CoopGitHubJson -GhPath $GhPath -Lookup $Lookup -Arguments @('api',"repos/$Repository/git/tags/$oid")
        if ($annotation.sha -cne $oid -or $null -eq $annotation.object) { throw 'Incomplete annotated-tag lookup; refusing to upload.' }
        $oid = $annotation.object.sha
        switch -CaseSensitive ($annotation.object.type) {
            'commit' { $kind = 'Commit' }
            'tag' { $kind = 'Tag' }
            default { throw 'Annotated tag does not resolve to a commit; refusing to upload.' }
        }
    }
    throw 'Git tag nesting exceeds the supported lookup depth; refusing to upload.'
}
