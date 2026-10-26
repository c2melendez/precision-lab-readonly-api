from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ErrorResponse(APIModel):
    error: str
    detail: str | None = None
    read_only: Literal[True] = True


class Blocker(APIModel):
    id: str
    status: str
    kind: str | None = None
    detail: str | None = None


class NextAction(APIModel):
    id: str
    status: str
    action: str


class ObservationMetadata(APIModel):
    source: Literal["github_live", "cache"]
    fetched_at: str
    cache_age_seconds: int = Field(ge=0)
    stale: bool = False


class RunnerWorkflowInfo(APIModel):
    version: str
    workflow_path: str
    blob_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class RunnerCommit(APIModel):
    sha: str | None = None
    subject: str | None = None


class RunnerPatchSummary(APIModel):
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bytes: int | None = Field(default=None, ge=1)


class RunnerRecord(APIModel):
    schema_version: int
    operation: Literal["gate", "docs"] | None = None
    repo: str
    canonical_branch: str
    base_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    gate: str | None = None
    workflow: str | None = None
    gate_workflow: str | None = None
    dispatched_run_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    dispatched_url: str | None = None
    dispatched_run_url: str | None = None
    dispatched_status: str | None = None
    dispatched_conclusion: str | None = None
    patch: RunnerPatchSummary | None = None
    commits: list[RunnerCommit] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    patch_head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    decision: str
    runner_run_id: str = Field(pattern=r"^[0-9]{1,20}$")
    runner_run_url: str
    recorded_at: str


class RepositoryStatus(APIModel):
    repo: Literal["lite", "plus"]
    repository_full_name: str
    default_branch: str
    canonical_branch: str
    current_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    technical_baseline_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    continuity_revision: int = Field(ge=0)
    global_certification: str
    state_status: Literal["CURRENT", "RECONCILE", "REFRESH"]
    next_action: NextAction | None
    reconciliation_action: str | None = None
    blockers: list[Blocker] = Field(default_factory=list)
    runner_workflow: RunnerWorkflowInfo
    latest_runner_record: RunnerRecord | None = None
    continuity_blob_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_links: dict[str, str]
    observation: ObservationMetadata


class StatusResponse(APIModel):
    served_at: str
    read_only: Literal[True] = True
    repositories: list[RepositoryStatus] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class GatePlan(APIModel):
    gate: str
    decision: Literal["RUN", "WAIT", "REUSE", "DIAGNOSE", "REVIEW", "REFRESH"]
    reason: str
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: int | None = None
    evidence_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    unavailable_run_ids: list[str] = Field(default_factory=list)
    action_taken: Literal[False] = False


class GatePlanResponse(APIModel):
    repository_full_name: str
    canonical_branch: str
    observed_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    state_revision: int = Field(ge=0)
    state_status: Literal["CURRENT", "RECONCILE", "REFRESH"]
    next_action: NextAction | None
    reconciliation_action: str | None = None
    blockers: list[Blocker] = Field(default_factory=list)
    gate: GatePlan
    publication_rule: str
    read_only: Literal[True] = True
    observation: ObservationMetadata
    history_complete: bool
    history_observed_runs: int = Field(ge=0)


class ActionStep(APIModel):
    name: str
    status: str
    conclusion: str | None = None


class ActionJob(APIModel):
    id: str
    name: str
    status: str
    conclusion: str | None = None
    steps: list[ActionStep] = Field(default_factory=list)


class GitHubRun(APIModel):
    id: str = Field(pattern=r"^[0-9]{1,20}$")
    name: str
    workflow_path: str
    event: str
    status: str
    conclusion: str | None = None
    head_branch: str
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    html_url: str
    created_at: str
    updated_at: str
    run_attempt: int = Field(ge=1)
    jobs: list[ActionJob] = Field(default_factory=list)


class RunEvidenceResponse(APIModel):
    read_only: Literal[True] = True
    github_run: GitHubRun
    runner_record: RunnerRecord | None = None
    record_relation: Literal["self", "dispatched_by", "none"]
    interpretation_note: str
    served_at: str
