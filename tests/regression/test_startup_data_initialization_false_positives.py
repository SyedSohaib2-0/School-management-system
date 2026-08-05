from __future__ import annotations

from datetime import datetime as real_datetime
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    fetch_one,
    insert_class,
    insert_student,
)


class RolledBackDateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 1, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class StartupDataInitializationFalsePositiveTests(unittest.TestCase):
    @staticmethod
    def _insert_monthly_fee(
        app: IsolatedApplication,
        *,
        student_id: int,
        class_id: int,
        month: int = 8,
        year: int = 2026,
        amount: float = 2000.0,
    ) -> int:
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_fee_records(
                    student_id,class_id,session_year,fee_type,month_name,
                    month_number,year,amount,status,paid_date,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,
                         '2026-08-01 10:00:00','2026-08-01 10:00:00')
                """,
                (
                    student_id,
                    class_id,
                    "2026-2027",
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
    def _validate(app: IsolatedApplication) -> tuple[bool, str]:
        connection = app.strict_connect()
        try:
            return app.database.schema_integrity.validate_current_schema(connection)
        finally:
            connection.close()

    def _paid_fee(self, app: IsolatedApplication, *, father_cnic: str | None = None):
        class_id = insert_class(app, "2", "A")
        student_id = insert_student(
            app,
            class_id=class_id,
            admission_number="INIT001",
            roll_number=1,
            name="ORIGINAL STUDENT",
            father_name="ORIGINAL FATHER",
            father_cnic=father_cnic,
        )
        fee_id = self._insert_monthly_fee(
            app,
            student_id=student_id,
            class_id=class_id,
        )
        payment = app.fee_service.process_payment(student_id, [fee_id])
        return class_id, student_id, fee_id, payment

    def test_historical_payment_snapshot_survives_legitimate_student_identity_edit(self):
        with IsolatedApplication() as app:
            _, student_id, _, payment = self._paid_fee(app)
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_number='INIT002', student_name='UPDATED STUDENT',
                        father_name='UPDATED FATHER', father_cnic='1234567890123',
                        updated_at=datetime((SELECT created_at FROM fee_payments WHERE id=?), '+1 second')
                    WHERE student_id=?
                    """,
                    (payment["payment_id"], student_id),
                )
                connection.commit()
            finally:
                connection.close()

            valid, message = self._validate(app)
            self.assertTrue(valid, message)

    def test_cancelled_receipt_snapshot_survives_reopened_fee_class_change(self):
        with IsolatedApplication() as app:
            old_class_id, student_id, fee_id, payment = self._paid_fee(app)
            new_class_id = insert_class(app, "3", "A")
            app.fee_service.cancel_immediate_payment(payment["payment_id"], "test")

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET class_id=?,updated_at=datetime((SELECT created_at FROM fee_payments WHERE id=?), '+2 seconds') WHERE student_id=?",
                    (new_class_id, payment["payment_id"], student_id),
                )
                connection.execute(
                    "UPDATE student_fee_records SET class_id=?,amount=2500,updated_at=datetime((SELECT created_at FROM fee_receipts WHERE payment_id=? LIMIT 1), '+2 seconds') WHERE id=?",
                    (new_class_id, payment["payment_id"], fee_id),
                )
                connection.execute(
                    """
                    INSERT INTO student_class_history(
                        student_id,old_class_id,new_class_id,effective_month,effective_year,
                        change_type,change_datetime,changed_by
                    ) VALUES(?,?,?,?,?,'MANUAL','2026-08-02 10:00:00','test')
                    """,
                    (student_id, old_class_id, new_class_id, 8, 2026),
                )
                connection.commit()
            finally:
                connection.close()

            valid, message = self._validate(app)
            self.assertTrue(valid, message)

    def test_clock_rollback_after_admission_does_not_block_database_initialization(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="CLOCK001",
                roll_number=1,
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2026-09-01 09:00:00',admission_year=2026,
                        admission_month=9,admission_day=1,admission_time='09:00:00'
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.execute(
                    """
                    UPDATE student_class_history
                    SET effective_year=2026,effective_month=9,change_datetime='2026-09-01 09:00:00'
                    WHERE student_id=? AND change_type='ADMISSION'
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            schema_integrity = app.database.schema_integrity
            connection = app.strict_connect()
            try:
                with mock.patch.object(schema_integrity, "datetime", RolledBackDateTime):
                    valid, message = schema_integrity.validate_current_schema(connection)
            finally:
                connection.close()
            self.assertTrue(valid, message)

    def test_legacy_blank_father_cnic_can_be_paid_under_production_triggers(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="LEGACY001",
                roll_number=1,
                father_cnic="1234567890123",
            )
            fee_id = self._insert_monthly_fee(
                app,
                student_id=student_id,
                class_id=class_id,
            )
            fixture = app.connect()
            try:
                fixture.execute(
                    "UPDATE students SET father_cnic='' WHERE student_id=?",
                    (student_id,),
                )
                fixture.commit()
            finally:
                fixture.close()

            with mock.patch.object(app.fee_service, "get_connection", app.strict_connect):
                payment = app.fee_service.process_payment(student_id, [fee_id])

            row = fetch_one(
                app,
                "SELECT payment_status,father_cnic_snapshot FROM fee_payments WHERE id=?",
                (payment["payment_id"],),
            )
            self.assertEqual(row["payment_status"], "Completed")
            self.assertEqual(str(row["father_cnic_snapshot"] or ""), "")

    def test_active_paid_receipt_snapshot_corruption_is_still_rejected(self):
        with IsolatedApplication() as app:
            _, _, _, payment = self._paid_fee(app)
            other_class = insert_class(app, "5", "A")
            fixture = app.connect()
            try:
                fixture.execute(
                    "UPDATE fee_receipts SET class_id_snapshot=? WHERE payment_id=?",
                    (other_class, payment["payment_id"]),
                )
                fixture.commit()
            finally:
                fixture.close()

            valid, message = self._validate(app)
            self.assertFalse(valid)
            self.assertIn("receipt snapshots", message.lower())

    def test_blank_historical_payment_identity_snapshot_is_rejected(self):
        with IsolatedApplication() as app:
            _, _, _, payment = self._paid_fee(app)
            fixture = app.connect()
            try:
                fixture.execute(
                    "UPDATE fee_payments SET student_name_snapshot='' WHERE id=?",
                    (payment["payment_id"],),
                )
                fixture.commit()
            finally:
                fixture.close()

            valid, message = self._validate(app)
            self.assertFalse(valid)
            self.assertIn("payment identity snapshots", message.lower())

    def test_historical_sibling_identity_and_clock_rollback_do_not_block_startup(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "6", "A")
            old_cnic = "1234512345671"
            new_cnic = "1234512345672"
            first = insert_student(
                app,
                class_id=class_id,
                admission_number="SIBH001",
                roll_number=1,
                name="FIRST SIBLING",
                father_name="SAME FATHER",
                father_cnic=old_cnic,
            )
            second = insert_student(
                app,
                class_id=class_id,
                admission_number="SIBH002",
                roll_number=2,
                name="SECOND SIBLING",
                father_name="SAME FATHER",
                father_cnic=old_cnic,
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO sibling_fee_settings(
                        monthly_fee_percentage,effective_from_month,
                        effective_from_year,created_at,created_by,is_active
                    ) VALUES(50,7,2026,'2026-07-01 09:00:00','test',1)
                    """
                )
                connection.executemany(
                    """
                    INSERT INTO sibling_fee_assignments(
                        student_id,normalized_father_name,normalized_father_cnic,
                        monthly_fee_percentage,is_sibling_policy,assignment_role,
                        effective_from_month,effective_from_year,created_at,is_active
                    ) VALUES(?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        (first, "SAME FATHER", old_cnic, 100, 1, "FULL", 7, 2026, "2026-07-01 10:00:00"),
                        (second, "SAME FATHER", old_cnic, 50, 1, "DISCOUNTED", 7, 2026, "2026-07-01 10:00:00"),
                    ),
                )
                connection.execute(
                    "UPDATE students SET father_cnic=?,updated_at='2026-08-01 10:00:00' WHERE student_id=?",
                    (new_cnic, first),
                )
                connection.executemany(
                    """
                    INSERT INTO sibling_fee_assignments(
                        student_id,normalized_father_name,normalized_father_cnic,
                        monthly_fee_percentage,is_sibling_policy,assignment_role,
                        effective_from_month,effective_from_year,created_at,is_active
                    ) VALUES(?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        (first, "SAME FATHER", new_cnic, 100, 0, "STANDARD", 8, 2026, "2026-08-01 10:00:01"),
                        (second, "SAME FATHER", old_cnic, 100, 0, "STANDARD", 8, 2026, "2026-08-01 10:00:01"),
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            schema_integrity = app.database.schema_integrity
            connection = app.strict_connect()
            try:
                with mock.patch.object(schema_integrity, "datetime", RolledBackDateTime):
                    valid, message = schema_integrity.validate_current_schema(connection)
            finally:
                connection.close()
            self.assertTrue(valid, message)

    def test_latest_sibling_assignment_identity_corruption_is_still_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "7", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="SIBC001",
                roll_number=1,
                father_name="SAME FATHER",
                father_cnic="1234512345671",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO sibling_fee_assignments(
                        student_id,normalized_father_name,normalized_father_cnic,
                        monthly_fee_percentage,is_sibling_policy,assignment_role,
                        effective_from_month,effective_from_year,created_at,is_active
                    ) VALUES(?, 'SAME FATHER','9999999999998',100,0,'STANDARD',
                             8,2026,'2026-08-01 10:00:00',1)
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            valid, message = self._validate(app)
            self.assertFalse(valid)
            self.assertIn("sibling assignment cnic", message.lower())


if __name__ == "__main__":
    unittest.main()
