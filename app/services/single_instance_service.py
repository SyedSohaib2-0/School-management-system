"""Single-instance guard for the desktop application.

The school database, automatic-backup scheduler, and staged restore workflow are
all designed for one interactive application process.  A QLockFile in the
per-user application-data folder prevents two copies from operating on the same
live data at the same time.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QLockFile

from app.config import APP_DATA_DIR


APPLICATION_LOCK_PATH = os.path.join(APP_DATA_DIR, "application.lock")


def acquire_application_lock() -> QLockFile | None:
    """Acquire and return the process-lifetime lock, or ``None`` if busy."""
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    lock = QLockFile(APPLICATION_LOCK_PATH)
    # Do not expire a valid long-running school session merely because the
    # lock file is old. QLockFile still detects a dead local process.
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return None
    return lock
