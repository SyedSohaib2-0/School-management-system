from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.regression.support import PROJECT_ROOT


class FeeRegressionTests(unittest.TestCase):
    def _load_fee_service_for_database(self, database_path: Path):
        import importlib
        import sys

        sys.path.insert(0, str(PROJECT_ROOT)) if str(PROJECT_ROOT) not in sys.path else None
        sys.modules.pop("app.services.fee_service", None)
        fee_service = importlib.import_module("app.services.fee_service")

        def connect():
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            return connection

        fee_service.get_connection = connect
        return fee_service, connect

    @staticmethod
    def _create_timeline_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE fee_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL,
                monthly_fee REAL NOT NULL,
                session_start_year INTEGER NOT NULL,
                effective_from_month INTEGER NOT NULL,
                effective_from_year INTEGER NOT NULL,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE fee_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL,
                monthly_fee REAL NOT NULL,
                session_start_year INTEGER NOT NULL,
                effective_from_month INTEGER NOT NULL,
                effective_from_year INTEGER NOT NULL,
                effective_to_month INTEGER,
                effective_to_year INTEGER,
                created_at TEXT
            );
            CREATE TABLE student_fee_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER,
                class_id INTEGER,
                session_year TEXT,
                fee_type TEXT,
                month_number INTEGER,
                year INTEGER,
                amount REAL,
                status TEXT,
                paid_date TEXT,
                updated_at TEXT
            );
            CREATE TABLE fee_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_status TEXT
            );
            CREATE TABLE fee_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_fee_record_id INTEGER,
                payment_id INTEGER,
                status TEXT
            );
            """
        )
        connection.commit()

    def test_first_fee_requires_april_and_revisions_preserve_history(self):
        with tempfile.TemporaryDirectory(prefix="hms_fee_timeline_") as temp_dir:
            database_path = Path(temp_dir) / "fee.db"
            connection = sqlite3.connect(database_path)
            try:
                self._create_timeline_schema(connection)
            finally:
                connection.close()

            fee_service, connect = self._load_fee_service_for_database(database_path)

            with self.assertRaisesRegex(ValueError, "must be effective from April"):
                fee_service.set_class_fees_for_level(
                    [1, 2], 3000, 7, 2027
                )

            connection = connect()
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM fee_settings").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM fee_history").fetchone()[0],
                    0,
                )
            finally:
                connection.close()

            fee_service.set_class_fees_for_level([1, 2], 2000, 4, 2027)
            fee_service.set_class_fees_for_level([1, 2], 3000, 7, 2027)

            connection = connect()
            try:
                for class_id in (1, 2):
                    current = connection.execute(
                        """
                        SELECT * FROM fee_settings
                        WHERE class_id=? AND session_start_year=2027
                        """,
                        (class_id,),
                    ).fetchone()
                    history = connection.execute(
                        """
                        SELECT * FROM fee_history
                        WHERE class_id=? AND session_start_year=2027
                        """,
                        (class_id,),
                    ).fetchone()
                    self.assertEqual(int(current["effective_from_month"]), 7)
                    self.assertEqual(float(current["monthly_fee"]), 3000.0)
                    self.assertEqual(int(history["effective_from_month"]), 4)
                    self.assertEqual(int(history["effective_to_month"]), 6)
                    self.assertEqual(float(history["monthly_fee"]), 2000.0)
            finally:
                connection.close()

    def test_all_section_fee_update_rolls_back_when_one_section_is_invalid(self):
        with tempfile.TemporaryDirectory(prefix="hms_fee_rollback_") as temp_dir:
            database_path = Path(temp_dir) / "fee.db"
            connection = sqlite3.connect(database_path)
            try:
                self._create_timeline_schema(connection)
            finally:
                connection.close()

            fee_service, connect = self._load_fee_service_for_database(database_path)
            fee_service.set_class_fees_for_level([1, 2], 2200, 4, 2028)

            connection = connect()
            try:
                connection.execute(
                    "DELETE FROM fee_settings WHERE class_id=2 AND session_start_year=2028"
                )
                connection.execute(
                    "DELETE FROM fee_history WHERE class_id=2 AND session_start_year=2028"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(ValueError):
                fee_service.set_class_fees_for_level([1, 2], 3300, 7, 2028)

            connection = connect()
            try:
                class_one = connection.execute(
                    """
                    SELECT * FROM fee_settings
                    WHERE class_id=1 AND session_start_year=2028
                    """
                ).fetchone()
                history_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM fee_history
                    WHERE class_id=1 AND session_start_year=2028
                    """
                ).fetchone()[0]
                self.assertEqual(int(class_one["effective_from_month"]), 4)
                self.assertEqual(float(class_one["monthly_fee"]), 2200.0)
                self.assertEqual(history_count, 0)
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
