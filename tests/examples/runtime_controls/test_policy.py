from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import assert_fields


@pytest.mark.asyncio
async def test_policy_deny_action_blocks_call_and_emits_policy_denied_event() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "deny-shell-delete",
                        "action": "deny",
                        "tools": ["shell"],
                        "actionPrefixes": ["delete"],
                        "reason": "delete blocked",
                    }
                ]
            },
            "onEvent": on_event,
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "shell", "action": "delete_file"}, lambda _runtime: _value("never"))

    assert_fields(excinfo.value, code="UNAUTHORIZED", status_code=403, message_includes="policy denied")
    assert len([event for event in events if event["type"] == "policy_denied"]) == 1


@pytest.mark.asyncio
async def test_require_approval_without_approval_handler_is_rejected() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "approval-required",
                        "action": "require_approval",
                        "tools": ["ticket-write"],
                        "reason": "needs approval",
                    }
                ]
            },
            "onEvent": on_event,
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "ticket-write", "action": "create"}, lambda _runtime: _value("never"))

    assert_fields(excinfo.value, code="UNAUTHORIZED", status_code=403, message_includes="requires approval")
    assert len([event for event in events if event["type"] == "policy_approval_required"]) == 1
    assert len([event for event in events if event["type"] == "policy_denied"]) == 0


@pytest.mark.asyncio
async def test_require_approval_with_handler_emits_denied_when_handler_returns_false() -> None:
    events = []
    def on_event(event):
        events.append(event)
    approval_contexts: list[dict[str, object]] = []

    async def approval_handler(context: dict[str, object]) -> bool:
        approval_contexts.append(context)
        return False

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "approval-required",
                        "action": "require_approval",
                        "tools": ["external-write"],
                        "destinations": ["*.external.localhost"],
                        "reason": "human gate",
                    }
                ],
                "approvalHandler": approval_handler,
            },
            "onEvent": on_event,
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run(
            {
                "toolName": "external-write",
                "destination": "https://billing.external.localhost/v1",
                "action": "create_invoice",
                "args": {"amount": 42},
            },
            lambda _runtime: _value("never"),
        )

    assert_fields(excinfo.value, code="UNAUTHORIZED", status_code=403, message_includes="approval denied")

    assert len(approval_contexts) == 1
    assert approval_contexts[0]["toolName"] == "external-write"
    assert len([event for event in events if event["type"] == "policy_approval_required"]) == 1
    assert len([event for event in events if event["type"] == "policy_denied"]) == 1


@pytest.mark.asyncio
async def test_require_approval_with_handler_emits_approved_event_and_allows_call() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "approval-required",
                        "action": "require_approval",
                        "tools": ["external-write"],
                        "reason": "manual approval needed",
                    }
                ],
                "approvalHandler": lambda _context: True,
            },
            "onEvent": on_event,
        }
    )

    result = await controls.run({"toolName": "external-write", "action": "create"}, lambda _runtime: _value("ok"))

    assert result == "ok"
    assert len([event for event in events if event["type"] == "policy_approval_required"]) == 1
    assert len([event for event in events if event["type"] == "policy_approved"]) == 1


@pytest.mark.asyncio
async def test_policy_matching_prefers_specificity_and_stricter_actions() -> None:
    controls_specific = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {"id": "allow-all", "action": "allow", "tools": ["*"], "destinations": ["*"]},
                    {
                        "id": "deny-exact",
                        "action": "deny",
                        "tools": ["http"],
                        "destinations": ["api.acme.localhost"],
                        "reason": "sensitive endpoint",
                    },
                ]
            },
        }
    )

    with pytest.raises(Exception) as excinfo_specific:
        await controls_specific.run({"toolName": "http", "destination": "https://api.acme.localhost/v1"}, lambda _runtime: _value("never"))
    assert_fields(excinfo_specific.value, code="UNAUTHORIZED", status_code=403)

    controls_tie = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {"id": "allow-shell", "action": "allow", "tools": ["shell"]},
                    {"id": "deny-shell", "action": "deny", "tools": ["shell"], "reason": "manual only"},
                ]
            },
        }
    )

    with pytest.raises(Exception) as excinfo_tie:
        await controls_tie.run({"toolName": "shell", "action": "exec"}, lambda _runtime: _value("never"))
    assert_fields(excinfo_tie.value, code="UNAUTHORIZED", status_code=403)


@pytest.mark.asyncio
async def test_policy_action_prefixes_use_longest_matching_prefix() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "allow-write",
                        "action": "allow",
                        "tools": ["repo-admin"],
                        "actionPrefixes": ["write"],
                    },
                    {
                        "id": "deny-dangerous-write",
                        "action": "deny",
                        "tools": ["repo-admin"],
                        "actionPrefixes": ["write:dangerous"],
                        "reason": "dangerous writes blocked",
                    },
                ]
            },
        }
    )

    with pytest.raises(Exception) as denied_exc:
        await controls.run({"toolName": "repo-admin", "action": "write:dangerous:force"}, lambda _runtime: _value("never"))
    assert_fields(denied_exc.value, code="UNAUTHORIZED")

    safe = await controls.run({"toolName": "repo-admin", "action": "write:standard"}, lambda _runtime: _value("ok"))
    assert safe == "ok"


@pytest.mark.asyncio
async def test_policy_can_be_disabled_globally() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "enabled": False,
                "rules": [
                    {
                        "action": "deny",
                        "tools": ["*"],
                        "reason": "would deny everything if enabled",
                    }
                ],
            },
        }
    )

    result = await controls.run({"toolName": "any-tool"}, lambda _runtime: _value("ok"))
    assert result == "ok"


@pytest.mark.asyncio
async def test_policy_dry_run_mode_emits_policy_dry_run_and_allows_deny_rules() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "mode": "dryRun",
                "rules": [
                    {
                        "id": "deny-shell",
                        "action": "deny",
                        "tools": ["shell"],
                        "reason": "deny in simulation",
                    }
                ],
            },
            "onEvent": on_event,
        }
    )

    result = await controls.run({"toolName": "shell"}, lambda _runtime: _value("ok"))
    assert result == "ok"

    assert len([event for event in events if event["type"] == "policy_dry_run"]) == 1
    assert len([event for event in events if event["type"] == "policy_denied"]) == 0


@pytest.mark.asyncio
async def test_policy_dry_run_mode_skips_approval_handler_for_require_approval() -> None:
    events = []
    def on_event(event):
        events.append(event)
    approval_calls = 0

    async def approval_handler(_context: dict[str, object]) -> bool:
        nonlocal approval_calls
        approval_calls += 1
        return False

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "mode": "dryRun",
                "rules": [
                    {
                        "id": "require-approval",
                        "action": "require_approval",
                        "tools": ["ticket-write"],
                        "reason": "approval in simulation",
                    }
                ],
                "approvalHandler": approval_handler,
            },
            "onEvent": on_event,
        }
    )

    result = await controls.run({"toolName": "ticket-write"}, lambda _runtime: _value("ok"))
    assert result == "ok"
    assert approval_calls == 0
    assert len([event for event in events if event["type"] == "policy_dry_run"]) == 1
    assert len([event for event in events if event["type"] == "policy_approval_required"]) == 0


async def _value(value: object) -> object:
    return value
