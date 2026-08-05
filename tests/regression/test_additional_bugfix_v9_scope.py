from __future__ import annotations

import importlib
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    AllowedPromotionDateTime,
    BlockedPromotionDateTime,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


def update_from_row(app, student_id: int, **overrides):
    row = app.student_service.get_student_by_id(student_id)
    values = {
        "student_id": student_id,
        "admission_number": row["admission_number"],
        "roll_number": int(row["roll_number"]),
        "class_id": int(row["class_id"]),
        "student_name": row["student_name"],
        "gender": row["gender"],
        "father_name": row["father_name"],
        "father_cnic": row["father_cnic"] or "",
        "father_contact_number": row["father_contact_number"],
        "whatsapp_number": row["whatsapp_number"] or "",
        "current_address": row["current_address"] or "Address",
        "permanent_address": row["permanent_address"] or "Address",
        "student_status": row["student_status"],
        "school_leaving_certificate_submitted": bool(
            row["school_leaving_certificate_submitted"]
        ),
        "form_b_submitted": bool(row["form_b_submitted"]),
        "birth_certificate_submitted": bool(row["birth_certificate_submitted"]),
        "date_of_birth": row["date_of_birth"] or "2015-01-01",
        "admission_year": int(row["admission_year"]),
        "admission_month": int(row["admission_month"]),
        "admission_day": int(row["admission_day"]),
        "admission_time": row["admission_time"] or "09:00:00",
        "updated_by": "regression-admin",
    }
    values.update(overrides)
    return app.student_service.update_student(**values)


class AdditionalBugFixV9ScopeTests(unittest.TestCase):
    def test_class10_status_check_is_inside_immediate_transaction(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H901", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 5000, 4, 2026)
            original = app.promotion_full_class_v2._class10_completion_statuses
            observed = {"in_transaction": False, "second_writer_blocked": False}

            def checking_statuses(cursor, connection, *args, **kwargs):
                observed["in_transaction"] = bool(connection.in_transaction)
                other = sqlite3.connect(str(app.database_path), timeout=0)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("BEGIN IMMEDIATE")
                    observed["second_writer_blocked"] = True
                finally:
                    other.close()
                return original(cursor, connection, *args, **kwargs)

            with (
                mock.patch.object(
                    app.promotion_full_class_v2, "datetime", AllowedPromotionDateTime
                ),
                mock.patch.object(app.fee_service, "datetime", AllowedPromotionDateTime),
                mock.patch.object(
                    app.promotion_full_class_v2,
                    "_class10_completion_statuses",
                    side_effect=checking_statuses,
                ),
            ):
                app.promotion_full_class_v2.promote_full_class(
                    class_id, {student_id: "P"}, "regression"
                )

            self.assertTrue(observed["in_transaction"])
            self.assertTrue(observed["second_writer_blocked"])
            row = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(row["student_status"], "Partially Graduated")

    def test_promotion_preview_is_read_only_and_window_is_shared(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H902", roll_number=2
            )
            app.fee_service.set_class_fee(class_id, 5000, 4, 2026)
            before = {
                table: int(fetch_one(app, f"SELECT COUNT(*) AS n FROM {table}")["n"])
                for table in (
                    "student_fee_records",
                    "promotion_batches",
                    "promotion_history",
                    "graduation_records",
                    "student_class_history",
                )
            }
            with (
                mock.patch.object(
                    app.promotion_full_class_v2, "datetime", AllowedPromotionDateTime
                ),
                mock.patch.object(app.fee_service, "datetime", AllowedPromotionDateTime),
            ):
                preview = app.promotion_full_class_v2.preview_full_class_promotion_plan(
                    class_id, {student_id: "P"}
                )
            self.assertTrue(preview["preview_read_only"])
            self.assertEqual(preview["source_fee_rows_would_be_generated"], 12)
            self.assertEqual(
                preview["class10_completion_status_by_student_id"][student_id],
                "Partially Graduated",
            )
            after = {
                table: int(fetch_one(app, f"SELECT COUNT(*) AS n FROM {table}")["n"])
                for table in before
            }
            self.assertEqual(after, before)

            with (
                mock.patch.object(
                    app.promotion_full_class_v2, "datetime", BlockedPromotionDateTime
                ),
                mock.patch.object(app.fee_service, "datetime", BlockedPromotionDateTime),
            ):
                overdue_preview = (
                    app.promotion_full_class_v2.preview_full_class_promotion_plan(
                        class_id, {student_id: "P"}
                    )
                )
                self.assertEqual(overdue_preview["promotion_mode"], "OVERDUE")
                self.assertEqual(overdue_preview["effective_date"], "2027-04-01")
                result = app.promotion_full_class_v2.promote_full_class(
                    class_id, {student_id: "P"}
                )
                self.assertEqual(result["promotion_mode"], "OVERDUE")

    def test_enabled_backup_failure_is_not_skipped_or_successful(self):
        with IsolatedApplication() as app:
            setup = app.backup_crypto_service.configure_backup_security(
                "Backup#V9Password", "Backup#V9Password"
            )
            self.assertTrue(setup["success"], setup)
            invalid_destination = app.root / "destination-is-a-file"
            invalid_destination.write_text("not a directory", encoding="utf-8")
            settings = {
                "internal_enabled": False,
                "selected_folder_enabled": True,
                "selected_folder_path": str(invalid_destination),
                "selected_folder_retention": 10,
                "google_drive_enabled": False,
            }
            with mock.patch.object(
                app.backup_service, "load_backup_settings", return_value=settings
            ):
                result = app.backup_service.run_automatic_backups()
            self.assertFalse(result["success"])
            self.assertEqual(result["failed_count"], 1)
            self.assertEqual(result["successful_count"], 0)
            failed = [item for item in result["results"] if not item["skipped"]]
            self.assertEqual(len(failed), 1)
            self.assertIn("Selected-folder", failed[0]["destination"])

    def test_shutdown_backup_wait_is_bounded(self):
        with IsolatedApplication():
            scheduler = importlib.import_module("app.services.backup_scheduler")
            with scheduler._scheduler_lock:
                scheduler._backup_running = True
                scheduler._backup_pending = False
                scheduler._scheduler_stopping = False
                scheduler._backup_interruption_requested = False
            started = time.monotonic()
            result = scheduler.complete_pending_backups_before_exit(0.05)
            elapsed = time.monotonic() - started
            self.assertFalse(result["success"])
            self.assertTrue(result["timed_out"])
            self.assertLess(elapsed, 1.0)
            status = scheduler.get_backup_scheduler_status()
            self.assertTrue(status["interruption_requested"])
            with scheduler._scheduler_lock:
                scheduler._backup_running = False
                scheduler._backup_pending = False

    def test_restore_symlink_escape_is_rejected(self):
        with IsolatedApplication() as app:
            approved = Path(app.backup_service.INTERNAL_BACKUP_DIRECTORY)
            approved.mkdir(parents=True, exist_ok=True)
            outside = app.root / "outside.db"
            outside.write_bytes(b"outside")
            link = approved / "linked.db"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("Symbolic links are unavailable in this environment.")
            valid, message, _ = app.backup_service.validate_restore_source_path(str(link))
            self.assertFalse(valid)
            self.assertIn("symbolic-link/junction", message)

    def test_released_roll_search_safe_edit_and_admission_audit(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="261174",
                roll_number=1,
                name="Legacy Student",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET roll_number=-1, released_roll_number=1,
                        student_status='Graduated'
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            self.assertEqual(
                [row["student_id"] for row in app.student_service.search_students(roll_number=1)],
                [student_id],
            )
            self.assertEqual(
                [row["student_id"] for row in app.student_service.search_students(roll_number="1*")],
                [student_id],
            )
            with self.assertRaises(ValueError):
                app.student_service.search_students(roll_number=-1)

            update_from_row(
                app,
                student_id,
                roll_number=1,
                student_name="Edited Legacy Student",
                current_address="New Address",
            )
            row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(int(row["roll_number"]), -1)
            self.assertEqual(int(row["released_roll_number"]), 1)
            self.assertEqual(row["admission_number"], "261174")
            self.assertEqual(row["student_name"], "EDITED LEGACY STUDENT")
            self.assertEqual(fetch_all(app, "SELECT * FROM admission_number_history"), [])

            with self.assertRaisesRegex(ValueError, "permanent"):
                update_from_row(
                    app, student_id, roll_number=1, admission_number="H61174"
                )
            self.assertEqual(
                fetch_all(app, "SELECT * FROM admission_number_history"), []
            )

            other_id = insert_student(
                app, class_id=class_id, admission_number="H700", roll_number=2
            )
            with self.assertRaisesRegex(ValueError, "permanent"):
                update_from_row(app, student_id, roll_number=1, admission_number="H700")
            self.assertIsNotNone(other_id)

    def test_password_change_is_one_conditional_write_transaction(self):
        with IsolatedApplication() as app:
            auth = importlib.import_module("app.services.auth_service")

            class Result:
                def __init__(self, row=None, rowcount=-1):
                    self._row = row
                    self.rowcount = rowcount
                def fetchone(self):
                    return self._row

            class FakeConnection:
                def __init__(self):
                    self.sql = []
                    self.rolled_back = False
                    self.closed = False
                def execute(self, sql, params=()):
                    normalized = " ".join(sql.split())
                    self.sql.append((normalized, params))
                    if normalized == "BEGIN IMMEDIATE;":
                        return Result()
                    if normalized.startswith("SELECT password_digest"):
                        return Result({"password_digest": "old-digest"})
                    if normalized.startswith("UPDATE admin_credentials"):
                        self.assert_conditional = "AND password_digest = ?" in normalized
                        return Result(rowcount=0)
                    raise AssertionError(normalized)
                def rollback(self):
                    self.rolled_back = True
                def commit(self):
                    raise AssertionError("Conflict must not commit")
                def close(self):
                    self.closed = True

            fake = FakeConnection()
            with (
                mock.patch.object(auth, "get_connection", return_value=fake),
                mock.patch.object(auth, "_verify_secret", return_value=True),
                mock.patch.object(auth, "_hash_secret", return_value="new-digest"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Authentication conflict"):
                    auth.change_admin_password("admin", "oldpass", "newpass")
            self.assertEqual(fake.sql[0][0], "BEGIN IMMEDIATE;")
            self.assertTrue(fake.assert_conditional)
            self.assertTrue(fake.rolled_back)
            self.assertTrue(fake.closed)

    def test_operational_class_delete_is_blocked_historical_class_inactivates_and_never_used_deletes(self):
        with IsolatedApplication() as app:
            used = insert_class(app, "3", "A")
            never_used = insert_class(app, "3", "B")
            student_id = insert_student(
                app, class_id=used, admission_number="H903", roll_number=1
            )

            with self.assertRaisesRegex(ValueError, "cannot be deleted or made Inactive"):
                app.class_service.delete_class(used)
            self.assertTrue(bool(fetch_one(
                app, "SELECT is_active FROM classes WHERE id=?", (used,)
            )["is_active"]))

            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET student_status='Dropped', released_roll_number=roll_number,
                        roll_number=-student_id
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            result = app.class_service.delete_class(used)
            self.assertEqual(result["action"], "inactivated")
            self.assertFalse(bool(fetch_one(app, "SELECT is_active FROM classes WHERE id=?", (used,))["is_active"]))
            self.assertNotIn(used, [int(row["id"]) for row in app.class_service.get_all_classes()])
            self.assertIn(
                used,
                [int(row["id"]) for row in app.class_service.get_all_classes(include_inactive=True)],
            )
            activated = app.class_service.set_class_active(used, True)
            self.assertTrue(activated["is_active"])

            deleted = app.class_service.delete_class(never_used)
            self.assertEqual(deleted["action"], "deleted")
            self.assertIsNone(app.class_service.get_class_by_id(never_used))

    def test_manual_class_changes_are_year_round_and_inactive_destination_is_blocked(self):
        with IsolatedApplication() as app:
            class_3a = insert_class(app, "3", "A")
            class_3b = insert_class(app, "3", "B")
            class_4a = insert_class(app, "4", "A")
            student_id = insert_student(
                app,
                class_id=class_3a,
                admission_number="H904",
                roll_number=1,
                gender="MALE",
            )
            for class_id in (class_3a, class_3b, class_4a):
                app.fee_service.set_class_fee(class_id, 2000 + class_id, 4, 2026)
            app.fee_service.ensure_fee_records_generated(student_id)

            update_from_row(
                app,
                student_id,
                class_id=class_4a,
                effective_month=7,
                effective_year=2026,
            )
            moved = fetch_one(app, "SELECT class_id, roll_number FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(int(moved["class_id"]), class_4a)
            self.assertGreater(int(moved["roll_number"]), 0)

            update_from_row(
                app,
                student_id,
                class_id=class_3b,
                effective_month=7,
                effective_year=2026,
            )
            moved_again = fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(int(moved_again["class_id"]), class_3b)
            history = fetch_all(
                app,
                "SELECT old_class_id, new_class_id FROM student_class_history "
                "WHERE student_id=? AND change_type='MANUAL' ORDER BY history_id",
                (student_id,),
            )
            self.assertEqual(len(history), 2)

            app.class_service.set_class_active(class_4a, False)
            with self.assertRaisesRegex(ValueError, "Inactive"):
                update_from_row(
                    app,
                    student_id,
                    class_id=class_4a,
                    effective_month=7,
                    effective_year=2026,
                )

            rows = fetch_all(
                app,
                "SELECT class_id, month_number, year, status FROM student_fee_records "
                "WHERE student_id=? AND fee_type='Monthly' ORDER BY year, month_number",
                (student_id,),
            )
            for row in rows:
                if (int(row["year"]), int(row["month_number"])) < (2026, 7):
                    self.assertEqual(int(row["class_id"]), class_3a)


if __name__ == "__main__":
    unittest.main()
