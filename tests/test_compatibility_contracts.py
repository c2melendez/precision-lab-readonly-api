from __future__ import annotations

import json
from pathlib import Path

from integrations.read_only_client import TOOL_SCHEMAS, gemini_function_declarations, openai_compatible_tool_schemas


ROOT = Path(__file__).resolve().parents[1]


def test_copilot_studio_swagger2_spec_is_read_only_and_has_expected_routes():
    spec_path = ROOT / "integrations" / "copilot-studio" / "precision-lab-swagger2.json"
    schema = json.loads(spec_path.read_text(encoding="utf-8"))

    assert schema["swagger"] == "2.0"
    assert schema["schemes"] == ["https"]
    assert schema["host"] == "precision-lab-readonly-api.onrender.com"
    assert set(schema["paths"]) == {
        "/healthz",
        "/v1/status",
        "/v1/gates/{gate}/plan",
        "/v1/runs/{run_id}",
    }
    for path, path_item in schema["paths"].items():
        operations = {
            method.lower()
            for method in path_item
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
        }
        assert operations <= {"get"}, f"Unexpected HTTP method in {path}: {operations}"
    for path in ("/v1/status", "/v1/gates/{gate}/plan", "/v1/runs/{run_id}"):
        assert schema["paths"][path]["get"]["security"] == [{"APIKeyHeader": []}]
    assert schema["securityDefinitions"]["APIKeyHeader"] == {
        "type": "apiKey",
        "name": "X-API-Key",
        "in": "header",
        "description": "Service API key. Configure as a secure connector secret; never include it in prompts.",
    }


def test_tool_contract_matches_both_provider_adapters():
    names = {item["name"] for item in TOOL_SCHEMAS}
    assert names == {
        "precision_lab_status",
        "precision_lab_gate_plan",
        "precision_lab_run_evidence",
    }
    assert {tool["function"]["name"] for tool in openai_compatible_tool_schemas()} == names
    assert {tool["name"] for tool in gemini_function_declarations()} == names
    combined_descriptions = " ".join(item["description"].lower() for item in TOOL_SCHEMAS)
    assert "never dispatches" in combined_descriptions
    assert "success" in combined_descriptions
    assert "certification pass" in combined_descriptions


def test_local_mcp_server_module_imports_and_exposes_a_run_entrypoint():
    from integrations.mcp_server import mcp

    assert callable(mcp.run)
