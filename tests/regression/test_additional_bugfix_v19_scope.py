from __future__ import annotations

from datetime import datetime as RealDateTime
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


class March2027DateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 20, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class AdditionalBugFixV19ScopeTests(unittest.TestCase):
    def _copy_database(self, app: IsolatedApplication, name: str) -> Path:
        target = app.root / name
        shutil.copy2(app.database_path, target)
        return target

    @staticmethod
    def _fixture_connection(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.create_function(
            "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
        )
        return connection

    def _validate_corruption(self, app, name, mutator, expected_text):
        candidate = self._copy_database(app, name)
        connection = self._fixture_connection(candidate)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            mutator(connection)
            connection.commit()
        finally:
            connection.close()
        valid, message = app.backup_service.validate_school_database_schema(
            str(candidate), require_current_schema=True
        )
        self.assertFalse(valid, message)
        self.assertIn(expected_text.lower(), message.lower())

    def _insert_monthly_fee(
        self,
        app,
        *,
        student_id: int,
        class_id: int,
        month: int = 7,
        year: int = 2026,
        amount: float = 1000,
        status: str = "Unpaid",
    ) -> int:
        session_start = year if month >= 4 else year - 1
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_fee_records(
                    student_id,class_id,session_year,fee_type,month_name,
                    month_number,year,amount,status,paid_date,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,'2026-07-01 10:00:00','2026-07-01 10:00:00')
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
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    @staticmethod
    def _student_snapshots(connection, student_id: int):
        return connection.execute(
            """
            SELECT students.tracking_id,students.admission_number,students.roll_number,
                   students.student_name,students.father_name,students.father_cnic,
                   classes.class_name,students.student_status
            FROM students LEFT JOIN classes ON classes.id=students.class_id
            WHERE students.student_id=?
            """,
            (student_id,),
        ).fetchone()

    def test_backup_creation_requires_full_current_schema_before_and_after_encryption(self):
        with IsolatedApplication() as app:
            password = "V19Backup#2027"
            configured = app.backup_crypto_service.configure_backup_security(
                password, password
            )
            self.assertTrue(configured["success"], configured)
            destination = app.root / "verified-backups"
            destination.mkdir()

            original = app.backup_service.validate_school_database_schema
            calls = []

            def spy(path, require_current_schema=False):
                calls.append((str(path), bool(require_current_schema)))
                return original(path, require_current_schema=require_current_schema)

            with mock.patch.object(
                app.backup_service,
                "validate_school_database_schema",
                side_effect=spy,
            ):
                result = app.backup_service.create_backup_in_destination(
                    str(destination), "v19", 2, "V19 test backup"
                )
            self.assertTrue(result["backup_created"], result)
            self.assertTrue(result["success"], result)
            self.assertGreaterEqual(len(calls), 2)
            self.assertTrue(all(require_current for _, require_current in calls))

            validation_calls = 0

            def reject_decrypted(path, require_current_schema=False):
                nonlocal validation_calls
                validation_calls += 1
                if validation_calls >= 2:
                    return False, "Current-schema validation deliberately failed."
                return original(path, require_current_schema=require_current_schema)

            with mock.patch.object(
                app.backup_service,
                "validate_school_database_schema",
                side_effect=reject_decrypted,
            ):
                failed = app.backup_service.create_backup_in_destination(
                    str(destination), "v19_bad", 2, "V19 damaged verification"
                )
            self.assertFalse(failed["success"])
            self.assertIsNone(failed["backup_path"])
            self.assertIn("current-schema", failed["message"].lower())

    def test_strict_paid_completed_and_numeric_receipt_rules(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="PAY001", roll_number=1
            )
            fee_id = self._insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, amount=1000
            )

            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "Pay Fee"):
                    strict.execute(
                        "UPDATE student_fee_records SET status='Paid' WHERE id=?",
                        (fee_id,),
                    )
                strict.rollback()

                snapshots = self._student_snapshots(strict, student_id)
                with self.assertRaisesRegex(sqlite3.IntegrityError, "digits only"):
                    strict.execute(
                        """
                        INSERT INTO fee_payments(
                            receipt_no,student_id,payment_date,total_amount,received_by,
                            created_at,payment_status,tracking_id_snapshot,
                            admission_number_snapshot,roll_number_snapshot,
                            student_name_snapshot,father_name_snapshot,father_cnic_snapshot,
                            class_name_snapshot,student_status_snapshot
                        ) VALUES('ABC',?,'2026-07-01 10:00:00',1000,
                                 '__HMS_PAYMENT_PENDING__','2026-07-01 10:00:00',
                                 'Completed',?,?,?,?,?,?,?,?)
                        """,
                        (student_id, *snapshots),
                    )
                strict.rollback()

                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "through Pay Fee"
                ):
                    strict.execute(
                        """
                        INSERT INTO fee_payments(
                            receipt_no,student_id,payment_date,total_amount,received_by,
                            created_at,payment_status,tracking_id_snapshot,
                            admission_number_snapshot,roll_number_snapshot,
                            student_name_snapshot,father_name_snapshot,father_cnic_snapshot,
                            class_name_snapshot,student_status_snapshot
                        ) VALUES('810001',?,'2026-07-01 10:00:00',1000,'admin',
                                 '2026-07-01 10:00:00','Completed',?,?,?,?,?,?,?,?)
                        """,
                        (student_id, *snapshots),
                    )
                strict.rollback()
            finally:
                strict.close()

            with self.assertRaisesRegex(ValueError, "No fee items"):
                app.fee_service.process_payment(student_id, [])

            payment = app.fee_service.process_payment(student_id, [fee_id])
            self.assertRegex(str(payment["receipt_no"]), r"^\d+$")
            stored_payment = fetch_one(
                app,
                "SELECT payment_status,total_amount FROM fee_payments WHERE id=?",
                (payment["payment_id"],),
            )
            receipt_items = fetch_all(
                app,
                "SELECT student_fee_record_id,status FROM fee_receipts WHERE payment_id=?",
                (payment["payment_id"],),
            )
            paid_fee = fetch_one(
                app, "SELECT status,paid_date FROM student_fee_records WHERE id=?", (fee_id,)
            )
            self.assertEqual(stored_payment["payment_status"], "Completed")
            self.assertEqual(len(receipt_items), 1)
            self.assertEqual(int(receipt_items[0]["student_fee_record_id"]), fee_id)
            self.assertEqual(receipt_items[0]["status"], "Paid")
            self.assertEqual(paid_fee["status"], "Paid")
            self.assertIsNotNone(paid_fee["paid_date"])

    def test_cancellation_and_snapshot_integrity_are_consistent(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="CAN001", roll_number=1
            )
            fee_id = self._insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, amount=1000
            )
            payment = app.fee_service.process_payment(student_id, [fee_id])
            cancelled = app.fee_service.cancel_immediate_payment(payment["payment_id"])
            self.assertEqual(cancelled["payment_status"], "Cancelled")
            self.assertEqual(
                fetch_one(app, "SELECT payment_status FROM fee_payments WHERE id=?", (payment["payment_id"],))["payment_status"],
                "Cancelled",
            )
            self.assertEqual(
                fetch_one(app, "SELECT status FROM fee_receipts WHERE payment_id=?", (payment["payment_id"],))["status"],
                "Cancelled",
            )
            reopened = fetch_one(
                app, "SELECT status,paid_date FROM student_fee_records WHERE id=?", (fee_id,)
            )
            self.assertEqual(reopened["status"], "Unpaid")
            self.assertIsNone(reopened["paid_date"])

            # Build one new valid payment for isolated restore-corruption checks.
            second_fee = self._insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, month=8, amount=1000
            )
            second_payment = app.fee_service.process_payment(
                student_id, [fee_id, second_fee]
            )

            self._validate_corruption(
                app,
                "cancelled-inconsistent.db",
                lambda c: c.execute(
                    "UPDATE fee_payments SET payment_status='Cancelled' WHERE id=?",
                    (second_payment["payment_id"],),
                ),
                "cancelled",
            )
            self._validate_corruption(
                app,
                "receipt-snapshot.db",
                lambda c: c.execute(
                    "UPDATE fee_receipts SET month_number_snapshot=12 WHERE payment_id=?",
                    (second_payment["payment_id"],),
                ),
                "receipt snapshots",
            )
            self._validate_corruption(
                app,
                "payment-snapshot.db",
                lambda c: c.execute(
                    "UPDATE fee_payments SET student_name_snapshot='OTHER STUDENT' WHERE id=?",
                    (second_payment["payment_id"],),
                ),
                "payment identity",
            )

    def test_lifecycle_graduation_and_admission_history_are_atomic(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="LIF001", roll_number=1
            )
            self._validate_corruption(
                app,
                "active-with-graduation.db",
                lambda c: c.execute(
                    """
                    INSERT INTO graduation_records(
                        student_id,admission_number,last_class_id,last_class_name,
                        last_roll_number,graduation_date,status,is_reversed,
                        completion_session_start_year,graduation_effective_date
                    ) VALUES(?,?,?,'10A',1,'2026-07-01 10:00:00','Graduated',0,
                             2025,'2026-04-01')
                    """,
                    (student_id, "LIF001", class_id),
                ),
                "active students",
            )
            self._validate_corruption(
                app,
                "completed-without-record.db",
                lambda c: c.execute(
                    "UPDATE students SET student_status='Graduated',roll_number=-1,released_roll_number=1 WHERE student_id=?",
                    (student_id,),
                ),
                "graduation record",
            )
            self._validate_corruption(
                app,
                "missing-admission-history.db",
                lambda c: c.execute(
                    "DELETE FROM student_class_history WHERE student_id=? AND change_type='ADMISSION'",
                    (student_id,),
                ),
                "exactly one admission",
            )

            strict = app.strict_connect()
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    strict.execute(
                        """
                        INSERT INTO student_class_history(
                            student_id,old_class_id,new_class_id,effective_month,effective_year,
                            change_type,change_datetime,changed_by
                        ) VALUES(?,NULL,?,4,2026,'ADMISSION','2026-04-01 09:00:00','test')
                        """,
                        (student_id, class_id),
                    )
                strict.rollback()
            finally:
                strict.close()

            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)
            connection = app.connect()
            try:
                connection.execute(
                    """
                    CREATE TRIGGER v19_block_graduation_insert
                    BEFORE INSERT ON graduation_records
                    BEGIN SELECT RAISE(ABORT,'forced graduation failure'); END
                    """
                )
                connection.commit()
            finally:
                connection.close()
            with mock.patch.object(
                app.promotion_full_class_v2, "datetime", March2027DateTime
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "forced graduation"):
                    app.promotion_full_class_v2.promote_full_class(
                        class_id, {student_id: "P"}, "v19"
                    )
            unchanged = fetch_one(
                app,
                "SELECT student_status,roll_number,released_roll_number FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(unchanged["student_status"], "Active")
            self.assertEqual(int(unchanged["roll_number"]), 1)
            self.assertIsNone(unchanged["released_roll_number"])
            self.assertEqual(fetch_all(app, "SELECT * FROM graduation_records"), [])
            self.assertEqual(fetch_all(app, "SELECT * FROM promotion_batches"), [])

    def test_promotion_counters_failure_counts_and_batch_membership_are_enforced(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="PRO001", roll_number=1
            )
            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "promotion batch"):
                    strict.execute(
                        """
                        INSERT INTO promotion_batches(
                            source_class_id,source_class_name,promotion_datetime,total_students,
                            passed_students,failed_students,graduated_students,created_by,is_reversed,
                            source_session_start_year,target_session_start_year,effective_date,promotion_mode
                        ) VALUES(?,'1A','2026-07-01 10:00:00',1,5,0,0,'test',0,
                                 2025,2026,'2026-04-01','OVERDUE')
                        """,
                        (class_id,),
                    )
                strict.rollback()
                batch_id = strict.execute(
                    """
                    INSERT INTO promotion_batches(
                        source_class_id,source_class_name,promotion_datetime,total_students,
                        passed_students,failed_students,graduated_students,created_by,is_reversed,
                        source_session_start_year,target_session_start_year,effective_date,promotion_mode
                    ) VALUES(?,'1A','2026-07-01 10:00:00',1,1,0,0,'test',0,
                             2025,2026,'2026-04-01','OVERDUE')
                    """,
                    (class_id,),
                ).lastrowid
                history_values = (
                    batch_id, student_id, "PRO001", "Test Student", "Test Father",
                    class_id, "1A", class_id, "1A", 1, 1, "P", "Active", "Active",
                    0, 0, "2026-07-01 10:00:00", "FULL_CLASS", 0,
                )
                strict.execute(
                    """
                    INSERT INTO promotion_history(
                        batch_id,student_id,admission_number,student_name_snapshot,
                        father_name_snapshot,old_class_id,old_class_name,new_class_id,
                        new_class_name,old_roll_number,new_roll_number,promotion_result,
                        previous_status,new_status,failure_count_before,failure_count_after,
                        promotion_datetime,promotion_type,is_reversed
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    history_values,
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    strict.execute(
                        """
                        INSERT INTO promotion_history(
                            batch_id,student_id,admission_number,student_name_snapshot,
                            father_name_snapshot,old_class_id,old_class_name,new_class_id,
                            new_class_name,old_roll_number,new_roll_number,promotion_result,
                            previous_status,new_status,failure_count_before,failure_count_after,
                            promotion_datetime,promotion_type,is_reversed
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        history_values,
                    )
                strict.rollback()

                batch_id = strict.execute(
                    """
                    INSERT INTO promotion_batches(
                        source_class_id,source_class_name,promotion_datetime,total_students,
                        passed_students,failed_students,graduated_students,created_by,is_reversed,
                        source_session_start_year,target_session_start_year,effective_date,promotion_mode
                    ) VALUES(?,'1A','2026-07-01 10:00:00',1,0,1,0,'test',0,2025,2026,'2026-04-01','OVERDUE')
                    """,
                    (class_id,),
                ).lastrowid
                negative = list(history_values)
                negative[0] = batch_id
                negative[14] = -1
                with self.assertRaisesRegex(sqlite3.IntegrityError, "negative"):
                    strict.execute(
                        """
                        INSERT INTO promotion_history(
                            batch_id,student_id,admission_number,student_name_snapshot,
                            father_name_snapshot,old_class_id,old_class_name,new_class_id,
                            new_class_name,old_roll_number,new_roll_number,promotion_result,
                            previous_status,new_status,failure_count_before,failure_count_after,
                            promotion_datetime,promotion_type,is_reversed
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        tuple(negative),
                    )
                strict.rollback()
            finally:
                strict.close()

    def test_fee_type_month_year_and_session_are_canonical(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="FEE001", roll_number=1
            )
            strict = app.strict_connect()
            try:
                base = (
                    student_id, class_id, "2026-2027", "July", 7, 2026,
                    1000, "Unpaid", "2026-07-01", "2026-07-01",
                )
                with self.assertRaisesRegex(sqlite3.IntegrityError, "Fee type"):
                    strict.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id,class_id,session_year,fee_type,month_name,
                            month_number,year,amount,status,created_at,updated_at
                        ) VALUES(?,?,?,'monthly',?,?,?,?,?,?,?)
                        """,
                        base,
                    )
                strict.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "Fee type"):
                    strict.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id,class_id,session_year,fee_type,month_name,
                            month_number,year,amount,status,created_at,updated_at
                        ) VALUES(?,?,?,'Monthly','July',NULL,NULL,?,?,?,?)
                        """,
                        (student_id, class_id, "2026-2027", 1000, "Unpaid", "2026-07-01", "2026-07-01"),
                    )
                strict.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "academic session"):
                    strict.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id,class_id,session_year,fee_type,month_name,
                            month_number,year,amount,status,created_at,updated_at
                        ) VALUES(?,?,'2030-2031','Monthly','July',7,2026,1000,'Unpaid',
                                 '2026-07-01','2026-07-01')
                        """,
                        (student_id, class_id),
                    )
                strict.rollback()
            finally:
                strict.close()

            fee_id = self._insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, amount=1000
            )
            connection = app.connect()
            try:
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(
                    "UPDATE student_fee_records SET fee_type=' monthly ',session_year='2030-2031' WHERE id=?",
                    (fee_id,),
                )
                connection.execute("PRAGMA user_version=19")
                connection.commit()
            finally:
                connection.close()
            app.database.initialize_database()
            repaired = fetch_one(
                app,
                "SELECT fee_type,session_year FROM student_fee_records WHERE id=?",
                (fee_id,),
            )
            self.assertEqual(repaired["fee_type"], "Monthly")
            self.assertEqual(repaired["session_year"], "2026-2027")

    def test_sibling_assignment_fields_and_cnic_are_consistent_without_priority_change(self):
        with IsolatedApplication() as app:
            class_2a = insert_class(app, "2", "A")
            class_3a = insert_class(app, "3", "A")
            father_cnic = "1234512345671"
            first = insert_student(
                app, class_id=class_2a, admission_number="SIB001", roll_number=1,
                father_name="Same Father", father_cnic=father_cnic,
            )
            second = insert_student(
                app, class_id=class_2a, admission_number="SIB002", roll_number=2,
                father_name="Same Father", father_cnic=father_cnic,
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO sibling_fee_settings(
                        monthly_fee_percentage,effective_from_month,effective_from_year,
                        created_at,created_by,is_active
                    ) VALUES(50,7,2026,'2026-07-01 10:00:00','test',1)
                    """
                )
                connection.commit()
            finally:
                connection.close()

            app.sibling_fee_service.recalculate_sibling_fees_for_student_ids(
                {first, second}, 7, 2026, changed_by="test"
            )
            assignments = fetch_all(
                app,
                "SELECT student_id,assignment_role,is_sibling_policy,monthly_fee_percentage "
                "FROM sibling_fee_assignments WHERE is_active=1 ORDER BY student_id",
            )
            self.assertEqual(assignments[0]["assignment_role"], "FULL")
            self.assertEqual(int(assignments[0]["student_id"]), first)
            self.assertEqual(float(assignments[0]["monthly_fee_percentage"]), 100.0)
            self.assertEqual(assignments[1]["assignment_role"], "DISCOUNTED")
            self.assertEqual(float(assignments[1]["monthly_fee_percentage"]), 50.0)

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET class_id=? WHERE student_id=?", (class_3a, second)
                )
                connection.commit()
            finally:
                connection.close()
            app.sibling_fee_service.recalculate_sibling_fees_for_student_ids(
                {first, second}, 8, 2026, changed_by="test"
            )
            august = fetch_all(
                app,
                "SELECT student_id,assignment_role FROM sibling_fee_assignments "
                "WHERE is_active=1 AND effective_from_month=8 AND effective_from_year=2026 "
                "ORDER BY student_id",
            )
            roles = {int(row["student_id"]): row["assignment_role"] for row in august}
            self.assertEqual(roles[second], "FULL")
            self.assertEqual(roles[first], "DISCOUNTED")

            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "sibling"):
                    strict.execute(
                        """
                        INSERT INTO sibling_fee_assignments(
                            student_id,normalized_father_name,normalized_father_cnic,
                            monthly_fee_percentage,is_sibling_policy,assignment_role,
                            effective_from_month,effective_from_year,created_at,is_active
                        ) VALUES(?, 'SAME FATHER', ?,50,1,'FULL',9,2026,'2026-09-01',1)
                        """,
                        (first, father_cnic),
                    )
                strict.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "sibling"):
                    strict.execute(
                        """
                        INSERT INTO sibling_fee_assignments(
                            student_id,normalized_father_name,normalized_father_cnic,
                            monthly_fee_percentage,is_sibling_policy,assignment_role,
                            effective_from_month,effective_from_year,created_at,is_active
                        ) VALUES(?, 'SAME FATHER','9999999999998',50,1,'DISCOUNTED',9,2026,'2026-09-01',1)
                        """,
                        (first,),
                    )
                strict.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "sibling"):
                    strict.execute(
                        """
                        INSERT INTO sibling_fee_assignments(
                            student_id,normalized_father_name,normalized_father_cnic,
                            monthly_fee_percentage,is_sibling_policy,assignment_role,
                            effective_from_month,effective_from_year,created_at,is_active
                        ) VALUES(?, 'SAME FATHER', ?,60,1,'DISCOUNTED',9,2026,'2026-09-01',1)
                        """,
                        (first, father_cnic),
                    )
                strict.rollback()
            finally:
                strict.close()


if __name__ == "__main__":
    unittest.main()
