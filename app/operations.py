from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from typing import Any

from .config import CONTINUITY_PATH, REPOSITORIES
from .github_api import GitHubApiError, GitHubReadClient
from .github_write import GitHubWriteClient
from .service import StateMismatchError, resolve_canonical_branches


_FORBIDDEN_PATH_COMPONENTS = {".git", ".github/secrets"}
_SECRET_NAME = re.compile(r"(^|/)(secrets?\.(json|ya?ml|toml)$|credentials?(\.|$)|id_rsa($|\.)|.*\.(pem|p12|pfx|key)$)", re.I)


def validate_change_paths(files: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    if not files:
        raise ValueError("At least one file change is required")
    if len(files) > 30:
        raise ValueError("A single bridge change is limited to 30 files")
    total_bytes = 0
    for item in files:
        path = item.get("path", "")
        content = item.get("content", "")
        if not isinstance(path, str) or not path or len(path) > 240:
            raise ValueError("Each file needs a non-empty repository-relative path of at most 240 characters")
        if path.startswith("/") or "\\" in path or "\x00" in path:
            raise ValueError(f"Unsafe repository path: {path!r}")
        pieces = path.split("/")
        if any(piece in ("", ".", "..") for piece in pieces) or any(piece == ".git" for piece in pieces):
            raise ValueError(f"Unsafe repository path: {path!r}")
        if path in seen:
            raise ValueError(f"Duplicate file path: {path}")
        seen.add(path)
        if re.fullmatch(r"qa/certification/runner/[0-9]{1,20}\.json", path):
            raise ValueError("Bridge requests cannot modify runner evidence records")
        basename = path.rsplit("/", 1)[-1].lower()
        if basename == ".env" or (basename.startswith(".env.") and basename not in {".env.example", ".env.sample", ".env.template"}):
            raise ValueError(f"Refusing to publish an environment secret file: {path}")
        if _SECRET_NAME.search(path):
            raise ValueError(f"Refusing to publish a path that looks like a secret or private key: {path}")
        if not isinstance(content, str):
            raise ValueError(f"File content must be UTF-8 text: {path}")
        total_bytes += len(content.encode("utf-8"))
    if total_bytes > 1_000_000:
        raise ValueError("Total change content exceeds the 1 MB safety limit")


def _decode_content(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict) or payload.get("type") != "file":
        return None
    content = payload.get("content")
    if not isinstance(content, str):
        return None
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def _same_files_on_ref(read_client: GitHubReadClient, repository: str, ref: str, files: list[dict[str, str]]) -> bool:
    for item in files:
        remote = read_client.get_content_json(repository, item["path"], ref, allow_404=True)
        if _decode_content(remote) != item["content"]:
            return False
    return True


def _create_git_commit(
    write_client: GitHubWriteClient,
    repository: str,
    parent_sha: str,
    files: list[dict[str, str]],
    message: str,
) -> str:
    parent_commit = write_client.get_json(f"repos/{repository}/git/commits/{parent_sha}")
    base_tree_sha = parent_commit.get("tree", {}).get("sha")
    if not isinstance(base_tree_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", base_tree_sha):
        raise GitHubApiError(502, "GitHub returned an invalid base tree SHA")

    entries = []
    for item in files:
        blob = write_client.post_json(
            f"repos/{repository}/git/blobs",
            {"content": item["content"], "encoding": "utf-8"},
        )
        blob_sha = blob.get("sha")
        if not isinstance(blob_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", blob_sha):
            raise GitHubApiError(502, f"GitHub did not return a valid blob SHA for {item['path']}")
        entries.append({"path": item["path"], "mode": "100644", "type": "blob", "sha": blob_sha})

    tree = write_client.post_json(
        f"repos/{repository}/git/trees",
        {"base_tree": base_tree_sha, "tree": entries},
    )
    tree_sha = tree.get("sha")
    if not isinstance(tree_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
        raise GitHubApiError(502, "GitHub did not return a valid change tree SHA")
    commit = write_client.post_json(
        f"repos/{repository}/git/commits",
        {"message": message, "tree": tree_sha, "parents": [parent_sha]},
    )
    commit_sha = commit.get("sha")
    if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise GitHubApiError(502, "GitHub did not return a valid change commit SHA")
    return commit_sha


def _create_change_request_unlocked(
    read_client: GitHubReadClient,
    write_client: GitHubWriteClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Create a PR or update the same task PR; never writes to the canonical branch."""
    alias = payload.get("repo")
    if alias not in REPOSITORIES:
        raise ValueError("repo must be lite or plus")
    repository = REPOSITORIES[alias]
    files = sorted(payload["files"], key=lambda item: item["path"])
    validate_change_paths(files)

    fingerprint_input = {
        "repo": alias,
        "files": [
            {
                "path": item["path"],
                "content_sha256": hashlib.sha256(item["content"].encode("utf-8")).hexdigest(),
            }
            for item in files
        ],
    }
    operation_id = hashlib.sha256(
        json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    requested_change_id = payload.get("change_id")
    if requested_change_id is None:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload.get("title", "")).strip()).strip("-._").lower()
        slug = re.sub(r"\.{2,}", "-", slug)[:30].rstrip("-.")
        requested_change_id = f"{slug}-{operation_id[:10]}" if slug else f"auto-{operation_id[:16]}"
    if (
        not isinstance(requested_change_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}", requested_change_id)
        or ".." in requested_change_id
        or requested_change_id.endswith(".")
        or requested_change_id.lower().endswith(".lock")
    ):
        raise ValueError("change_id must be a valid stable branch suffix (letters, numbers, dot, underscore or hyphen; max 48 characters; no '..', trailing dot, or .lock suffix)")
    branch_name = f"bridge-{requested_change_id}"

    pointers, _defaults = resolve_canonical_branches(read_client)
    canonical_branch = pointers[alias]
    current_head = read_client.get_ref_sha(repository, canonical_branch)

    # Reusing already-integrated content takes precedence over any attempt to create a branch.
    if _same_files_on_ref(read_client, repository, canonical_branch, files):
        return {
            "repo": alias,
            "repository_full_name": repository,
            "decision": "REUSE",
            "action_taken": False,
            "canonical_branch": canonical_branch,
            "current_head_sha": current_head,
            "operation_id": operation_id,
            "change_id": requested_change_id,
            "branch": None,
            "pull_request_number": None,
            "pull_request_url": None,
            "detail": "Every requested file already matches the canonical branch; no branch, PR, commit, or Actions run was created.",
        }

    # The stable change_id selects the task PR. Later corrections with the same ID update
    # its head, so PR checks re-run once on the new code instead of creating another PR.
    query = {
        "state": "open",
        "head": f"c2melendez:{branch_name}",
        "base": canonical_branch,
        "per_page": 100,
    }
    existing_prs = write_client.get_json(f"repos/{repository}/pulls", params=query)
    open_pr_list = existing_prs if isinstance(existing_prs, list) else []
    existing_pr = next(
        (
            pr for pr in open_pr_list
            if pr.get("head", {}).get("ref") == branch_name
            and pr.get("base", {}).get("ref") == canonical_branch
        ),
        None,
    )

    if existing_pr is not None:
        pr_number = int(existing_pr["number"])
        branch_ref = write_client.get_json(
            f"repos/{repository}/git/ref/heads/{branch_name}", allow_404=True
        )
        branch_head = str(branch_ref.get("object", {}).get("sha", "")) if isinstance(branch_ref, dict) else ""
        recorded_pr_head = str(existing_pr.get("head", {}).get("sha", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", branch_head) or branch_head != recorded_pr_head:
            raise StateMismatchError("The bridge PR branch moved during reconciliation; refresh the PR head before updating it.")

        if _same_files_on_ref(read_client, repository, branch_name, files):
            return {
                "repo": alias,
                "repository_full_name": repository,
                "decision": "PR_REUSED",
                "action_taken": False,
                "canonical_branch": canonical_branch,
                "current_head_sha": current_head,
                "operation_id": operation_id,
                "change_id": requested_change_id,
                "branch": branch_name,
                "branch_head_sha": branch_head,
                "pull_request_number": pr_number,
                "pull_request_url": existing_pr.get("html_url"),
                "detail": "The same task PR already contains the requested file contents; no commit or duplicate Actions run was created.",
            }

        if payload["expected_head_sha"].lower() != current_head.lower():
            raise StateMismatchError(
                f"Stale change base: expected {payload['expected_head_sha']}, observed canonical HEAD {current_head}. "
                "Refresh current state before updating the existing PR."
            )

        commit_sha = _create_git_commit(
            write_client,
            repository,
            branch_head,
            files,
            f"{payload.get('commit_message') or ('fix(bridge): update ' + requested_change_id)} (bridge {operation_id[:12]})",
        )
        # force=false and a compare of the observed head avoid overwriting a concurrent
        # commit. If the ref moved, GitHub rejects the non-fast-forward update.
        write_client.patch_json(
            f"repos/{repository}/git/refs/heads/{branch_name}",
            {"sha": commit_sha, "force": False},
            expected=(200,),
        )
        return {
            "repo": alias,
            "repository_full_name": repository,
            "decision": "PR_UPDATED",
            "action_taken": True,
            "canonical_branch": canonical_branch,
            "current_head_sha": current_head,
            "operation_id": operation_id,
            "change_id": requested_change_id,
            "branch": branch_name,
            "branch_head_sha": commit_sha,
            "pull_request_number": pr_number,
            "pull_request_url": existing_pr.get("html_url"),
            "detail": "Updated the existing task PR with one new commit; its pull_request checks must validate the new head. No duplicate PR was created.",
        }

    if payload["expected_head_sha"].lower() != current_head.lower():
        raise StateMismatchError(
            f"Stale change base: expected {payload['expected_head_sha']}, observed canonical HEAD {current_head}. "
            "Refresh current state and regenerate the patch."
        )

    historical_prs = write_client.get_json(
        f"repos/{repository}/pulls",
        params={"state": "all", "head": f"c2melendez:{branch_name}", "base": canonical_branch, "per_page": 100},
    )
    historical_pr_list = historical_prs if isinstance(historical_prs, list) else []
    if any(pr.get("head", {}).get("ref") == branch_name for pr in historical_pr_list):
        raise StateMismatchError(
            f"change_id {requested_change_id!r} already belongs to a closed PR; choose a new change_id for this new task."
        )

    branch_ref = write_client.get_json(f"repos/{repository}/git/ref/heads/{branch_name}", allow_404=True)
    if branch_ref is None:
        branch_head = _create_git_commit(
            write_client,
            repository,
            current_head,
            files,
            payload.get("commit_message") or f"chore(bridge): {payload['title']}",
        )
        write_client.post_json(
            f"repos/{repository}/git/refs",
            {"ref": f"refs/heads/{branch_name}", "sha": branch_head},
        )
    else:
        branch_head = str(branch_ref.get("object", {}).get("sha", ""))
        if not re.fullmatch(r"[0-9a-f]{40}", branch_head):
            raise GitHubApiError(502, "An existing bridge branch has an invalid HEAD SHA")
        if not _same_files_on_ref(read_client, repository, branch_name, files):
            new_head = _create_git_commit(
                write_client,
                repository,
                branch_head,
                files,
                payload.get("commit_message") or f"chore(bridge): recover {payload['title']}",
            )
            write_client.patch_json(
                f"repos/{repository}/git/refs/heads/{branch_name}",
                {"sha": new_head, "force": False},
                expected=(200,),
            )
            branch_head = new_head

    body_base = payload.get("body") or ""
    body = (
        f"{body_base.rstrip()}\n\n---\nBridge operation: {operation_id}\n"
        f"Stable change_id: {requested_change_id}\n"
        f"Expected canonical base: {current_head}\n"
        f"Files: {', '.join(item['path'] for item in files)}\n\n"
        "GitHub pull_request workflows validate this change. Do not dispatch the same gate manually while those checks are running."
    ).strip()
    pr = write_client.post_json(
        f"repos/{repository}/pulls",
        {
            "title": payload["title"],
            "head": branch_name,
            "base": canonical_branch,
            "body": body,
            "draft": bool(payload.get("draft", False)),
        },
    )
    return {
        "repo": alias,
        "repository_full_name": repository,
        "decision": "PR_CREATED",
        "action_taken": True,
        "canonical_branch": canonical_branch,
        "current_head_sha": current_head,
        "operation_id": operation_id,
        "change_id": requested_change_id,
        "branch": branch_name,
        "branch_head_sha": branch_head,
        "pull_request_number": int(pr["number"]),
        "pull_request_url": pr.get("html_url"),
        "detail": "Created one branch and PR for the task; GitHub pull_request workflows validate it, without a duplicate gate dispatch.",
    }

_CHANGE_LOCKS = {"lite": threading.Lock(), "plus": threading.Lock()}


def create_change_request(
    read_client: GitHubReadClient,
    write_client: GitHubWriteClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Serialize concurrent requests for the same repository."""
    alias = payload.get("repo")
    if alias not in REPOSITORIES:
        raise ValueError("repo must be lite or plus")
    with _CHANGE_LOCKS[alias]:
        return _create_change_request_unlocked(read_client, write_client, payload)



def _pull_request_is_prose_only(
    read_client: GitHubReadClient,
    write_client: GitHubWriteClient,
    repository: str,
    canonical_branch: str,
    pr_number: int,
    pr: dict[str, Any],
) -> bool:
    expected_count = pr.get("changed_files")
    if not isinstance(expected_count, int) or expected_count < 1:
        return False
    files = write_client.get_json(
        f"repos/{repository}/pulls/{pr_number}/files",
        params={"per_page": 100},
    )
    if not isinstance(files, list) or len(files) != expected_count:
        raise StateMismatchError("Pull request file listing is incomplete; cannot apply the prose-only no-run exception.")
    state_text, _ = read_client.get_content_text(repository, CONTINUITY_PATH, canonical_branch)
    try:
        state = json.loads(state_text)
    except json.JSONDecodeError as exc:
        raise StateMismatchError("Current continuity.json is invalid; cannot apply a prose-only merge exception.") from exc
    allowed_paths = state.get("prose_only_paths", [])
    if not isinstance(allowed_paths, list) or not allowed_paths:
        return False
    allow = {item for item in allowed_paths if isinstance(item, str)}
    changed_paths = {item.get("filename") for item in files if isinstance(item, dict)}
    return bool(changed_paths) and len(changed_paths) == expected_count and changed_paths.issubset(allow)


def merge_pull_request(
    read_client: GitHubReadClient,
    write_client: GitHubWriteClient,
    alias: str,
    pr_number: int,
    expected_head_sha: str,
) -> dict[str, Any]:
    repository = REPOSITORIES[alias]
    if pr_number <= 0:
        raise ValueError("Pull request number must be positive")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", expected_head_sha):
        raise ValueError("expected_head_sha must be a full 40-character commit SHA")

    pointers, _defaults = resolve_canonical_branches(read_client)
    canonical_branch = pointers[alias]
    pr = write_client.get_json(f"repos/{repository}/pulls/{pr_number}", allow_404=True)
    if pr is None:
        raise GitHubApiError(404, "Pull request not found")
    if pr.get("state") == "closed" and pr.get("merged") is True:
        return {
            "repo": alias,
            "repository_full_name": repository,
            "decision": "ALREADY_MERGED",
            "action_taken": False,
            "pull_request_number": pr_number,
            "pull_request_url": pr.get("html_url"),
            "head_sha": pr.get("head", {}).get("sha"),
            "merge_commit_sha": pr.get("merge_commit_sha"),
            "validated_check_runs": [],
            "detail": "The pull request is already merged; no new operation was performed.",
        }
    if pr.get("state") != "open":
        raise StateMismatchError("Only an open pull request can be merged")
    if pr.get("draft") is True:
        raise StateMismatchError("Draft pull requests cannot be merged by the bridge")
    if pr.get("base", {}).get("ref") != canonical_branch:
        raise StateMismatchError(f"Pull request base is not the current canonical branch {canonical_branch}")
    if pr.get("head", {}).get("repo", {}).get("full_name") != repository:
        raise StateMismatchError("Cross-repository pull requests are not accepted by the bridge merge route")
    current_pr_head = pr.get("head", {}).get("sha")
    if current_pr_head != expected_head_sha:
        raise StateMismatchError(
            f"Pull request head moved: expected {expected_head_sha}, observed {current_pr_head}. Re-read checks before merging."
        )
    if pr.get("mergeable") is not True:
        raise StateMismatchError("GitHub has not confirmed that the pull request is mergeable; retry after refreshing its state")

    prose_only = _pull_request_is_prose_only(
        read_client, write_client, repository, canonical_branch, pr_number, pr
    )
    checks_payload = write_client.get_json(
        f"repos/{repository}/commits/{expected_head_sha}/check-runs",
        params={"per_page": 100},
    )
    checks = checks_payload.get("check_runs", [])
    total_count = int(checks_payload.get("total_count", len(checks)))
    if len(checks) < total_count:
        raise StateMismatchError("Check-run evidence is paginated; refresh the complete check list before merging")

    accepted = {"success", "skipped", "neutral"}
    incomplete = [
        item for item in checks
        if item.get("status") != "completed" or item.get("conclusion") not in accepted
    ]
    if incomplete:
        names = ", ".join(str(item.get("name", "unnamed")) for item in incomplete[:8])
        raise StateMismatchError(f"PR checks are pending or unsuccessful: {names}")
    successful_checks = [
        item for item in checks
        if item.get("status") == "completed" and item.get("conclusion") == "success"
    ]
    if not successful_checks and not prose_only:
        raise StateMismatchError(
            "No successful check exists for this PR head, and the changed paths are not all declared prose-only."
        )
    if total_count == 0 and not prose_only:
        raise StateMismatchError("No check-run evidence exists for this exact PR head; refusing an unvalidated merge")
    check_names = sorted({str(item.get("name", "")) for item in checks if item.get("name")})
    result = write_client.put_json(
        f"repos/{repository}/pulls/{pr_number}/merge",
        {"sha": expected_head_sha, "merge_method": "squash"},
        expected=(200,),
    )
    if result.get("merged") is not True:
        raise GitHubApiError(502, "GitHub did not confirm the pull request merge")
    return {
        "repo": alias,
        "repository_full_name": repository,
        "decision": "MERGED",
        "action_taken": True,
        "pull_request_number": pr_number,
        "pull_request_url": pr.get("html_url"),
        "head_sha": expected_head_sha,
        "merge_commit_sha": result.get("sha"),
        "validated_check_runs": check_names,
        "detail": "GitHub confirmed the merge after every check run on the exact PR head was complete and successful or explicitly skipped/neutral.",
    }


# A single in-process lock prevents two simultaneous API requests from dispatching
# the same repository's runner. After restarts, the live Actions history and the
# runner's committed result records provide the durable guard.
import time
from datetime import datetime, timezone

from .config import RUNNER_WORKFLOW_PATH
from .service import get_gate_plan, get_runner_records


_GATE_LOCKS = {"lite": threading.Lock(), "plus": threading.Lock()}
_RECENT_DISPATCHES: dict[tuple[str, str, str], float] = {}
_DISPATCH_COOLDOWN_SECONDS = 120


def execute_gate_request(
    read_client: GitHubReadClient,
    write_client: GitHubWriteClient,
    alias: str,
    gate_name: str,
    *,
    retry_after_review: bool = False,
    retry_reason: str | None = None,
) -> dict[str, Any]:
    """Dispatch a gate only when planning authorizes it; retries require explicit failure review.

    All other planner decisions are returned without writing to GitHub. A PR-based
    change operation is intentionally separate: PR checks should not be duplicated
    by also dispatching the same gate manually.
    """
    if alias not in REPOSITORIES:
        raise ValueError("repo must be lite or plus")

    if retry_after_review and (not retry_reason or len(retry_reason.strip()) < 20):
        raise ValueError("An intentional retry requires a review reason of at least 20 characters")

    with _GATE_LOCKS[alias]:
        plan = get_gate_plan(read_client, alias, gate_name)
        gate_plan = plan["gate"]
        decision = gate_plan["decision"]
        base_response = {
            "repo": alias,
            "repository_full_name": plan["repository_full_name"],
            "gate": gate_name,
            "canonical_branch": plan["canonical_branch"],
            "observed_head": plan["observed_head"],
            "input_fingerprint": gate_plan.get("input_fingerprint"),
            "planner_decision": decision,
            "action_taken": False,
            "runner_run_id": None,
            "runner_run_url": None,
        }

        if plan["state_status"] != "CURRENT":
            base_response["decision"] = "REFRESH"
            base_response["detail"] = plan.get("reconciliation_action") or "Continuity state is not current; refresh before any dispatch."
            return base_response
        if decision != "RUN" and not (decision == "DIAGNOSE" and retry_after_review):
            base_response["decision"] = decision
            base_response["detail"] = gate_plan.get("reason") or "Planner decision is not RUN; no GitHub Actions workflow was dispatched."
            return base_response

        repository = REPOSITORIES[alias]
        branch = plan["canonical_branch"]
        runs, history_complete = read_client.get_workflow_runs(repository, RUNNER_WORKFLOW_PATH, branch)
        if not history_complete:
            base_response["decision"] = "REFRESH"
            base_response["detail"] = "Runner workflow history is incomplete; refusing to risk a duplicate dispatch."
            return base_response

        active_statuses = {"queued", "in_progress", "requested", "waiting", "pending"}
        active = [
            run for run in runs
            if run.get("event") == "workflow_dispatch" and run.get("status") in active_statuses
            and run.get("head_branch") == branch
        ]
        if active:
            latest = max(active, key=lambda run: str(run.get("created_at", "")))
            run_id = str(latest.get("id", ""))
            base_response.update({
                "decision": "WAIT",
                "runner_run_id": run_id if run_id.isdigit() else None,
                "runner_run_url": f"https://github.com/{repository}/actions/runs/{run_id}" if run_id.isdigit() else None,
                "detail": "A Certification Runner workflow is already active for this canonical branch; no second dispatch was sent.",
            })
            return base_response

        dispatch_key = (alias, gate_name, str(plan["observed_head"]))
        last_dispatch = _RECENT_DISPATCHES.get(dispatch_key)
        if last_dispatch is not None and time.monotonic() - last_dispatch < _DISPATCH_COOLDOWN_SECONDS:
            base_response.update({
                "decision": "WAIT",
                "detail": "A dispatch request for this gate and HEAD was just submitted; allow GitHub Actions to index it before retrying.",
            })
            return base_response

        # A previous runner request for this exact base/gate is not silently retried.
        # Inspect its result and diagnose before changing state or intentionally requesting
        # a new run; this protects against repeated dispatches after a partial failure.
        records = get_runner_records(read_client, repository, branch, limit=15)
        prior = [
            record for record in records
            if str(record.get("gate", "")) == gate_name
            and str(record.get("base_sha", "")).lower() == str(plan["observed_head"]).lower()
            and str(record.get("decision", "")).upper() in {"DISPATCHED", "NOT_DISPATCHED"}
        ]
        if prior:
            latest = max(prior, key=lambda record: str(record.get("recorded_at", "")))
            prior_decision = str(latest.get("decision", "")).upper()
            prior_status = str(latest.get("dispatched_status", "")).lower()
            prior_conclusion = str(latest.get("dispatched_conclusion", "")).lower()
            retryable_failure = (
                prior_decision == "NOT_DISPATCHED"
                or (
                    prior_status == "completed"
                    and prior_conclusion in {"failure", "cancelled", "timed_out", "startup_failure", "action_required", "stale"}
                )
            )
            if not retry_after_review or not retryable_failure:
                base_response.update({
                    "decision": "DIAGNOSE",
                    "detail": (
                        "A prior runner record exists for this gate and HEAD. Do not repeat it automatically; "
                        "a deliberate retry is allowed only after reviewing a recorded failed or not-dispatched attempt."
                    ),
                    "prior_runner_run_id": str(latest.get("runner_run_id", "")),
                })
                return base_response
        elif decision == "DIAGNOSE":
            base_response.update({
                "decision": "DIAGNOSE",
                "detail": "No matching runner record could be tied to this HEAD; inspect live Actions history before retrying.",
            })
            return base_response

        inputs = {"target_repo": alias, "gate": gate_name, "dry_run": "false"}
        # Set the local idempotency window before issuing the network request. If the
        # client times out after GitHub accepted the dispatch, a retry will be suppressed.
        _RECENT_DISPATCHES[dispatch_key] = time.monotonic()
        write_client.dispatch_workflow(repository, RUNNER_WORKFLOW_PATH, branch, inputs)
        base_response.update({
            "decision": "DISPATCHED",
            "action_taken": True,
            "detail": (
                "GitHub accepted the workflow_dispatch request. Query live runner/run evidence before interpreting it "
                "as completed; dispatch acceptance is not a test PASS."
            ),
        })
        # Resolve the run ID if the GitHub API has indexed it already. Failure to find it
        # is reported as pending discovery, never treated as proof that no dispatch occurred.
        refreshed, _complete = read_client.get_workflow_runs(repository, RUNNER_WORKFLOW_PATH, branch)
        candidates = [
            run for run in refreshed
            if run.get("event") == "workflow_dispatch" and run.get("head_branch") == branch
            and run.get("head_sha") == plan["observed_head"]
        ]
        if candidates:
            latest = max(candidates, key=lambda run: str(run.get("created_at", "")))
            run_id = str(latest.get("id", ""))
            if run_id.isdigit():
                base_response["runner_run_id"] = run_id
                base_response["runner_run_url"] = f"https://github.com/{repository}/actions/runs/{run_id}"
        return base_response
