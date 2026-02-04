"""Framework detection from requirements string."""

from __future__ import annotations

from buildfunctions.types import Framework


def detect_framework(requirements: str | None) -> Framework | None:
    """Scan requirements for torch/pytorch.

    Returns 'pytorch' if found, None otherwise.
    Currently defaults to 'pytorch' when no requirements given.
    """
    if not requirements:
        return "pytorch"

    lower = requirements.lower()

    if "torch" in lower:
        return "pytorch"

    return None
