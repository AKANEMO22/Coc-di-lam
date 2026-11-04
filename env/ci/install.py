#!/usr/bin/env python3
"""Interactive, on-host installer. Secrets never enter the Git checkout."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

ROOT = Path('/opt/dpl-ci')
HOME = Path('/var/lib/dpl-ci')
CONFIG = HOME / 'config'
# Root-only (0600) copy of the administration PAT, so reruns do not prompt again.
TOKEN_FILE = Path('/root/.config/dpl-ci/github-pat')
OWNER = 'Coc-di-lam'
USER = 'dpl-ci'


class InstallError(RuntimeError):
    pass


def command(args, *, cwd=None):
    """Capture command output, including credentials/config, rather than echo it."""
    environment = dict(os.environ)
    environment.pop('GITHUB_TOKEN', None)
    result = subprocess.run([str(a) for a in args], cwd=cwd, text=True, capture_output=True, env=environment)
    if result.returncode:
        raise InstallError(f'{Path(str(args[0])).name} failed (exit {result.returncode}); output withheld to protect credentials.')
    return result.stdout.strip()


class GitHub:
    def __init__(self, token):
        self.token = token

    def call(self, path, payload=None):
        request = urllib.request.Request(
            'https://api.github.com' + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'dpl-ci-installer',
                     'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            code = error.code
            details = []
            try:
                body = json.loads(error.read(16384))
                if isinstance(body, dict):
                    if isinstance(body.get('message'), str):
                        details.append(body['message'])
                    errors = body.get('errors') or []
                    if isinstance(errors, str):
                        errors = [errors]
                    for item in errors[:4]:
                        if isinstance(item, dict):
                            details.append('{} ({}): {}'.format(item.get('field', 'validation'),
                                           item.get('code', 'unknown'), item.get('message', 'rejected')))
                        elif isinstance(item, str):
                            details.append(item)
            except (OSError, ValueError, TypeError):
                pass
            error.close()
            detail = '; '.join(details) or 'Response details unavailable.'
            for secret in [self.token] + ([value for value in payload.values() if isinstance(value, str)]
                                         if isinstance(payload, dict) else []):
                if secret:
                    detail = detail.replace(secret, '[redacted]')
            detail = re.sub(r'https?://\S+', '[URL omitted]', detail)
            detail = re.sub(r'\b(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]+', '[redacted]', detail)
            detail = ' '.join(detail.split())[:600]
            endpoint = path.split('?', 1)[0]
            raise InstallError(f'GitHub API HTTP {code} during {request.get_method()} {endpoint}: {detail}') from None
        except (OSError, ValueError):
            raise InstallError('GitHub API request failed; details withheld to protect credentials.') from None


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.dpl-', dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_root_script(source, destination):
    """An active runner sees either the old script or the complete new one."""
    if destination.is_symlink():
        raise InstallError(f'Refusing symlink at protected script {destination}.')
    descriptor, temporary = tempfile.mkstemp(prefix='.dpl-script-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(source.read_bytes())
        os.chmod(temporary, 0o755)
        command(['chown', 'root:root', temporary])
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def deployment_lock():
    from pipeline import host_lock
    return host_lock(HOME / 'state' / 'deploy.lock')


def asset_checksum(asset, release_body):
    digest = asset.get('digest') or ''
    if re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        return digest.split(':', 1)[1]
    match = re.search(r'<!-- BEGIN SHA linux-x64 -->\s*([0-9a-f]{64})\s*<!-- END SHA linux-x64 -->', release_body)
    if not match:
        raise InstallError('Official runner release has no verifiable SHA-256 checksum.')
    return match.group(1)


def extract_runner(archive_path, target, expected_checksum):
    digest = hashlib.sha256()
    with Path(archive_path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected_checksum:
        raise InstallError('Runner archive SHA-256 mismatch; archive was not extracted.')
    if not hasattr(tarfile, 'data_filter'):
        # Keep the same upstream safe filter without replacing the host's Python.
        # Only the verified archive and destination are visible to the container.
        target = Path(target).resolve()
        target.mkdir(parents=True, exist_ok=True)
        print('Host Python lacks safe tar filters; using isolated Python container for extraction...')
        script = ("import tarfile; "
                  "archive=tarfile.open('/runner.tar.gz', 'r:gz'); "
                  "archive.extractall('/extract', filter='data'); archive.close()")
        command(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                 '--mount', f'type=bind,source={Path(archive_path).resolve()},target=/runner.tar.gz,readonly',
                 '--mount', f'type=bind,source={target},target=/extract',
                 'python:3.13-slim', 'python', '-c', script])
        return
    try:
        with tarfile.open(archive_path, 'r:gz') as archive:
            # data_filter rejects path traversal, external links and special devices.
            archive.extractall(target, filter='data')
    except (tarfile.TarError, OSError):
        raise InstallError('Runner archive rejected by safe extraction checks.') from None


def group_chat_id(event):
    chat = (event or {}).get('message', {}).get('chat', {})
    value = chat.get('id')
    return str(value) if chat.get('chat_type') == 'GROUP' and value is not None else None


def validate_workspace(workspace):
    workspace = Path(workspace).expanduser().resolve()
    required = [workspace / 'compose.yaml'] + [workspace / d / '.env' for d in ('core_agent', 'core-tts-stt', 'backend')]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise InstallError('Existing workspace is incomplete; missing files: ' + ', '.join(missing))
    return workspace


def read_github_token():
    # Do not let runner configuration or dependency commands inherit the administration PAT.
    token = os.environ.pop('GITHUB_TOKEN', '').strip()
    if not token and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding='utf-8').strip()
    if not token:
        token = getpass.getpass('GitHub PAT (hidden; Administration write on the six repos): ').strip()
        if token:
            write_private(TOKEN_FILE, token + '\n')
            print(f'PAT saved for later runs: {TOKEN_FILE} (root only).')
    if not token:
        raise InstallError('GitHub token is required for deploy keys and runner registration.')
    return token


def validate_service_account(account, group):
    if Path(account.pw_dir) != HOME or account.pw_gid != group.gr_gid:
        raise InstallError(f'Existing {USER} account must use home {HOME} and primary group {USER}; repair the account before retrying.')


def discover_workspace():
    ids = command(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=dpl-pvan']).splitlines()
    if ids:
        containers = json.loads(command(['docker', 'inspect', *ids]))
        for container in containers:
            directory = container.get('Config', {}).get('Labels', {}).get('com.docker.compose.project.working_dir')
            if directory:
                return Path(directory)
    return Path('/opt/dpl')


def seed_baseline(workspace, state):
    """Keep a reproducible rollback only when every service currently has a running image."""
    state = Path(state)
    if (state / 'current.json').exists():
        return
    config = json.loads(command(['docker', 'compose', '-p', 'dpl-pvan', '-f', str(workspace / 'compose.yaml'), 'config', '--format', 'json'], cwd=workspace))
    ids = command(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=dpl-pvan']).splitlines()
    if not ids:
        print('No running dpl-pvan baseline: the first deploy cannot auto-rollback.')
        return
    containers = json.loads(command(['docker', 'inspect', *ids]))
    images = {c['Config']['Labels'].get('com.docker.compose.service'): c['Image'] for c in containers}
    if any(name not in images for name in config.get('services', {})):
        print('Some services are not running: baseline was not recorded; first deploy cannot auto-rollback.')
        return
    for name, service in config['services'].items():
        tag = 'dpl-ci-baseline/' + name + ':initial'
        command(['docker', 'tag', images[name], tag])
        service['image'] = tag
        service.pop('build', None)
        service['pull_policy'] = 'never'
    baseline = state / 'baseline' / 'compose.json'
    write_private(baseline, json.dumps(config, indent=2))
    write_private(state / 'current.json', json.dumps({'files': [str(baseline)]}))


def preflight():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise InstallError('Run this installer as root on Linux CT 104, not on the Proxmox host or Windows.')
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        raise InstallError('This installer currently supports Linux x64 only.')
    if sys.version_info < (3, 8):
        raise InstallError('Python >= 3.8 is required for the installer.')
    for executable in ('docker', 'git', 'ssh-keygen', 'runuser', 'useradd', 'usermod', 'getent', 'chown', 'systemctl'):
        if not shutil.which(executable):
            raise InstallError(f'Missing prerequisite: {executable}. Run ci/install.sh to install base tools.')
    command(['docker', 'info', '--format', '{{.ServerVersion}}'])
    version = command(['docker', 'compose', 'version', '--short'])
    numbers = re.search(r'(\d+)\.(\d+)', version)
    if not numbers or tuple(map(int, numbers.groups())) < (2, 20):
        raise InstallError('Docker Compose >= 2.20 is required for include and health waiting.')
    command(['systemctl', 'show', '--property=Version', '--value'])


def validate_zalo_preset(preset):
    if not isinstance(preset, dict) or any(
            not isinstance(preset.get(field), str) or not preset[field].strip()
            for field in ('token', 'chat_id')):
        raise InstallError('Zalo config must contain non-empty string token and chat_id fields; values omitted.')
    return {field: preset[field].strip() for field in ('token', 'chat_id')}


def load_zalo_config(path):
    try:
        preset = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError):
        raise InstallError('Cannot read Zalo config JSON; check the file path, permissions and JSON syntax. Values omitted.') from None
    return validate_zalo_preset(preset)


def configure_zalo(preset=None):
    from zalo import ZaloError, api_call, send_message
    previous = CONFIG / 'zalo.json'
    if preset is not None:
        preset = validate_zalo_preset(preset)
        token = preset['token']
    else:
        saved = json.loads(previous.read_text()) if previous.exists() else {}
        token = getpass.getpass('Zalo Bot token (hidden; Enter reuses saved token): ').strip() or saved.get('token', '')
    if not token:
        raise InstallError('A valid Zalo token is required, or use --skip-zalo explicitly.')
    try:
        api_call(token, 'getMe')
        if preset is not None:
            # The supplied group is explicit: do not inspect or consume webhook/poll events.
            chat_id = preset['chat_id']
        else:
            webhook = api_call(token, 'getWebhookInfo') or {}
            if webhook.get('url'):
                print('Bot already has a webhook; it will be preserved. Get the group chat.id from your webhook events.')
                chat_id = input('Group chat_id: ').strip()
            else:
                chat_id = input('Group chat_id if known (Enter discovers from a group mention): ').strip()
                if not chat_id:
                    print('Mention the bot in the destination Zalo group with CI/CD. Waiting up to 3 minutes...')
                    for _ in range(18):
                        event = api_call(token, 'getUpdates', {'timeout': '10'}, timeout=15)
                        chat_id = group_chat_id(event)
                        if chat_id:
                            break
        if not chat_id:
            raise InstallError('No group event received. Mention the bot and rerun --configure-zalo.')
        send_message(token, str(chat_id), 'DPL CI/CD: đã cấu hình thông báo nhóm cho máy 104. Kết quả build và triển khai sẽ được gửi tại đây.')
    except ZaloError:
        raise InstallError('Zalo rejected the request. Check the token in Bot Creator, group membership and chat_id; token/URL omitted.') from None
    with deployment_lock():
        write_private(previous, json.dumps({'token': token, 'chat_id': str(chat_id)}, ensure_ascii=False))
        command(['chown', USER + ':' + USER, str(previous)])
    print('Zalo notification configuration saved outside Git; test message sent.')


def prepare_user_and_files(workspace):
    import grp
    import pwd
    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        command(['useradd', '--create-home', '--user-group', '--home-dir', str(HOME), '--shell', '/bin/bash', USER])
        account = pwd.getpwnam(USER)
    try:
        group = grp.getgrnam(USER)
    except KeyError:
        raise InstallError(f'Existing {USER} account has no {USER} group; repair the account before retrying.') from None
    validate_service_account(account, group)
    command(['getent', 'group', 'docker'])
    command(['usermod', '-aG', 'docker', USER])
    for directory in (HOME, CONFIG, HOME / '.ssh', HOME / 'state', HOME / 'releases', HOME / 'runners'):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        command(['chown', USER + ':' + USER, str(directory)])
    if ROOT.is_symlink():
        raise InstallError(f'Refusing symlink at protected script directory {ROOT}.')
    lock = HOME / 'state' / 'deploy.lock'
    descriptor = os.open(lock, os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    os.close(descriptor)
    command(['chown', USER + ':' + USER, str(lock)])
    with deployment_lock():
        ROOT.mkdir(parents=True, exist_ok=True)
        command(['chown', 'root:root', str(ROOT)])
        ROOT.chmod(0o755)
        for name in ('pipeline.py', 'zalo.py', 'install.py'):
            write_root_script(Path(__file__).resolve().parent / name, ROOT / name)
        for name in ('core_agent', 'core-tts-stt', 'backend'):
            target = CONFIG / (name + '.env')
            if not target.exists():
                write_private(target, (workspace / name / '.env').read_text())
            command(['chown', USER + ':' + USER, str(target)])
        seed_baseline(workspace, HOME / 'state')
        command(['chown', '-R', USER + ':' + USER, str(HOME / 'state')])


def prepare_ssh(github, repos):
    ssh = HOME / '.ssh'
    keys = github.call('/meta').get('ssh_keys', [])
    if not keys:
        raise InstallError('GitHub did not return trusted SSH host keys.')
    write_private(ssh / 'known_hosts', ''.join('[ssh.github.com]:443 ' + key + '\n' for key in keys))
    config = []
    for repo in repos:
        print(f'Preparing read-only deploy key: {OWNER}/{repo}', flush=True)
        key = ssh / ('id_' + repo)
        if not key.exists():
            command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'dpl-ci-104-' + repo, '-f', str(key)])
        public = Path(str(key) + '.pub').read_text().strip()
        existing = github.call(f'/repos/{OWNER}/{repo}/keys?per_page=100')
        if not any(item.get('key', '').split()[:2] == public.split()[:2] and item.get('read_only') for item in existing):
            github.call(f'/repos/{OWNER}/{repo}/keys', {'title': 'dpl-ci-104', 'key': public, 'read_only': True})
        config.append(f'Host github-dpl-{repo}\n  HostName ssh.github.com\n  Port 443\n  User git\n  IdentityFile {key}\n  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n  UserKnownHostsFile {ssh / "known_hosts"}\n')
    write_private(ssh / 'config', '\n'.join(config))
    command(['chown', '-R', USER + ':' + USER, str(ssh)])
    for repo in repos:
        # Run from the service home: the installer's own cwd (e.g. /opt/dpl-104/env) may be unreadable to it.
        command(['runuser', '-u', USER, '--', 'git', 'ls-remote',
                 f'git@github-dpl-{repo}:{OWNER}/{repo}.git', 'HEAD'], cwd=HOME)


def install_runners(github, repos):
    release = github.call('/repos/actions/runner/releases/latest')
    asset = next((a for a in release['assets'] if re.fullmatch(r'actions-runner-linux-x64-[\d.]+\.tar\.gz', a['name'])), None)
    if not asset or not asset['browser_download_url'].startswith('https://github.com/actions/runner/releases/download/'):
        raise InstallError('Official Linux x64 runner archive not found.')
    digest = asset_checksum(asset, release.get('body', ''))
    # Validate repository policy and privileges before installing any runner.
    for repo in repos:
        github.call(f'/repos/{OWNER}/{repo}/actions/runners?per_page=100')
    with tempfile.TemporaryDirectory(prefix='dpl-runner-') as temporary:
        archive = Path(temporary) / asset['name']
        print('Downloading and verifying official GitHub runner archive...')
        try:
            with urllib.request.urlopen(asset['browser_download_url'], timeout=120) as source, archive.open('wb') as destination:
                shutil.copyfileobj(source, destination)
        except OSError:
            raise InstallError('Runner archive download failed.') from None
        dependencies_installed = False
        for repo in repos:
            directory = HOME / 'runners' / repo
            configured = directory / '.runner'
            if configured.exists():
                saved = json.loads(configured.read_text())
                if saved.get('gitHubUrl', '').rstrip('/').lower() != f'https://github.com/{OWNER}/{repo}'.lower():
                    raise InstallError('Existing runner targets another repository; refusing replacement.')
                print(f'Keeping registered runner: {repo}')
            else:
                print(f'Installing repository runner: {repo}')
                extract_runner(archive, directory, digest)
                if not dependencies_installed:
                    command(['bash', str(directory / 'bin' / 'installdependencies.sh')], cwd=directory)
                    dependencies_installed = True
                command(['chown', '-R', USER + ':' + USER, str(directory)])
                registration = github.call(f'/repos/{OWNER}/{repo}/actions/runners/registration-token', {})
                command(['runuser', '-u', USER, '--', str(directory / 'config.sh'), '--unattended',
                         '--url', f'https://github.com/{OWNER}/{repo}', '--token', registration['token'],
                         '--name', 'dpl-104-' + repo, '--labels', 'dpl-104', '--work', '_work'], cwd=directory)
        # Queued jobs may start immediately: configure every runner before opening consumption.
        for repo in repos:
            directory = HOME / 'runners' / repo
            if not (directory / '.service').exists():
                command(['bash', str(directory / 'svc.sh'), 'install', USER], cwd=directory)
            command(['bash', str(directory / 'svc.sh'), 'start'], cwd=directory)
            print(f'Runner service started: {repo}')


def main():
    parser = argparse.ArgumentParser(description='Install DPL push-build-deploy runners on Linux CT 104 (run as root).')
    parser.add_argument('--workspace', type=Path, help='Existing DPL workspace containing compose.yaml and real component .env files')
    parser.add_argument('--skip-zalo', action='store_true', help='Explicitly defer Zalo setup; later use --configure-zalo')
    parser.add_argument('--configure-zalo', action='store_true', help='Only configure/test the Zalo group notifier')
    parser.add_argument('--zalo-config', type=Path, help='Protected JSON file containing token and chat_id; skips Zalo prompts and polling')
    parser.add_argument('--check', action='store_true', help='Read-only preflight; do not install or register anything')
    args = parser.parse_args()
    if args.skip_zalo and args.zalo_config:
        parser.error('--skip-zalo cannot be combined with --zalo-config')
    try:
        preset = load_zalo_config(args.zalo_config) if args.zalo_config else None
        preflight()
        if args.configure_zalo:
            if args.check:
                print('Preflight passed; no configuration changed.')
            else:
                configure_zalo(preset)
            return 0
        candidate = args.workspace or discover_workspace()
        if not args.workspace and not args.check:
            candidate = Path(input(f'Existing DPL workspace [{candidate}]: ').strip() or str(candidate))
        workspace = validate_workspace(candidate)
        # Resolve config without exposing .env contents or altering any services.
        command(['docker', 'compose', '-p', 'dpl-pvan', '-f', str(workspace / 'compose.yaml'), 'config', '--quiet'], cwd=workspace)
        if args.check:
            print(f'Preflight passed for {workspace}; no configuration changed.')
            return 0
        from pipeline import REPOS
        token = read_github_token()
        github = GitHub(token)
        for repo in REPOS:
            info = github.call(f'/repos/{OWNER}/{repo}')
            if not info.get('permissions', {}).get('admin'):
                raise InstallError(f'GitHub token must have admin access to {OWNER}/{repo}.')
        prepare_user_and_files(workspace)
        if not args.skip_zalo:
            configure_zalo(preset)
        with deployment_lock():
            prepare_ssh(github, REPOS)
        install_runners(github, REPOS)
        print('Installed. Push a commit to start a build; default-branch pushes also deploy to CT 104.')
        print('Runner services make outbound connections; no router port forwarding is required.')
        return 0
    except (InstallError, OSError, ValueError, KeyError):
        # Do not print arbitrary exception objects: upstream errors may contain tokens.
        error = sys.exc_info()[1]
        print(str(error) if isinstance(error, InstallError) else 'Installation failed; check prerequisites and rerun. Sensitive details withheld.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
