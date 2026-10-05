"""GitHub App authentication and GitHub REST interactions.

Auth uses PyGithub's ``GithubIntegration`` (App JWT -> installation token).
All REST calls then use the installation token via a requests session.
"""

from __future__ import annotations

import base64
import io
import logging
import zipfile
from typing import Any

import requests

from app.log_analyzer import (
    classify_failure,
    extract_failing_command,
    extract_referenced_files,
    trim_log,
)
from app.models import FailureContext, WorkflowRunRef

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
MAX_FILE_CHARS = 8_000
MAX_DIFF_CHARS = 20_000


class GithubClient:
    """Thin wrapper around the GitHub REST API for one installation/repo."""

    def __init__(
        self,
        app_id: int,
        private_key: str,
        installation_id: int,
        repo_full_name: str,
    ) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self.installation_id = installation_id
        self.repo_full_name = repo_full_name
        self._token: str | None = None
        self._session = requests.Session()

    # -- auth ---------------------------------------------------------------

    def installation_token(self) -> str:
        """Mint (and cache) an installation access token."""
        if self._token is None:
            from github import Auth, GithubIntegration  # imported lazily

            integration = GithubIntegration(auth=Auth.AppAuth(self.app_id, self.private_key))
            auth = integration.get_access_token(self.installation_id)
            self._token = auth.token
        return self._token

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.installation_token()}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, url: str, accept: str = "application/vnd.github+json") -> requests.Response:
        resp = self._session.get(url, headers=self._headers(accept), timeout=30)
        resp.raise_for_status()
        return resp

    # -- context collection -------------------------------------------------

    def fetch_failed_logs(self, run_id: int) -> str:
        """Download and concatenate the logs of the failed jobs in a run."""
        jobs_url = f"{GITHUB_API}/repos/{self.repo_full_name}/actions/runs/{run_id}/jobs"
        jobs = self._get(jobs_url).json().get("jobs", [])
        failed = [j for j in jobs if j.get("conclusion") == "failure"]
        if not failed:
            failed = jobs

        chunks: list[str] = []
        for job in failed:
            logs_url = job.get("logs_url")
            if not logs_url:
                continue
            resp = self._session.get(logs_url, headers=self._headers(), timeout=60)
            if resp.status_code != 200:
                logger.warning("Could not download logs for job %s: %s", job.get("id"), resp.status_code)
                continue
            chunks.append(self._unzip_logs(resp.content))
        if not chunks:
            # Fallback: whole-run log archive.
            resp = self._session.get(
                f"{GITHUB_API}/repos/{self.repo_full_name}/actions/runs/{run_id}/logs",
                headers=self._headers(),
                timeout=60,
            )
            if resp.status_code == 200:
                chunks.append(self._unzip_logs(resp.content))
        return "\n".join(chunks)

    @staticmethod
    def _unzip_logs(blob: bytes) -> str:
        """Extract the text of every .txt entry in a GitHub log archive."""
        out: list[str] = []
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                try:
                    out.append(f"===== {name} =====\n" + zf.read(name).decode("utf-8", "replace"))
                except Exception:  # pragma: no cover - defensive
                    continue
        return "\n".join(out)

    def get_commit_diff(self, sha: str) -> str:
        """Return the unified diff of a single commit."""
        resp = self._get(
            f"{GITHUB_API}/repos/{self.repo_full_name}/commits/{sha}",
            accept="application/vnd.github.v3.diff",
        )
        return resp.text[:MAX_DIFF_CHARS]

    def get_file(self, path: str, ref: str) -> str:
        """Return the raw text of a file at a ref, or '' if missing."""
        resp = self._session.get(
            f"{GITHUB_API}/repos/{self.repo_full_name}/contents/{path}",
            headers=self._headers("application/vnd.github.raw+json"),
            params={"ref": ref},
            timeout=30,
        )
        if resp.status_code != 200:
            return ""
        return resp.text[:MAX_FILE_CHARS]

    def get_workflow_yaml(self, path: str, ref: str) -> str:
        """Return the workflow YAML file contents at a ref."""
        return self.get_file(path, ref)

    def find_pr_for_branch(self, branch: str) -> int | None:
        """Return the open PR number whose head is ``branch``, if any."""
        owner = self.repo_full_name.split("/")[0]
        resp = self._get(
            f"{GITHUB_API}/repos/{self.repo_full_name}/pulls"
            f"?head={owner}:{branch}&state=all&per_page=1"
        )
        prs = resp.json()
        return prs[0]["number"] if prs else None

    def build_failure_context(
        self, run: WorkflowRunRef, workflow_path: str | None = None
    ) -> FailureContext:
        """Collect logs, diff, workflow YAML, and traceback files."""
        raw_logs = self.fetch_failed_logs(run.run_id)
        trimmed = trim_log(raw_logs)
        command = extract_failing_command(trimmed)
        classification = classify_failure(trimmed, command)

        diff = self.get_commit_diff(run.head_sha)
        workflow_yaml = self.get_workflow_yaml(workflow_path, run.head_sha) if workflow_path else ""

        files: dict[str, str] = {}
        if classification.value in ("test_failure", "lint"):
            for path in extract_referenced_files(trimmed):
                content = self.get_file(path, run.head_sha)
                if content:
                    files[path] = content

        return FailureContext(
            run=run,
            trimmed_log=trimmed,
            failure_type=classification,
            failing_command=command,
            diff=diff,
            workflow_yaml=workflow_yaml,
            files=files,
        )

    # -- write actions ------------------------------------------------------

    def post_comment(self, number: int, body: str) -> None:
        """Post a comment on an issue or PR."""
        resp = self._session.post(
            f"{GITHUB_API}/repos/{self.repo_full_name}/issues/{number}/comments",
            headers=self._headers(),
            json={"body": body},
            timeout=30,
        )
        resp.raise_for_status()

    def create_fix_pr(
        self,
        branch: str,
        base_branch: str,
        base_sha: str,
        title: str,
        body: str,
        patched_files: dict[str, str],
        commit_message: str,
    ) -> str:
        """Create a branch, commit verified file contents, and open a PR.

        Returns the HTML URL of the opened PR.
        """
        # 1. Branch off the verified base commit.
        resp = self._session.post(
            f"{GITHUB_API}/repos/{self.repo_full_name}/git/refs",
            headers=self._headers(),
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
            timeout=30,
        )
        resp.raise_for_status()

        # 2. Commit each changed file on the new branch.
        for path, content in patched_files.items():
            existing = self._session.get(
                f"{GITHUB_API}/repos/{self.repo_full_name}/contents/{path}",
                headers=self._headers(),
                params={"ref": branch},
                timeout=30,
            )
            payload: dict[str, Any] = {
                "message": commit_message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            if existing.status_code == 200:
                # Updating an existing file requires its current blob SHA.
                payload["sha"] = existing.json()["sha"]
            resp = self._session.put(
                f"{GITHUB_API}/repos/{self.repo_full_name}/contents/{path}",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()

        # 3. Open the pull request. Never auto-merge.
        resp = self._session.post(
            f"{GITHUB_API}/repos/{self.repo_full_name}/pulls",
            headers=self._headers(),
            json={"title": title, "head": branch, "base": base_branch, "body": body},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["html_url"]
