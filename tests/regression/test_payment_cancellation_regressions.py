from __future__ import annotations

import importlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from tests.regression.support import PROJECT_ROOT


class PaymentCancellationRegressionTests(unittest.TestCase):
    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE students (
                student_id INTEGER PRIMARY KEY,
                student_status TEXT NOT NULL,
                updated_at TEXT,
                updated_by TEXT
            );
            CREATE TABLE student_fee_records (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                fee_type TEXT NOT NULL,
                status TEXT NOT NULL,
                paid_date TEXT,
                updated_at TEXT
            );
            CREATE TABLE fee_payments (
                id INTEGER PRIMARY KEY,
                receipt_no TEXT NOT NULL,
                student_id INTEGER NOT NULL,
                payment_date TEXT,
                total_amount REAL NOT NULL,
                received_by TEXT,
                created_at TEXT,
                payment_status TEXT NOT NULL,
                student_status_before TEXT,
                student_status_after TEXT,
                auto_graduated INTEGER NOT NULL DEFAULT 0,
                auto_archived INTEGER NOT NULL DEFAULT 0,
                cancelled_at TEXT,
                cancelled_by TEXT,
                cancellation_reason TEXT
            );
            CREATE TABLE fee_receipts (
                id INTEGER PRIMARY KEY,
                payment_id INTEGER NOT NULL,
                student_fee_record_id INTEGER NOT NULL,
                item_name TEXT,
                amount REAL,
                status TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE graduation_records (
                graduated_record_id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                is_reversed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE student_class_history (
                history_id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                changed_by TEXT
            );
            CREATE TABLE student_status_history (
                id INTEGER PRIMARY KEY,
                student_id INTEGER NOT NULL,
                old_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                effective_datetime TEXT NOT NULL,
                reason TEXT,
                changed_by TEXT,
                created_at TEXT NOT NULL,
                related_class_id INTEGER,
                related_event_id INTEGER
            );
            """
        )

    def _seed_case(
        self,
        database_path: Path,
        *,
        status_before: str,
        status_after: str,
        auto_graduated: int,
        auto_archived: int,
        include_graduation_row: bool,
        include_archive_history: bool,
    ) -> None:
        connection = sqlite3.connect(database_path)
        try:
            self._create_schema(connection)
            connection.execute(
                "INSERT INTO students(student_id, student_status) VALUES(1, ?)",
                (status_after,),
            )
            connection.execute(
                """
                INSERT INTO student_fee_records(
                    id, student_id, fee_type, status, paid_date
                ) VALUES(11, 1, 'Monthly', 'Paid', '2026-07-28')
                """
            )
            connection.execute(
                """
                INSERT INTO fee_payments(
                    id, receipt_no, student_id, total_amount,
                    payment_status, student_status_before, student_status_after,
                    auto_graduated, auto_archived
                ) VALUES(21, 'R-21', 1, 1000.0, 'Completed', ?, ?, ?, ?)
                """,
                (
                    status_before,
                    status_after,
                    int(auto_graduated),
                    int(auto_archived),
                ),
            )
            connection.execute(
                """
                INSERT INTO fee_receipts(
                    id, payment_id, student_fee_record_id, item_name, amount, status
                ) VALUES(31, 21, 11, 'April 2026', 1000.0, 'Paid')
                """
            )
            if include_graduation_row:
                connection.execute(
                    """
                    INSERT INTO graduation_records(
                        graduated_record_id, student_id, status, is_reversed
                    ) VALUES(41, 1, 'Graduated', 0)
                    """
                )
            if include_archive_history:
                connection.execute(
                    """
                    INSERT INTO student_status_history(
                        id, student_id, old_status, new_status,
                        effective_datetime, reason, changed_by, created_at
                    ) VALUES(51, 1, 'Dropped', 'Archived',
                             '2026-07-29 00:00:00', 'Automatic archive',
                             'automatic_archive', '2026-07-29 00:00:00')
                    """
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _load_fee_service(self, database_path: Path):
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        sys.modules.pop("app.services.fee_service", None)
        fee_service = importlib.import_module("app.services.fee_service")
        fee_service.get_connection = lambda: self._connect(database_path)
        return fee_service

    def _read_state(self, database_path: Path):
        connection = self._connect(database_path)
        try:
            graduation = connection.execute(
                "SELECT status FROM graduation_records WHERE student_id=1"
            ).fetchone()
            return {
                "student": connection.execute(
                    "SELECT student_status FROM students WHERE student_id=1"
                ).fetchone()["student_status"],
                "fee": connection.execute(
                    "SELECT status FROM student_fee_records WHERE id=11"
                ).fetchone()["status"],
                "receipt": connection.execute(
                    "SELECT status FROM fee_receipts WHERE id=31"
                ).fetchone()["status"],
                "payment": connection.execute(
                    "SELECT payment_status FROM fee_payments WHERE id=21"
                ).fetchone()["payment_status"],
                "history_count": connection.execute(
                    "SELECT COUNT(*) FROM student_status_history"
                ).fetchone()[0],
                "graduation_status": graduation["status"] if graduation else None,
            }
        finally:
            connection.close()

    def test_missing_automatic_archive_history_rolls_back_everything(self):
        with tempfile.TemporaryDirectory(prefix="hms_cancel_archive_") as temp_dir:
            database_path = Path(temp_dir) / "case.db"
            self._seed_case(
                database_path,
                status_before="Dropped",
                status_after="Archived",
                auto_graduated=0,
                auto_archived=1,
                include_graduation_row=False,
                include_archive_history=False,
            )
            fee_service = self._load_fee_service(database_path)
            with self.assertRaisesRegex(RuntimeError, "automatic archive history"):
                fee_service.cancel_immediate_payment(21, "regression")

            self.assertEqual(
                self._read_state(database_path),
                {
                    "student": "Archived",
                    "fee": "Paid",
                    "receipt": "Paid",
                    "payment": "Completed",
                    "history_count": 0,
                    "graduation_status": None,
                },
            )

    def test_valid_archive_and_graduation_cancellations_restore_complete_state(self):
        with tempfile.TemporaryDirectory(prefix="hms_cancel_valid_") as temp_dir:
            archive_path = Path(temp_dir) / "archive.db"
            self._seed_case(
                archive_path,
                status_before="Dropped",
                status_after="Archived",
                auto_graduated=0,
                auto_archived=1,
                include_graduation_row=False,
                include_archive_history=True,
            )
            fee_service = self._load_fee_service(archive_path)
            result = fee_service.cancel_immediate_payment(21, "regression")
            self.assertEqual(result["payment_status"], "Cancelled")
            self.assertEqual(
                self._read_state(archive_path),
                {
                    "student": "Dropped",
                    "fee": "Unpaid",
                    "receipt": "Cancelled",
                    "payment": "Cancelled",
                    "history_count": 0,
                    "graduation_status": None,
                },
            )

            graduation_path = Path(temp_dir) / "graduation.db"
            self._seed_case(
                graduation_path,
                status_before="Partially Graduated",
                status_after="Graduated",
                auto_graduated=1,
                auto_archived=0,
                include_graduation_row=True,
                include_archive_history=False,
            )
            fee_service = self._load_fee_service(graduation_path)
            result = fee_service.cancel_immediate_payment(21, "regression")
            self.assertEqual(result["payment_status"], "Cancelled")
            self.assertEqual(
                self._read_state(graduation_path),
                {
                    "student": "Partially Graduated",
                    "fee": "Unpaid",
                    "receipt": "Cancelled",
                    "payment": "Cancelled",
                    "history_count": 0,
                    "graduation_status": "Partially Graduated",
                },
            )


if __name__ == "__main__":
    unittest.main()
