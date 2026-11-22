import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'installer/vendor-native/release-media.py'
SPEC = importlib.util.spec_from_file_location('release_media', str(SOURCE))
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class ReleaseMediaTests(unittest.TestCase):
    def make_archive(self, root, extra=None):
        payload = {}
        for name in ('setup.sh', 'payload.tgz', 'desk.tgz', 'node.tar.xz', 'model.onnx',
                     'geoip.csv.gz', 'repos/rocky/repodata/repomd.xml', 'keys/rocky.asc'):
            payload[name] = name.encode()
        manifest = dict(
            schema=1, owner='MyTellPBX', platform='rocky8.10-x86_64', bootstrap='setup.sh',
            bundle='payload.tgz', desk='desk.tgz', node='node.tar.xz', wheels=['model.onnx'],
            geoip={'path': 'geoip.csv.gz', 'month': '2026-11'},
            repositories=[{'id': 'rocky', 'path': 'repos/rocky', 'gpgkey': 'keys/rocky.asc'}],
            files={name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                   for name, data in payload.items()})
        raw = json.dumps(manifest).encode()
        payload['manifest.json'] = raw
        archive = root / 'media.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name, data in payload.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
            if extra:
                member = tarfile.TarInfo(extra)
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/passwd'
                output.addfile(member)
        return archive, {'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                         'bytes': archive.stat().st_size, 'manifest_sha256': hashlib.sha256(raw).hexdigest()}

    def test_extracts_verified_local_media(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(os, 'geteuid', create=True, return_value=0):
            root = Path(temp)
            archive, pin = self.make_archive(root)
            RELEASE.unpack(archive, root/'verified', pin)
            self.assertEqual((root/'verified'/'desk.tgz').read_bytes(), b'desk.tgz')

    def test_refuses_links_and_wrong_pin_without_exposing_a_partial_depot(self):
        for kind in ('symlink', 'pin'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp, \
                    patch.object(os, 'geteuid', create=True, return_value=0):
                root = Path(temp)
                archive, pin = self.make_archive(root, extra='escape' if kind == 'symlink' else None)
                if kind == 'pin':
                    pin['sha256'] = '0' * 64
                with self.assertRaises(ValueError):
                    RELEASE.unpack(archive, root/'verified', pin)
                self.assertFalse((root/'verified').exists())
