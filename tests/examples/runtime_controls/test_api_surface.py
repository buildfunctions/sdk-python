from __future__ import annotations

import pytest

from buildfunctions import RuntimeControls

from .helpers import assert_fields, make_exception


@pytest.mark.asyncio
async def test_run_returns_execute_result_and_passes_abort_signal() -> None:
    controls = RuntimeControls.create({"retry": {"maxAttempts": 1}})

    received_signal = None

    async def execute(runtime: dict[str, object]) -> str:
        nonlocal received_signal
        received_signal = runtime["signal"]
        return "ok"

    result = await controls.run({"toolName": "simple-tool"}, execute)

    assert result == "ok"
    assert received_signal is not None
    assert hasattr(received_signal, "aborted")
    assert received_signal.aborted is False


@pytest.mark.asyncio
async def test_wrap_resolves_run_context_from_args_and_forwards_args_tuple() -> None:
    seen: list[dict[str, object]] = []
    controls = RuntimeControls.create({"retry": {"maxAttempts": 1}})

    async def wrapped_run(args: tuple[dict[str, str]], runtime: dict[str, object]) -> dict[str, object]:
        seen.append(
            {
                "argsLength": len(args),
                "signalType": type(runtime["signal"]).__name__,
                "request": args[0],
            }
        )
        return {"ok": True, "id": args[0]["id"]}

    wrapped = controls.wrap(
        {
            "toolName": "http-fetch",
            "resolveRunKey": lambda request: f"run-{request['id']}",
            "resolveDestination": lambda request: f"https://{request['host']}/v1/jobs",
            "resolveAction": lambda request: f"{request['method']} {request['path']}",
            "run": wrapped_run,
        }
    )

    request = {
        "id": "1234",
        "host": "api.example.localhost",
        "method": "POST",
        "path": "/tasks",
    }

    result = await wrapped(request)

    assert result == {"ok": True, "id": "1234"}
    assert len(seen) == 1
    assert seen[0]["argsLength"] == 1
    assert seen[0]["signalType"]
    assert seen[0]["request"]["host"] == "api.example.localhost"


@pytest.mark.asyncio
async def test_reset_clears_the_normalized_default_budget_key() -> None:
    controls = RuntimeControls.create(
        {
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
        }
    )

    await controls.run({"toolName": "default-budget-tool", "runKey": "   "}, lambda _runtime: _async_value("first"))

    with pytest.raises(Exception) as excinfo:
        await controls.run({"toolName": "default-budget-tool"}, lambda _runtime: _async_value("blocked"))

    assert_fields(excinfo.value, code="INVALID_REQUEST", message_includes="tool-call budget exceeded")

    await controls.reset()

    after_reset = await controls.run({"toolName": "default-budget-tool"}, lambda _runtime: _async_value("after-reset"))
    assert after_reset == "after-reset"


@pytest.mark.asyncio
async def test_runkey_budgets_are_isolated_per_run() -> None:
    controls = RuntimeControls.create(
        {
            "maxToolCalls": 1,
            "retry": {"maxAttempts": 1},
        }
    )

    await controls.run({"toolName": "task-tool", "runKey": "run-a"}, lambda _runtime: _async_value("a-1"))
    await controls.run({"toolName": "task-tool", "runKey": "run-b"}, lambda _runtime: _async_value("b-1"))

    with pytest.raises(Exception) as excinfo_a:
        await controls.run({"toolName": "task-tool", "runKey": "run-a"}, lambda _runtime: _async_value("a-2"))

    assert_fields(excinfo_a.value, code="INVALID_REQUEST", message_includes="tool-call budget exceeded")

    with pytest.raises(Exception) as excinfo_b:
        await controls.run(
            {"toolName": "task-tool", "runKey": "run-b"},
            lambda _runtime: _raise_async(make_exception("This should not run due budget for run-b", "UNKNOWN_ERROR")),
        )

    assert_fields(excinfo_b.value, code="INVALID_REQUEST")


async def _async_value(value: object) -> object:
    return value


async def _raise_async(error: Exception) -> None:
    raise error
