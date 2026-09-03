"""Make project-root modules (crew.py, crawler.py, cli_ui.py...) importable from tests.

The installable library lives under ``src/job_automation``; this conftest also
puts the project root on ``sys.path`` so the standalone entry-point modules can
be imported for pure-logic unit tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
