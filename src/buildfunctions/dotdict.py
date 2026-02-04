"""DotDict - A dict subclass that supports both dot notation and bracket notation.

Allows accessing dict keys as attributes:
    d = DotDict({"name": "test", "id": 123})
    d.name  # "test"
    d["name"]  # "test"
    d.get("name")  # "test"
"""

from __future__ import annotations

from typing import Any


class DotDict(dict):
    """Dict that supports attribute access (dot notation) in addition to bracket notation."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
            # Recursively wrap nested dicts
            if isinstance(value, dict) and not isinstance(value, DotDict):
                value = DotDict(value)
                self[key] = value
            return value
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")

    def __setattr__(self, key: str, value: Any) -> None:
        self[key] = value

    def __delattr__(self, key: str) -> None:
        try:
            del self[key]
        except KeyError:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{key}'")

    def __repr__(self) -> str:
        return f"DotDict({super().__repr__()})"
