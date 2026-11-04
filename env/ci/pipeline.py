#!/usr/bin/env python3
"""Build immutable DPL releases and update the existing Compose project on CT 104."""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from zalo import ZaloError, send_message


REPOS = {
    "env": {"directory": "env", "branch": "master"},
    "backend": {"directory": "backend", "branch": "main"},
    "core_ai_agent": {"directory": "core_agent", "branch": "main"},
    "core-tts-stt": {"directory": "core-tts-stt", "branch": "main"},
    "frontend": {"directory": "frontend", "branch": "main"},
    "RAG_CocDiLam": {"directory": "RAG_CocDiLam", "branch": "main"},
}
CONFIG_COMPONENTS = ("backend", "core_agent", "core-tts-stt")

# Lint + tests of the pushed repository, run in a throwaway container before any image is built.
# Lint only flags errors that break at runtime (syntax errors, undefined names), not style.
UV_IMAGE = "ghcr.io/astral-sh/uv:0.8.22-python3.12-bookworm-slim"
NODE_IMAGE = "node:22-alpine"
RUFF = "uvx ruff@0.6.9 check --target-version py311 --select E9,F63,F7,F82 --output-format concise ."
PYTEST = RUFF + " && uv sync --frozen && uv run --frozen pytest -q -p no:cacheprovider"
CHECKS = {
    "env": (UV_IMAGE, RUFF + " && python -m unittest discover -s tests"),
    "backend": (UV_IMAGE, PYTEST),
    "core_ai_agent": (UV_IMAGE, PYTEST),
    "core-tts-stt": (UV_IMAGE, PYTEST),
    "RAG_CocDiLam": (UV_IMAGE, PYTEST),
    "frontend": (NODE_IMAGE, "npm ci --no-audit --no-fund && npx tsc -b"),
}
CHECK_CACHE_VOLUME = "dpl-ci-check-cache"
# Build cache unused for this long is dropped after a successful deploy; recent layers stay cached.
BUILD_CACHE_MAX_AGE = "168h"

# GPU policy, read from /var/lib/dpl-ci/config/gpu-mode (or env DPL_GPU_MODE):
#   auto    - use the GPU when Docker has the nvidia runtime, otherwise run GPU services on CPU (default)
#   cpu     - always strip GPU requests and run on CPU
#   require - fail if a service requests a GPU and the nvidia runtime is missing (old behaviour)
GPU_MODES = ("auto", "cpu", "require")
GPU_OVERRIDE_FILE = "compose.no-gpu.yaml"

# Host address for every published port, read from /var/lib/dpl-ci/config/bind-address
# (or env DPL_BIND_ADDRESS), e.g. 0.0.0.0. Missing/empty = keep what each compose.yaml says.
BIND_OVERRIDE_FILE = "compose.bind.yaml"
# When the stack has this entry point, only its ports get the bind address; the rest stay on 127.0.0.1.
EDGE_SERVICE = "edge"


class PipelineError(RuntimeError):
    pass


@contextlib.contextmanager
def host_lock(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        print("Waiting for host-wide build/deployment lock", flush=True)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_command(args, cwd=None, capture=False):
    environment = dict(os.environ, COMPOSE_PARALLEL_LIMIT="1", COMPOSE_BAKE="false", GIT_TERMINAL_PROMPT="0")
    # No child (git, docker, test containers) needs a GitHub token; never pass one on.
    for name in ("GITHUB_TOKEN", "GITHUB_READ_TOKEN"):
        environment.pop(name, None)
    try:
        result = subprocess.run(args, cwd=cwd, env=environment, check=True, text=True,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None)
    except subprocess.CalledProcessError as exc:
        raise PipelineError("{} command failed (exit {})".format(Path(args[0]).name, exc.returncode)) from None
    except OSError:
        raise PipelineError("Unable to execute {}".format(Path(args[0]).name)) from None
    return result.stdout or ""


def read_host_setting(path):
    """Read a small host config file; missing = "", unreadable = clear PipelineError."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise PipelineError("Cannot read {} ({}); check its owner/permissions match the other files in {}"
                            .format(path, exc.strerror or type(exc).__name__, path.parent)) from None


def make_build_context_readable(root):
    """The pipeline runs with umask 077, so git checkouts are 0600/0700. Docker COPY keeps those
    modes and non-root container users then cannot read their own code. Open the source tree
    (dirs 755, files 644, executables 755); the release directory itself stays 0700."""
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name != ".git"]
        os.chmod(directory, 0o755)
        for name in filenames:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                continue
            mode = os.stat(path).st_mode
            os.chmod(path, 0o755 if mode & 0o100 else 0o644)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def gpu_requests(config):
    """Return {service: [what requests a GPU]} for every service asking for an NVIDIA GPU."""
    requested = {}
    for name, service in (config.get("services") or {}).items():
        reasons = []
        devices = (((service.get("deploy") or {}).get("resources") or {}).get("reservations") or {}).get("devices") or []
        if any(isinstance(device, dict) and device.get("driver") == "nvidia" for device in devices):
            reasons.append("devices")
        gpus = service.get("gpus")
        if gpus == "all" or (isinstance(gpus, list) and any(
                isinstance(device, dict) and device.get("driver", "nvidia") == "nvidia" for device in gpus)):
            reasons.append("gpus")
        if service.get("runtime") == "nvidia":
            reasons.append("runtime")
        if reasons:
            requested[name] = reasons
    return requested


def no_gpu_override(requested):
    """Compose override (needs Compose >= 2.24.4 for !reset) that removes the GPU requests."""
    lines = ["# Generated by dpl-ci: no NVIDIA runtime on this host, GPU requests removed.", "services:"]
    for name in sorted(requested):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name):
            raise PipelineError("Unsupported Compose service name")
        lines.append("  {}:".format(name))
        reasons = requested[name]
        if "devices" in reasons:
            lines += ["    deploy:", "      resources:", "        reservations:", "          devices: !reset []"]
        if "gpus" in reasons:
            lines.append("    gpus: !reset []")
        if "runtime" in reasons:
            lines.append("    runtime: !reset null")
    return "\n".join(lines) + "\n"


class Pipeline:
    def __init__(self, home=Path("/var/lib/dpl-ci"), environment=None, command=run_command):
        self.home = Path(home)
        self.environment = dict(os.environ if environment is None else environment)
        self.command = command
        repository = self.environment.get("GITHUB_REPOSITORY", "")
        self.repo = repository[len("Coc-di-lam/"):] if repository.startswith("Coc-di-lam/") else repository
        if repository != "Coc-di-lam/" + self.repo or self.repo not in REPOS:
            raise PipelineError("Unsupported GitHub repository")
        self.sha = self.environment.get("GITHUB_SHA", "")
        if not re.fullmatch(r"[0-9a-f]{40}", self.sha):
            raise PipelineError("Invalid triggering commit")
        ref = self.environment.get("GITHUB_REF", "")
        self.branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        if not ref.startswith("refs/heads/") or not self.branch or self.branch.startswith("-"):
            raise PipelineError("Only branch events are supported")
        self.deploy = self.branch == REPOS[self.repo]["branch"]
        self.run_id = self.environment.get("GITHUB_RUN_ID", "")
        self.attempt = self.environment.get("GITHUB_RUN_ATTEMPT", "1")
        if not self.run_id.isdigit() or not self.attempt.isdigit():
            raise PipelineError("Invalid workflow run identifier")
        self.run_url = "https://github.com/{}/actions/runs/{}".format(repository, self.run_id)
        self.state = self.home / "state"
        self.actor = self.environment.get("GITHUB_ACTOR", "")[:60] or "không rõ"
        self.subject = ""
        self.stage = "chuẩn bị mã nguồn"

    def repo_url(self, name):
        return "git@github-dpl-{}:Coc-di-lam/{}.git".format(name, name)

    def is_current(self):
        output = self.command(["git", "ls-remote", self.repo_url(self.repo), "refs/heads/" + self.branch], capture=True)
        fields = output.strip().split()
        if not fields or not re.fullmatch(r"[0-9a-f]{40}", fields[0]):
            raise PipelineError("Cannot resolve current branch head")
        return fields[0] == self.sha

    def compose(self, files):
        args = ["docker", "compose", "--project-name", "dpl-pvan"]
        for file in files:
            args += ["--file", str(file)]
        return args

    def gpu_mode(self):
        mode = self.environment.get("DPL_GPU_MODE", "").strip().lower()
        path = self.home / "config" / "gpu-mode"
        if not mode:
            mode = read_host_setting(path).lower()
        mode = mode or "auto"
        if mode not in GPU_MODES:
            raise PipelineError("Invalid GPU mode {!r}; use one of: {}".format(mode, ", ".join(GPU_MODES)))
        return mode

    def has_nvidia_runtime(self):
        try:
            runtimes = json.loads(self.command(["docker", "info", "--format", "{{json .Runtimes}}"], capture=True))
        except (PipelineError, ValueError, TypeError):
            return False
        return isinstance(runtimes, dict) and "nvidia" in runtimes

    def resolve_gpu(self, release, files, config):
        """Return (files, config) that this host can actually run."""
        requested = gpu_requests(config)
        if not requested:
            return files, config
        names = ", ".join(sorted(requested))
        mode = self.gpu_mode()
        if mode != "cpu" and self.has_nvidia_runtime():
            print("NVIDIA runtime available; GPU enabled for {}".format(names), flush=True)
            return files, config
        if mode == "require":
            raise PipelineError(
                "NVIDIA runtime required by {}; CT104 must have GPU passthrough and NVIDIA Container Toolkit "
                "configured before this stack can be built/deployed. Running services were not changed.".format(names))
        override = release / GPU_OVERRIDE_FILE
        override.write_text(no_gpu_override(requested), encoding="utf-8")
        override.chmod(0o600)
        files = files + [str(override)]
        try:
            config = json.loads(self.command(self.compose(files) + ["config", "--format", "json"], capture=True))
        except PipelineError:
            raise PipelineError("Unable to remove GPU requests (Docker Compose >= 2.24.4 is required for !reset). "
                                "Running services were not changed.") from None
        left = gpu_requests(config)
        if left:
            raise PipelineError("GPU requests still present for {} after override. Running services were not changed."
                                .format(", ".join(sorted(left))))
        print("::warning::No NVIDIA runtime (gpu-mode={}); running {} without GPU".format(mode, names), flush=True)
        return files, config

    def bind_address(self):
        address = self.environment.get("DPL_BIND_ADDRESS", "").strip()
        path = self.home / "config" / "bind-address"
        if not address:
            address = read_host_setting(path)
        if address and not re.fullmatch(r"(\d{1,3}\.){3}\d{1,3}|::|::1", address):
            raise PipelineError("Invalid bind address {!r}".format(address))
        return address

    def resolve_bind(self, release, files, config):
        """Rewrite the host IP of published ports to the configured bind address: only the edge's
        ports when the stack has an edge, otherwise (older env revisions) every published port."""
        address = self.bind_address()
        if not address:
            return files, config
        services = config.get("services") or {}
        if EDGE_SERVICE in services:
            services = {EDGE_SERVICE: services[EDGE_SERVICE]}
        lines = ["# Generated by dpl-ci: published ports of {} bound to {}.".format(", ".join(sorted(services)), address),
                 "services:"]
        changed = []
        for name, service in sorted(services.items()):
            ports = service.get("ports") or []
            if not any(isinstance(port, dict) and port.get("published") for port in ports):
                continue
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name):
                raise PipelineError("Unsupported Compose service name")
            rewritten = []
            for port in ports:
                port = dict(port)
                if port.get("published"):
                    port["host_ip"] = address
                    changed.append("{}:{}".format(name, port["published"]))
                rewritten.append(port)
            lines += ["  {}:".format(name), "    ports: !override " + json.dumps(rewritten)]
        if not changed:
            return files, config
        override = release / BIND_OVERRIDE_FILE
        override.write_text("\n".join(lines) + "\n", encoding="utf-8")
        override.chmod(0o600)
        files = files + [str(override)]
        try:
            config = json.loads(self.command(self.compose(files) + ["config", "--format", "json"], capture=True))
        except PipelineError:
            raise PipelineError("Unable to rewrite port bindings (Docker Compose >= 2.24.4 is required for !override). "
                                "Running services were not changed.") from None
        print("Published ports bound to {}: {}".format(address, ", ".join(changed)), flush=True)
        return files, config

    def prepare_release(self):
        releases = self.home / "releases"
        releases.mkdir(parents=True, exist_ok=True)
        release = Path(tempfile.mkdtemp(prefix="{}-{}-{}-".format(self.run_id, self.attempt, self.repo), dir=releases))
        release.chmod(0o700)
        revisions = {}
        for name, settings in REPOS.items():
            destination = release / settings["directory"]
            branch = self.branch if name == self.repo else settings["branch"]
            self.command(["git", "clone", "--quiet", "--depth", "1", "--branch", branch,
                          self.repo_url(name), str(destination)])
            if name == self.repo:
                self.command(["git", "fetch", "--quiet", "--depth", "1", "origin", self.sha], cwd=destination)
                self.command(["git", "checkout", "--quiet", "--detach", self.sha], cwd=destination)
            revisions[name] = self.command(["git", "rev-parse", "HEAD"], cwd=destination, capture=True).strip()
            make_build_context_readable(destination)
        for name in CONFIG_COMPONENTS:
            source = self.home / "config" / (name + ".env")
            if not source.is_file():
                raise PipelineError("Required host configuration missing: {}.env".format(name))
            destination = release / name / ".env"
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
        for name in ("compose.yaml", "compose.gateway.yaml", "compose.core.yaml", "compose.edge.yaml"):
            # The core and edge overrides are newer than the others; an env revision without them does not reference them.
            if name in ("compose.core.yaml", "compose.edge.yaml") and not (release / "env" / name).is_file():
                continue
            shutil.copyfile(release / "env" / name, release / name)
        files = [str(release / "compose.yaml")]
        config = json.loads(self.command(self.compose(files) + ["config", "--format", "json"], capture=True))
        files, config = self.resolve_gpu(release, files, config)
        files, config = self.resolve_bind(release, files, config)
        services = {name: service for name, service in config.get("services", {}).items() if "build" in service}
        if not services:
            raise PipelineError("Compose contains no buildable services")
        images = {"services": {}}
        tag = release.name.lower().replace("_", "-")
        for name in services:
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
                raise PipelineError("Unsupported Compose service name")
            images["services"][name] = {"image": "dpl-ci-{}:{}".format(name.lower().replace("_", "-"), tag)}
        if "web" in services:
            images["services"]["web"]["healthcheck"] = {
                "test": ["CMD", "wget", "-q", "--spider", "http://127.0.0.1:8080/"],
                "interval": "10s", "timeout": "5s", "retries": 6, "start_period": "10s",
            }
        write_json(release / "images.json", images)
        write_json(release / "revisions.json", revisions)
        files.append(str(release / "images.json"))
        return release, files

    def run_checks(self, release):
        image, script = CHECKS[self.repo]
        source = release / REPOS[self.repo]["directory"]
        self.stage = "lint/test"
        print("Running lint and tests of {} in {}".format(self.repo, image), flush=True)
        # Work on a copy without the host .env, so tests never see production settings or secrets.
        self.command(["docker", "run", "--rm", "--pull", "missing",
                      "--volume", "{}:/src:ro".format(source), "--volume", CHECK_CACHE_VOLUME + ":/cache",
                      "--env", "UV_CACHE_DIR=/cache/uv", "--env", "npm_config_cache=/cache/npm",
                      "--env", "UV_LINK_MODE=copy", "--env", "CI=1", image, "sh", "-c",
                      "mkdir -p /work && cp -a /src/. /work && rm -f /work/.env && cd /work && " + script])

    def prune_build_cache(self):
        """Drop old BuildKit cache only (never images, containers or volumes); failure is harmless."""
        try:
            self.command(["docker", "builder", "prune", "--force", "--filter", "until=" + BUILD_CACHE_MAX_AGE])
        except PipelineError:
            print("::warning::Build cache cleanup failed; deployment result is unchanged", flush=True)

    def read_subject(self, release):
        try:
            subject = self.command(["git", "log", "-1", "--format=%s"],
                                   cwd=release / REPOS[self.repo]["directory"], capture=True).strip()
        except PipelineError:
            return
        self.subject = subject.splitlines()[0][:120] if subject else ""

    def message(self, ok, detail=""):
        if ok:
            head = "✅ {} build thành công".format(self.repo) + (
                ", đã deploy lên CT104" if self.deploy else " (nhánh {}, không deploy)".format(self.branch[:80]))
        else:
            head = "❌ {} thất bại ở bước {}".format(self.repo, self.stage)
        lines = [head]
        if detail:
            lines.append(detail)
        lines += ["👤 Push bởi: {}".format(self.actor),
                  "📝 Commit: {}{}".format(self.sha[:7], " – " + self.subject if self.subject else ""),
                  "🔗 {}".format(self.run_url)]
        return "\n".join(lines)

    def notify(self, text):
        path = self.home / "config" / "zalo.json"
        if not path.is_file():
            print("::warning::Zalo notification is not configured", flush=True)
            return
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            send_message(config["token"], config["chat_id"], text)
        except (ZaloError, OSError, ValueError, KeyError, TypeError):
            print("::warning::Zalo notification failed; credentials and response details omitted", flush=True)

    def safe_notify(self, text):
        try:
            self.notify(text)
        except Exception:
            print("::warning::Notification failed; deployment result is unchanged", flush=True)

    def execute(self):
        self.state.mkdir(parents=True, exist_ok=True)
        with host_lock(self.state / "deploy.lock"):
            rollback = ""
            try:
                # Only the final result is announced; superseded commits are skipped silently.
                if self.deploy and not self.is_current():
                    print("Skipped: a newer default-branch commit already exists", flush=True)
                    return "skipped"
                release, files = self.prepare_release()
                self.read_subject(release)
                self.run_checks(release)
                self.stage = "build"
                print("Building all Docker images in sequence", flush=True)
                self.command(self.compose(files) + ["build"], cwd=release)
                if not self.deploy:
                    self.safe_notify(self.message(True))
                    return "built"
                if not self.is_current():
                    print("Skipped: a newer commit arrived during the build", flush=True)
                    return "skipped"
                self.stage = "deploy"
                current_file = self.state / "current.json"
                previous = json.loads(current_file.read_text(encoding="utf-8")) if current_file.is_file() else None
                print("Updating services and waiting for readiness", flush=True)
                try:
                    self.command(self.compose(files) + ["up", "--detach", "--no-build", "--wait", "--wait-timeout", "300"], cwd=release)
                except Exception:
                    if previous and previous.get("files"):
                        try:
                            self.command(self.compose(previous["files"]) + ["up", "--detach", "--no-build", "--wait", "--wait-timeout", "300"])
                            rollback = "↩️ Đã khôi phục bản đang chạy trước đó"
                        except Exception:
                            rollback = "⚠️ Khôi phục bản trước cũng thất bại, cần kiểm tra CT104"
                    else:
                        rollback = "⚠️ Chưa có bản trước để khôi phục"
                    raise
                if previous:
                    write_json(self.state / "previous.json", previous)
                write_json(current_file, {"files": files, "release": str(release), "repository": self.repo, "sha": self.sha, "run_url": self.run_url})
                self.safe_notify(self.message(True))
                self.prune_build_cache()
                return "deployed"
            except Exception:
                self.safe_notify(self.message(False, rollback))
                raise


def main():
    os.umask(0o077)
    try:
        result = Pipeline().execute()
        print("DPL CI/CD result: " + result, flush=True)
        return 0
    except PipelineError as exc:
        print("::error::" + str(exc), file=sys.stderr)
    except Exception:
        print("::error::CI/CD failed; inspect the failed step. Secret-bearing exception details omitted.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
