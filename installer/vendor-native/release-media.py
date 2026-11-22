#!/usr/bin/env python3
"""Fetch and verify the packaged Rocky and MytelDesk dependencies for SSH setup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media


ASSET = 'mytelpbx-install-media-rocky8.10-x86_64.tar.gz'
RELEASE_URL = 'https://github.com/mytel-one/mytelPBX/releases/download/v7.7.0-r9/'
MAX_ARCHIVE = 2 * 1024 ** 3 - 1
MAX_EXPANDED = 4 * 1024 ** 3


def read_pin(path):
    pin = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(pin, dict) or set(pin) != {'sha256', 'manifest_sha256', 'bytes'}
            or any(not isinstance(pin[key], str) or not re.fullmatch('[0-9a-f]{64}', pin[key])
                   for key in ('sha256', 'manifest_sha256'))
            or type(pin['bytes']) is not int or not 0 < pin['bytes'] <= MAX_ARCHIVE):
        raise ValueError('Invalid dependency release pin')
    return pin


def verify_archive(path, pin):
    media.trusted(path, os.geteuid())
    media.verified(path, {'sha256': pin['sha256'], 'bytes': pin['bytes']}, os.geteuid())


def download(path, pin):
    log = path.with_name('dependency-download.log')
    part = path.with_suffix('.part')
    part.touch(mode=0o600, exist_ok=False)
    progress = '--progress-bar' if sys.stderr.isatty() else '--silent'
    print('  Downloading MytelPBX dependencies from GitHub ({:.1f} MiB)...'.format(
        pin['bytes'] / 1048576), file=sys.stderr, flush=True)
    with log.open('x', encoding='ascii') as report:
        for attempt in range(1, 4):
            options = ['curl', '--disable', '--fail', '--location',
                       '--proto', '=https', '--proto-redir', '=https', progress,
                       '--connect-timeout', '20', '--speed-limit', '1024', '--speed-time', '90',
                       '--max-time', '2700', '--max-filesize', str(pin['bytes']),
                       '--continue-at', '-', '--output', str(part), RELEASE_URL + ASSET]
            status = subprocess.call(options)
            report.write('attempt={} curl={} bytes={}\n'.format(attempt, status, part.stat().st_size))
            report.flush()
            if status == 0:
                verify_archive(part, pin)
                part.rename(path)
                return
            if status == 33:
                part.write_bytes(b'')
    raise ValueError('Dependency transfer from GitHub failed; see ' + str(log))


def unpack(archive, root, pin):
    verify_archive(archive, pin)
    if root.exists() or root.is_symlink():
        raise ValueError('Dependency workspace already exists')
    with tempfile.TemporaryDirectory(prefix='.media-', dir=str(root.parent)) as scratch:
        target = Path(scratch) / 'media'
        target.mkdir()
        seen = set()
        size = 0
        with tarfile.open(str(archive), 'r|gz') as source:
            for entry in source:
                if len(seen) >= media.MAX_FILES + 1:
                    raise ValueError('Too many release media files')
                name = media.relative(entry.name)
                if not entry.isdir() and (not entry.isreg() or entry.sparse is not None or name in seen
                                          or not 0 < entry.size <= MAX_EXPANDED - size):
                    raise ValueError('Invalid dependency archive entry')
                path = target / name
                if entry.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                size += entry.size
                seen.add(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(entry) as incoming, path.open('xb') as outgoing:
                    shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        media.load(target, pin['manifest_sha256'], expected_uid=os.geteuid())
        target.rename(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workdir', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        work = args.workdir
        if work.resolve() != work.absolute() or os.geteuid() != 0:
            raise ValueError('Canonical private root workspace required')
        info = media.trusted(work, 0, directory=True)
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('Dependency workspace must be private')
        pin = read_pin(Path(__file__).with_name('release-media.json'))
        archive = work / ASSET
        download(archive, pin)
        root = work / 'release-media'
        unpack(archive, root, pin)
        archive.unlink()
    except (OSError, ValueError, EOFError, tarfile.TarError) as error:
        print('MytelPBX: ' + str(error), file=sys.stderr)
        return 1
    print(root)
    print(pin['manifest_sha256'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
