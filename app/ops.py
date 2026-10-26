"""Authenticated, allowlisted dispatch to the certification runner.

The router is intentionally separate from the public read-only API key.
"""
from __future__ import annotations

import hmac
import os
import re
from typing import Literal

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/v1/ops", tags=["Operations"])
REPOS = {"lite": "c2melendez/precision-lab-lite", "plus": "c2melendez/precision-lab-plus"}
WORKFLOW = ".github/workflows/certification-runner.yml"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: Literal["lite", "plus"]
    operation: Literal["gate", "docs"]
    gate: Literal["cumulative", "h1d", "security"] = "cumulative"
    dry_run: bool = True
    docs_patch_url: str | None = Field(default=None, max_length=2048)
    docs_patch_sha256: str | None = None
    docs_expected_base_sha: str | None = None
    docs_patch_hosts: str | None = Field(default=None, max_length=512)


def _auth(key: str | None) -> None:
    expected = os.getenv("OPS_API_KEY", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Operations dispatch is not configured")
    if not key or not hmac.compare_digest(key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid operations key")


def _validate(body: DispatchRequest) -> None:
    if body.operation == "docs":
        if body.docs_patch_sha256 and not SHA256.fullmatch(body.docs_patch_sha256):
            raise HTTPException(status_code=422, detail="Invalid patch SHA-256")
        if body.docs_expected_base_sha and not SHA40.fullmatch(body.docs_expected_base_sha):
            raise HTTPException(status_code=422, detail="Invalid expected base commit SHA")
        if body.docs_patch_url and not body.docs_patch_url.startswith("https://"):
            raise HTTPException(status_code=422, detail="Patch URL must use HTTPS")
        if not body.dry_run and not all((body.docs_patch_url, body.docs_patch_sha256, body.docs_expected_base_sha)):
            raise HTTPException(status_code=422, detail="Publishing docs requires URL, SHA-256 and expected base SHA")
    elif any((body.docs_patch_url, body.docs_patch_sha256, body.docs_expected_base_sha, body.docs_patch_hosts)):
        raise HTTPException(status_code=422, detail="Patch fields are only valid for docs operation")


@router.post("/dispatch", status_code=202)
async def dispatch(
    body: DispatchRequest,
    x_ops_key: str | None = Header(default=None, alias="X-Ops-Key"),
):
    _auth(x_ops_key)
    _validate(body)
    # Fail closed until durable idempotency and deployment checks are complete.
    if os.getenv("OPS_DISPATCH_ENABLED", "").strip().lower() != "true":
        raise HTTPException(status_code=503, detail="Runner dispatch is disabled pending idempotency and rollout validation")
    token = os.getenv("RUNNER_DISPATCH_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="Runner dispatch credential is not configured")
    repo = REPOS[body.repo]
    inputs = {
        "operation": body.operation,
        "target_repo": body.repo,
        "gate": body.gate,
        "dry_run": str(body.dry_run).lower(),
        "docs_patch_url": body.docs_patch_url or "",
        "docs_patch_sha256": body.docs_patch_sha256 or "",
        "docs_expected_base_sha": body.docs_expected_base_sha or "",
        "docs_patch_hosts": body.docs_patch_hosts or "",
    }
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW}/dispatches"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": "main", "inputs": inputs},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="GitHub Actions dispatch request failed") from exc
    if response.status_code == 204:
        return {
            "status": "dispatch_accepted",
            "repo": body.repo,
            "operation": body.operation,
            "dry_run": body.dry_run,
            "workflow": WORKFLOW,
            "completed": False,
            "certification_pass": False,
        }
    if response.status_code in (401, 403, 404):
        raise HTTPException(status_code=502, detail=f"GitHub rejected dispatch request (HTTP {response.status_code})")
    raise HTTPException(status_code=502, detail=f"GitHub dispatch API returned HTTP {response.status_code}")
