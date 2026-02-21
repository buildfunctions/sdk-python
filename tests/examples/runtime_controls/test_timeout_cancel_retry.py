from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls, create_abort_controller

from .helpers import assert_fields, wait_with_abort, make_exception


@pytest.mark.asyncio
async def test_timeout_converts_to_network_error_and_surfaces_timed_out_message() -> None:
    controls = RuntimeControls.create(
        {
            "timeoutMs": 10,
            "retry": {"maxAttempts": 1},
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run(
            {"toolName": "slow-tool"},
            lambda runtime: _run_slow(runtime),
        )

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="timed out")


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_retry_and_maps_to_network_error() -> None:
    attempts = 0
    controls = RuntimeControls.create(
        {
            "timeoutMs": 200,
            "retry": {
                "maxAttempts": 4,
                "initialDelayMs": 0,
                "maxDelayMs": 0,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
        }
    )

    controller = create_abort_controller()

    async def execute(runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1

        async def cancel_soon() -> None:
            await wait_with_abort(5, None)
            controller.abort(Exception("user-cancelled"))

        _ = runtime
        import asyncio

        asyncio.create_task(cancel_soon())
        await wait_with_abort(50, runtime["signal"])
        return "never"

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "cancelled-tool", "signal": controller.signal}, execute)

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="cancelled")
    assert attempts == 1


@pytest.mark.asyncio
async def test_default_retry_policy_retries_retryable_status_codes_and_emits_retry_events() -> None:
    events = []
    def on_event(event):
        events.append(event)

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
            "onEvent": on_event,
        }
    )

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise make_exception("provider unavailable", "NETWORK_ERROR", 503)
        return "ok"

    result = await controls.run({"toolName": "provider-call"}, execute)

    assert result == "ok"
    assert attempts == 3
    assert len([event for event in events if event["type"] == "retry"]) == 2


@pytest.mark.asyncio
async def test_fatal_buildfunctions_errors_do_not_retry() -> None:
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
        }
    )

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        raise make_exception("bad input", "VALIDATION_ERROR", 400)

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "validation-tool"}, execute)

    assert_fields(excinfo.value, code="VALIDATION_ERROR", status_code=400)
    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_classifier_boolean_return_can_suppress_retries() -> None:
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
            "retryClassifier": lambda _ctx: False,
        }
    )

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        raise make_exception("temporary outage", "NETWORK_ERROR", 503)

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "classifier-tool"}, execute)

    assert_fields(excinfo.value, code="NETWORK_ERROR", status_code=503)
    assert attempts == 1


@pytest.mark.asyncio
async def test_retry_classifier_decision_object_can_force_retry_with_custom_delay_and_reason() -> None:
    events = []
    def on_event(event):
        events.append(event)

    attempts = 0
    seen_classifier_input: list[dict[str, object]] = []

    def retry_classifier(ctx: dict[str, object]) -> dict[str, object]:
        seen_classifier_input.append(ctx)
        if ctx["attempt"] == 1 and ctx["error"]["code"] == "UNKNOWN_ERROR":
            return {"retryable": True, "delayMs": 0, "reason": "force-once"}
        return {"retryable": False}

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 2,
                "initialDelayMs": 100,
                "maxDelayMs": 100,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "retryClassifier": retry_classifier,
            "onEvent": on_event,
        }
    )

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise Exception("parse explosion")
        return "ok"

    result = await controls.run(
        {
            "toolName": "parser-tool",
            "destination": "https://api.parse.localhost/v1",
            "action": "parse_payload",
        },
        execute,
    )

    assert result == "ok"
    assert attempts == 2

    assert len(seen_classifier_input) == 1
    assert seen_classifier_input[0]["toolName"] == "parser-tool"
    assert seen_classifier_input[0]["destination"] == "https://api.parse.localhost/v1"
    assert seen_classifier_input[0]["action"] == "parse_payload"
    assert seen_classifier_input[0]["attempt"] == 1

    retry_event = next(event for event in events if event["type"] == "retry")
    assert retry_event["details"]["delayMs"] == 0
    assert retry_event["details"]["classifierReason"] == "force-once"


async def _run_slow(runtime: dict[str, object]) -> str:
    await wait_with_abort(40, runtime["signal"])
    return "never"
