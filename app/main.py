from __future__ import annotations

import hmac
import os
from typing import Annotated, Iterator, Literal

import yaml
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.security import APIKeyHeader

from .config import Settings
from .github_api import GitHubApiError, GitHubReadClient
from .models import ErrorResponse, GatePlanResponse, RunEvidenceResponse, StatusResponse
from .ops import router as ops_router
from .service import (
    StateMismatchError,
    get_gate_plan,
    get_run_evidence,
    get_status,
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

app = FastAPI(
    title="Precision Lab Certification Read-only API",
    version="0.1.0",
    summary="Certification evidence plus separately authenticated, allowlisted runner dispatch.",
    description=(
        "Observation-only service for a GPT custom Action. No endpoint can dispatch, "
        "cancel, re-run, publish, push, or otherwise mutate certification resources. "
        "A GitHub Actions success is raw evidence and never an automatic certification PASS."
    ),
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)


@app.middleware("http")
async def read_only_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Read-Only"] = "false" if request.url.path.startswith("/v1/ops/") else "true"
    return response


def require_api_key(value: Annotated[str | None, Depends(api_key_header)]) -> None:
    expected = Settings.from_env().service_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="API authentication is not configured")
    if not value or not hmac.compare_digest(value, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")


def get_github() -> Iterator[GitHubReadClient]:
    settings = Settings.from_env()
    try:
        client = GitHubReadClient(
            token=settings.github_token,
            timeout=settings.request_timeout_seconds,
        )
    except GitHubApiError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    try:
        yield client
    finally:
        client.close()


@app.exception_handler(GitHubApiError)
async def github_error_handler(_request: Request, exc: GitHubApiError):
    status_code = exc.status_code if exc.status_code in (404, 502, 503) else 502
    body = {"error": "upstream_error", "detail": exc.message, "read_only": True}
    return JSONResponse(status_code=status_code, content=body)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException):
    if exc.status_code == 401:
        error = "unauthorized"
    elif exc.status_code == 404:
        error = "not_found"
    elif exc.status_code == 409:
        error = "state_reconciliation_required"
    elif exc.status_code == 503:
        error = "service_unavailable"
    else:
        error = "request_rejected"
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error, "detail": str(exc.detail), "read_only": True},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    # Return only field/error type, not user-supplied values.
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": "Invalid request parameters", "read_only": True},
    )


@app.exception_handler(StateMismatchError)
async def state_mismatch_handler(_request: Request, exc: StateMismatchError):
    return JSONResponse(
        status_code=409,
        content={"error": "state_reconciliation_required", "detail": str(exc), "read_only": True},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(_request: Request, exc: Exception):
    # Do not echo exceptions that could contain request context or credential fragments.
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "detail": "Unexpected internal error; no write action was attempted.", "read_only": True},
    )


@app.get("/healthz", tags=["Health"], include_in_schema=True)
def healthz():
    """Liveness only; intentionally does not call GitHub or disclose configuration."""
    return {"status": "ok", "service": "precision-lab-readonly-api", "read_only": True}


@app.get(
    "/v1/status",
    tags=["Status"],
    response_model=StatusResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        422: {"model": ErrorResponse, "description": "Invalid query parameters"},
        404: {"model": ErrorResponse, "description": "Repository or continuity resource not found"},
        409: {"model": ErrorResponse, "description": "Canonical branch pointers or continuity state need reconciliation"},
        502: {"model": ErrorResponse, "description": "GitHub returned incomplete or invalid data"},
        503: {"model": ErrorResponse, "description": "GitHub credential or API unavailable"},
    },
)
def status(
    repo: Literal["lite", "plus", "both"] = Query(default="both"),
    client: GitHubReadClient = Depends(get_github),
):
    """Read live repo pointers, continuity state, runner version and latest runner record."""
    return get_status(client, repo)


@app.get(
    "/v1/gates/{gate}/plan",
    tags=["Planning"],
    response_model=GatePlanResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        400: {"model": ErrorResponse, "description": "Unknown gate or policy-blocked gate"},
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        422: {"model": ErrorResponse, "description": "Invalid query or path parameters"},
        409: {"model": ErrorResponse, "description": "Canonical branch pointers or continuity state need reconciliation"},
        502: {"model": ErrorResponse, "description": "Incomplete source tree or GitHub response"},
        503: {"model": ErrorResponse, "description": "GitHub credential or API unavailable"},
    },
)
def gate_plan(
    gate: Annotated[str, Path(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")],
    repo: Literal["lite", "plus"] = Query(...),
    refresh: bool = Query(default=True, description="Accepted for compatibility; this implementation always reads fresh GitHub data."),
    client: GitHubReadClient = Depends(get_github),
):
    del refresh  # No cache is implemented; every request is fresh regardless of this flag.
    try:
        return get_gate_plan(client, repo, gate)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"Planner refused incomplete source evidence: {exc}") from exc


@app.get(
    "/v1/runs/{run_id}",
    tags=["Runs"],
    response_model=RunEvidenceResponse,
    dependencies=[Depends(require_api_key)],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid run ID"},
        401: {"model": ErrorResponse, "description": "Missing or invalid API key"},
        422: {"model": ErrorResponse, "description": "Invalid query or path parameters"},
        404: {"model": ErrorResponse, "description": "Run not found in the selected repository"},
        409: {"model": ErrorResponse, "description": "Canonical branch pointers need reconciliation"},
        502: {"model": ErrorResponse, "description": "GitHub returned incomplete or invalid data"},
        503: {"model": ErrorResponse, "description": "GitHub credential or API unavailable"},
    },
)
def run_evidence(
    run_id: Annotated[str, Path(min_length=1, max_length=20, pattern=r"^[0-9]+$")],
    repo: Literal["lite", "plus"] = Query(...),
    client: GitHubReadClient = Depends(get_github),
):
    try:
        return get_run_evidence(client, repo, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/openapi.yaml", include_in_schema=False)
def openapi_yaml():
    schema = app.openapi()
    return Response(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), media_type="application/yaml")


# Generated Action schema points at a placeholder until Render assigns a concrete host.
# Set PUBLIC_BASE_URL in Render after the service hostname is known.
def custom_openapi():
    from fastapi.openapi.utils import get_openapi

    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, summary=app.summary, description=app.description, routes=app.routes)
    base_url = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/") or "https://REPLACE-WITH-READONLY-SERVICE-HOST"
    schema["servers"] = [{"url": base_url, "description": "Replace the placeholder with the deployed Render service URL." if "REPLACE-WITH" in base_url else "Deployed read-only API"}]
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi

# Operational routes are isolated from the public read-only key and require X-Ops-Key.
app.include_router(ops_router)
