from __future__ import annotations

import asyncio

import pytest

from buildfunctions import RuntimeControls

from .helpers import assert_fields, sleep, wait_with_abort, make_exception


@pytest.mark.asyncio
async def test_before_call_verifier_can_reject_tool_invocation() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "verifiers": {
                "beforeCall": lambda _ctx: {"allow": False, "reason": "manual gate failed"},
            },
            "onEvent": on_event,
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "shell", "action": "exec"}, lambda _runtime: _value("never"))

    assert_fields(excinfo.value, code="INVALID_REQUEST", message_includes="verifier rejected tool call")

    rejected = next((event for event in events if event["type"] == "verifier_rejected"), None)
    assert rejected is not None
    assert rejected["details"]["phase"] == "before_call"


@pytest.mark.asyncio
async def test_after_success_verifier_can_reject_result_without_retrying() -> None:
    attempts = 0

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 3,
                "initialDelayMs": 0,
                "maxDelayMs": 0,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "verifiers": {
                "afterSuccess": lambda _ctx: {"allow": False, "reason": "result shape invalid"},
            },
        }
    )

    async def execute(_runtime: dict[str, object]) -> dict[str, bool]:
        nonlocal attempts
        attempts += 1
        return {"ok": True}

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "result-tool"}, execute)

    assert_fields(excinfo.value, code="INVALID_REQUEST", message_includes="verifier rejected tool result")
    assert attempts == 1


@pytest.mark.asyncio
async def test_idempotency_replays_successful_result_without_re_executing_tool() -> None:
    events = []
    def on_event(event):
        events.append(event)

    calls = 0
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "idempotency": {"enabled": True},
            "onEvent": on_event,
        }
    )

    context = {
        "toolName": "ticket-create",
        "runKey": "run-idem-1",
        "idempotencyKey": "ticket-77",
    }

    async def execute(_runtime: dict[str, object]) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"ticketId": "OPS-77"}

    first = await controls.run(context, execute)
    second = await controls.run(context, execute)

    assert first == {"ticketId": "OPS-77"}
    assert second == {"ticketId": "OPS-77"}
    assert calls == 1
    assert len([event for event in events if event["type"] == "idempotency_replay"]) == 1


@pytest.mark.asyncio
async def test_idempotency_can_replay_final_errors_when_include_errors_is_enabled() -> None:
    calls = 0

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "idempotency": {
                "enabled": True,
                "includeErrors": True,
            },
        }
    )

    context = {
        "toolName": "provider-call",
        "runKey": "run-idem-2",
        "idempotencyKey": "provider-op-42",
    }

    async def execute_fail(_runtime: dict[str, object]) -> str:
        nonlocal calls
        calls += 1
        raise make_exception("provider down", "NETWORK_ERROR", 503)

    with pytest.raises(Exception) as first_exc:
        await controls.run(context, execute_fail)
    assert_fields(first_exc.value, code="NETWORK_ERROR", status_code=503)

    async def execute_should_not_run(_runtime: dict[str, object]) -> str:
        nonlocal calls
        calls += 1
        raise make_exception("should not execute", "UNKNOWN_ERROR", 500)

    with pytest.raises(Exception) as second_exc:
        await controls.run(context, execute_should_not_run)
    assert_fields(second_exc.value, code="NETWORK_ERROR", status_code=503)

    assert calls == 1


@pytest.mark.asyncio
async def test_idempotency_record_expires_when_ttl_ms_elapses() -> None:
    calls = 0

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "idempotency": {
                "enabled": True,
                "ttlMs": 20,
            },
        }
    )

    context = {
        "toolName": "ttl-tool",
        "runKey": "run-idem-ttl",
        "idempotencyKey": "same-op",
    }

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal calls
        calls += 1
        return f"result-{calls}"

    first = await controls.run(context, execute)
    await sleep(30)
    second = await controls.run(context, execute)

    assert first == "result-1"
    assert second == "result-2"
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrency_reject_mode_blocks_simultaneous_access_to_same_resource() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "concurrency": {
                "enabled": True,
                "waitMode": "reject",
                "leaseMs": 500,
            },
            "onEvent": on_event,
        }
    )

    first = asyncio.create_task(
        controls.run(
            {
                "toolName": "repo-write",
                "resourceKey": "repo:buildfunctions/sdk-night-agent",
            },
            lambda runtime: _wait_and_return(runtime, 60, "first-done"),
        )
    )

    await sleep(10)

    with pytest.raises(Exception) as second_exc:
        await controls.run(
            {
                "toolName": "repo-write",
                "resourceKey": "repo:buildfunctions/sdk-night-agent",
            },
            lambda _runtime: _value("second-done"),
        )

    assert_fields(second_exc.value, code="INVALID_REQUEST", message_includes="concurrency lock")

    first_result = await first
    assert first_result == "first-done"
    assert len([event for event in events if event["type"] == "concurrency_rejected"]) == 1


@pytest.mark.asyncio
async def test_concurrency_wait_mode_serializes_conflicting_calls() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "concurrency": {
                "enabled": True,
                "waitMode": "wait",
                "waitTimeoutMs": 300,
                "pollIntervalMs": 10,
                "leaseMs": 500,
            },
        }
    )

    sequence: list[str] = []

    async def first_execute(runtime: dict[str, object]) -> str:
        sequence.append("first-start")
        await wait_with_abort(60, runtime["signal"])
        sequence.append("first-end")
        return "first"

    async def second_execute(_runtime: dict[str, object]) -> str:
        sequence.append("second-start")
        return "second"

    first = asyncio.create_task(
        controls.run(
            {
                "toolName": "repo-write",
                "resourceKey": "repo:buildfunctions/sdk-night-agent",
            },
            first_execute,
        )
    )

    await sleep(5)

    second = asyncio.create_task(
        controls.run(
            {
                "toolName": "repo-write",
                "resourceKey": "repo:buildfunctions/sdk-night-agent",
            },
            second_execute,
        )
    )

    first_result = await first
    second_result = await second

    assert first_result == "first"
    assert second_result == "second"
    assert sequence == ["first-start", "first-end", "second-start"]


@pytest.mark.asyncio
async def test_concurrency_wait_mode_times_out_when_lock_is_not_released_in_time() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "concurrency": {
                "enabled": True,
                "waitMode": "wait",
                "waitTimeoutMs": 30,
                "pollIntervalMs": 10,
                "leaseMs": 500,
            },
            "onEvent": on_event,
        }
    )

    first = asyncio.create_task(
        controls.run(
            {
                "toolName": "db-write",
                "resourceKey": "db:tenant-1",
            },
            lambda runtime: _wait_and_return(runtime, 80, "first"),
        )
    )

    await sleep(5)

    with pytest.raises(Exception) as second_exc:
        await controls.run(
            {
                "toolName": "db-write",
                "resourceKey": "db:tenant-1",
            },
            lambda _runtime: _value("second"),
        )

    assert_fields(second_exc.value, code="INVALID_REQUEST", message_includes="lock wait timeout")

    await first

    assert len([event for event in events if event["type"] == "concurrency_wait"]) == 1
    assert len([event for event in events if event["type"] == "concurrency_rejected"]) == 1


@pytest.mark.asyncio
async def test_wrap_resolves_idempotency_key_and_resource_key_from_arguments() -> None:
    calls = 0

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "idempotency": {"enabled": True},
            "concurrency": {"enabled": True, "waitMode": "reject", "leaseMs": 500},
        }
    )

    async def wrapped_run(args: tuple[dict[str, str]], runtime: dict[str, object]) -> dict[str, str]:
        nonlocal calls
        calls += 1
        await wait_with_abort(5, runtime["signal"])
        return {"id": f"ticket-{args[0]['idempotencyKey']}"}

    wrapped = controls.wrap(
        {
            "toolName": "ticket-write",
            "resolveRunKey": lambda input_: input_["runKey"],
            "resolveIdempotencyKey": lambda input_: input_["idempotencyKey"],
            "resolveResourceKey": lambda input_: input_["resourceKey"],
            "run": wrapped_run,
        }
    )

    input_value = {
        "runKey": "wrap-run",
        "idempotencyKey": "777",
        "resourceKey": "ticket:777",
    }

    first = await wrapped(input_value)
    second = await wrapped(input_value)

    assert first == {"id": "ticket-777"}
    assert second == {"id": "ticket-777"}
    assert calls == 1


async def _value(value: object) -> object:
    return value


async def _wait_and_return(runtime: dict[str, object], ms: int, value: str) -> str:
    await wait_with_abort(ms, runtime["signal"])
    return value
