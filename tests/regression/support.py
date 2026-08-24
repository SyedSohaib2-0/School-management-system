from __future__ import annotations

import gc
import importlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)
    importlib.invalidate_caches()


class IsolatedApplication:
    """Load HMS services against one temporary LOCALAPPDATA database."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="hms_regression_")
        self.root = Path(self._temporary.name)
        self.local_app_data = self.root / "localappdata"
        self._old_localappdata = os.environ.get("LOCALAPPDATA")
        self._old_dpapi_key = os.environ.get("HMS_BACKUP_TEST_DPAPI_KEY")
        os.environ["LOCALAPPDATA"] = str(self.local_app_data)
        os.environ["HMS_BACKUP_TEST_DPAPI_KEY"] = "hms-regression-dpapi-context"
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

        project_text = str(PROJECT_ROOT)
        if project_text not in sys.path:
            sys.path.insert(0, project_text)

        purge_app_modules()
        self.config = importlib.import_module("app.config")
        self.database = importlib.import_module("app.database")
        self.database.initialize_database()

        self.fee_service = importlib.import_module("app.services.fee_service")
        self.student_service = importlib.import_module("app.services.student_service")
        self.promotion_service = importlib.import_module("app.services.promotion_service")
        self.promotion_full_class_v2 = importlib.import_module(
            "app.services.promotion_full_class_v2"
        )
        self.backup_service = importlib.import_module("app.services.backup_service")
        self.backup_crypto_service = importlib.import_module(
            "app.services.backup_crypto_service"
        )
        self.class_service = importlib.import_module("app.services.class_service")
        self.expense_service = importlib.import_module("app.services.expense_service")
        self.real_time_finance_service = importlib.import_module(
            "app.services.real_time_finance_service"
        )
        self.sibling_fee_service = importlib.import_module(
            "app.services.sibling_fee_service"
        )
        self.fee_setting_report_service = importlib.import_module(
            "app.services.fee_setting_report_service"
        )

        def test_connection():
            return self.connect()

        for module in (
            self.fee_service,
            self.student_service,
            self.promotion_service,
            self.promotion_full_class_v2,
            self.class_service,
            self.expense_service,
            self.real_time_finance_service,
            self.sibling_fee_service,
            self.fee_setting_report_service,
        ):
            module.get_connection = test_connection

    @property
    def database_path(self) -> Path:
        return Path(self.config.DATABASE_PATH)

    def connect(self) -> sqlite3.Connection:
        connection = self.database.get_connection(automatic_backup=False)
        connection.create_function(
            "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
        )
        return connection

    def strict_connect(self) -> sqlite3.Connection:
        """Return a production-strict connection for V19 integrity tests."""
        return self.database.get_connection(automatic_backup=False)

    def close(self) -> None:
        # A database-changing test can leave the queued automatic-backup
        # worker active for a fraction of a second. Complete that worker
        # before deleting the isolated LOCALAPPDATA tree so Windows does
        # not retain a handle to a temporary SQLite snapshot.
        try:
            scheduler = importlib.import_module(
                "app.services.backup_scheduler"
            )
            scheduler.complete_pending_backups_before_exit()
        except Exception:
            # Cleanup must still restore the process environment even when
            # a test deliberately damages backup state.
            pass

        purge_app_modules()
        gc.collect()

        if self._old_localappdata is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old_localappdata
        if self._old_dpapi_key is None:
            os.environ.pop("HMS_BACKUP_TEST_DPAPI_KEY", None)
        else:
            os.environ["HMS_BACKUP_TEST_DPAPI_KEY"] = self._old_dpapi_key

        # Antivirus and filesystem filters can briefly retain a released
        # SQLite file on Windows. Retry only that transient WinError 32;
        # all other cleanup failures remain visible to the test runner.
        for attempt in range(8):
            try:
                self._temporary.cleanup()
                return
            except PermissionError as error:
                is_transient_windows_lock = (
                    os.name == "nt"
                    and getattr(error, "winerror", None) == 32
                )
                if not is_transient_windows_lock or attempt == 7:
                    raise
                gc.collect()
                time.sleep(0.20 * (attempt + 1))

    def __enter__(self) -> "IsolatedApplication":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def insert_class(app: IsolatedApplication, level: str, section: str) -> int:
    class_name = f"{level}{section}"
    now = "2026-07-28 04:00:00"
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO classes(
                class_level, section, class_name, student_capacity,
                created_at, updated_at
            ) VALUES (?, ?, ?, 40, ?, ?)
            """,
            (level, section, class_name, now, now),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def insert_student(
    app: IsolatedApplication,
    *,
    class_id: int,
    admission_number: str,
    roll_number: int,
    name: str = "Test Student",
    father_name: str = "Test Father",
    status: str = "Active",
    failure_count: int = 0,
    gender: str = "MALE",
    father_cnic: str | None = None,
) -> int:
    now = "2026-07-28 04:00:00"
    if father_cnic is None:
        numeric_seed = sum((index + 1) * ord(ch) for index, ch in enumerate(admission_number))
        father_cnic = f"{numeric_seed % (10**13):013d}"
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO students(
                admission_number, roll_number, class_id, student_name,
                gender, father_name, father_cnic, father_contact_number,
                whatsapp_number, current_address, permanent_address,
                school_leaving_certificate_submitted, form_b_submitted,
                birth_certificate_submitted, date_of_birth, admission_date,
                admission_year, admission_month, admission_day,
                admission_time, student_status, failure_count,
                created_at, created_by, updated_at, updated_by
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, '+920000000000', '', '', '',
                1, 0, 0, '2015-01-01', '2026-04-01 09:00:00',
                2026, 4, 1, '09:00:00', ?, ?, ?, 'test', ?, 'test'
            )
            """,
            (
                admission_number,
                int(roll_number),
                int(class_id),
                name,
                gender,
                father_name,
                father_cnic,
                status,
                int(failure_count),
                now,
                now,
            ),
        )
        student_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO student_class_history(
                student_id, old_class_id, new_class_id,
                effective_month, effective_year, change_type,
                change_datetime, changed_by
            ) VALUES (?, NULL, ?, 4, 2026, 'ADMISSION', ?, 'test')
            """,
            (student_id, int(class_id), now),
        )
        connection.commit()
        return student_id
    finally:
        connection.close()


def fetch_one(app: IsolatedApplication, sql: str, parameters=()):
    connection = app.connect()
    try:
        return connection.execute(sql, parameters).fetchone()
    finally:
        connection.close()


def fetch_all(app: IsolatedApplication, sql: str, parameters=()):
    connection = app.connect()
    try:
        return connection.execute(sql, parameters).fetchall()
    finally:
        connection.close()


class AllowedPromotionDateTime(__import__('datetime').datetime):
    """Stable January-March timestamp for promotion regression tests."""

    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 15, 10, 30, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class BlockedPromotionDateTime(__import__('datetime').datetime):
    """Stable outside-window timestamp for promotion rejection tests."""

    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 4, 1, 10, 30, 0)
        return value if tz is None else value.replace(tzinfo=tz)


def allowed_promotion_window(app):
    """Patch the full-class service clock to an allowed promotion date."""
    return mock.patch.object(
        app.promotion_full_class_v2,
        "datetime",
        AllowedPromotionDateTime,
    )


def blocked_promotion_window(app):
    """Patch the full-class service clock to a blocked promotion date."""
    return mock.patch.object(
        app.promotion_full_class_v2,
        "datetime",
        BlockedPromotionDateTime,
    )
