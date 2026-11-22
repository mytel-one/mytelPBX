"""Failure injection into actual native Bash functions, never host commands."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = 'C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else shutil.which('bash')


@unittest.skipUnless(BASH and Path(BASH).is_file(), 'Bash is required')
class NativeFunctionFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='native-function-contract-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.log = self.base / 'netinstall.log'
        source = (ROOT / 'installer/vendor-native/mytelpbx-netinstall.sh').read_text(encoding='utf-8')
        self.definitions = source.split('\n# MYTEL_NATIVE_FLOW\n', 1)[0]
        # Only fixture paths are writable, and every target command is stubbed.
        self.definitions = self.definitions.replace('/usr/sbin/amportal', 'amportal_fixture')
        self.definitions = self.definitions.replace('/etc/asterisk/modules_additional.conf',
                                                    '${MYTEL_NATIVE_WORK}/modules.conf')
        self.definitions = self.definitions.replace('/etc/redhat-release',
                                                    (self.base / 'redhat-release').as_posix())
        self.definitions = self.definitions.replace('/etc/dnf/dnf.conf',
                                                    (self.base / 'dnf.conf').as_posix())
        (self.base / 'redhat-release').write_text('Rocky Linux release 8.10 (Green Obsidian)\n')
        (self.base / 'dnf.conf').write_text('[main]\n')

    def shell(self, body):
        script = self.definitions + '\nMYTEL_NATIVE_WORK=' + shlex.quote(self.base.as_posix())
        script += '\nMYTEL_BUNDLE_ROOT=' + shlex.quote(ROOT.as_posix())
        script += '\nFIXTURE_PYTHON=' + shlex.quote(Path(sys.executable).as_posix())
        script += '''
MYTEL_PYTHON=mytel_python_fixture
BACKTITLE=Fixture
umask 077
source "$MYTEL_BUNDLE_ROOT/installer/vendor-native/terminal-ui.sh"
mytel_terminal_init
mytel_python_fixture(){
  case "$2" in
    */dnf-progress.py) "$FIXTURE_PYTHON" "$@" ;;
    *) repair_stub "$@" ;;
  esac
}
'''
        script += body
        return subprocess.run([BASH, '--noprofile', '--norc', '-s'], input=script.encode('utf-8'),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)

    def test_package_transaction_failure_survives_successful_renderer_and_trailing_log(self):
        result = self.shell('''
rpm(){ return 1; }
yum(){ printf 'private-fixture-package-diagnostic\\n'; return 42; }
mytel_native_step mytel_package_batch "required-package" core
printf 'CONTINUED\\n'
''')
        self.assertEqual(result.returncode, 42)
        self.assertNotIn(b'CONTINUED', result.stdout)
        self.assertNotIn(b'private-fixture-package-diagnostic', result.stdout + result.stderr)
        self.assertIn('private-fixture-package-diagnostic', self.log.read_text())
        self.assertIn('requested: required-package', (self.base / 'netinstall_errors.txt').read_text())

    def test_renderer_failure_is_fatal_even_after_successful_package_transaction(self):
        result = self.shell('''
mytel_python_fixture(){
  if [[ ${*: -1} == --check-log ]]; then return 0; fi
  cat >/dev/null
  return 7
}
rpm(){ return 1; }
yum(){ return 0; }
mytel_native_step mytel_package_batch "required-package" core
printf 'CONTINUED\\n'
''')
        self.assertEqual(result.returncode, 7)
        self.assertNotIn(b'CONTINUED', result.stdout)

    def test_install_batch_submits_all_missing_packages_in_one_transaction(self):
        result = self.shell('''
rpm(){ [ "$3" = preinstalled ]; }
yum(){
  printf 'invoked\\n' >> "$MYTEL_NATIVE_WORK/calls"
  [ "$*" = '-y install needed second' ] || return 71
  printf '(1/2): needed-1.0-1.el8.x86_64.rpm 10 kB/s | 10 kB 00:01\\n'
  printf '(2/2): https://hidden:fixture-secret@repo.invalid/download\\n'
  printf '  Installing : dependency-1.0-1.el8.noarch 1/2\\n'
  printf '  Verifying : needed-1.0-1.el8.x86_64 2/2\\n'
}
mytel_native_step mytel_package_batch $'preinstalled\\nneeded\\nsecond' core
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / 'calls').read_text().splitlines(), ['invoked'])
        for text in (b'2 to install', b'1 already installed',
                     b'Download  1/2  needed-1.0-1.el8.x86_64.rpm',
                     b'Transaction  1/2  dependency-1.0-1.el8.noarch', b'Verification  2/2'):
            self.assertIn(text, result.stdout)
        self.assertNotIn(b'fixture-secret', result.stdout + result.stderr)
        self.assertIn('fixture-secret', self.log.read_text())
        self.assertNotIn(b'\x1b[', result.stdout + result.stderr)

    def test_stage_display_does_not_change_native_result(self):
        result = self.shell('''
mytel_terminal_banner
check_tools(){ return 0; }
install_base_packages(){ return 18; }
mytel_native_step check_tools
mytel_native_step install_base_packages
printf 'CONTINUED\\n'
''')
        self.assertEqual(result.returncode, 18)
        self.assertIn(b'Mytel.one', result.stdout)
        self.assertIn(b'[01/09]  Checking setup tools', result.stdout)
        self.assertIn(b'Done', result.stdout)
        self.assertIn(b'[04/09]  Installing base packages', result.stdout)
        self.assertIn(b'Stopped', result.stderr)
        self.assertNotIn(b'CONTINUED', result.stdout)

    def test_product_package_floors_stay_single_dnf_arguments_and_are_rechecked(self):
        shell = '''
rpm(){ return 1; }
mytel_unmet_package_floors(){
  [[ $# == 2 && $1 == 'alpha>=5.0.0-1' && $2 == 'beta>=2.12.0-1' ]] || return 81
  if [[ ! -e $MYTEL_NATIVE_WORK/installed || $BAD_POST == 1 ]]; then printf '%s\\n' 'beta >= 2.12.0-1'; fi
}
yum(){
  [[ $# == 6 && $1 == --exclude=issabel-license && $2 == --exclude=fop2 && $3 == -y && $4 == install && $5 == other && $6 == 'beta >= 2.12.0-1' ]] || return 82
  touch "$MYTEL_NATIVE_WORK/installed"
}
mytel_package_batch 'alpha>=5.0.0-1 beta>=2.12.0-1 other' product
'''
        result = self.shell('BAD_POST=0\n' + shell)
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.base / 'installed').unlink()
        result = self.shell('BAD_POST=1\n' + shell)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Required module version missing', result.stderr)

    def test_online_install_limits_transactions_to_required_packages(self):
        for name, data in [('inst1.txt', 'base-one base-two'), ('inst2.txt', 'product-core')]:
            (self.base / name).write_text(data)
        result = self.shell('''
ADDPKGS=community-block-list
dnf(){ echo UNEXPECTED_DNF; return 91; }
yum(){ echo UNEXPECTED_YUM; return 91; }
mytel_package_batch(){ printf '%s|%s\\n' "$2" "$1" >> "$MYTEL_NATIVE_WORK/calls"; }
mytel_native_step install_base_packages
mytel_native_step install_product_packages
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / 'calls').read_text().splitlines(),
                         ['core|base-one base-two', 'product|product-core community-block-list'])
        self.assertNotIn(b'UNEXPECTED', result.stdout)
        self.assertIn(b'[04/09]  Installing base packages', result.stdout)
        self.assertIn(b'[05/09]  Installing MytelPBX packages', result.stdout)

    def test_every_essential_post_install_failure_stops_before_later_stage(self):
        for failing in ('enable-mariadb', 'start-mariadb', 'set-root-pass', 'enable-httpd',
                        'clear-root-pass', 'mv', 'amportal'):
            with self.subTest(command=failing):
                result = self.shell('FAIL=' + shlex.quote(failing) + '''
systemctl(){ case "$1 $2" in 'enable mariadb.service') key=enable-mariadb;; 'start mariadb') key=start-mariadb;; 'enable httpd') key=enable-httpd;; *) key=other;; esac; [ "$FAIL" != "$key" ]; }
mysql(){ if [[ "$1" = -p* ]]; then key=clear-root-pass; else key=set-root-pass; fi; [ "$FAIL" != "$key" ]; }
mv(){ [ "$FAIL" != mv ]; }
amportal_fixture(){ [ "$FAIL" != amportal ]; }
setenforce(){ :; }; sed(){ :; }; cp(){ :; }; firewall-cmd(){ :; }; rm(){ :; }; mkdir(){ :; }
mytel_native_step post_install
printf 'CONTINUED\\n'
''')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b'CONTINUED', result.stdout)
                self.assertIn(b'stopped at post_install', result.stderr)

    def test_native_rpm_transactions_use_only_verified_local_sources(self):
        system_repos = self.base / 'system-repos'
        system_repos.mkdir()
        (system_repos / 'Rocky.repo').write_text('original native OS source')
        (system_repos / 'Issabel.repo').write_text('previous configuration')
        self.definitions = self.definitions.replace('/etc/yum.repos.d', system_repos.as_posix())
        result = self.shell(r'''
MYTEL_REQUIRE_MEDIA_REPOS=1
MYTEL_MEDIA_ROOT="$MYTEL_NATIVE_WORK/media"
MYTEL_MEDIA_MANIFEST_SHA256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export BETAREPO='--setopt=reposdir=/untrusted --enablerepo=*'
rpm(){ return 1; }
dnf(){ printf '%s\n' "$@" > "$MYTEL_NATIVE_WORK/metadata-args"; }
yum(){ printf '%s\n' "$@" > "$MYTEL_NATIVE_WORK/install-args"; }
repair_stub(){
  case "$3" in
    */media.py)
      printf '[mytel-platform]\nbaseurl=file:///verified/platform\nenabled=1\ngpgcheck=1\n'
      printf 'MEDIA\n' >> "$MYTEL_NATIVE_WORK/verified";;
    */vendor-mirror.py)
      printf '[mytelpbx-packages]\nbaseurl=file:///verified/pbx\n' > "$5/Issabel.repo"
      printf '[mytelpbx-builds]\nbaseurl=file:///verified/builds\n' > "$5/Issabel5.repo"
      : > "$5/commercial-addons.repo";;
    *) return 77;;
  esac
}
mytel_check_install_repositories || exit $?
printf '%s' "$MYTEL_NATIVE_REPO_DIR" > "$MYTEL_NATIVE_WORK/preflight-path"
mytel_native_step add_repos || exit $?
bash -c 'test -d "$MYTEL_NATIVE_REPO_DIR"' || exit 92
printf 'httpd\n' > "$MYTEL_NATIVE_WORK/inst1.txt"
printf 'pbx-core\n' > "$MYTEL_NATIVE_WORK/inst2.txt"
ADDPKGS=community-block-list
mytel_native_step install_base_packages || exit $?
cp "$MYTEL_NATIVE_WORK/install-args" "$MYTEL_NATIVE_WORK/base-args"
mytel_native_step install_product_packages || exit $?
printf '%s' "$MYTEL_NATIVE_REPO_DIR" > "$MYTEL_NATIVE_WORK/repo-path"
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        repo = Path((self.base / 'repo-path').read_text())
        preflight = Path((self.base / 'preflight-path').read_text())
        self.assertNotEqual(preflight, repo)
        self.assertEqual((system_repos / 'Rocky.repo').read_text(), 'original native OS source')
        self.assertIn('file:///verified/pbx', (system_repos / 'Issabel.repo').read_text())
        self.assertEqual((self.base / 'vendor-repos-before/Issabel.repo').read_text(), 'previous configuration')
        self.assertFalse((system_repos / 'Mytel-platform.repo').exists())
        platform = (repo / 'Mytel-platform.repo').read_text()
        self.assertIn('module_hotfixes=1', platform)
        self.assertIn('gpgcheck=1', platform)
        self.assertEqual((self.base / 'verified').read_text().splitlines(), ['MEDIA'])
        for file in ('metadata-args', 'base-args', 'install-args'):
            args = (self.base / file).read_text().splitlines()
            self.assertIn('--noplugins', args)
            self.assertIn('--disablerepo=*', args)
            self.assertEqual([arg for arg in args if arg.startswith('--setopt=reposdir=')],
                             ['--setopt=reposdir=' + (preflight if file == 'metadata-args' else repo).as_posix()])
            self.assertIn('--setopt=cachedir=' + self.base.as_posix() + '/dnf-cache', args)
            self.assertEqual([arg for arg in args if arg.startswith('--enablerepo=')],
                             ['--enablerepo=mytelpbx-packages', '--enablerepo=mytelpbx-builds',
                              '--enablerepo=mytel-platform'])
            self.assertFalse(any(arg.startswith(('http:', 'https:')) or arg == 'update' for arg in args))
        self.assertEqual((self.base / 'metadata-args').read_text().splitlines()[-1], 'makecache')
        self.assertEqual((self.base / 'base-args').read_text().splitlines()[-1], 'httpd')
        self.assertEqual((self.base / 'install-args').read_text().splitlines()[-2:], ['pbx-core', 'community-block-list'])

    def test_missing_or_invalid_release_media_stops_before_package_manager(self):
        for var in ('', 'a' * 64):
            with self.subTest(manifest=bool(var)):
                result = self.shell('MYTEL_MEDIA_MANIFEST_SHA256=' + shlex.quote(var) + r'''
MYTEL_MEDIA_ROOT="$MYTEL_NATIVE_WORK/media"
MYTEL_REQUIRE_MEDIA_REPOS=1
rpm(){ return 0; }
yum(){ touch "$MYTEL_NATIVE_WORK/escape"; }
dnf(){ touch "$MYTEL_NATIVE_WORK/escape"; }
repair_stub(){
  case "$3" in
    */media.py) printf '[partial]\nenabled=1\n'; return 77;;
    *) touch "$MYTEL_NATIVE_WORK/escape";;
  esac
}
mytel_check_install_repositories
''')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.base / 'verified-platform.repo').exists())
                self.assertFalse((self.base / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
