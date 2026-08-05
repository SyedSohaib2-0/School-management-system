"""
Application-wide crash reporting and user-facing failure handling.

The crash catcher is installed before Qt and the rest of the application are
imported. It records uncaught Python exceptions from the main thread,
background threads, and unraisable cleanup callbacks. It also enables
Python's faulthandler so low-level interpreter faults can leave a diagnostic
file when the operating system still permits one to be written.

Crash reports never include frame local-variable values. This avoids writing
passwords, CNIC values, or other school data that may exist in memory.
"""

from __future__ import annotations

import atexit
import faulthandler
import os
import platform
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4


CRASH_CATCHER_MARKER = (
    "GLOBAL_CRASH_CATCHER_V17"
)

_APP_DATA_FOLDER_NAME = "HiraModelSchool"
_CRASH_FOLDER_NAME = "crash_reports"
_MAX_CRASH_ARTIFACTS = 50

_state_lock = threading.RLock()
_installed = False
_atexit_registered = False
_handling_exception = False

_qt_application: Any | None = None
_qt_bridge: Any | None = None

_fatal_fault_handle: Any | None = None
_fatal_fault_path: Path | None = None
_fatal_fault_initial_size = 0

_original_sys_excepthook = sys.excepthook
_original_threading_excepthook = getattr(
    threading,
    "excepthook",
    None,
)
_original_unraisablehook = getattr(
    sys,
    "unraisablehook",
    None,
)


def _preferred_crash_directory() -> Path:
    """Return the normal per-user crash-report directory."""
    override = os.environ.get(
        "HMS_CRASH_REPORT_DIR",
        "",
    ).strip()

    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get(
        "LOCALAPPDATA",
        "",
    ).strip()

    if local_app_data:
        return (
            Path(local_app_data)
            / _APP_DATA_FOLDER_NAME
            / _CRASH_FOLDER_NAME
        )

    return (
        Path.home()
        / "AppData"
        / "Local"
        / _APP_DATA_FOLDER_NAME
        / _CRASH_FOLDER_NAME
    )


def _fallback_crash_directory() -> Path:
    """Return a writable fallback if LocalAppData cannot be used."""
    return (
        Path(tempfile.gettempdir())
        / _APP_DATA_FOLDER_NAME
        / _CRASH_FOLDER_NAME
    )


def get_crash_report_directory() -> Path:
    """
    Create and return a writable crash-report directory.

    LocalAppData is preferred because it remains writable after installation
    under Program Files. The Windows temporary directory is used only when the
    preferred location cannot be created.
    """
    candidates = (
        _preferred_crash_directory(),
        _fallback_crash_directory(),
    )

    last_error: OSError | None = None

    for candidate in candidates:
        try:
            candidate.mkdir(
                parents=True,
                exist_ok=True,
            )
            return candidate
        except OSError as error:
            last_error = error

    raise OSError(
        "No writable crash-report directory could be created."
    ) from last_error


def _safe_exception_message(
    exception_value: BaseException | None,
) -> str:
    """Convert an exception to a short printable description."""
    if exception_value is None:
        return "No exception message was provided."

    try:
        message = str(exception_value).strip()
    except Exception:
        message = "The exception message could not be converted to text."

    return message or "No exception message was provided."


def _format_traceback_without_locals(
    exception_type: type[BaseException] | None,
    exception_value: BaseException | None,
    exception_traceback: TracebackType | None,
) -> str:
    """Format the exception chain without capturing local variable values."""
    try:
        trace = traceback.TracebackException(
            exception_type,
            exception_value,
            exception_traceback,
            capture_locals=False,
        )
        return "".join(
            trace.format(
                chain=True,
            )
        )
    except Exception as formatting_error:
        return (
            "Traceback formatting failed: "
            f"{type(formatting_error).__name__}: "
            f"{formatting_error}\n"
        )


def _build_report_text(
    *,
    report_id: str,
    occurred_at: datetime,
    origin: str,
    exception_type: type[BaseException] | None,
    exception_value: BaseException | None,
    exception_traceback: TracebackType | None,
    thread_name: str,
    thread_ident: int | None,
    extra_context: str | None = None,
) -> str:
    """Build a support-ready diagnostic report."""
    exception_name = (
        exception_type.__name__
        if exception_type is not None
        else "UnknownException"
    )
    exception_message = _safe_exception_message(
        exception_value
    )
    traceback_text = _format_traceback_without_locals(
        exception_type,
        exception_value,
        exception_traceback,
    )

    lines = [
        "HIRA MODEL SCHOOL MANAGEMENT SYSTEM - CRASH REPORT",
        "=" * 72,
        "",
        f"Report ID: {report_id}",
        f"Occurred at: {occurred_at.strftime('%Y-%m-%d %H:%M:%S.%f')}",
        f"Origin: {origin}",
        f"Thread: {thread_name}",
        f"Thread identifier: {thread_ident}",
        "",
        "APPLICATION ENVIRONMENT",
        "-" * 72,
        f"Python version: {platform.python_version()}",
        f"Python implementation: {platform.python_implementation()}",
        f"Operating system: {platform.platform()}",
        f"Executable: {sys.executable}",
        f"Frozen application: {bool(getattr(sys, 'frozen', False))}",
        f"Working directory: {os.getcwd()}",
        f"Command line: {sys.argv!r}",
        "",
        "EXCEPTION SUMMARY",
        "-" * 72,
        f"Exception type: {exception_name}",
        f"Exception message: {exception_message}",
    ]

    if extra_context:
        lines.extend(
            [
                f"Additional context: {extra_context}",
            ]
        )

    lines.extend(
        [
            "",
            "TRACEBACK",
            "-" * 72,
            traceback_text.rstrip(),
            "",
            "END OF REPORT",
            "=" * 72,
            "",
        ]
    )

    return "\n".join(lines)


def _prune_old_crash_artifacts(
    crash_directory: Path,
    *,
    protected_paths: tuple[Path, ...] = (),
) -> None:
    """Keep diagnostic files bounded without risking the current report."""
    protected = {path.resolve() for path in protected_paths}
    candidates = []
    for pattern in ("hira_crash_*.txt", "hira_fatal_fault_*.log"):
        for path in crash_directory.glob(pattern):
            try:
                resolved = path.resolve()
                stat = path.stat()
            except OSError:
                continue
            candidates.append((stat.st_ctime_ns, path.name, resolved, path))

    candidates.sort(reverse=True)
    retained = 0
    for _created, _name, resolved, path in candidates:
        if resolved in protected:
            retained += 1
            continue
        if retained < _MAX_CRASH_ARTIFACTS:
            retained += 1
            continue
        try:
            path.unlink()
        except OSError:
            pass


def write_crash_report(
    exception_type: type[BaseException] | None,
    exception_value: BaseException | None,
    exception_traceback: TracebackType | None,
    *,
    origin: str,
    thread_name: str | None = None,
    thread_ident: int | None = None,
    extra_context: str | None = None,
) -> Path:
    """Write one uniquely named crash-report text file and return its path."""
    occurred_at = datetime.now()
    report_id = uuid4().hex

    safe_thread_name = (
        thread_name
        or threading.current_thread().name
        or "UnknownThread"
    )

    if thread_ident is None:
        thread_ident = threading.current_thread().ident

    report_text = _build_report_text(
        report_id=report_id,
        occurred_at=occurred_at,
        origin=origin,
        exception_type=exception_type,
        exception_value=exception_value,
        exception_traceback=exception_traceback,
        thread_name=safe_thread_name,
        thread_ident=thread_ident,
        extra_context=extra_context,
    )

    crash_directory = get_crash_report_directory()
    timestamp = occurred_at.strftime(
        "%Y-%m-%d_%H-%M-%S_%f"
    )
    report_path = crash_directory / (
        "hira_crash_"
        f"{timestamp}_"
        f"{report_id[:8]}.txt"
    )

    with report_path.open(
        "x",
        encoding="utf-8",
        newline="\n",
    ) as report_file:
        report_file.write(report_text)
        report_file.flush()
        try:
            os.fsync(report_file.fileno())
        except OSError:
            pass

    _prune_old_crash_artifacts(
        crash_directory,
        protected_paths=(report_path,),
    )
    return report_path


def _show_windows_fallback_message(
    report_path: Path | None,
) -> None:
    """Show a minimal native Windows message when Qt is unavailable."""
    if os.environ.get(
        "HMS_CRASH_DIALOG_DISABLED",
        "",
    ) == "1":
        return

    path_text = (
        str(report_path)
        if report_path is not None
        else "A crash report could not be written."
    )

    message = (
        "Something unexpected went wrong and the software must close.\n\n"
        "A diagnostic text file was saved here:\n"
        f"{path_text}\n\n"
        "Please send this file to support."
    )

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                message,
                "Unexpected Application Error",
                0x00000010,
            )
            return
        except Exception:
            pass

    try:
        sys.stderr.write(
            message + "\n"
        )
        sys.stderr.flush()
    except Exception:
        pass


def _show_qt_crash_dialog(
    report_path_text: str,
) -> None:
    """Show the polite crash dialog on the Qt GUI thread."""
    application = _qt_application

    if application is None:
        _show_windows_fallback_message(
            Path(report_path_text)
            if report_path_text
            else None
        )
        return

    if os.environ.get(
        "HMS_CRASH_DIALOG_DISABLED",
        "",
    ) == "1":
        application.quit()
        return

    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        message_box = QMessageBox()
        message_box.setIcon(
            QMessageBox.Icon.Critical
        )
        message_box.setWindowTitle(
            "Unexpected Application Error"
        )
        message_box.setText(
            "Something unexpected went wrong."
        )
        if report_path_text:
            informative_text = (
                "The software will close safely. A diagnostic text file "
                "has been created. Please send this file to support.\n\n"
                f"Crash report:\n{report_path_text}"
            )
        else:
            informative_text = (
                "The software will close safely. The crash reporter could "
                "not save its diagnostic file. Please contact support and "
                "describe what you were doing when the error appeared."
            )

        message_box.setInformativeText(
            informative_text
        )
        message_box.setDetailedText(
            "No school records were intentionally changed by the "
            "crash reporter. The report contains the technical "
            "traceback and application environment, but it does not "
            "capture password, CNIC, or other frame-local values."
        )

        open_folder_button = message_box.addButton(
            "Open Report Folder",
            QMessageBox.ButtonRole.ActionRole,
        )
        message_box.addButton(
            "Close Application",
            QMessageBox.ButtonRole.AcceptRole,
        )

        message_box.exec()

        if (
            message_box.clickedButton()
            is open_folder_button
            and report_path_text
        ):
            report_folder = str(
                Path(report_path_text).parent
            )
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(
                    report_folder
                )
            )

    except Exception:
        _show_windows_fallback_message(
            Path(report_path_text)
            if report_path_text
            else None
        )

    try:
        application.quit()
    except Exception:
        pass


def attach_qt_application(
    application: Any,
) -> None:
    """
    Attach the active QApplication and create a queued GUI-thread bridge.

    Background-thread exception hooks emit through this bridge instead of
    directly touching Qt widgets from a worker thread.
    """
    global _qt_application
    global _qt_bridge

    _qt_application = application

    try:
        from PySide6.QtCore import (
            QObject,
            Signal,
            Slot,
            Qt,
        )

        class _CrashDialogBridge(QObject):
            show_requested = Signal(str)

            def __init__(self) -> None:
                super().__init__()
                self.show_requested.connect(
                    self._show,
                    Qt.ConnectionType.QueuedConnection,
                )

            @Slot(str)
            def _show(
                self,
                report_path_text: str,
            ) -> None:
                _show_qt_crash_dialog(
                    report_path_text
                )

        bridge = _CrashDialogBridge()
        bridge.moveToThread(
            application.thread()
        )
        _qt_bridge = bridge

    except Exception:
        _qt_bridge = None


def _notify_user(
    report_path: Path | None,
) -> None:
    """Display the report location without violating Qt thread affinity."""
    report_path_text = (
        str(report_path)
        if report_path is not None
        else ""
    )

    if (
        _qt_application is not None
        and threading.current_thread()
        is threading.main_thread()
    ):
        _show_qt_crash_dialog(
            report_path_text
        )
        return

    if _qt_bridge is not None:
        try:
            _qt_bridge.show_requested.emit(
                report_path_text
            )
            return
        except Exception:
            pass

    _show_windows_fallback_message(
        report_path
    )


def handle_unhandled_exception(
    exception_type: type[BaseException] | None,
    exception_value: BaseException | None,
    exception_traceback: TracebackType | None,
    *,
    origin: str,
    thread_name: str | None = None,
    thread_ident: int | None = None,
    extra_context: str | None = None,
    notify_user: bool = True,
) -> Path | None:
    """
    Record one uncaught exception and notify the user.

    KeyboardInterrupt and SystemExit are intentional control-flow exceptions
    and are passed through rather than reported as application crashes.
    """
    global _handling_exception

    if exception_type in (
        KeyboardInterrupt,
        SystemExit,
    ):
        return None

    with _state_lock:
        if _handling_exception:
            try:
                traceback.print_exception(
                    exception_type,
                    exception_value,
                    exception_traceback,
                )
            except Exception:
                pass
            return None

        _handling_exception = True

    report_path: Path | None = None

    try:
        try:
            report_path = write_crash_report(
                exception_type,
                exception_value,
                exception_traceback,
                origin=origin,
                thread_name=thread_name,
                thread_ident=thread_ident,
                extra_context=extra_context,
            )
        except Exception:
            report_path = None

        if os.environ.get(
            "HMS_CRASH_STDERR_DISABLED",
            "",
        ) != "1":
            try:
                traceback.print_exception(
                    exception_type,
                    exception_value,
                    exception_traceback,
                )
            except Exception:
                pass

        if notify_user:
            _notify_user(
                report_path
            )

        return report_path

    finally:
        with _state_lock:
            _handling_exception = False


def _global_sys_excepthook(
    exception_type: type[BaseException],
    exception_value: BaseException,
    exception_traceback: TracebackType | None,
) -> None:
    """Handle uncaught exceptions from the main Python thread."""
    if exception_type in (
        KeyboardInterrupt,
        SystemExit,
    ):
        _original_sys_excepthook(
            exception_type,
            exception_value,
            exception_traceback,
        )
        return

    handle_unhandled_exception(
        exception_type,
        exception_value,
        exception_traceback,
        origin="Uncaught main-thread exception",
        thread_name=threading.current_thread().name,
        thread_ident=threading.current_thread().ident,
    )


def _global_threading_excepthook(
    arguments: Any,
) -> None:
    """Handle uncaught exceptions from Python background threads."""
    if arguments.exc_type in (
        KeyboardInterrupt,
        SystemExit,
    ):
        if _original_threading_excepthook is not None:
            _original_threading_excepthook(
                arguments
            )
        return

    thread = arguments.thread

    handle_unhandled_exception(
        arguments.exc_type,
        arguments.exc_value,
        arguments.exc_traceback,
        origin="Uncaught background-thread exception",
        thread_name=(
            thread.name
            if thread is not None
            else "UnknownThread"
        ),
        thread_ident=(
            thread.ident
            if thread is not None
            else None
        ),
    )


def _global_unraisablehook(
    arguments: Any,
) -> None:
    """Record exceptions raised by destructors or other unraisable callbacks."""
    object_description = ""

    try:
        object_value = arguments.object
        object_type = type(object_value)
        object_description = (
            f"{object_type.__module__}."
            f"{object_type.__qualname__}"
        )
    except Exception:
        object_description = (
            "Unknown unraisable object type"
        )

    extra_context_parts = []

    if getattr(
        arguments,
        "err_msg",
        None,
    ):
        extra_context_parts.append(
            str(arguments.err_msg)
        )

    if object_description:
        extra_context_parts.append(
            "Object type: "
            f"{object_description[:500]}"
        )

    handle_unhandled_exception(
        arguments.exc_type,
        arguments.exc_value,
        arguments.exc_traceback,
        origin="Unraisable Python callback exception",
        extra_context=" | ".join(
            extra_context_parts
        ) or None,
    )


def _enable_fatal_fault_capture() -> None:
    """Enable faulthandler into a per-session diagnostic file."""
    global _fatal_fault_handle
    global _fatal_fault_path
    global _fatal_fault_initial_size

    if not hasattr(
        faulthandler,
        "enable",
    ):
        return

    try:
        crash_directory = get_crash_report_directory()
        timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H-%M-%S_%f"
        )
        path = crash_directory / (
            "hira_fatal_fault_"
            f"{timestamp}_"
            f"{uuid4().hex[:8]}.txt"
        )
        handle = path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        )
        handle.write(
            "HIRA MODEL SCHOOL MANAGEMENT SYSTEM - FATAL FAULT LOG\n"
        )
        handle.write(
            "This file is retained only if Python writes a low-level "
            "fault traceback.\n\n"
        )
        handle.flush()
        initial_size = handle.tell()

        faulthandler.enable(
            file=handle,
            all_threads=True,
        )

        _fatal_fault_handle = handle
        _fatal_fault_path = path
        _fatal_fault_initial_size = initial_size

    except Exception:
        _fatal_fault_handle = None
        _fatal_fault_path = None
        _fatal_fault_initial_size = 0


def install_global_crash_catcher() -> None:
    """Install every available process-wide Python exception hook."""
    global _installed
    global _atexit_registered

    with _state_lock:
        if _installed:
            return

        sys.excepthook = (
            _global_sys_excepthook
        )

        if hasattr(
            threading,
            "excepthook",
        ):
            threading.excepthook = (
                _global_threading_excepthook
            )

        if hasattr(
            sys,
            "unraisablehook",
        ):
            sys.unraisablehook = (
                _global_unraisablehook
            )

        _enable_fatal_fault_capture()

        if not _atexit_registered:
            atexit.register(
                finalize_crash_catcher
            )
            _atexit_registered = True

        _installed = True


def finalize_crash_catcher() -> None:
    """
    Close the session fault file and remove it when no fatal fault occurred.

    Timestamped Python crash reports are never deleted here.
    """
    global _fatal_fault_handle
    global _fatal_fault_path
    global _fatal_fault_initial_size
    global _qt_application
    global _qt_bridge

    handle = _fatal_fault_handle
    path = _fatal_fault_path
    initial_size = _fatal_fault_initial_size

    _fatal_fault_handle = None
    _fatal_fault_path = None
    _fatal_fault_initial_size = 0
    _qt_bridge = None
    _qt_application = None

    if handle is None:
        return

    try:
        if faulthandler.is_enabled():
            faulthandler.disable()
    except Exception:
        pass

    try:
        handle.flush()
        handle.close()
    except Exception:
        pass

    if path is None:
        return

    try:
        if path.stat().st_size <= initial_size:
            path.unlink(
                missing_ok=True
            )
    except OSError:
        pass


def restore_original_exception_hooks() -> None:
    """Restore interpreter hooks; intended for isolated regression tests."""
    global _installed

    finalize_crash_catcher()

    sys.excepthook = (
        _original_sys_excepthook
    )

    if (
        _original_threading_excepthook
        is not None
        and hasattr(
            threading,
            "excepthook",
        )
    ):
        threading.excepthook = (
            _original_threading_excepthook
        )

    if (
        _original_unraisablehook
        is not None
        and hasattr(
            sys,
            "unraisablehook",
        )
    ):
        sys.unraisablehook = (
            _original_unraisablehook
        )

    _installed = False
