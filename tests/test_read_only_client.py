from __future__ import annotations

import json

import httpx
import pytest

from integrations.read_only_client import (
    DEFAULT_API_BASE_URL,
    ReadOnlyApiClient,
    execute_readonly_tool,
    gemini_function_declarations,
    openai_compatible_tool_schemas,
    responses_api_tool_schemas,
)


def _client(handler):
    return ReadOnlyApiClient(
        base_url=DEFAULT_API_BASE_URL,
        api_key="test-secret-never-returned",
        transport=httpx.MockTransport(handler),
    )


def test_status_uses_authenticated_get_and_returns_payload_without_credentials():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/status"
        assert request.url.params["repo"] == "both"
        assert request.headers["X-API-Key"] == "test-secret-never-returned"
        return httpx.Response(
            200,
            json={"read_only": True, "repositories": [{"repo": "lite"}, {"repo": "plus"}]},
        )

    with _client(handler) as client:
        result = client.status("both")

    assert result["read_only"] is True
    assert len(result["repositories"]) == 2
    assert len(seen) == 1
    assert "test-secret-never-returned" not in json.dumps(result)


def test_gate_plan_and_run_evidence_are_get_only_and_validate_path_parameters():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"read_only": True, "path": request.url.path})

    with _client(handler) as client:
        plan = client.gate_plan(repo="lite", gate="cumulative", refresh=True)
        run = client.run_evidence(repo="plus", run_id="123456")

    assert plan["path"] == "/v1/gates/cumulative/plan"
    assert run["path"] == "/v1/runs/123456"
    assert seen[0].url.params["repo"] == "lite"
    assert seen[0].url.params["refresh"] == "true"
    assert seen[1].url.params["repo"] == "plus"
    assert all(item.method == "GET" for item in seen)


@pytest.mark.parametrize("repo", ["bothx", "../lite", "", None])
def test_status_rejects_unknown_repository_without_network(repo):
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with _client(handler) as client:
        with pytest.raises(ValueError, match="repo must be"):
            client.status(repo)  # type: ignore[arg-type]

    assert called is False


@pytest.mark.parametrize("gate", ["../status", "space gate", "gate?repo=plus", ""])
def test_gate_plan_rejects_untrusted_gate_path(gate):
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with _client(handler) as client:
        with pytest.raises(ValueError, match="exact gate identifier"):
            client.gate_plan(repo="lite", gate=gate)

    assert called is False


@pytest.mark.parametrize("run_id", ["../123", "1?repo=plus", "abc", "", "1/2"])
def test_run_evidence_rejects_non_numeric_run_id(run_id):
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    with _client(handler) as client:
        with pytest.raises(ValueError, match="decimal digits"):
            client.run_evidence(repo="lite", run_id=run_id)

    assert called is False


def test_http_401_is_reported_without_echoing_key_or_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            401,
            json={"error": "unauthorized", "detail": "Missing or invalid API key"},
        )

    with _client(handler) as client:
        with pytest.raises(RuntimeError, match="HTTP 401") as error:
            client.status("both")

    assert "test-secret-never-returned" not in str(error.value)
    assert "Missing or invalid API key" not in str(error.value)


def test_api_base_url_must_be_https_and_must_not_embed_credentials():
    with pytest.raises(ValueError, match="HTTPS"):
        ReadOnlyApiClient(base_url="http://example.test", api_key="key")
    with pytest.raises(ValueError, match="credentials"):
        ReadOnlyApiClient(base_url="https://user:pass@precision-lab-readonly-api.onrender.com", api_key="key")
    with pytest.raises(ValueError, match="default HTTPS port"):
        ReadOnlyApiClient(base_url="https://precision-lab-readonly-api.onrender.com:8443", api_key="key")


def test_function_schemas_are_available_for_openai_compatible_and_gemini_hosts():
    openai_tools = openai_compatible_tool_schemas()
    gemini_tools = gemini_function_declarations()
    responses_tools = responses_api_tool_schemas()

    def schema_keys(node):
        if isinstance(node, dict):
            nested = set()
            for value in node.values():
                nested |= schema_keys(value)
            return set(node) | nested
        if isinstance(node, list):
            nested = set()
            for value in node:
                nested |= schema_keys(value)
            return nested
        return set()

    for provider_schema in (openai_tools, gemini_tools, responses_tools):
        assert "additionalProperties" not in schema_keys(provider_schema)
        assert "pattern" not in schema_keys(provider_schema)
        assert "default" not in schema_keys(provider_schema)
    assert all(item["type"] == "function" and "name" in item and "function" not in item
               for item in responses_tools)
    assert {item["function"]["name"] for item in openai_tools} == {
        "precision_lab_status",
        "precision_lab_gate_plan",
        "precision_lab_run_evidence",
    }
    assert {item["name"] for item in gemini_tools} == {
        "precision_lab_status",
        "precision_lab_gate_plan",
        "precision_lab_run_evidence",
    }
    assert all("parameters" in item["function"] for item in openai_tools)
    descriptions = " ".join(item["function"]["description"] for item in openai_tools)
    assert "never dispatches" in descriptions
    assert "not by itself certification PASS" in descriptions


def test_dispatcher_accepts_only_allowlisted_read_tools(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "")
    with pytest.raises(ValueError, match="Unknown read-only tool"):
        execute_readonly_tool("dispatch_github_workflow", {})


def test_missing_api_key_is_rejected_before_network_call(monkeypatch):
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key is missing"):
        ReadOnlyApiClient.from_environment()

def test_untrusted_custom_https_host_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("PRECISION_LAB_ALLOW_CUSTOM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="not allowlisted"):
        ReadOnlyApiClient(base_url="https://example.test", api_key="key")

    monkeypatch.setenv("PRECISION_LAB_ALLOW_CUSTOM_BASE_URL", "true")
    with ReadOnlyApiClient(base_url="https://example.test", api_key="key") as client:
        assert client.base_url == "https://example.test"

