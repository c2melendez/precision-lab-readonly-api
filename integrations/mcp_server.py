"""Local stdio MCP adapter for Precision Lab's hosted read-only REST API.

The MCP process holds SERVICE_API_KEY in its own environment. The key is never
part of a tool schema, prompt, tool result, or server log. All tools map to HTTP
GET requests through integrations.read_only_client.
"""
from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from integrations.read_only_client import execute_readonly_tool

mcp = FastMCP(
    "Precision Lab Read-only",
    instructions=(
        "Read-only evidence access for Precision Lab Lite and Plus. "
        "Use precision_lab_status before selecting a gate when the exact gate id "
        "is not already grounded in trusted evidence. Gate plans are advisory only: "
        "RUN never dispatches a workflow. Never claim certification from an HTTP 200 "
        "or a successful GitHub Actions run alone. These tools cannot commit, push, "
        "dispatch, cancel, or rerun workflows, and cannot invoke Compass Bridge."
    ),
)


@mcp.tool()
def precision_lab_status(repo: Literal["lite", "plus", "both"] = "both") -> dict[str, Any]:
    """Read current certification continuity for Lite, Plus, or both repositories."""
    return execute_readonly_tool("precision_lab_status", {"repo": repo})


@mcp.tool()
def precision_lab_gate_plan(
    repo: Literal["lite", "plus"],
    gate: str,
    refresh: bool = True,
) -> dict[str, Any]:
    """Read an advisory plan for an exact known gate; this tool never runs it."""
    return execute_readonly_tool(
        "precision_lab_gate_plan",
        {"repo": repo, "gate": gate, "refresh": refresh},
    )


@mcp.tool()
def precision_lab_run_evidence(
    repo: Literal["lite", "plus"],
    run_id: str,
) -> dict[str, Any]:
    """Read raw GitHub Actions evidence for a numeric run id; success is not certification."""
    return execute_readonly_tool(
        "precision_lab_run_evidence",
        {"repo": repo, "run_id": run_id},
    )


if __name__ == "__main__":
    # MCP hosts launch this local server over stdio; no public MCP endpoint is opened.
    mcp.run(transport="stdio")
