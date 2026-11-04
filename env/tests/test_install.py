import hashlib
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

CI = Path(__file__).resolve().parents[1] / 'ci'
sys.path.insert(0, str(CI))
import install


class InstallerTests(unittest.TestCase):
    def test_github_validation_error_identifies_endpoint_and_reason_without_token(self):
        import urllib.error
        body = {'message': 'Validation Failed', 'errors': [
            {'resource': 'PublicKey', 'field': 'key', 'code': 'custom',
             'message': 'key is already in use; test-secret-token'}]}
        response = urllib.error.HTTPError('https://api.github.com/repos/Coc-di-lam/env/keys',
                                         422, 'Unprocessable Entity', {}, io.BytesIO(json.dumps(body).encode()))
        with patch.object(install.urllib.request, 'urlopen', side_effect=response):
            with self.assertRaises(install.InstallError) as error:
                install.GitHub('test-secret-token').call('/repos/Coc-di-lam/env/keys',
                                                       {'key': 'ssh-ed25519 public-test', 'read_only': True})
        self.assertIn('POST /repos/Coc-di-lam/env/keys', str(error.exception))
        self.assertIn('key is already in use', str(error.exception))
        self.assertNotIn('test-secret-token', str(error.exception))

    def test_github_validation_error_does_not_echo_response_urls(self):
        import urllib.error
        body = {'message': 'See https://example.test/?token=encoded-sensitive-value'}
        response = urllib.error.HTTPError('https://api.github.com/user', 422, 'Unprocessable Entity',
                                         {}, io.BytesIO(json.dumps(body).encode()))
        with patch.object(install.urllib.request, 'urlopen', side_effect=response):
            with self.assertRaises(install.InstallError) as error:
                install.GitHub('test-token').call('/user')
        self.assertNotIn('encoded-sensitive-value', str(error.exception))

    def test_old_python_uses_isolated_safe_extractor_after_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'runner.tar.gz'
            archive.write_bytes(b'checksum-verified-download')
            target = Path(tmp) / 'runner'
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(install, 'hasattr', return_value=False, create=True), \
                    patch.object(install, 'command') as command:
                install.extract_runner(archive, target, digest)
            args = command.call_args.args[0]
            self.assertEqual(args[:3], ['docker', 'run', '--rm'])
            self.assertIn('--read-only', args)
            self.assertIn('none', args)
            self.assertIn('python:3.13-slim', args)
            self.assertIn("filter='data'", args[-1])
            self.assertTrue(target.is_dir())

    def test_old_python_still_rejects_checksum_mismatch_before_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'runner.tar.gz'
            archive.write_bytes(b'bad-download')
            with patch.object(install, 'hasattr', return_value=False, create=True), \
                    patch.object(install, 'command') as command:
                with self.assertRaises(install.InstallError):
                    install.extract_runner(archive, Path(tmp) / 'runner', '0' * 64)
            command.assert_not_called()

    def test_checksum_comes_from_official_digest_or_release_body(self):
        digest = 'a' * 64
        asset = {'name': 'actions-runner-linux-x64-2.tar.gz', 'digest': 'sha256:' + digest}
        self.assertEqual(install.asset_checksum(asset, ''), digest)
        asset.pop('digest')
        self.assertEqual(install.asset_checksum(asset, '<!-- BEGIN SHA linux-x64 -->' + digest + '<!-- END SHA linux-x64 -->'), digest)
        with self.assertRaises(install.InstallError):
            install.asset_checksum(asset, '')

    def test_checksum_mismatch_stops_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'runner.tar.gz'
            archive.write_bytes(b'wrong archive')
            with self.assertRaises(install.InstallError):
                install.extract_runner(archive, Path(tmp) / 'runner', '0' * 64)
            self.assertFalse((Path(tmp) / 'runner').exists())

    def test_archive_cannot_escape_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'runner.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                member = tarfile.TarInfo('../escape')
                member.size = 1
                tar.addfile(member, io.BytesIO(b'x'))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(install.InstallError):
                install.extract_runner(archive, Path(tmp) / 'runner', digest)
            self.assertFalse((Path(tmp) / 'escape').exists())

    def test_group_discovery_does_not_choose_private_chat(self):
        self.assertIsNone(install.group_chat_id({'message': {'chat': {'id': 'private', 'chat_type': 'PRIVATE'}}}))
        self.assertEqual(install.group_chat_id({'message': {'chat': {'id': 123, 'chat_type': 'GROUP'}}}), '123')

    def test_group_polling_continues_after_request_timeout(self):
        import zalo
        event = {'message': {'chat': {'id': 'group-104', 'chat_type': 'GROUP'}}}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install, 'CONFIG', Path(tmp)), patch.object(install.getpass, 'getpass', return_value='secret'), \
                    patch('builtins.input', return_value=''), patch.object(install, 'command'), \
                    patch.object(install, 'deployment_lock', return_value=contextlib.nullcontext()) as lock, \
                    patch.object(zalo, 'api_call', side_effect=[{}, {}, {}, event]) as api, \
                    patch.object(zalo, 'send_message') as send:
                install.configure_zalo()
            self.assertEqual(api.call_count, 4)
            lock.assert_called_once()
            self.assertEqual(json.loads((Path(tmp) / 'zalo.json').read_text())['chat_id'], 'group-104')
            self.assertEqual(send.call_args.args[1], 'group-104')

    def test_valid_bot_without_webhook_can_configure_group(self):
        import zalo
        responses = [
            {'ok': True, 'result': {'can_join_groups': True}},
            {'ok': False, 'description': 'Not Found', 'error_code': 404},
            {'ok': True, 'result': {'message': {'chat': {'id': 'group-104', 'chat_type': 'GROUP'}}}},
            {'ok': True, 'result': {}},
        ]
        streams = [io.BytesIO(json.dumps(response).encode()) for response in responses]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install, 'CONFIG', Path(tmp)), patch.object(install.getpass, 'getpass', return_value='test-token'), \
                    patch('builtins.input', return_value=''), patch.object(install, 'command'), \
                    patch.object(install, 'deployment_lock', return_value=contextlib.nullcontext()), \
                    patch.object(zalo, 'urlopen', side_effect=streams) as request:
                install.configure_zalo()
            methods = [call.args[0].full_url.rsplit('/', 1)[1] for call in request.call_args_list]
            self.assertEqual(methods, ['getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage'])
            self.assertEqual(json.loads((Path(tmp) / 'zalo.json').read_text())['chat_id'], 'group-104')
        for stream in streams:
            stream.close()

    def test_invalid_bot_stops_before_webhook_or_polling(self):
        import zalo
        with tempfile.TemporaryDirectory() as tmp:
            for code in (401, 403):
                response = io.BytesIO(json.dumps({'ok': False, 'error_code': code}).encode())
                with patch.object(install, 'CONFIG', Path(tmp)), patch.object(install.getpass, 'getpass', return_value='test-token'), \
                        patch.object(zalo, 'urlopen', return_value=response) as request, \
                        patch('builtins.input') as input_prompt:
                    with self.assertRaises(install.InstallError):
                        install.configure_zalo()
                request.assert_called_once()
                self.assertTrue(request.call_args.args[0].full_url.endswith('/getMe'))
                input_prompt.assert_not_called()
                self.assertFalse((Path(tmp) / 'zalo.json').exists())

    def test_zalo_preset_configures_group_without_prompts_or_polling(self):
        import zalo
        preset = {'token': 'test-token', 'chat_id': 'test-group'}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install, 'CONFIG', Path(tmp)), patch.object(install.getpass, 'getpass') as password, \
                    patch('builtins.input') as prompt, patch.object(install, 'command'), \
                    patch.object(install, 'deployment_lock', return_value=contextlib.nullcontext()), \
                    patch.object(zalo, 'api_call', return_value={}) as api, patch.object(zalo, 'send_message') as send:
                install.configure_zalo(preset)
            password.assert_not_called()
            prompt.assert_not_called()
            api.assert_called_once_with('test-token', 'getMe')
            self.assertEqual(send.call_args.args[:2], ('test-token', 'test-group'))
            self.assertEqual(json.loads((Path(tmp) / 'zalo.json').read_text()), preset)

    def test_zalo_preset_rejects_missing_or_invalid_fields_before_network(self):
        import zalo
        invalid = [None, [], {}, {'token': '', 'chat_id': 'test-group'},
                   {'token': 'test-token'}, {'token': 'test-token', 'chat_id': ''},
                   {'token': 42, 'chat_id': 'test-group'}, {'token': 'test-token', 'chat_id': False}]
        with patch.object(zalo, 'api_call') as api:
            for preset in invalid:
                with self.subTest(preset=preset):
                    with self.assertRaises(install.InstallError) as error:
                        install.validate_zalo_preset(preset)
                    self.assertNotIn('test-token', str(error.exception))
            api.assert_not_called()

    def test_zalo_preset_auth_failure_does_not_write_config(self):
        import zalo
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install, 'CONFIG', Path(tmp)), \
                    patch.object(zalo, 'api_call', side_effect=zalo.ZaloError('Unauthorized')), \
                    patch.object(zalo, 'send_message') as send:
                with self.assertRaises(install.InstallError):
                    install.configure_zalo({'token': 'test-token', 'chat_id': 'test-group'})
            send.assert_not_called()
            self.assertFalse((Path(tmp) / 'zalo.json').exists())

    def test_malformed_zalo_preset_file_is_sanitized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'preset.json'
            path.write_text('{"token":"test-token", invalid-json}')
            with self.assertRaises(install.InstallError) as error:
                install.load_zalo_config(path)
            self.assertNotIn('test-token', str(error.exception))

    def test_zalo_preset_send_failure_does_not_write_config(self):
        import zalo
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(install, 'CONFIG', Path(tmp)), patch.object(zalo, 'api_call', return_value={}), \
                    patch.object(zalo, 'send_message', side_effect=zalo.ZaloError('Send rejected')):
                with self.assertRaises(install.InstallError):
                    install.configure_zalo({'token': 'test-token', 'chat_id': 'test-group'})
            self.assertFalse((Path(tmp) / 'zalo.json').exists())

    def test_main_passes_zalo_preset_to_normal_and_notifier_only_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = {'token': 'test-token', 'chat_id': 'test-group'}
            path = Path(tmp) / 'preset.json'
            path.write_text(json.dumps(preset))
            for mode in ([], ['--configure-zalo']):
                argv = ['install.py', '--workspace', tmp, '--zalo-config', str(path), *mode]
                with patch.object(sys, 'argv', argv), patch.object(install, 'preflight'), \
                        patch.object(install, 'validate_workspace', return_value=Path(tmp)), \
                        patch.object(install, 'read_github_token', return_value='test-github-token'), \
                        patch.object(install, 'GitHub') as github, patch.object(install, 'command'), \
                        patch.object(install, 'prepare_user_and_files'), patch.object(install, 'prepare_ssh'), \
                        patch.object(install, 'install_runners'), \
                        patch.object(install, 'deployment_lock', return_value=contextlib.nullcontext()), \
                        patch.object(install, 'configure_zalo') as configure:
                    github.return_value.call.return_value = {'permissions': {'admin': True}}
                    self.assertEqual(install.main(), 0)
                    configure.assert_called_once_with(preset)

    def test_skip_zalo_and_preset_are_mutually_exclusive(self):
        with patch.object(sys, 'argv', ['install.py', '--skip-zalo', '--zalo-config', 'preset.json']), \
                patch.object(sys, 'stderr', io.StringIO()), patch.object(install, 'preflight') as preflight:
            with self.assertRaises(SystemExit) as error:
                install.main()
            self.assertEqual(error.exception.code, 2)
            preflight.assert_not_called()

    def test_protected_config_is_written_without_echoing_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'config.json'
            install.write_private(path, '{"token":"never-print"}')
            self.assertEqual(path.read_text(), '{"token":"never-print"}')
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_github_error_does_not_include_credentials_or_response_body(self):
        import urllib.error
        error = urllib.error.HTTPError('https://api.github.com', 401, 'secret-value', {}, io.BytesIO(b'secret-value'))
        with patch.object(install.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(install.InstallError, 'HTTP 401') as context:
                install.GitHub('secret-value').call('/user')
        self.assertNotIn('secret-value', str(context.exception))
        self.assertTrue(error.closed)

    def test_administration_token_is_removed_from_child_environment(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'temporary-admin-token'}):
            self.assertEqual(install.read_github_token(), 'temporary-admin-token')
            self.assertNotIn('GITHUB_TOKEN', os.environ)

    def test_prompted_token_is_saved_and_reused_without_prompting(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp) / 'pat' / 'github-pat'
            with patch.object(install, 'TOKEN_FILE', saved), patch.dict(os.environ, {}, clear=False):
                os.environ.pop('GITHUB_TOKEN', None)
                with patch.object(install.getpass, 'getpass', return_value='prompted-token') as prompt:
                    self.assertEqual(install.read_github_token(), 'prompted-token')
                    self.assertEqual(install.read_github_token(), 'prompted-token')
                self.assertEqual(prompt.call_count, 1)
                if os.name == 'posix':
                    self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_no_child_process_inherits_administration_token(self):
        response = SimpleNamespace(returncode=0, stdout='ok')
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'temporary-admin-token'}), \
                patch.object(install.subprocess, 'run', return_value=response) as run:
            install.command(['docker', 'info'])
        self.assertNotIn('GITHUB_TOKEN', run.call_args.kwargs['env'])

    def test_existing_account_must_have_expected_home_and_primary_group(self):
        group = SimpleNamespace(gr_gid=123)
        account = SimpleNamespace(pw_dir=str(install.HOME), pw_gid=123)
        install.validate_service_account(account, group)
        account.pw_dir = '/wrong-home'
        with self.assertRaises(install.InstallError):
            install.validate_service_account(account, group)
        account.pw_dir = str(install.HOME)
        account.pw_gid = 99
        with self.assertRaises(install.InstallError):
            install.validate_service_account(account, group)

    def test_script_directory_itself_is_root_owned(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'home'
            root = Path(tmp) / 'protected'
            workspace = Path(tmp) / 'source'
            for name in ('backend', 'core_agent', 'core-tts-stt'):
                (workspace / name).mkdir(parents=True)
                (workspace / name / '.env').write_text('CONFIG=value')
            account = SimpleNamespace(pw_dir=str(home), pw_gid=123)
            modules = {'pwd': SimpleNamespace(getpwnam=lambda name: account),
                       'grp': SimpleNamespace(getgrnam=lambda name: SimpleNamespace(gr_gid=123))}
            with patch.object(install, 'HOME', home), patch.object(install, 'ROOT', root), \
                    patch.object(install, 'CONFIG', home / 'config'), patch.dict(sys.modules, modules), \
                    patch.object(install, 'deployment_lock', return_value=contextlib.nullcontext()) as lock, \
                    patch.object(install, 'seed_baseline') as baseline, \
                    patch.object(install, 'command', return_value='') as command:
                install.prepare_user_and_files(workspace)
            command.assert_any_call(['chown', 'root:root', str(root)])
            lock.assert_called_once()
            baseline.assert_called_once_with(workspace, home / 'state')

    def test_script_replacement_is_atomic_and_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'new.py'
            destination = Path(tmp) / 'current.py'
            source.write_text('print("complete new script")')
            destination.write_text('old script')
            replace = os.replace
            observed = []
            def replacement(temporary, target):
                observed.append((destination.read_text(), Path(temporary).read_text()))
                replace(temporary, target)
            with patch.object(install.os, 'replace', side_effect=replacement), patch.object(install, 'command'):
                install.write_root_script(source, destination)
            self.assertEqual(observed, [('old script', 'print("complete new script")')])
            self.assertEqual(destination.read_text(), source.read_text())
            if os.name != 'nt':
                self.assertEqual(destination.stat().st_mode & 0o777, 0o755)

    def test_workspace_requires_all_real_environment_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'compose.yaml').write_text('name: dpl-pvan')
            with self.assertRaises(install.InstallError):
                install.validate_workspace(root)
            for directory in ('core_agent', 'backend', 'core-tts-stt'):
                (root / directory).mkdir()
                (root / directory / '.env').write_text('REAL_SETTING=value')
            self.assertEqual(install.validate_workspace(root), root.resolve())

    def test_baseline_does_not_overwrite_previous_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            manifest = state / 'current.json'
            manifest.write_text('{"files":["/previous/compose.json"]}')
            with patch.object(install, 'command') as command:
                install.seed_baseline(Path('/source'), state)
            command.assert_not_called()
            self.assertEqual(json.loads(manifest.read_text())['files'], ['/previous/compose.json'])

    def test_all_runners_are_configured_before_any_service_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            asset = {'name': 'actions-runner-linux-x64-2.338.0.tar.gz',
                     'browser_download_url': 'https://github.com/actions/runner/releases/download/v2.338.0/runner.tar.gz',
                     'digest': 'sha256:' + 'a' * 64}
            github = unittest.mock.Mock()
            github.call.side_effect = lambda path, payload=None: ({'assets': [asset]} if path.endswith('/latest')
                else {'token': 'short-lived-registration'} if path.endswith('/registration-token') else {'runners': []})
            def record(args, **kwargs):
                calls.append([str(a) for a in args])
                return ''
            def extract(archive, target, checksum):
                target.mkdir(parents=True)
            with patch.object(install, 'HOME', Path(tmp)), patch.object(install, 'command', side_effect=record), \
                    patch.object(install, 'extract_runner', side_effect=extract), \
                    patch.object(install.urllib.request, 'urlopen', return_value=io.BytesIO(b'archive')):
                install.install_runners(github, ['backend', 'frontend'])
            config_indices = [i for i, call in enumerate(calls) if '--unattended' in call]
            starts = [i for i, call in enumerate(calls) if call[-1] == 'start']
            self.assertLess(max(config_indices), min(starts))


if __name__ == '__main__':
    unittest.main()
