from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class FixedJulyDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 12, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


def add_arguments(class_id: int, admission_number: str, roll_number: int, **overrides):
    values = {
        "admission_number": admission_number,
        "roll_number": roll_number,
        "class_id": class_id,
        "student_name": "ALI KHAN",
        "father_name": "AHMAD KHAN",
        "father_cnic": "12345-1234567-1",
        "father_contact_number": "+920000000000",
        "school_leaving_certificate_submitted": True,
        "admission_year": 2026,
        "admission_month": 4,
        "admission_day": 1,
        "admission_time": "09:00:00",
        "gender": "Male",
        "date_of_birth": "01-01-2015",
        "created_by": "v14-regression",
    }
    values.update(overrides)
    return values


def update_from_row(app, student_id: int, **overrides):
    row = app.student_service.get_student_by_id(student_id)
    values = {
        "student_id": int(student_id),
        "admission_number": row["admission_number"],
        "roll_number": int(row["roll_number"]),
        "class_id": int(row["class_id"]),
        "student_name": row["student_name"],
        "father_name": row["father_name"],
        "father_cnic": row["father_cnic"] or "",
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
        "date_of_birth": row["date_of_birth"] or "2015-01-01",
        "form_b_submitted": bool(row["form_b_submitted"]),
        "birth_certificate_submitted": bool(row["birth_certificate_submitted"]),
        "updated_by": "v14-regression",
    }
    values.update(overrides)
    return app.student_service.update_student(**values)


def set_april_fee(app, class_id: int, amount: float = 1000.0):
    app.fee_service.set_class_fee(
        class_id=class_id,
        monthly_fee=amount,
        effective_from_month=4,
        effective_from_year=2026,
        created_by="v14-regression",
    )


class AdditionalBugFixV14ScopeTests(unittest.TestCase):
    def test_father_cnic_is_mandatory_normalized_and_siblings_are_allowed(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            for invalid in ("", "123", "123451234567A", "12345-123456-1"):
                with self.assertRaisesRegex(ValueError, "Father CNIC"):
                    app.student_service.add_student(
                        **add_arguments(class_id, f"H{100 + len(invalid)}", 1, father_cnic=invalid)
                    )

            first = app.student_service.add_student(
                **add_arguments(class_id, "H1401", 1)
            )
            stored = fetch_one(
                app,
                "SELECT father_cnic FROM students WHERE student_id=?",
                (first,),
            )
            self.assertEqual(stored["father_cnic"], "1234512345671")

            sibling = app.student_service.add_student(
                **add_arguments(
                    class_id,
                    "H1402",
                    2,
                    student_name="BILAL KHAN",
                    father_cnic="1234512345671",
                )
            )
            self.assertGreater(sibling, first)

            with self.assertRaisesRegex(ValueError, "Student Record Already Exists"):
                app.student_service.add_student(
                    **add_arguments(
                        class_id,
                        "H1403",
                        3,
                        student_name=" Ali   Khan ",
                        father_name=" Ahmad  Khan ",
                        father_cnic="12345 1234567 1",
                    )
                )

    def test_concurrent_duplicate_admission_allows_one_and_blank_legacy_cnic_blocks_edit(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            barrier = threading.Barrier(2)
            outcomes = []
            lock = threading.Lock()

            def admit(admission_number, roll_number):
                try:
                    barrier.wait(timeout=5)
                    student_id = app.student_service.add_student(
                        **add_arguments(
                            class_id,
                            admission_number,
                            roll_number,
                            student_name=" Ali   Khan ",
                            father_name=" Ahmad Khan ",
                            father_cnic="1234512345671",
                        )
                    )
                    result = ("ok", student_id)
                except Exception as error:
                    result = ("error", str(error))
                with lock:
                    outcomes.append(result)

            threads = [
                threading.Thread(target=admit, args=("H1410", 1)),
                threading.Thread(target=admit, args=("H1411", 2)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertEqual(sum(1 for status, _ in outcomes if status == "ok"), 1)
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]),
                1,
            )

            legacy_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H1412",
                roll_number=3,
                name="LEGACY STUDENT",
                father_name="LEGACY FATHER",
                father_cnic="",
            )
            before = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (legacy_id,)))
            with self.assertRaisesRegex(ValueError, "Father CNIC is required"):
                update_from_row(app, legacy_id, current_address="SHOULD NOT SAVE")
            after = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (legacy_id,)))
            self.assertEqual(after, before)

    def test_manual_class_history_is_chronological_continuous_and_same_month_append_only(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_1b = insert_class(app, "1", "B")
            class_1c = insert_class(app, "1", "C")
            class_2a = insert_class(app, "2", "A")
            for class_id in (class_1a, class_1b, class_1c, class_2a):
                set_april_fee(app, class_id)
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="H1420",
                roll_number=1,
                father_cnic="1234512345671",
            )

            with mock.patch.object(app.student_service, "datetime", FixedJulyDateTime):
                with self.assertRaisesRegex(ValueError, "earlier than the Admission Month"):
                    update_from_row(
                        app,
                        student_id,
                        class_id=class_1b,
                        effective_month=3,
                        effective_year=2026,
                    )
                update_from_row(
                    app,
                    student_id,
                    class_id=class_1c,
                    effective_month=5,
                    effective_year=2026,
                )
                update_from_row(
                    app,
                    student_id,
                    class_id=class_1b,
                    effective_month=6,
                    effective_year=2026,
                )
                with self.assertRaisesRegex(ValueError, "latest existing"):
                    update_from_row(
                        app,
                        student_id,
                        class_id=class_1a,
                        effective_month=5,
                        effective_year=2026,
                    )
                update_from_row(
                    app,
                    student_id,
                    class_id=class_2a,
                    effective_month=7,
                    effective_year=2026,
                )
                update_from_row(
                    app,
                    student_id,
                    class_id=class_1c,
                    effective_month=7,
                    effective_year=2026,
                )

            history = fetch_all(
                app,
                """
                SELECT old_class_id, new_class_id, effective_month, effective_year
                FROM student_class_history
                WHERE student_id=?
                ORDER BY effective_year, effective_month, history_id
                """,
                (student_id,),
            )
            transitions = [
                (row["old_class_id"], row["new_class_id"], row["effective_month"], row["effective_year"])
                for row in history
            ]
            self.assertIn((class_1a, class_1c, 5, 2026), transitions)
            self.assertIn((class_1c, class_1b, 6, 2026), transitions)
            self.assertIn((class_1b, class_2a, 7, 2026), transitions)
            self.assertIn((class_2a, class_1c, 7, 2026), transitions)
            self.assertEqual(
                int(fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (student_id,))["class_id"]),
                class_1c,
            )

    def test_duplicate_fee_repair_deletes_only_exact_copies_and_preserves_conflicts(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="H1430",
                roll_number=1,
            )
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_student_monthly_fee_period_unique")
                for _ in range(2):
                    connection.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id, class_id, session_year, fee_type,
                            month_name, month_number, year, amount, status,
                            paid_date, created_at, updated_at
                        ) VALUES (?, ?, '2026-2027', 'Monthly', 'July', 7, 2026,
                                  1000, 'Unpaid', NULL, '2026-07-01', '2026-07-01')
                        """,
                        (student_id, class_1a),
                    )
                deleted = app.database._repair_duplicate_monthly_fee_records(connection)
                self.assertEqual(deleted, 1)
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records")["n"]),
                1,
            )

            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id, class_id, session_year, fee_type,
                        month_name, month_number, year, amount, status,
                        paid_date, created_at, updated_at
                    ) VALUES (?, ?, '2026-2027', 'Monthly', 'August', 8, 2026,
                              1000, 'Unpaid', NULL, '2026-08-01', '2026-08-01')
                    """,
                    (student_id, class_1a),
                )
                connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id, class_id, session_year, fee_type,
                        month_name, month_number, year, amount, status,
                        paid_date, created_at, updated_at
                    ) VALUES (?, ?, '2026-2027', 'Monthly', 'August', 8, 2026,
                              1500, 'Unpaid', NULL, '2026-08-01', '2026-08-01')
                    """,
                    (student_id, class_2a),
                )
                with self.assertRaisesRegex(RuntimeError, "conflicting duplicate"):
                    app.database._repair_duplicate_monthly_fee_records(connection)
                count = connection.execute(
                    "SELECT COUNT(*) FROM student_fee_records WHERE month_number=8 AND year=2026"
                ).fetchone()[0]
                self.assertEqual(int(count), 2)
                connection.rollback()
            finally:
                connection.close()

    def test_backup_boolean_parser_and_timestamp_retention_are_fail_closed(self):
        with IsolatedApplication() as app:
            settings_module = __import__(
                "app.services.backup_settings_service",
                fromlist=["normalise_backup_settings"],
            )
            settings = settings_module.normalise_backup_settings({
                "internal_enabled": "false",
                "selected_folder_enabled": "0",
                "google_drive_enabled": "yes",
            })
            self.assertFalse(settings["internal_enabled"])
            self.assertFalse(settings["selected_folder_enabled"])
            self.assertTrue(settings["google_drive_enabled"])
            with self.assertLogs(settings_module.__name__, level="WARNING"):
                unknown = settings_module.normalise_backup_settings({
                    "internal_enabled": "corrupt-value"
                })
            self.assertTrue(unknown["internal_enabled"])

            with tempfile.TemporaryDirectory(prefix="hms_retention_") as folder:
                extension = app.backup_service.BACKUP_FILE_EXTENSION
                prefix = app.backup_service.INTERNAL_BACKUP_PREFIX
                old_path = Path(folder) / f"{prefix}_2026-07-28_23-59-59-000001{extension}"
                new_path = Path(folder) / f"{prefix}_2026-07-29_00-00-01-000001{extension}"
                unknown_path = Path(folder) / "do_not_delete.txt"
                old_path.write_bytes(b"old")
                new_path.write_bytes(b"new")
                unknown_path.write_text("keep", encoding="utf-8")
                future_mtime = time.time() + 10000
                os.utime(old_path, (future_mtime, future_mtime))
                result = app.backup_service.apply_backup_retention(
                    folder,
                    prefix,
                    1,
                    protected_backup_path=str(new_path),
                )
                self.assertTrue(result["success"])
                self.assertFalse(old_path.exists())
                self.assertTrue(new_path.exists())
                self.assertTrue(unknown_path.exists())
                self.assertEqual(result["ordered_files"][0], new_path.name)

    def test_lower_class_sections_sort_naturally_and_class4_rules_remain_unchanged(self):
        with IsolatedApplication() as app:
            for section in ("A", "AA", "AB", "B", "Z"):
                insert_class(app, "1", section)
            ordered = [
                row["section"]
                for row in app.class_service.get_classes_by_level("1")
            ]
            self.assertEqual(ordered, ["A", "B", "Z", "AA", "AB"])
            self.assertEqual(app.config.section_rank("A"), 1)
            self.assertEqual(app.config.section_rank("Z"), 26)
            self.assertEqual(app.config.section_rank("AA"), 27)
            self.assertEqual(app.config.section_rank("AB"), 28)
            self.assertTrue(app.class_service.validate_section("A", "4"))
            self.assertFalse(app.class_service.validate_section("AA", "4"))

    def test_archive_is_atomic_blocks_unpaid_and_concurrent_generation_cannot_create_dues(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            set_april_fee(app, class_id, 1000)
            blocked_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H1440",
                roll_number=1,
                status="Dropped",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO student_class_history(
                        student_id, old_class_id, new_class_id,
                        effective_month, effective_year, change_type,
                        change_datetime, changed_by
                    ) VALUES (?, NULL, ?, 3, 2027, 'MANUAL', '2027-03-01', 'test')
                    """,
                    (blocked_id, class_id),
                )
                connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id, class_id, session_year, fee_type,
                        month_name, month_number, year, amount, status,
                        created_at, updated_at
                    ) VALUES (?, ?, '2026-2027', 'Monthly', 'March', 3, 2027,
                              1000, 'Unpaid', '2027-03-01', '2027-03-01')
                    """,
                    (blocked_id, class_id),
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(ValueError, "outstanding fee records"):
                app.student_service.archive_eligible_dropped_students(
                    as_of=datetime(2027, 4, 1, 9, 0, 0),
                    student_ids=[blocked_id],
                )
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (blocked_id,))["student_status"],
                "Dropped",
            )

            clear_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H1441",
                roll_number=2,
                status="Dropped",
            )
            app.fee_service.set_student_fee_override(
                clear_id, 0, 4, 2026, number_of_sessions=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO student_class_history(
                        student_id, old_class_id, new_class_id,
                        effective_month, effective_year, change_type,
                        change_datetime, changed_by
                    ) VALUES (?, NULL, ?, 3, 2027, 'MANUAL', '2027-03-01', 'test')
                    """,
                    (clear_id, class_id),
                )
                connection.commit()
            finally:
                connection.close()

            archive_started = threading.Event()
            original_boundary = app.student_service._archive_drop_boundary

            def paused_boundary(*args, **kwargs):
                archive_started.set()
                time.sleep(0.35)
                return original_boundary(*args, **kwargs)

            errors = []

            def run_archive():
                try:
                    app.student_service.archive_eligible_dropped_students(
                        as_of=datetime(2027, 4, 1, 9, 0, 0),
                        student_ids=[clear_id],
                    )
                except Exception as error:
                    errors.append(error)

            def run_fee_generation():
                archive_started.wait(timeout=5)
                try:
                    app.fee_service.ensure_fee_records_generated(
                        clear_id,
                        as_of=datetime(2027, 4, 1, 9, 0, 0),
                    )
                except Exception as error:
                    errors.append(error)

            with mock.patch.object(
                app.student_service,
                "_archive_drop_boundary",
                side_effect=paused_boundary,
            ):
                archive_thread = threading.Thread(target=run_archive)
                fee_thread = threading.Thread(target=run_fee_generation)
                archive_thread.start()
                fee_thread.start()
                archive_thread.join(timeout=10)
                fee_thread.join(timeout=10)

            self.assertEqual(errors, [])
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (clear_id,))["student_status"],
                "Archived",
            )
            self.assertEqual(
                int(fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=? AND status='Unpaid'",
                    (clear_id,),
                )["n"]),
                0,
            )


if __name__ == "__main__":
    unittest.main()
