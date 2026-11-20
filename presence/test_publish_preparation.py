"""Offline counterexamples only. Never GitHub/Git/process/game execution."""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import publish_test as publication


class PreparationTests(unittest.TestCase):
    def fixture(self, root):
        path = root / 'synthetic.txt'
        path.write_bytes(b'synthetic asset')
        receipt = {'files': {path.name: publication.digest(path)}}
        release = {'tag_name': publication.TAG, 'prerelease': True, 'draft': True,
                   'assets': [{'name': path.name, 'size': path.stat().st_size,
                               'digest': 'sha256:' + receipt['files'][path.name]}]}
        return receipt, release

    def test_exact_draft_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, release = self.fixture(root)
            publication.check_release(release, root, receipt, True)

    def test_draft_size_digest_or_extra_asset_refuses(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, original = self.fixture(root)
            for field, value in [('size', 0), ('digest', None), ('name', 'unexpected')]:
                release = copy.deepcopy(original)
                release['assets'][0][field] = value
                with self.assertRaises(RuntimeError):
                    publication.check_release(release, root, receipt, True)
            release = copy.deepcopy(original)
            release['assets'] *= 2
            with self.assertRaises(RuntimeError):
                publication.check_release(release, root, receipt, True)

    def test_normal_or_already_public_release_refuses_draft_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, original = self.fixture(root)
            for field, value in [('prerelease', False), ('draft', False), ('tag_name', publication.LATEST)]:
                release = copy.deepcopy(original)
                release[field] = value
                with self.assertRaises(RuntimeError):
                    publication.check_release(release, root, receipt, True)

    def test_only_http404_counts_as_absent(self):
        for status in [401, 403, 429, 500, 503]:
            with patch.object(publication.OPENER, 'open', side_effect=urllib.error.HTTPError(
                    'https://api.github.com/fixture', status, 'synthetic', {}, io.BytesIO())):
                with self.assertRaises(RuntimeError):
                    publication.remote('fixture', absent=True)
        with patch.object(publication.OPENER, 'open', side_effect=urllib.error.HTTPError(
                'https://api.github.com/fixture', 404, 'synthetic', {}, io.BytesIO())):
            self.assertIsNone(publication.remote('fixture', absent=True))
            with self.assertRaises(RuntimeError):
                publication.remote('fixture')

    def test_publish_requires_both_explicit_acceptances_before_io(self):
        for flags in [[], ['--discord-card-accepted'], ['--test-publication-approved']]:
            with patch.object(sys, 'argv', ['publisher', '--build', 'not-an-output', '--publish', *flags]), \
                 patch.object(publication, 'local_assets', side_effect=AssertionError('must not read')):
                with self.assertRaisesRegex(RuntimeError, 'separate TEST-only'):
                    publication.main()

    def test_default_is_read_only_with_unused_identity_checks(self):
        with patch.object(sys, 'argv', ['publisher', '--build', 'fixture']), \
             patch.object(publication, 'local_assets', return_value=(None, None, None, None)), \
             patch.object(publication, 'baseline') as baseline, \
             patch.object(publication, 'remote', return_value=None) as remote, \
             patch.object(publication, 'git', side_effect=AssertionError('no writes')), \
             patch.object(publication, 'gh', side_effect=AssertionError('no writes')):
            publication.main()
            self.assertEqual(remote.call_count, 2)
            baseline.assert_called_once()

    def test_existing_public_state_must_be_preserved(self):
        before = {'releases': {'old': {'assets': ['same']}},
                  'branches': {'main': publication.MAIN}, 'tags': {'stable': 'same'}}
        after = copy.deepcopy(before)
        after['releases']['new'] = {}
        after['tags'][publication.TAG] = 'new'
        publication.preserve_snapshot(before, after)
        for section, key in [('branches', 'main'), ('tags', 'stable'), ('releases', 'old')]:
            bad = copy.deepcopy(after)
            bad[section][key] = 'changed'
            with self.assertRaises(RuntimeError):
                publication.preserve_snapshot(before, bad)

    def test_authenticated_pending_tag_lookup_distinguishes_null_and_draft(self):
        repository = {'nameWithOwner': publication.REPO, 'viewerPermission': 'WRITE', 'release': None}
        with patch.object(publication, 'gh', return_value=json.dumps({'data': {'repository': repository}})) as gh:
            self.assertIsNone(publication.authenticated_release())
            self.assertIn('graphql', gh.call_args.args)
        repository['release'] = {'databaseId': 123, 'tagName': publication.TAG,
                                 'isDraft': True, 'isPrerelease': True}
        with patch.object(publication, 'gh', return_value=json.dumps({'data': {'repository': repository}})):
            self.assertEqual(publication.authenticated_release()['databaseId'], 123)

    def test_incomplete_unauthorized_or_graphql_error_never_means_absent(self):
        for response in [{}, {'errors': ['synthetic']}, {'data': {'repository': None}},
                         {'data': {'repository': {'nameWithOwner': publication.REPO,
                                                   'viewerPermission': 'READ', 'release': None}}}]:
            with patch.object(publication, 'gh', return_value=json.dumps(response)):
                with self.assertRaises(RuntimeError):
                    publication.authenticated_release()

    def test_pending_tag_draft_refuses_before_push(self):
        with patch.object(sys, 'argv', ['publisher', '--build', 'fixture', '--publish',
                                        '--discord-card-accepted', '--test-publication-approved']), \
             patch.object(publication, 'local_assets', return_value=(None, None, None, None)), \
             patch.object(publication, 'baseline'), \
             patch.object(publication, 'remote', return_value=None), \
             patch.object(publication, 'authenticated_release', return_value={'isDraft': True}), \
             patch.object(publication, 'git', side_effect=AssertionError('must not push')):
            with self.assertRaisesRegex(RuntimeError, 'pending-tag draft'):
                publication.main()

    def test_simulated_full_flow_reads_numeric_draft_and_refuses_bad_digest_before_publish(self):
        # Every network/Git/gh operation is replaced. This is an ordering fixture,
        # not evidence that a real GitHub draft was uploaded or published.
        for bad_digest in [False, True]:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                receipt, draft = self.fixture(root)
                receipt['publicSourceRevision'] = 'a' * 40
                draft['id'] = 123
                if bad_digest:
                    draft['assets'][0]['digest'] = 'sha256:' + '0' * 64
                events = []

                def remote(path, absent=False):
                    return None if absent else {'object': {'sha': 'a' * 40}}

                def gh(*args):
                    events.append(args)
                    if args[:2] == ('release', 'create'):
                        return ''
                    if args == ('api', 'repos/' + publication.REPO + '/releases/123'):
                        return json.dumps(draft)
                    if args[:2] == ('release', 'edit'):
                        return ''
                    raise AssertionError('Unexpected real/by-tag draft operation: ' + str(args))

                with patch.object(sys, 'argv', ['publisher', '--build', str(root), '--publish',
                                                '--discord-card-accepted', '--test-publication-approved']), \
                     patch.object(publication, 'local_assets', return_value=(root, root, receipt, root / 'notes')), \
                     patch.object(publication, 'baseline'), \
                     patch.object(publication, 'remote', side_effect=remote), \
                     patch.object(publication, 'authenticated_release', side_effect=[None, {
                         'databaseId': 123, 'isDraft': True, 'isPrerelease': True}]), \
                     patch.object(publication, 'public_snapshot', return_value={}), \
                     patch.object(publication, 'preserve_snapshot'), \
                     patch.object(publication, 'anonymous_verify', return_value={'mock': True}), \
                     patch.object(publication, 'git', return_value=''), \
                     patch.object(publication, 'gh', side_effect=gh):
                    if bad_digest:
                        with self.assertRaisesRegex(RuntimeError, 'digest differs'):
                            publication.main()
                    else:
                        publication.main()
                published = any(event[:2] == ('release', 'edit') for event in events)
                self.assertEqual(published, not bad_digest)
                self.assertEqual((root / 'PUBLIC-TEST19-VERIFICATION.json').exists(), not bad_digest)


if __name__ == '__main__':
    unittest.main(verbosity=2)
