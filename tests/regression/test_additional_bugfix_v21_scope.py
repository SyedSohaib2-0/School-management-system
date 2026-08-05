from __future__ import annotations

from decimal import Decimal
import importlib
import shutil
import sqlite3
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class AdditionalBugFixV21ScopeTests(unittest.TestCase):
    @staticmethod
    def _insert_monthly_fee(app, *, student_id, class_id, month, amount):
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_fee_records(
                    student_id,class_id,session_year,fee_type,month_name,
                    month_number,year,amount,status,paid_date,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?, 'Unpaid',NULL,?,?)
                """,
                (
                    student_id, class_id, "2026-2027", "Monthly",
                    app.fee_service.MONTH_NUMBER_TO_NAME[month], month, 2026, amount,
                    "2026-07-30 04:30:00", "2026-07-30 04:30:00",
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    @staticmethod
    def _money(value):
        return Decimal(str(value)).quantize(Decimal("0.01"))

    def test_selected_months_match_receipt_exactly_and_partial_payment_is_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H3001", roll_number=1,
                name="EXACT PAYMENT STUDENT", father_name="EXACT PAYMENT FATHER",
                father_cnic="1234512345671",
            )
            fee_ids = [
                self._insert_monthly_fee(app, student_id=student_id, class_id=class_id, month=4, amount=1000.00),
                self._insert_monthly_fee(app, student_id=student_id, class_id=class_id, month=5, amount=1250.50),
                self._insert_monthly_fee(app, student_id=student_id, class_id=class_id, month=6, amount=999.99),
            ]

            preview = app.fee_service.get_payment_preview(student_id, fee_ids)
            self.assertEqual([item["item_name"] for item in preview["fee_items"]], ["April 2026", "May 2026", "June 2026"])
            self.assertEqual([self._money(item["amount"]) for item in preview["fee_items"]], [Decimal("1000.00"), Decimal("1250.50"), Decimal("999.99")])
            self.assertEqual(self._money(preview["total_amount"]), Decimal("3250.49"))

            payment = app.fee_service.process_payment(student_id, fee_ids)
            self.assertEqual([item["item_name"] for item in payment["paid_items"]], ["April 2026", "May 2026", "June 2026"])
            self.assertEqual([self._money(item["amount"]) for item in payment["paid_items"]], [Decimal("1000.00"), Decimal("1250.50"), Decimal("999.99")])
            self.assertEqual(self._money(payment["total_paid"]), Decimal("3250.49"))

            receipt_rows = fetch_all(
                app,
                """
                SELECT receipts.id AS receipt_item_id,receipts.student_fee_record_id,
                       receipts.item_name,receipts.amount,records.amount AS fee_amount
                FROM fee_receipts AS receipts
                JOIN student_fee_records AS records ON records.id=receipts.student_fee_record_id
                WHERE receipts.payment_id=? AND receipts.status='Paid'
                ORDER BY receipts.id
                """,
                (payment["payment_id"],),
            )
            self.assertEqual([int(row["student_fee_record_id"]) for row in receipt_rows], fee_ids)
            self.assertEqual([row["item_name"] for row in receipt_rows], ["April 2026", "May 2026", "June 2026"])
            for row in receipt_rows:
                self.assertEqual(self._money(row["amount"]), self._money(row["fee_amount"]))
            stored_payment = fetch_one(app, "SELECT total_amount FROM fee_payments WHERE id=?", (payment["payment_id"],))
            self.assertEqual(self._money(stored_payment["total_amount"]), Decimal("3250.49"))

            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "partial payments"):
                    strict.execute("UPDATE fee_receipts SET amount=500 WHERE id=?", (int(receipt_rows[0]["receipt_item_id"]),))
                strict.rollback()
            finally:
                strict.close()

            candidate = app.root / "partial-payment-candidate.db"
            shutil.copy2(app.database_path, candidate)
            connection = sqlite3.connect(candidate)
            connection.create_function("HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True)
            try:
                connection.execute("DROP TRIGGER trg_fee_receipts_integrity_update")
                connection.execute("UPDATE fee_receipts SET amount=500 WHERE id=?", (int(receipt_rows[0]["receipt_item_id"]),))
                connection.execute("UPDATE fee_payments SET total_amount=2750.49 WHERE id=?", (payment["payment_id"],))
                schema_integrity = importlib.import_module("app.schema_integrity")
                connection.execute(
                    schema_integrity.INTEGRITY_TRIGGER_SQL[
                        "trg_fee_receipts_integrity_update"
                    ]
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(str(candidate), require_current_schema=True)
            self.assertFalse(valid, message)
            self.assertIn("complete linked fee obligation", message.lower())


if __name__ == "__main__":
    unittest.main()
