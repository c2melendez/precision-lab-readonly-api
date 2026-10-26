from __future__ import annotations

from typing import Any

import httpx

from .github_api import GitHubApiError


class GitHubWriteClient:
    """Restricted GitHub REST writer used only by authenticated operation routes.

    The hostname is fixed to api.github.com. Callers supply repository aliases
    resolved from configuration, never arbitrary repository names or URLs.
    """

    def __init__(self, token: str, timeout: float = 15.0):
        if not token:
            raise GitHubApiError(503, "GITHUB_WRITE_TOKEN is not configured")
        self._token = token
        self._base_url = "https://api.github.com"
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200, 201),
        allow_404: bool = False,
    ) -> Any:
        # path is composed internally from allowlisted repository constants,
        # validated numeric PR IDs and server-owned Git object IDs.
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "precision-lab-bridge-api/0.2",
        }
        try:
            response = self._http.request(method, url, params=params, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise GitHubApiError(502, "GitHub operation timed out") from exc
        except httpx.HTTPError as exc:
            raise GitHubApiError(502, "Could not connect to GitHub") from exc

        if response.status_code == 404 and allow_404:
            return None
        if response.status_code == 401:
            raise GitHubApiError(503, "GitHub write credential was rejected")
        if response.status_code == 403:
            if response.headers.get("X-RateLimit-Remaining") == "0":
                raise GitHubApiError(503, "GitHub API rate limit reached")
            raise GitHubApiError(503, "GitHub write credential lacks required repository permission")
        if response.status_code == 429:
            raise GitHubApiError(503, "GitHub API rate limit reached")
        if response.status_code >= 500:
            raise GitHubApiError(502, "GitHub API is temporarily unavailable")
        if response.status_code not in expected:
            status = 409 if response.status_code in (409, 422) else 502
            raise GitHubApiError(status, f"GitHub rejected the requested operation (HTTP {response.status_code})")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubApiError(502, "GitHub returned an invalid JSON response") from exc

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_404: bool = False,
    ) -> Any:
        return self.request_json("GET", path, params=params, expected=(200,), allow_404=allow_404)

    def post_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        expected: tuple[int, ...] = (201,),
    ) -> Any:
        return self.request_json("POST", path, body=body, expected=expected)

    def put_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        expected: tuple[int, ...] = (200, 201),
    ) -> Any:
        return self.request_json("PUT", path, body=body, expected=expected)

    def patch_json(
        self,
        path: str,
        body: dict[str, Any],
        *,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        return self.request_json("PATCH", path, body=body, expected=expected)

    def dispatch_workflow(self, repository: str, workflow_path: str, ref: str, inputs: dict[str, str]) -> None:
        workflow = workflow_path.rsplit("/", 1)[-1]
        self.request_json(
            "POST",
            f"repos/{repository}/actions/workflows/{workflow}/dispatches",
            body={"ref": ref, "inputs": inputs},
            expected=(204,),
        )
