from __future__ import annotations

from datetime import datetime
import importlib
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, insert_class, insert_student


class FirstSiblingClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 9, 1, 4, 12, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class RolledBackSiblingClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 9, 1, 4, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


def insert_monthly_fee(
    app: IsolatedApplication,
    *,
    student_id: int,
    class_id: int,
    month_number: int = 7,
    year: int = 2026,
    amount: float = 1000.0,
) -> int:
    month_name = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }[int(month_number)]
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id,class_id,session_year,fee_type,
                month_name,month_number,year,amount,status,paid_date,
                created_at,updated_at
            ) VALUES(?,?,'2026-2027','Monthly',?,?,?,?,'Unpaid',NULL,
                     '2026-07-01 09:00:00','2026-07-01 09:00:00')
            """,
            (int(student_id), int(class_id), month_name, int(month_number),
             int(year), float(amount)),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


class DeeperStartupClockAndSequenceHardeningSameV31Tests(unittest.TestCase):
    def _create_sibling_group(self, app: IsolatedApplication) -> None:
        class_7 = insert_class(app, "7", "A")
        class_10 = insert_class(app, "10", "A")
        for index, class_id in enumerate((class_7, class_7, class_10), start=1):
            insert_student(
                app,
                class_id=class_id,
                admission_number=f"HCLK{index}",
                roll_number=index if class_id == class_7 else 1,
                name=f"Clock Sibling {index}",
                father_name="Clock Father",
                father_cnic="1730112345671",
            )

    def test_sibling_setting_revision_after_clock_rollback_survives_reopen(self):
        with IsolatedApplication() as app:
            self._create_sibling_group(app)
            with mock.patch.object(
                app.sibling_fee_service, "datetime", FirstSiblingClock
            ):
                app.sibling_fee_service.save_sibling_fee_percentage(
                    50, created_by="regression"
                )
            with mock.patch.object(
                app.sibling_fee_service, "datetime", RolledBackSiblingClock
            ):
                app.sibling_fee_service.save_sibling_fee_percentage(
                    80, created_by="regression"
                )

            schema_integrity = importlib.import_module("app.schema_integrity")
            connection = app.connect()
            try:
                valid, message = schema_integrity.validate_sibling_assignment_integrity(
                    connection
                )
            finally:
                connection.close()
            self.assertTrue(valid, message)
            app.database.initialize_database()

    def test_clock_rollback_sibling_fix_does_not_accept_unknown_percentage(self):
        with IsolatedApplication() as app:
            self._create_sibling_group(app)
            with mock.patch.object(
                app.sibling_fee_service, "datetime", FirstSiblingClock
            ):
                app.sibling_fee_service.save_sibling_fee_percentage(50)
            with mock.patch.object(
                app.sibling_fee_service, "datetime", RolledBackSiblingClock
            ):
                app.sibling_fee_service.save_sibling_fee_percentage(80)
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE sibling_fee_assignments
                    SET monthly_fee_percentage=40
                    WHERE assignment_role='DISCOUNTED' AND is_active=0
                    """
                )
                connection.commit()
                schema_integrity = importlib.import_module("app.schema_integrity")
                valid, message = schema_integrity.validate_sibling_assignment_integrity(
                    connection
                )
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("role fields are contradictory", message)

    def test_normal_history_tampered_to_another_configured_percentage_is_rejected(self):
        with IsolatedApplication() as app:
            self._create_sibling_group(app)
            with mock.patch.object(
                app.sibling_fee_service, "datetime", RolledBackSiblingClock
            ):
                app.sibling_fee_service.save_sibling_fee_percentage(50)
            with mock.patch.object(
                app.sibling_fee_service, "datetime", FirstSiblingClock
            ):
                app.sibling_fee_service.save_sibling_fee_percentage(80)
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE sibling_fee_assignments
                    SET monthly_fee_percentage=80
                    WHERE assignment_role='DISCOUNTED' AND is_active=0
                    """
                )
                connection.commit()
                schema_integrity = importlib.import_module("app.schema_integrity")
                valid, message = schema_integrity.validate_sibling_assignment_integrity(
                    connection
                )
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("role fields are contradictory", message)

    def test_date_only_payment_timestamp_allows_same_day_created_at(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="HDATE1", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            result = app.fee_service.process_payment(student_id, [fee_id])
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE fee_payments SET payment_date='2026-07-28' WHERE id=?",
                    (int(result["payment_id"]),),
                )
                connection.execute(
                    "UPDATE fee_payments SET created_at='2026-07-28 16:45:30' WHERE id=?",
                    (int(result["payment_id"]),),
                )
                connection.commit()
            finally:
                connection.close()
            app.database.initialize_database()

    def test_date_only_payment_timestamp_rejects_later_created_day(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="HDATE2", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            result = app.fee_service.process_payment(student_id, [fee_id])
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE fee_payments
                    SET payment_date='2026-07-28', created_at='2026-07-29 00:00:00'
                    WHERE id=?
                    """,
                    (int(result["payment_id"]),),
                )
                connection.commit()
                schema_integrity = importlib.import_module("app.schema_integrity")
                valid, message = schema_integrity.validate_payment_timestamps(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("creation time is later than payment time", message)

    def test_stale_receipt_sequence_never_reuses_historical_number(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            for number in range(1, 5):
                student_id = insert_student(
                    app,
                    class_id=class_id,
                    admission_number=f"HSEQ{number}",
                    roll_number=number,
                )
                fee_id = insert_monthly_fee(
                    app, student_id=student_id, class_id=class_id
                )
                result = app.fee_service.process_payment(student_id, [fee_id])
                self.assertEqual(result["receipt_no"], f"{number:06d}")

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE receipt_number_sequence SET last_issued_number=1 WHERE id=1"
                )
                connection.commit()
            finally:
                connection.close()

            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="HSEQ5",
                roll_number=5,
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            result = app.fee_service.process_payment(student_id, [fee_id])
            self.assertEqual(result["receipt_no"], "000005")

    def test_receipt_sequence_ahead_of_history_remains_monotonic(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE receipt_number_sequence SET last_issued_number=99 WHERE id=1"
                )
                connection.commit()
            finally:
                connection.close()
            student_id = insert_student(
                app, class_id=class_id, admission_number="HSEQ100", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            result = app.fee_service.process_payment(student_id, [fee_id])
            self.assertEqual(result["receipt_no"], "000100")


if __name__ == "__main__":
    unittest.main()
