"""Agent-logic safety layer composed on top of runtime-controls config."""

from __future__ import annotations

import re
from typing import Any

from buildfunctions.runtime_controls import _dict_get, _get_callable, _maybe_await

DEFAULT_INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(all|any|previous)\s+instructions\b", re.I),
    re.compile(r"\bsystem\s+prompt\b", re.I),
    re.compile(r"\bdeveloper\s+message\b", re.I),
    re.compile(r"<script\b", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
]


def _escape_regex(value: str) -> str:
    return re.escape(value)


def _match_pattern(value: str, pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith("*"):
        return value.startswith(pattern[:-1])
    return value == pattern


def _normalize_run_key(run_key: str | None = None) -> str:
    if not run_key:
        return "default"
    trimmed = run_key.strip()
    return trimmed if trimmed else "default"


def _create_state_store(adapter: Any = None) -> dict[str, Any]:
    if adapter is None:
        state: dict[str, Any] = {}

        async def _get(key: str) -> Any:
            return state.get(key)

        async def _set(key: str, value: Any) -> None:
            state[key] = value

        return {"get": _get, "set": _set}

    async def _get(key: str) -> Any:
        get_fn = _get_callable(adapter, "get")
        if not get_fn:
            return None
        return await _maybe_await(get_fn(key))

    async def _set(key: str, value: Any) -> None:
        set_fn = _get_callable(adapter, "set")
        if not set_fn:
            return
        await _maybe_await(set_fn(key, value))

    return {"get": _get, "set": _set}


def _normalize_verifier_decision(decision: Any) -> dict[str, Any]:
    if isinstance(decision, bool):
        return {"allow": decision}
    if not isinstance(decision, dict):
        return {"allow": True}
    return {
        "allow": bool(decision.get("allow", False)),
        "reason": decision.get("reason") if isinstance(decision.get("reason"), str) else None,
    }


def _safe_serialize(value: Any) -> str:
    seen: set[int] = set()

    def transform(current: Any) -> Any:
        if isinstance(current, (str, int, float, bool)) or current is None:
            return current

        if isinstance(current, dict):
            object_id = id(current)
            if object_id in seen:
                return "[Circular]"
            seen.add(object_id)
            return {str(key): transform(subvalue) for key, subvalue in current.items()}

        if isinstance(current, (list, tuple)):
            object_id = id(current)
            if object_id in seen:
                return "[Circular]"
            seen.add(object_id)
            return [transform(item) for item in current]

        return str(current)

    try:
        import json

        return json.dumps(transform(value), sort_keys=True)
    except Exception:
        return str(value)


def _build_injection_matcher(config: dict[str, Any] | None = None) -> dict[str, Any]:
    guard = _dict_get(config or {}, "injectionGuard", "injection_guard")
    if not isinstance(guard, dict) or guard.get("enabled") is False:
        return {"enabled": False, "reason": "", "patterns": []}

    patterns: list[re.Pattern[str]] = []
    raw_patterns = guard.get("patterns")
    if isinstance(raw_patterns, list) and raw_patterns:
        for pattern in raw_patterns:
            if isinstance(pattern, re.Pattern):
                patterns.append(pattern)
            elif isinstance(pattern, str):
                patterns.append(re.compile(_escape_regex(pattern), re.I))
    else:
        patterns = list(DEFAULT_INJECTION_PATTERNS)

    return {
        "enabled": True,
        "reason": str(guard.get("reason") or "Potential prompt/tool injection pattern detected"),
        "patterns": patterns,
    }


def _matches_terminal_action(context: dict[str, Any], config: dict[str, Any] | None) -> bool:
    terminal_actions = []
    if isinstance(config, dict):
        maybe_terminal_actions = config.get("terminalActions")
        if isinstance(maybe_terminal_actions, list):
            terminal_actions = maybe_terminal_actions

    action = _dict_get(context, "action")
    if not terminal_actions or not isinstance(action, str):
        return False

    for terminal_action in terminal_actions:
        if not isinstance(terminal_action, dict):
            continue

        tool_pattern = str(terminal_action.get("toolNamePattern") or "*")
        if not _match_pattern(str(_dict_get(context, "toolName", default="")), tool_pattern):
            continue

        action_prefix = terminal_action.get("actionPrefix")
        if isinstance(action_prefix, str) and action.startswith(action_prefix):
            return True

    return False


def _build_intent_allowlist_policy_rules(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    allowlist = _dict_get(config or {}, "intentAllowlist", "intent_allowlist")
    if not isinstance(allowlist, dict) or allowlist.get("enabled") is False:
        return []

    rules = allowlist.get("rules")
    if not isinstance(rules, list) or len(rules) == 0:
        return []

    allow_rules: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue

        tool_name_pattern = rule.get("toolNamePattern")
        if not isinstance(tool_name_pattern, str) or not tool_name_pattern:
            continue

        allow_rules.append(
            {
                "id": rule.get("id") or f"agent_logic_allow_{index + 1}",
                "action": "allow",
                "tools": [tool_name_pattern],
                "actionPrefixes": rule.get("actionPrefixes"),
                "destinations": rule.get("destinations"),
                "reason": rule.get("reason"),
            }
        )

    fallback_deny = {
        "id": "agent_logic_deny_unlisted",
        "action": "deny",
        "tools": ["*"],
        "reason": allowlist.get("denyReason") or "Tool call is not in the configured intent allowlist",
    }

    return [*allow_rules, fallback_deny]


def _merge_before_call_verifiers(base_before_call: Any, safety_before_call: Any) -> Any:
    async def merged(context: dict[str, Any]) -> dict[str, Any]:
        if callable(base_before_call):
            base_decision = _normalize_verifier_decision(await _maybe_await(base_before_call(context)))
            if not base_decision["allow"]:
                return base_decision

        return await _maybe_await(safety_before_call(context))

    return merged


def apply_agent_logic_safety(
    base_config: dict[str, Any] | None = None,
    safety_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply agent-logic safety settings onto runtime-controls config.

    Mirrors TypeScript applyAgentLogicSafety(baseConfig, safetyConfig).
    """

    base_config = dict(base_config or {})
    safety_config = dict(safety_config or {})

    injection_matcher = _build_injection_matcher(safety_config)

    exit_condition = _dict_get(safety_config, "exitCondition", "exit_condition")
    exit_condition = exit_condition if isinstance(exit_condition, dict) else {}

    exit_condition_enabled = bool(exit_condition.get("enabled") is True)
    max_steps_per_run = max(1, int(round(float(exit_condition.get("maxStepsPerRun") or 30))))
    block_after_terminal = bool(exit_condition.get("blockAfterTerminal", True))
    exit_state_store = _create_state_store(exit_condition.get("stateAdapter"))

    async def safety_before_call(context: dict[str, Any]) -> dict[str, Any]:
        if injection_matcher["enabled"]:
            candidate = "\n".join(
                [
                    str(_dict_get(context, "toolName") or ""),
                    str(_dict_get(context, "action") or ""),
                    str(_dict_get(context, "destination") or ""),
                    _safe_serialize(_dict_get(context, "args")),
                ]
            )

            for pattern in injection_matcher["patterns"]:
                if pattern.search(candidate):
                    return {
                        "allow": False,
                        "reason": f"{injection_matcher['reason']} (matched: {pattern.pattern})",
                    }

        if exit_condition_enabled:
            run_key = _normalize_run_key(_dict_get(context, "runKey", "run_key"))
            state_key = f"agent_logic_exit:{run_key}"
            state = await exit_state_store["get"](state_key)
            if not isinstance(state, dict):
                state = {"steps": 0, "terminalReached": False}

            if bool(state.get("terminalReached")) and block_after_terminal:
                return {
                    "allow": False,
                    "reason": "Run already reached terminal action; further tool calls are blocked",
                }

            next_steps = int(state.get("steps", 0)) + 1
            terminal_reached = bool(state.get("terminalReached")) or _matches_terminal_action(context, exit_condition)

            await exit_state_store["set"](
                state_key,
                {
                    "steps": next_steps,
                    "terminalReached": terminal_reached,
                },
            )

            if (not terminal_reached) and next_steps > max_steps_per_run:
                return {
                    "allow": False,
                    "reason": f"Exit condition not reached within {max_steps_per_run} tool calls",
                }

        return {"allow": True}

    allowlist_policy_rules = _build_intent_allowlist_policy_rules(safety_config)
    allowlist_policy_enabled = len(allowlist_policy_rules) > 0

    base_verifiers = _dict_get(base_config, "verifiers")
    base_verifiers = base_verifiers if isinstance(base_verifiers, dict) else {}

    base_policy = _dict_get(base_config, "policy")
    base_policy = base_policy if isinstance(base_policy, dict) else {}

    merged_config = {
        **base_config,
        "verifiers": {
            **base_verifiers,
            "beforeCall": _merge_before_call_verifiers(base_verifiers.get("beforeCall"), safety_before_call),
        },
    }

    if allowlist_policy_enabled:
        merged_config["policy"] = {
            "enabled": True,
            "mode": base_policy.get("mode", "enforce"),
            "approvalHandler": base_policy.get("approvalHandler"),
            "rules": [*allowlist_policy_rules, *list(base_policy.get("rules") or [])],
        }
    else:
        merged_config["policy"] = base_policy if base_policy else _dict_get(merged_config, "policy")

    return merged_config


def applyAgentLogicSafety(
    base_config: dict[str, Any] | None = None,
    safety_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """TypeScript-cased alias for apply_agent_logic_safety()."""
    return apply_agent_logic_safety(base_config, safety_config)


__all__ = [
    "apply_agent_logic_safety",
    "applyAgentLogicSafety",
]
