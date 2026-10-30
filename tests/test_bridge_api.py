from __future__ import annotations

from app.bridge_api import BridgeApiError, get_bridge_run
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
