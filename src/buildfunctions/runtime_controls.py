"""Runtime controls wrapper for arbitrary async tool calls.

This module provides function-based APIs mirroring the TypeScript runtime-controls
surface:
- RuntimeControls.create(config)

No API key is required; controls wrap any async function.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Iterable, Literal, TypedDict, cast
from urllib.parse import urlparse

from buildfunctions.dotdict import DotDict
from buildfunctions.errors import BuildfunctionsError

RuntimePolicyMode = Literal["enforce", "dryRun"]
RuntimePolicyAction = Literal["allow", "deny", "require_approval"]
ToolConcurrencyWaitMode = Literal["reject", "wait"]
RuntimeControlEventType = Literal[
    "retry",
    "loop_warning",
    "loop_quarantine",
    "loop_stop",
    "circuit_open",
    "budget_stop",
    "policy_denied",
    "policy_approval_required",
    "policy_approved",
    "policy_dry_run",
    "verifier_rejected",
    "idempotency_replay",
    "concurrency_wait",
    "concurrency_rejected",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _to_int(value: Any, fallback: int) -> int:
    if not _is_number(value):
        return fallback
    return int(round(float(value)))


def _to_float(value: Any, fallback: float) -> float:
    if not _is_number(value):
        return fallback
    return float(value)


def _clamp_number(value: Any, fallback: float, minimum: float | None = None, maximum: float | None = None) -> float:
    if not _is_number(value):
        result = fallback
    else:
        result = float(value)

    if minimum is not None and result < minimum:
        result = minimum
    if maximum is not None and result > maximum:
        result = maximum
    return result


def _dict_get(mapping: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not mapping:
        return default
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


async def _maybe_await(value: Any) -> Any:
    if asyncio.isfuture(value) or asyncio.iscoroutine(value):
        return await value
    return value


def _get_callable(container: Any, key: str) -> Callable[..., Any] | None:
    if isinstance(container, dict):
        candidate = container.get(key)
    else:
        candidate = getattr(container, key, None)

    if callable(candidate):
        return cast(Callable[..., Any], candidate)
    return None


def _make_failure(message: str, code: str = "UNKNOWN_ERROR", status_code: int | None = None) -> Exception:
    error = Exception(message or "Tool call failed")
    setattr(error, "code", code)
    if status_code is not None:
        setattr(error, "status_code", status_code)
        setattr(error, "statusCode", status_code)
    return error


def _has_failure_fields(error: Any) -> bool:
    return bool(
        isinstance(error, Exception)
        and isinstance(getattr(error, "code", None), str)
    )


def _create_abort_signal() -> DotDict:
    event = asyncio.Event()
    listeners: list[Callable[[], None]] = []
    signal = DotDict({"aborted": False, "reason": None})

    def remove_event_listener(callback: Callable[[], None]) -> None:
        nonlocal listeners
        listeners = [listener for listener in listeners if listener is not callback]

    def add_event_listener(callback: Callable[[], None], *, once: bool = False) -> Callable[[], None]:
        if once:
            def once_callback() -> None:
                try:
                    callback()
                finally:
                    remove_event_listener(once_callback)

            listeners.append(once_callback)
            return once_callback

        listeners.append(callback)
        return callback

    def addEventListener(event_name: str, callback: Callable[[], None], options: Any = None) -> Callable[[], None]:
        if event_name != "abort":
            return callback

        once = isinstance(options, dict) and bool(options.get("once"))
        return add_event_listener(callback, once=once)

    def removeEventListener(event_name: str, callback: Callable[[], None]) -> None:
        if event_name == "abort":
            remove_event_listener(callback)

    def abort(reason: Any = None) -> None:
        if signal.aborted:
            return

        signal.aborted = True
        signal.reason = reason
        event.set()

        for listener in list(listeners):
            try:
                listener()
            except Exception:
                # Listener failures must not break cancellation propagation.
                continue

    async def wait() -> None:
        await event.wait()

    signal.add_event_listener = add_event_listener
    signal.addEventListener = addEventListener
    signal.remove_event_listener = remove_event_listener
    signal.removeEventListener = removeEventListener
    signal.abort = abort
    signal.wait = wait
    return signal


def _create_abort_controller() -> DotDict:
    signal = _create_abort_signal()

    def abort(reason: Any = None) -> None:
        signal.abort(reason)

    return DotDict({"signal": signal, "abort": abort})


def create_abort_controller() -> DotDict:
    """Create an abort controller compatible with runtime-controls cancellation."""
    controller = _create_abort_controller()
    return DotDict({"signal": controller.signal, "abort": controller.abort})


def _is_abort_signal(signal: Any) -> bool:
    if signal is None:
        return False
    if not callable(_get_callable(signal, "wait")):
        return False
    if isinstance(signal, dict):
        return "aborted" in signal
    return hasattr(signal, "aborted")


def _signal_aborted(signal: Any) -> bool:
    if signal is None:
        return False

    if _is_abort_signal(signal):
        return signal.aborted

    if hasattr(signal, "aborted"):
        try:
            return bool(getattr(signal, "aborted"))
        except Exception:
            return False

    if isinstance(signal, dict) and "aborted" in signal:
        return bool(signal["aborted"])

    return False


def _signal_reason(signal: Any) -> Any:
    if signal is None:
        return None

    if hasattr(signal, "reason"):
        try:
            return getattr(signal, "reason")
        except Exception:
            return None

    if isinstance(signal, dict):
        return signal.get("reason")

    return None


def _add_abort_listener(signal: Any, callback: Callable[[], None]) -> Callable[[], None] | None:
    if signal is None:
        return None

    if _is_abort_signal(signal):
        add_event_listener = _get_callable(signal, "add_event_listener")
        if add_event_listener:
            return cast(Callable[[], None], add_event_listener(callback, once=True))

    add_event_listener = _get_callable(signal, "add_event_listener")
    if add_event_listener:
        return cast(Callable[[], None], add_event_listener(callback, once=True))

    add_event_listener = _get_callable(signal, "addEventListener")
    if add_event_listener:
        return cast(Callable[[], None], add_event_listener("abort", callback, {"once": True}))

    return None


def _remove_abort_listener(signal: Any, callback: Callable[[], None] | None) -> None:
    if signal is None or callback is None:
        return

    if _is_abort_signal(signal):
        remove_event_listener = _get_callable(signal, "remove_event_listener")
        if remove_event_listener:
            remove_event_listener(callback)
            return

    remove_event_listener = _get_callable(signal, "remove_event_listener")
    if remove_event_listener:
        remove_event_listener(callback)
        return

    remove_event_listener = _get_callable(signal, "removeEventListener")
    if remove_event_listener:
        remove_event_listener("abort", callback)


async def _wait_for_abort(signal: Any) -> None:
    if signal is None:
        await asyncio.Future()
        return

    if _is_abort_signal(signal):
        await signal.wait()
        return

    wait_callable = _get_callable(signal, "wait")
    if wait_callable:
        await _maybe_await(wait_callable())
        return

    while True:
        if _signal_aborted(signal):
            return
        await asyncio.sleep(0.005)


def _resolve_retry_config(defaults: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    return {
        "maxAttempts": max(1, _to_int(_clamp_number(overrides.get("maxAttempts"), defaults["maxAttempts"], 1), defaults["maxAttempts"])),
        "initialDelayMs": max(0, _to_int(_clamp_number(overrides.get("initialDelayMs"), defaults["initialDelayMs"], 0), defaults["initialDelayMs"])),
        "maxDelayMs": max(0, _to_int(_clamp_number(overrides.get("maxDelayMs"), defaults["maxDelayMs"], 0), defaults["maxDelayMs"])),
        "backoffFactor": _to_float(_clamp_number(overrides.get("backoffFactor"), defaults["backoffFactor"], 1), defaults["backoffFactor"]),
        "jitterRatio": _to_float(_clamp_number(overrides.get("jitterRatio"), defaults["jitterRatio"], 0, 1), defaults["jitterRatio"]),
    }


def _resolve_loop_breaker_config(defaults: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    return {
        "enabled": bool(overrides.get("enabled", defaults["enabled"])),
        "warningThreshold": max(1, _to_int(_clamp_number(overrides.get("warningThreshold"), defaults["warningThreshold"], 1), defaults["warningThreshold"])),
        "quarantineThreshold": max(1, _to_int(_clamp_number(overrides.get("quarantineThreshold"), defaults["quarantineThreshold"], 1), defaults["quarantineThreshold"])),
        "stopThreshold": max(1, _to_int(_clamp_number(overrides.get("stopThreshold"), defaults["stopThreshold"], 1), defaults["stopThreshold"])),
        "quarantineMs": max(0, _to_int(_clamp_number(overrides.get("quarantineMs"), defaults["quarantineMs"], 0), defaults["quarantineMs"])),
        "stopCooldownMs": max(0, _to_int(_clamp_number(overrides.get("stopCooldownMs"), defaults["stopCooldownMs"], 0), defaults["stopCooldownMs"])),
        "maxFingerprints": max(20, _to_int(_clamp_number(overrides.get("maxFingerprints"), defaults["maxFingerprints"], 20), defaults["maxFingerprints"])),
    }


def _resolve_circuit_breaker_config(defaults: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    return {
        "enabled": bool(overrides.get("enabled", defaults["enabled"])),
        "windowMs": max(1000, _to_int(_clamp_number(overrides.get("windowMs"), defaults["windowMs"], 1000), defaults["windowMs"])),
        "minRequests": max(1, _to_int(_clamp_number(overrides.get("minRequests"), defaults["minRequests"], 1), defaults["minRequests"])),
        "failureRateThreshold": _to_float(_clamp_number(overrides.get("failureRateThreshold"), defaults["failureRateThreshold"], 0, 1), defaults["failureRateThreshold"]),
        "cooldownMs": max(1000, _to_int(_clamp_number(overrides.get("cooldownMs"), defaults["cooldownMs"], 1000), defaults["cooldownMs"])),
    }


def _resolve_idempotency_config(defaults: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    ttl = overrides.get("ttlMs")
    ttl_ms = None
    if _is_number(ttl) and float(ttl) > 0:
        ttl_ms = int(round(float(ttl)))

    return {
        "enabled": bool(overrides.get("enabled", defaults["enabled"])),
        "ttlMs": ttl_ms,
        "includeErrors": bool(overrides.get("includeErrors", defaults["includeErrors"])),
        "namespaceByRunKey": bool(overrides.get("namespaceByRunKey", defaults["namespaceByRunKey"])),
    }


def _resolve_concurrency_config(defaults: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    wait_mode = overrides.get("waitMode", defaults["waitMode"])
    if wait_mode != "wait":
        wait_mode = "reject"

    return {
        "enabled": bool(overrides.get("enabled", defaults["enabled"])),
        "leaseMs": max(1000, _to_int(_clamp_number(overrides.get("leaseMs"), defaults["leaseMs"], 1000), defaults["leaseMs"])),
        "waitMode": wait_mode,
        "waitTimeoutMs": max(0, _to_int(_clamp_number(overrides.get("waitTimeoutMs"), defaults["waitTimeoutMs"], 0), defaults["waitTimeoutMs"])),
        "pollIntervalMs": max(10, _to_int(_clamp_number(overrides.get("pollIntervalMs"), defaults["pollIntervalMs"], 10), defaults["pollIntervalMs"])),
    }


def _resolve_runtime_overrides(overrides: dict[str, Any] | None) -> dict[str, list[dict[str, Any]]]:
    overrides = overrides or {}

    def normalize(mapping: dict[str, Any] | None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for raw_pattern, raw_override in (mapping or {}).items():
            pattern = str(raw_pattern).strip()
            if not pattern:
                continue
            override = _as_dict(raw_override)
            results.append(
                {
                    "pattern": pattern,
                    "override": {
                        "timeoutMs": override.get("timeoutMs"),
                        "retry": _as_dict(override.get("retry")),
                        "loopBreaker": _as_dict(override.get("loopBreaker")),
                        "circuitBreaker": _as_dict(override.get("circuitBreaker")),
                    },
                }
            )
        return results

    return {
        "tools": normalize(_as_dict(overrides.get("tools"))),
        "destinations": normalize(_as_dict(overrides.get("destinations"))),
    }


DEFAULT_TIMEOUT_MS = 60_000
DEFAULT_RETRY = {
    "maxAttempts": 4,
    "initialDelayMs": 250,
    "maxDelayMs": 10_000,
    "backoffFactor": 2.0,
    "jitterRatio": 0.2,
}
DEFAULT_LOOP_BREAKER = {
    "enabled": True,
    "warningThreshold": 5,
    "quarantineThreshold": 8,
    "stopThreshold": 12,
    "quarantineMs": 15_000,
    "stopCooldownMs": 120_000,
    "maxFingerprints": 200,
}
DEFAULT_CIRCUIT_BREAKER = {
    "enabled": True,
    "windowMs": 30_000,
    "minRequests": 20,
    "failureRateThreshold": 0.6,
    "cooldownMs": 60_000,
}
DEFAULT_POLICY = {
    "enabled": True,
    "mode": "enforce",
    "rules": [],
    "approvalHandler": None,
}
DEFAULT_IDEMPOTENCY = {
    "enabled": True,
    "ttlMs": None,
    "includeErrors": False,
    "namespaceByRunKey": True,
}
DEFAULT_CONCURRENCY = {
    "enabled": False,
    "leaseMs": 30_000,
    "waitMode": "reject",
    "waitTimeoutMs": 5_000,
    "pollIntervalMs": 50,
}


def _resolve_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}

    max_tool_calls_raw = _dict_get(config, "maxToolCalls", "max_tool_calls")
    max_tool_calls: int | None = None
    if _is_number(max_tool_calls_raw):
        max_tool_calls = max(1, int(round(float(max_tool_calls_raw))))

    policy = _as_dict(_dict_get(config, "policy"))
    verifiers = _as_dict(_dict_get(config, "verifiers"))

    return {
        "tenantKey": str(_dict_get(config, "tenantKey", "tenant_key", default="default")),
        "timeoutMs": max(0, _to_int(_clamp_number(_dict_get(config, "timeoutMs", "timeout_ms"), DEFAULT_TIMEOUT_MS, 0), DEFAULT_TIMEOUT_MS)),
        "maxToolCalls": max_tool_calls,
        "retry": _resolve_retry_config(DEFAULT_RETRY, _as_dict(_dict_get(config, "retry"))),
        "retryClassifier": _dict_get(config, "retryClassifier", "retry_classifier"),
        "loopBreaker": _resolve_loop_breaker_config(DEFAULT_LOOP_BREAKER, _as_dict(_dict_get(config, "loopBreaker", "loop_breaker"))),
        "circuitBreaker": _resolve_circuit_breaker_config(DEFAULT_CIRCUIT_BREAKER, _as_dict(_dict_get(config, "circuitBreaker", "circuit_breaker"))),
        "policy": {
            "enabled": bool(_dict_get(policy, "enabled", default=DEFAULT_POLICY["enabled"])),
            "mode": _dict_get(policy, "mode", default=DEFAULT_POLICY["mode"]),
            "rules": list(_dict_get(policy, "rules", default=DEFAULT_POLICY["rules"])),
            "approvalHandler": _dict_get(policy, "approvalHandler", "approval_handler"),
        },
        "verifiers": {
            "beforeCall": _dict_get(verifiers, "beforeCall", "before_call"),
            "afterSuccess": _dict_get(verifiers, "afterSuccess", "after_success"),
            "afterError": _dict_get(verifiers, "afterError", "after_error"),
        },
        "idempotency": _resolve_idempotency_config(DEFAULT_IDEMPOTENCY, _as_dict(_dict_get(config, "idempotency"))),
        "concurrency": _resolve_concurrency_config(DEFAULT_CONCURRENCY, _as_dict(_dict_get(config, "concurrency"))),
        "overrides": _resolve_runtime_overrides(_as_dict(_dict_get(config, "overrides"))),
        "state": _as_dict(_dict_get(config, "state")),
        "onEvent": _dict_get(config, "onEvent", "on_event"),
        "eventSinks": list(_dict_get(config, "eventSinks", "event_sinks", default=[])),
        "onEventSinkFailure": _dict_get(config, "onEventSinkFailure", "on_event_sink_failure"),
    }

def _create_state_store(adapter: Any = None) -> Any:
    if adapter is None:
        state: dict[str, Any] = {}

        async def _get(key: str) -> Any:
            return state.get(key)

        async def _set(key: str, value: Any) -> None:
            state[key] = value

        async def _delete(key: str) -> None:
            state.pop(key, None)

        async def _keys() -> list[str]:
            return list(state.keys())

        return cast(DotDict, SimpleNamespace(get=_get, set=_set, delete=_delete, keys=_keys))

    known_keys: set[str] = set()

    async def _get(key: str) -> Any:
        get_fn = _get_callable(adapter, "get")
        if not get_fn:
            return None
        value = await _maybe_await(get_fn(key))
        if value is not None:
            known_keys.add(key)
        return value

    async def _set(key: str, value: Any) -> None:
        set_fn = _get_callable(adapter, "set")
        if not set_fn:
            return
        known_keys.add(key)
        await _maybe_await(set_fn(key, value))

    async def _delete(key: str) -> None:
        delete_fn = _get_callable(adapter, "delete")
        known_keys.discard(key)
        if delete_fn:
            await _maybe_await(delete_fn(key))

    async def _keys() -> list[str]:
        keys_fn = _get_callable(adapter, "keys")
        if not keys_fn:
            return list(known_keys)

        raw_iterable = await _maybe_await(keys_fn())
        if raw_iterable is None:
            return []
        if isinstance(raw_iterable, list):
            return [str(item) for item in raw_iterable]
        if isinstance(raw_iterable, tuple):
            return [str(item) for item in raw_iterable]
        if isinstance(raw_iterable, set):
            return [str(item) for item in raw_iterable]
        if isinstance(raw_iterable, Iterable):
            return [str(item) for item in raw_iterable]
        return []

    return cast(DotDict, SimpleNamespace(get=_get, set=_set, delete=_delete, keys=_keys))


def _normalize_verifier_decision(decision: Any) -> dict[str, Any]:
    if isinstance(decision, bool):
        return {"allow": decision}

    if not isinstance(decision, dict):
        return {"allow": True}

    return {
        "allow": bool(decision.get("allow", False)),
        "reason": decision.get("reason") if isinstance(decision.get("reason"), str) else None,
    }


def _stable_stringify(value: Any) -> str:
    if value is None or isinstance(value, (str, int, float, bool)):
        return json.dumps(value)

    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_stable_stringify(item) for item in value) + "]"

    if isinstance(value, dict):
        keys = sorted(value.keys())
        return "{" + ",".join(f"{json.dumps(str(key))}:{_stable_stringify(value[key])}" for key in keys) + "}"

    raise TypeError("Unsupported value for stable stringify")


def _digest_stable(value: Any) -> str:
    try:
        serialized = _stable_stringify(value)
    except Exception:
        serialized = str(value)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _build_fingerprint(tool_name: str, args: Any) -> str:
    return f"{tool_name}:{_digest_stable(args if args is not None else None)}"


def _build_outcome_hash(params: dict[str, Any]) -> str:
    payload = {
        "ok": bool(params.get("ok")),
        "statusCode": params.get("statusCode"),
        "code": params.get("code"),
        "message": params.get("message"),
        "data": params.get("data"),
    }
    return _digest_stable(payload)


def _compute_backoff_delay(config: dict[str, Any], attempt: int) -> int:
    exponent = max(0, attempt - 1)
    base_delay = float(config["initialDelayMs"]) * (float(config["backoffFactor"]) ** exponent)
    bounded = min(float(config["maxDelayMs"]), max(0.0, base_delay))

    jitter_ratio = float(config["jitterRatio"])
    if jitter_ratio <= 0:
        return int(round(bounded))

    jitter_offset = (random.random() * 2 - 1) * jitter_ratio
    jittered = bounded * (1 + jitter_offset)
    return max(0, int(round(jittered)))

def _create_run_signal(timeout_ms: int, external_signal: Any = None) -> Any:
    controller = _create_abort_controller()
    timeout_handle: asyncio.TimerHandle | None = None
    timed_out = False

    def on_external_abort() -> None:
        controller.abort(_signal_reason(external_signal))

    remove_ref: Callable[[], None] | None = None
    if _signal_aborted(external_signal):
        on_external_abort()
    else:
        remove_ref = _add_abort_listener(external_signal, on_external_abort)

    if timeout_ms > 0:
        loop = asyncio.get_running_loop()

        def on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            controller.abort()

        timeout_handle = loop.call_later(timeout_ms / 1000.0, on_timeout)

    def cleanup() -> None:
        if timeout_handle:
            timeout_handle.cancel()
        _remove_abort_listener(external_signal, remove_ref)

    return cast(DotDict, SimpleNamespace(signal=controller.signal, cleanup=cleanup, did_timeout=lambda: timed_out))


async def _race_with_abort(signal: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    if _signal_aborted(signal):
        raise Exception("aborted")

    fn_task = asyncio.create_task(fn())
    abort_task = asyncio.create_task(_wait_for_abort(signal))

    done, pending = await asyncio.wait({fn_task, abort_task}, return_when=asyncio.FIRST_COMPLETED)

    if abort_task in done:
        if not fn_task.done():
            fn_task.cancel()
        for task in pending:
            task.cancel()
        raise Exception("aborted")

    abort_task.cancel()
    for task in pending:
        task.cancel()
    return await fn_task


async def _sleep_with_abort(ms: int, signal: Any = None) -> None:
    if ms <= 0:
        return

    sleep_task = asyncio.create_task(asyncio.sleep(ms / 1000.0))
    abort_task = asyncio.create_task(_wait_for_abort(signal))

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


def _extract_status_code(error: Any) -> int | None:
    if _has_failure_fields(error):
        status_code = getattr(error, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    if isinstance(error, BuildfunctionsError):
        return getattr(error, "status_code", None)

    if isinstance(error, dict):
        status_code = error.get("statusCode")
        if isinstance(status_code, int):
            return status_code

        status = error.get("status")
        if isinstance(status, int):
            return status

        response = error.get("response")
        if isinstance(response, dict) and isinstance(response.get("status"), int):
            return cast(int, response["status"])

    status_code_attr = getattr(error, "statusCode", None)
    if isinstance(status_code_attr, int):
        return status_code_attr

    status_attr = getattr(error, "status", None)
    if isinstance(status_attr, int):
        return status_attr

    response_attr = getattr(error, "response", None)
    response_status = getattr(response_attr, "status", None)
    if isinstance(response_status, int):
        return response_status

    return None


def _is_retryable_status(status_code: int | None) -> bool:
    if not isinstance(status_code, int):
        return False
    return status_code == 408 or status_code == 429 or status_code >= 500


def _normalize_failure(error: Any, params: dict[str, Any]) -> Exception:
    if _has_failure_fields(error):
        return error

    if isinstance(error, BuildfunctionsError):
        code = getattr(error, "code", "UNKNOWN_ERROR")
        status_code = getattr(error, "status_code", None)
        return _make_failure(str(error), code, status_code if status_code is not None else params.get("statusCode"))

    if params.get("cancelledByCaller"):
        return _make_failure("Tool call cancelled by caller", "NETWORK_ERROR", params.get("statusCode"))

    if params.get("didTimeout"):
        return _make_failure("Tool call timed out", "NETWORK_ERROR", params.get("statusCode"))

    if isinstance(error, Exception):
        message = str(error) or "Tool call failed"
        transient = bool(
            re.search(r"timeout|timed out|econnreset|eai_again|enotfound|network|socket|rate limit|temporar", message, re.I)
        )
        return _make_failure(message, "NETWORK_ERROR" if transient else "UNKNOWN_ERROR", params.get("statusCode"))

    return _make_failure("Tool call failed", "UNKNOWN_ERROR", params.get("statusCode"))


def _is_fatal_buildfunctions_code(code: str) -> bool:
    return code in {"UNAUTHORIZED", "INVALID_REQUEST", "VALIDATION_ERROR", "NOT_FOUND", "SIZE_LIMIT_EXCEEDED"}


def _should_retry_failure(error: Exception, status_code: int | None, cancelled_by_caller: bool) -> bool:
    error_code = getattr(error, "code", "UNKNOWN_ERROR")
    error_status_code = getattr(error, "status_code", None)

    if cancelled_by_caller:
        return False
    if _is_retryable_status(status_code if status_code is not None else error_status_code):
        return True
    if _is_fatal_buildfunctions_code(error_code):
        return False
    return error_code == "NETWORK_ERROR"


def _match_pattern(value: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _host_matches(pattern: str, host: str) -> bool:
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        return host.lower().endswith(pattern[1:].lower())
    return host.lower() == pattern.lower()


def _normalize_destination(destination: str | None) -> str | None:
    if not destination:
        return None

    try:
        parsed = urlparse(destination)
        if parsed.netloc:
            return parsed.netloc
    except Exception:
        pass

    return destination


def _get_tool_pattern_specificity(pattern: str) -> int:
    if pattern == "*":
        return 0
    if pattern.endswith("*"):
        return 1
    return 2


def _get_destination_pattern_specificity(pattern: str) -> int:
    if pattern == "*":
        return 0
    if pattern.startswith("*."):
        return 1
    return 2


def _find_tool_override(entries: list[dict[str, Any]], tool_name: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None

    for entry in entries:
        pattern = str(entry.get("pattern", ""))
        if not _match_pattern(tool_name, pattern):
            continue
        score = _get_tool_pattern_specificity(pattern)
        if best is None or score > int(best["score"]):
            best = {"score": score, "override": entry.get("override", {})}

    if not best:
        return None
    return _as_dict(best.get("override"))


def _find_destination_override(entries: list[dict[str, Any]], destination: str | None) -> dict[str, Any] | None:
    if not destination:
        return None

    best: dict[str, Any] | None = None

    for entry in entries:
        pattern = str(entry.get("pattern", ""))
        if not _host_matches(pattern, destination):
            continue

        score = _get_destination_pattern_specificity(pattern)
        if best is None or score > int(best["score"]):
            best = {"score": score, "override": entry.get("override", {})}

    if not best:
        return None
    return _as_dict(best.get("override"))


def _apply_runtime_override(base: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    if not override:
        return base

    timeout_ms = base["timeoutMs"]
    if _is_number(override.get("timeoutMs")):
        timeout_ms = max(0, int(round(_clamp_number(override.get("timeoutMs"), timeout_ms, 0))))

    return {
        "timeoutMs": timeout_ms,
        "retry": _resolve_retry_config(base["retry"], _as_dict(override.get("retry"))) if override.get("retry") else base["retry"],
        "loopBreaker": _resolve_loop_breaker_config(base["loopBreaker"], _as_dict(override.get("loopBreaker"))) if override.get("loopBreaker") else base["loopBreaker"],
        "circuitBreaker": _resolve_circuit_breaker_config(base["circuitBreaker"], _as_dict(override.get("circuitBreaker"))) if override.get("circuitBreaker") else base["circuitBreaker"],
    }


def _resolve_effective_call_config(resolved: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    normalized_destination = _normalize_destination(_dict_get(context, "destination"))

    destination_override = _find_destination_override(resolved["overrides"]["destinations"], normalized_destination)
    tool_override = _find_tool_override(resolved["overrides"]["tools"], str(_dict_get(context, "toolName", "tool_name", default="")))

    defaults = {
        "timeoutMs": _dict_get(context, "timeoutMs", "timeout_ms", default=resolved["timeoutMs"]),
        "retry": resolved["retry"],
        "loopBreaker": resolved["loopBreaker"],
        "circuitBreaker": resolved["circuitBreaker"],
    }

    return _apply_runtime_override(_apply_runtime_override(defaults, destination_override), tool_override)


def _get_policy_action_strictness(action: RuntimePolicyAction) -> int:
    if action == "deny":
        return 2
    if action == "require_approval":
        return 1
    return 0


def _get_tool_rule_match_rank(rule: dict[str, Any], context: dict[str, Any], index: int) -> dict[str, int] | None:
    tool_specificity = -1
    tools = rule.get("tools")
    if isinstance(tools, list) and tools:
        scores = [_get_tool_pattern_specificity(pattern) for pattern in tools if isinstance(pattern, str) and _match_pattern(context["toolName"], pattern)]
        if not scores:
            return None
        tool_specificity = max(scores)

    destination_specificity = -1
    destinations = rule.get("destinations")
    if isinstance(destinations, list) and destinations:
        destination = _normalize_destination(_dict_get(context, "destination"))
        if not destination:
            return None
        scores = [_get_destination_pattern_specificity(pattern) for pattern in destinations if isinstance(pattern, str) and _host_matches(pattern, destination)]
        if not scores:
            return None
        destination_specificity = max(scores)

    action_prefix_specificity = -1
    action_prefixes = rule.get("actionPrefixes")
    if isinstance(action_prefixes, list) and action_prefixes:
        action = _dict_get(context, "action")
        if not isinstance(action, str):
            return None
        lengths = [len(prefix) for prefix in action_prefixes if isinstance(prefix, str) and action.startswith(prefix)]
        if not lengths:
            return None
        action_prefix_specificity = max(lengths)

    return {
        "toolSpecificity": tool_specificity,
        "destinationSpecificity": destination_specificity,
        "actionPrefixSpecificity": action_prefix_specificity,
        "strictness": _get_policy_action_strictness(cast(RuntimePolicyAction, rule.get("action", "allow"))),
        "index": index,
    }


def _compare_rule_ranks(a: dict[str, int], b: dict[str, int]) -> int:
    if a["toolSpecificity"] != b["toolSpecificity"]:
        return a["toolSpecificity"] - b["toolSpecificity"]
    if a["destinationSpecificity"] != b["destinationSpecificity"]:
        return a["destinationSpecificity"] - b["destinationSpecificity"]
    if a["actionPrefixSpecificity"] != b["actionPrefixSpecificity"]:
        return a["actionPrefixSpecificity"] - b["actionPrefixSpecificity"]
    if a["strictness"] != b["strictness"]:
        return a["strictness"] - b["strictness"]
    return b["index"] - a["index"]


def _find_matching_tool_rule(rules: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any] | None:
    best_rule: dict[str, Any] | None = None
    best_rank: dict[str, int] | None = None

    for index, rule in enumerate(rules):
        rank = _get_tool_rule_match_rank(rule, context, index)
        if not rank:
            continue

        if best_rank is None or _compare_rule_ranks(rank, best_rank) > 0:
            best_rank = rank
            best_rule = rule

    return best_rule


def create_runtime_controls(config: dict[str, Any] | None = None) -> DotDict:
    resolved = _resolve_config(config)

    loop_store = _create_state_store(_dict_get(resolved["state"], "loop"))
    circuit_store = _create_state_store(_dict_get(resolved["state"], "circuit"))
    budget_store = _create_state_store(_dict_get(resolved["state"], "budget"))
    lock_store = _create_state_store(_dict_get(resolved["state"], "lock"))
    idempotency_store = _create_state_store(_dict_get(resolved["state"], "idempotency"))

    loop_prefix = f"{resolved['tenantKey']}:loop:"
    lock_prefix = f"{resolved['tenantKey']}:lock:"
    idempotency_prefix = f"{resolved['tenantKey']}:idempotency:"

    async def _run_on_event_sink_failure(params: dict[str, Any]) -> None:
        callback = resolved.get("onEventSinkFailure")
        if not callable(callback):
            return

        try:
            maybe = callback(params)
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception:
            return

    def emit_event(event: dict[str, Any]) -> None:
        emitted = {**event, "timestamp": _now_ms()}

        on_event = resolved.get("onEvent")
        if callable(on_event):
            try:
                maybe = on_event(emitted)
                if asyncio.iscoroutine(maybe):
                    asyncio.create_task(_maybe_await(maybe))
            except Exception:
                pass

        event_sinks = resolved.get("eventSinks", [])
        if not isinstance(event_sinks, list) or len(event_sinks) == 0:
            return

        for sink_index, sink in enumerate(event_sinks):
            if not callable(sink):
                continue

            async def _fanout(target: Callable[[dict[str, Any]], Any], idx: int) -> None:
                try:
                    await _maybe_await(target(emitted))
                except Exception as failure:
                    await _run_on_event_sink_failure(
                        {
                            "failure": failure,
                            "event": emitted,
                            "sinkIndex": idx,
                        }
                    )

            asyncio.create_task(_fanout(sink, sink_index))

    def get_loop_state_key(fingerprint: str) -> str:
        return f"{loop_prefix}{fingerprint}"

    def normalize_run_key(run_key: str | None = None) -> str:
        if not run_key:
            return "default"
        trimmed = run_key.strip()
        return trimmed if trimmed else "default"

    def get_run_budget_key(run_key: str) -> str:
        return f"{resolved['tenantKey']}:budget:{run_key}"

    def get_lock_state_key(resource_key: str) -> str:
        return f"{lock_prefix}{_digest_stable(resource_key)}"

    def get_verifier_base_context(context: dict[str, Any]) -> dict[str, Any]:
        return {
            "toolName": _dict_get(context, "toolName", "tool_name"),
            "runKey": _dict_get(context, "runKey", "run_key"),
            "destination": _dict_get(context, "destination"),
            "action": _dict_get(context, "action"),
            "args": _dict_get(context, "args"),
            "idempotencyKey": _dict_get(context, "idempotencyKey", "idempotency_key"),
            "resourceKey": _dict_get(context, "resourceKey", "resource_key"),
        }

    async def enforce_before_verifier(context: dict[str, Any]) -> None:
        verifier = _dict_get(resolved["verifiers"], "beforeCall")
        if not callable(verifier):
            return

        decision = _normalize_verifier_decision(await _maybe_await(verifier(get_verifier_base_context(context))))
        if decision["allow"]:
            return

        reason = decision.get("reason") or "before-call verifier rejected tool call"
        emit_event(
            {
                "type": "verifier_rejected",
                "message": reason,
                "details": {
                    "phase": "before_call",
                    "toolName": _dict_get(context, "toolName"),
                    "destination": _dict_get(context, "destination"),
                    "action": _dict_get(context, "action"),
                },
            }
        )
        raise _make_failure(f"Verifier rejected tool call: {reason}", "INVALID_REQUEST")

    async def enforce_success_verifier(context: dict[str, Any], result: Any) -> None:
        verifier = _dict_get(resolved["verifiers"], "afterSuccess")
        if not callable(verifier):
            return

        payload = {**get_verifier_base_context(context), "result": result}
        decision = _normalize_verifier_decision(await _maybe_await(verifier(payload)))
        if decision["allow"]:
            return

        reason = decision.get("reason") or "success verifier rejected tool result"
        emit_event(
            {
                "type": "verifier_rejected",
                "message": reason,
                "details": {
                    "phase": "after_success",
                    "toolName": _dict_get(context, "toolName"),
                    "destination": _dict_get(context, "destination"),
                    "action": _dict_get(context, "action"),
                },
            }
        )
        raise _make_failure(f"Verifier rejected tool result: {reason}", "INVALID_REQUEST")

    async def apply_error_verifier(context: dict[str, Any], normalized_error: Exception, raw_error: Any) -> Exception:
        verifier = _dict_get(resolved["verifiers"], "afterError")
        if not callable(verifier):
            return normalized_error

        payload = {
            **get_verifier_base_context(context),
            "error": {
                "message": str(normalized_error),
                "code": getattr(normalized_error, "code", "UNKNOWN_ERROR"),
                "statusCode": getattr(normalized_error, "status_code", getattr(normalized_error, "statusCode", None)),
            },
            "rawError": raw_error,
        }
        decision = _normalize_verifier_decision(await _maybe_await(verifier(payload)))
        if decision["allow"]:
            return normalized_error

        reason = decision.get("reason") or "error verifier rejected tool error"
        emit_event(
            {
                "type": "verifier_rejected",
                "message": reason,
                "details": {
                    "phase": "after_error",
                    "toolName": _dict_get(context, "toolName"),
                    "destination": _dict_get(context, "destination"),
                    "action": _dict_get(context, "action"),
                    "originalCode": getattr(normalized_error, "code", "UNKNOWN_ERROR"),
                },
            }
        )
        return _make_failure(f"Verifier rejected tool error: {reason}", "INVALID_REQUEST")

    def get_idempotency_state_key(context: dict[str, Any]) -> str | None:
        if not resolved["idempotency"]["enabled"]:
            return None

        idempotency_key = _dict_get(context, "idempotencyKey", "idempotency_key")
        if not isinstance(idempotency_key, str):
            return None

        trimmed = idempotency_key.strip()
        if not trimmed:
            return None

        scope = normalize_run_key(_dict_get(context, "runKey", "run_key")) if resolved["idempotency"]["namespaceByRunKey"] else "global"
        key_hash = _digest_stable(trimmed)
        return f"{idempotency_prefix}{scope}:{_dict_get(context, 'toolName')}:{key_hash}"

    async def read_idempotency_record(context: dict[str, Any]) -> dict[str, Any] | None:
        state_key = get_idempotency_state_key(context)
        if not state_key:
            return None

        record = await idempotency_store.get(state_key)
        if not isinstance(record, dict):
            return None

        now = _now_ms()
        expires_at = record.get("expiresAt")
        if isinstance(expires_at, int) and expires_at <= now:
            await idempotency_store.delete(state_key)
            return None

        return record

    async def store_idempotency_success(context: dict[str, Any], result: Any) -> None:
        state_key = get_idempotency_state_key(context)
        if not state_key:
            return

        now = _now_ms()
        ttl_ms = resolved["idempotency"]["ttlMs"]
        payload = {
            "storedAt": now,
            "expiresAt": (now + ttl_ms) if isinstance(ttl_ms, int) else None,
            "ok": True,
            "result": result,
        }
        await idempotency_store.set(state_key, payload)

    async def store_idempotency_error(context: dict[str, Any], error: Exception) -> None:
        if not resolved["idempotency"]["includeErrors"]:
            return

        state_key = get_idempotency_state_key(context)
        if not state_key:
            return

        now = _now_ms()
        ttl_ms = resolved["idempotency"]["ttlMs"]
        payload = {
            "storedAt": now,
            "expiresAt": (now + ttl_ms) if isinstance(ttl_ms, int) else None,
            "ok": False,
            "error": {
                "message": str(error),
                "code": getattr(error, "code", "UNKNOWN_ERROR"),
                "statusCode": getattr(error, "status_code", getattr(error, "statusCode", None)),
            },
        }
        await idempotency_store.set(state_key, payload)

    async def try_replay_idempotency(context: dict[str, Any]) -> tuple[bool, Any]:
        state_key = get_idempotency_state_key(context)
        if not state_key:
            return (False, None)

        record = await read_idempotency_record(context)
        if not record:
            return (False, None)

        emit_event(
            {
                "type": "idempotency_replay",
                "message": f"Replayed idempotent result for {_dict_get(context, 'toolName')}",
                "details": {
                    "toolName": _dict_get(context, "toolName"),
                    "runKey": normalize_run_key(_dict_get(context, "runKey", "run_key")),
                    "hadError": not bool(record.get("ok", False)),
                },
            }
        )

        if bool(record.get("ok")):
            return (True, record.get("result"))

        record_error = _as_dict(record.get("error"))
        if record_error:
            raise _make_failure(
                str(record_error.get("message", "Replayed idempotent tool error")),
                str(record_error.get("code", "UNKNOWN_ERROR")),
                cast(int | None, record_error.get("statusCode")),
            )

        raise _make_failure("Replayed idempotent tool error", "UNKNOWN_ERROR")

    async def acquire_resource_lock(context: dict[str, Any], minimum_lease_ms: int) -> dict[str, str] | None:
        if not resolved["concurrency"]["enabled"]:
            return None

        resource_key = _dict_get(context, "resourceKey", "resource_key")
        if not isinstance(resource_key, str):
            return None

        resource_key = resource_key.strip()
        if not resource_key:
            return None

        key = get_lock_state_key(resource_key)
        owner = f"{_now_ms()}:{random.random():.8f}".replace("0.", "")
        lease_ms = max(int(resolved["concurrency"]["leaseMs"]), minimum_lease_ms)

        async def try_acquire() -> bool:
            now = _now_ms()
            existing = await lock_store.get(key)
            if not isinstance(existing, dict) or not isinstance(existing.get("expiresAt"), int) or int(existing["expiresAt"]) <= now:
                await lock_store.set(
                    key,
                    {
                        "owner": owner,
                        "expiresAt": now + lease_ms,
                    },
                )
                return True
            return False

        if await try_acquire():
            return {"key": key, "owner": owner}

        if resolved["concurrency"]["waitMode"] == "reject":
            emit_event(
                {
                    "type": "concurrency_rejected",
                    "message": "Concurrency lock is already held",
                    "details": {
                        "toolName": _dict_get(context, "toolName"),
                        "resourceKey": resource_key,
                        "waitMode": "reject",
                    },
                }
            )
            raise _make_failure("Concurrency lock is already held for resource", "INVALID_REQUEST")

        emit_event(
            {
                "type": "concurrency_wait",
                "message": "Waiting for concurrency lock",
                "details": {
                    "toolName": _dict_get(context, "toolName"),
                    "resourceKey": resource_key,
                    "waitTimeoutMs": resolved["concurrency"]["waitTimeoutMs"],
                },
            }
        )

        started_at = _now_ms()
        while True:
            if await try_acquire():
                return {"key": key, "owner": owner}

            elapsed = _now_ms() - started_at
            if elapsed >= int(resolved["concurrency"]["waitTimeoutMs"]):
                emit_event(
                    {
                        "type": "concurrency_rejected",
                        "message": "Concurrency lock wait timeout",
                        "details": {
                            "toolName": _dict_get(context, "toolName"),
                            "resourceKey": resource_key,
                            "waitMode": "wait",
                            "waitTimeoutMs": resolved["concurrency"]["waitTimeoutMs"],
                            "elapsedMs": elapsed,
                        },
                    }
                )
                raise _make_failure("Concurrency lock wait timeout", "INVALID_REQUEST")

            try:
                await _sleep_with_abort(int(resolved["concurrency"]["pollIntervalMs"]), _dict_get(context, "signal"))
            except Exception:
                raise _make_failure("Tool call cancelled by caller", "NETWORK_ERROR")

    async def release_resource_lock(lock_ref: dict[str, str] | None) -> None:
        if not lock_ref:
            return

        state = await lock_store.get(lock_ref["key"])
        if not isinstance(state, dict):
            return

        if state.get("owner") != lock_ref["owner"]:
            return

        await lock_store.delete(lock_ref["key"])

    async def prune_loop_states(loop_config: dict[str, Any]) -> None:
        keys = [key for key in await loop_store.keys() if key.startswith(loop_prefix)]
        if len(keys) <= int(loop_config["maxFingerprints"]):
            return

        oldest_key: str | None = None
        oldest_timestamp = float("inf")

        for key in keys:
            state = await loop_store.get(key)
            if not isinstance(state, dict):
                continue
            last_seen_at = state.get("lastSeenAt")
            if not isinstance(last_seen_at, int):
                continue
            if last_seen_at < oldest_timestamp:
                oldest_timestamp = float(last_seen_at)
                oldest_key = key

        if oldest_key:
            await loop_store.delete(oldest_key)

    async def enforce_loop_before_call(fingerprint: str, loop_config: dict[str, Any]) -> None:
        if not loop_config["enabled"]:
            return

        loop_state_key = get_loop_state_key(fingerprint)
        state = await loop_store.get(loop_state_key)
        if not isinstance(state, dict):
            return

        now = _now_ms()
        stop_until = state.get("stopUntil")
        if isinstance(stop_until, int) and stop_until > now:
            raise _make_failure("Loop breaker blocked repeated no-progress tool pattern", "INVALID_REQUEST")

        quarantine_until = state.get("quarantineUntil")
        if isinstance(quarantine_until, int) and quarantine_until > now:
            raise _make_failure("Loop breaker quarantined repeated tool pattern", "INVALID_REQUEST")

        state["lastSeenAt"] = now
        await loop_store.set(loop_state_key, state)

    async def record_loop_outcome(params: dict[str, Any], loop_config: dict[str, Any]) -> None:
        if not loop_config["enabled"]:
            return

        now = _now_ms()
        loop_state_key = get_loop_state_key(str(params["fingerprint"]))
        state = await loop_store.get(loop_state_key)
        if not isinstance(state, dict):
            state = {
                "streak": 0,
                "lastSeenAt": now,
            }

        if state.get("lastOutcomeHash") == params["outcomeHash"]:
            state["streak"] = int(state.get("streak", 0)) + 1
        else:
            state["streak"] = 1
            state["lastOutcomeHash"] = params["outcomeHash"]
            state["quarantineUntil"] = None
            state["stopUntil"] = None

        state["lastSeenAt"] = now

        streak = int(state["streak"])

        if streak >= int(loop_config["stopThreshold"]):
            previous = int(state.get("stopUntil") or 0)
            state["stopUntil"] = now + int(loop_config["stopCooldownMs"])
            if previous <= now:
                emit_event(
                    {
                        "type": "loop_stop",
                        "message": f"Loop breaker stop threshold reached for {params['toolName']}",
                        "details": {
                            "toolName": params["toolName"],
                            "streak": streak,
                            "stopUntil": state["stopUntil"],
                            "statusCode": params.get("statusCode"),
                        },
                    }
                )
        elif streak >= int(loop_config["quarantineThreshold"]):
            previous = int(state.get("quarantineUntil") or 0)
            state["quarantineUntil"] = now + int(loop_config["quarantineMs"])
            if previous <= now:
                emit_event(
                    {
                        "type": "loop_quarantine",
                        "message": f"Loop breaker quarantine threshold reached for {params['toolName']}",
                        "details": {
                            "toolName": params["toolName"],
                            "streak": streak,
                            "quarantineUntil": state["quarantineUntil"],
                            "statusCode": params.get("statusCode"),
                        },
                    }
                )
        elif streak >= int(loop_config["warningThreshold"]):
            emit_event(
                {
                    "type": "loop_warning",
                    "message": f"Loop breaker warning threshold reached for {params['toolName']}",
                    "details": {
                        "toolName": params["toolName"],
                        "streak": streak,
                        "statusCode": params.get("statusCode"),
                    },
                }
            )

        await loop_store.set(loop_state_key, state)
        await prune_loop_states(loop_config)

    def get_circuit_key(tool_name: str, destination: str | None) -> str:
        normalized_destination = _normalize_destination(destination) or "default"
        return f"{resolved['tenantKey']}:{tool_name}:{normalized_destination}"

    async def enforce_circuit_before_call(tool_name: str, destination: str | None, circuit_config: dict[str, Any]) -> None:
        if not circuit_config["enabled"]:
            return

        key = get_circuit_key(tool_name, destination)
        state = await circuit_store.get(key)
        now = _now_ms()
        if isinstance(state, dict) and isinstance(state.get("openUntil"), int) and int(state["openUntil"]) > now:
            raise _make_failure("Dependency temporarily unavailable (circuit breaker open)", "NETWORK_ERROR")

    async def record_circuit_call(params: dict[str, Any], circuit_config: dict[str, Any]) -> None:
        if not circuit_config["enabled"]:
            return

        now = _now_ms()
        key = get_circuit_key(str(params["toolName"]), cast(str | None, params.get("destination")))
        state = await circuit_store.get(key)
        if not isinstance(state, dict):
            state = {"samples": []}

        samples = state.get("samples")
        if not isinstance(samples, list):
            samples = []

        samples.append({"timestamp": now, "failed": bool(params.get("failed"))})
        min_timestamp = now - int(circuit_config["windowMs"])
        samples = [sample for sample in samples if isinstance(sample, dict) and int(sample.get("timestamp", 0)) >= min_timestamp]
        state["samples"] = samples

        if len(samples) >= int(circuit_config["minRequests"]):
            failure_count = len([sample for sample in samples if bool(sample.get("failed"))])
            failure_rate = failure_count / len(samples)
            if failure_rate >= float(circuit_config["failureRateThreshold"]):
                previous = int(state.get("openUntil") or 0)
                state["openUntil"] = now + int(circuit_config["cooldownMs"])
                if previous <= now:
                    emit_event(
                        {
                            "type": "circuit_open",
                            "message": f"Circuit breaker opened for {params['toolName']}",
                            "details": {
                                "key": key,
                                "failureCount": failure_count,
                                "total": len(samples),
                                "failureRate": failure_rate,
                                "openUntil": state["openUntil"],
                            },
                        }
                    )

        await circuit_store.set(key, state)

    async def enforce_run_budget(context: dict[str, Any]) -> None:
        max_tool_calls = resolved.get("maxToolCalls")
        if not isinstance(max_tool_calls, int):
            return

        run_key = normalize_run_key(_dict_get(context, "runKey", "run_key"))
        budget_key = get_run_budget_key(run_key)
        state = await budget_store.get(budget_key)
        if not isinstance(state, dict):
            state = {"count": 0}

        count = int(state.get("count", 0))
        if count >= max_tool_calls:
            emit_event(
                {
                    "type": "budget_stop",
                    "message": f"Tool-call budget exceeded for run \"{run_key}\"",
                    "details": {
                        "runKey": run_key,
                        "toolName": _dict_get(context, "toolName"),
                        "maxToolCalls": max_tool_calls,
                        "usedCalls": count,
                    },
                }
            )
            raise _make_failure(
                f"Tool-call budget exceeded for run \"{run_key}\" ({max_tool_calls} max calls)",
                "INVALID_REQUEST",
            )

        state["count"] = count + 1
        await budget_store.set(budget_key, state)

    async def enforce_policy(context: dict[str, Any]) -> None:
        policy_config = resolved["policy"]
        if not policy_config["enabled"] or not policy_config["rules"]:
            return

        matching_rule = _find_matching_tool_rule(cast(list[dict[str, Any]], policy_config["rules"]), context)
        if not matching_rule:
            return

        reason = str(matching_rule.get("reason") or "Policy blocked tool call")
        action = cast(RuntimePolicyAction, matching_rule.get("action"))

        if action == "allow":
            return

        if policy_config.get("mode") == "dryRun":
            emit_event(
                {
                    "type": "policy_dry_run",
                    "message": reason,
                    "details": {
                        "ruleId": matching_rule.get("id"),
                        "toolName": _dict_get(context, "toolName"),
                        "destination": _dict_get(context, "destination"),
                        "action": _dict_get(context, "action"),
                        "simulatedAction": action,
                    },
                }
            )
            return

        if action == "deny":
            emit_event(
                {
                    "type": "policy_denied",
                    "message": reason,
                    "details": {
                        "ruleId": matching_rule.get("id"),
                        "toolName": _dict_get(context, "toolName"),
                        "destination": _dict_get(context, "destination"),
                        "action": _dict_get(context, "action"),
                    },
                }
            )
            raise _make_failure(f"Policy denied tool call: {reason}", "UNAUTHORIZED", 403)

        emit_event(
            {
                "type": "policy_approval_required",
                "message": reason,
                "details": {
                    "ruleId": matching_rule.get("id"),
                    "toolName": _dict_get(context, "toolName"),
                    "destination": _dict_get(context, "destination"),
                    "action": _dict_get(context, "action"),
                },
            }
        )

        approval_handler = policy_config.get("approvalHandler")
        if not callable(approval_handler):
            raise _make_failure(
                f"Policy requires approval but no approvalHandler is configured: {reason}",
                "UNAUTHORIZED",
                403,
            )

        approved = await _maybe_await(
            approval_handler(
                {
                    "ruleId": matching_rule.get("id"),
                    "reason": matching_rule.get("reason"),
                    "toolName": _dict_get(context, "toolName"),
                    "destination": _dict_get(context, "destination"),
                    "action": _dict_get(context, "action"),
                    "args": _dict_get(context, "args"),
                }
            )
        )

        if not approved:
            emit_event(
                {
                    "type": "policy_denied",
                    "message": "Tool call approval denied",
                    "details": {
                        "ruleId": matching_rule.get("id"),
                        "toolName": _dict_get(context, "toolName"),
                    },
                }
            )
            raise _make_failure("Tool call approval denied by policy handler", "UNAUTHORIZED", 403)

        emit_event(
            {
                "type": "policy_approved",
                "message": "Tool call approved by policy handler",
                "details": {
                    "ruleId": matching_rule.get("id"),
                    "toolName": _dict_get(context, "toolName"),
                },
            }
        )

    async def resolve_retry_decision(params: dict[str, Any]) -> dict[str, Any]:
        fallback = {"retryable": bool(params["defaultRetryable"])}

        retry_classifier = resolved.get("retryClassifier")
        if not callable(retry_classifier):
            return fallback

        classifier_input = {
            "error": {
                "message": str(params["normalizedError"]),
                "code": getattr(params["normalizedError"], "code", "UNKNOWN_ERROR"),
                "statusCode": getattr(
                    params["normalizedError"],
                    "status_code",
                    getattr(params["normalizedError"], "statusCode", None),
                ),
            },
            "rawError": params["rawError"],
            "statusCode": params.get("statusCode"),
            "cancelledByCaller": params["cancelledByCaller"],
            "attempt": params["attempt"],
            "maxAttempts": params["maxAttempts"],
            "toolName": _dict_get(params["context"], "toolName"),
            "destination": _dict_get(params["context"], "destination"),
            "action": _dict_get(params["context"], "action"),
        }

        result = await _maybe_await(retry_classifier(classifier_input))

        if isinstance(result, bool):
            return {"retryable": result}

        if not isinstance(result, dict):
            return fallback

        delay_ms = result.get("delayMs")
        normalized_delay = None
        if _is_number(delay_ms) and float(delay_ms) >= 0:
            normalized_delay = int(round(float(delay_ms)))

        return {
            "retryable": bool(result.get("retryable")),
            "reason": result.get("reason") if isinstance(result.get("reason"), str) else None,
            "delayMs": normalized_delay,
        }

    async def run(context: dict[str, Any], fn: Callable[[dict[str, Any]], Awaitable[Any]]) -> Any:
        if not isinstance(context, dict):
            raise _make_failure("run() requires context dictionary", "VALIDATION_ERROR")

        tool_name = _dict_get(context, "toolName", "tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            raise _make_failure("run() requires context.toolName", "VALIDATION_ERROR")

        effective = _resolve_effective_call_config(resolved, context)
        fingerprint = _build_fingerprint(tool_name, _dict_get(context, "args"))

        await enforce_policy(context)
        await enforce_before_verifier(context)

        replay_hit, replay_value = await try_replay_idempotency(context)
        if replay_hit:
            return replay_value

        await enforce_run_budget(context)
        await enforce_loop_before_call(fingerprint, effective["loopBreaker"])

        lock_ref = await acquire_resource_lock(
            context,
            (int(effective["timeoutMs"]) + 1000) if int(effective["timeoutMs"]) > 0 else int(resolved["concurrency"]["leaseMs"]),
        )

        try:
            retry = effective["retry"]
            for attempt in range(1, int(retry["maxAttempts"]) + 1):
                await enforce_circuit_before_call(tool_name, _dict_get(context, "destination"), effective["circuitBreaker"])

                timeout_ms = int(effective["timeoutMs"])
                run_signal_ref = _create_run_signal(timeout_ms, _dict_get(context, "signal"))
                error_phase: Literal["execute", "after_success"] = "execute"

                try:
                    result = await _race_with_abort(run_signal_ref.signal, lambda: fn({"signal": run_signal_ref.signal}))

                    await record_circuit_call(
                        {
                            "toolName": tool_name,
                            "destination": _dict_get(context, "destination"),
                            "failed": False,
                        },
                        effective["circuitBreaker"],
                    )

                    error_phase = "after_success"
                    await enforce_success_verifier(context, result)
                    await store_idempotency_success(context, result)

                    await record_loop_outcome(
                        {
                            "fingerprint": fingerprint,
                            "outcomeHash": _build_outcome_hash({"ok": True, "data": result}),
                            "toolName": tool_name,
                        },
                        effective["loopBreaker"],
                    )
                    return result

                except Exception as raw_error:
                    status_code = _extract_status_code(raw_error)
                    cancelled_by_caller = _signal_aborted(_dict_get(context, "signal"))
                    normalized = _normalize_failure(
                        raw_error,
                        {
                            "didTimeout": run_signal_ref.did_timeout(),
                            "cancelledByCaller": cancelled_by_caller,
                            "statusCode": status_code,
                        },
                    )
                    normalized = await apply_error_verifier(context, normalized, raw_error)

                    if error_phase == "execute":
                        await record_circuit_call(
                            {
                                "toolName": tool_name,
                                "destination": _dict_get(context, "destination"),
                                "failed": True,
                            },
                            effective["circuitBreaker"],
                        )

                    normalized_status_code = status_code if status_code is not None else getattr(
                        normalized,
                        "status_code",
                        getattr(normalized, "statusCode", None),
                    )
                    default_retryable = (
                        attempt < int(retry["maxAttempts"]) and _should_retry_failure(normalized, normalized_status_code, cancelled_by_caller)
                    )

                    retry_decision = await resolve_retry_decision(
                        {
                            "context": context,
                            "rawError": raw_error,
                            "normalizedError": normalized,
                            "statusCode": normalized_status_code,
                            "attempt": attempt,
                            "maxAttempts": int(retry["maxAttempts"]),
                            "cancelledByCaller": cancelled_by_caller,
                            "defaultRetryable": default_retryable,
                        }
                    )

                    can_retry = attempt < int(retry["maxAttempts"]) and bool(retry_decision.get("retryable"))

                    if not can_retry:
                        await record_loop_outcome(
                            {
                                "fingerprint": fingerprint,
                                "outcomeHash": _build_outcome_hash(
                                    {
                                        "ok": False,
                                        "statusCode": normalized_status_code,
                                        "code": getattr(normalized, "code", "UNKNOWN_ERROR"),
                                        "message": str(normalized),
                                    }
                                ),
                                "toolName": tool_name,
                                "statusCode": normalized_status_code,
                            },
                            effective["loopBreaker"],
                        )
                        await store_idempotency_error(context, normalized)
                        raise normalized

                    delay_ms = (
                        int(retry_decision["delayMs"])
                        if isinstance(retry_decision.get("delayMs"), int)
                        else _compute_backoff_delay(retry, attempt)
                    )

                    emit_event(
                        {
                            "type": "retry",
                            "message": f"Retrying tool call {tool_name} (attempt {attempt + 1}/{retry['maxAttempts']})",
                            "details": {
                                "toolName": tool_name,
                                "delayMs": delay_ms,
                                "statusCode": normalized_status_code,
                                "reason": str(normalized),
                                "classifierReason": retry_decision.get("reason"),
                            },
                        }
                    )

                    try:
                        await _sleep_with_abort(delay_ms, _dict_get(context, "signal"))
                    except Exception:
                        raise _make_failure("Tool call cancelled by caller", "NETWORK_ERROR")

                finally:
                    run_signal_ref.cleanup()

            raise _make_failure("Tool call failed after retries", "NETWORK_ERROR")
        finally:
            await release_resource_lock(lock_ref)

    def wrap(params: dict[str, Any]) -> Callable[..., Awaitable[Any]]:
        handler = _dict_get(params, "run", "fn", "function")
        if not callable(handler):
            raise _make_failure('wrap() requires a "run", "fn", or "function" property', "VALIDATION_ERROR")

        async def wrapped(*args: Any) -> Any:
            resolve_run_key = _dict_get(params, "resolveRunKey", "resolve_run_key")
            run_key = await _maybe_await(resolve_run_key(*args)) if callable(resolve_run_key) else _dict_get(params, "runKey", "run_key")

            resolve_destination = _dict_get(params, "resolveDestination", "resolve_destination")
            destination = await _maybe_await(resolve_destination(*args)) if callable(resolve_destination) else _dict_get(params, "destination")

            resolve_action = _dict_get(params, "resolveAction", "resolve_action")
            action = await _maybe_await(resolve_action(*args)) if callable(resolve_action) else None

            resolve_idempotency_key = _dict_get(params, "resolveIdempotencyKey", "resolve_idempotency_key")
            idempotency_key = (
                await _maybe_await(resolve_idempotency_key(*args))
                if callable(resolve_idempotency_key)
                else _dict_get(params, "idempotencyKey", "idempotency_key")
            )

            resolve_resource_key = _dict_get(params, "resolveResourceKey", "resolve_resource_key")
            resource_key = (
                await _maybe_await(resolve_resource_key(*args))
                if callable(resolve_resource_key)
                else _dict_get(params, "resourceKey", "resource_key")
            )

            return await run(
                {
                    "toolName": _dict_get(params, "toolName", "tool_name"),
                    "runKey": run_key,
                    "destination": destination,
                    "action": action,
                    "args": args,
                    "idempotencyKey": idempotency_key,
                    "resourceKey": resource_key,
                },
                lambda runtime: handler(args, runtime),
            )

        return wrapped

    async def reset(run_key: str | None = None) -> None:
        normalized = normalize_run_key(run_key)
        await budget_store.delete(get_run_budget_key(normalized))

    return DotDict(
        {
            "run": run,
            "wrap": wrap,
            "reset": reset,
        }
    )

RuntimeControls = DotDict(
    {
        "create": create_runtime_controls,
    }
)


__all__ = [
    "RuntimeControls",
    "create_abort_controller",
]
