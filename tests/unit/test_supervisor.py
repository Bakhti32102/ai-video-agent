"""Unit tests for the supervisor and core result/exception utilities."""

from __future__ import annotations

import pytest

from app.core.enums import AgentName, WorkflowPhase
from app.core.result import Result


def test_result_ok_and_fail() -> None:
    ok = Result.ok("data")
    assert ok.success and ok.data == "data"
    fail = Result.fail("err1", "err2")
    assert fail.is_failure and fail.errors == ["err1", "err2"]


def test_result_add_error_flips_success() -> None:
    r = Result.ok()
    r.add_error("late failure")
    assert not r.success


def test_result_to_dict_serializable() -> None:
    r = Result.fail("e", warnings=["w"], metadata={"k": 1})
    d = r.to_dict()
    assert d["success"] is False
    assert d["warnings"] == ["w"]
    assert d["metadata"] == {"k": 1}


@pytest.mark.asyncio
async def test_supervisor_retries_then_succeeds() -> None:
    from app.agents.supervisor import SupervisorAgent
    from app.mcp.client import McpClient

    sup = SupervisorAgent(McpClient(), max_retries=2)
    # Script split_scenes succeeds first try.
    result = await sup.run_agent(
        AgentName.SCRIPT,
        "split_scenes",
        {"script_text": "hi", "total_duration_sec": 5.0, "project_id": "p1"},
    )
    assert result.success


@pytest.mark.asyncio
async def test_supervisor_returns_failure_after_retries() -> None:
    from app.agents.supervisor import SupervisorAgent
    from app.mcp.client import McpClient

    sup = SupervisorAgent(McpClient(), max_retries=1)
    # An unknown tool always fails — tests the retry-then-give-up path.
    result = await sup.run_agent(AgentName.SCRIPT, "nonexistent_tool", {"script_text": "hi"})
    assert not result.success
    assert result.attempt == 2  # 1 retry + initial


@pytest.mark.asyncio
async def test_supervisor_run_step_updates_workflow_state() -> None:
    from app.agents.supervisor import SupervisorAgent
    from app.mcp.client import McpClient
    from app.schemas.contracts import WorkflowState

    sup = SupervisorAgent(McpClient(), max_retries=1)
    state = WorkflowState(id="ws1", project_id="p1", current_phase=WorkflowPhase.SCRIPT_UNDERSTANDING)
    r = await sup.run_step(
        state,
        AgentName.SCRIPT,
        "split_scenes",
        {"script_text": "hi", "total_duration_sec": 5.0, "project_id": "p1"},
    )
    assert r.success
    assert r.data.current_phase == WorkflowPhase.AUDIO_ANALYSIS
    assert r.data.agent_statuses["script"] == "success"
