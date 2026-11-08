#!/usr/bin/env python3
"""Protected completed-output retention and cross-job cache isolation contracts."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cache', ROOT / 'scripts/nixbench-cache.py')
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)
A = '/nix/store/' + 'a' * 32 + '-completed-dependency'
B = '/nix/store/' + 'b' * 32 + '-completed-output'


class Retention(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.recorder = cache.Recorder(self.root / 'state', self.root / 'roots', owner=os.getuid())
        self.recorder.init()
        original = os.path.lexists
        fixture = patch.object(cache.os.path, 'lexists', side_effect=lambda p: str(p) in [A, B] or original(p))
        fixture.start()
        self.addCleanup(fixture.stop)

    def test_completed_outputs_survive_a_later_failure_and_repeat_recording(self):
        self.recorder.record([A])
        self.recorder.record([B, A])
        output = self.root / 'outputs.txt'
        self.assertTrue(self.recorder.collect(output))
        self.assertEqual(cache.manifest(output), [A, B])
        self.assertEqual((self.recorder.gcroots / Path(A).name).readlink(), Path(A))
        # A failed dependent build never calls the success hook.
        self.assertNotIn('failed', output.read_text())
        self.recorder.cleanup()
        self.assertEqual(list(self.recorder.gcroots.iterdir()), [])

    def test_invalid_or_oversized_manifests_are_rejected(self):
        for invalid in ['--option', A + '/child', A + '.drv', A + '\n' + B, '/nix/store/' + 'e' * 32 + '-invalid-hash']:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                cache.validate([invalid])
        with patch.object(cache, 'MAX_ROOTS', 1), self.assertRaises(ValueError):
            cache.validate([A, B])
        with patch.object(cache, 'MAX_BYTES', 2), self.assertRaises(ValueError):
            cache.validate([A])

    def test_symlinked_manifest_is_not_read(self):
        real = self.root / 'real'
        real.write_text(A + '\n')
        alias = self.root / 'alias'
        alias.symlink_to(real)
        with self.assertRaises(ValueError):
            cache.manifest(alias)

    def test_root_collision_never_replaces_existing_data(self):
        self.recorder.gcroots.mkdir()
        entry = self.recorder.gcroots / Path(A).name
        entry.write_text('preserved')
        with self.assertRaises(ValueError):
            self.recorder.record([A])
        self.assertEqual(entry.read_text(), 'preserved')

    def test_changed_root_and_unprotected_state_are_rejected(self):
        self.recorder.gcroots.mkdir()
        entry = self.recorder.gcroots / Path(A).name
        entry.symlink_to(B)
        with self.assertRaises(ValueError):
            self.recorder.record([A])
        self.recorder.state.chmod(0o777)
        with self.assertRaises(ValueError):
            self.recorder.collect(self.root / 'outputs.txt')

    def test_writable_recorded_manifest_is_rejected(self):
        self.recorder.record([A])
        (self.recorder.state / 'outputs.txt').chmod(0o666)
        with self.assertRaises(ValueError):
            self.recorder.collect(self.root / 'outputs.txt')

    def test_failure_marker_prevents_incomplete_publication(self):
        self.recorder.record([A])
        (self.recorder.state / 'failed').write_text('failure')
        with self.assertRaises(ValueError):
            self.recorder.collect(self.root / 'outputs.txt')

    def test_verified_seeds_remain_in_next_index_without_rebuild(self):
        seed = self.root / 'seed'
        seed.write_text(A + '\n')
        output = self.root / 'outputs.txt'
        self.assertTrue(self.recorder.collect(output, seed, [B]))
        self.assertEqual(cache.manifest(output), [A, B])

    def test_cleanup_refuses_unrelated_files(self):
        self.recorder.gcroots.mkdir()
        entry = self.recorder.gcroots / 'unrelated'
        entry.write_text('preserved')
        with self.assertRaises(ValueError):
            self.recorder.cleanup()
        self.assertEqual(entry.read_text(), 'preserved')

    def test_warm_restores_and_verifies_only_with_builds_disabled(self):
        seed = self.root / 'seed'
        seed.write_text(A + '\n')
        metadata = json.dumps({A: {'narSize': 9}}).encode()
        with patch.dict(os.environ, {'NIKS3_READ_URL': 'https://niks3.monke.fail/oidc', 'RUNNER_TEMP': str(self.root)}), patch.object(cache, 'command', side_effect=[metadata, b'', b'', b'']) as command, patch.object(cache.shutil, 'disk_usage', return_value=type('Space', (), {'free': 100 * 1024**3})()), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=A+'\n')):
            cache.warm(seed)
        calls = [c.args[0] for c in command.call_args_list]
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[0][:4], ['nix', 'path-info', '--store', 'https://niks3.monke.fail/oidc'])
        self.assertEqual(calls[1][:3], ['nix-store', '--realise', '--add-root'])
        self.assertIn('--indirect', calls[1])
        self.assertIn('--sigs-needed', calls[-1])
        for call in calls:
            self.assertIn('max-jobs', call)
            self.assertEqual(call[call.index('max-jobs')+1], '0')
            self.assertEqual(call[call.index('fallback')+1], 'false')

    def test_oversized_seed_is_rejected_before_download(self):
        seed = self.root / 'seed'
        seed.write_text(A + '\n')
        metadata = json.dumps({A: {'narSize': cache.MAX_NAR_BYTES+1}}).encode()
        with patch.dict(os.environ, {'NIKS3_READ_URL': 'https://niks3.monke.fail/oidc'}), patch.object(cache, 'command', return_value=metadata) as command, self.assertRaises(ValueError):
            cache.warm(seed)
        self.assertEqual(command.call_count, 1)

    def test_seed_requires_space_for_store_and_export_before_download(self):
        seed = self.root / 'seed'
        seed.write_text(A + '\n')
        metadata = json.dumps({A: {'narSize': 9}}).encode()
        with patch.dict(os.environ, {'NIKS3_READ_URL': 'https://niks3.monke.fail/oidc', 'RUNNER_TEMP': str(self.root)}), patch.object(cache, 'command', return_value=metadata) as command, patch.object(cache.shutil, 'disk_usage', return_value=type('Space', (), {'free': 0})()), self.assertRaises(ValueError):
            cache.warm(seed)
        self.assertEqual(command.call_count, 1)

    def test_unexpected_reader_and_denied_roots_never_reach_cache(self):
        seed = self.root / 'seed'
        seed.write_text(A + '\n')
        with patch.dict(os.environ, {'NIKS3_READ_URL': 'https://example.invalid'}), patch.object(cache, 'command') as command, self.assertRaises(ValueError):
            cache.warm(seed)
        command.assert_not_called()
        seed.write_text(A.replace('completed-dependency', 'subnetter') + '\n')
        with patch.dict(os.environ, {'NIKS3_READ_URL': 'https://niks3.monke.fail/oidc'}), patch.object(cache, 'command') as command, self.assertRaises(ValueError):
            cache.warm(seed)
        command.assert_not_called()


class WorkflowIsolation(unittest.TestCase):
    def setUp(self):
        self.jobs = yaml.safe_load((ROOT / '.github/workflows/nixbench-opencode-provider.yml').read_text())['jobs']

    def test_models_have_no_cache_reader_writer_or_oidc_permission(self):
        job = self.jobs['refresh-and-screen']
        self.assertEqual(job['permissions']['id-token'], 'none')
        self.assertFalse(any('id-token' in s.get('env', {}) for s in job['steps']))
        runs = '\n'.join(s.get('run', '') for s in job['steps'])
        self.assertNotIn('configure-niks3.py', runs)
        self.assertNotIn('push-niks3.py', runs)
        installer = next(s for s in job['steps'] if s.get('uses', '').startswith('DeterminateSystems/'))
        self.assertFalse(installer['with']['trust-runner-user'])
        self.assertIn('post-build-hook = /usr/local/bin/nixbench-cache-hook', installer['with']['extra-conf'])
        self.assertNotIn('https://niks3.monke.fail', installer['with']['extra-conf'])
        collector = next(s for s in job['steps'] if s.get('id') == 'cache_outputs')
        self.assertIn('always()', collector['if'])
        self.assertLess(job['steps'].index(collector), next(i for i,s in enumerate(job['steps']) if s.get('name')=='Commit refreshed model data'))

    def test_trusted_jobs_are_default_branch_only_and_cleanup_before_export(self):
        seed = self.jobs['cache-seed']
        publisher = self.jobs['publish-cache']
        for job in [seed, publisher]:
            self.assertEqual(job['permissions']['id-token'], 'write')
            self.assertIn('github.event.repository.default_branch', job['if'])
            self.assertIn("github.event_name == 'schedule'", job['if'])
            self.assertIn("github.event_name == 'workflow_dispatch'", job['if'])
            self.assertNotIn('pull_request', job['if'])
        runs = [s.get('run', '') for s in seed['steps']]
        self.assertLess(runs.index('python3 ci/configure-niks3.py --cleanup'), runs.index('python3 ci/transfer-attic.py export --directory cache-seed --manifest cache-index/outputs.txt'))
        runs = [s.get('run', '') for s in publisher['steps']]
        imported = runs.index('python3 ci/transfer-attic.py import --directory cache-transfer')
        self.assertLess(runs.index('bash ci/install-niks3.sh'), imported)
        self.assertLess(runs.index('python3 ci/push-niks3.py --outputs bootstrap-outputs.txt'), imported)
        artifact = next(s for s in publisher['steps'] if s.get('id') == 'download')
        self.assertNotIn('continue-on-error', artifact)
        saved = next(i for i,s in enumerate(publisher['steps']) if s.get('uses', '').startswith('actions/cache/save@'))
        self.assertLess(runs.index('python3 ci/push-niks3.py --outputs cache-transfer/outputs.txt'), saved)

    def test_cache_self_test_neither_runs_models_nor_modifies_rankings(self):
        job = self.jobs['refresh-and-screen']
        fixture = next(s for s in job['steps'] if s.get('name') == 'Run only the public cache fixture')
        self.assertEqual(fixture['if'], '${{ inputs.cache_smoke_only == true }}')
        self.assertNotIn('env', fixture)
        for step in job['steps']:
            if ('OPENCODE_API_KEY' in step.get('env', {}) or step.get('name') in
                    ['Install OpenCode v2', 'Refresh models.dev and choose the next candidate', 'Commit refreshed model data']):
                self.assertIn('inputs.cache_smoke_only != true', step['if'])
        self.assertEqual(job['permissions']['id-token'], 'none')

    def test_canonical_helper_content_is_pinned(self):
        registry = json.loads((ROOT / 'ci/shared-cache-helper-hashes.json').read_text())
        for name, expected in registry['files'].items():
            self.assertEqual(hashlib.sha256((ROOT / 'ci' / name).read_bytes()).hexdigest(), expected, name)


if __name__ == '__main__':
    unittest.main()
