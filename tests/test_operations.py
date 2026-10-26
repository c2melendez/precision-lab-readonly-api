from __future__ import annotations

import base64

import pytest

import app.operations as operations
from app.github_api import GitHubApiError
from app.operations import create_change_request, merge_pull_request, validate_change_paths
from app.service import StateMismatchError

BRANCH = "qa/syntax-audit-in625-a1"
HEAD = "a" * 40


class FakeRead:
    def __init__(self, files=None):
        self.files = files or {}

    def get_repo(self, repository):
        return {"default_branch": "main"}

    def get_content_text(self, repository, path, ref):
        if path == "qa/certification/README.md" and ref == "main":
            return f"# Entry\n\n## Rama canónica activa\n`{BRANCH}`\n", "1" * 40
        raise AssertionError(f"Unexpected file read: {repository} {path} {ref}")

    def get_ref_sha(self, repository, branch):
        assert branch == BRANCH
        return HEAD

    def get_content_json(self, repository, path, ref, *, allow_404=False):
        assert ref == BRANCH
        if path not in self.files:
            return None
        value = base64.b64encode(self.files[path].encode("utf-8")).decode("ascii")
        return {"type": "file", "encoding": "base64", "content": value, "sha": "2" * 40}


class UnusedWriter:
    def __getattr__(self, name):
        raise AssertionError(f"No GitHub write was expected: {name}")


def test_change_path_validation_blocks_traversal_secrets_and_runner_forgery():
    for path in ("../README.md", "/tmp/file.md", ".env", "app/private.pem", "qa/certification/runner/123.json"):
        with pytest.raises(ValueError):
            validate_change_paths([{"path": path, "content": "x"}])


def test_change_path_validation_accepts_safe_docs_and_example_env():
    validate_change_paths([
        {"path": "qa/certification/CURRENT_STATE.md", "content": "Updated state"},
        {"path": ".env.example", "content": "SERVICE_API_KEY=\n"},
    ])


def test_identical_change_returns_reuse_without_creating_a_branch_or_pr():
    path = "qa/certification/CURRENT_STATE.md"
    content = "# Current state\nNo work needed\n"
    read = FakeRead({path: content})
    result = create_change_request(
        read,
        UnusedWriter(),
        {
            "repo": "lite",
            "expected_head_sha": HEAD,
            "title": "Refresh current certification state",
            "body": "test",
            "commit_message": None,
            "draft": False,
            "files": [{"path": path, "content": content}],
        },
    )
    assert result["decision"] == "REUSE"
    assert result["action_taken"] is False
    assert result["pull_request_number"] is None


def test_dispatch_runs_once_and_immediate_retry_waits(monkeypatch):
    class Read:
        def get_workflow_runs(self, repository, workflow_path, branch):
            assert repository == "c2melendez/precision-lab-lite"
            assert workflow_path == operations.RUNNER_WORKFLOW_PATH
            assert branch == BRANCH
            return [], True

    class Writer:
        def __init__(self):
            self.dispatch_calls = 0

        def dispatch_workflow(self, repository, workflow_path, branch, inputs):
            self.dispatch_calls += 1
            assert repository == "c2melendez/precision-lab-lite"
            assert workflow_path == operations.RUNNER_WORKFLOW_PATH
            assert branch == BRANCH
            assert inputs == {"target_repo": "lite", "gate": "cumulative", "dry_run": "false"}

    read, writer = Read(), Writer()
    monkeypatch.setattr(operations, "get_gate_plan", lambda *_: {
        "state_status": "CURRENT",
        "repository_full_name": "c2melendez/precision-lab-lite",
        "canonical_branch": BRANCH,
        "observed_head": HEAD,
        "gate": {"decision": "RUN", "input_fingerprint": "f" * 64, "reason": "No matching run"},
    })
    monkeypatch.setattr(operations, "get_runner_records", lambda *_a, **_k: [])
    operations._RECENT_DISPATCHES.pop(("lite", "cumulative", HEAD), None)

    first = operations.execute_gate_request(read, writer, "lite", "cumulative")
    second = operations.execute_gate_request(read, writer, "lite", "cumulative")

    assert first["decision"] == "DISPATCHED"
    assert first["action_taken"] is True
    assert second["decision"] == "WAIT"
    assert second["action_taken"] is False
    assert writer.dispatch_calls == 1


def test_merge_rejects_failed_check_without_writing():
    class Read:
        def get_repo(self, repository):
            return {"default_branch": "main"}

        def get_content_text(self, repository, path, ref):
            if path == "qa/certification/README.md" and ref == "main":
                return f"# Entry\n\n## Rama canónica activa\n`{BRANCH}`\n", "1" * 40
            raise AssertionError((repository, path, ref))

    class Writer:
        def __init__(self):
            self.merge_calls = 0

        def get_json(self, path, *, params=None, allow_404=False):
            if path.endswith("/pulls/7"):
                return {
                    "state": "open",
                    "merged": False,
                    "draft": False,
                    "mergeable": True,
                    "base": {"ref": BRANCH},
                    "head": {"sha": HEAD, "repo": {"full_name": "c2melendez/precision-lab-lite"}},
                    "html_url": "https://github.com/c2melendez/precision-lab-lite/pull/7",
                }
            if path.endswith(f"/commits/{HEAD}/check-runs"):
                return {"total_count": 1, "check_runs": [{"name": "CI", "status": "completed", "conclusion": "failure"}]}
            raise AssertionError(path)

        def put_json(self, *args, **kwargs):
            self.merge_calls += 1
            raise AssertionError("A failing check must prevent merging")

    writer = Writer()
    with pytest.raises(StateMismatchError, match="unsuccessful"):
        merge_pull_request(Read(), writer, "lite", 7, HEAD)
    assert writer.merge_calls == 0


def test_merge_allows_prose_only_change_without_new_actions():
    class Read:
        def get_repo(self, repository):
            return {"default_branch": "main"}

        def get_content_text(self, repository, path, ref):
            if path == "qa/certification/README.md" and ref == "main":
                return f"# Entry\n\n## Rama canónica activa\n`{BRANCH}`\n", "1" * 40
            if path == "qa/certification/continuity.json":
                return '{"prose_only_paths":["qa/certification/CURRENT_STATE.md"]}', "2" * 40
            raise AssertionError((repository, path, ref))

    class Writer:
        def __init__(self):
            self.merge_calls = 0

        def get_json(self, path, *, params=None, allow_404=False):
            if path.endswith("/pulls/8"):
                return {
                    "state": "open",
                    "merged": False,
                    "draft": False,
                    "mergeable": True,
                    "changed_files": 1,
                    "base": {"ref": BRANCH},
                    "head": {"sha": HEAD, "repo": {"full_name": "c2melendez/precision-lab-lite"}},
                    "html_url": "https://github.com/c2melendez/precision-lab-lite/pull/8",
                }
            if path.endswith("/pulls/8/files"):
                return [{"filename": "qa/certification/CURRENT_STATE.md"}]
            if path.endswith(f"/commits/{HEAD}/check-runs"):
                return {"total_count": 0, "check_runs": []}
            raise AssertionError(path)

        def put_json(self, path, body, *, expected=(200, 201)):
            self.merge_calls += 1
            assert path.endswith("/pulls/8/merge")
            assert body["sha"] == HEAD
            return {"merged": True, "sha": "e" * 40}

    writer = Writer()
    result = merge_pull_request(Read(), writer, "lite", 8, HEAD)
    assert result["decision"] == "MERGED"
    assert result["validated_check_runs"] == []
    assert writer.merge_calls == 1
