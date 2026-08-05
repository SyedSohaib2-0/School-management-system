from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
        "updated_by": "v15-regression",
    }
    values.update(overrides)
    return values


class AdditionalBugFixV15ScopeTests(unittest.TestCase):
    def test_promotion_releases_dropped_and_archived_rolls_atomically(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "1", "A")
            destination = insert_class(app, "2", "A")
            app.fee_service.set_class_fee(source, 1000, 4, 2026)
            app.fee_service.set_class_fee(destination, 1200, 4, 2027)
            active = insert_student(
                app, class_id=source, admission_number="H1501", roll_number=1
            )
            dropped = insert_student(
                app, class_id=source, admission_number="H1502", roll_number=2,
                status="Dropped",
            )
            archived = insert_student(
                app, class_id=source, admission_number="H1503", roll_number=3,
                status="Archived",
            )
            insert_monthly_fee(
                app, student_id=dropped, class_id=source,
                session_year="2026-2027", month_name="March",
                month_number=3, year=2027, amount=1000,
            )

            before = fetch_all(
                app,
                "SELECT student_id, roll_number, released_roll_number FROM students "
                "WHERE student_id IN (?, ?) ORDER BY student_id",
                (dropped, archived),
            )
            self.assertEqual([int(row["roll_number"]) for row in before], [2, 3])

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    source, {active: "P"}, "v15-regression"
                )

            after = fetch_all(
                app,
                "SELECT student_id, student_status, roll_number, released_roll_number "
                "FROM students WHERE student_id IN (?, ?) ORDER BY student_id",
                (dropped, archived),
            )
            self.assertEqual([row["student_status"] for row in after], ["Dropped", "Archived"])
            self.assertEqual([int(row["released_roll_number"]) for row in after], [2, 3])
            self.assertTrue(all(int(row["roll_number"]) < 0 for row in after))
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM student_fee_records "
                    "WHERE student_id=? AND status='Unpaid'",
                    (dropped,),
                )["n"],
                1,
            )
            history = fetch_all(
                app,
                "SELECT student_id, old_class_id, new_class_id, changed_by "
                "FROM student_class_history WHERE student_id IN (?, ?) "
                "AND changed_by LIKE '%inactive roll release%' ORDER BY student_id",
                (dropped, archived),
            )
            self.assertEqual(len(history), 2)
            self.assertTrue(all(int(row["old_class_id"]) == source for row in history))
            self.assertTrue(all(int(row["new_class_id"]) == source for row in history))

            # A failed promotion must not release an inactive roll.
            failed_source = insert_class(app, "3", "A")
            failed_destination = insert_class(app, "4", "A")
            app.fee_service.set_class_fee(failed_source, 1000, 4, 2026)
            failed_active = insert_student(
                app, class_id=failed_source, admission_number="H1504", roll_number=1
            )
            failed_dropped = insert_student(
                app, class_id=failed_source, admission_number="H1505", roll_number=2,
                status="Dropped",
            )
            with allowed_promotion_window(app):
                with self.assertRaises(ValueError):
                    app.promotion_full_class_v2.promote_full_class(
                        failed_source, {failed_active: "P"}, "v15-regression"
                    )
            unchanged = fetch_one(
                app,
                "SELECT roll_number, released_roll_number FROM students WHERE student_id=?",
                (failed_dropped,),
            )
            self.assertEqual(int(unchanged["roll_number"]), 2)
            self.assertIsNone(unchanged["released_roll_number"])

    def test_individual_fee_removal_prevalidates_every_class_fee(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1510", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO student_fee_override(
                        student_id, monthly_fee, effective_from_month,
                        effective_from_year, is_active, created_at, updated_at
                    ) VALUES (?, 500, 4, 2026, 1, '2026-04-01', '2026-04-01')
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()
            february = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id,
                session_year="2026-2027", month_name="February",
                month_number=2, year=2027, amount=500,
            )
            march = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id,
                session_year="2026-2027", month_name="March",
                month_number=3, year=2027, amount=500,
            )
            before_override = dict(fetch_one(
                app, "SELECT * FROM student_fee_override WHERE student_id=?",
                (student_id,),
            ))
            before_rows = [dict(row) for row in fetch_all(
                app, "SELECT * FROM student_fee_records WHERE id IN (?, ?) ORDER BY id",
                (february, march),
            )]

            with self.assertRaisesRegex(ValueError, "Missing month: February 2027"):
                app.fee_service.remove_student_fee_override(
                    student_id, effective_from_month=2,
                    session_start_year=2026, removed_by="v15-regression",
                )

            self.assertEqual(
                dict(fetch_one(app, "SELECT * FROM student_fee_override WHERE student_id=?", (student_id,))),
                before_override,
            )
            self.assertEqual(
                [dict(row) for row in fetch_all(
                    app, "SELECT * FROM student_fee_records WHERE id IN (?, ?) ORDER BY id",
                    (february, march),
                )],
                before_rows,
            )

    def test_invalid_pending_restore_is_quarantined_and_startup_can_continue(self):
        with IsolatedApplication() as app:
            service = app.backup_service
            Path(service.PENDING_RESTORE_DIRECTORY).mkdir(parents=True, exist_ok=True)
            Path(service.PENDING_RESTORE_REQUEST_PATH).write_text("{damaged", encoding="utf-8")
            Path(service.PENDING_RESTORE_DATABASE_PATH).write_bytes(b"not sqlite")
            database_before = app.database_path.read_bytes()

            result = service.process_pending_restore_before_startup()
            self.assertTrue(result["success"])
            self.assertTrue(result["restore_quarantined"])
            self.assertTrue(result["startup_warning"])
            self.assertFalse(Path(service.PENDING_RESTORE_REQUEST_PATH).exists())
            self.assertTrue(Path(result["quarantine_report"]).is_file())
            self.assertEqual(app.database_path.read_bytes(), database_before)
            report = Path(result["quarantine_report"]).read_text(encoding="utf-8")
            self.assertIn("Live database changed: NO", report)

            second_start = service.process_pending_restore_before_startup()
            self.assertTrue(second_start["success"])
            self.assertFalse(second_start["restore_processed"])
            run_source = (PROJECT_ROOT / "run.py").read_text(encoding="utf-8")
            self.assertIn('restore_result.get("startup_warning", False)', run_source)

    def test_backup_validation_closes_connection_on_failure(self):
        with IsolatedApplication() as app, tempfile.TemporaryDirectory() as temp_dir:
            candidate = Path(temp_dir) / "damaged.db"
            candidate.write_bytes(b"damaged")

            class FailingConnection:
                def __init__(self):
                    self.closed = False
                def execute(self, _sql):
                    raise sqlite3.DatabaseError("invalid")
                def close(self):
                    self.closed = True

            fake = FailingConnection()
            with mock.patch.object(app.backup_service.sqlite3, "connect", return_value=fake):
                valid, _message = app.backup_service.validate_sqlite_database(str(candidate))
            self.assertFalse(valid)
            self.assertTrue(fake.closed)
            renamed = candidate.with_name("renamed.db")
            candidate.rename(renamed)
            renamed.unlink()
            self.assertFalse(renamed.exists())

    def test_legacy_and_padded_receipt_lookup_is_compatible_and_ambiguous_safe(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            first_student = insert_student(
                app, class_id=class_id, admission_number="H1520", roll_number=1
            )
            first_fee = insert_monthly_fee(
                app, student_id=first_student, class_id=class_id,
                session_year="2026-2027", month_name="July",
                month_number=7, year=2026, amount=1000,
            )
            first_payment = app.fee_service.process_payment(first_student, [first_fee])
            self.assertEqual(
                app.fee_service.get_completed_payment_by_receipt_number(1)["receipt_no"],
                first_payment["receipt_no"],
            )

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE fee_payments SET receipt_no='1' WHERE id=?",
                    (first_payment["payment_id"],),
                )
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                app.fee_service.get_completed_payment_by_receipt_number("1")["receipt_no"],
                "1",
            )

            second_student = insert_student(
                app, class_id=class_id, admission_number="H1521", roll_number=2,
                name="SECOND STUDENT",
            )
            second_fee = insert_monthly_fee(
                app, student_id=second_student, class_id=class_id,
                session_year="2026-2027", month_name="July",
                month_number=7, year=2026, amount=1200,
            )
            second_payment = app.fee_service.process_payment(second_student, [second_fee])
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE fee_payments SET receipt_no='000001' WHERE id=?",
                    (second_payment["payment_id"],),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(ValueError, "Manual reconciliation"):
                app.fee_service.get_completed_payment_by_receipt_number("1")

    def test_edit_student_duplicate_identity_is_transactional_and_concurrent_safe(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            first = insert_student(
                app, class_id=class_id, admission_number="H1530", roll_number=1,
                name="FIRST STUDENT", father_name="FIRST FATHER",
                father_cnic="1234512345671",
            )
            second = insert_student(
                app, class_id=class_id, admission_number="H1531", roll_number=2,
                name="SECOND STUDENT", father_name="SECOND FATHER",
                father_cnic="9876598765431",
            )
            before = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (second,)))
            with self.assertRaisesRegex(ValueError, "same Student Name"):
                app.student_service.update_student(
                    **update_values(
                        app, second,
                        student_name="FIRST STUDENT",
                        father_name="FIRST FATHER",
                        father_cnic="1234512345671",
                    )
                )
            self.assertEqual(
                dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (second,))),
                before,
            )

            third = insert_student(
                app, class_id=class_id, admission_number="H1532", roll_number=3,
                name="THIRD STUDENT", father_name="THIRD FATHER",
                father_cnic="1357913579135",
            )
            target = {
                "student_name": "COMMON STUDENT",
                "father_name": "COMMON FATHER",
                "father_cnic": "2468024680246",
            }
            def edit(student_id):
                return app.student_service.update_student(
                    **update_values(app, student_id, **target)
                )

            outcomes = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(edit, second), pool.submit(edit, third)]
                for future in futures:
                    try:
                        future.result()
                        outcomes.append("ok")
                    except ValueError:
                        outcomes.append("duplicate")
            self.assertEqual(sorted(outcomes), ["duplicate", "ok"])
            count = fetch_one(
                app,
                "SELECT COUNT(*) AS n FROM students WHERE student_name=? "
                "AND father_name=? AND father_cnic=?",
                ("COMMON STUDENT", "COMMON FATHER", "2468024680246"),
            )["n"]
            self.assertEqual(int(count), 1)

    def test_fee_reports_are_read_only_and_receipt_filename_is_unique(self):
        with IsolatedApplication() as app, tempfile.TemporaryDirectory() as temp_dir:
            class_id = insert_class(app, "1", "A")
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1540", roll_number=1
            )
            before_students = [dict(row) for row in fetch_all(app, "SELECT * FROM students")]
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records")["n"]),
                0,
            )
            class_rows = app.fee_service.get_class_fee_status_rows(class_id)
            school_rows = app.fee_service.get_full_school_fee_status_rows()
            self.assertEqual(len(class_rows), 1)
            self.assertEqual(len(school_rows), 1)
            self.assertTrue(class_rows[0]["report_read_only"])
            self.assertGreater(class_rows[0]["simulated_missing_count"], 0)
            self.assertGreater(len(class_rows[0]["unpaid_months"]), 0)
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records")["n"]),
                0,
            )
            self.assertEqual(
                [dict(row) for row in fetch_all(app, "SELECT * FROM students")],
                before_students,
            )

            filename_service = __import__(
                "app.services.receipt_filename_service",
                fromlist=["build_paid_receipt_default_filename"],
            )
            default_name = filename_service.build_paid_receipt_default_filename(
                {
                    "receipt_no": "125",
                    "tracking_id": "HMS-000045",
                    "payment_date": "29-07-2026",
                }
            )
            self.assertEqual(
                default_name,
                "Fee_Receipt_125_HMS-000045_2026-07-29.pdf",
            )
            existing = Path(temp_dir) / default_name
            existing.write_bytes(b"old")
            safe_path = filename_service.make_duplicate_safe_pdf_path(str(existing))
            self.assertTrue(safe_path.endswith("_2.pdf"))
            receipt_source = (PROJECT_ROOT / "app/modules/fee_receipt.py").read_text(encoding="utf-8")
            self.assertIn("build_paid_receipt_default_filename(data)", receipt_source)
            self.assertIn("make_duplicate_safe_pdf_path(file_path)", receipt_source)
            self.assertEqual(receipt_source.count('>Receipt No.</td>'), 3)
