import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / 'packaging/issabel-security/build-rpm.py'
SPEC = importlib.util.spec_from_file_location('issabel_security_rpm', RECIPE)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)

BASE_SPEC = b'''Name: issabel-security
Version: 5.0.0
Release: 1
Source0: issabel-%{modname}-%{version}.tar.gz

%prep
%setup -n %{name}-%{version}

%install
# Native package steps remain in place.
%pre
    rpm -q --queryformat='%{VERSION}-%{RELEASE}' %{name} > %{_datadir}/issabel/module_installer/%{name}-%{version}-%{release}/preversion_%{modname}.info
%post
/usr/share/issabel/privileged/ssl_certbot writeasteriskcert
%files
/usr/share/issabel/privileged/ssl_certbot
'''


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class IssabelSecurityRpmTests(unittest.TestCase):
    def archive(self, source, helper=b'old native SSL helper'):
        with tarfile.open(str(source), 'w:gz') as tar:
            for path, data in (('issabel-security.spec', BASE_SPEC), (BUILDER.HELPER_PATH, helper)):
                member = tarfile.TarInfo(BUILDER.SOURCE_DIRECTORY + '/' + path)
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))

    def test_exact_patch_payload_and_original_source_are_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'upstream.tar.gz'
            topdir = Path(directory) / 'rpmbuild'
            self.archive(source)
            before = source.read_bytes()
            with mock.patch.multiple(BUILDER, SOURCE_SHA256=sha256(before),
                                     SPEC_SHA256=sha256(BASE_SPEC),
                                     ORIGINAL_HELPER_SHA256=sha256(b'old native SSL helper')):
                path = BUILDER.prepare(source, topdir)
                with self.assertRaises(FileExistsError):
                    BUILDER.prepare(source, topdir)
            result = path.read_text(encoding='utf-8')
            self.assertIn('Release: ' + BUILDER.RELEASE + '\n', result)
            self.assertIn('Source1: ' + BUILDER.FIXED_HELPER_NAME + '\n', result)
            self.assertIn('%global _binary_payload w2.xzdio\n', result)
            self.assertIn('%global _rpmformat 4\n', result)
            self.assertIn('%setup -n ' + BUILDER.SOURCE_DIRECTORY + '\n', result)
            self.assertIn(sha256(b'old native SSL helper'), result)
            self.assertIn(BUILDER.FIXED_HELPER_SHA256, result)
            old_query = "--queryformat='%{VERSION}-%{RELEASE}'"
            runtime_query = "--queryformat='%%{VERSION}-%%{RELEASE}'"
            scripts = BASE_SPEC.decode().split('%install', 1)[1]
            self.assertEqual(result.split('%install', 1)[1], scripts.replace(old_query, runtime_query, 1))
            self.assertEqual((topdir / 'SOURCES' / BUILDER.SOURCE_NAME).read_bytes(), before)
            self.assertEqual(sha256((topdir / 'SOURCES' / BUILDER.FIXED_HELPER_NAME).read_bytes()),
                             BUILDER.FIXED_HELPER_SHA256)
            self.assertEqual(source.read_bytes(), before)

    def test_rejects_unpinned_archive_without_creating_build_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'different.tar.gz'
            topdir = Path(directory) / 'rpmbuild'
            self.archive(source)
            with self.assertRaisesRegex(ValueError, 'upstream source archive'):
                BUILDER.prepare(source, topdir)
            self.assertFalse(topdir.exists())

    def test_rejects_changed_original_helper_without_creating_build_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'upstream.tar.gz'
            topdir = Path(directory) / 'rpmbuild'
            self.archive(source, b'a different helper')
            with mock.patch.multiple(BUILDER, SOURCE_SHA256=sha256(source.read_bytes()),
                                     SPEC_SHA256=sha256(BASE_SPEC)):
                with self.assertRaisesRegex(ValueError, 'original SSL helper'):
                    BUILDER.prepare(source, topdir)
            self.assertFalse(topdir.exists())


if __name__ == '__main__':
    unittest.main()
