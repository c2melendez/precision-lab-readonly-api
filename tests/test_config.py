from __future__ import annotations

import pytest

from app.config import DEFAULT_TIMEOUT_SECONDS, Settings


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_TIMEOUT_SECONDS),
        ("", DEFAULT_TIMEOUT_SECONDS),
        ("abc", DEFAULT_TIMEOUT_SECONDS),
        ("nan", DEFAULT_TIMEOUT_SECONDS),
        ("inf", DEFAULT_TIMEOUT_SECONDS),
        ("-5", DEFAULT_TIMEOUT_SECONDS),
        ("0", DEFAULT_TIMEOUT_SECONDS),
        ("9999", DEFAULT_TIMEOUT_SECONDS),
        ("15", 15.0),
        (" 30 ", 30.0),
    ],
)
def test_timeout_parsing_never_raises(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("GITHUB_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_TIMEOUT_SECONDS", raw)
    assert Settings.from_env().request_timeout_seconds == expected
