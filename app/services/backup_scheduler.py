"""
Queued automatic-backup scheduler.

Rapid database commits are combined into one backup operation.
Only one backup operation can run at a time.

If another database change occurs while a backup is running,
one additional backup is scheduled after the current operation.
"""

import os
import json
import threading
import time
import tempfile
from datetime import datetime

from app.config import BACKUPS_DIR


DEFAULT_BACKUP_DELAY_SECONDS = 2.0
SHUTDOWN_BACKUP_TIMEOUT_SECONDS = 30.0
SHUTDOWN_BACKUP_POLL_SECONDS = 0.05
RETRY_DELAYS_SECONDS = (60.0, 300.0, 900.0, 3600.0)
RETRY_STATE_PATH = os.path.join(BACKUPS_DIR, "automatic_backup_retry_state.json")
RETRY_STATE_FALLBACK_PATH = os.path.join(
    tempfile.gettempdir(), "HiraModelSchool", "automatic_backup_retry_state.json"
)


_scheduler_lock = threading.RLock()

_backup_timer: threading.Timer | None = None

_backup_running = False
_backup_pending = False

_scheduler_stopping = False
_scheduler_started = False
_backup_interruption_requested = False
_pending_destinations: set[str] | None = None
_retry_attempt = 0
_change_generation = 0


def _persist_retry_state_locked() -> None:
    """Persist only unresolved backup work; caller holds the scheduler lock."""
    try:
        os.makedirs(BACKUPS_DIR, exist_ok=True)
        if not _backup_pending:
            if os.path.isfile(RETRY_STATE_PATH):
                os.remove(RETRY_STATE_PATH)
            if os.path.isfile(RETRY_STATE_FALLBACK_PATH):
                os.remove(RETRY_STATE_FALLBACK_PATH)
            return
        temporary = RETRY_STATE_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as output:
            json.dump(
                {
                    "pending": True,
                    "destinations": (
                        sorted(_pending_destinations)
                        if _pending_destinations is not None
                        else None
                    ),
                    "retry_attempt": int(_retry_attempt),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                output,
                indent=2,
            )
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, RETRY_STATE_PATH)
        if os.path.isfile(RETRY_STATE_FALLBACK_PATH):
            os.remove(RETRY_STATE_FALLBACK_PATH)
    except OSError as error:
        _write_scheduler_error(f"Could not persist backup retry state: {error}")
        try:
            os.makedirs(os.path.dirname(RETRY_STATE_FALLBACK_PATH), exist_ok=True)
            if not _backup_pending:
                if os.path.isfile(RETRY_STATE_FALLBACK_PATH):
                    os.remove(RETRY_STATE_FALLBACK_PATH)
                return
            temporary = RETRY_STATE_FALLBACK_PATH + ".tmp"
            with open(temporary, "w", encoding="utf-8") as output:
                json.dump(
                    {
                        "pending": True,
                        "destinations": sorted(_pending_destinations) if _pending_destinations is not None else None,
                        "retry_attempt": int(_retry_attempt),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    output,
                    indent=2,
                )
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, RETRY_STATE_FALLBACK_PATH)
        except OSError as fallback_error:
            _write_scheduler_error(
                f"Could not persist backup retry state to fallback storage: {fallback_error}"
            )


def _retry_delay(attempt: int) -> float:
    index = max(0, min(int(attempt), len(RETRY_DELAYS_SECONDS) - 1))
    return RETRY_DELAYS_SECONDS[index]


def _destination_requires_retry(result: dict) -> bool:
    """Return True only when the current database revision was not backed up.

    Retention cleanup can fail after a new encrypted backup was already created
    and fully verified. Retrying that destination immediately creates another
    copy of the same revision and can grow the directory indefinitely while one
    old file remains locked. Keep logging the retention warning, but retry only
    when no usable backup was produced.
    """
    return (
        not bool(result.get("success", False))
        and not bool(result.get("skipped", False))
        and not bool(result.get("backup_created", False))
    )


def _write_scheduler_error(
    message: str,
) -> None:
    """Write scheduler errors without interrupting the software."""
    try:
        os.makedirs(
            BACKUPS_DIR,
            exist_ok=True,
        )

        log_path = os.path.join(
            BACKUPS_DIR,
            "automatic_backup_errors.log",
        )

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        with open(
            log_path,
            "a",
            encoding="utf-8",
        ) as log_file:
            log_file.write(
                f"[{timestamp}] {message}\n"
            )

    except OSError:
        pass


def _start_timer_locked(
    delay_seconds: float,
) -> None:
    """
    Start the backup timer.

    The caller must already hold _scheduler_lock.
    """
    global _backup_timer

    if _scheduler_stopping or not _scheduler_started:
        return

    if (
        _backup_timer is not None
        and _backup_timer.is_alive()
    ):
        _backup_timer.cancel()

    _backup_timer = threading.Timer(
        delay_seconds,
        _execute_scheduled_backup,
    )

    _backup_timer.daemon = True
    _backup_timer.start()


def schedule_automatic_backup(
    delay_seconds: float = (
        DEFAULT_BACKUP_DELAY_SECONDS
    ),
) -> None:
    """
    Mark an automatic backup as pending.

    Repeated calls within the delay period are combined into one
    backup operation.
    """
    global _backup_pending
    global _pending_destinations
    global _retry_attempt
    global _change_generation

    try:
        safe_delay = float(
            delay_seconds
        )
    except (
        TypeError,
        ValueError,
    ):
        safe_delay = (
            DEFAULT_BACKUP_DELAY_SECONDS
        )

    if safe_delay < 0:
        safe_delay = 0

    with _scheduler_lock:
        if _scheduler_stopping:
            return

        _backup_pending = True
        _change_generation += 1
        # A new committed database revision requires every enabled destination,
        # even if an older revision was already pending for one destination.
        _pending_destinations = None
        _retry_attempt = 0
        _persist_retry_state_locked()

        if _backup_running or not _scheduler_started:
            return

        _start_timer_locked(
            safe_delay
        )


def _execute_scheduled_backup() -> None:
    """
    Execute one pending automatic backup operation.

    This function runs in a background thread.
    """
    global _backup_timer
    global _backup_running
    global _backup_pending
    global _pending_destinations
    global _retry_attempt
    global _change_generation

    with _scheduler_lock:
        _backup_timer = None

        if _scheduler_stopping:
            return

        if _backup_running:
            _backup_pending = True
            return

        if not _backup_pending:
            return

        _backup_running = True
        run_generation = int(_change_generation)
        requested_destinations = (
            set(_pending_destinations)
            if _pending_destinations is not None
            else None
        )

    try:
        from app.services.backup_service import (
            run_automatic_backups,
        )

        result = run_automatic_backups(destination_names=requested_destinations)

        failed_destinations = {
            str(item.get("destination"))
            for item in result.get("results", [])
            if _destination_requires_retry(item)
        }

        for destination_result in result.get(
            "results",
            [],
        ):
            if destination_result.get(
                "success",
                False,
            ):
                continue

            if destination_result.get(
                "skipped",
                False,
            ):
                continue

            _write_scheduler_error(
                (
                    f"{destination_result.get('destination', 'Backup')}: "
                    f"{destination_result.get('message', 'Unknown error')}"
                )
            )

    except Exception as error:
        failed_destinations = requested_destinations
        _write_scheduler_error(
            (
                "Unexpected queued automatic-backup error: "
                f"{error}"
            )
        )

    finally:
        with _scheduler_lock:
            _backup_running = False
            newer_revision_pending = int(_change_generation) > int(run_generation)
            if newer_revision_pending:
                # The completed backup represents an older committed revision.
                # Every enabled destination must receive the newest revision.
                _backup_pending = True
                _pending_destinations = None
                _retry_attempt = 0
                _persist_retry_state_locked()
                if not _scheduler_stopping:
                    _start_timer_locked(DEFAULT_BACKUP_DELAY_SECONDS)
            elif failed_destinations:
                _backup_pending = True
                _pending_destinations = set(failed_destinations)
                delay = _retry_delay(_retry_attempt)
                _retry_attempt += 1
                _persist_retry_state_locked()
                if not _scheduler_stopping:
                    _start_timer_locked(delay)
            else:
                _backup_pending = False
                _pending_destinations = None
                _retry_attempt = 0
                _persist_retry_state_locked()



def start_automatic_backup_scheduler() -> dict:
    """Start retry processing only after restore and schema startup are safe.

    Importing this module must never start background database work.  Normal
    commits made during controlled startup may mark a backup pending; this
    function starts the timer once the live database is ready.
    """
    global _scheduler_started

    with _scheduler_lock:
        if _scheduler_stopping:
            return {
                "success": False,
                "started": False,
                "message": "The backup scheduler is stopping.",
            }
        if _scheduler_started:
            return {
                "success": True,
                "started": False,
                "already_started": True,
            }
        _scheduler_started = True

    _restore_retry_state_after_restart()

    with _scheduler_lock:
        if (
            _backup_pending
            and not _backup_running
            and not (
                _backup_timer is not None
                and _backup_timer.is_alive()
            )
        ):
            _start_timer_locked(DEFAULT_BACKUP_DELAY_SECONDS)

    return {
        "success": True,
        "started": True,
        "already_started": False,
    }


def get_backup_scheduler_status() -> dict:
    """Return the current scheduler status for testing."""
    with _scheduler_lock:
        timer_active = (
            _backup_timer is not None
            and _backup_timer.is_alive()
        )

        return {
            "backup_running": _backup_running,
            "backup_pending": _backup_pending,
            "timer_active": timer_active,
            "scheduler_stopping": _scheduler_stopping,
            "scheduler_started": _scheduler_started,
            "interruption_requested": _backup_interruption_requested,
            "retry_attempt": _retry_attempt,
            "change_generation": _change_generation,
            "pending_destinations": (
                sorted(_pending_destinations)
                if _pending_destinations is not None
                else None
            ),
        }


def _execute_shutdown_backup() -> None:
    """Run one pending backup while shutdown blocks all new work."""
    global _backup_running
    global _backup_pending
    global _pending_destinations
    global _retry_attempt
    try:
        from app.services.backup_service import run_automatic_backups
        result = run_automatic_backups(destination_names=_pending_destinations)
        if not result.get("success", False):
            for destination_result in result.get("results", []):
                if destination_result.get("success") or destination_result.get("skipped"):
                    continue
                _write_scheduler_error(
                    f"Shutdown backup failure - {destination_result.get('destination', 'Backup')}: "
                    f"{destination_result.get('message', 'Unknown error')}"
                )
    except Exception as error:
        _write_scheduler_error(f"Unexpected shutdown backup error: {error}")
    finally:
        with _scheduler_lock:
            _backup_running = False
            failures = {
                str(item.get("destination"))
                for item in result.get("results", [])
                if _destination_requires_retry(item)
            } if 'result' in locals() else set(_pending_destinations or [])
            _backup_pending = bool(failures)
            _pending_destinations = failures or None
            if not failures:
                _retry_attempt = 0
            _persist_retry_state_locked()


def complete_pending_backups_before_exit(
    timeout_seconds: float = SHUTDOWN_BACKUP_TIMEOUT_SECONDS,
) -> dict:
    """Complete backup work with a bounded wait; never hang shutdown forever."""
    global _backup_timer
    global _scheduler_stopping
    global _backup_running
    global _backup_pending
    global _backup_interruption_requested

    try:
        bounded_timeout = max(0.0, float(timeout_seconds))
    except (TypeError, ValueError):
        bounded_timeout = SHUTDOWN_BACKUP_TIMEOUT_SECONDS

    with _scheduler_lock:
        _scheduler_stopping = True
        _backup_interruption_requested = False
        if _backup_timer is not None and _backup_timer.is_alive():
            _backup_timer.cancel()
        _backup_timer = None
        should_start = _backup_pending and not _backup_running
        if should_start:
            _backup_pending = False
            _backup_running = True
            worker = threading.Thread(
                target=_execute_shutdown_backup,
                name="HMS-ShutdownBackup",
                daemon=True,
            )
            worker.start()

    deadline = time.monotonic() + bounded_timeout
    while True:
        with _scheduler_lock:
            running = _backup_running
            pending = _backup_pending
        if not running:
            if not pending:
                return {
                    "success": True,
                    "timed_out": False,
                    "message": "All pending automatic backups were completed.",
                }
            # A shutdown attempt completed but one or more destinations still
            # failed. Preserve that pending state for the next application
            # start, but do not wait until the timeout for work that has
            # already finished and cannot succeed without a later retry.
            return {
                "success": False,
                "timed_out": False,
                "pending": True,
                "message": (
                    "Automatic backup remains pending for one or more "
                    "destinations and will be retried later."
                ),
            }
        if time.monotonic() >= deadline:
            with _scheduler_lock:
                _backup_interruption_requested = True
                _backup_pending = False
            message = (
                f"Automatic backup did not finish within {bounded_timeout:.1f} seconds. "
                "Shutdown will continue; the incomplete backup was not marked successful."
            )
            _write_scheduler_error(message)
            return {
                "success": False,
                "timed_out": True,
                "interruption_requested": True,
                "message": message,
            }
        time.sleep(SHUTDOWN_BACKUP_POLL_SECONDS)


def _restore_retry_state_after_restart() -> None:
    """Resume unresolved automatic backup work after application restart."""
    global _backup_pending
    global _pending_destinations
    global _retry_attempt
    retry_state_path = (
        RETRY_STATE_PATH
        if os.path.isfile(RETRY_STATE_PATH)
        else RETRY_STATE_FALLBACK_PATH
    )
    try:
        if not os.path.isfile(retry_state_path):
            return
        with open(retry_state_path, "r", encoding="utf-8") as source:
            state = json.load(source)
        if not isinstance(state, dict):
            raise ValueError("Backup retry state must be a JSON object.")
        pending_value = state.get("pending")
        if pending_value is False:
            os.remove(retry_state_path)
            return
        if pending_value is not True:
            raise ValueError("Backup retry state is missing its pending flag.")

        destinations = state.get("destinations")
        restored_destinations = None
        if destinations is not None:
            if not isinstance(destinations, list):
                raise ValueError("Backup retry destinations must be a list or null.")
            known_destinations = {
                "Internal automatic backup",
                "Selected-folder automatic backup",
                "Google Drive automatic backup",
            }
            requested = {str(value).strip() for value in destinations if str(value).strip()}
            if requested and requested.issubset(known_destinations):
                restored_destinations = requested
            else:
                # A parseable but stale/unknown destination list must not make
                # the scheduler silently drop the unresolved database revision.
                # Retry every currently enabled destination instead.
                _write_scheduler_error(
                    "Backup retry destinations were empty or unknown; "
                    "all enabled destinations will be retried."
                )

        with _scheduler_lock:
            _backup_pending = True
            _pending_destinations = restored_destinations
            _retry_attempt = max(0, int(state.get("retry_attempt", 0)))
            if _scheduler_started and not _scheduler_stopping:
                _start_timer_locked(_retry_delay(_retry_attempt))
    except Exception as error:
        _write_scheduler_error(f"Could not restore backup retry state: {error}")
        # A malformed retry-state document otherwise fails on every startup
        # forever. Preserve it for diagnosis, then allow the scheduler to
        # continue with a clean state.
        if os.path.isfile(retry_state_path):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            quarantine_path = f"{retry_state_path}.corrupt_{timestamp}"
            try:
                os.replace(retry_state_path, quarantine_path)
                _write_scheduler_error(
                    f"Damaged backup retry state was quarantined as {quarantine_path}."
                )
            except OSError as quarantine_error:
                _write_scheduler_error(
                    f"Could not quarantine damaged backup retry state: {quarantine_error}"
                )
