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


@dataclass(frozen=True)
class Settings:
    github_token: str
    service_api_key: str
    request_timeout_seconds: float = 15.0
    bridge_api_key: str = ""
    bridge_base_url: str = "https://cert-runner-compass.lovable.app"
    bridge_timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            github_token=os.getenv("GITHUB_READ_TOKEN", "").strip(),
            service_api_key=os.getenv("SERVICE_API_KEY", "").strip(),
            request_timeout_seconds=float(os.getenv("GITHUB_TIMEOUT_SECONDS", "15")),
            bridge_api_key=os.getenv("llm", "").strip(),
            bridge_base_url=os.getenv("BRIDGE_BASE_URL", "https://cert-runner-compass.lovable.app").strip().rstrip("/"),
            bridge_timeout_seconds=float(os.getenv("BRIDGE_TIMEOUT_SECONDS", "15")),
        )
