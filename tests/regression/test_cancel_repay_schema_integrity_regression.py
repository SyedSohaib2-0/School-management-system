from __future__ import annotations

import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class CancelRepaySchemaIntegrityRegressionTests(unittest.TestCase):
    """Protect valid cancellation history when the same fee is paid again."""

    @staticmethod
    def _insert_monthly_fee(
        app: IsolatedApplication,
        *,
        student_id: int,
        class_id: int,
        month: int = 7,
        year: int = 2026,
        amount: float = 2000.0,
    ) -> int:
        session_start = year if month >= 4 else year - 1
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_fee_records(
                    student_id,class_id,session_year,fee_type,month_name,
                    month_number,year,amount,status,paid_date,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,
                         '2026-07-01 10:00:00','2026-07-01 10:00:00')
                """,
                (
                    student_id,
                    class_id,
                    f"{session_start}-{session_start + 1}",
                    "Monthly",
                    app.fee_service.MONTH_NUMBER_TO_NAME[month],
                    month,
                    year,
                    amount,
                    "Unpaid",
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    @staticmethod
    def _validate_payment_integrity(app: IsolatedApplication) -> tuple[bool, str]:
        connection = app.strict_connect()
        try:
            return app.database.schema_integrity.validate_payment_receipt_integrity(
                connection
            )
        finally:
            connection.close()

    @staticmethod
    def _validate_current_schema(app: IsolatedApplication) -> tuple[bool, str]:
        connection = app.strict_connect()
        try:
            return app.database.schema_integrity.validate_current_schema(connection)
        finally:
            connection.close()

    def _seed_fee(self, app: IsolatedApplication) -> tuple[int, int]:
        class_id = insert_class(app, "1", "A")
        student_id = insert_student(
            app,
            class_id=class_id,
            admission_number="REP001",
            roll_number=1,
        )
        fee_id = self._insert_monthly_fee(
            app,
            student_id=student_id,
            class_id=class_id,
        )
        return student_id, fee_id

    def test_cancel_then_repay_same_fee_passes_final_schema_validation(self):
        with IsolatedApplication() as app:
            student_id, fee_id = self._seed_fee(app)

            payment_a = app.fee_service.process_payment(student_id, [fee_id])
            cancelled = app.fee_service.cancel_immediate_payment(
                payment_a["payment_id"], "regression"
            )
            self.assertEqual(cancelled["payment_status"], "Cancelled")

            payment_a_row = fetch_one(
                app,
                "SELECT payment_status FROM fee_payments WHERE id=?",
                (payment_a["payment_id"],),
            )
            payment_a_receipts = fetch_all(
                app,
                "SELECT status FROM fee_receipts WHERE payment_id=?",
                (payment_a["payment_id"],),
            )
            reopened_fee = fetch_one(
                app,
                "SELECT status,paid_date FROM student_fee_records WHERE id=?",
                (fee_id,),
            )
            self.assertEqual(payment_a_row["payment_status"], "Cancelled")
            self.assertTrue(payment_a_receipts)
            self.assertTrue(
                all(row["status"] == "Cancelled" for row in payment_a_receipts)
            )
            self.assertEqual(reopened_fee["status"], "Unpaid")
            self.assertIsNone(reopened_fee["paid_date"])

            valid, message = self._validate_payment_integrity(app)
            self.assertTrue(valid, message)
            valid, message = self._validate_current_schema(app)
            self.assertTrue(valid, message)

            payment_b = app.fee_service.process_payment(student_id, [fee_id])

            payment_rows = fetch_all(
                app,
                """
                SELECT id,payment_status FROM fee_payments
                WHERE id IN (?,?) ORDER BY id
                """,
                (payment_a["payment_id"], payment_b["payment_id"]),
            )
            self.assertEqual(
                [(int(row["id"]), row["payment_status"]) for row in payment_rows],
                [
                    (int(payment_a["payment_id"]), "Cancelled"),
                    (int(payment_b["payment_id"]), "Completed"),
                ],
            )
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT status FROM fee_receipts WHERE payment_id=?",
                    (payment_a["payment_id"],),
                )["status"],
                "Cancelled",
            )
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT status FROM fee_receipts WHERE payment_id=?",
                    (payment_b["payment_id"],),
                )["status"],
                "Paid",
            )
            repaid_fee = fetch_one(
                app,
                "SELECT status,paid_date FROM student_fee_records WHERE id=?",
                (fee_id,),
            )
            self.assertEqual(repaid_fee["status"], "Paid")
            self.assertIsNotNone(repaid_fee["paid_date"])

            valid, message = self._validate_payment_integrity(app)
            self.assertTrue(valid, message)
            valid, message = self._validate_current_schema(app)
            self.assertTrue(valid, message)

            # Exercise a fresh database open through the production backup
            # validator, which uses the same final current-schema checks as
            # startup without writing to the database.
            valid, message = app.backup_service.validate_school_database_schema(
                str(app.database_path), require_current_schema=True
            )
            self.assertTrue(valid, message)

    def test_cancelled_payment_with_its_own_active_receipt_is_rejected(self):
        with IsolatedApplication() as app:
            student_id, fee_id = self._seed_fee(app)
            payment = app.fee_service.process_payment(student_id, [fee_id])

            fixture = app.connect()
            try:
                fixture.execute(
                    "UPDATE fee_payments SET payment_status='Cancelled' WHERE id=?",
                    (payment["payment_id"],),
                )
                fixture.commit()
            finally:
                fixture.close()

            valid, message = self._validate_payment_integrity(app)
            self.assertFalse(valid, message)
            self.assertIn("cancelled payments", message.lower())

    def test_paid_fee_without_active_completed_payment_evidence_is_rejected(self):
        with IsolatedApplication() as app:
            student_id, fee_id = self._seed_fee(app)
            payment = app.fee_service.process_payment(student_id, [fee_id])

            fixture = app.connect()
            try:
                fixture.execute(
                    "UPDATE fee_receipts SET status='Cancelled' WHERE payment_id=?",
                    (payment["payment_id"],),
                )
                fixture.execute(
                    "UPDATE fee_payments SET payment_status='Cancelled' WHERE id=?",
                    (payment["payment_id"],),
                )
                fixture.commit()
            finally:
                fixture.close()

            valid, message = self._validate_payment_integrity(app)
            self.assertFalse(valid, message)
            self.assertIn("lack valid completed payment evidence", message.lower())


if __name__ == "__main__":
    unittest.main()
