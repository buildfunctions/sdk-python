from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls, applyAgentLogicSafety

from ..helpers import assert_fields


@pytest.mark.asyncio
async def test_intent_allowlist_denies_tool_action_outside_allowed_intents() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        applyAgentLogicSafety(
            {
                "retry": {"maxAttempts": 1},
                "onEvent": on_event,
            },
            {
                "intentAllowlist": {
                    "enabled": True,
                    "rules": [
                        {
                            "toolNamePattern": "repo-write",
                            "actionPrefixes": ["push_"],
                        }
                    ],
                    "denyReason": "Tool/action is outside intent allowlist",
                }
            },
        )
    )

    allowed = await controls.run(
        {"toolName": "repo-write", "runKey": "run-allowlist", "action": "push_commit"},
        lambda _runtime: _value("ok"),
    )
    assert allowed == "ok"

    with pytest.raises(Exception) as deny_exc:
        await controls.run(
            {"toolName": "repo-write", "runKey": "run-allowlist", "action": "delete_branch"},
            lambda _runtime: _value("never"),
        )

    assert_fields(deny_exc.value, code="UNAUTHORIZED", status_code=403, message_includes="allowlist")
    assert len([event for event in events if event["type"] == "policy_denied"]) == 1


async def _value(value: object) -> object:
    return value
