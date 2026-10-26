from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class APIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ErrorResponse(APIModel):
    error: str
    detail: str | None = None
    read_only: bool = True


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


class ChangeFile(APIModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=300000)


class ChangeRequest(APIModel):
    repo: Literal["lite", "plus"]
    change_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$", description="Stable identifier for one task. Reuse it for subsequent corrections to update the existing PR; use a new value for an independent task.")
    expected_head_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$", description="Canonical branch HEAD observed immediately before composing this change. A stale SHA is rejected.")
    title: str = Field(min_length=8, max_length=120)
    body: str = Field(default="", max_length=12000)
    commit_message: str | None = Field(default=None, max_length=160)
    draft: bool = False
    files: list[ChangeFile] = Field(min_length=1, max_length=30)


class ChangeOperationResponse(APIModel):
    repo: Literal["lite", "plus"]
    repository_full_name: str
    decision: Literal["REUSE", "PR_CREATED", "PR_REUSED", "PR_UPDATED"]
    action_taken: bool
    canonical_branch: str
    current_head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    operation_id: str | None = None
    change_id: str | None = None
    branch: str | None = None
    branch_head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    detail: str


class GateExecutionResponse(APIModel):
    repo: Literal["lite", "plus"]
    repository_full_name: str
    gate: str
    canonical_branch: str
    observed_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    input_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    planner_decision: str
    decision: Literal["RUN", "WAIT", "REUSE", "DIAGNOSE", "REVIEW", "REFRESH", "DISPATCHED"]
    action_taken: bool
    runner_run_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    runner_run_url: str | None = None
    prior_runner_run_id: str | None = None
    detail: str
    read_only: Literal[False] = False


class MergeRequest(APIModel):
    repo: Literal["lite", "plus"]
    expected_head_sha: str = Field(pattern=r"^[0-9a-fA-F]{40}$")


class MergeOperationResponse(APIModel):
    repo: Literal["lite", "plus"]
    repository_full_name: str
    decision: Literal["MERGED", "ALREADY_MERGED"]
    action_taken: bool
    pull_request_number: int
    pull_request_url: str | None = None
    head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    merge_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    validated_check_runs: list[str] = Field(default_factory=list)
    detail: str
    read_only: Literal[False] = False
