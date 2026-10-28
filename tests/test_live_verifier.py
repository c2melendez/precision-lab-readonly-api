from __future__ import annotations

import httpx
import pytest

from scripts.verify_live_api import parse_base_url, request_json


TRUSTED = "https://precision-lab-readonly-api.onrender.com"


def test_parse_base_url_accepts_only_trusted_https_origin_by_default():
    assert parse_base_url(TRUSTED) == TRUSTED
    assert parse_base_url(TRUSTED + ":443") == TRUSTED + ":443"


@pytest.mark.parametrize(
    "url, message",
    [
        ("http://precision-lab-readonly-api.onrender.com", "HTTPS"),
        ("https://example.invalid", "not allowlisted"),
        ("https://precision-lab-readonly-api.onrender.com:8443", "default HTTPS port"),
        ("https://user:password@precision-lab-readonly-api.onrender.com", "credentials"),
        ("https://precision-lab-readonly-api.onrender.com/v1", "origin only"),
        ("https://precision-lab-readonly-api.onrender.com?repo=both", "query"),
    ],
)
def test_parse_base_url_rejects_unsafe_origin(url, message, monkeypatch):
    monkeypatch.delenv("PRECISION_LAB_ALLOW_CUSTOM_BASE_URL", raising=False)
    with pytest.raises(ValueError, match=message):
        parse_base_url(url)


def test_custom_https_origin_requires_explicit_operator_opt_in(monkeypatch):
    monkeypatch.setenv("PRECISION_LAB_ALLOW_CUSTOM_BASE_URL", "true")
    assert parse_base_url("https://localhost:8443") == "https://localhost:8443"


def test_request_json_uses_get_and_redacts_service_key_from_response():
    secret = "test-service-key-do-not-display"
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"detail": "redacted", "echo": secret})

    with httpx.Client(
        base_url=TRUSTED,
        headers={"X-API-Key": secret},
        transport=httpx.MockTransport(handler),
    ) as client:
        status, body, error = request_json(client, "/v1/status", api_key=secret, params={"repo": "both"})

    assert status == 200
    assert error is None
    assert body == {"detail": "redacted", "echo": "[REDACTED]"}
    assert len(seen) == 1 and seen[0].method == "GET"
