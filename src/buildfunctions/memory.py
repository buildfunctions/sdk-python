"""Memory string parsing utility."""

from __future__ import annotations

import re


def parse_memory(memory: str | int) -> int:
    """Parse memory string to megabytes.

    Accepts: "2GB", "1024MB", or raw int (treated as MB).
    Returns value in MB.
    """
    if isinstance(memory, int):
        return memory

    text = memory.strip().upper()
    match = re.match(r"^(\d+)\s*(GB|MB)$", text)

    if not match:
        raise ValueError(f'Invalid memory format: "{memory}". Use "2GB" or "1024MB".')

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "GB":
        return value * 1024
    return value  # MB
