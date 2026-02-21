from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import create_map_adapter, assert_fields, make_exception


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_high_failure_rate_and_blocks_during_cooldown() -> None:
    events = []
    def on_event(event):
        events.append(event)
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "circuitBreaker": {
                "enabled": True,
                "windowMs": 1000,
                "minRequests": 2,
                "failureRateThreshold": 0.5,
                "cooldownMs": 120,
            },
            "onEvent": on_event,
        }
    )

    with pytest.raises(Exception) as excinfo_1:
        await controls.run(
            {"toolName": "http", "destination": "api.service.localhost"},
            lambda _runtime: _raise(make_exception("upstream down", "NETWORK_ERROR", 503)),
        )
    assert_fields(excinfo_1.value, code="NETWORK_ERROR", status_code=503)

    with pytest.raises(Exception) as excinfo_2:
        await controls.run(
            {"toolName": "http", "destination": "api.service.localhost"},
            lambda _runtime: _raise(make_exception("upstream still down", "NETWORK_ERROR", 503)),
        )
    assert_fields(excinfo_2.value, code="NETWORK_ERROR", status_code=503)

    assert len([event for event in events if event["type"] == "circuit_open"]) == 1

    with pytest.raises(Exception) as blocked_exc:
        await controls.run({"toolName": "http", "destination": "api.service.localhost"}, lambda _runtime: _value("blocked"))

    assert_fields(blocked_exc.value, code="NETWORK_ERROR", message_includes="circuit breaker open")


@pytest.mark.asyncio
async def test_circuit_breaker_does_not_open_when_failure_rate_stays_below_threshold() -> None:
    events = []
    def on_event(event):
        events.append(event)
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "circuitBreaker": {
                "enabled": True,
                "windowMs": 1000,
                "minRequests": 4,
                "failureRateThreshold": 0.75,
                "cooldownMs": 120,
            },
            "onEvent": on_event,
        }
    )

    for _ in range(2):
        with pytest.raises(Exception) as excinfo:
            await controls.run(
                {"toolName": "fetch", "destination": "stats.localhost"},
                lambda _runtime: _raise(make_exception("transient", "NETWORK_ERROR", 503)),
            )
        assert_fields(excinfo.value, code="NETWORK_ERROR")

    for _ in range(3):
        result = await controls.run({"toolName": "fetch", "destination": "stats.localhost"}, lambda _runtime: _value("ok"))
        assert result == "ok"

    assert len([event for event in events if event["type"] == "circuit_open"]) == 0


@pytest.mark.asyncio
async def test_circuit_state_adapter_persists_open_state_across_instances_and_normalizes_destination_host() -> None:
    _backing_map, adapter = create_map_adapter()

    first = RuntimeControls.create(
        {
            "tenantKey": "tenant-circuit",
            "retry": {"maxAttempts": 1},
            "circuitBreaker": {
                "enabled": True,
                "windowMs": 1000,
                "minRequests": 1,
                "failureRateThreshold": 1,
                "cooldownMs": 120,
            },
            "state": {"circuit": adapter},
        }
    )

    with pytest.raises(Exception) as first_exc:
        await first.run(
            {"toolName": "http", "destination": "https://api.persist.localhost/v1/jobs"},
            lambda _runtime: _raise(make_exception("down", "NETWORK_ERROR", 503)),
        )
    assert_fields(first_exc.value, code="NETWORK_ERROR")

    second = RuntimeControls.create(
        {
            "tenantKey": "tenant-circuit",
            "retry": {"maxAttempts": 1},
            "circuitBreaker": {
                "enabled": True,
                "windowMs": 1000,
                "minRequests": 1,
                "failureRateThreshold": 1,
                "cooldownMs": 120,
            },
            "state": {"circuit": adapter},
        }
    )

    with pytest.raises(Exception) as second_exc:
        await second.run({"toolName": "http", "destination": "api.persist.localhost"}, lambda _runtime: _value("blocked"))
    assert_fields(second_exc.value, code="NETWORK_ERROR", message_includes="circuit breaker open")


@pytest.mark.asyncio
async def test_circuit_key_is_isolated_by_destination_host() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "circuitBreaker": {
                "enabled": True,
                "windowMs": 1000,
                "minRequests": 1,
                "failureRateThreshold": 1,
                "cooldownMs": 120,
            },
        }
    )

    with pytest.raises(Exception) as excinfo:
        await controls.run(
            {"toolName": "http", "destination": "api-a.localhost"},
            lambda _runtime: _raise(make_exception("host-a down", "NETWORK_ERROR", 503)),
        )
    assert_fields(excinfo.value, code="NETWORK_ERROR")

    other_destination = await controls.run({"toolName": "http", "destination": "api-b.localhost"}, lambda _runtime: _value("ok"))
    assert other_destination == "ok"


async def _value(value: object) -> object:
    return value


async def _raise(error: Exception) -> None:
    raise error
