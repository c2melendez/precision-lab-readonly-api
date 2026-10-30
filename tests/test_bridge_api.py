from __future__ import annotations

from app.bridge_api import BridgeApiError, dispatch_bridge_run, get_bridge_request, get_bridge_run
from app.config import Settings
from fastapi.testclient import TestClient
from app.main import app


def test_bridge_run_proxy_requires_observer_api_key(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    client = TestClient(app)
    run_id = "00000000-0000-0000-0000-000000000001"

    response = client.get(f"/v1/bridge/runs/{run_id}")

    assert response.status_code == 401


def test_bridge_run_proxy_uses_llm_key_and_returns_read_only_payload(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    monkeypatch.setenv("llm", "bridge-secret")
    monkeypatch.setenv("BRIDGE_BASE_URL", "https://bridge.example")
    seen = {}

    def fake_get_bridge_run(run_id, settings):
        seen["run_id"] = run_id
        seen["bridge_api_key"] = settings.bridge_api_key
        seen["bridge_base_url"] = settings.bridge_base_url
        return {"id": run_id, "state": "RUNNING", "runs": []}

    monkeypatch.setattr("app.main.get_bridge_run", fake_get_bridge_run)
    client = TestClient(app)
    run_id = "00000000-0000-0000-0000-000000000001"

    response = client.get(
        f"/v1/bridge/runs/{run_id}",
        headers={"X-API-Key": "observer-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "read_only": True,
        "bridge_run": {"id": run_id, "state": "RUNNING", "runs": []},
    }
    assert seen == {
        "run_id": run_id,
        "bridge_api_key": "bridge-secret",
        "bridge_base_url": "https://bridge.example",
    }
    assert "bridge-secret" not in response.text


def test_bridge_client_sends_x_bridge_key_and_refuses_redirects(monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "00000000-0000-0000-0000-000000000001", "state": "RUNNING"}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, headers):
            seen["url"] = url
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("app.bridge_api.httpx.Client", FakeClient)
    settings = Settings(
        github_token="",
        service_api_key="",
        bridge_api_key="bridge-secret",
        bridge_base_url="https://bridge.example",
        bridge_timeout_seconds=7,
    )
    payload = get_bridge_run(
        "00000000-0000-0000-0000-000000000001",
        settings,
    )

    assert payload["state"] == "RUNNING"
    assert seen["url"] == "https://bridge.example/api/runs/00000000-0000-0000-0000-000000000001"
    assert seen["headers"]["X-Bridge-Key"] == "bridge-secret"
    assert seen["client_kwargs"] == {"timeout": 7, "follow_redirects": False}


def test_bridge_client_refuses_non_https_url():
    settings = Settings(
        github_token="",
        service_api_key="",
        bridge_api_key="bridge-secret",
        bridge_base_url="http://bridge.example",
    )
    try:
        get_bridge_run("00000000-0000-0000-0000-000000000001", settings)
    except BridgeApiError as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("Non-HTTPS Bridge URL must be refused")


def test_bridge_status_route_is_get_only_and_uses_observer_authentication():
    schema = app.openapi()
    route = schema["paths"]["/v1/bridge/runs/{run_id}"]

    assert "get" in route
    assert not any(method in route for method in ("post", "put", "patch", "delete"))
    assert route["get"]["security"] == [{"APIKeyHeader": []}]


def test_bridge_timeout_defaults_to_ninety_seconds_and_supports_override(monkeypatch):
    monkeypatch.delenv("BRIDGE_TIMEOUT_SECONDS", raising=False)
    assert Settings.from_env().bridge_timeout_seconds == 90.0

    monkeypatch.setenv("BRIDGE_TIMEOUT_SECONDS", "23")
    assert Settings.from_env().bridge_timeout_seconds == 23.0



def test_dispatch_client_uses_dedicated_scoped_key_and_idempotency_header(monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 201

        @staticmethod
        def json():
            return {"id": "receipt-uuid", "idempotency_id": "claim-uuid", "reused": False}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, headers, json):
            seen.update(url=url, headers=headers, json=json)
            return FakeResponse()

    monkeypatch.setattr("app.bridge_api.httpx.Client", FakeClient)
    settings = Settings(
        github_token="",
        service_api_key="",
        bridge_api_key="read-only-secret",
        bridge_dispatch_api_key="dispatch-only-secret",
        bridge_base_url="https://bridge.example",
        bridge_timeout_seconds=11,
    )
    payload = {
        "target_repo": "lite", "gate": "cumulative", "dry_run": False,
        "idempotency_key": "validation-1", "validation_id": "validation-1",
        "correlation_id": "correlation-1", "branch": "qa/certification",
        "requested_sha": "a" * 40,
    }
    status, body = dispatch_bridge_run(payload, settings)

    assert status == 201
    assert body["id"] == "receipt-uuid"
    assert seen["url"] == "https://bridge.example/api/runs"
    assert seen["headers"]["X-Bridge-Key"] == "dispatch-only-secret"
    assert seen["headers"]["Idempotency-Key"] == "validation-1"
    assert "read-only-secret" not in str(seen["headers"])
    assert seen["json"] == payload
    assert seen["client_kwargs"] == {"timeout": 11, "follow_redirects": False}


def test_dispatch_client_treats_timeout_as_uncertain_and_never_retries(monkeypatch):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def post(self, *_args, **_kwargs):
            import httpx
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("app.bridge_api.httpx.Client", FakeClient)
    settings = Settings(
        github_token="", service_api_key="", bridge_dispatch_api_key="dispatch-secret",
        bridge_base_url="https://bridge.example",
    )
    try:
        dispatch_bridge_run({"idempotency_key": "same-key"}, settings)
    except BridgeApiError as exc:
        assert exc.status_code == 409
        assert "DISPATCH_OUTCOME_UNKNOWN" in exc.message
    else:
        raise AssertionError("Timeout must be treated as an uncertain dispatch outcome")


def test_dispatch_route_requires_observer_authentication_and_live_run_plan(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    monkeypatch.setenv("BRIDGE_DISPATCH_KEY", "dispatch-secret")
    monkeypatch.setenv("BRIDGE_BASE_URL", "https://bridge.example")
    client = TestClient(app)
    payload = {
        "target_repo": "lite", "gate": "cumulative", "dry_run": False,
        "idempotency_key": "validation-1", "validation_id": "validation-1",
        "correlation_id": "correlation-1", "branch": "qa/syntax-audit-in625-a1",
        "requested_sha": "a" * 40,
    }
    response = client.post("/v1/bridge/dispatch", json=payload)
    assert response.status_code == 401


def test_dispatch_route_does_not_call_bridge_when_plan_is_not_run(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    monkeypatch.setenv("BRIDGE_DISPATCH_KEY", "dispatch-secret")
    from app.main import get_github
    app.dependency_overrides[get_github] = lambda: object()
    monkeypatch.setattr("app.main.get_gate_plan", lambda *_args: {
        "state_status": "CURRENT",
        "canonical_branch": "qa/syntax-audit-in625-a1",
        "observed_head": "a" * 40,
        "gate": {"decision": "WAIT", "reason": "Equivalent gate run is active"},
    })
    monkeypatch.setattr("app.main.dispatch_bridge_run", lambda *_args: (_ for _ in ()).throw(AssertionError("Bridge must not be called")))
    try:
        response = TestClient(app).post("/v1/bridge/dispatch", json={
            "target_repo": "lite", "gate": "cumulative", "dry_run": False,
            "idempotency_key": "validation-1", "validation_id": "validation-1",
            "correlation_id": "correlation-1", "branch": "qa/syntax-audit-in625-a1",
            "requested_sha": "a" * 40,
        }, headers={"X-API-Key": "observer-secret"})
        assert response.status_code == 200
        assert response.json()["decision"] == "WAIT"
        assert response.json()["action_taken"] is False
        assert response.headers["X-Read-Only"] == "false"
    finally:
        app.dependency_overrides.clear()


def test_dispatch_route_rejects_stale_head_before_bridge_call(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    from app.main import get_github
    app.dependency_overrides[get_github] = lambda: object()
    monkeypatch.setattr("app.main.get_gate_plan", lambda *_args: {
        "state_status": "CURRENT",
        "canonical_branch": "qa/syntax-audit-in625-a1",
        "observed_head": "b" * 40,
        "gate": {"decision": "RUN", "reason": "No equivalent run"},
    })
    monkeypatch.setattr("app.main.dispatch_bridge_run", lambda *_args: (_ for _ in ()).throw(AssertionError("Bridge must not be called")))
    try:
        response = TestClient(app).post("/v1/bridge/dispatch", json={
            "target_repo": "lite", "gate": "cumulative", "dry_run": False,
            "idempotency_key": "validation-1", "validation_id": "validation-1",
            "correlation_id": "correlation-1", "branch": "qa/syntax-audit-in625-a1",
            "requested_sha": "a" * 40,
        }, headers={"X-API-Key": "observer-secret"})
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_dispatch_route_requires_canonical_branch_and_sha_and_calls_bridge(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    monkeypatch.setenv("BRIDGE_DISPATCH_KEY", "dispatch-secret")
    from app.main import get_github
    app.dependency_overrides[get_github] = lambda: object()
    monkeypatch.setattr("app.main.get_gate_plan", lambda *_args: {
        "state_status": "CURRENT",
        "canonical_branch": "qa/syntax-audit-in625-a1",
        "observed_head": "a" * 40,
        "gate": {"decision": "RUN", "reason": "No equivalent run"},
    })
    seen = {}
    monkeypatch.setattr("app.main.dispatch_bridge_run", lambda payload, settings: (
        seen.update(payload=payload, key=settings.bridge_dispatch_api_key) or
        (201, {"id": "receipt-uuid", "reused": False})
    ))
    try:
        response = TestClient(app).post("/v1/bridge/dispatch", json={
            "target_repo": "lite", "gate": "cumulative", "dry_run": False,
            "idempotency_key": "validation-1", "validation_id": "validation-1",
            "correlation_id": "correlation-1", "branch": "qa/syntax-audit-in625-a1",
            "requested_sha": "a" * 40,
        }, headers={"X-API-Key": "observer-secret"})
        assert response.status_code == 201
        assert response.json()["id"] == "receipt-uuid"
        assert seen["key"] == "dispatch-secret"
        assert seen["payload"]["requested_sha"] == "a" * 40
    finally:
        app.dependency_overrides.clear()



def test_bridge_receipt_lookup_uses_read_scoped_key_and_reconciles(monkeypatch):
    seen = {}

    class FakeResponse:
        status_code = 200
        @staticmethod
        def json():
            return {"idempotency_id": "claim-1", "state": "SUCCEEDED", "outcome_state": "SUCCEEDED"}

    class FakeClient:
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def get(self, url, headers):
            seen.update(url=url, headers=headers)
            return FakeResponse()

    monkeypatch.setattr("app.bridge_api.httpx.Client", FakeClient)
    settings = Settings(
        github_token="", service_api_key="", bridge_api_key="read-only-secret",
        bridge_dispatch_api_key="dispatch-only-secret", bridge_base_url="https://bridge.example",
    )
    result = get_bridge_request("validation-1", settings)
    assert result["state"] == "SUCCEEDED"
    assert seen["url"] == "https://bridge.example/api/runs/lookup"
    assert seen["headers"]["X-Bridge-Key"] == "read-only-secret"
    assert seen["headers"]["Idempotency-Key"] == "validation-1"
    assert "dispatch-only-secret" not in str(seen["headers"])


def test_bridge_receipt_reconciliation_route_requires_observer_auth(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    response = TestClient(app).get("/v1/bridge/requests/validation-1")
    assert response.status_code == 401


def test_bridge_receipt_reconciliation_route_uses_read_credential(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "observer-secret")
    monkeypatch.setenv("llm", "read-only-secret")
    monkeypatch.setenv("BRIDGE_BASE_URL", "https://bridge.example")
    seen = {}
    def fake_lookup(key, settings):
        seen["key"] = key
        seen["url"] = settings.bridge_base_url
        return {"idempotency_id": "claim-1", "state": "RUNNING"}
    monkeypatch.setattr("app.main.get_bridge_request", fake_lookup)
    response = TestClient(app).get(
        "/v1/bridge/requests/validation-1",
        headers={"X-API-Key": "observer-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "read_only": True,
        "bridge_request": {"idempotency_id": "claim-1", "state": "RUNNING"},
    }
    assert seen == {"key": "validation-1", "url": "https://bridge.example"}
