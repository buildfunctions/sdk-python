from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from buildfunctions import RuntimeControls, create_abort_controller

from .helpers import assert_fields, sleep, make_exception


@pytest.mark.asyncio
async def test_config_values_above_max_are_clamped() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "circuitBreaker": {
                "enabled": True,
                "windowMs": 1000,
                "minRequests": 1,
                "failureRateThreshold": 9,
                "cooldownMs": 80,
            },
            "onEvent": on_event,
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run(
            {"toolName": "http", "destination": "clamp.localhost"},
            lambda _runtime: _raise(make_exception("down", "NETWORK_ERROR", 503)),
        )

    assert_fields(excinfo.value, code="NETWORK_ERROR")
    assert len([event for event in events if event["type"] == "circuit_open"]) == 1


@pytest.mark.asyncio
async def test_circular_args_do_not_crash_fingerprint_hashing_fallback_path() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 2,
                "quarantineThreshold": 99,
                "stopThreshold": 99,
                "maxFingerprints": 200,
            },
            "onEvent": on_event,
        }
    )

    circular: dict[str, object] = {}
    circular["self"] = circular

    await controls.run({"toolName": "hash-tool", "args": circular}, lambda _runtime: _value("same"))
    await controls.run({"toolName": "hash-tool", "args": circular}, lambda _runtime: _value("same"))

    assert len([event for event in events if event["type"] == "loop_warning"]) == 1


@pytest.mark.asyncio
async def test_retry_backoff_jitter_path_is_used_when_jitter_ratio_gt_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    def on_event(event):
        events.append(event)
    attempts = 0

    monkeypatch.setattr(random, "random", lambda: 1.0)

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 2,
                "initialDelayMs": 10,
                "maxDelayMs": 10,
                "backoffFactor": 1,
                "jitterRatio": 0.5,
            },
            "onEvent": on_event,
        }
    )

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise make_exception("temporary", "NETWORK_ERROR", 503)
        return "ok"

    result = await controls.run({"toolName": "jitter-tool"}, execute)

    assert result == "ok"
    assert attempts == 2

    retry_event = next(event for event in events if event["type"] == "retry")
    assert retry_event["details"]["delayMs"] == 15


@pytest.mark.asyncio
async def test_pre_aborted_caller_signal_short_circuits_execution() -> None:
    controls = RuntimeControls.create({"retry": {"maxAttempts": 1}})
    controller = create_abort_controller()
    controller.abort(Exception("already-aborted"))

    executed = False

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal executed
        executed = True
        return "never"

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "pre-aborted", "signal": controller.signal}, execute)

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="cancelled")
    assert executed is False


@pytest.mark.asyncio
async def test_aborting_during_an_in_flight_call_rejects_even_if_execute_resolves_later() -> None:
    controls = RuntimeControls.create({"retry": {"maxAttempts": 1}})
    controller = create_abort_controller()

    async def execute(_runtime: dict[str, object]) -> str:
        async def cancel_soon() -> None:
            await sleep(5)
            controller.abort(Exception("cancel"))

        import asyncio

        asyncio.create_task(cancel_soon())
        await sleep(30)
        return "late-success"

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "abort-race", "signal": controller.signal}, execute)

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="cancelled")
    await sleep(40)


@pytest.mark.asyncio
async def test_cancellation_during_retry_delay_maps_to_cancelled_caller_error() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 2,
                "initialDelayMs": 25,
                "maxDelayMs": 25,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "retryClassifier": lambda _ctx: True,
        }
    )

    controller = create_abort_controller()
    attempts = 0

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            controller.abort(Exception("cancel-before-delay"))
        raise make_exception("temporary", "NETWORK_ERROR", 503)

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "retry-delay-cancel", "signal": controller.signal}, execute)

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="cancelled")
    assert attempts == 1


@pytest.mark.asyncio
async def test_status_extraction_supports_multiple_error_shapes() -> None:
    def create_controls() -> object:
        return RuntimeControls.create(
            {
                "retry": {
                    "maxAttempts": 2,
                    "initialDelayMs": 0,
                    "maxDelayMs": 0,
                    "backoffFactor": 1,
                    "jitterRatio": 0,
                }
            }
        )

    status_code_shape = Exception("statusCode shape")
    setattr(status_code_shape, "statusCode", 503)

    status_shape = Exception("status shape")
    setattr(status_shape, "status", 503)

    response_status_shape = Exception("response.status shape")
    setattr(response_status_shape, "response", SimpleNamespace(status=503))

    shapes = [
        {"name": "statusCode", "value": status_code_shape},
        {"name": "status", "value": status_shape},
        {"name": "response.status", "value": response_status_shape},
    ]

    for shape in shapes:
        attempts = 0
        controls = create_controls()

        async def execute(_runtime: dict[str, object]) -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise shape["value"]
            return "ok"

        result = await controls.run({"toolName": f"status-shape-{shape['name']}"}, execute)
        assert result == "ok"
        assert attempts == 2


@pytest.mark.asyncio
async def test_non_error_throw_normalizes_to_unknown_error_fallback() -> None:
    controls = RuntimeControls.create({"retry": {"maxAttempts": 1}})

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "non-error"}, lambda _runtime: _raise_non_error())

    assert_fields(excinfo.value, code="UNKNOWN_ERROR", message_includes="tool call failed")


@pytest.mark.asyncio
async def test_invalid_retry_classifier_return_falls_back_to_default_decision() -> None:
    attempts = 0

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 2,
                "initialDelayMs": 0,
                "maxDelayMs": 0,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "retryClassifier": lambda _ctx: "retry-maybe",
        }
    )

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise make_exception("temporary", "NETWORK_ERROR", 503)
        return "ok"

    result = await controls.run({"toolName": "invalid-classifier"}, execute)

    assert result == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_policy_no_match_branches_allow_execution_when_destination_action_constraints_unmet() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "needs-destination",
                        "action": "deny",
                        "tools": ["shell"],
                        "destinations": ["api.secure.localhost"],
                        "reason": "requires destination match",
                    },
                    {
                        "id": "needs-prefix",
                        "action": "deny",
                        "tools": ["shell"],
                        "actionPrefixes": ["delete"],
                        "reason": "requires action prefix",
                    },
                ]
            },
        }
    )

    no_destination = await controls.run({"toolName": "shell"}, lambda _runtime: _value("ok-1"))
    assert no_destination == "ok-1"

    destination_mismatch = await controls.run(
        {"toolName": "shell", "destination": "https://other.localhost/v1"},
        lambda _runtime: _value("ok-2"),
    )
    assert destination_mismatch == "ok-2"

    no_action_match = await controls.run(
        {"toolName": "shell", "destination": "https://other.localhost/v1", "action": "read_file"},
        lambda _runtime: _value("ok-3"),
    )
    assert no_action_match == "ok-3"


@pytest.mark.asyncio
async def test_policy_rule_with_non_matching_tools_is_ignored() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "http-only",
                        "action": "deny",
                        "tools": ["http*"],
                        "reason": "http tools denied",
                    }
                ]
            },
        }
    )

    result = await controls.run({"toolName": "shell"}, lambda _runtime: _value("ok"))
    assert result == "ok"


@pytest.mark.asyncio
async def test_policy_destination_specificity_prefers_exact_destination_over_wildcard() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "wildcard-destination",
                        "action": "deny",
                        "tools": ["shell"],
                        "destinations": ["*.acme.localhost"],
                        "reason": "wildcard-reason",
                    },
                    {
                        "id": "exact-destination",
                        "action": "deny",
                        "tools": ["shell"],
                        "destinations": ["api.acme.localhost"],
                        "reason": "exact-reason",
                    },
                ]
            },
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "shell", "destination": "https://api.acme.localhost/v1"}, lambda _runtime: _value("never"))

    assert_fields(excinfo.value, code="UNAUTHORIZED", message_includes="exact-reason")


@pytest.mark.asyncio
async def test_policy_exact_tie_currently_resolves_to_earlier_rule_index() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "policy": {
                "rules": [
                    {
                        "id": "earlier",
                        "action": "deny",
                        "tools": ["shell"],
                        "reason": "earlier-reason",
                    },
                    {
                        "id": "later",
                        "action": "deny",
                        "tools": ["shell"],
                        "reason": "later-reason",
                    },
                ]
            },
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "shell"}, lambda _runtime: _value("never"))

    assert_fields(excinfo.value, code="UNAUTHORIZED", message_includes="earlier-reason")


@pytest.mark.asyncio
async def test_loop_breaker_can_be_disabled() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "enabled": False,
                "warningThreshold": 1,
                "quarantineThreshold": 2,
                "stopThreshold": 3,
                "quarantineMs": 20,
                "stopCooldownMs": 50,
                "maxFingerprints": 20,
            },
            "onEvent": on_event,
        }
    )

    context = {
        "toolName": "loop-disabled",
        "args": {"a": 1},
    }

    for _ in range(6):
        result = await controls.run(context, lambda _runtime: _value("same-outcome"))
        assert result == "same-outcome"

    loop_events = [event for event in events if event["type"].startswith("loop_")]
    assert len(loop_events) == 0


@pytest.mark.asyncio
async def test_loop_pruning_tolerates_stale_keys_that_resolve_to_undefined_state() -> None:
    backing_map: dict[str, object] = {
        "tenant-stale:loop:stale-only-key": None,
    }

    def get(key: str):
        return backing_map.get(key)

    def set(key: str, value: object) -> None:
        backing_map[key] = value

    def delete(key: str) -> None:
        backing_map.pop(key, None)

    def keys():
        return backing_map.keys()

    controls = RuntimeControls.create(
        {
            "tenantKey": "tenant-stale",
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 100,
                "quarantineThreshold": 200,
                "stopThreshold": 300,
                "maxFingerprints": 20,
            },
            "state": {"loop": SimpleNamespace(get=get, set=set, delete=delete, keys=keys)},
        }
    )

    for index in range(25):
        await controls.run({"toolName": "stale-loop-tool", "args": {"index": index}}, lambda _runtime, idx=index: _value(f"ok-{idx}"))

    loop_keys = [key for key in backing_map.keys() if key.startswith("tenant-stale:loop:")]
    assert len(loop_keys) <= 21, f"Expected stale+bounded keys, got {len(loop_keys)}"


async def _value(value: object) -> object:
    return value


async def _raise(error: Exception) -> None:
    raise error


async def _raise_non_error() -> None:
    raise Exception()
