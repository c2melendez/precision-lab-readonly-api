#!/usr/bin/env python3
"""Cross-platform, read-only live verifier for Precision Lab API.

Reads SERVICE_API_KEY from the process environment. Never prints request headers
or the key. All HTTP operations in this file are GET; no workflow is dispatched.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

DEFAULT_BASE_URL = "https://precision-lab-readonly-api.onrender.com"
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Base URL must be an absolute HTTPS origin.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL must not include credentials, query parameters, or a fragment.")
    if parsed.path not in ("", "/"):
        raise ValueError("Base URL must be the HTTPS origin only, without a path prefix.")
    trusted_host = (parsed.hostname or "").lower() == "precision-lab-readonly-api.onrender.com"
    custom_host_opt_in = os.getenv("PRECISION_LAB_ALLOW_CUSTOM_BASE_URL", "").lower() == "true"
    if not trusted_host and not custom_host_opt_in:
        raise ValueError(
            "API host is not allowlisted. Custom HTTPS hosts require explicit operator opt-in "
            "via PRECISION_LAB_ALLOW_CUSTOM_BASE_URL=true."
        )
    return f"https://{parsed.netloc}"


def safe_text(value: Any, api_key: str) -> str:
    text = str(value)
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return text


def request_json(
    client: httpx.Client,
    path: str,
    *,
    api_key: str = "",
    params: dict[str, Any] | None = None,
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    try:
        response = client.get(path, params=params)
    except httpx.HTTPError as exc:
        return None, None, f"Request failed before receiving an HTTP response ({type(exc).__name__})."
    try:
        body = response.json()
    except ValueError:
        return response.status_code, None, f"Response was not JSON (HTTP {response.status_code})."
    if not isinstance(body, dict):
        return response.status_code, None, f"JSON response has unexpected top-level type (HTTP {response.status_code})."
    return response.status_code, json.loads(safe_text(json.dumps(body), api_key)), None


def result(
    test_id: str,
    status: str,
    detail: str,
    *,
    http_status: int | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"id": test_id, "status": status, "detail": detail}
    if http_status is not None:
        record["http_status"] = http_status
    if evidence is not None:
        record["evidence"] = evidence
    return record


def run_verification(
    base_url: str,
    api_key: str,
    *,
    gate_lite: str | None = None,
    gate_plus: str | None = None,
    run_lite: str | None = None,
    run_plus: str | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    base = parse_base_url(base_url)

    # Public checks deliberately use a client without an API-key header.
    with httpx.Client(base_url=base, timeout=30, follow_redirects=False) as public:
        code, body, error = request_json(public, "/healthz")
        if error:
            records.append(result("T01", "BLOCKED" if code is None else "FAIL", error, http_status=code))
        elif code == 200 and body and body.get("status") == "ok" and body.get("read_only") is True:
            records.append(result("T01", "PASS", "Public liveness confirms the service is up and read-only.", http_status=code, evidence=body))
        else:
            records.append(result("T01", "FAIL", "Unexpected liveness response.", http_status=code, evidence=body))

        code, schema, error = request_json(public, "/openapi.json")
        if error:
            records.append(result("T02", "BLOCKED" if code is None else "FAIL", error, http_status=code))
        else:
            expected_paths = {"/healthz", "/v1/status", "/v1/gates/{gate}/plan", "/v1/runs/{run_id}"}
            actual_paths = set((schema or {}).get("paths", {}))
            operations_ok = all(
                {name.lower() for name in path_item if name.lower() in HTTP_METHODS} <= {"get"}
                for path_item in (schema or {}).get("paths", {}).values()
            )
            schemes = ((schema or {}).get("components") or {}).get("securitySchemes") or {}
            auth_ok = schemes.get("APIKeyHeader", {}).get("name") == "X-API-Key"
            server_urls = [item.get("url", "").rstrip("/") for item in (schema or {}).get("servers", [])]
            server_ok = base.rstrip("/") in server_urls
            schema_ok = bool((schema or {}).get("openapi")) and expected_paths <= actual_paths and operations_ok and auth_ok and server_ok
            evidence = {
                "openapi_version": (schema or {}).get("openapi"),
                "paths": sorted(actual_paths),
                "get_only": operations_ok,
                "x_api_key_header": auth_ok,
                "server_matches_base_url": server_ok,
            }
            records.append(result("T02", "PASS" if code == 200 and schema_ok else "FAIL",
                                  "OpenAPI contract and GET-only surface checked.", http_status=code, evidence=evidence))

        code, body, error = request_json(public, "/v1/status", params={"repo": "both"})
        if error:
            records.append(result("T03", "BLOCKED" if code is None else "FAIL", error, http_status=code))
        elif code == 401 and body and body.get("error") == "unauthorized":
            records.append(result("T03", "PASS", "Protected status route rejects a request without credentials.", http_status=code, evidence=body))
        else:
            records.append(result("T03", "FAIL", "Unauthenticated status request did not return the expected HTTP 401.", http_status=code, evidence=body))

    if not api_key.strip():
        for test_id, description in [
            ("T04", "Authenticated bilateral status"),
            ("T05", "Authenticated Lite status"),
            ("T06", "Authenticated Plus status"),
        ]:
            records.append(result(test_id, "BLOCKED", f"{description} requires SERVICE_API_KEY in the process environment; it is not requested or printed by this script."))
    else:
        auth_headers = {"X-API-Key": api_key.strip(), "Accept": "application/json"}
        with httpx.Client(base_url=base, headers=auth_headers, timeout=60, follow_redirects=False) as authenticated:
            for test_id, repo, expected_aliases in [
                ("T04", "both", {"lite", "plus"}),
                ("T05", "lite", {"lite"}),
                ("T06", "plus", {"plus"}),
            ]:
                code, body, error = request_json(authenticated, "/v1/status", api_key=api_key.strip(), params={"repo": repo})
                if error:
                    records.append(result(test_id, "FAIL" if code is not None else "BLOCKED", error, http_status=code))
                    continue
                actual = {str(item.get("repo")) for item in (body or {}).get("repositories", []) if isinstance(item, dict)}
                ok = code == 200 and (body or {}).get("read_only") is True and actual == expected_aliases
                records.append(result(test_id, "PASS" if ok else "FAIL",
                                      f"Authenticated status checked for repo={repo}.", http_status=code, evidence=body))

            for test_id, repo, gate in [
                ("T07", "lite", gate_lite),
                ("T08", "plus", gate_plus),
            ]:
                if not gate:
                    records.append(result(test_id, "PENDING", f"Supply an exact, already-grounded {repo.upper()} gate id to run this optional read-only plan test; the script does not guess gate ids."))
                    continue
                if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", gate):
                    records.append(result(test_id, "FAIL", "Invalid gate ID syntax; no network request was sent."))
                    continue
                code, body, error = request_json(
                    authenticated, f"/v1/gates/{gate}/plan", api_key=api_key.strip(),
                    params={"repo": repo, "refresh": "true"},
                )
                if error:
                    records.append(result(test_id, "FAIL" if code is not None else "BLOCKED", error, http_status=code))
                    continue
                plan = (body or {}).get("gate") or {}
                ok = code == 200 and (body or {}).get("read_only") is True and plan.get("action_taken") is False
                records.append(result(test_id, "PASS" if ok else "FAIL",
                                      f"Read-only plan checked for repo={repo}; this call never dispatches a workflow.",
                                      http_status=code, evidence=body))

            for test_id, repo, run_id in [
                ("T09", "lite", run_lite),
                ("T10", "plus", run_plus),
            ]:
                if not run_id:
                    records.append(result(test_id, "PENDING", f"Supply a known numeric {repo.upper()} run id to retrieve its raw evidence."))
                    continue
                if not run_id.isdigit() or len(run_id) > 20:
                    records.append(result(test_id, "FAIL", f"Provided {repo.upper()} run id must contain 1-20 digits only."))
                    continue
                code, body, error = request_json(
                    authenticated, f"/v1/runs/{run_id}", api_key=api_key.strip(), params={"repo": repo},
                )
                if error:
                    records.append(result(test_id, "FAIL" if code is not None else "BLOCKED", error, http_status=code))
                    continue
                run_obj = (body or {}).get("github_run") or {}
                ok = code == 200 and (body or {}).get("read_only") is True and str(run_obj.get("id")) == run_id
                records.append(result(test_id, "PASS" if ok else "FAIL",
                                      f"Raw read-only Actions evidence checked for repo={repo}. Success is not certification PASS.",
                                      http_status=code, evidence=body))

    statuses = [item["status"] for item in records]
    if "FAIL" in statuses:
        exit_code = 1
    elif any(value in {"BLOCKED", "PENDING", "NOT VERIFIED"} for value in statuses):
        exit_code = 2
    else:
        exit_code = 0
    return {
        "target": base,
        "timestamp_utc": utc_now(),
        "execution_profile": "direct-rest-python",
        "read_only": True,
        "external_write_attempted": False,
        "results": records,
        "exit_code": exit_code,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run read-only Precision Lab API checks without GPT Actions.")
    parser.add_argument("--base-url", default=os.getenv("PRECISION_LAB_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--gate-lite", help="Exact Lite gate id grounded in current status/documentation; never guessed.")
    parser.add_argument("--gate-plus", help="Exact Plus gate id grounded in current status/documentation; never guessed.")
    parser.add_argument("--run-lite", help="Known numeric GitHub Actions run id for Lite.")
    parser.add_argument("--run-plus", help="Known numeric GitHub Actions run id for Plus.")
    args = parser.parse_args(argv)
    try:
        report = run_verification(
            args.base_url,
            os.getenv("SERVICE_API_KEY", ""),
            gate_lite=args.gate_lite,
            gate_plus=args.gate_plus,
            run_lite=args.run_lite,
            run_plus=args.run_plus,
        )
    except ValueError as exc:
        print(json.dumps({"status": "FAIL", "detail": str(exc), "read_only": True}, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
