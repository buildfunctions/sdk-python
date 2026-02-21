from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import create_map_adapter, assert_fields, make_exception


@pytest.mark.asyncio
async def test_loop_breaker_emits_warning_quarantine_and_blocks_while_quarantine_is_active() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 2,
                "quarantineThreshold": 3,
                "stopThreshold": 10,
                "quarantineMs": 80,
                "stopCooldownMs": 500,
                "maxFingerprints": 200,
            },
            "onEvent": on_event,
        }
    )

    context = {
        "toolName": "fix-suggester",
        "args": {"runId": "run-22", "mode": "auto"},
    }

    await controls.run(context, lambda _runtime: _value("same-outcome"))
    await controls.run(context, lambda _runtime: _value("same-outcome"))
    await controls.run(context, lambda _runtime: _value("same-outcome"))

    assert len([event for event in events if event["type"] == "loop_warning"]) == 1
    assert len([event for event in events if event["type"] == "loop_quarantine"]) == 1

    with pytest.raises(Exception) as excinfo:
        await controls.run(context, lambda _runtime: _value("blocked"))

    assert_fields(excinfo.value, code="INVALID_REQUEST", message_includes="quarantined")


@pytest.mark.asyncio
async def test_loop_breaker_stop_threshold_emits_stop_event_and_blocks_subsequent_calls() -> None:
    events = []
    def on_event(event):
        events.append(event)

    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 2,
                "quarantineThreshold": 99,
                "stopThreshold": 3,
                "stopCooldownMs": 100,
                "maxFingerprints": 200,
            },
            "onEvent": on_event,
        }
    )

    context = {
        "toolName": "recommender",
        "args": {"id": 44},
    }

    for _ in range(3):
        with pytest.raises(Exception) as excinfo:
            await controls.run(context, lambda _runtime: _raise(make_exception("no progress", "UNKNOWN_ERROR", 422)))
        assert_fields(excinfo.value, code="UNKNOWN_ERROR", status_code=422)

    assert len([event for event in events if event["type"] == "loop_stop"]) == 1

    with pytest.raises(Exception) as blocked_exc:
        await controls.run(context, lambda _runtime: _value("blocked"))

    assert_fields(blocked_exc.value, code="INVALID_REQUEST", message_includes="loop breaker blocked")


@pytest.mark.asyncio
async def test_loop_fingerprint_uses_stable_argument_hashing_regardless_of_object_key_order() -> None:
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

    await controls.run({"toolName": "http", "args": {"alpha": 1, "beta": 2}}, lambda _runtime: _value("same"))
    await controls.run({"toolName": "http", "args": {"beta": 2, "alpha": 1}}, lambda _runtime: _value("same"))

    assert len([event for event in events if event["type"] == "loop_warning"]) == 1


@pytest.mark.asyncio
async def test_loop_state_adapter_persists_streaks_across_controls_instances() -> None:
    _backing_map, adapter = create_map_adapter()

    first = RuntimeControls.create(
        {
            "tenantKey": "tenant-loop",
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 2,
                "quarantineThreshold": 99,
                "stopThreshold": 99,
                "maxFingerprints": 200,
            },
            "state": {"loop": adapter},
        }
    )

    events = []
    def on_event(event):
        events.append(event)

    second = RuntimeControls.create(
        {
            "tenantKey": "tenant-loop",
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 2,
                "quarantineThreshold": 99,
                "stopThreshold": 99,
                "maxFingerprints": 200,
            },
            "state": {"loop": adapter},
            "onEvent": on_event,
        }
    )

    context = {
        "toolName": "persisted-tool",
        "args": {"jobId": "abc"},
    }

    await first.run(context, lambda _runtime: _value("same"))
    await second.run(context, lambda _runtime: _value("same"))

    assert len([event for event in events if event["type"] == "loop_warning"]) == 1


@pytest.mark.asyncio
async def test_loop_state_pruning_keeps_fingerprint_map_bounded_by_max_fingerprints_floor() -> None:
    backing_map, adapter = create_map_adapter()

    controls = RuntimeControls.create(
        {
            "tenantKey": "tenant-prune",
            "retry": {"maxAttempts": 1},
            "loopBreaker": {
                "warningThreshold": 100,
                "quarantineThreshold": 200,
                "stopThreshold": 300,
                "maxFingerprints": 20,
            },
            "state": {"loop": adapter},
        }
    )

    for index in range(25):
        await controls.run({"toolName": "fingerprinted-tool", "args": {"index": index}}, lambda _runtime, idx=index: _value(f"ok-{idx}"))

    loop_keys = [key for key in backing_map.keys() if key.startswith("tenant-prune:loop:")]
    assert len(loop_keys) <= 20, f"Expected at most 20 loop keys, got {len(loop_keys)}"


async def _value(value: object) -> object:
    return value


async def _raise(error: Exception) -> None:
    raise error
