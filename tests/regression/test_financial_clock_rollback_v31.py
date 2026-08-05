from __future__ import annotations

from datetime import datetime
from pathlib import Path
import importlib
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, insert_class, insert_student


class RolledBackDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 1, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class CaughtUpDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 9, 2, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class FinancialClockRollbackV31Tests(unittest.TestCase):
    def _insert_future_completed_payment(
        self,
        app: IsolatedApplication,
        *,
        created_at: str = "2026-09-01 10:00:00",
        payment_date: str = "2026-09-01 10:00:00",
    ) -> tuple[int, int]:
        class_id = insert_class(app, "7", "A")
        student_id = insert_student(
            app,
            class_id=class_id,
            admission_number="H901",
            roll_number=1,
            name="Clock Test Student",
        )
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO fee_payments(
                    receipt_no,student_id,payment_date,total_amount,
                    received_by,created_at,payment_status,
                    student_status_before,student_status_after,
                    tracking_id_snapshot,admission_number_snapshot,
                    roll_number_snapshot,student_name_snapshot,
                    father_name_snapshot,father_cnic_snapshot,
                    class_name_snapshot,student_status_snapshot
                ) VALUES(
                    '000005',?,?,1000,'admin',?,'Completed',
                    'Active','Active','HMS-000901','H901',1,
                    'Clock Test Student','Test Father','0000000000001',
                    '7A','Active'
                )
                """,
                (student_id, payment_date, created_at),
            )
            connection.commit()
            return student_id, int(cursor.lastrowid)
        finally:
            connection.close()

    def test_future_relative_to_computer_clock_is_nonfatal_when_internal_chronology_is_valid(self):
        with IsolatedApplication() as app:
            self._insert_future_completed_payment(app)
            schema_integrity = importlib.import_module("app.schema_integrity")
            connection = app.connect()
            try:
                with mock.patch.object(
                    schema_integrity,
                    "datetime",
                    RolledBackDateTime,
                ):
                    valid, message = schema_integrity.validate_payment_timestamps(
                        connection
                    )
            finally:
                connection.close()

            self.assertTrue(valid, message)
            self.assertIn("runtime clock guard", message)

    def test_internal_payment_chronology_remains_strict(self):
        with IsolatedApplication() as app:
            self._insert_future_completed_payment(
                app,
                payment_date="2026-09-01 10:00:00",
                created_at="2026-09-01 10:01:00",
            )
            schema_integrity = importlib.import_module("app.schema_integrity")
            connection = app.connect()
            try:
                with mock.patch.object(
                    schema_integrity,
                    "datetime",
                    RolledBackDateTime,
                ):
                    valid, message = schema_integrity.validate_payment_timestamps(
                        connection
                    )
            finally:
                connection.close()

            self.assertFalse(valid)
            self.assertIn("creation time is later than payment time", message)

    def test_clock_status_detects_rollback_and_clears_after_clock_catches_up(self):
        with IsolatedApplication() as app:
            self._insert_future_completed_payment(app)

            rollback = app.fee_service.get_financial_clock_status(
                as_of=RolledBackDateTime.now()
            )
            caught_up = app.fee_service.get_financial_clock_status(
                as_of=CaughtUpDateTime.now()
            )

            self.assertTrue(rollback["clock_rollback_detected"])
            self.assertEqual(rollback["latest_receipt_no"], "000005")
            self.assertEqual(
                rollback["latest_financial_timestamp"],
                datetime(2026, 9, 1, 10, 0, 0),
            )
            self.assertFalse(caught_up["clock_rollback_detected"])

    def test_payment_and_cancellation_are_blocked_while_clock_is_behind(self):
        with IsolatedApplication() as app:
            student_id, payment_id = self._insert_future_completed_payment(app)

            with mock.patch.object(
                app.fee_service,
                "datetime",
                RolledBackDateTime,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "Computer date/time is earlier",
                ):
                    app.fee_service.process_payment(student_id, [999999])

                with self.assertRaisesRegex(
                    ValueError,
                    "Computer date/time is earlier",
                ):
                    app.fee_service.cancel_immediate_payment(payment_id)

            connection = app.connect()
            try:
                row = connection.execute(
                    "SELECT payment_status,cancelled_at FROM fee_payments WHERE id=?",
                    (payment_id,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(str(row["payment_status"]), "Completed")
            self.assertIsNone(row["cancelled_at"])

    def test_main_window_warns_and_skips_automatic_archive_without_layout_change(self):
        source = (
            Path(__file__).resolve().parents[2] / "app" / "main_window.py"
        ).read_text(encoding="utf-8")

        self.assertIn("fee_service.get_financial_clock_status()", source)
        self.assertIn("skipped_due_to_clock_rollback", source)
        self.assertIn("QTimer.singleShot", source)
        self.assertIn("Computer Date/Time Warning", source)
        self.assertIn("Fee payment and payment cancellation are temporarily blocked", source)
        self.assertIn("self._register_main_dashboard()", source)


if __name__ == "__main__":
    unittest.main()
