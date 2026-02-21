from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import create_map_adapter, assert_fields


@pytest.mark.asyncio
async def test_max_tool_calls_enforces_per_run_budget_and_reset_clears_it() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "maxToolCalls": 2,
            "retry": {"maxAttempts": 1},
            "onEvent": on_event,
        }
    )

    await controls.run({"toolName": "shell", "runKey": "run-1"}, lambda _runtime: _value("ok-1"))
    await controls.run({"toolName": "shell", "runKey": "run-1"}, lambda _runtime: _value("ok-2"))

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "shell", "runKey": "run-1"}, lambda _runtime: _value("blocked"))

    assert_fields(excinfo.value, code="INVALID_REQUEST", message_includes="tool-call budget exceeded")
    assert len([event for event in events if event["type"] == "budget_stop"]) == 1

    await controls.reset("run-1")
    after_reset = await controls.run({"toolName": "shell", "runKey": "run-1"}, lambda _runtime: _value("ok-3"))
    assert after_reset == "ok-3"


@pytest.mark.asyncio
async def test_budget_counters_are_scoped_by_runkey() -> None:
    controls = RuntimeControls.create(
        {
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
        }
    )

    await controls.run({"toolName": "ci", "runKey": "run-a"}, lambda _runtime: _value("a-1"))
    await controls.run({"toolName": "ci", "runKey": "run-b"}, lambda _runtime: _value("b-1"))

    with pytest.raises(Exception) as excinfo_a:
        await controls.run({"toolName": "ci", "runKey": "run-a"}, lambda _runtime: _value("a-2"))
    assert_fields(excinfo_a.value, code="INVALID_REQUEST")

    with pytest.raises(Exception) as excinfo_b:
        await controls.run({"toolName": "ci", "runKey": "run-b"}, lambda _runtime: _value("b-2"))
    assert_fields(excinfo_b.value, code="INVALID_REQUEST")


@pytest.mark.asyncio
async def test_budget_state_adapter_persists_counters_across_controls_instances() -> None:
    _backing_map, adapter = create_map_adapter()

    first = RuntimeControls.create(
        {
            "tenantKey": "tenant-budget",
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
            "state": {"budget": adapter},
        }
    )

    second = RuntimeControls.create(
        {
            "tenantKey": "tenant-budget",
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
            "state": {"budget": adapter},
        }
    )

    await first.run({"toolName": "shell", "runKey": "persisted-run"}, lambda _runtime: _value("ok"))

    with pytest.raises(Exception) as excinfo:
        await second.run({"toolName": "shell", "runKey": "persisted-run"}, lambda _runtime: _value("blocked"))

    assert_fields(excinfo.value, code="INVALID_REQUEST")


@pytest.mark.asyncio
async def test_budget_state_is_isolated_by_tenant_key_when_sharing_adapter_backend() -> None:
    _backing_map, adapter = create_map_adapter()

    tenant_a = RuntimeControls.create(
        {
            "tenantKey": "tenant-a",
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
            "state": {"budget": adapter},
        }
    )

    tenant_b = RuntimeControls.create(
        {
            "tenantKey": "tenant-b",
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
            "state": {"budget": adapter},
        }
    )

    await tenant_a.run({"toolName": "shell", "runKey": "same-run-key"}, lambda _runtime: _value("a-ok"))
    tenant_b_result = await tenant_b.run({"toolName": "shell", "runKey": "same-run-key"}, lambda _runtime: _value("b-ok"))

    assert tenant_b_result == "b-ok"


@pytest.mark.asyncio
async def test_reset_only_affects_the_selected_run_key() -> None:
    controls = RuntimeControls.create(
        {
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
        }
    )

    await controls.run({"toolName": "tool", "runKey": "run-a"}, lambda _runtime: _value("a-1"))
    await controls.run({"toolName": "tool", "runKey": "run-b"}, lambda _runtime: _value("b-1"))

    await controls.reset("run-a")

    run_a_after_reset = await controls.run({"toolName": "tool", "runKey": "run-a"}, lambda _runtime: _value("a-2"))
    assert run_a_after_reset == "a-2"

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "tool", "runKey": "run-b"}, lambda _runtime: _value("b-2"))

    assert_fields(excinfo.value, code="INVALID_REQUEST")


async def _value(value: object) -> object:
    return value
