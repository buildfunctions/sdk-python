from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import sleep, make_exception


@pytest.mark.asyncio
async def test_event_sinks_receive_runtime_control_events() -> None:
    sink_events: list[dict[str, object]] = []

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 2,
                "initialDelayMs": 0,
                "maxDelayMs": 0,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "eventSinks": [lambda event: sink_events.append(event)],
        }
    )

    attempts = 0

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise make_exception("temporary outage", "NETWORK_ERROR", 503)
        return "ok"

    result = await controls.run({"toolName": "sink-tool"}, execute)
    assert result == "ok"

    await sleep(0)

    retry_event = next((event for event in sink_events if event["type"] == "retry"), None)
    assert retry_event is not None
    assert retry_event["details"]["toolName"] == "sink-tool"


@pytest.mark.asyncio
async def test_on_event_sink_failure_captures_sink_failures_without_breaking_tool_execution() -> None:
    sink_failures: list[dict[str, object]] = []

    def failing_sink(_event: dict[str, object]) -> None:
        raise Exception("sink failed")

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 2,
                "initialDelayMs": 0,
                "maxDelayMs": 0,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "eventSinks": [failing_sink],
            "onEventSinkFailure": lambda params: sink_failures.append(params),
        }
    )

    attempts = 0

    async def execute(_runtime: dict[str, object]) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise make_exception("temporary outage", "NETWORK_ERROR", 503)
        return "ok"

    result = await controls.run({"toolName": "sink-error-tool"}, execute)
    assert result == "ok"

    await sleep(0)

    assert len(sink_failures) == 1
    assert sink_failures[0]["sinkIndex"] == 0
    assert sink_failures[0]["event"]["type"] == "retry"
    assert "sink failed" in str(sink_failures[0]["failure"]).lower()
