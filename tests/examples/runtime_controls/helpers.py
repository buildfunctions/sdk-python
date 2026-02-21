from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any


async def wait_with_abort(ms: int, signal: Any = None) -> None:
    if ms <= 0:
        return

    async def _wait_abort() -> None:
        if signal is None:
            await asyncio.Future()
            return

        if hasattr(signal, "wait") and callable(signal.wait):
            await signal.wait()
            return

        while True:
            aborted = bool(getattr(signal, "aborted", False)) if not isinstance(signal, dict) else bool(signal.get("aborted"))
            if aborted:
                return
            await asyncio.sleep(0.001)

    sleep_task = asyncio.create_task(asyncio.sleep(ms / 1000.0))
    abort_task = asyncio.create_task(_wait_abort())

    done, pending = await asyncio.wait({sleep_task, abort_task}, return_when=asyncio.FIRST_COMPLETED)

    if abort_task in done:
        sleep_task.cancel()
        for task in pending:
            task.cancel()
        raise Exception("aborted")

    abort_task.cancel()
    for task in pending:
        task.cancel()
    await sleep_task


async def sleep(ms: int) -> None:
    await asyncio.sleep(ms / 1000.0)


def create_map_adapter(initial_entries: list[tuple[str, Any]] | None = None) -> tuple[dict[str, Any], Any]:
    backing_map = dict(initial_entries or [])

    def get(key: str) -> Any:
        return backing_map.get(key)

    def set(key: str, value: Any) -> None:
        backing_map[key] = value

    def delete(key: str) -> None:
        backing_map.pop(key, None)

    def keys():
        return backing_map.keys()

    return backing_map, SimpleNamespace(get=get, set=set, delete=delete, keys=keys)


def make_exception(message: str, code: str = "UNKNOWN_ERROR", status_code: int | None = None) -> Exception:
    error = Exception(message)
    setattr(error, "code", code)
    if status_code is not None:
        setattr(error, "status_code", status_code)
        setattr(error, "statusCode", status_code)
    return error


def assert_fields(
    error: Exception,
    *,
    code: str | None = None,
    status_code: int | None = None,
    message_includes: str | None = None,
) -> None:
    assert isinstance(error, Exception), f"Expected Exception, got {type(error)!r}"
    assert isinstance(getattr(error, "code", None), str), f"Expected error.code, got {getattr(error, 'code', None)!r}"

    if code is not None:
        assert getattr(error, "code", None) == code

    if status_code is not None:
        actual_status_code = getattr(error, "status_code", getattr(error, "statusCode", None))
        assert actual_status_code == status_code

    if message_includes is not None:
        assert re.search(message_includes, str(error), re.I), f"Expected '{message_includes}' in '{error}'"
