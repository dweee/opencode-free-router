#!/usr/bin/env python3
"""Retain dedicated benchmark-job outputs; never run an output or mint a token."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

STATE = Path('/var/lib/nixbench-cache')
GCROOTS = Path('/nix/var/nix/gcroots/nixbench-cache')
KEY = 'niks3.monke.fail-1:q13GqiZgAxx7SyoLZInXoJjviZMOdNpfG03xs0VUD2M='
MAX_ROOTS = 8192
MAX_BYTES = 1024 * 1024
MAX_NAR_BYTES = 8192 * 1024 * 1024
STORE = re.compile(r'/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+')


def validate(paths):
    paths = sorted(set(paths))
    text = ''.join(p + '\n' for p in paths)
    if len(paths) > MAX_ROOTS or len(text.encode()) > MAX_BYTES:
        raise ValueError('Manifest exceeds bounds')
    if any(not STORE.fullmatch(p) or p.endswith('.drv') or len(Path(p).name) > 255 for p in paths):
        raise ValueError('Expected output paths')
    return paths


def manifest(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Expected bounded regular manifest')
    return validate(path.read_text().splitlines())


class Recorder:
    def __init__(self, state=STATE, gcroots=GCROOTS, owner=0):
        self.state, self.gcroots, self.owner = Path(state), Path(gcroots), owner

    def protected(self, path):
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != self.owner or metadata.st_mode & 0o022:
            raise ValueError('Unprotected retention directory')

    def init(self):
        if os.geteuid() != self.owner:
            raise PermissionError('Retention requires its protected owner')
        self.state.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.protected(self.state)

    def record(self, paths):
        self.init()
        self.gcroots.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.protected(self.gcroots)
        fd = os.open(self.state / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.state / 'outputs.txt'
            previous = manifest(path) if path.exists() else []
            outputs = validate([*previous, *paths])
            for output in outputs:
                if not os.path.lexists(output):
                    raise ValueError('Output is not present')
                link = self.gcroots / Path(output).name
                if link.is_symlink():
                    if str(link.readlink()) != output:
                        raise ValueError('Root target changed')
                elif link.exists():
                    raise ValueError('Unexpected root entry')
                else:
                    link.symlink_to(output)
            temporary = self.state / 'outputs.next'
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, 'w') as stream:
                stream.write(''.join(p + '\n' for p in outputs))
            temporary.chmod(0o644)
            os.replace(temporary, path)

    def collect(self, destination, seed=None, extra=()):
        self.protected(self.state)
        if (self.state / 'failed').exists():
            raise ValueError('Retention was incomplete')
        recorded = self.state / 'outputs.txt'
        if recorded.exists() and (recorded.lstat().st_uid != self.owner or recorded.lstat().st_mode & 0o022):
            raise ValueError('Untrusted retention manifest')
        roots = validate([*(manifest(recorded) if recorded.exists() else []),
                          *(manifest(seed) if seed else []), *extra])
        if any(not os.path.lexists(p) for p in roots):
            raise ValueError('Retained output disappeared')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(''.join(p + '\n' for p in roots))
        return bool(roots)

    def cleanup(self):
        if os.geteuid() != self.owner:
            raise PermissionError('Cleanup requires its protected owner')
        if self.gcroots.exists():
            self.protected(self.gcroots)
            for link in self.gcroots.iterdir():
                if not link.is_symlink() or not STORE.fullmatch(str(link.readlink())):
                    raise ValueError('Unexpected root entry')
                link.unlink()


def command(args):
    result = subprocess.run(args, capture_output=True, timeout=900)
    if result.returncode:
        raise RuntimeError('Cache data operation failed')
    return result.stdout


def warm(path):
    roots = manifest(path)
    if not roots:
        return
    url = os.environ.get('NIKS3_READ_URL')
    if url != 'https://niks3.monke.fail/oidc':
        raise ValueError('Expected the trusted job reader')
    # The cache index comes only from the separate trusted publisher. That
    # publisher receives data from a public-only dedicated benchmark store.
    spec = importlib.util.spec_from_file_location('public', Path(__file__).resolve().parents[1] / 'ci/public-closure.py')
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    policy.check_paths(roots)
    flags = ['--option', 'trusted-public-keys', KEY, '--option', 'extra-trusted-public-keys', '',
             '--option', 'max-jobs', '0', '--option', 'builders', '', '--option', 'fallback', 'false',
             '--option', 'require-sigs', 'true']
    raw = command(['nix', 'path-info', '--store', url, '--json', '--recursive', *flags, *roots])
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('Closure metadata exceeds bounds')
    metadata = json.loads(raw)
    if isinstance(metadata, list):
        metadata = {entry['path']: entry for entry in metadata}
    if not isinstance(metadata, dict) or not set(roots) <= metadata.keys():
        raise ValueError('Incomplete closure metadata')
    policy.check_paths(metadata.keys())
    sizes = [entry['narSize'] for entry in metadata.values()]
    if any(type(size) is not int or size < 0 for size in sizes):
        raise ValueError('Invalid closure size')
    nar_bytes = sum(sizes)
    if nar_bytes > MAX_NAR_BYTES:
        raise ValueError('Closure exceeds transfer bounds')
    temporary = Path(os.environ['RUNNER_TEMP'])
    if (shutil.disk_usage('/nix/store').free < nar_bytes + 2 * 1024**3
            or shutil.disk_usage(temporary).free < 2 * nar_bytes + 2 * 1024**3):
        raise ValueError('Insufficient space for seed and export')
    # Realisation registers the indirect roots before releasing temporary
    # download roots. Keep the seed alive through verification and export.
    # Multiple outputs receive root, root-2, ...; no builds are permitted.
    command(['nix-store', '--realise', '--add-root', str(temporary / 'nixbench-cache-seed'),
             '--indirect', '--option', 'substituters', url,
             '--option', 'extra-substituters', '', *flags, *roots])
    command(['nix', 'store', 'copy-sigs', '--substituter', url, '--recursive', *flags, *roots])
    command(['nix', 'store', 'verify', '--recursive', '--sigs-needed', '1', *flags, *roots])
    policy.runtime(roots)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['init', 'record', 'collect', 'cleanup', 'warm'])
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--add-root', action='append', default=[])
    args = parser.parse_args()
    recorder = Recorder()
    if args.mode == 'record':
        # A cache bookkeeping failure must not change a benchmark measurement.
        # The later trusted collect step fails closed rather than hiding it.
        try:
            raw = os.environ.get('OUT_PATHS', '')
            if len(raw.encode()) > MAX_BYTES:
                raise ValueError('Oversized hook input')
            recorder.record(validate(raw.split()))
        except Exception as error:
            try:
                (STATE / 'failed').write_text('Completed output retention failed: '+type(error).__name__+'\n')
            except OSError:
                pass
        return 0
    if args.mode == 'init':
        recorder.init()
    elif args.mode == 'cleanup':
        recorder.cleanup()
    elif args.mode == 'warm':
        if not args.manifest:
            raise ValueError('Missing cache index')
        warm(args.manifest)
    else:
        if not args.manifest:
            raise ValueError('Missing manifest destination')
        present = recorder.collect(args.manifest, args.seed, args.add_root)
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write('has_outputs=' + str(present).lower() + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('NixBench cache data operation failed; diagnostics withheld.', file=sys.stderr)
        sys.exit(1)
