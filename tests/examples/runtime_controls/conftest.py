from __future__ import annotations

import sys
from pathlib import Path

# Import from local source instead of installed package.
def _project_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in current.parents:
        if (candidate / "src" / "buildfunctions").exists():
            return candidate
    raise RuntimeError("Unable to locate sdk-python project root from test path")

sys.path.insert(0, str(_project_root(Path(__file__)) / "src"))
