from __future__ import annotations

import os
from dataclasses import dataclass

REPOSITORIES: dict[str, str] = {
    "lite": "c2melendez/precision-lab-lite",
    "plus": "c2melendez/precision-lab-plus",
}
DEFAULT_BRANCH_POINTER_PATH = "qa/certification/README.md"
CONTINUITY_PATH = "qa/certification/continuity.json"
CURRENT_STATE_PATH = "qa/certification/CURRENT_STATE.md"
RUNNER_WORKFLOW_PATH = ".github/workflows/certification-runner.yml"
RUNNER_RECORDS_PATH = "qa/certification/runner"
MAX_RUN_HISTORY = 15
MAX_PLAN_TREE_COMMITS = 12
MAX_ACTION_JOB_PAGES = 10
MAX_RUNNER_RECORD_SCAN = 25
FRESHNESS_SECONDS = 15 * 60


DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 60.0


def _parse_timeout(raw: str | None) -> float:
    """Parse GITHUB_TIMEOUT_SECONDS; fall back to the default on invalid or out-of-range input."""
    try:
        value = float((raw or "").strip())
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if not (0 < value <= MAX_TIMEOUT_SECONDS):  # also rejects nan/inf/negatives
        return DEFAULT_TIMEOUT_SECONDS
    return value


@dataclass(frozen=True)
class Settings:
    github_token: str
    service_api_key: str
    request_timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            github_token=os.getenv("GITHUB_READ_TOKEN", "").strip(),
            service_api_key=os.getenv("SERVICE_API_KEY", "").strip(),
            request_timeout_seconds=_parse_timeout(os.getenv("GITHUB_TIMEOUT_SECONDS")),
        )
