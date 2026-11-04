import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
import pipeline


SHA = "a" * 40
ENV = {
    "GITHUB_REPOSITORY": "Coc-di-lam/backend", "GITHUB_SHA": SHA,
    "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.calls = []
        self.subject = pipeline.Pipeline(self.home, ENV, command=self.command)
        self.notifications = []
        self.subject.notify = self.notifications.append

    def command(self, args, cwd=None, capture=False):
        self.calls.append((args, cwd))
        if args[:2] == ["git", "ls-remote"]:
            return SHA + "\trefs/heads/main\n"
        return ""

    def ready(self):
        self.release = self.home / "releases" / "test"
        self.release.mkdir(parents=True)
        self.files = [str(self.release / "compose.yaml"), str(self.release / "images.json")]
        self.subject.prepare_release = lambda: (self.release, self.files)

    def test_source_urls_use_per_repository_deploy_key_hosts(self):
        for name in pipeline.REPOS:
            self.assertEqual(self.subject.repo_url(name), "git@github-dpl-{0}:Coc-di-lam/{0}.git".format(name))

    @patch("pipeline.host_lock")
    def test_build_failure_never_updates_running_services(self, lock):
        self.ready()
        def fail(args, **kwargs):
            self.calls.append((args, None))
            if "ls-remote" in args:
                return SHA + "\trefs/heads/main"
            if "build" in args:
                raise pipeline.PipelineError("build failed")
            return ""
        self.subject.command = fail
        with self.assertRaises(pipeline.PipelineError):
            self.subject.execute()
        self.assertFalse(any("up" in args for args, _ in self.calls))
        self.assertFalse((self.home / "state" / "current.json").exists())
        self.assertTrue(self.notifications[-1].startswith("❌ backend thất bại ở bước build"))

    @patch("pipeline.host_lock")
    def test_success_builds_before_deploy_and_records_current(self, lock):
        self.ready()
        self.assertEqual(self.subject.execute(), "deployed")
        verbs = ["build" if "build" in args else "up" if "up" in args else "git" for args, _ in self.calls]
        self.assertLess(verbs.index("build"), verbs.index("up"))
        current = json.loads((self.home / "state" / "current.json").read_text())
        self.assertEqual(current["files"], self.files)
        up = next(args for args, _ in self.calls if "up" in args)
        self.assertIn("--wait", up)
        self.assertIn("--no-build", up)
        # Only old build cache is pruned, after the deploy; never images, containers or volumes.
        prunes = [args for args, _ in self.calls if "prune" in args]
        self.assertEqual(prunes, [["docker", "builder", "prune", "--force", "--filter", "until=168h"]])
        self.assertGreater(self.calls.index((prunes[0], None)), max(i for i, (a, _) in enumerate(self.calls) if "up" in a))
        self.assertFalse(any("down" in args for args, _ in self.calls))

    @patch("pipeline.host_lock")
    def test_nondefault_branch_only_builds(self, lock):
        self.ready()
        self.subject = pipeline.Pipeline(self.home, dict(ENV, GITHUB_REF="refs/heads/feature/test"), command=self.command)
        self.subject.notify = self.notifications.append
        self.subject.prepare_release = lambda: (self.release, self.files)
        self.assertEqual(self.subject.execute(), "built")
        self.assertTrue(any("build" in args for args, _ in self.calls))
        self.assertFalse(any("up" in args for args, _ in self.calls))

    @patch("pipeline.host_lock")
    def test_stale_commit_is_skipped_before_build(self, lock):
        self.subject.command = lambda *args, **kwargs: "b" * 40 + "\trefs/heads/main"
        self.subject.prepare_release = lambda: self.fail("stale event should not clone")
        self.assertEqual(self.subject.execute(), "skipped")

    @patch("pipeline.host_lock")
    def test_commit_superseded_during_build_is_not_deployed(self, lock):
        self.ready()
        latest = iter([True, False])
        self.subject.is_current = lambda: next(latest)
        self.assertEqual(self.subject.execute(), "skipped")
        self.assertTrue(any("build" in args for args, _ in self.calls))
        self.assertFalse(any("up" in args for args, _ in self.calls))

    @patch("pipeline.host_lock")
    def test_health_failure_rolls_back_previous_manifest(self, lock):
        self.ready()
        state = self.home / "state"
        state.mkdir()
        previous = {"files": [str(self.home / "baseline.json")]}
        (state / "current.json").write_text(json.dumps(previous))
        def fail(args, **kwargs):
            self.calls.append((args, None))
            if "ls-remote" in args:
                return SHA + "\trefs/heads/main"
            if "up" in args and self.files[0] in args:
                raise pipeline.PipelineError("health failed")
            return ""
        self.subject.command = fail
        with self.assertRaises(pipeline.PipelineError):
            self.subject.execute()
        ups = [args for args, _ in self.calls if "up" in args]
        self.assertEqual(len(ups), 2)
        self.assertIn(previous["files"][0], ups[1])
        self.assertEqual(json.loads((state / "current.json").read_text()), previous)
        self.assertIn("❌ backend thất bại ở bước deploy", self.notifications[-1])
        self.assertIn("Đã khôi phục bản đang chạy trước đó", self.notifications[-1])

    @patch("pipeline.host_lock")
    def test_notification_failure_does_not_hide_build_failure(self, lock):
        self.ready()
        self.subject.notify = lambda message: (_ for _ in ()).throw(RuntimeError("notification failure"))
        self.subject.command = lambda *args, **kwargs: (_ for _ in ()).throw(pipeline.PipelineError("git failure"))
        with self.assertRaisesRegex(pipeline.PipelineError, "git failure"):
            self.subject.execute()

    def test_invalid_repository_and_tag_events_are_rejected(self):
        for env in (dict(ENV, GITHUB_REPOSITORY="evil/backend"), dict(ENV, GITHUB_REF="refs/tags/v1")):
            with self.assertRaises(pipeline.PipelineError):
                pipeline.Pipeline(self.home, env)

    def prepare_sources(self, services=None, runtimes=None, gpu_mode=None, without_gpu=None, edge_file=False):
        config = self.home / "config"
        config.mkdir()
        if gpu_mode:
            (config / "gpu-mode").write_text(gpu_mode)
        for name in ("backend", "core_agent", "core-tts-stt"):
            (config / (name + ".env")).write_text("SECRET=hidden")
        def clone(args, cwd=None, capture=False):
            self.calls.append((args, cwd))
            if args[:2] == ["git", "ls-remote"]:
                return SHA + "\trefs/heads/main"
            if args[:2] == ["git", "clone"]:
                dest = Path(args[-1])
                dest.mkdir()
                if dest.name == "env":
                    (dest / "compose.yaml").write_text("name: dpl-pvan")
                    (dest / "compose.gateway.yaml").write_text("services: {}")
                    (dest / "compose.core.yaml").write_text("services: {}")
                    if edge_file:
                        (dest / "compose.edge.yaml").write_text("services: {}")
            if "rev-parse" in args:
                return SHA
            if "config" in args and without_gpu is not None and any(pipeline.GPU_OVERRIDE_FILE in str(a) for a in args):
                return json.dumps({"name": "dpl-pvan", "services": without_gpu})
            if "config" in args:
                return json.dumps({"name": "dpl-pvan", "services": services if services is not None else {name: {"build": {}} for name in ("app", "gateway", "web", "speech", "minio")}})
            if args[:2] == ["docker", "info"]:
                return json.dumps(runtimes if runtimes is not None else {"runc": {}})
            return ""
        self.subject.command = clone

    def test_prepare_release_pins_trigger_sha_and_copies_secrets_privately(self):
        self.prepare_sources()
        release, files = self.subject.prepare_release()
        self.assertEqual((release / "backend" / ".env").read_text(), "SECRET=hidden")
        self.assertTrue(any("checkout" in args and SHA in args for args, _ in self.calls))
        override = json.loads(Path(files[-1]).read_text())
        self.assertEqual(set(override["services"]), {"app", "gateway", "web", "speech", "minio"})
        self.assertIn("healthcheck", override["services"]["web"])
        self.assertFalse(any(args[:2] == ["docker", "info"] for args, _ in self.calls))

    @patch("pipeline.host_lock")
    def test_nvidia_request_without_runtime_fails_before_build_or_deploy(self, lock):
        services = {"speech": {"build": {}, "deploy": {"resources": {"reservations": {"devices": [
            {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
        ]}}}}}
        self.prepare_sources(services, gpu_mode="require")
        with self.assertRaisesRegex(pipeline.PipelineError, "NVIDIA runtime required.*CT104"):
            self.subject.execute()
        self.assertTrue(any(args[:2] == ["docker", "info"] for args, _ in self.calls))
        self.assertFalse(any("build" in args or "up" in args for args, _ in self.calls))

    def test_nvidia_request_with_runtime_prepares_release(self):
        services = {"speech": {"build": {}, "deploy": {"resources": {"reservations": {"devices": [
            {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
        ]}}}}}
        self.prepare_sources(services, {"runc": {}, "nvidia": {"path": "nvidia-container-runtime"}})
        release, files = self.subject.prepare_release()
        self.assertTrue(release.is_dir())
        self.assertEqual(len(files), 2)
        self.assertTrue(any(args[:2] == ["docker", "info"] for args, _ in self.calls))

    def test_gpus_all_also_requires_nvidia_runtime(self):
        self.prepare_sources({"speech": {"build": {}, "gpus": "all"}}, gpu_mode="require")
        with self.assertRaisesRegex(pipeline.PipelineError, "NVIDIA runtime required"):
            self.subject.prepare_release()

    def test_auto_gpu_mode_without_runtime_runs_gpu_services_on_cpu(self):
        self.prepare_sources({"speech": {"build": {}, "gpus": "all"}}, without_gpu={"speech": {"build": {}}})
        release, files = self.subject.prepare_release()
        self.assertTrue(any(f.endswith(pipeline.GPU_OVERRIDE_FILE) for f in files))
        self.assertIn("gpus: !reset []", (release / pipeline.GPU_OVERRIDE_FILE).read_text())

    @patch("pipeline.host_lock")
    def test_lint_and_tests_of_pushed_repo_run_before_build_without_host_env(self, lock):
        self.ready()
        self.assertEqual(self.subject.execute(), "deployed")
        runs = [i for i, (args, _) in enumerate(self.calls) if args[:2] == ["docker", "run"]]
        builds = [i for i, (args, _) in enumerate(self.calls) if "build" in args]
        self.assertEqual(len(runs), 1)
        self.assertLess(runs[0], builds[0])
        check = self.calls[runs[0]][0]
        self.assertIn("{}:/src:ro".format(self.release / "backend"), check)
        self.assertIn("rm -f /work/.env", check[-1])
        self.assertIn("ruff", check[-1])
        self.assertIn("pytest", check[-1])

    @patch("pipeline.host_lock")
    def test_failed_tests_stop_before_build_and_report_stage(self, lock):
        self.ready()
        def fail(args, **kwargs):
            self.calls.append((args, None))
            if "ls-remote" in args:
                return SHA + "\trefs/heads/main"
            if args[:2] == ["docker", "run"]:
                raise pipeline.PipelineError("docker command failed (exit 1)")
            return ""
        self.subject.command = fail
        with self.assertRaises(pipeline.PipelineError):
            self.subject.execute()
        self.assertFalse(any("build" in args or "up" in args for args, _ in self.calls))
        self.assertTrue(self.notifications[-1].startswith("❌ backend thất bại ở bước lint/test"))

    def published(self, port, target):
        return {"mode": "ingress", "host_ip": "127.0.0.1", "target": target, "published": str(port), "protocol": "tcp"}

    def test_bind_address_opens_only_the_edge_when_the_stack_has_one(self):
        services = {"edge": {"build": {}, "ports": [self.published(8030, 8080), self.published(8031, 8081)]},
                    "postgres": {"image": "postgres", "ports": [self.published(5432, 5432)]},
                    "app": {"build": {}, "ports": [self.published(8001, 8000)]}}
        self.prepare_sources(services)
        (self.home / "config" / "bind-address").write_text("0.0.0.0")
        release, files = self.subject.prepare_release()
        override = (release / pipeline.BIND_OVERRIDE_FILE).read_text()
        self.assertIn("  edge:", override)
        self.assertNotIn("postgres", override)
        self.assertNotIn("  app:", override)
        self.assertEqual(override.count('"host_ip": "0.0.0.0"'), 2)
        self.assertIn(str(release / pipeline.BIND_OVERRIDE_FILE), files)

    def test_bind_address_without_an_edge_keeps_opening_every_port(self):
        services = {"web": {"build": {}, "ports": [self.published(8030, 8080)]},
                    "app": {"build": {}, "ports": [self.published(8001, 8000)]}}
        self.prepare_sources(services)
        (self.home / "config" / "bind-address").write_text("0.0.0.0")
        release, _ = self.subject.prepare_release()
        override = (release / pipeline.BIND_OVERRIDE_FILE).read_text()
        self.assertIn("  web:", override)
        self.assertIn("  app:", override)

    def test_env_without_edge_override_still_prepares(self):
        self.prepare_sources()
        release, _ = self.subject.prepare_release()
        self.assertTrue((release / "compose.core.yaml").is_file())
        self.assertFalse((release / "compose.edge.yaml").exists())
        self.calls.clear()

    def test_edge_override_is_copied_from_env(self):
        self.prepare_sources(edge_file=True)
        release, _ = self.subject.prepare_release()
        self.assertTrue((release / "compose.edge.yaml").is_file())

    def test_every_repository_has_checks(self):
        self.assertEqual(set(pipeline.CHECKS), set(pipeline.REPOS))

    @patch("pipeline.host_lock")
    def test_success_message_names_repo_pusher_commit_and_link(self, lock):
        self.ready()
        self.subject = pipeline.Pipeline(self.home, dict(ENV, GITHUB_ACTOR="nguyenvana"), command=self.command)
        self.subject.notify = self.notifications.append
        self.subject.prepare_release = lambda: (self.release, self.files)
        self.subject.read_subject = lambda release: setattr(self.subject, "subject", "Sửa lỗi đăng nhập")
        self.subject.execute()
        self.assertEqual(self.notifications, [
            "✅ backend build thành công, đã deploy lên CT104\n"
            "👤 Push bởi: nguyenvana\n"
            "📝 Commit: aaaaaaa – Sửa lỗi đăng nhập\n"
            "🔗 https://github.com/Coc-di-lam/backend/actions/runs/123"])

    @patch("pipeline.host_lock")
    def test_superseded_commit_sends_no_notification(self, lock):
        self.subject.command = lambda *args, **kwargs: "b" * 40 + "\trefs/heads/main"
        self.assertEqual(self.subject.execute(), "skipped")
        self.assertEqual(self.notifications, [])


class CommandTests(unittest.TestCase):
    @patch("pipeline.subprocess.run")
    def test_git_never_prompts_and_receives_no_github_token(self, run):
        run.return_value = pipeline.subprocess.CompletedProcess([], 0, stdout="head")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "admin-secret", "GITHUB_READ_TOKEN": "read-secret"}):
            self.assertEqual(pipeline.run_command(["git", "ls-remote", "git@github-dpl-env:Coc-di-lam/env.git"], capture=True), "head")
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertNotIn("GITHUB_READ_TOKEN", environment)

    @patch("pipeline.subprocess.run")
    def test_non_git_children_also_do_not_receive_github_tokens(self, run):
        run.return_value = pipeline.subprocess.CompletedProcess([], 0, stdout="")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "admin-secret", "GITHUB_READ_TOKEN": "read-secret"}):
            pipeline.run_command(["docker", "info"], capture=True)
        self.assertEqual(run.call_args.args[0], ["docker", "info"])
        self.assertNotIn("GITHUB_TOKEN", run.call_args.kwargs["env"])
        self.assertNotIn("GITHUB_READ_TOKEN", run.call_args.kwargs["env"])

    @patch("pipeline.subprocess.run")
    def test_subprocess_error_does_not_echo_credential_or_error_output(self, run):
        run.side_effect = pipeline.subprocess.CalledProcessError(128, ["git"], output="admin-secret", stderr="read-secret")
        with self.assertRaises(pipeline.PipelineError) as error:
            pipeline.run_command(["git", "ls-remote", "https://github.com/Coc-di-lam/env.git"], capture=True)
        self.assertEqual(str(error.exception), "git command failed (exit 128)")
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("https://", str(error.exception))


if __name__ == "__main__":
    unittest.main()
