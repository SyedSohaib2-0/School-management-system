from __future__ import annotations

from datetime import datetime
import importlib
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def update_values(app, student_id, **overrides):
    row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
    values = {
        "student_id": int(student_id),
        "admission_number": row["admission_number"],
        "roll_number": int(row["roll_number"]),
        "class_id": int(row["class_id"]),
        "student_name": row["student_name"],
        "father_name": row["father_name"],
        "father_cnic": row["father_cnic"],
        "father_contact_number": row["father_contact_number"],
        "student_status": row["student_status"],
        "school_leaving_certificate_submitted": bool(
            row["school_leaving_certificate_submitted"]
        ),
        "admission_year": int(row["admission_year"]),
        "admission_month": int(row["admission_month"]),
        "admission_day": int(row["admission_day"]),
        "admission_time": row["admission_time"] or "09:00:00",
        "gender": row["gender"] or "Male",
        "whatsapp_number": row["whatsapp_number"] or "",
        "current_address": row["current_address"] or "",
        "permanent_address": row["permanent_address"] or "",
        "date_of_birth": row["date_of_birth"],
        "form_b_submitted": bool(row["form_b_submitted"]),
        "birth_certificate_submitted": bool(row["birth_certificate_submitted"]),
        "updated_by": "v16-regression",
    }
    values.update(overrides)
    return values


def insert_monthly_fee(
    app,
    *,
    student_id,
    class_id,
    session_year,
    month_name,
    month_number,
    year,
    amount,
    status="Unpaid",
):
    now = "2026-07-29 00:00:00"
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id, class_id, session_year, fee_type,
                month_name, month_number, year, amount, status,
                paid_date, created_at, updated_at
            ) VALUES (?, ?, ?, 'Monthly', ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                student_id, class_id, session_year, month_name,
                month_number, year, amount, status, now, now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


class FixedJulyDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 12, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class AdditionalBugFixV16ScopeTests(unittest.TestCase):
    def test_sibling_assignment_audit_and_append_only_percentage_timeline(self):
        with IsolatedApplication() as app:
            sibling_service = importlib.import_module(
                "app.services.sibling_fee_service"
            )
            class_id = insert_class(app, "5", "A")
            app.fee_service.set_class_fee(class_id, 2000, 4, 2026)
            app.fee_service.set_class_fee(class_id, 2200, 4, 2027)
            first = insert_student(
                app, class_id=class_id, admission_number="H1601", roll_number=1,
                name="ALI KHAN", father_name="AHMAD KHAN",
                father_cnic="1234512345671",
            )
            second = insert_student(
                app, class_id=class_id, admission_number="H1602", roll_number=2,
                name="BILAL KHAN", father_name="  Ahmad   Khan ",
                father_cnic="12345-1234567-1",
            )
            app.fee_service.ensure_fee_records_generated(first)
            app.fee_service.ensure_fee_records_generated(second)

            with mock.patch.object(sibling_service, "datetime", FixedJulyDateTime):
                sibling_service.save_sibling_fee_percentage(50, created_by="first-user")

            assignments_before = [dict(row) for row in fetch_all(
                app,
                "SELECT * FROM sibling_fee_assignments ORDER BY id",
            )]
            self.assertEqual(len(assignments_before), 2)

            # An address-only edit must not rewrite sibling assignment audit rows.
            app.student_service.update_student(
                **update_values(app, second, current_address="NEW ADDRESS")
            )
            assignments_after_address = [dict(row) for row in fetch_all(
                app, "SELECT * FROM sibling_fee_assignments ORDER BY id"
            )]
            self.assertEqual(assignments_after_address, assignments_before)

            discounted = fetch_one(
                app,
                "SELECT student_id FROM sibling_fee_assignments "
                "WHERE assignment_role='DISCOUNTED' ORDER BY id DESC LIMIT 1",
            )
            paid_student = int(discounted["student_id"])
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE student_fee_records
                    SET status='Paid', paid_date='2026-07-15'
                    WHERE student_id=? AND month_number=7 AND year=2026
                    """,
                    (paid_student,),
                )
                paid_before = dict(connection.execute(
                    "SELECT id, amount, status, paid_date FROM student_fee_records "
                    "WHERE student_id=? AND month_number=7 AND year=2026",
                    (paid_student,),
                ).fetchone())
                connection.commit()
            finally:
                connection.close()

            # A second setting in the same month is append-only and supersedes,
            # rather than deleting, the earlier record.
            with mock.patch.object(sibling_service, "datetime", FixedJulyDateTime):
                sibling_service.save_sibling_fee_percentage(60, created_by="second-user")

            settings = fetch_all(
                app,
                "SELECT id, monthly_fee_percentage, created_by, is_active, "
                "superseded_at, superseded_by FROM sibling_fee_settings ORDER BY id",
            )
            self.assertEqual(len(settings), 2)
            self.assertEqual(float(settings[0]["monthly_fee_percentage"]), 50.0)
            self.assertEqual(int(settings[0]["is_active"]), 0)
            self.assertEqual(settings[0]["superseded_by"], "second-user")
            self.assertEqual(float(settings[1]["monthly_fee_percentage"]), 60.0)
            self.assertEqual(int(settings[1]["is_active"]), 1)

            connection = app.connect()
            try:
                future_setting = sibling_service._setting_for_period(
                    connection.cursor(), 4, 2027
                )
            finally:
                connection.close()
            self.assertEqual(float(future_setting["monthly_fee_percentage"]), 60.0)

            paid_after = dict(fetch_one(
                app,
                "SELECT id, amount, status, paid_date FROM student_fee_records "
                "WHERE student_id=? AND month_number=7 AND year=2026",
                (paid_student,),
            ))
            self.assertEqual(paid_after, paid_before)

            # Recalculating an identical same-period assignment preserves IDs.
            ids_before = [int(row["id"]) for row in fetch_all(
                app, "SELECT id FROM sibling_fee_assignments ORDER BY id"
            )]
            sibling_service.recalculate_sibling_fees_for_student_ids(
                [first, second], 7, 2026, changed_by="identical-recheck"
            )
            ids_after = [int(row["id"]) for row in fetch_all(
                app, "SELECT id FROM sibling_fee_assignments ORDER BY id"
            )]
            self.assertEqual(ids_after, ids_before)

            # A sibling-relevant identity change triggers recalculation.
            app.student_service.update_student(
                **update_values(app, second, father_name="DIFFERENT FATHER")
            )
            self.assertGreater(
                int(fetch_one(
                    app, "SELECT COUNT(*) AS n FROM sibling_fee_assignments"
                )["n"]),
                len(ids_before),
            )

    def test_student_search_is_read_only_and_archive_events_use_status_history(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")
            app.fee_service.set_class_fee(class_id, 1000, 4, 2025)
            dropped = insert_student(
                app, class_id=class_id, admission_number="H1610", roll_number=1,
                status="Dropped", father_cnic="1234512345671",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO student_class_history(
                        student_id, old_class_id, new_class_id,
                        effective_month, effective_year, change_type,
                        change_datetime, changed_by
                    ) VALUES(?, NULL, ?, 3, 2026, 'MANUAL',
                             '2026-03-10 10:00:00', 'drop-test')
                    """,
                    (dropped, class_id),
                )
                connection.commit()
            finally:
                connection.close()

            before = dict(fetch_one(
                app, "SELECT * FROM students WHERE student_id=?", (dropped,)
            ))
            rows = app.student_service.search_students(
                admission_number="H1610", include_archived=True
            )
            self.assertEqual(len(rows), 1)
            after = dict(fetch_one(
                app, "SELECT * FROM students WHERE student_id=?", (dropped,)
            ))
            self.assertEqual(after, before)
            search_source = (
                PROJECT_ROOT / "app/modules/student_management.py"
            ).read_text(encoding="utf-8")
            search_method = search_source[
                search_source.index("    def _perform_search(self):"):
                search_source.index("    def _clear_search", search_source.index("    def _perform_search(self):"))
            ]
            self.assertNotIn("archive_eligible_dropped_students", search_method)

            result = app.student_service.archive_eligible_dropped_students(
                as_of=datetime(2026, 7, 29, 12, 0, 0),
                student_ids=[dropped],
            )
            self.assertEqual(result["archived_student_ids"], [dropped])
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (dropped,))["student_status"],
                "Archived",
            )
            status_event = fetch_one(
                app,
                "SELECT old_status, new_status, changed_by FROM student_status_history "
                "WHERE student_id=? ORDER BY id DESC LIMIT 1",
                (dropped,),
            )
            self.assertEqual(
                (status_event["old_status"], status_event["new_status"], status_event["changed_by"]),
                ("Dropped", "Archived", "automatic_archive"),
            )
            self.assertEqual(
                int(fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM student_class_history "
                    "WHERE student_id=? AND changed_by='automatic_archive'",
                    (dropped,),
                )["n"]),
                0,
            )

            # Final payment archives an eligible Dropped student in the same
            # controlled payment transaction.
            payment_student = insert_student(
                app, class_id=class_id, admission_number="H1611", roll_number=2,
                status="Dropped", father_cnic="9876598765431",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO student_class_history(
                        student_id, old_class_id, new_class_id,
                        effective_month, effective_year, change_type,
                        change_datetime, changed_by
                    ) VALUES(?, NULL, ?, 3, 2026, 'MANUAL',
                             '2026-03-10 10:00:00', 'drop-test')
                    """,
                    (payment_student, class_id),
                )
                connection.commit()
            finally:
                connection.close()
            fee_id = insert_monthly_fee(
                app, student_id=payment_student, class_id=class_id,
                session_year="2025-2026", month_name="March",
                month_number=3, year=2026, amount=1000,
            )
            payment = app.fee_service.process_payment(payment_student, [fee_id])
            self.assertTrue(payment["student_archived"])
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (payment_student,))["student_status"],
                "Archived",
            )

    def test_receipt_sequence_never_reuses_cancelled_deleted_or_migrated_numbers(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            students = [
                insert_student(
                    app, class_id=class_id, admission_number=f"H162{i}",
                    roll_number=i, name=f"STUDENT {i}",
                )
                for i in range(1, 4)
            ]
            fee_ids = [
                insert_monthly_fee(
                    app, student_id=student_id, class_id=class_id,
                    session_year="2026-2027", month_name="July",
                    month_number=7, year=2026, amount=1000,
                )
                for student_id in students
            ]
            first = app.fee_service.process_payment(students[0], [fee_ids[0]])
            self.assertEqual(first["receipt_no"], "000001")
            app.fee_service.cancel_immediate_payment(first["payment_id"], "v16-test")
            second = app.fee_service.process_payment(students[1], [fee_ids[1]])
            self.assertEqual(second["receipt_no"], "000002")

            connection = app.connect()
            try:
                connection.execute(
                    "DELETE FROM fee_receipts WHERE payment_id=?",
                    (second["payment_id"],),
                )
                connection.execute(
                    "DELETE FROM fee_payments WHERE id=?",
                    (second["payment_id"],),
                )
                connection.commit()
            finally:
                connection.close()
            third = app.fee_service.process_payment(students[2], [fee_ids[2]])
            self.assertEqual(third["receipt_no"], "000003")
            self.assertEqual(
                int(fetch_one(
                    app,
                    "SELECT last_issued_number FROM receipt_number_sequence WHERE id=1",
                )["last_issued_number"]),
                3,
            )

        # Version-8 Admission Fee cleanup seeds the sequence before deletion.
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1629", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute("DROP TABLE receipt_number_sequence")
                fee_cursor = connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id, class_id, session_year, fee_type,
                        month_name, month_number, year, amount, status,
                        paid_date, created_at, updated_at
                    ) VALUES(?, ?, '2026-2027', 'Admission', 'Admission Fee',
                             4, 2026, 500, 'Paid', '2026-04-01',
                             '2026-04-01', '2026-04-01')
                    """,
                    (student_id, class_id),
                )
                payment_cursor = connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no, student_id, payment_date, total_amount,
                        created_at, payment_status
                    ) VALUES('000100', ?, '2026-04-01', 500,
                             '2026-04-01', 'Completed')
                    """,
                    (student_id,),
                )
                connection.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id, student_fee_record_id, item_name,
                        amount, status, created_at
                    ) VALUES(?, ?, 'Admission Fee', 500, 'Paid', '2026-04-01')
                    """,
                    (int(payment_cursor.lastrowid), int(fee_cursor.lastrowid)),
                )
                connection.execute("PRAGMA user_version=7")
                connection.commit()
            finally:
                connection.close()
            app.database.initialize_database()
            self.assertEqual(
                int(fetch_one(
                    app,
                    "SELECT last_issued_number FROM receipt_number_sequence WHERE id=1",
                )["last_issued_number"]),
                100,
            )
            next_fee = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id,
                session_year="2026-2027", month_name="July",
                month_number=7, year=2026, amount=1000,
            )
            self.assertEqual(
                app.fee_service.process_payment(student_id, [next_fee])["receipt_no"],
                "000101",
            )

    def test_tracking_id_migration_canonicalizes_and_blocks_collisions(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            ids = [
                insert_student(
                    app, class_id=class_id, admission_number=f"H163{i}", roll_number=i
                )
                for i in range(1, 4)
            ]
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_students_tracking_id_normalized_unique")
                connection.execute("UPDATE students SET tracking_id='HMS-1' WHERE student_id=?", (ids[0],))
                connection.execute("UPDATE students SET tracking_id='hms-0002' WHERE student_id=?", (ids[1],))
                connection.execute("UPDATE students SET tracking_id='3' WHERE student_id=?", (ids[2],))
                connection.execute("PRAGMA user_version=16")
                connection.commit()
            finally:
                connection.close()
            app.database.initialize_database()
            values = [row["tracking_id"] for row in fetch_all(
                app, "SELECT tracking_id FROM students ORDER BY student_id"
            )]
            self.assertEqual(values, ["HMS-000001", "HMS-000002", "HMS-000003"])
            connection = app.connect()
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE students SET tracking_id='hms-000001' WHERE student_id=?",
                        (ids[1],),
                    )
                connection.rollback()
            finally:
                connection.close()
            self.assertEqual(
                app.fee_service.search_students_for_fee(tracking_id="hms-1")[0]["student_id"],
                ids[0],
            )

        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            first = insert_student(app, class_id=class_id, admission_number="H1638", roll_number=1)
            second = insert_student(app, class_id=class_id, admission_number="H1639", roll_number=2)
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_students_tracking_id_normalized_unique")
                connection.execute("UPDATE students SET tracking_id='HMS-1' WHERE student_id=?", (first,))
                connection.execute("UPDATE students SET tracking_id='1' WHERE student_id=?", (second,))
                connection.execute("PRAGMA user_version=16")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeError, "manual reconciliation"):
                app.database.initialize_database()
            rows = fetch_all(
                app,
                "SELECT student_id, tracking_id FROM students ORDER BY student_id",
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["tracking_id"] for row in rows], ["HMS-1", "1"])
            reports = list(Path(app.config.BACKUPS_DIR).glob("Tracking_ID_Reconciliation_*.txt"))
            self.assertTrue(reports)

    def test_repeated_digit_cnic_is_rejected_and_sibling_matching_requires_name_and_cnic(self):
        with IsolatedApplication() as app:
            for digit in "0123456789":
                with self.assertRaisesRegex(ValueError, "same digit repeated"):
                    app.student_service.normalize_father_cnic(digit * 13)

            sibling_service = importlib.import_module(
                "app.services.sibling_fee_service"
            )
            class_id = insert_class(app, "5", "A")
            app.fee_service.set_class_fee(class_id, 2000, 4, 2026)
            matching_one = insert_student(
                app, class_id=class_id, admission_number="H1641", roll_number=1,
                name="ONE", father_name="AHMAD KHAN", father_cnic="1234512345671",
            )
            different_name = insert_student(
                app, class_id=class_id, admission_number="H1642", roll_number=2,
                name="TWO", father_name="MUHAMMAD KHAN", father_cnic="1234512345671",
            )
            different_cnic = insert_student(
                app, class_id=class_id, admission_number="H1643", roll_number=3,
                name="THREE", father_name="AHMAD KHAN", father_cnic="9876598765431",
            )
            matching_two = insert_student(
                app, class_id=class_id, admission_number="H1644", roll_number=4,
                name="FOUR", father_name=" AHMAD   KHAN ", father_cnic="12345-1234567-1",
            )
            repeated_legacy = insert_student(
                app, class_id=class_id, admission_number="H1645", roll_number=5,
                name="LEGACY", father_name="AHMAD KHAN", father_cnic="1111111111111",
            )
            with mock.patch.object(sibling_service, "datetime", FixedJulyDateTime):
                sibling_service.save_sibling_fee_percentage(50, created_by="v16-test")
            assignments = {
                int(row["student_id"]): dict(row)
                for row in fetch_all(
                    app,
                    "SELECT student_id, is_sibling_policy, assignment_role "
                    "FROM sibling_fee_assignments ORDER BY id",
                )
            }
            self.assertEqual(int(assignments[matching_one]["is_sibling_policy"]), 1)
            self.assertEqual(int(assignments[matching_two]["is_sibling_policy"]), 1)
            self.assertEqual(int(assignments[different_name]["is_sibling_policy"]), 0)
            self.assertEqual(int(assignments[different_cnic]["is_sibling_policy"]), 0)
            self.assertNotIn(repeated_legacy, assignments)

            with self.assertRaisesRegex(ValueError, "same digit repeated"):
                app.student_service.update_student(
                    **update_values(app, repeated_legacy, current_address="CANNOT SAVE")
                )


if __name__ == "__main__":
    unittest.main()
