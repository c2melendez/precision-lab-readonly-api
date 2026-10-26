from datetime import datetime, timezone

import pytest

from app.planner import fingerprint, plan

BRANCH = "qa/syntax-audit-in625-a1"
REPO = "c2melendez/precision-lab-lite"
HEAD = "a" * 40
BASE = "b" * 40


def make_state():
    return {
        "schema_version": 1,
        "repository": REPO,
        "canonical_branch": BRANCH,
        "revision": 295,
        "technical_baseline_sha": BASE,
        "prose_only_paths": ["qa/certification/CURRENT_STATE.md"],
        "global_certification": "OPEN",
        "next_action": {"id": "EXAMPLE-NEXT", "status": "READY", "action": "Example only"},
        "blockers": [],
        "gates": {
            "cumulative": {
                "workflow": ".github/workflows/in625-cumulative-certification.yml",
                "inputs": ["*"],
                "acceptance_revision": "test-v1",
            }
        },
    }


def test_green_workflow_without_reviewed_logs_is_review_not_reuse():
    state = make_state()
    entries = {
        ".github/workflows/in625-cumulative-certification.yml": ["100644", "blob", "1" * 40],
        "src/math.py": ["100644", "blob", "2" * 40],
    }
    live = {
        "repository": REPO,
        "branch": BRANCH,
        "head_sha": HEAD,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "runs": [{
            "id": 123,
            "path": ".github/workflows/in625-cumulative-certification.yml",
            "head_sha": HEAD,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "success",
            "jobs": [],
            "logs_reviewed": False,
            "evidence_executed": False,
            "acceptance_revision": None,
        }],
    }
    result = plan(state, live, lambda sha: entries)
    assert result["state_status"] == "CURRENT"
    assert result["gates"][0]["decision"] == "REVIEW"
    assert "logs" in result["gates"][0]["reason"]
    assert result["next_action"]["id"] == "EXAMPLE-NEXT"


def test_stale_snapshot_is_rejected():
    state = make_state()
    live = {
        "repository": REPO,
        "branch": BRANCH,
        "head_sha": HEAD,
        "fetched_at": "2020-01-01T00:00:00+00:00",
        "runs": [],
    }
    with pytest.raises(ValueError, match="expired"):
        plan(state, live, lambda sha: {})


def test_fingerprint_excludes_explicit_prose_continuity_index_and_generated_runner_records():
    gate = {"workflow": ".github/workflows/x.yml", "inputs": ["*"], "acceptance_revision": "v1"}
    prose = ["qa/certification/CURRENT_STATE.md"]
    base = {
        ".github/workflows/x.yml": ["100644", "blob", "1" * 40],
        "qa/certification/CURRENT_STATE.md": ["100644", "blob", "2" * 40],
        "qa/certification/continuity.json": ["100644", "blob", "3" * 40],
        "qa/certification/runner/1.json": ["100644", "blob", "4" * 40],
    }
    changed_prose = dict(base)
    changed_prose["qa/certification/CURRENT_STATE.md"] = ["100644", "blob", "5" * 40]
    changed_index = dict(base)
    changed_index["qa/certification/continuity.json"] = ["100644", "blob", "6" * 40]
    changed_runner_record = dict(base)
    changed_runner_record["qa/certification/runner/1.json"] = ["100644", "blob", "7" * 40]
    assert fingerprint(base, gate, prose) == fingerprint(changed_prose, gate, prose)
    assert fingerprint(base, gate, prose) == fingerprint(changed_index, gate, prose)
    assert fingerprint(base, gate, prose) == fingerprint(changed_runner_record, gate, prose)

    # The exclusion is deliberately narrow: nonnumeric files, schemas, code,
    # and certification contracts under the same directory remain gate inputs.
    changed_runner_schema = dict(base)
    changed_runner_schema["qa/certification/runner/schema.json"] = ["100644", "blob", "8" * 40]
    assert fingerprint(base, gate, prose) != fingerprint(changed_runner_schema, gate, prose)

    changed_contract = dict(base)
    changed_contract["qa/certification/SY_P_23_CONVENTION.md"] = ["100644", "blob", "9" * 40]
    assert fingerprint(base, gate, prose) != fingerprint(changed_contract, gate, prose)


@pytest.mark.parametrize(
    "technical_path",
    [
        ".github/workflows/api-ci.yml",
        "scripts/certification/continuity.py",
        "app/planner.py",
        "app/service.py",
        "tests/test_planner.py",
        "qa/certification/fixtures/sg28-cases.jsonl",
        "qa/certification/SY_P_23_CONVENTION.md",
        "qa/certification/runner/schema.json",
        "requirements.txt",
        "requirements-dev.txt",
    ],
)
def test_fingerprint_changes_for_each_technical_input_category(technical_path):
    """Generated result records are excluded, but every technical input class is tracked."""
    gate = {
        "workflow": ".github/workflows/api-ci.yml",
        "inputs": ["*"],
        "acceptance_revision": "fingerprint-regression-v1",
    }
    prose = ["qa/certification/CURRENT_STATE.md"]
    entries = {
        ".github/workflows/api-ci.yml": ["100644", "blob", "1" * 40],
        technical_path: ["100644", "blob", "2" * 40],
    }
    changed = dict(entries)
    changed[technical_path] = ["100644", "blob", "3" * 40]

    assert fingerprint(entries, gate, prose) != fingerprint(changed, gate, prose)
