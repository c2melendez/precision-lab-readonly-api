from __future__ import annotations

import hmac
import os
from typing import Annotated, Iterator, Literal
from uuid import UUID

import yaml
from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.security import APIKeyHeader

from .bridge_api import BridgeApiError, dispatch_bridge_run, get_bridge_request, get_bridge_run
from .config import Settings
from .github_api import GitHubApiError, GitHubReadClient
from .models import BridgeDispatchRequest, ErrorResponse, GatePlanResponse, RunEvidenceResponse, StatusResponse
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
    summary="Read-only continuity, gate-planning, and GitHub Actions evidence for Precision Lab Lite and Plus.",
    description=(
        "Single LLM-facing control plane for Precision Lab. It observes certification state, "
        "plans permitted gates, and may request a scoped dispatch through the internal Bridge. "
        "It never calls GitHub dispatch directly. A GitHub Actions success is raw evidence and "
        "never an automatic certification PASS."
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
    response.headers["X-Read-Only"] = "true" if request.method == "GET" else "false"
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


@app.exception_handler(BridgeApiError)
async def bridge_error_handler(request: Request, exc: BridgeApiError):
    if request.url.path == "/v1/bridge/dispatch":
        uncertain = "DISPATCH_OUTCOME_UNKNOWN" in exc.message
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "bridge_upstream_error",
                "detail": exc.message,
                "action_taken": "unknown" if uncertain else False,
                "reconcile_required": uncertain,
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "bridge_upstream_error", "detail": exc.message, "read_only": True},
    )


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
    "/v1/bridge/runs/{run_id}",
    tags=["Bridge"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid Observador API key"},
        404: {"model": ErrorResponse, "description": "Bridge run not found or not owned by the configured Bridge key"},
        422: {"model": ErrorResponse, "description": "Invalid Bridge run UUID"},
        502: {"model": ErrorResponse, "description": "Bridge is unavailable or returned invalid data"},
        503: {"model": ErrorResponse, "description": "Bridge credential or URL is not configured, or rejected"},
    },
)
def bridge_run_status(run_id: UUID):
    """Read one Bridge run receipt; does not dispatch a workflow."""
    payload = get_bridge_run(str(run_id), Settings.from_env())
    return {"read_only": True, "bridge_run": payload}


@app.get(
    "/v1/bridge/requests/{idempotency_key}",
    tags=["Bridge"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid Observer API key"},
        404: {"model": ErrorResponse, "description": "Bridge request not found for this read-scoped key"},
        422: {"model": ErrorResponse, "description": "Invalid idempotency key"},
        502: {"model": ErrorResponse, "description": "Bridge receipt lookup unavailable"},
        503: {"model": ErrorResponse, "description": "Bridge read credential or URL is not configured, or rejected"},
    },
)
def bridge_request_status(
    idempotency_key: Annotated[str, Path(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")],
):
    """Read and reconcile a Bridge idempotency receipt without dispatching."""
    payload = get_bridge_request(idempotency_key, Settings.from_env())
    return {"read_only": True, "bridge_request": payload}


@app.post(
    "/v1/bridge/dispatch",
    tags=["Bridge"],
    dependencies=[Depends(require_api_key)],
    responses={
        401: {"model": ErrorResponse, "description": "Missing or invalid Observer API key"},
        409: {"description": "Stale plan or uncertain dispatch outcome; reconcile before retrying"},
        422: {"model": ErrorResponse, "description": "Invalid dispatch request"},
        503: {"description": "Bridge dispatch credential or upstream unavailable"},
    },
)
def bridge_dispatch(
    payload: BridgeDispatchRequest,
    client: GitHubReadClient = Depends(get_github),
):
    """Validate the live canonical plan, then ask the internal Bridge to dispatch."""
    try:
        plan = get_gate_plan(client, payload.target_repo, payload.gate)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc).strip("'")) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (StateMismatchError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Certification state requires reconciliation before dispatch") from exc

    if plan["state_status"] != "CURRENT":
        return JSONResponse(status_code=409, content={
            "decision": "RECONCILE",
            "action_taken": False,
            "state_status": plan["state_status"],
            "reconciliation_action": plan.get("reconciliation_action"),
        })
    if payload.branch != plan["canonical_branch"] or payload.requested_sha != plan["observed_head"]:
        raise HTTPException(status_code=409, detail="Requested branch or SHA is not the observed canonical head")
    gate_decision = plan["gate"]["decision"]
    if gate_decision != "RUN":
        status_code = 200 if gate_decision in ("WAIT", "REUSE") else 409
        return JSONResponse(status_code=status_code, content={
            "decision": gate_decision,
            "reason": plan["gate"]["reason"],
            "action_taken": False,
            "canonical_branch": plan["canonical_branch"],
            "observed_head": plan["observed_head"],
        })

    status_code, result = dispatch_bridge_run(payload.model_dump(), Settings.from_env())
    return JSONResponse(status_code=status_code, content=result)


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
