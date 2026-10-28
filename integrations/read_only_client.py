"""Vendor-neutral read-only client and function schemas for Precision Lab.

This module is deliberately a small HTTP client, not an LLM SDK integration.
OpenAI-compatible, Gemini, Qwen, DeepSeek, Grok, or custom agent hosts can expose
its function schemas to a model and use execute_readonly_tool() as the executor.
The host application, not the model, performs the HTTPS GET request.
"""
from __future__ import annotations

import os
import re
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

DEFAULT_API_BASE_URL = "https://precision-lab-readonly-api.onrender.com"
RepoAlias = Literal["lite", "plus", "both"]
SingleRepoAlias = Literal["lite", "plus"]
_GATE_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_RUN_ID_RE = re.compile(r"^[0-9]{1,20}$")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "precision_lab_status",
        "description": (
            "Read the current certification continuity status of Precision Lab Lite, Plus, "
            "or both. This tool is read-only and never starts a workflow."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "enum": ["lite", "plus", "both"],
                    "description": "Repository alias to inspect; use both unless a single repo is requested.",
                }
            },
            "required": ["repo"],
            "additionalProperties": False,
        },
    },
    {
        "name": "precision_lab_gate_plan",
        "description": (
            "Read the advisory plan for one known gate in Lite or Plus. RUN is a recommendation only; "
            "action_taken is always false. This tool never dispatches GitHub Actions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "enum": ["lite", "plus"]},
                "gate": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9._-]{1,100}$",
                    "description": "Exact gate id obtained from current authenticated status or trusted repo evidence.",
                },
                "refresh": {
                    "type": "boolean",
                    "default": True,
                    "description": "Compatibility parameter; the API currently always reads fresh source data.",
                },
            },
            "required": ["repo", "gate"],
            "additionalProperties": False,
        },
    },
    {
        "name": "precision_lab_run_evidence",
        "description": (
            "Read raw GitHub Actions evidence for a numeric run id in Lite or Plus. "
            "A successful run is not by itself certification PASS."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "enum": ["lite", "plus"]},
                "run_id": {
                    "type": "string",
                    "pattern": "^[0-9]{1,20}$",
                    "description": "Exact numeric GitHub Actions run id.",
                },
            },
            "required": ["repo", "run_id"],
            "additionalProperties": False,
        },
    },
]


class ReadOnlyApiClient:
    """Minimal HTTPS client. It can issue GET requests only."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_API_BASE_URL,
        api_key: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url.strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("API base URL must be an absolute HTTPS URL.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API base URL must not contain credentials, a query, or a fragment.")
        if parsed.path not in ("", "/"):
            raise ValueError("API base URL must be the service origin, without a path prefix.")
        try:
            port = parsed.port
        except ValueError:
            raise ValueError("API base URL contains an invalid port.") from None
        if port not in (None, 443):
            raise ValueError("API base URL must use the default HTTPS port.")
        trusted_host = (parsed.hostname or "").lower() == "precision-lab-readonly-api.onrender.com"
        custom_host_opt_in = os.getenv("PRECISION_LAB_ALLOW_CUSTOM_BASE_URL", "").lower() == "true"
        if not trusted_host and not custom_host_opt_in:
            raise ValueError(
                "API host is not allowlisted. Custom HTTPS hosts require explicit operator opt-in "
                "via PRECISION_LAB_ALLOW_CUSTOM_BASE_URL=true."
            )
        key = api_key.strip()
        if not key:
            raise ValueError("API key is missing. Set SERVICE_API_KEY in the tool host environment.")
        self.base_url = f"https://{parsed.netloc}"
        self._api_key = key
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": key, "Accept": "application/json"},
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=False,
        )

    @classmethod
    def from_environment(cls, *, transport: httpx.BaseTransport | None = None) -> "ReadOnlyApiClient":
        key = os.getenv("SERVICE_API_KEY", "").strip()
        base_url = os.getenv("PRECISION_LAB_API_BASE_URL", DEFAULT_API_BASE_URL).strip()
        return cls(base_url=base_url, api_key=key, transport=transport)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "ReadOnlyApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            # Exception text may contain request metadata. Report the class only.
            raise RuntimeError(f"Read-only API request failed before receiving a response ({type(exc).__name__}).") from None

        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"Read-only API returned non-JSON content (HTTP {response.status_code}).") from None

        if response.status_code >= 400:
            code = payload.get("error", "http_error") if isinstance(payload, dict) else "http_error"
            if response.status_code == 401:
                raise RuntimeError("Read-only API returned HTTP 401: missing or invalid SERVICE_API_KEY in the tool host.")
            if response.status_code == 503:
                raise RuntimeError("Read-only API returned HTTP 503: service or upstream credentials may be unavailable.")
            raise RuntimeError(f"Read-only API returned HTTP {response.status_code} ({code}); response details omitted.")
        if not isinstance(payload, dict):
            raise RuntimeError("Read-only API returned JSON with an unexpected top-level type.")
        return payload

    def status(self, repo: RepoAlias = "both") -> dict[str, Any]:
        if repo not in ("lite", "plus", "both"):
            raise ValueError("repo must be lite, plus, or both.")
        return self._get_json("/v1/status", params={"repo": repo})

    def gate_plan(
        self,
        *,
        repo: SingleRepoAlias,
        gate: str,
        refresh: bool = True,
    ) -> dict[str, Any]:
        if repo not in ("lite", "plus"):
            raise ValueError("repo must be lite or plus.")
        if not _GATE_RE.fullmatch(gate):
            raise ValueError("gate must be a valid exact gate identifier.")
        return self._get_json(
            f"/v1/gates/{gate}/plan",
            params={"repo": repo, "refresh": str(refresh).lower()},
        )

    def run_evidence(self, *, repo: SingleRepoAlias, run_id: str) -> dict[str, Any]:
        if repo not in ("lite", "plus"):
            raise ValueError("repo must be lite or plus.")
        if not _RUN_ID_RE.fullmatch(str(run_id)):
            raise ValueError("run_id must contain decimal digits only.")
        return self._get_json(f"/v1/runs/{run_id}", params={"repo": repo})


def _portable_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep to the widely supported JSON Schema subset; executor validates omitted constraints."""
    portable = {"type", "description", "properties", "required", "enum", "items", "nullable", "format"}
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in portable:
            continue
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                name: _portable_tool_schema(prop)
                for name, prop in value.items()
                if isinstance(prop, dict)
            }
        elif key == "items" and isinstance(value, dict):
            result[key] = _portable_tool_schema(value)
        else:
            result[key] = value
    return result


def openai_compatible_tool_schemas() -> list[dict[str, Any]]:
    """Return conservative Chat Completions schemas for OpenAI-compatible endpoints.

    DeepSeek, Qwen, Meta Model API and xAI Chat Completions can use this nested
    shape when the chosen model/endpoint documents Chat Completions tool calling.
    Runtime validation remains strict in execute_readonly_tool().
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": _portable_tool_schema(tool["parameters"]),
            },
        }
        for tool in TOOL_SCHEMAS
    ]


def responses_api_tool_schemas() -> list[dict[str, Any]]:
    """Return flat function tools used by Responses-style APIs (including Meta/xAI formats)."""
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "parameters": _portable_tool_schema(tool["parameters"]),
        }
        for tool in TOOL_SCHEMAS
    ]


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove JSON-Schema keywords outside Gemini's portable function-schema subset."""
    portable = {"type", "description", "properties", "required", "enum", "items", "nullable", "format"}
    result: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in portable:
            continue
        if key == "properties" and isinstance(value, dict):
            result[key] = {
                name: _gemini_schema(prop)
                for name, prop in value.items()
                if isinstance(prop, dict)
            }
        elif key == "items" and isinstance(value, dict):
            result[key] = _gemini_schema(value)
        else:
            result[key] = value
    return result


def gemini_function_declarations() -> list[dict[str, Any]]:
    """Return Gemini function declarations using a conservative portable schema subset."""
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": _gemini_schema(tool["parameters"]),
        }
        for tool in TOOL_SCHEMAS
    ]


def execute_readonly_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute one allowlisted tool. No caller-controlled URL or HTTP method exists."""
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    allowed = {"precision_lab_status", "precision_lab_gate_plan", "precision_lab_run_evidence"}
    if name not in allowed:
        raise ValueError(f"Unknown read-only tool: {name}")
    with ReadOnlyApiClient.from_environment() as client:
        if name == "precision_lab_status":
            return client.status(arguments.get("repo", "both"))
        if name == "precision_lab_gate_plan":
            return client.gate_plan(
                repo=arguments.get("repo"),
                gate=arguments.get("gate", ""),
                refresh=arguments.get("refresh", True),
            )
        if name == "precision_lab_run_evidence":
            return client.run_evidence(
                repo=arguments.get("repo"),
                run_id=str(arguments.get("run_id", "")),
            )
