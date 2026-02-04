"""Resolve code parameter - supports both inline code and file paths.

Relative paths (./foo.py, ../bar.py) are resolved relative to the caller's
file location, not the current working directory.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from buildfunctions.errors import ValidationError

# SDK directory - used to skip SDK frames when finding caller
_SDK_DIR = Path(__file__).parent.resolve()

CODE_EXTENSIONS = frozenset({
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",  # JavaScript & TypeScript
    ".py", ".pyw", ".pyi",                          # Python
})


def get_caller_file() -> Path | None:
    """Get the file path of the caller (the file that called the SDK).

    Used to resolve relative paths against the caller's location.
    Skips frames that are inside the SDK itself.
    """
    for frame_info in inspect.stack():
        frame_path = Path(frame_info.filename).resolve()

        # Skip frames inside the SDK directory
        try:
            frame_path.relative_to(_SDK_DIR)
            continue  # Frame is inside SDK, skip it
        except ValueError:
            pass  # Frame is outside SDK

        # Skip frames that don't have a real file
        if not frame_path.exists():
            continue

        return frame_path

    return None


def _looks_like_file_path(value: str) -> bool:
    """Check if a string looks like a file path."""
    if value.startswith(("/", "./", "../", "~")):
        return True
    # Windows drive letter
    if len(value) >= 3 and value[1] == ":" and value[2] in ("/", "\\"):
        return True
    # Ends with known code file extension
    dot_index = value.rfind(".")
    if dot_index > 0:
        ext = value[dot_index:].lower()
        if ext in CODE_EXTENSIONS:
            return True
    return False


async def resolve_code(code: str, base_path: Path | None = None) -> str:
    """Resolve code string - reads from file if it's a path, returns as-is if inline.

    Args:
        code: Either inline code or a file path
        base_path: Base directory for resolving relative paths (e.g., caller's directory).
                   If not provided, automatically detects caller's file location.

    Detection heuristic:
    1. If the string contains a newline, treat as inline code.
    2. If the resolved path exists on disk, read and return the file contents.
    3. If it looks like a path but does not exist, raise ValidationError.
    4. Otherwise treat as single-line inline code.
    """
    if "\n" in code:
        return code

    # Expand ~ to home directory
    path_to_check = Path(code).expanduser()

    # Resolve the path:
    # - Absolute paths stay absolute
    # - Relative paths resolve against base_path or caller's directory
    if path_to_check.is_absolute():
        resolved = path_to_check.resolve()
    elif base_path:
        resolved = (base_path / path_to_check).resolve()
    else:
        # Auto-detect caller's directory for relative paths
        caller_file = get_caller_file()
        if caller_file:
            resolved = (caller_file.parent / path_to_check).resolve()
        else:
            resolved = path_to_check.resolve()

    if resolved.exists() and resolved.is_file():
        return resolved.read_text(encoding="utf-8")

    if _looks_like_file_path(code):
        raise ValidationError(
            f'Code file not found: "{code}" (resolved to "{resolved}"). '
            f"If this is meant to be inline code, ensure it is a valid code string."
        )

    # Single-line inline code
    return code
