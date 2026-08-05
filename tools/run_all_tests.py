#!/usr/bin/env python3
"""Run unit, integration, and regression tests against isolated data only."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    project_text = str(PROJECT_ROOT)
    if project_text not in sys.path:
        sys.path.insert(0, project_text)
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(PROJECT_ROOT / "tests"),
        pattern="test_*.py",
        top_level_dir=str(PROJECT_ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
