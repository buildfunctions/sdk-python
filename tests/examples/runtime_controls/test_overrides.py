from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import assert_fields, wait_with_abort, make_exception


@pytest.mark.asyncio
async def test_tool_override_wins_over_destination_override_when_both_match() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "timeoutMs": 100,
            "overrides": {
                "destinations": {"api.service.localhost": {"timeoutMs": 1}},
                "tools": {"shell": {"timeoutMs": 40}},
            },
        }
    )

    successful = await controls.run(
        {"toolName": "shell", "destination": "https://api.service.localhost/v1"},
        lambda runtime: _wait_and_return(runtime, 15, "ok"),
    )
    assert successful == "ok"

    with pytest.raises(Exception) as excinfo:
        await controls.run(
            {"toolName": "http", "destination": "https://api.service.localhost/v1"},
            lambda runtime: _wait_and_return(runtime, 15, "never"),
        )

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="timed out")


@pytest.mark.asyncio
async def test_destination_override_specificity_prefers_exact_over_wildcard_over_global() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "timeoutMs": 100,
            "overrides": {
                "destinations": {
                    "*": {"timeoutMs": 1},
                    "*.service.localhost": {"timeoutMs": 60},
                    "api.service.localhost": {"timeoutMs": 80},
                }
            },
        }
    )

    exact_result = await controls.run(
        {"toolName": "http", "destination": "https://api.service.localhost/v1"},
        lambda runtime: _wait_and_return(runtime, 30, "exact-ok"),
    )
    assert exact_result == "exact-ok"

    wildcard_result = await controls.run(
        {"toolName": "http", "destination": "https://foo.service.localhost/v1"},
        lambda runtime: _wait_and_return(runtime, 10, "wildcard-ok"),
    )
    assert wildcard_result == "wildcard-ok"

    with pytest.raises(Exception) as excinfo:
        await controls.run(
            {"toolName": "http", "destination": "https://other.localhost/v1"},
            lambda runtime: _wait_and_return(runtime, 20, "never"),
        )

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="timed out")


@pytest.mark.asyncio
async def test_tool_override_specificity_prefers_exact_over_prefix_over_global() -> None:
    controls = RuntimeControls.create(
        {
            "retry": {"maxAttempts": 1},
            "timeoutMs": 200,
            "overrides": {
                "tools": {
                    "*": {"timeoutMs": 5},
                    "http*": {"timeoutMs": 20},
                    "http-fetch": {"timeoutMs": 40},
                }
            },
        }
    )

    exact = await controls.run({"toolName": "http-fetch"}, lambda runtime: _wait_and_return(runtime, 30, "exact"))
    assert exact == "exact"

    prefix = await controls.run({"toolName": "http-stream"}, lambda runtime: _wait_and_return(runtime, 8, "prefix"))
    assert prefix == "prefix"

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "db-query"}, lambda runtime: _wait_and_return(runtime, 15, "never"))

    assert_fields(excinfo.value, code="NETWORK_ERROR", message_includes="timed out")


@pytest.mark.asyncio
async def test_tool_override_can_raise_retry_attempts_above_global_retry_config() -> None:
    flaky_attempts = 0
    normal_attempts = 0

    controls = RuntimeControls.create(
        {
            "retry": {
                "maxAttempts": 1,
                "initialDelayMs": 0,
                "maxDelayMs": 0,
                "backoffFactor": 1,
                "jitterRatio": 0,
            },
            "overrides": {
                "tools": {
                    "flaky-tool": {
                        "retry": {
                            "maxAttempts": 2,
                            "initialDelayMs": 0,
                            "maxDelayMs": 0,
                            "backoffFactor": 1,
                            "jitterRatio": 0,
                        }
                    }
                }
            },
        }
    )

    async def flaky(_runtime: dict[str, object]) -> str:
        nonlocal flaky_attempts
        flaky_attempts += 1
        if flaky_attempts == 1:
            raise make_exception("temporary", "NETWORK_ERROR", 503)
        return "flaky-ok"

    flaky_result = await controls.run({"toolName": "flaky-tool"}, flaky)
    assert flaky_result == "flaky-ok"
    assert flaky_attempts == 2

    async def normal(_runtime: dict[str, object]) -> str:
        nonlocal normal_attempts
        normal_attempts += 1
        raise make_exception("temporary", "NETWORK_ERROR", 503)

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "normal-tool"}, normal)

    assert_fields(excinfo.value, code="NETWORK_ERROR")
    assert normal_attempts == 1


@pytest.mark.asyncio
async def test_tool_override_can_disable_circuit_breaker_for_selected_tools() -> None:
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
            "overrides": {
                "tools": {
                    "safe-tool": {
                        "circuitBreaker": {
                            "enabled": False,
                        }
                    }
                }
            },
        }
    )

    with pytest.raises(Exception) as safe_exc:
        await controls.run(
            {"toolName": "safe-tool", "destination": "safe.localhost"},
            lambda _runtime: _raise(make_exception("down", "NETWORK_ERROR", 503)),
        )
    assert_fields(safe_exc.value, code="NETWORK_ERROR")

    safe_second_call = await controls.run({"toolName": "safe-tool", "destination": "safe.localhost"}, lambda _runtime: _value("ok"))
    assert safe_second_call == "ok"

    with pytest.raises(Exception) as unsafe_first:
        await controls.run(
            {"toolName": "unsafe-tool", "destination": "unsafe.localhost"},
            lambda _runtime: _raise(make_exception("down", "NETWORK_ERROR", 503)),
        )
    assert_fields(unsafe_first.value, code="NETWORK_ERROR")

    with pytest.raises(Exception) as unsafe_second:
        await controls.run({"toolName": "unsafe-tool", "destination": "unsafe.localhost"}, lambda _runtime: _value("blocked"))
    assert_fields(unsafe_second.value, code="NETWORK_ERROR", message_includes="circuit breaker open")


async def _wait_and_return(runtime: dict[str, object], ms: int, value: str) -> str:
    await wait_with_abort(ms, runtime["signal"])
    return value


async def _value(value: object) -> object:
    return value


async def _raise(error: Exception) -> None:
    raise error
