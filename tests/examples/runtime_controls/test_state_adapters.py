from __future__ import annotations

from types import SimpleNamespace

import pytest

from buildfunctions import RuntimeControls

from .helpers import wait_with_abort


@pytest.mark.asyncio
async def test_state_adapter_without_keys_still_supports_loop_pruning_via_tracked_keys() -> None:
    backing_map: dict[str, object] = {}

    def get(key: str):
        return backing_map.get(key)

    def set(key: str, value: object) -> None:
        backing_map[key] = value

    def delete(key: str) -> None:
        backing_map.pop(key, None)

    controls = RuntimeControls.create(
        {
            "tenantKey": "tenant-no-keys",
            "retry": {"maxAttempts": 1},
            "timeoutMs": 50,
            "loopBreaker": {
                "warningThreshold": 100,
                "quarantineThreshold": 200,
                "stopThreshold": 300,
                "maxFingerprints": 20,
            },
            "state": {"loop": SimpleNamespace(get=get, set=set, delete=delete)},
        }
    )

    for index in range(25):
        await controls.run(
            {
                "toolName": f"tool-{index}",
                "args": {"index": index},
            },
            lambda runtime: _run_wait(runtime),
        )

    loop_keys = [key for key in backing_map.keys() if key.startswith("tenant-no-keys:loop:")]
    assert len(loop_keys) <= 20, f"Expected at most 20 loop keys, got {len(loop_keys)}"


async def _run_wait(runtime: dict[str, object]) -> str:
    await wait_with_abort(1, runtime["signal"])
    return "ok"
