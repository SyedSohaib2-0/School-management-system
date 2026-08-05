from __future__ import annotations

from datetime import datetime
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_one,
    insert_class,
    insert_student,
)


class DatabaseMigrationRegressionTests(unittest.TestCase):
    def test_fresh_database_reaches_current_schema_and_is_idempotent(self):
        with IsolatedApplication() as app:
            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                required_tables = {
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type='table'
                        """
                    ).fetchall()
                }
            finally:
                connection.close()

            self.assertEqual(version, app.database.CURRENT_SCHEMA_VERSION)
            self.assertTrue(
                {
                    "students",
                    "classes",
                    "student_fee_records",
                    "promotion_history",
                    "fee_payments",
                    "fee_receipts",
                    "sibling_fee_assignments",
                    "admin_credentials",
                }.issubset(required_tables)
            )

            app.database.initialize_database()
            connection = app.connect()
            try:
                second_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
            finally:
                connection.close()
            self.assertEqual(second_version, app.database.CURRENT_SCHEMA_VERSION)

    def test_version_six_upgrade_assigns_tracking_id_and_preserves_records(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H7",
                roll_number=1,
            )

            connection = app.connect()
            try:
                connection.execute(
                    "DROP INDEX IF EXISTS idx_students_tracking_id_unique"
                )
                connection.execute(
                    "DROP TRIGGER IF EXISTS trg_students_assign_tracking_id"
                )
                connection.execute(
                    "UPDATE students SET tracking_id='' WHERE student_id=?",
                    (student_id,),
                )
                connection.execute("PRAGMA user_version=6")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()

            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                student = connection.execute(
                    """
                    SELECT admission_number, class_id, roll_number, tracking_id
                    FROM students WHERE student_id=?
                    """,
                    (student_id,),
                ).fetchone()
                class_count = int(
                    connection.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
                )
                student_count = int(
                    connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]
                )
                index_exists = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='index' AND name='idx_students_class_roll_unique'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(version, app.database.CURRENT_SCHEMA_VERSION)
            self.assertEqual(student["admission_number"], "H7")
            self.assertEqual(int(student["class_id"]), class_id)
            self.assertEqual(int(student["roll_number"]), 1)
            self.assertEqual(student["tracking_id"], f"HMS-{student_id:06d}")
            self.assertEqual(class_count, 1)
            self.assertEqual(student_count, 1)
            self.assertIsNotNone(index_exists)

    def test_version_twenty_two_upgrade_backfills_promotion_sessions_without_changing_finance(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app, class_id=class_10a, admission_number="H22M", roll_number=1
            )
            app.fee_service.set_class_fee(class_10a, 2000, 4, 2026)
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2027, 3, 20, 9, 0)
            )
            fee_connection = app.connect()
            try:
                fee_ids = [
                    int(row["id"])
                    for row in fee_connection.execute(
                        "SELECT id FROM student_fee_records WHERE student_id=? ORDER BY year,month_number",
                        (student_id,),
                    ).fetchall()
                ]
            finally:
                fee_connection.close()
            payment = app.fee_service.process_payment(student_id, fee_ids)
            app.promotion_full_class_v2.promote_full_class(
                class_10a,
                {student_id: "P"},
                "migration-test",
                as_of=datetime(2027, 3, 25, 10, 0),
            )

            connection = app.connect()
            try:
                before = {
                    "students": int(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]),
                    "fees": int(connection.execute("SELECT COUNT(*) FROM student_fee_records").fetchone()[0]),
                    "payments": int(connection.execute("SELECT COUNT(*) FROM fee_payments").fetchone()[0]),
                    "receipts": int(connection.execute("SELECT COUNT(*) FROM fee_receipts").fetchone()[0]),
                    "receipt_total": float(connection.execute("SELECT COALESCE(SUM(amount),0) FROM fee_receipts").fetchone()[0]),
                }
                for trigger in (
                    "trg_promotion_session_context_insert",
                    "trg_promotion_session_context_update",
                    "trg_promotion_cycle_integrity_insert",
                    "trg_promotion_cycle_integrity_update",
                    "trg_graduation_session_context_insert",
                    "trg_graduation_session_context_update",
                ):
                    connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                connection.execute("DROP INDEX IF EXISTS idx_promotion_batch_class_source_session_unique")
                connection.execute("DROP INDEX IF EXISTS idx_promotion_cycle_class_source_unique")
                connection.execute("DROP INDEX IF EXISTS idx_promotion_cycle_completed_batch_unique")
                connection.execute("DROP TABLE promotion_cycles")
                connection.execute(
                    "UPDATE promotion_batches SET source_session_start_year=NULL,target_session_start_year=NULL,effective_date=NULL,promotion_mode=NULL"
                )
                connection.execute(
                    "UPDATE graduation_records SET completion_session_start_year=NULL,graduation_effective_date=NULL"
                )
                connection.execute("PRAGMA user_version=22")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()

            connection = app.connect()
            try:
                self.assertEqual(int(connection.execute("PRAGMA user_version").fetchone()[0]), 23)
                batch = connection.execute(
                    "SELECT source_session_start_year,target_session_start_year,effective_date,promotion_mode FROM promotion_batches"
                ).fetchone()
                self.assertEqual(tuple(batch), (2026, 2027, "2027-04-01", "NORMAL"))
                graduation = connection.execute(
                    "SELECT completion_session_start_year,graduation_effective_date,status FROM graduation_records WHERE student_id=?",
                    (student_id,),
                ).fetchone()
                self.assertEqual(tuple(graduation), (2026, "2027-04-01", "Graduated"))
                cycle = connection.execute(
                    "SELECT status,source_session_start_year,target_session_start_year,completed_batch_id FROM promotion_cycles"
                ).fetchone()
                self.assertEqual(tuple(cycle)[:3], ("COMPLETED", 2026, 2027))
                self.assertIsNotNone(cycle["completed_batch_id"])
                after = {
                    "students": int(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]),
                    "fees": int(connection.execute("SELECT COUNT(*) FROM student_fee_records").fetchone()[0]),
                    "payments": int(connection.execute("SELECT COUNT(*) FROM fee_payments").fetchone()[0]),
                    "receipts": int(connection.execute("SELECT COUNT(*) FROM fee_receipts").fetchone()[0]),
                    "receipt_total": float(connection.execute("SELECT COALESCE(SUM(amount),0) FROM fee_receipts").fetchone()[0]),
                }
                self.assertEqual(after, before)
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                connection.close()

    def test_version_twelve_upgrade_releases_completed_student_rolls(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            graduated_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H10G",
                roll_number=4,
                status="Graduated",
            )
            partial_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H10P",
                roll_number=7,
                status="Partially Graduated",
            )

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET released_roll_number=NULL WHERE student_id IN (?, ?)",
                    (graduated_id, partial_id),
                )
                connection.execute("PRAGMA user_version=12")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()

            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                rows = connection.execute(
                    """
                    SELECT student_id, roll_number, released_roll_number, student_status
                    FROM students
                    WHERE student_id IN (?, ?)
                    ORDER BY student_id
                    """,
                    (graduated_id, partial_id),
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(version, app.database.CURRENT_SCHEMA_VERSION)
            self.assertEqual(
                [int(row["released_roll_number"]) for row in rows],
                [4, 7],
            )
            self.assertEqual(
                [int(row["roll_number"]) for row in rows],
                [-graduated_id, -partial_id],
            )
            self.assertEqual(
                [row["student_status"] for row in rows],
                ["Graduated", "Partially Graduated"],
            )

    def test_duplicate_rolls_block_migration_twelve_without_data_loss(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            first_student = insert_student(
                app,
                class_id=class_id,
                admission_number="H8",
                roll_number=1,
            )

            connection = app.connect()
            try:
                connection.execute(
                    "DROP INDEX IF EXISTS idx_students_class_roll_unique"
                )
                connection.execute("PRAGMA user_version=11")
                connection.commit()
            finally:
                connection.close()

            second_student = insert_student(
                app,
                class_id=class_id,
                admission_number="H9",
                roll_number=1,
                name="Duplicate Roll",
            )

            with self.assertRaisesRegex(RuntimeError, "duplicate roll numbers"):
                app.database.initialize_database()

            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                rows = connection.execute(
                    """
                    SELECT student_id, admission_number, roll_number
                    FROM students WHERE class_id=? ORDER BY student_id
                    """,
                    (class_id,),
                ).fetchall()
                index_exists = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='index' AND name='idx_students_class_roll_unique'
                    """
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(version, 11)
            self.assertEqual(
                [int(row["student_id"]) for row in rows],
                [first_student, second_student],
            )
            self.assertEqual([int(row["roll_number"]) for row in rows], [1, 1])
            self.assertIsNone(index_exists)

    def test_newer_database_version_is_rejected_without_changes(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            connection = app.connect()
            try:
                connection.execute(
                    f"PRAGMA user_version={app.database.CURRENT_SCHEMA_VERSION + 1}"
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(RuntimeError, "newer version"):
                app.database.initialize_database()

            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                class_name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(version, app.database.CURRENT_SCHEMA_VERSION + 1)
            self.assertEqual(class_name, "1A")


if __name__ == "__main__":
    unittest.main()
