from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import app, get_github

BRANCH = "qa/syntax-audit-in625-a1"


def continuity(repo: str):
    return {
        "schema_version": 1,
        "revision": 7,
        "repository": repo,
        "canonical_branch": BRANCH,
        "technical_baseline_sha": "b" * 40,
        "baseline_case": "EXAMPLE",
        "baseline_status": "NO_EJECUTADO",
        "global_certification": "OPEN",
        "prose_only_paths": ["qa/certification/CURRENT_STATE.md"],
        "gates": {
            "cumulative": {"workflow": ".github/workflows/in625-cumulative-certification.yml", "inputs": ["*"], "acceptance_revision": "v1"},
            "security": {"workflow": ".github/workflows/certification-dependency-gates.yml", "inputs": ["*"], "acceptance_revision": "v1", "allow_reuse": False},
        },
        "next_action": {"id": "TEST-NEXT", "status": "READY", "action": "Read-only example next action"},
        "blockers": [{"id": "SAMPLE", "status": "BLOCKED"}],
    }


class FakeGitHub:
    def __init__(self):
        self.repo_ids = {
            "c2melendez/precision-lab-lite": "lite",
            "c2melendez/precision-lab-plus": "plus",
        }
        self.states = {repo: continuity(repo) for repo in self.repo_ids}
        self.workflow_run_reads = 0
        self.records = {
            "lite": {
                "schema_version": 2,
                "operation": "docs",
                "repo": "c2melendez/precision-lab-lite",
                "canonical_branch": BRANCH,
                "base_sha": "c" * 40,
                "patch": {"url": "https://gist.example/private-package", "sha256": "d" * 64, "bytes": 15},
                "commits": [{"sha": "e" * 40, "subject": "docs(test): sample [skip ci]"}],
                "paths": ["qa/certification/CURRENT_STATE.md"],
                "patch_head_sha": "e" * 40,
                "decision": "PUBLISHED",
                "runner_run_id": "1234",
                "runner_run_url": "https://evil.example/fake-runner-url",
                "dispatched_run_id": "555",
                "dispatched_run_url": "https://evil.example/fake-child-url",
                "dispatched_status": "completed",
                "dispatched_conclusion": "success",
                "recorded_at": "2026-10-26T07:40:15+00:00",
            }
        }

    def get_repo(self, repository):
        return {"default_branch": "main"}

    def get_content_text(self, repository, path, ref):
        alias = self.repo_ids[repository]
        if path == "qa/certification/README.md" and ref == "main":
            return f"# Entry\n\n## Rama canónica activa\n`{BRANCH}`\n", "1" * 40
        if path == "qa/certification/continuity.json":
            return json.dumps(self.states[repository]), "2" * 40
        if path == ".github/workflows/certification-runner.yml" and ref == "main":
            return "name: Certification Runner\n# Versión 3. Docs operation.\n", "3" * 40
        if path == "qa/certification/runner/1234.json" and alias == "lite":
            return json.dumps(self.records[alias]), "4" * 40
        raise AssertionError(f"Unexpected file read: {repository} {path} {ref}")

    def get_ref_sha(self, repository, branch):
        assert branch == BRANCH
        return "a" * 40

    def get_commit_tree(self, repository, sha):
        # Same tree as baseline means the continuity state remains current.
        return {
            ".github/workflows/in625-cumulative-certification.yml": ["100644", "blob", "1" * 40],
            "src/math.py": ["100644", "blob", "2" * 40],
        }

    def list_directory(self, repository, path, ref):
        if path == "qa/certification/runner":
            if self.repo_ids[repository] == "lite":
                return [{"name": "1234.json", "type": "file"}]
            return []
        return []

    def get_content_json(self, repository, path, ref, *, allow_404=False):
        return None

    def get_workflow_runs(self, repository, workflow_path, branch):
        self.workflow_run_reads += 1
        return [], True

    def get_run(self, repository, run_id):
        if run_id != 555:
            return None
        return {
            "id": 555,
            "name": "Example Gate",
            "path": ".github/workflows/in625-cumulative-certification.yml@refs/heads/qa/syntax-audit-in625-a1",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "success",
            "head_branch": BRANCH,
            "head_sha": "a" * 40,
            "html_url": "https://github.com/c2melendez/precision-lab-lite/actions/runs/555",
            "created_at": "2026-10-26T07:00:00Z",
            "updated_at": "2026-10-26T07:01:00Z",
            "run_attempt": 1,
        }

    def get_run_jobs(self, repository, run_id, attempt):
        return [{"id": 77, "name": "test", "status": "completed", "conclusion": "success", "steps": [
            {"name": "Run tests", "status": "completed", "conclusion": "success"}
        ]}]


def test_health_is_public_and_does_not_query_github():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["read_only"] is True


def test_api_key_required(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    client = TestClient(app)
    response = client.get("/v1/status?repo=lite")
    assert response.status_code == 401


def test_status_is_read_only_and_sanitizes_document_package_url(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    fake = FakeGitHub()
    app.dependency_overrides[get_github] = lambda: fake
    try:
        client = TestClient(app)
        response = client.get("/v1/status?repo=lite", headers={"X-API-Key": "test-key"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["repositories"][0]["canonical_branch"] == BRANCH
        assert payload["repositories"][0]["state_status"] == "CURRENT"
        assert payload["repositories"][0]["next_action"]["id"] == "TEST-NEXT"
        record = payload["repositories"][0]["latest_runner_record"]
        assert record["operation"] == "docs"
        assert "patch" in record
        assert "url" not in record["patch"]
        assert "gist.example/private-package" not in response.text
        assert "evil.example" not in response.text
        assert record["runner_run_url"] == "https://github.com/c2melendez/precision-lab-lite/actions/runs/1234"
        assert record["dispatched_run_url"] == "https://github.com/c2melendez/precision-lab-lite/actions/runs/555"
        assert not any("skip-Actions marker" in warning for warning in payload["warnings"])
    finally:
        app.dependency_overrides.clear()


def test_status_warns_on_skip_marker_when_docs_package_changes_gate_inputs(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    fake = FakeGitHub()
    # A historical docs publication that also changes a technical gate input
    # must be surfaced for reconciliation; prose-only paths should not warn.
    fake.records["lite"]["paths"] = ["src/math.py"]
    app.dependency_overrides[get_github] = lambda: fake
    try:
        client = TestClient(app)
        response = client.get("/v1/status?repo=lite", headers={"X-API-Key": "test-key"})
        assert response.status_code == 200, response.text
        warnings = response.json()["warnings"]
        assert any("skip-Actions marker" in warning for warning in warnings)
        assert any("src/math.py" in warning for warning in warnings)
    finally:
        app.dependency_overrides.clear()


def test_no_mutating_operations_are_exposed():
    schema = app.openapi()
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey", "in": "header", "name": "X-API-Key"
    }
    assert schema["paths"]["/v1/status"]["get"]["security"] == [{"APIKeyHeader": []}]
    allowed = {"get", "parameters", "summary", "description", "servers"}
    for path, definition in schema["paths"].items():
        operations = {key.lower() for key in definition if key.lower() in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}}
        assert operations <= {"get"}, f"Non-read-only method exposed at {path}: {operations}"


def test_gate_plan_is_advisory_only_and_plus_security_is_hard_blocked(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    fake = FakeGitHub()
    app.dependency_overrides[get_github] = lambda: fake
    try:
        client = TestClient(app)
        plan_response = client.get(
            "/v1/gates/cumulative/plan?repo=lite", headers={"X-API-Key": "test-key"}
        )
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["read_only"] is True
        assert plan["gate"]["action_taken"] is False
        assert plan["gate"]["decision"] == "RUN"

        blocked = client.get(
            "/v1/gates/security/plan?repo=plus", headers={"X-API-Key": "test-key"}
        )
        assert blocked.status_code == 400
        assert blocked.json()["read_only"] is True
        assert "hard-blocked" in blocked.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_child_run_finds_parent_record_without_exposing_patch_url(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    fake = FakeGitHub()
    app.dependency_overrides[get_github] = lambda: fake
    try:
        client = TestClient(app)
        response = client.get("/v1/runs/555?repo=lite", headers={"X-API-Key": "test-key"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["read_only"] is True
        assert payload["record_relation"] == "dispatched_by"
        assert payload["runner_record"]["runner_run_id"] == "1234"
        assert "gist.example/private-package" not in response.text
        assert payload["github_run"]["jobs"][0]["steps"][0]["name"] == "Run tests"
        assert "success is not certification PASS" in payload["interpretation_note"]
    finally:
        app.dependency_overrides.clear()


def test_status_suppresses_next_action_when_technical_baseline_differs(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    fake = FakeGitHub()
    original = fake.get_commit_tree

    def diverging_tree(repository, sha):
        tree = original(repository, sha)
        if sha == "b" * 40:
            tree = dict(tree)
            tree["src/math.py"] = ["100644", "blob", "9" * 40]
        return tree

    fake.get_commit_tree = diverging_tree
    app.dependency_overrides[get_github] = lambda: fake
    try:
        client = TestClient(app)
        response = client.get("/v1/status?repo=lite", headers={"X-API-Key": "test-key"})
        assert response.status_code == 200, response.text
        item = response.json()["repositories"][0]
        assert item["state_status"] == "RECONCILE"
        assert item["next_action"] is None
        assert item["reconciliation_action"]
    finally:
        app.dependency_overrides.clear()


def test_openapi_uses_a_valid_placeholder_when_public_base_url_is_blank(monkeypatch):
    from app.main import custom_openapi

    old_schema = app.openapi_schema
    try:
        monkeypatch.setenv("PUBLIC_BASE_URL", "")
        app.openapi_schema = None
        schema = custom_openapi()
        assert schema["servers"][0]["url"] == "https://REPLACE-WITH-READONLY-SERVICE-HOST"
    finally:
        app.openapi_schema = old_schema


def test_gate_plan_returns_refresh_before_reading_workflow_history_if_baseline_differs(monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "test-key")
    fake = FakeGitHub()
    original = fake.get_commit_tree

    def diverging_tree(repository, sha):
        tree = original(repository, sha)
        if sha == "b" * 40:
            tree = dict(tree)
            tree["src/math.py"] = ["100644", "blob", "9" * 40]
        return tree

    fake.get_commit_tree = diverging_tree
    app.dependency_overrides[get_github] = lambda: fake
    try:
        client = TestClient(app)
        response = client.get(
            "/v1/gates/cumulative/plan?repo=lite",
            headers={"X-API-Key": "test-key"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["state_status"] == "RECONCILE"
        assert payload["next_action"] is None
        assert payload["gate"]["decision"] == "REFRESH"
        assert payload["gate"]["action_taken"] is False
        assert payload["history_complete"] is False
        assert payload["history_observed_runs"] == 0
        assert fake.workflow_run_reads == 0
    finally:
        app.dependency_overrides.clear()
