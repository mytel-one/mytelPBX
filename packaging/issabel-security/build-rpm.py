#!/usr/bin/env python3
"""Build the Issabel security RPM with the publisher's exact SSL helper fix.

Use the original source tarball recorded in the Mytel R9 package catalog. The
generated source RPM contains those unchanged bytes and the upstream fixed file.
"""
import argparse
import hashlib
import io
from pathlib import Path
import subprocess
import tarfile


SOURCE_COMMIT = 'c7f05b08535001ba45104dff15a381a04eb7c5fa'
FIX_COMMIT = '4559a0801738eaee7feae4832a43c5f1e5cebecf'
SOURCE_DIRECTORY = 'security-' + SOURCE_COMMIT
SOURCE_NAME = SOURCE_DIRECTORY + '.tar.gz'
SOURCE_SHA256 = '91a9a057743071cce6887ef474474995214e4130d5979c49a5f5b73fd17b5a0a'
SPEC_SHA256 = '9931cd77aa12a70cdc07aed2ea95c90a2343cad8c69d32d87a21296e4e4b8ef7'
ORIGINAL_HELPER_SHA256 = '02cab38570094a9409a6f87dc4120edc84cc4046779999d3a24bc112fe725e65'
FIXED_HELPER_SHA256 = 'b9f00a0485746b401542e8bc98151915546807f03fe7c47895dffa6886d55249'
FIXED_HELPER_NAME = 'ssl_certbot-' + FIX_COMMIT[:8] + '.php'
INSTALLED_HELPER = '/usr/share/issabel/privileged/ssl_certbot'
HELPER_PATH = 'setup' + INSTALLED_HELPER
NAME_VERSION = 'issabel-security-5.0.0'
RELEASE = '2.mytel.el8'
SOURCE_MAX_BYTES = 4 * 1024 * 1024


def verify(data, expected, label):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Unexpected ' + label + '; SHA-256 verification failed')
    return data


def read_member(tar, path):
    member = tar.getmember(SOURCE_DIRECTORY + '/' + path)
    if not member.isfile():
        raise ValueError('Unexpected upstream source member type: ' + path)
    return tar.extractfile(member).read()


def render_spec(original):
    # Source0 retains the exact publisher archive. Redirect %setup solely for
    # its pinned directory name. Protect the native runtime query from spec
    # expansion so it still records the version installed before this upgrade.
    anchor = '%setup -n %{name}-%{version}\n'
    prep = ('%setup -n ' + SOURCE_DIRECTORY + '\n'
            '# Exact fix from IssabelFoundation/security ' + FIX_COMMIT + '\n'
            'helper="' + HELPER_PATH + '"\n'
            'echo "' + ORIGINAL_HELPER_SHA256 + '  $helper" | sha256sum --check --status || exit 1\n'
            'cat "%{SOURCE1}" > "$helper" || exit 1\n'
            'echo "' + FIXED_HELPER_SHA256 + '  $helper" | sha256sum --check --status || exit 1\n')
    source_line = 'Source0: issabel-%{modname}-%{version}.tar.gz\n'
    query = "    rpm -q --queryformat='%{VERSION}-%{RELEASE}' %{name} > "
    changes = (('Release: 1\n', 'Release: ' + RELEASE + '\n'),
               (source_line, 'Source0: ' + SOURCE_NAME + '\nSource1: ' + FIXED_HELPER_NAME + '\n'
                '%global _rpmformat 4\n%global _binary_payload w2.xzdio\n'),
               (anchor, prep),
               (query, "    rpm -q --queryformat='%%{VERSION}-%%{RELEASE}' %{name} > "))
    for before, after in changes:
        if original.count(before) != 1:
            raise ValueError('Unexpected layout in verified upstream RPM spec')
        original = original.replace(before, after, 1)
    return original.encode('utf-8')


def prepare(source, topdir):
    with source.open('rb') as stream:
        archive = verify(stream.read(SOURCE_MAX_BYTES + 1), SOURCE_SHA256, 'upstream source archive')
    fixed = verify(Path(__file__).with_name(FIXED_HELPER_NAME).read_bytes(),
                   FIXED_HELPER_SHA256, 'fixed SSL helper source')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as tar:
        spec = verify(read_member(tar, 'issabel-security.spec'), SPEC_SHA256, 'upstream RPM spec')
        verify(read_member(tar, HELPER_PATH), ORIGINAL_HELPER_SHA256, 'original SSL helper')
    rendered = render_spec(spec.decode('utf-8'))
    topdir.mkdir()
    sources = topdir / 'SOURCES'
    specs = topdir / 'SPECS'
    sources.mkdir()
    specs.mkdir()
    (sources / SOURCE_NAME).write_bytes(archive)
    (sources / FIXED_HELPER_NAME).write_bytes(fixed)
    candidate = specs / 'issabel-security.spec'
    candidate.write_bytes(rendered)
    return candidate


def verify_binary(package):
    info = subprocess.check_output([
        'rpm', '-qp', '--queryformat', '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\t'
        '%{PAYLOADFORMAT}\t%{PAYLOADCOMPRESSOR}\t%{PAYLOADFLAGS}\n'
        '[%{FILENAMES}\t%{FILEDIGESTS}\t%{FILEMODES:perms}\t%{FILEUSERNAME}\t%{FILEGROUPNAME}\n]',
        str(package),
    ]).decode('utf-8').splitlines()
    helper = '\t'.join((INSTALLED_HELPER, FIXED_HELPER_SHA256, '-rwxr-xr-x', 'root', 'root'))
    identity = package.name[:-4] + '\tcpio\txz\t2'
    if not info or info[0] != identity or info.count(helper) != 1:
        raise ValueError('Built RPM identity, payload format or fixed SSL helper did not match')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='pinned ' + SOURCE_NAME + ' from the published R9 sources')
    parser.add_argument('topdir', type=Path, help='fresh RPM build directory under an existing parent')
    parser.add_argument('--prepare-only', action='store_true', help='write verified SOURCES and SPECS without building')
    args = parser.parse_args()
    topdir = args.topdir.absolute()
    try:
        spec = prepare(args.source, topdir)
        if args.prepare_only:
            print(spec)
            return
        subprocess.run(['rpmbuild', '-ba', '--define', '_topdir ' + str(topdir),
                        '--define', '_binary_filedigest_algorithm 8', str(spec)], check=True)
        package = topdir / 'RPMS' / 'noarch' / (NAME_VERSION + '-' + RELEASE + '.noarch.rpm')
        verify_binary(package)
        print(package)
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
