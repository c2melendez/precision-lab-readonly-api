from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from .config import (
    CONTINUITY_PATH,
    CURRENT_STATE_PATH,
    DEFAULT_BRANCH_POINTER_PATH,
    MAX_PLAN_TREE_COMMITS,
    MAX_RUNNER_RECORD_SCAN,
    REPOSITORIES,
    RUNNER_RECORDS_PATH,
    RUNNER_WORKFLOW_PATH,
)
from .github_api import GitHubApiError, GitHubReadClient
from .planner import fingerprint, plan as continuity_plan


class StateMismatchError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_json(content: str, path: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise GitHubApiError(502, f"Repository file {path} contains invalid JSON") from exc
    if not isinstance(value, dict):
        raise GitHubApiError(502, f"Repository file {path} must contain a JSON object")
    return value


def parse_canonical_branch(readme: str) -> str:
    match = re.search(
        r"^##\s+Rama can[oó]nica activa\s*\n\s*`?([A-Za-z0-9._/-]+)`?\s*$",
        readme,
        re.MULTILINE | re.IGNORECASE,
    )
    if not match:
        raise GitHubApiError(502, "The default-branch certification README has no canonical-branch pointer")
    branch = match.group(1).strip("` ")
    if not branch or branch.startswith("/") or ".." in branch.split("/"):
        raise GitHubApiError(502, "The canonical-branch pointer is invalid")
    return branch


def resolve_canonical_branches(client: GitHubReadClient) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve both default-branch pointers and fail closed if they disagree."""
    pointers: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for alias, repository in REPOSITORIES.items():
        meta = client.get_repo(repository)
        default_branch = meta.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch:
            raise GitHubApiError(502, f"Could not resolve the default branch for {repository}")
        defaults[alias] = default_branch
        readme, _ = client.get_content_text(repository, DEFAULT_BRANCH_POINTER_PATH, default_branch)
        pointers[alias] = parse_canonical_branch(readme)
    if pointers["lite"] != pointers["plus"]:
        raise StateMismatchError(
            f"Canonical branch pointers differ: Lite={pointers['lite']}, Plus={pointers['plus']}. Reconcile before planning."
        )
    return pointers, defaults


def normalize_operation(record: dict[str, Any]) -> str | None:
    operation = record.get("operation")
    if operation in ("gate", "docs"):
        return operation
    if record.get("gate") and record.get("gate_workflow"):
        return "gate"  # Legacy schema_version 1 runner record.
    return None


def sanitize_runner_record(record: dict[str, Any], repository: str) -> dict[str, Any]:
    """Return selected non-secret evidence fields. Never return patch URL or raw record."""
    runner_id = str(record.get("runner_run_id") or "")
    if not runner_id.isdigit():
        runner_id = str(record.get("run_id") or "")
    if not runner_id.isdigit():
        runner_id = "0"
    # Construct links from trusted repository constants and numeric IDs. Do not
    # echo arbitrary URLs that could be injected into a hand-edited runner record.
    runner_url = f"https://github.com/{repository}/actions/runs/{runner_id}"
    commits = []
    for commit in record.get("commits", []) or []:
        if isinstance(commit, dict):
            commits.append({"sha": commit.get("sha"), "subject": commit.get("subject")})
    patch = record.get("patch") if isinstance(record.get("patch"), dict) else {}
    dispatched_id = record.get("dispatched_run_id")
    dispatched_id = str(dispatched_id) if dispatched_id is not None and str(dispatched_id).isdigit() else None
    return {
        "schema_version": int(record.get("schema_version", 1)),
        "operation": normalize_operation(record),
        "repo": record.get("repo", repository),
        "canonical_branch": record.get("canonical_branch", ""),
        "base_sha": record.get("base_sha", ""),
        "gate": record.get("gate"),
        "workflow": record.get("workflow"),
        "gate_workflow": record.get("gate_workflow"),
        "dispatched_run_id": dispatched_id,
        "dispatched_url": (
            f"https://github.com/{repository}/actions/runs/{dispatched_id}"
            if dispatched_id is not None else None
        ),
        "dispatched_run_url": (
            f"https://github.com/{repository}/actions/runs/{dispatched_id}"
            if dispatched_id is not None else None
        ),
        "dispatched_status": record.get("dispatched_status"),
        "dispatched_conclusion": record.get("dispatched_conclusion"),
        "patch": {
            "sha256": patch.get("sha256") or patch.get("sha256_hex"),
            "bytes": patch.get("bytes"),
        } if patch else None,
        "commits": commits,
        "paths": record.get("paths", []),
        "patch_head_sha": record.get("patch_head_sha"),
        "decision": record.get("decision", "UNKNOWN"),
        "runner_run_id": runner_id,
        "runner_run_url": runner_url,
        "recorded_at": record.get("recorded_at") or utc_now(),
    }


def get_runner_records(client: GitHubReadClient, repository: str, branch: str, limit: int = 15) -> list[dict[str, Any]]:
    listing = client.list_directory(repository, RUNNER_RECORDS_PATH, branch)
    candidates = []
    for item in listing:
        name = item.get("name", "")
        if item.get("type") == "file" and re.fullmatch(r"[0-9]{1,20}\.json", name):
            candidates.append((int(name[:-5]), name))
    candidates.sort(reverse=True)
    records = []
    # Read a bounded tail. Runner IDs increase; latest state is recorded in the canonical branch.
    for _, filename in candidates[:max(1, min(limit, MAX_RUNNER_RECORD_SCAN))]:
        try:
            content, _ = client.get_content_text(repository, f"{RUNNER_RECORDS_PATH}/{filename}", branch)
            record = parse_json(content, f"{RUNNER_RECORDS_PATH}/{filename}")
            record.setdefault("runner_run_id", filename[:-5])
            records.append(record)
        except GitHubApiError as exc:
            # An inaccessible record must not be replaced with an invented result.
            if exc.status_code in (502, 503):
                raise
    return records


def latest_runner_record(client: GitHubReadClient, repository: str, branch: str) -> dict[str, Any] | None:
    records = get_runner_records(client, repository, branch, limit=5)
    if not records:
        return None
    records.sort(key=lambda r: (str(r.get("recorded_at", "")), int(r.get("runner_run_id", 0) or 0)), reverse=True)
    return sanitize_runner_record(records[0], repository)


def get_repo_status(client: GitHubReadClient, alias: str, pointers: dict[str, str], defaults: dict[str, str], warnings: list[str]) -> dict[str, Any]:
    repository = REPOSITORIES[alias]
    canonical_branch = pointers[alias]
    head_sha = client.get_ref_sha(repository, canonical_branch)
    continuity_text, continuity_blob_sha = client.get_content_text(repository, CONTINUITY_PATH, canonical_branch)
    state = parse_json(continuity_text, CONTINUITY_PATH)
    if state.get("repository") != repository or state.get("canonical_branch") != canonical_branch:
        raise StateMismatchError(f"continuity.json identity mismatch for {repository}; reconcile before using its next action")

    baseline_sha = state.get("technical_baseline_sha", "")
    if not isinstance(baseline_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", baseline_sha):
        raise GitHubApiError(502, "continuity.json has no valid technical_baseline_sha")
    state_status = "REFRESH"
    reconciliation_action = "Refresh baseline objects and reconcile technical HEAD, evidence and ledger before choosing the next objective"
    try:
        current_tree = client.get_commit_tree(repository, head_sha)
        baseline_tree = client.get_commit_tree(repository, state.get("technical_baseline_sha", ""))
        guard = {"workflow": "__snapshot_guard__", "inputs": ["*"], "acceptance_revision": "snapshot-v1"}
        if fingerprint(current_tree, guard, state.get("prose_only_paths", [])) == fingerprint(
            baseline_tree, guard, state.get("prose_only_paths", [])
        ):
            state_status = "CURRENT"
            reconciliation_action = None
        else:
            state_status = "RECONCILE"
    except (ValueError, KeyError):
        state_status = "REFRESH"

    workflow_text, workflow_blob_sha = client.get_content_text(repository, RUNNER_WORKFLOW_PATH, defaults[alias])
    version_match = re.search(r"Versi[oó]n\s+(\d+)", workflow_text, re.IGNORECASE)
    workflow_version = f"v{version_match.group(1)}" if version_match else "unknown"
    record = latest_runner_record(client, repository, canonical_branch)
    if record:
        for commit in record.get("commits", []) or []:
            subject = str(commit.get("subject", ""))
            if "[skip ci]" in subject.lower():
                warning = (
                    f"{alias}: latest runner record includes a documentation commit subject with [skip ci]; "
                    "this conflicts with runner-v3 comments that say not to use [skip ci]. Verify the actual workflow trigger/filter behavior."
                )
                if warning not in warnings:
                    warnings.append(warning)

    return {
        "repo": alias,
        "repository_full_name": repository,
        "default_branch": defaults[alias],
        "canonical_branch": canonical_branch,
        "current_head_sha": head_sha,
        "technical_baseline_sha": state.get("technical_baseline_sha", ""),
        "continuity_revision": int(state.get("revision", 0)),
        "global_certification": str(state.get("global_certification", "OPEN")),
        "next_action": state.get("next_action") if state_status == "CURRENT" else None,
        "state_status": state_status,
        "reconciliation_action": reconciliation_action,
        "blockers": state.get("blockers", []),
        "runner_workflow": {
            "version": workflow_version,
            "workflow_path": RUNNER_WORKFLOW_PATH,
            "blob_sha": workflow_blob_sha,
        },
        "latest_runner_record": record,
        "continuity_blob_sha": continuity_blob_sha,
        "source_links": {
            "repository": f"https://github.com/{repository}",
            "branch": f"https://github.com/{repository}/tree/{canonical_branch}",
            "continuity": f"https://github.com/{repository}/blob/{canonical_branch}/{CONTINUITY_PATH}",
            "current_state": f"https://github.com/{repository}/blob/{canonical_branch}/{CURRENT_STATE_PATH}",
            "runner_workflow": f"https://github.com/{repository}/blob/{defaults[alias]}/{RUNNER_WORKFLOW_PATH}",
        },
        "observation": {"source": "github_live", "fetched_at": utc_now(), "cache_age_seconds": 0, "stale": False},
    }


def get_status(client: GitHubReadClient, repo_query: str = "both") -> dict[str, Any]:
    warnings: list[str] = []
    pointers, defaults = resolve_canonical_branches(client)
    aliases = list(REPOSITORIES) if repo_query == "both" else [repo_query]
    repositories = [get_repo_status(client, alias, pointers, defaults, warnings) for alias in aliases]
    return {"served_at": utc_now(), "read_only": True, "repositories": repositories, "warnings": warnings}


def _branch_and_state(client: GitHubReadClient, alias: str) -> tuple[str, str, str, dict[str, Any]]:
    pointers, _defaults = resolve_canonical_branches(client)
    branch = pointers[alias]
    repository = REPOSITORIES[alias]
    state_text, _ = client.get_content_text(repository, CONTINUITY_PATH, branch)
    state = parse_json(state_text, CONTINUITY_PATH)
    if state.get("repository") != repository or state.get("canonical_branch") != branch:
        raise StateMismatchError("continuity.json repository/branch identity mismatch")
    head_sha = client.get_ref_sha(repository, branch)
    return repository, branch, head_sha, state


def get_gate_plan(client: GitHubReadClient, alias: str, gate_name: str) -> dict[str, Any]:
    repository, branch, head_sha, state = _branch_and_state(client, alias)
    gates = state.get("gates", {})
    if gate_name not in gates:
        raise KeyError(f"Unknown gate '{gate_name}' for {alias}")
    if alias == "plus" and gate_name == "security":
        raise PermissionError("Security gate in Plus is hard-blocked by runner policy until S19 contract acceptance is resolved")

    gate = gates[gate_name]
    if not isinstance(gate, dict) or not isinstance(gate.get("workflow"), str):
        raise ValueError("Gate entry is missing its source workflow path")
    current_tree = client.get_commit_tree(repository, head_sha)
    tree_cache: dict[str, dict[str, list[str]]] = {head_sha: current_tree}

    def tree_at(sha: str) -> dict[str, list[str]]:
        if sha not in tree_cache:
            if len(tree_cache) >= MAX_PLAN_TREE_COMMITS:
                raise ValueError("planner tree-observation cap reached; refresh with a narrower reviewed history window")
            tree_cache[sha] = client.get_commit_tree(repository, sha)
        return tree_cache[sha]

    narrowed_state = dict(state)
    narrowed_state["gates"] = {gate_name: gate}
    # Establish whether the technical baseline still covers HEAD before querying
    # workflow history. If it does not, no historical run can authorize planning;
    # stop early to avoid needless API calls and remain fail-closed.
    preflight_live = {
        "repository": repository,
        "branch": branch,
        "head_sha": head_sha,
        "fetched_at": utc_now(),
        "runs": [],
    }
    preflight = continuity_plan(narrowed_state, preflight_live, tree_at)
    if preflight["state_status"] != "CURRENT":
        gate_result = preflight["gates"][0]
        gate_result["decision"] = "REFRESH"
        gate_result["reason"] = preflight["reconciliation_action"] or "Reconcile continuity state before planning"
        gate_result["action_taken"] = False
        return {
            "repository_full_name": repository,
            "canonical_branch": branch,
            "observed_head": head_sha,
            "state_revision": int(state.get("revision", 0)),
            "state_status": preflight["state_status"],
            "next_action": None,
            "reconciliation_action": preflight.get("reconciliation_action"),
            "blockers": state.get("blockers", []),
            "gate": gate_result,
            "publication_rule": preflight["publication_rule"],
            "read_only": True,
            "observation": {"source": "github_live", "fetched_at": utc_now(), "cache_age_seconds": 0, "stale": False},
            "history_complete": False,
            "history_observed_runs": 0,
        }

    runs, history_complete = client.get_workflow_runs(repository, gate["workflow"], branch)
    observations = []
    for run in runs:
        sha = run.get("head_sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            continue
        path = str(run.get("path", "")).split("@", 1)[0]
        observations.append({
            "id": int(run["id"]),
            "path": path,
            "head_sha": sha,
            "created_at": run.get("created_at"),
            "run_attempt": int(run.get("run_attempt", 1)),
            "status": run.get("status"),
            "conclusion": run.get("conclusion"),
            # Automatic GitHub metadata alone never proves that logs/steps were reviewed.
            "jobs": [],
            "logs_reviewed": False,
            "evidence_executed": False,
            "acceptance_revision": None,
        })
    live = {**preflight_live, "fetched_at": utc_now(), "runs": observations}
    result = continuity_plan(narrowed_state, live, tree_at)
    gate_result = result["gates"][0]
    if result["state_status"] != "CURRENT":
        gate_result["decision"] = "REFRESH"
        gate_result["reason"] = result["reconciliation_action"] or "Reconcile state before using a gate plan"
    elif not history_complete and gate_result["decision"] == "RUN":
        # Bounded API history cannot prove there is no matching older run. Fail closed.
        gate_result["decision"] = "REFRESH"
        gate_result["reason"] = (
            "Workflow history is truncated at the service's read limit; refresh/paginate observations before any execution decision"
        )
    gate_result["action_taken"] = False
    return {
        "repository_full_name": repository,
        "canonical_branch": branch,
        "observed_head": head_sha,
        "state_revision": int(state.get("revision", 0)),
        "state_status": result["state_status"],
        "next_action": result.get("next_action"),
        "reconciliation_action": result.get("reconciliation_action"),
        "blockers": state.get("blockers", []),
        "gate": gate_result,
        "publication_rule": result["publication_rule"],
        "read_only": True,
        "observation": {"source": "github_live", "fetched_at": utc_now(), "cache_age_seconds": 0, "stale": False},
        "history_complete": history_complete,
        "history_observed_runs": len(observations),
    }


def _safe_runner_record_for_run(record: dict[str, Any], repository: str) -> dict[str, Any]:
    safe = sanitize_runner_record(record, repository)
    # Drop nullable fields that do not exist in older v1 records; keep schema predictable.
    return {key: value for key, value in safe.items() if value is not None}


def find_related_runner_record(client: GitHubReadClient, alias: str, branch: str, run_id: int) -> tuple[dict[str, Any] | None, str]:
    repository = REPOSITORIES[alias]
    # First attempt the exact filename. A matching file is a runner's own publication record.
    direct = client.get_content_json(repository, f"{RUNNER_RECORDS_PATH}/{run_id}.json", branch, allow_404=True)
    if isinstance(direct, dict) and direct.get("type") == "file" and isinstance(direct.get("content"), str):
        import base64
        try:
            record = json.loads(base64.b64decode(direct["content"]).decode("utf-8"))
            if isinstance(record, dict):
                record.setdefault("runner_run_id", str(run_id))
                return _safe_runner_record_for_run(record, repository), "self"
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    # A child gate run's ID is stored as dispatched_run_id in its parent runner record.
    # Scan only the most recent records to bound latency/rate-limit use.
    listing = client.list_directory(repository, RUNNER_RECORDS_PATH, branch)
    candidates = []
    for item in listing:
        name = str(item.get("name", ""))
        if item.get("type") == "file" and re.fullmatch(r"[0-9]{1,20}\.json", name):
            candidates.append((int(name[:-5]), name))
    candidates.sort(reverse=True)
    for _, filename in candidates[:MAX_RUNNER_RECORD_SCAN]:
        text, _ = client.get_content_text(repository, f"{RUNNER_RECORDS_PATH}/{filename}", branch)
        record = parse_json(text, f"{RUNNER_RECORDS_PATH}/{filename}")
        record.setdefault("runner_run_id", filename[:-5])
        if str(record.get("dispatched_run_id", "")) == str(run_id):
            return _safe_runner_record_for_run(record, repository), "dispatched_by"
    return None, "none"


def get_run_evidence(client: GitHubReadClient, alias: str, run_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]{1,20}", run_id):
        raise ValueError("run_id must contain decimal digits only")
    repository = REPOSITORIES[alias]
    raw = client.get_run(repository, int(run_id))
    if raw is None:
        raise LookupError(f"Run {run_id} was not found in {repository}")
    attempt = max(1, int(raw.get("run_attempt", 1)))
    jobs_raw = client.get_run_jobs(repository, int(run_id), attempt)
    jobs = []
    for job in jobs_raw:
        steps = []
        for step in job.get("steps", []) or []:
            steps.append({
                "name": str(step.get("name", "")),
                "status": str(step.get("status", "unknown")),
                "conclusion": step.get("conclusion"),
            })
        jobs.append({
            "id": str(job.get("id", "")),
            "name": str(job.get("name", "")),
            "status": str(job.get("status", "unknown")),
            "conclusion": job.get("conclusion"),
            "steps": steps,
        })

    try:
        pointers, _defaults = resolve_canonical_branches(client)
        branch = pointers[alias]
        record, relation = find_related_runner_record(client, alias, branch, int(run_id))
    except StateMismatchError:
        branch, record, relation = "", None, "none"

    github_run = {
        "id": str(raw.get("id", run_id)),
        "name": str(raw.get("name", "")),
        "workflow_path": str(raw.get("path", "")).split("@", 1)[0],
        "event": str(raw.get("event", "")),
        "status": str(raw.get("status", "unknown")),
        "conclusion": raw.get("conclusion"),
        "head_branch": str(raw.get("head_branch", "")),
        "head_sha": str(raw.get("head_sha", "")),
        "html_url": str(raw.get("html_url", f"https://github.com/{repository}/actions/runs/{run_id}")),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "run_attempt": attempt,
        "jobs": jobs,
    }
    note = "Raw GitHub Actions evidence only; success is not certification PASS."
    if not branch:
        note += " Canonical branch pointers disagree, so no runner-record relation was asserted."
    return {
        "read_only": True,
        "github_run": github_run,
        "runner_record": record,
        "record_relation": relation,
        "interpretation_note": note,
        "served_at": utc_now(),
    }
