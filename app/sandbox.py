"""Docker-based verification sandbox.

The container clones the repo at the failing commit, applies the generated
patch, and re-runs ONLY the originally failing command. No host filesystem is
mounted and no host secrets are passed in beyond the short-lived installation
token, which is injected as an environment variable.
"""

from __future__ import annotations

import io
import logging
import tarfile
import time

import docker
from docker.errors import DockerException, ImageNotFound

from app.models import Diagnosis, FailureContext, VerificationResult
from app.patcher import PatchValidationError

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "python:3.12-slim"
WORKDIR = "/workspace"
REPO_DIR = f"{WORKDIR}/repo"
PATCH_PATH = f"{WORKDIR}/patch.diff"
OUTPUT_TAIL_CHARS = 3_000


def _tar_file(name: str, content: str) -> bytes:
    """Build a tar archive containing a single file."""
    buf = io.BytesIO()
    data = content.encode("utf-8")
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Sandbox:
    """Runs one verification attempt inside an ephemeral container."""

    def __init__(
        self,
        timeout: int = 180,
        image: str = DEFAULT_IMAGE,
        mem_limit: str = "1g",
        nano_cpus: int = 1_000_000_000,
        enabled: bool = True,
        client: docker.DockerClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.image = image
        self.mem_limit = mem_limit
        self.nano_cpus = nano_cpus
        self.enabled = enabled
        self._client = client

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _ensure_image(self) -> None:
        try:
            self.client.images.get(self.image)
        except ImageNotFound:
            logger.info("Pulling sandbox image %s", self.image)
            self.client.images.pull(self.image)

    @staticmethod
    def _exec(container, command: str, workdir: str = REPO_DIR) -> tuple[int, str]:
        """Run a shell command and return (exit_code, combined_output)."""
        result = container.exec_run(["/bin/bash", "-lc", command], workdir=workdir)
        output = result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        return result.exit_code, output or ""

    def run(
        self,
        context: FailureContext,
        diagnosis: Diagnosis,
        installation_token: str = "",
    ) -> VerificationResult:
        """Apply the patch and re-run the failing command. Never raises."""
        command = context.failing_command
        if not command:
            return VerificationResult(
                passed=False,
                command="",
                error="no failing command could be extracted from the logs",
            )
        if not self.enabled:
            return VerificationResult(
                passed=False, command=command, error="sandbox disabled by configuration"
            )

        if installation_token:
            # Short-lived installation token, injected via env (never mounted).
            repo_url = (
                f"https://x-access-token:${{GIT_TOKEN}}@github.com/{context.run.repo_full_name}.git"
            )
        else:
            repo_url = f"https://github.com/{context.run.repo_full_name}.git"
        started = time.monotonic()
        container = None
        try:
            self._ensure_image()
            container = self.client.containers.create(
                self.image,
                command=["sleep", str(self.timeout + 180)],
                working_dir=WORKDIR,
                mem_limit=self.mem_limit,
                nano_cpus=self.nano_cpus,
                environment={"GIT_TOKEN": installation_token},
                labels={"fixci": "sandbox"},
                auto_remove=False,
            )
            container.start()

            # 1. Install git (slim image lacks it) and clone at the failing commit.
            # workdir must exist before exec; REPO_DIR is created by the clone.
            code, out = self._exec(
                container,
                "apt-get update -qq && apt-get install -y -qq --no-install-recommends git "
                "ca-certificates >/dev/null 2>&1 && "
                f"git clone --quiet {repo_url} {REPO_DIR} && "
                f"cd {REPO_DIR} && git checkout --quiet {context.run.head_sha}",
                workdir=WORKDIR,
            )
            if code != 0:
                return self._fail(command, started, f"clone failed:\n{out}")

            # 2. Install dependencies (network enabled).
            self._exec(container, "if [ -f requirements.txt ]; then pip install -q -r requirements.txt; fi")
            self._exec(
                container,
                "if [ -f pyproject.toml ] && [ ! -f requirements.txt ]; then pip install -q -e .; fi",
            )

            # 3. Apply the patch. `git apply --check` first, exactly like patcher.py.
            container.put_archive(WORKDIR, _tar_file("patch.diff", diagnosis.patch))
            code, out = self._exec(
                container, f"git apply --check {PATCH_PATH} && git apply {PATCH_PATH}"
            )
            if code != 0:
                return self._fail(command, started, f"patch did not apply:\n{out}")

            # 4. Best-effort network isolation for the verification step.
            self._isolate(container)

            # 5. Re-run ONLY the failing command, hard-capped by `timeout`.
            code, out = self._exec(container, f"timeout {self.timeout} bash -lc {_q(command)}")
            passed = code == 0
            output_tail = out[-OUTPUT_TAIL_CHARS:]

            patched_files = self._collect_patched_files(container)
            return VerificationResult(
                passed=passed,
                command=command,
                output_tail=output_tail,
                duration_s=round(time.monotonic() - started, 2),
                patched_files=patched_files,
            )
        except PatchValidationError as exc:
            return self._fail(command, started, str(exc))
        except DockerException as exc:
            logger.exception("Sandbox docker error")
            return self._fail(command, started, f"docker error: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Sandbox unexpected error")
            return self._fail(command, started, f"unexpected sandbox error: {exc}")
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:  # pragma: no cover - best effort
                    pass

    # -- helpers ------------------------------------------------------------

    def _isolate(self, container) -> None:
        """Try to disconnect the container from the default bridge network."""
        try:
            self.client.networks.get("bridge").disconnect(container)
        except DockerException:  # pragma: no cover - not always supported
            logger.debug("Could not isolate network for verification step")

    def _collect_patched_files(self, container) -> dict[str, str]:
        """Read back the working-tree contents of files the patch changed."""
        # `git add -N` marks new files as intent-to-add so they show up in diff.
        code, out = self._exec(container, "git add -N . >/dev/null 2>&1; git diff --name-only")
        if code != 0:
            return {}
        files: dict[str, str] = {}
        for path in out.splitlines():
            path = path.strip()
            if not _is_safe_relpath(path):
                continue
            code, content = self._exec(container, f"cat -- {_q(path)}")
            if code == 0:
                files[path] = content
        return files

    @staticmethod
    def _fail(command: str, started: float, error: str) -> VerificationResult:
        return VerificationResult(
            passed=False,
            command=command,
            output_tail=error[:OUTPUT_TAIL_CHARS],
            duration_s=round(time.monotonic() - started, 2),
            error=error,
        )


def _q(value: str) -> str:
    """Single-quote a value for safe interpolation into a shell command."""
    return "'" + value.replace("'", "'\\''") + "'"


def _is_safe_relpath(path: str) -> bool:
    """True for a non-empty repo-relative path with no traversal."""
    return bool(path) and not path.startswith("/") and ".." not in path.split("/")
