"""Future TEST19 only. Default: local/public read verification; never publication.

Publication requires three explicit switches after the separate owner acceptance
and user approval. No normal publisher/feed, Pages deployment, overwrite or force.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

REPO = 'benandteicher/noita-ew-coop-plus'
TAG = 'coopplus-test-v1.9.18.12-b19-discord-r1'
NAME = 'Noita-CoopPlus-1.9.18.12-build19-TEST-DISCORD-r1-SEALED.zip'
MAIN = '1b02c881507b6b9b1323b19417a40d78321e0e88'
LATEST = 'coopplus-v1.9.17.1-b6'
API = 'https://api.github.com/repos/' + REPO + '/'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def remote(path, absent=False):
    request = urllib.request.Request(API + path, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'CoopPlus-TEST19-verification',
    })
    try:
        with OPENER.open(request, timeout=30) as response:
            require(not response.headers.get('Link'), 'Paginated public state needs review')
            return json.load(response)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        if absent and status == 404:
            return None
        raise RuntimeError('Public read failed: HTTP ' + str(status)) from None


def baseline():
    latest = remote('releases/latest')
    main = remote('git/ref/heads/main')
    require(latest['tag_name'] == LATEST and latest['id'] == 437994762,
            'Stable Latest changed; inspect before any publication')
    require(main['object']['sha'] == MAIN, 'Public main changed; inspect first')


def public_snapshot():
    releases = remote('releases?per_page=100')
    branches = remote('branches?per_page=100')
    tags = remote('tags?per_page=100')
    return {
        'releases': {str(r['id']): {
            'tag': r['tag_name'], 'prerelease': r['prerelease'], 'draft': r['draft'],
            'assets': sorted((a['id'], a['name'], a['size'], a.get('digest'))
                             for a in r['assets']),
        } for r in releases},
        'branches': {b['name']: b['commit']['sha'] for b in branches},
        'tags': {t['name']: t['commit']['sha'] for t in tags},
    }


def preserve_snapshot(before, after):
    for section in before:
        for key, value in before[section].items():
            require(after[section].get(key) == value, 'Existing public state changed: ' + section)
    require(after['branches'] == before['branches'], 'Unexpected branch change')
    require(set(after['tags']) - set(before['tags']) == {TAG}, 'Unexpected tag change')
    require(len(set(after['releases']) - set(before['releases'])) == 1,
            'Unexpected release change')


def git(source, *args):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    return subprocess.check_output(['git', '--no-optional-locks', '-C', str(source), *args],
                                   env=env, timeout=120).decode().strip()


def local_assets(build):
    build = build.resolve(strict=True)
    for part in (build, *build.parents):
        require(not (getattr(part.lstat(), 'st_file_attributes', 0) & 0x400),
                'Linked/reparse build paths refused')
    receipt = json.loads((build / 'FROZEN-TEST19.json').read_text(encoding='utf8'))
    require(receipt['tag'] == TAG and receipt['runtimeId'] == 'coopplus-20261112-b19-presence1',
            'Wrong dedicated TEST artifact')
    revision = receipt['sourceRevision']
    public = receipt['publicSourceRevision']
    tree = receipt['sourceTree']
    require(all(isinstance(s, str) and re.fullmatch('[0-9a-f]{40}', s)
                for s in (revision, public, tree)), 'Invalid frozen source identifiers')
    source = build / 'owner-source'
    for part in (source, *source.parents):
        require(part.is_dir() and not part.is_symlink()
                and not (getattr(part.lstat(), 'st_file_attributes', 0) & 0x400),
                'Linked/reparse owner source refused')
    require(git(source, 'rev-parse', 'HEAD') == revision and not git(source, 'status', '--porcelain'),
            'Owner source is no longer the frozen clean revision')
    require(git(source, 'rev-list', '--parents', '-1', public) == public,
            'Public source must be parentless; never publish inherited local history')
    require(receipt['publicSourceTree'] == tree == git(source, 'rev-parse', public + '^{tree}')
            == git(source, 'rev-parse', revision + '^{tree}'), 'Public and owner trees differ')
    expected = {NAME, 'CoopPlus-TEST19-source-' + revision[:7] + '.zip',
                'BUILD-PROVENANCE.json', 'SHA256SUMS.txt'}
    require(set(receipt['files']) == expected, 'Unexpected or incomplete TEST asset set')
    for name, sha in receipt['files'].items():
        require(Path(name).name == name and re.fullmatch('[0-9a-f]{64}', sha),
                'Unsafe asset inventory')
        path = build / name
        require(path.is_file() and not path.is_symlink()
                and not (getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
                and digest(path) == sha, 'Frozen asset bytes changed: ' + name)
    provenance = json.loads((build / 'BUILD-PROVENANCE.json').read_text(encoding='utf8'))
    require(provenance['sourceRevision'] == revision and provenance['sourceTree'] == tree
            and provenance['publicSourceRevision'] == public
            and provenance['publicSourceTree'] == tree, 'Source provenance mapping changed')
    inventory = json.loads((source / 'PUBLICATION-INVENTORY.json').read_text(encoding='utf8'))
    notes = source / 'presence/TEST19-RELEASE-NOTES.md'
    require(digest(notes) == inventory['files']['presence/TEST19-RELEASE-NOTES.md']['sha256'],
            'Release notes differ from reviewed source')
    return build, source, receipt, notes


def check_release(release, build, receipt, draft):
    require(release['tag_name'] == TAG and release['prerelease'] is True
            and release['draft'] is draft, 'Wrong release identity/type/state')
    assets = release['assets']
    require(len(assets) == len(receipt['files'])
            and {a['name'] for a in assets} == set(receipt['files']), 'Draft/public asset set differs')
    for asset in assets:
        require(asset['size'] == (build / asset['name']).stat().st_size
                and asset.get('digest') == 'sha256:' + receipt['files'][asset['name']],
                'Draft/public size or digest differs: ' + asset['name'])


def anonymous_verify(build, receipt):
    release = remote('releases/tags/' + TAG)
    check_release(release, build, receipt, False)
    require(remote('git/ref/tags/' + TAG)['object']['sha'] == receipt['publicSourceRevision'],
            'Public tag differs from reviewed parentless source')
    for asset in release['assets']:
        expected_url = 'https://github.com/' + REPO + '/releases/download/' + TAG + '/' + asset['name']
        require(asset['browser_download_url'] == expected_url, 'Unexpected public download URL')
        request = urllib.request.Request(expected_url, headers={'User-Agent': 'CoopPlus-TEST19-verification'})
        hasher = hashlib.sha256()
        size = 0
        with OPENER.open(request, timeout=30) as response:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                require(size <= asset['size'], 'Oversized anonymous download')
                hasher.update(chunk)
        require(size == asset['size'] and hasher.hexdigest() == receipt['files'][asset['name']],
                'Anonymous asset download differs')
    baseline()
    return {'result': 'PASS anonymous full TEST asset hashes, prerelease, tag, stable Latest and main',
            'tag': TAG, 'files': receipt['files'], 'sourceTree': receipt['sourceTree'],
            'publicSourceRevision': receipt['publicSourceRevision'], 'normalReleasePublished': False,
            'twoPcGameplayAcceptance': 'pending; not established by publication'}


def gh(*args):
    # These are reachable only after all explicit publication switches/preflight.
    return subprocess.check_output(['gh', *args], timeout=180).decode()


def authenticated_release():
    # REST's by-tag lookup is for published releases. Match GitHub CLI's own
    # GraphQL pending-tag lookup, then use a numeric REST ID for draft assets.
    query = ('query($owner:String!,$name:String!,$tagName:String!){'
             'repository(owner:$owner,name:$name){nameWithOwner viewerPermission '
             'release(tagName:$tagName){databaseId tagName isDraft isPrerelease}}}')
    response = json.loads(gh('api', 'graphql', '-f', 'query=' + query,
                             '-f', 'owner=benandteicher', '-f', 'name=noita-ew-coop-plus',
                             '-f', 'tagName=' + TAG))
    require(not response.get('errors'), 'Authenticated release lookup failed; never assume absence')
    repository = response.get('data', {}).get('repository')
    require(isinstance(repository, dict) and repository.get('nameWithOwner') == REPO
            and repository.get('viewerPermission') in ('ADMIN', 'MAINTAIN', 'WRITE')
            and 'release' in repository, 'Incomplete or unauthorized repository lookup')
    release = repository['release']
    if release is not None:
        require(isinstance(release, dict) and release.get('tagName') == TAG
                and type(release.get('databaseId')) is int and release['databaseId'] > 0
                and type(release.get('isDraft')) is bool, 'Invalid release identity lookup')
    return release


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--discord-card-accepted', action='store_true')
    parser.add_argument('--test-publication-approved', action='store_true')
    parser.add_argument('--verify-public', action='store_true')
    args = parser.parse_args()
    require(not (args.publish and args.verify_public), 'Choose one action')
    if args.publish:
        require(args.discord_card_accepted and args.test_publication_approved,
                'Actual Discord acceptance and separate TEST-only publication approval required')
    build, source, receipt, notes = local_assets(args.build)
    baseline()
    if args.verify_public:
        print(json.dumps(anonymous_verify(build, receipt), indent=2))
        return
    require(remote('releases/tags/' + TAG, absent=True) is None, 'Versioned release already exists')
    require(remote('git/ref/tags/' + TAG, absent=True) is None, 'Versioned tag already exists')
    if not args.publish:
        print('PASS local frozen bytes, parentless source, stable baseline and unused TEST identity. Read-only; nothing published.')
        return
    require(authenticated_release() is None, 'An existing public or pending-tag draft release must be preserved')
    before = public_snapshot()
    # Normal non-force push, dedicated parentless source/tag only; never main.
    git(source, 'push', 'https://github.com/' + REPO + '.git',
        receipt['publicSourceRevision'] + ':refs/tags/' + TAG)
    gh('release', 'create', TAG, '--repo', REPO, '--verify-tag', '--draft', '--prerelease',
       '--latest=false', '--title', 'Co-op+ standalone TEST19 Discord (NOT Latest)',
       '--notes-file', str(notes), *(str(build / n) for n in receipt['files']))
    identity = authenticated_release()
    require(identity is not None and identity['isDraft'] is True
            and identity.get('isPrerelease') is True, 'Created draft identity/type not established')
    draft = json.loads(gh('api', 'repos/' + REPO + '/releases/' + str(identity['databaseId'])))
    require(draft.get('id') == identity['databaseId'], 'Draft ID changed')
    check_release(draft, build, receipt, True)
    require(remote('git/ref/tags/' + TAG)['object']['sha'] == receipt['publicSourceRevision'],
            'Uploaded source tag differs')
    baseline()
    gh('release', 'edit', TAG, '--repo', REPO, '--draft=false', '--prerelease', '--latest=false')
    result = anonymous_verify(build, receipt)
    preserve_snapshot(before, public_snapshot())
    output = build / 'PUBLIC-TEST19-VERIFICATION.json'
    with output.open('x', encoding='utf8') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
