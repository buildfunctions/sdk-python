from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls, applyAgentLogicSafety

from ..helpers import assert_fields


@pytest.mark.asyncio
async def test_exit_condition_enforces_missing_terminal_and_post_terminal_blocking() -> None:
    controls = RuntimeControls.create(
        applyAgentLogicSafety(
            {
                "retry": {"maxAttempts": 1},
            },
            {
                "exitCondition": {
                    "enabled": True,
                    "maxStepsPerRun": 2,
                    "terminalActions": [
                        {
                            "toolNamePattern": "agent-control",
                            "actionPrefix": "finish",
                        }
                    ],
                    "blockAfterTerminal": True,
                }
            },
        )
    )

    await controls.run(
        {"toolName": "planner", "runKey": "run-no-exit", "action": "plan_step"},
        lambda _runtime: _value("step-1"),
    )
    await controls.run(
        {"toolName": "planner", "runKey": "run-no-exit", "action": "plan_step"},
        lambda _runtime: _value("step-2"),
    )

    with pytest.raises(Exception) as no_exit_exc:
        await controls.run(
            {"toolName": "planner", "runKey": "run-no-exit", "action": "plan_step"},
            lambda _runtime: _value("never"),
        )

    assert_fields(no_exit_exc.value, code="INVALID_REQUEST", message_includes="exit condition")

    finished = await controls.run(
        {"toolName": "agent-control", "runKey": "run-finished", "action": "finish"},
        lambda _runtime: _value("done"),
    )
    assert finished == "done"

    with pytest.raises(Exception) as terminal_exc:
        await controls.run(
            {"toolName": "planner", "runKey": "run-finished", "action": "plan_step"},
            lambda _runtime: _value("never"),
        )

    assert_fields(terminal_exc.value, code="INVALID_REQUEST", message_includes="terminal action")


async def _value(value: object) -> object:
    return value
