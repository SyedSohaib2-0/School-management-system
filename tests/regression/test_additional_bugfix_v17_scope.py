from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
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


class AdditionalBugFixV17ScopeTests(unittest.TestCase):
    def _copy_database(self, app: IsolatedApplication, name: str) -> Path:
        target = app.root / name
        shutil.copy2(app.database_path, target)
        return target

    @staticmethod
    def _register_fixture_mode(connection: sqlite3.Connection) -> None:
        connection.create_function(
            "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
        )

    def _insert_payment_fixture(
        self,
        connection: sqlite3.Connection,
        *,
        student_id: int,
        fee_record_id: int,
        receipt_no: str,
        payment_total: float,
        receipt_amount: float,
    ) -> int:
        student = connection.execute(
            """
            SELECT students.tracking_id, students.admission_number, students.roll_number,
                   students.student_name, students.father_name, students.father_cnic,
                   students.student_status, classes.class_name
            FROM students
            LEFT JOIN classes ON classes.id=students.class_id
            WHERE students.student_id=?
            """,
            (student_id,),
        ).fetchone()
        fee = connection.execute(
            """
            SELECT records.class_id, classes.class_name, records.session_year,
                   records.month_number, records.year, records.month_name
            FROM student_fee_records AS records
            LEFT JOIN classes ON classes.id=records.class_id
            WHERE records.id=?
            """,
            (fee_record_id,),
        ).fetchone()
        self.assertIsNotNone(student)
        self.assertIsNotNone(fee)
        payment = connection.execute(
            """
            INSERT INTO fee_payments(
                receipt_no,student_id,payment_date,total_amount,received_by,created_at,
                payment_status,tracking_id_snapshot,admission_number_snapshot,
                roll_number_snapshot,student_name_snapshot,father_name_snapshot,
                father_cnic_snapshot,class_name_snapshot,student_status_snapshot
            ) VALUES(?,?, '2026-07-01 10:00:00',?,'test','2026-07-01 10:00:00',
                     'Completed',?,?,?,?,?,?,?,?)
            """,
            (
                receipt_no, student_id, payment_total,
                student[0], student[1], student[2], student[3], student[4], student[5],
                student[7] or "-", student[6],
            ),
        )
        payment_id = int(payment.lastrowid)
        connection.execute(
            """
            INSERT INTO fee_receipts(
                payment_id,student_fee_record_id,item_name,amount,status,created_at,
                class_id_snapshot,class_name_snapshot,session_year_snapshot,
                month_number_snapshot,year_snapshot
            ) VALUES(?,?,?,?,'Paid','2026-07-01 10:00:00',?,?,?,?,?)
            """,
            (
                payment_id, fee_record_id, f"{fee[5]} {fee[4]}", receipt_amount,
                fee[0], fee[1] or "-", fee[2], fee[3], fee[4],
            ),
        )
        return payment_id

    def _insert_monthly_row(
        self,
        app,
        *,
        student_id: int,
        class_id: int,
        month: int,
        year: int,
        amount: float,
        status: str,
    ) -> int:
        session_start = year if month >= 4 else year - 1
        now = "2026-07-29 10:00:00"
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_fee_records(
                    student_id,class_id,session_year,fee_type,month_name,
                    month_number,year,amount,status,paid_date,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?)
                """,
                (
                    student_id,
                    class_id,
                    f"{session_start}-{session_start + 1}",
                    "Monthly",
                    app.fee_service.MONTH_NUMBER_TO_NAME[month],
                    month,
                    year,
                    amount,
                    status,
                    now,
                    now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def test_invalid_tracking_id_search_validates_before_sqlite_and_never_leaks(self):
        with IsolatedApplication() as app:
            connection_calls = []
            original = app.fee_service.get_connection

            def counted_connection():
                connection_calls.append(1)
                return original()

            with mock.patch.object(
                app.fee_service, "get_connection", side_effect=counted_connection
            ):
                for _ in range(25):
                    with self.assertRaisesRegex(ValueError, "Tracking ID"):
                        app.fee_service.search_students_for_fee(
                            tracking_id="not-a-tracking-id"
                        )
            self.assertEqual(connection_calls, [])

    def test_reactivation_repairs_legacy_zero_paid_but_preserves_evidenced_paid(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            app.fee_service.set_class_fee(
                class_id, 1200, 4, 2026, created_by="Alice", change_reason="Initial"
            )
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="RZ001",
                roll_number=1,
                status="Dropped",
            )
            july_id = self._insert_monthly_row(
                app,
                student_id=student_id,
                class_id=class_id,
                month=7,
                year=2026,
                amount=0,
                status="Paid",
            )
            august_id = self._insert_monthly_row(
                app,
                student_id=student_id,
                class_id=class_id,
                month=8,
                year=2026,
                amount=1200,
                status="Paid",
            )
            connection = app.connect()
            try:
                payment = connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no,student_id,payment_date,total_amount,received_by,
                        created_at,payment_status
                    ) VALUES('900001',?,'2026-08-01 10:00:00',1200,'test',
                             '2026-08-01 10:00:00','Completed')
                    """,
                    (student_id,),
                )
                connection.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id,student_fee_record_id,item_name,amount,status,created_at
                    ) VALUES(?,?, 'August 2026',1200,'Paid','2026-08-01 10:00:00')
                    """,
                    (int(payment.lastrowid), august_id),
                )
                connection.commit()
            finally:
                connection.close()

            app.fee_service.generate_reactivation_fee_records(
                student_id, class_id, 7, 2026
            )
            july = fetch_one(
                app,
                "SELECT amount,status,paid_date,class_id FROM student_fee_records WHERE id=?",
                (july_id,),
            )
            august = fetch_one(
                app,
                "SELECT amount,status,paid_date,class_id FROM student_fee_records WHERE id=?",
                (august_id,),
            )
            self.assertEqual((float(july["amount"]), july["status"]), (1200.0, "Unpaid"))
            self.assertIsNone(july["paid_date"])
            self.assertEqual((float(august["amount"]), august["status"]), (1200.0, "Paid"))

            zero_class = insert_class(app, "2", "A")
            app.fee_service.set_class_fee(
                zero_class, 1200, 4, 2026, created_by="Alice", change_reason="Initial"
            )
            zero_student = insert_student(
                app,
                class_id=zero_class,
                admission_number="RZ002",
                roll_number=1,
                status="Dropped",
            )
            zero_row = self._insert_monthly_row(
                app,
                student_id=zero_student,
                class_id=zero_class,
                month=7,
                year=2026,
                amount=0,
                status="Paid",
            )
            app.fee_service.set_student_fee_override(
                zero_student, 0, 4, 2026, number_of_sessions=1
            )
            app.fee_service.generate_reactivation_fee_records(
                zero_student, zero_class, 7, 2026
            )
            repaired = fetch_one(
                app, "SELECT amount,status,paid_date FROM student_fee_records WHERE id=?", (zero_row,)
            )
            self.assertEqual((float(repaired["amount"]), repaired["status"]), (0.0, "Waived"))
            self.assertIsNone(repaired["paid_date"])

    def test_restore_rejects_wrong_indexes_and_missing_foreign_keys(self):
        with IsolatedApplication() as app:
            wrong_index = self._copy_database(app, "wrong-index.db")
            connection = sqlite3.connect(wrong_index)
            try:
                connection.execute("DROP INDEX idx_students_class_roll_unique")
                connection.execute(
                    "CREATE UNIQUE INDEX idx_students_class_roll_unique ON students(student_name)"
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(wrong_index), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("idx_students_class_roll_unique", message)
            self.assertIn("Expected", message)

            missing_fk = self._copy_database(app, "missing-fk.db")
            connection = sqlite3.connect(missing_fk)
            try:
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute("DROP INDEX IF EXISTS idx_fee_receipts_payment_fee_unique")
                connection.execute("ALTER TABLE fee_receipts RENAME TO fee_receipts_old")
                connection.execute(
                    """
                    CREATE TABLE fee_receipts(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        payment_id INTEGER NOT NULL,
                        student_fee_record_id INTEGER NOT NULL,
                        item_name TEXT NOT NULL,
                        amount REAL NOT NULL CHECK(amount>=0),
                        status TEXT NOT NULL CHECK(status IN ('Paid','Cancelled')),
                        created_at TEXT NOT NULL,
                        class_id_snapshot INTEGER,
                        class_name_snapshot TEXT,
                        session_year_snapshot TEXT,
                        month_number_snapshot INTEGER,
                        year_snapshot INTEGER
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO fee_receipts SELECT * FROM fee_receipts_old"
                )
                connection.execute("DROP TABLE fee_receipts_old")
                connection.execute(
                    "CREATE UNIQUE INDEX idx_fee_receipts_payment_fee_unique "
                    "ON fee_receipts(payment_id,student_fee_record_id)"
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(missing_fk), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("foreign-key", message.lower())

    def test_restore_rejects_financial_status_ownership_total_and_duplicate_damage(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            first = insert_student(app, class_id=class_id, admission_number="FI001", roll_number=1)
            second = insert_student(app, class_id=class_id, admission_number="FI002", roll_number=2)
            first_fee = self._insert_monthly_row(
                app, student_id=first, class_id=class_id, month=7, year=2026,
                amount=1000, status="Unpaid"
            )
            second_fee = self._insert_monthly_row(
                app, student_id=second, class_id=class_id, month=7, year=2026,
                amount=1000, status="Unpaid"
            )

            mismatch = self._copy_database(app, "mismatch.db")
            connection = sqlite3.connect(mismatch)
            try:
                self._register_fixture_mode(connection)
                self._insert_payment_fixture(
                    connection, student_id=first, fee_record_id=first_fee,
                    receipt_no="710001", payment_total=9999, receipt_amount=1000,
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(mismatch), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("does not equal", message)

            ownership = self._copy_database(app, "ownership.db")
            connection = sqlite3.connect(ownership)
            try:
                self._register_fixture_mode(connection)
                connection.execute("DROP TRIGGER IF EXISTS trg_fee_receipts_integrity_insert")
                self._insert_payment_fixture(
                    connection, student_id=first, fee_record_id=second_fee,
                    receipt_no="710002", payment_total=1000, receipt_amount=1000,
                )
                from app import schema_integrity
                schema_integrity.install_integrity_triggers(connection)
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(ownership), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("another student", message)

            damaged = self._copy_database(app, "negative-status-duplicate.db")
            connection = sqlite3.connect(damaged)
            try:
                self._register_fixture_mode(connection)
                connection.execute("PRAGMA ignore_check_constraints=ON")
                for trigger in (
                    "trg_fee_settings_nonnegative_insert",
                    "trg_students_status_update",
                ):
                    connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                connection.execute(
                    "INSERT INTO fee_settings(class_id,monthly_fee,session_start_year,effective_from_month,effective_from_year,created_at,updated_at) "
                    "VALUES(?, -1, 2026,4,2026,'2026-04-01','2026-04-01')",
                    (class_id,),
                )
                connection.execute(
                    "UPDATE students SET student_status='InvisibleStatus' WHERE student_id=?",
                    (first,),
                )
                from app import schema_integrity
                schema_integrity.install_integrity_triggers(connection)
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(damaged), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertTrue(
                "financial" in message.lower()
                or "unsupported" in message.lower()
                or "integrity check" in message.lower()
                or "check constraint" in message.lower()
            )

            duplicate = self._copy_database(app, "duplicate-receipt.db")
            connection = sqlite3.connect(duplicate)
            try:
                self._register_fixture_mode(connection)
                connection.execute("DROP INDEX idx_fee_receipts_payment_fee_unique")
                payment_id = self._insert_payment_fixture(
                    connection, student_id=first, fee_record_id=first_fee,
                    receipt_no="710003", payment_total=2000, receipt_amount=1000,
                )
                fee = connection.execute(
                    "SELECT class_id,session_year,month_number,year,month_name FROM student_fee_records WHERE id=?",
                    (first_fee,),
                ).fetchone()
                class_name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?", (fee[0],)
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id,student_fee_record_id,item_name,amount,status,created_at,
                        class_id_snapshot,class_name_snapshot,session_year_snapshot,
                        month_number_snapshot,year_snapshot
                    ) VALUES(?,?,?,1000,'Paid','2026-07-01 10:00:00',?,?,?,?,?)
                    """,
                    (payment_id, first_fee, f"{fee[4]} {fee[3]}", fee[0], class_name, fee[1], fee[2], fee[3]),
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(duplicate), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertTrue(
                "duplicate" in message.lower()
                or "idx_fee_receipts_payment_fee_unique" in message
            )

    def test_startup_repairs_safe_missing_index_and_refuses_conflicting_data(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            insert_student(app, class_id=class_id, admission_number="SR001", roll_number=1)
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_students_class_roll_unique")
                connection.commit()
            finally:
                connection.close()
            app.database.initialize_database()
            index = fetch_one(
                app,
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_students_class_roll_unique'",
            )
            self.assertIsNotNone(index)

            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_students_class_roll_unique")
                connection.commit()
            finally:
                connection.close()
            insert_student(app, class_id=class_id, admission_number="SR002", roll_number=1)
            with self.assertRaises(Exception):
                app.database.initialize_database()
            count = fetch_one(
                app,
                "SELECT COUNT(*) AS n FROM students WHERE class_id=? AND roll_number=1",
                (class_id,),
            )["n"]
            self.assertEqual(int(count), 2)

    def test_current_and_next_scheduled_fee_are_separate(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            app.fee_service.set_class_fee(
                class_id, 1000, 4, 2026, created_by="Alice", change_reason="April fee"
            )
            app.fee_service.set_class_fee(
                class_id, 1500, 9, 2026, created_by="Bob", change_reason="September fee"
            )
            summary = app.fee_service.get_class_fee_schedule_summary(
                class_id, 2026, as_of_datetime=datetime(2026, 7, 29, 10, 0, 0)
            )
            self.assertEqual(float(summary["currently_effective"]["monthly_fee"]), 1000.0)
            self.assertEqual(float(summary["next_scheduled"]["monthly_fee"]), 1500.0)
            self.assertEqual(
                float(app.fee_service.get_class_fee_for_period(class_id, 7, 2026)["monthly_fee"]),
                1000.0,
            )
            self.assertEqual(
                float(app.fee_service.get_class_fee_for_period(class_id, 9, 2026)["monthly_fee"]),
                1500.0,
            )
            ui_source = (
                Path(__file__).resolve().parents[2] / "app/modules/fee_management.py"
            ).read_text(encoding="utf-8")
            self.assertIn("Currently Effective Fee:", ui_source)
            self.assertIn("Next Scheduled Fee:", ui_source)
            self.assertIn("Fee at Selected Period:", ui_source)

    def test_backup_partial_success_crypto_classification_and_async_worker_contract(self):
        with IsolatedApplication() as app:
            password = "V17Backup#2027"
            setup = app.backup_crypto_service.configure_backup_security(password, password)
            self.assertTrue(setup["success"], setup)
            destination = app.root / "backup-destination"
            destination.mkdir()
            old_path = destination / "test_2020-01-01_000000.hmsbackup"
            old_path.write_bytes(b"old")
            original_remove = app.backup_service.os.remove

            def fail_old(path):
                if Path(path) == old_path:
                    raise PermissionError("Permission denied for old backup")
                return original_remove(path)

            with mock.patch.object(app.backup_service.os, "remove", side_effect=fail_old):
                result = app.backup_service.create_backup_in_destination(
                    str(destination), "test", 1, "Unit destination"
                )
            self.assertEqual(result["status"], "partial_success")
            self.assertTrue(result["backup_created"])
            self.assertFalse(result["retention_success"])
            self.assertIn(str(old_path), result["failed_retention_paths"])
            self.assertIn("Permission denied", result["retention_error"])

            truncated = app.root / "truncated.hmsbackup"
            truncated.write_bytes(app.backup_crypto_service.BACKUP_MAGIC + b"\x00")
            inspection = app.backup_crypto_service.inspect_encrypted_backup(str(truncated))
            self.assertEqual(
                inspection["classification"],
                app.backup_crypto_service.BACKUP_INSPECTION_CORRUPTED_HEADER,
            )

            foreign = app.root / "foreign.hmsbackup"
            created = app.backup_crypto_service.create_encrypted_backup_from_database(
                str(app.database_path), str(foreign), "unit"
            )
            self.assertTrue(created["success"], created)
            Path(app.backup_crypto_service.KEY_STORE_PATH).unlink()
            second_password = "DifferentKey#2027"
            configured = app.backup_crypto_service.configure_backup_security(
                second_password, second_password
            )
            self.assertTrue(configured["success"], configured)
            foreign_inspection = app.backup_crypto_service.inspect_encrypted_backup(str(foreign))
            self.assertEqual(
                foreign_inspection["classification"],
                app.backup_crypto_service.BACKUP_INSPECTION_PASSWORD_REQUIRED,
            )
            wrong = app.backup_crypto_service.validate_encrypted_backup_detailed(
                str(foreign), recovery_password="WrongPassword#2027"
            )
            self.assertEqual(
                wrong["classification"],
                app.backup_crypto_service.BACKUP_INSPECTION_AUTHENTICATION_FAILED,
            )

            ui_source = (
                Path(__file__).resolve().parents[2] / "app/modules/backup_management.py"
            ).read_text(encoding="utf-8")
            for marker in ("QRunnable", "QThreadPool", "signals.started", "cancel()", "closeEvent"):
                self.assertIn(marker, ui_source)

    def test_class_and_individual_fee_audit_is_preserved_and_atomic(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            app.fee_service.set_class_fee(
                class_id, 1000, 4, 2026, created_by="Alice", change_reason="Initial April"
            )
            app.fee_service.set_class_fee(
                class_id, 1200, 9, 2026, created_by="Bob", change_reason="September revision"
            )
            april_history = fetch_one(
                app,
                "SELECT id,monthly_fee,created_at,created_by FROM fee_history "
                "WHERE class_id=? AND effective_from_month=4 AND effective_from_year=2026",
                (class_id,),
            )
            self.assertEqual(april_history["created_by"], "Alice")
            preserved = tuple(april_history)
            app.fee_service.set_class_fee(
                class_id, 1300, 9, 2026, created_by="Carol", change_reason="Correction"
            )
            april_after = fetch_one(
                app,
                "SELECT id,monthly_fee,created_at,created_by FROM fee_history WHERE id=?",
                (april_history["id"],),
            )
            self.assertEqual(tuple(april_after), preserved)
            current = fetch_one(
                app,
                "SELECT created_by,updated_by,change_reason FROM fee_settings WHERE class_id=?",
                (class_id,),
            )
            self.assertEqual(current["created_by"], "Bob")
            self.assertEqual(current["updated_by"], "Carol")

            student_id = insert_student(
                app, class_id=class_id, admission_number="AU001", roll_number=1
            )
            unpaid_id = self._insert_monthly_row(
                app, student_id=student_id, class_id=class_id,
                month=9, year=2026, amount=1200, status="Unpaid"
            )
            first_rule = app.fee_service.set_student_fee_override(
                student_id, 50, 9, 2026, created_by="Alice", change_reason="Support"
            )
            second_rule = app.fee_service.set_student_fee_override(
                student_id, 60, 9, 2026, created_by="Bob", change_reason="Correction"
            )
            rules = fetch_all(
                app,
                "SELECT id,monthly_fee_percentage,created_by,superseded_by,superseded_by_record_id "
                "FROM student_fee_override WHERE student_id=? ORDER BY id",
                (student_id,),
            )
            self.assertEqual([float(row["monthly_fee_percentage"]) for row in rules], [50.0, 60.0])
            self.assertEqual(rules[0]["created_by"], "Alice")
            self.assertEqual(rules[0]["superseded_by"], "Bob")
            self.assertEqual(int(rules[0]["superseded_by_record_id"]), second_rule)
            self.assertEqual(rules[1]["created_by"], "Bob")
            recalculated = fetch_one(
                app, "SELECT amount,status FROM student_fee_records WHERE id=?", (unpaid_id,)
            )
            self.assertEqual(recalculated["status"], "Unpaid")
            self.assertEqual(float(recalculated["amount"]), 780.0)

            missing_class = insert_class(app, "2", "A")
            self._insert_monthly_row(
                app, student_id=student_id, class_id=missing_class,
                month=10, year=2026, amount=500, status="Unpaid"
            )
            before = [tuple(row) for row in fetch_all(
                app,
                "SELECT id,is_active,superseded_at,superseded_by_record_id FROM student_fee_override "
                "WHERE student_id=? ORDER BY id",
                (student_id,),
            )]
            with self.assertRaises(ValueError):
                app.fee_service.set_student_fee_override(
                    student_id, 70, 9, 2026, created_by="Dave", change_reason="Must rollback"
                )
            after = [tuple(row) for row in fetch_all(
                app,
                "SELECT id,is_active,superseded_at,superseded_by_record_id FROM student_fee_override "
                "WHERE student_id=? ORDER BY id",
                (student_id,),
            )]
            self.assertEqual(after, before)
            self.assertEqual(first_rule, int(rules[0]["id"]))


if __name__ == "__main__":
    unittest.main()
