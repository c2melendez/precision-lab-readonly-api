from __future__ import annotations

import fnmatch
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable


def selected(path: str, gate: dict[str, Any], prose_paths: list[str]) -> bool:
    # Gate result records are produced after execution and must not invalidate
    # the gate fingerprint that they document. Exclude numeric runner records
    # only; keep runner code, tests, schemas, and all other certification files.
    if (
        path in prose_paths
        or path == "qa/certification/continuity.json"
        or re.fullmatch(r"qa/certification/runner/[0-9]{1,20}\.json", path)
    ):
        return False
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in gate["inputs"])


def fingerprint(entries: dict[str, list[str]], gate: dict[str, Any], prose_paths: list[str]) -> str:
    payload = {
        "planner_version": 2,
        "gate": gate,
        "inputs": {
            path: value
            for path, value in sorted(entries.items())
            if selected(path, gate, prose_paths)
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def plan(state: dict[str, Any], live: dict[str, Any], tree_at: Callable[[str], dict[str, list[str]]], now: datetime | None = None) -> dict[str, Any]:
    """Read-only port of the repository continuity planner's decision rules.

    Source reference: scripts/certification/continuity.py on the canonical branch.
    The service intentionally never writes back to GitHub and never interprets a
    workflow conclusion as proof that certification is accepted.
    """
    now = now or datetime.now(timezone.utc)
    if state.get("schema_version") != 1:
        raise ValueError("unsupported continuity schema")
    if (live["repository"], live["branch"]) != (state["repository"], state["canonical_branch"]):
        raise ValueError("repository/branch mismatch: rediscover canonical branch")
    age = now - parse_time(live["fetched_at"])
    if age < timedelta(seconds=-30) or age > timedelta(minutes=15):
        raise ValueError("GitHub snapshot expired or future-dated; refresh it")

    current = tree_at(live["head_sha"])
    cache = {live["head_sha"]: current}
    guard = {"workflow": "__snapshot_guard__", "inputs": ["*"], "acceptance_revision": "snapshot-v1"}
    try:
        baseline = tree_at(state["technical_baseline_sha"])
        same_baseline = fingerprint(current, guard, state["prose_only_paths"]) == fingerprint(
            baseline, guard, state["prose_only_paths"]
        )
        state_status = "CURRENT" if same_baseline else "RECONCILE"
    except (KeyError, ValueError):
        state_status = "REFRESH"

    results = []
    for name, gate in state["gates"].items():
        if not gate.get("acceptance_revision"):
            raise ValueError(f"gate {name} lacks acceptance_revision")
        if not gate.get("inputs") or not selected(gate["workflow"], gate, state["prose_only_paths"]):
            raise ValueError(f"gate {name} has unsafe/empty input scope")
        input_hash = fingerprint(current, gate, state["prose_only_paths"])
        item: dict[str, Any] = {
            "gate": name,
            "input_fingerprint": input_hash,
            "head_sha": live["head_sha"],
            "decision": "RUN",
            "reason": "no verified equivalent run in fresh observations",
        }
        relevant = []
        unavailable = []
        for run in live.get("runs", []):
            if run.get("path") != gate["workflow"]:
                continue
            run_sha = run.get("head_sha")
            try:
                if run_sha not in cache:
                    cache[run_sha] = tree_at(run_sha)
                same = fingerprint(cache[run_sha], gate, state["prose_only_paths"]) == input_hash
            except (KeyError, ValueError):
                unavailable.append(str(run.get("id", "")))
                continue
            if same:
                relevant.append(run)

        relevant.sort(
            key=lambda r: (parse_time(r["created_at"]), r.get("run_attempt", 1), int(r["id"])),
            reverse=True,
        )
        if relevant:
            run = relevant[0]
            item["run_id"] = int(run["id"])
            item["evidence_sha"] = run["head_sha"]
            if run.get("status") != "completed":
                item.update(decision="WAIT", reason="equivalent inputs already queued/running; inspect existing run")
            elif run.get("conclusion") == "success":
                max_age = gate.get("max_evidence_age_hours")
                expired = max_age is not None and now - parse_time(run["created_at"]) > timedelta(hours=max_age)
                if gate.get("allow_reuse", True) is False:
                    item.update(
                        decision="REVIEW",
                        reason="dynamic external evidence: review current advisories/preconditions before scheduling; prior green cannot be reused automatically",
                    )
                elif expired:
                    item.update(
                        decision="REVIEW",
                        reason="evidence age exceeds this gate policy; reassess current external inputs before scheduling",
                    )
                elif (
                    run.get("logs_reviewed") is True
                    and run.get("evidence_executed") is True
                    and run.get("jobs")
                    and run.get("acceptance_revision") == gate["acceptance_revision"]
                    and all(job.get("conclusion") == "success" for job in run["jobs"])
                ):
                    item.update(
                        decision="REUSE",
                        reason="prior reviewed evidence covers identical gate inputs; not a new execution or global certification",
                    )
                else:
                    item.update(
                        decision="REVIEW",
                        reason="success metadata needs actual required-step execution/origin evidence, logs, all job conclusions and current acceptance_revision review before reuse",
                    )
            elif run.get("conclusion") in ("failure", "timed_out", "action_required"):
                item.update(
                    decision="DIAGNOSE",
                    reason="same inputs already failed; classify cause and record a change or justified retry before dispatch",
                )
            else:
                item.update(
                    decision="REVIEW",
                    reason="cancelled/skipped/neutral run is not PASS; establish why before rerunning",
                )
        elif unavailable:
            item.update(
                decision="REFRESH",
                reason="workflow runs exist but source objects unavailable; fetch evidence commits before scheduling",
                unavailable_run_ids=unavailable,
            )
        results.append(item)

    return {
        "repository": state["repository"],
        "branch": state["canonical_branch"],
        "observed_head": live["head_sha"],
        "state_revision": state["revision"],
        "state_status": state_status,
        "next_action": state.get("next_action") if state_status == "CURRENT" else None,
        "reconciliation_action": None
        if state_status == "CURRENT"
        else "Refresh baseline objects and reconcile technical HEAD, evidence and ledger before choosing the next objective",
        "blockers": state.get("blockers", []),
        "gates": results,
        "publication_rule": "Refresh remote HEAD immediately before publishing; use expected_sha compare-and-swap. No persistent lock.",
    }
