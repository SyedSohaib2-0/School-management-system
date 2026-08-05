from __future__ import annotations

from datetime import datetime, timezone
import importlib
import sqlite3
import unittest

from tests.regression.support import IsolatedApplication, insert_class, insert_student


def insert_monthly_fee(app: IsolatedApplication, *, student_id: int, class_id: int,
                       month_number: int = 7, year: int = 2026,
                       amount: float = 1000.0, fee_type: str = "Monthly") -> int:
    names = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }
    session_start = year if month_number >= 4 else year - 1
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id,class_id,session_year,fee_type,month_name,
                month_number,year,amount,status,paid_date,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,'Unpaid',NULL,
                     '2026-07-01 09:00:00','2026-07-01 09:00:00')
            """,
            (
                int(student_id), int(class_id),
                f"{session_start}-{session_start + 1}", fee_type,
                names[int(month_number)], int(month_number), int(year), float(amount),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def rebuild_students_as_v21(connection: sqlite3.Connection) -> None:
    """Recreate the pre-V22 students table to exercise the real rebuild path."""
    trigger_names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    ]
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for trigger_name in trigger_names:
            escaped = trigger_name.replace('"', '""')
            connection.execute(f'DROP TRIGGER IF EXISTS "{escaped}"')
        connection.execute("DROP TABLE IF EXISTS students_v21")
        connection.execute(
            """
            CREATE TABLE students_v21 (
                student_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_number TEXT NOT NULL UNIQUE,
                tracking_id TEXT,
                roll_number INTEGER NOT NULL,
                released_roll_number INTEGER,
                class_id INTEGER NOT NULL,
                student_name TEXT NOT NULL,
                gender TEXT NOT NULL DEFAULT '',
                father_name TEXT NOT NULL,
                father_cnic TEXT,
                father_contact_number TEXT NOT NULL,
                whatsapp_number TEXT,
                current_address TEXT,
                permanent_address TEXT,
                school_leaving_certificate_submitted INTEGER NOT NULL DEFAULT 0,
                form_b_submitted INTEGER NOT NULL DEFAULT 0,
                birth_certificate_submitted INTEGER NOT NULL DEFAULT 0,
                date_of_birth TEXT,
                admission_date TEXT NOT NULL,
                admission_year INTEGER NOT NULL,
                admission_month INTEGER NOT NULL,
                admission_day INTEGER NOT NULL,
                admission_time TEXT NOT NULL,
                student_status TEXT NOT NULL DEFAULT 'Active'
                    CHECK (student_status IN (
                        'Active','Dropped','Archived','Graduated','Partially Graduated'
                    )),
                failure_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                created_by TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT,
                FOREIGN KEY (class_id) REFERENCES classes (id)
            )
            """
        )
        columns = (
            "student_id,admission_number,tracking_id,roll_number,released_roll_number,"
            "class_id,student_name,gender,father_name,father_cnic,"
            "father_contact_number,whatsapp_number,current_address,permanent_address,"
            "school_leaving_certificate_submitted,form_b_submitted,"
            "birth_certificate_submitted,date_of_birth,admission_date,admission_year,"
            "admission_month,admission_day,admission_time,student_status,failure_count,"
            "created_at,created_by,updated_at,updated_by"
        )
        connection.execute(
            f"INSERT INTO students_v21({columns}) SELECT {columns} FROM students"
        )
        connection.execute("DROP TABLE students")
        connection.execute("ALTER TABLE students_v21 RENAME TO students")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


class MoreStartupMigrationTechnicalHardeningSameV31Tests(unittest.TestCase):
    def test_timezone_aware_student_and_payment_timestamps_survive_startup(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="TZ001", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            payment = app.fee_service.process_payment(student_id, [fee_id])

            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2026-04-01T09:00:00+05:00'
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.execute(
                    """
                    UPDATE fee_payments
                    SET payment_date='2026-07-01T10:00:00+05:00',
                        created_at='2026-07-01T10:00:00+05:00'
                    WHERE id=?
                    """,
                    (int(payment["payment_id"]),),
                )
                connection.commit()
                schema_integrity = importlib.import_module("app.schema_integrity")
                valid, message = schema_integrity.validate_payment_timestamps(connection)
                self.assertTrue(valid, message)
                valid, message = schema_integrity.validate_student_admission_fields(connection)
                self.assertTrue(valid, message)
            finally:
                connection.close()

            status = app.fee_service.get_financial_clock_status(
                as_of=datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
            )
            self.assertIn("clock_rollback_detected", status)
            app.database.initialize_database()

    def test_malformed_cancelled_timestamp_is_rejected_during_startup_validation(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="TZ002", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            payment = app.fee_service.process_payment(student_id, [fee_id])
            app.fee_service.cancel_immediate_payment(int(payment["payment_id"]))

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE fee_payments SET cancelled_at='not-a-timestamp' WHERE id=?",
                    (int(payment["payment_id"]),),
                )
                connection.commit()
                schema_integrity = importlib.import_module("app.schema_integrity")
                valid, message = schema_integrity.validate_payment_timestamps(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("cancelled_at", message)
            with self.assertRaisesRegex(RuntimeError, "cancelled_at"):
                app.database.initialize_database()

    def test_v13_blank_tracking_snapshot_is_repaired_after_canonicalization(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="MIGTRK", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            payment = app.fee_service.process_payment(student_id, [fee_id])

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET tracking_id='' WHERE student_id=?",
                    (student_id,),
                )
                connection.execute(
                    "UPDATE fee_payments SET tracking_id_snapshot='' WHERE id=?",
                    (int(payment["payment_id"]),),
                )
                connection.execute("PRAGMA user_version=13")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()
            connection = app.connect()
            try:
                row = connection.execute(
                    """
                    SELECT students.tracking_id, payments.tracking_id_snapshot
                    FROM students
                    JOIN fee_payments AS payments ON payments.student_id=students.student_id
                    WHERE students.student_id=? AND payments.id=?
                    """,
                    (student_id, int(payment["payment_id"])),
                ).fetchone()
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                connection.close()
            expected = f"HMS-{student_id:06d}"
            self.assertEqual(tuple(row), (expected, expected))
            self.assertEqual(version, 23)

    def test_v13_blank_fee_session_snapshot_is_repaired_after_canonicalization(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="MIGSES", roll_number=1
            )
            fee_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            payment = app.fee_service.process_payment(student_id, [fee_id])

            connection = app.connect()
            try:
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(
                    "UPDATE student_fee_records SET session_year='' WHERE id=?", (fee_id,)
                )
                connection.execute(
                    "UPDATE fee_receipts SET session_year_snapshot='' WHERE payment_id=?",
                    (int(payment["payment_id"]),),
                )
                connection.execute("PRAGMA user_version=13")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()
            connection = app.connect()
            try:
                row = connection.execute(
                    """
                    SELECT records.session_year, receipts.session_year_snapshot
                    FROM student_fee_records AS records
                    JOIN fee_receipts AS receipts
                      ON receipts.student_fee_record_id=records.id
                    WHERE records.id=? AND receipts.payment_id=?
                    """,
                    (fee_id, int(payment["payment_id"])),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(tuple(row), ("2026-2027", "2026-2027"))

    def test_case_variant_exact_duplicate_monthly_rows_are_repaired(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="CASE01", roll_number=1
            )
            first_id = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id
            )
            connection = app.connect()
            try:
                connection.execute("DROP INDEX IF EXISTS idx_student_monthly_fee_period_unique")
                connection.execute("PRAGMA ignore_check_constraints=ON")
                first = connection.execute(
                    "SELECT * FROM student_fee_records WHERE id=?", (first_id,)
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,month_name,
                        month_number,year,amount,status,paid_date,created_at,updated_at
                    ) VALUES(?,?,?,' monthly ',?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(first["student_id"]), int(first["class_id"]),
                        first["session_year"], first["month_name"],
                        int(first["month_number"]), int(first["year"]),
                        float(first["amount"]), first["status"], first["paid_date"],
                        first["created_at"], first["updated_at"],
                    ),
                )
                connection.execute("PRAGMA user_version=19")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()
            connection = app.connect()
            try:
                rows = connection.execute(
                    """
                    SELECT fee_type FROM student_fee_records
                    WHERE student_id=? AND month_number=7 AND year=2026
                    """,
                    (student_id,),
                ).fetchall()
                index_exists = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='index' AND name='idx_student_monthly_fee_period_unique'
                    """
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual([row["fee_type"] for row in rows], ["Monthly"])
            self.assertIsNotNone(index_exists)

    def test_case_variant_conflicting_duplicates_fail_cleanly_and_restore_v19(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="CASE02", roll_number=1
            )
            insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, amount=1000
            )
            connection = app.connect()
            try:
                connection.execute("DROP INDEX IF EXISTS idx_student_monthly_fee_period_unique")
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,month_name,
                        month_number,year,amount,status,paid_date,created_at,updated_at
                    ) VALUES(?,?,'2026-2027',' monthly ','July',7,2026,1500,
                             'Unpaid',NULL,'2026-07-01','2026-07-01')
                    """,
                    (student_id, class_id),
                )
                connection.execute("PRAGMA user_version=19")
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(RuntimeError, "conflicting duplicate Monthly Fee"):
                app.database.initialize_database()

            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                rows = connection.execute(
                    """
                    SELECT fee_type,amount FROM student_fee_records
                    WHERE student_id=? AND month_number=7 AND year=2026
                    ORDER BY id
                    """,
                    (student_id,),
                ).fetchall()
                coming_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(students)")
                }
            finally:
                connection.close()
            self.assertEqual(version, 19)
            self.assertEqual(
                [(row["fee_type"], float(row["amount"])) for row in rows],
                [("Monthly", 1000.0), (" monthly ", 1500.0)],
            )
            self.assertIn("target_session_start_year", coming_columns)

    def test_v21_failure_after_students_rebuild_restores_exact_pre_upgrade_schema(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            insert_student(
                app, class_id=class_id, admission_number="ROLLBK", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    "DROP INDEX IF EXISTS idx_promotion_batch_class_source_session_unique"
                )
                connection.execute("DELETE FROM promotion_cycles")
                for suffix in (1, 2):
                    connection.execute(
                        """
                        INSERT INTO promotion_batches(
                            source_class_id,source_class_name,destination_class_id,
                            destination_class_name,promotion_datetime,
                            source_session_start_year,target_session_start_year,
                            effective_date,promotion_mode,total_students,passed_students,
                            failed_students,graduated_students,created_by,is_reversed
                        ) VALUES(?, '1A', NULL, NULL, ?, NULL, NULL, NULL, NULL,
                                 0,0,0,0,'test',0)
                        """,
                        (class_id, f"2027-03-0{suffix} 10:00:00"),
                    )
                rebuild_students_as_v21(connection)
                connection.execute("PRAGMA user_version=21")
                connection.commit()
                before_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='students'"
                    ).fetchone()[0]
                )
                before_columns = tuple(
                    row[1] for row in connection.execute("PRAGMA table_info(students)")
                )
            finally:
                connection.close()

            with self.assertRaisesRegex(
                RuntimeError,
                "one active promotion batch|Critical index idx_promotion_batch",
            ):
                app.database.initialize_database()

            connection = app.connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                after_sql = str(
                    connection.execute(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='students'"
                    ).fetchone()[0]
                )
                after_columns = tuple(
                    row[1] for row in connection.execute("PRAGMA table_info(students)")
                )
                student_count = int(
                    connection.execute("SELECT COUNT(*) FROM students").fetchone()[0]
                )
            finally:
                connection.close()
            self.assertEqual(version, 21)
            self.assertEqual(after_columns, before_columns)
            self.assertEqual(after_sql, before_sql)
            self.assertNotIn("target_session_start_year", after_columns)
            self.assertEqual(student_count, 1)

    def test_versions_remain_unchanged(self):
        with IsolatedApplication() as app:
            self.assertEqual(app.database.CURRENT_SCHEMA_VERSION, 23)
            config = importlib.import_module("app.config")
            version_text = getattr(config, "APP_VERSION", None) or getattr(
                config, "APPLICATION_VERSION", None
            )
            if version_text is not None:
                self.assertEqual(str(version_text), "1.1.0")


if __name__ == "__main__":
    unittest.main()
