from __future__ import annotations

from datetime import datetime
import importlib
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    allowed_promotion_window,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class FixedPaymentDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 7, 15, 9, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class SameSecondEditDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 8, 2, 19, 30, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class RolledBackEditDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 1, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


def _validate_and_reopen(app: IsolatedApplication) -> None:
    schema_integrity = importlib.import_module("app.schema_integrity")
    connection = app.strict_connect()
    try:
        valid, message = schema_integrity.validate_current_schema(connection)
    finally:
        connection.close()
    if not valid:
        raise AssertionError(message)

    # This is the same final startup path that previously raised the user's
    # "database failed final schema verification" error.
    app.database.initialize_database()


def _set_capacity(app: IsolatedApplication, class_id: int, capacity: int) -> None:
    connection = app.connect()
    try:
        connection.execute(
            "UPDATE classes SET student_capacity=? WHERE id=?",
            (int(capacity), int(class_id)),
        )
        connection.commit()
    finally:
        connection.close()


def _mark_destination_promoted(
    app: IsolatedApplication, class_id: int, class_name: str
) -> None:
    connection = app.connect()
    try:
        connection.execute(
            """
            INSERT INTO promotion_batches(
                source_class_id, source_class_name, destination_class_id,
                destination_class_name, promotion_datetime, total_students,
                passed_students, failed_students, graduated_students,
                created_by, is_reversed, source_session_start_year,
                target_session_start_year, effective_date, promotion_mode
            ) VALUES (?, ?, NULL, 'No Passed Students',
                      '2027-03-01 09:00:00', 1, 0, 1, 0, 'test', 0,
                      2026, 2027, '2027-04-01', 'NORMAL')
            """,
            (int(class_id), class_name),
        )
        connection.commit()
    finally:
        connection.close()


def _update_student_identity(
    app: IsolatedApplication,
    student_id: int,
    *,
    student_name: str,
    father_name: str,
    father_cnic: str,
) -> None:
    row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
    app.student_service.update_student(
        student_id=student_id,
        roll_number=int(row["roll_number"]),
        class_id=int(row["class_id"]),
        student_name=student_name,
        father_name=father_name,
        father_cnic=father_cnic,
        father_contact_number=row["father_contact_number"],
        student_status=row["student_status"],
        school_leaving_certificate_submitted=bool(
            row["school_leaving_certificate_submitted"]
        ),
        admission_year=int(row["admission_year"]),
        admission_month=int(row["admission_month"]),
        admission_day=int(row["admission_day"]),
        admission_time=row["admission_time"],
        updated_by="startup-hardening-test",
        admission_number=None,
        gender=row["gender"] or "Male",
        whatsapp_number=row["whatsapp_number"] or "",
        current_address=row["current_address"] or "",
        permanent_address=row["permanent_address"] or "",
        date_of_birth=row["date_of_birth"] or "",
        form_b_submitted=bool(row["form_b_submitted"]),
        birth_certificate_submitted=bool(row["birth_certificate_submitted"]),
    )


def _create_and_pay_first_month(
    app: IsolatedApplication,
    student_id: int,
    class_id: int,
) -> dict:
    app.fee_service.set_class_fee(class_id, 1200, 4, 2026)
    app.fee_service.ensure_fee_records_generated(
        student_id, as_of=datetime(2026, 8, 2, 19, 0, 0)
    )
    first = fetch_one(
        app,
        """
        SELECT id FROM student_fee_records
        WHERE student_id=? AND fee_type='Monthly' AND status='Unpaid'
        ORDER BY year, month_number, id LIMIT 1
        """,
        (student_id,),
    )
    with mock.patch.object(app.fee_service, "datetime", SameSecondEditDateTime):
        return app.fee_service.process_payment(student_id, [int(first["id"])])


class AdditionalStartupHardeningSameVersionTests(unittest.TestCase):
    def test_failure_relocation_result_r_survives_full_startup_validation(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "5", "A")
            class_6a = insert_class(app, "6", "A")
            class_6b = insert_class(app, "6", "B")
            _set_capacity(app, class_6a, 2)
            _set_capacity(app, class_6b, 2)

            failed = insert_student(
                app,
                class_id=class_6a,
                admission_number="START-R-001",
                roll_number=1,
                name="Existing Failed Student",
                failure_count=1,
            )
            incoming_one = insert_student(
                app,
                class_id=source,
                admission_number="START-R-002",
                roll_number=1,
                name="Incoming Student One",
            )
            incoming_two = insert_student(
                app,
                class_id=source,
                admission_number="START-R-003",
                roll_number=2,
                name="Incoming Student Two",
            )
            _mark_destination_promoted(app, class_6a, "6A")
            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(source, 1300, 4, target_session - 1)
            app.fee_service.set_class_fee(class_6a, 1500, 4, target_session)
            app.fee_service.set_class_fee(class_6b, 1500, 4, target_session)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    source,
                    {incoming_one: "P", incoming_two: "P"},
                    "startup-hardening-test",
                )

            moved = fetch_one(
                app,
                "SELECT class_id FROM students WHERE student_id=?",
                (failed,),
            )
            relocation = fetch_one(
                app,
                """
                SELECT promotion_result,promotion_type
                FROM promotion_history
                WHERE student_id=?
                ORDER BY promotion_id DESC LIMIT 1
                """,
                (failed,),
            )
            self.assertEqual(int(moved["class_id"]), class_6b)
            self.assertEqual(
                (relocation["promotion_result"], relocation["promotion_type"]),
                ("R", "Failure Relocation"),
            )
            _validate_and_reopen(app)

    def test_cancelled_historical_receipt_can_differ_from_reopened_fee_row(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="START-CANCEL-001",
                roll_number=1,
            )
            app.fee_service.set_class_fee(class_1a, 1000, 4, 2026)
            app.fee_service.set_class_fee(class_1a, 1100, 4, 2027)
            app.fee_service.set_class_fee(class_2a, 1500, 4, 2027)
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2027, 7, 15, 8, 30, 0)
            )
            rows = fetch_all(
                app,
                """
                SELECT id,month_number,year
                FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                ORDER BY year,month_number,id
                """,
                (student_id,),
            )
            selected = [
                int(row["id"])
                for row in rows
                if (int(row["year"]), int(row["month_number"])) <= (2027, 4)
            ]

            with mock.patch.object(app.fee_service, "datetime", FixedPaymentDateTime):
                payment = app.fee_service.process_payment(student_id, selected)

            app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {student_id: "P"},
                "startup-hardening-test",
                as_of=datetime(2027, 7, 15, 12, 0, 0),
            )

            # Cancellation reopens the fee under current class/fee policy but
            # must preserve the immutable receipt snapshot from Payment A.
            with mock.patch.object(app.fee_service, "datetime", FixedPaymentDateTime):
                app.fee_service.cancel_immediate_payment(
                    int(payment["payment_id"]), "startup-hardening-test"
                )

            cancelled = fetch_one(
                app,
                """
                SELECT r.status AS receipt_status,
                       r.class_id_snapshot,
                       f.status AS fee_status,
                       f.class_id AS current_class_id,
                       f.amount AS current_amount
                FROM fee_receipts r
                JOIN student_fee_records f ON f.id=r.student_fee_record_id
                WHERE r.payment_id=? AND f.month_number=4 AND f.year=2027
                """,
                (int(payment["payment_id"]),),
            )
            self.assertEqual(cancelled["receipt_status"], "Cancelled")
            self.assertEqual(cancelled["fee_status"], "Unpaid")
            self.assertEqual(int(cancelled["class_id_snapshot"]), class_1a)
            self.assertEqual(int(cancelled["current_class_id"]), class_2a)
            self.assertEqual(float(cancelled["current_amount"]), 1500.0)
            _validate_and_reopen(app)

    def test_same_second_student_identity_edit_after_payment_does_not_block_startup(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="START-EDIT-001",
                roll_number=1,
                name="Student Before Edit",
                father_name="Father Before Edit",
                father_cnic="1111122222333",
            )
            payment = _create_and_pay_first_month(app, student_id, class_1a)
            with mock.patch.object(app.student_service, "datetime", SameSecondEditDateTime):
                _update_student_identity(
                    app,
                    student_id,
                    student_name="Student After Edit",
                    father_name="Father After Edit",
                    father_cnic="9999988888777",
                )

            snapshot = fetch_one(
                app,
                "SELECT student_name_snapshot FROM fee_payments WHERE id=?",
                (int(payment["payment_id"]),),
            )
            current = fetch_one(
                app,
                "SELECT student_name FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(str(snapshot["student_name_snapshot"]).upper(), "STUDENT BEFORE EDIT")
            self.assertEqual(str(current["student_name"]).upper(), "STUDENT AFTER EDIT")
            _validate_and_reopen(app)

    def test_clock_rollback_student_identity_edit_after_payment_does_not_block_startup(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="START-EDIT-002",
                roll_number=1,
                name="Rollback Before Edit",
                father_name="Rollback Father Before",
                father_cnic="1234512345123",
            )
            _create_and_pay_first_month(app, student_id, class_1a)
            with mock.patch.object(app.student_service, "datetime", RolledBackEditDateTime):
                _update_student_identity(
                    app,
                    student_id,
                    student_name="Rollback After Edit",
                    father_name="Rollback Father After",
                    father_cnic="3215432154321",
                )

            times = fetch_one(
                app,
                "SELECT created_at,updated_at FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertLess(str(times["updated_at"]), str(times["created_at"]))
            _validate_and_reopen(app)

    def test_empty_legacy_multiletter_class_is_reportable_but_not_operational(self):
        with IsolatedApplication() as app:
            legacy_class = insert_class(app, "4", "AA")
            _validate_and_reopen(app)

            insert_student(
                app,
                class_id=legacy_class,
                admission_number="START-LEGACY-001",
                roll_number=1,
                status="Active",
            )
            schema_integrity = importlib.import_module("app.schema_integrity")
            connection = app.strict_connect()
            try:
                valid, message = schema_integrity.validate_current_schema(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("single-letter section rule", message)
            self.assertIn("operational student", message)

    def test_tampered_never_edited_payment_snapshot_remains_rejected(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="START-TAMPER-001",
                roll_number=1,
                name="Untouched Student",
                father_name="Untouched Father",
                father_cnic="1020310203102",
            )
            payment = _create_and_pay_first_month(app, student_id, class_1a)

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE fee_payments SET student_name_snapshot='TAMPERED' WHERE id=?",
                    (int(payment["payment_id"]),),
                )
                connection.commit()
            finally:
                connection.close()

            schema_integrity = importlib.import_module("app.schema_integrity")
            connection = app.strict_connect()
            try:
                valid, message = schema_integrity.validate_current_schema(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("Payment identity snapshots do not match", message)


if __name__ == "__main__":
    unittest.main()
