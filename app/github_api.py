from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from .config import MAX_ACTION_JOB_PAGES, MAX_RUN_HISTORY


@dataclass
class GitHubApiError(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


class GitHubReadClient:
    """Small allowlisted GitHub REST client. This class deliberately implements GET only."""

    def __init__(self, token: str, timeout: float = 15.0):
        if not token:
            raise GitHubApiError(503, "GITHUB_READ_TOKEN is not configured")
        self._token = token
        # Fixed destination prevents a misconfigured environment variable from
        # sending the GitHub credential to an arbitrary host.
        self._base_url = "https://api.github.com"
        self._timeout = timeout
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def get_json(self, path: str, params: dict[str, Any] | None = None, *, allow_404: bool = False) -> Any:
        # path is authored internally; callers never supply arbitrary URLs.
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "precision-lab-readonly-api/0.1",
        }
        try:
            response = self._http.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise GitHubApiError(502, "GitHub request timed out") from exc
        except httpx.HTTPError as exc:
            raise GitHubApiError(502, "Could not connect to GitHub") from exc

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code == 401:
            raise GitHubApiError(503, "GitHub read credential was rejected")
        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining == "0":
                raise GitHubApiError(503, "GitHub API rate limit reached")
            raise GitHubApiError(503, "GitHub read credential lacks permission or the request is forbidden")
        if response.status_code == 429:
            raise GitHubApiError(503, "GitHub API rate limit reached")
        if response.status_code >= 500:
            raise GitHubApiError(502, "GitHub API is temporarily unavailable")
        if response.status_code >= 400:
            raise GitHubApiError(502, f"GitHub API returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubApiError(502, "GitHub returned an invalid JSON response") from exc

    @staticmethod
    def _repo_path(repository: str, subpath: str = "") -> str:
        # Repo names come only from config.REPOSITORIES. Quote path segments but preserve separators.
        repo = quote(repository, safe="/")
        tail = quote(subpath, safe="/")
        return f"repos/{repo}/{tail}" if tail else f"repos/{repo}"

    def get_repo(self, repository: str) -> dict[str, Any]:
        return self.get_json(self._repo_path(repository))

    def get_content_text(self, repository: str, path: str, ref: str) -> tuple[str, str]:
        data = self.get_json(
            self._repo_path(repository, f"contents/{quote(path, safe='/')}"),
            params={"ref": ref},
        )
        if not isinstance(data, dict) or data.get("type") != "file":
            raise GitHubApiError(502, f"Expected a text file at {path}")
        encoded = data.get("content")
        if not isinstance(encoded, str):
            # Contents API may omit content for oversized files; these project files should be small.
            raise GitHubApiError(502, f"GitHub did not return file content for {path}")
        try:
            content = base64.b64decode(encoded, validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubApiError(502, f"GitHub file {path} was not UTF-8 text") from exc
        return content, str(data.get("sha", ""))

    def get_content_json(self, repository: str, path: str, ref: str, *, allow_404: bool = False) -> dict[str, Any] | None:
        return self.get_json(
            self._repo_path(repository, f"contents/{quote(path, safe='/')}"),
            params={"ref": ref},
            allow_404=allow_404,
        )

    def list_directory(self, repository: str, path: str, ref: str) -> list[dict[str, Any]]:
        data = self.get_json(
            self._repo_path(repository, f"contents/{quote(path, safe='/')}"),
            params={"ref": ref},
            allow_404=True,
        )
        if data is None:
            return []
        if not isinstance(data, list):
            raise GitHubApiError(502, f"Expected a directory listing for {path}")
        return data

    def get_ref_sha(self, repository: str, branch: str) -> str:
        data = self.get_json(
            self._repo_path(repository, f"git/ref/heads/{quote(branch, safe='/')}"),
            allow_404=True,
        )
        if data is None:
            raise GitHubApiError(404, f"Branch {branch} was not found")
        sha = data.get("object", {}).get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            raise GitHubApiError(502, "GitHub returned an invalid branch HEAD SHA")
        return sha

    def get_commit_tree(self, repository: str, commit_sha: str) -> dict[str, list[str]]:
        """Return the full leaf tree in continuity.py's exact path->[mode,type,oid] shape."""
        commit = self.get_json(self._repo_path(repository, f"git/commits/{quote(commit_sha, safe='')}"), allow_404=True)
        if commit is None:
            raise ValueError(f"Commit object {commit_sha} is unavailable")
        tree_sha = commit.get("tree", {}).get("sha")
        if not isinstance(tree_sha, str) or len(tree_sha) != 40:
            raise ValueError(f"Commit object {commit_sha} has no valid tree SHA")
        tree_data = self.get_json(
            self._repo_path(repository, f"git/trees/{quote(tree_sha, safe='')}"),
            params={"recursive": "1"},
        )
        if tree_data.get("truncated") is not False:
            raise ValueError(f"Recursive tree for {commit_sha} is truncated or completeness was not reported")
        entries: dict[str, list[str]] = {}
        for item in tree_data.get("tree", []):
            if item.get("type") == "tree":
                continue
            path = item.get("path")
            mode = item.get("mode")
            kind = item.get("type")
            sha = item.get("sha")
            if all(isinstance(v, str) and v for v in (path, mode, kind, sha)):
                entries[path] = [mode, kind, sha]
        return entries

    def get_workflow_runs(self, repository: str, workflow_path: str, branch: str) -> tuple[list[dict[str, Any]], bool]:
        workflow_file = quote(workflow_path.rsplit("/", 1)[-1], safe="")
        data = self.get_json(
            self._repo_path(repository, f"actions/workflows/{workflow_file}/runs"),
            params={"branch": branch, "per_page": 100, "page": 1},
        )
        runs = data.get("workflow_runs", [])
        if not isinstance(runs, list):
            raise GitHubApiError(502, "GitHub workflow-runs response was incomplete")
        # A full page means history is bounded and not proven exhaustive.
        filtered = [r for r in runs if r.get("path", "").split("@", 1)[0] == workflow_path]
        filtered.sort(key=lambda r: (r.get("created_at", ""), r.get("run_attempt", 1), int(r.get("id", 0))), reverse=True)
        # Only a bounded tail is inspected; incomplete coverage is explicitly reported.
        complete = len(runs) < 100 and len(filtered) <= MAX_RUN_HISTORY
        return filtered[:MAX_RUN_HISTORY], complete

    def get_run(self, repository: str, run_id: int) -> dict[str, Any] | None:
        return self.get_json(self._repo_path(repository, f"actions/runs/{run_id}"), allow_404=True)

    def get_run_jobs(self, repository: str, run_id: int, attempt: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for page in range(1, MAX_ACTION_JOB_PAGES + 1):
            data = self.get_json(
                self._repo_path(repository, f"actions/runs/{run_id}/attempts/{attempt}/jobs"),
                params={"per_page": 100, "page": page},
            )
            jobs = data.get("jobs", [])
            if not isinstance(jobs, list):
                raise GitHubApiError(502, "GitHub jobs response was incomplete")
            results.extend(jobs)
            if len(results) >= int(data.get("total_count", len(results))) or len(jobs) < 100:
                return results
        raise GitHubApiError(502, "GitHub jobs pagination exceeded the safe limit")
