from __future__ import annotations

from urllib.parse import urlsplit

import httpx

from .config import Settings


class BridgeApiError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def get_bridge_run(run_id: str, settings: Settings) -> dict:
    """Fetch one Bridge-owned run using Render's lowercase llm secret.

    This client is intentionally GET-only. It never calls a dispatch or mutation route.
    """
    if not settings.bridge_api_key:
        raise BridgeApiError(503, "Bridge API credential is not configured")

    parsed = urlsplit(settings.bridge_base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise BridgeApiError(503, "Bridge base URL must be a plain HTTPS origin")

    url = settings.bridge_base_url.rstrip("/") + "/api/runs/" + run_id
    headers = {
        "Accept": "application/json",
        "X-Bridge-Key": settings.bridge_api_key,
        "User-Agent": "precision-lab-readonly-api/0.2",
    }

    try:
        with httpx.Client(
            timeout=settings.bridge_timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise BridgeApiError(502, "Bridge request timed out") from exc
    except httpx.HTTPError as exc:
        raise BridgeApiError(502, "Could not connect to Bridge") from exc

    if response.status_code in (401, 403):
        raise BridgeApiError(503, "Bridge rejected the configured API credential")
    if response.status_code == 404:
        raise BridgeApiError(404, "Bridge run was not found for this key")
    if response.status_code == 429 or response.status_code >= 500:
        raise BridgeApiError(502, "Bridge is temporarily unavailable")
    if response.status_code != 200:
        raise BridgeApiError(502, "Bridge returned an unexpected HTTP status")

    try:
        payload = response.json()
    except ValueError as exc:
        raise BridgeApiError(502, "Bridge returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BridgeApiError(502, "Bridge returned an unexpected response shape")
    return payload



def dispatch_bridge_run(payload: dict, settings: Settings) -> tuple[int, dict]:
    """Dispatch through Bridge using a dedicated dispatch-scoped credential.

    A timeout or upstream 5xx is treated as an uncertain outcome: callers must
    reconcile the same idempotency key before attempting any retry.
    """
    if not settings.bridge_dispatch_api_key:
        raise BridgeApiError(503, "Bridge dispatch credential is not configured")

    parsed = urlsplit(settings.bridge_base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise BridgeApiError(503, "Bridge base URL must be a plain HTTPS origin")

    url = settings.bridge_base_url.rstrip("/") + "/api/runs"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Bridge-Key": settings.bridge_dispatch_api_key,
        "Idempotency-Key": payload["idempotency_key"],
        "User-Agent": "precision-lab-readonly-api/0.3",
    }
    try:
        with httpx.Client(timeout=settings.bridge_timeout_seconds, follow_redirects=False) as client:
            response = client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise BridgeApiError(409, "DISPATCH_OUTCOME_UNKNOWN; reconcile this idempotency key before retrying") from exc
    except httpx.HTTPError as exc:
        raise BridgeApiError(409, "DISPATCH_OUTCOME_UNKNOWN; reconcile this idempotency key before retrying") from exc

    if response.status_code in (401, 403):
        raise BridgeApiError(503, "Bridge rejected the dispatch-scoped credential")
    if response.status_code >= 500:
        raise BridgeApiError(409, "DISPATCH_OUTCOME_UNKNOWN; reconcile this idempotency key before retrying")
    try:
        body = response.json()
    except ValueError as exc:
        raise BridgeApiError(502, "Bridge returned invalid dispatch JSON") from exc
    if not isinstance(body, dict):
        raise BridgeApiError(502, "Bridge returned an unexpected dispatch response shape")
    allowed = {
        "id", "idempotency_id", "state", "reused", "target_repo", "gate",
        "dry_run", "status_url", "error", "required_scope",
    }
    safe_body = {key: value for key, value in body.items() if key in allowed}
    if response.status_code not in (200, 201, 202, 400, 409):
        raise BridgeApiError(502, "Bridge returned an unexpected dispatch HTTP status")
    return response.status_code, safe_body
