from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from tests.regression.support import (
    AllowedPromotionDateTime,
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


def update_student_from_row(
    app: IsolatedApplication,
    student_id: int,
    *,
    class_id: int | None = None,
    roll_number: int | None = None,
    status: str | None = None,
    effective_month: int | None = None,
    effective_year: int | None = None,
    admission_year: int | None = None,
    admission_month: int | None = None,
    admission_day: int | None = None,
    admission_time: str | None = None,
) -> None:
    row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
    app.student_service.update_student(
        student_id=student_id,
        admission_number=row["admission_number"],
        roll_number=int(row["roll_number"] if roll_number is None else roll_number),
        class_id=int(row["class_id"] if class_id is None else class_id),
        student_name=row["student_name"],
        gender=row["gender"] or "Male",
        father_name=row["father_name"],
        father_cnic=row["father_cnic"] or "",
        father_contact_number=row["father_contact_number"],
        whatsapp_number=row["whatsapp_number"] or "",
        current_address=row["current_address"] or "",
        permanent_address=row["permanent_address"] or "",
        student_status=row["student_status"] if status is None else status,
        school_leaving_certificate_submitted=bool(
            row["school_leaving_certificate_submitted"]
        ),
        form_b_submitted=bool(row["form_b_submitted"]),
        birth_certificate_submitted=bool(row["birth_certificate_submitted"]),
        date_of_birth=row["date_of_birth"] or "",
        admission_year=int(
            row["admission_year"] if admission_year is None else admission_year
        ),
        admission_month=int(
            row["admission_month"] if admission_month is None else admission_month
        ),
        admission_day=int(
            row["admission_day"] if admission_day is None else admission_day
        ),
        admission_time=row["admission_time"] if admission_time is None else admission_time,
        effective_month=effective_month,
        effective_year=effective_year,
        updated_by="consolidated-regression",
    )


def add_manual_monthly_row(
    app: IsolatedApplication,
    *,
    student_id: int,
    class_id: int,
    month: int,
    year: int,
    amount: float,
    status: str = "Unpaid",
) -> int:
    session_start = year if month >= 4 else year - 1
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id, class_id, session_year, fee_type, month_name,
                month_number, year, amount, status, paid_date, created_at, updated_at
            ) VALUES (?, ?, ?, 'Monthly', ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                student_id,
                class_id,
                f"{session_start}-{session_start + 1}",
                app.fee_service.MONTH_NUMBER_TO_NAME[month],
                month,
                year,
                amount,
                status,
                "2026-07-28 12:00:00",
                "2026-07-28 12:00:00",
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


class ConsolidatedBugFixScopeTests(unittest.TestCase):
    def test_promotion_uses_one_timestamp_and_preserves_source_fee_continuity(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "1", "A")
            destination = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=source, admission_number="H1101", roll_number=1
            )
            app.fee_service.set_class_fee(source, 1000, 4, 2026)
            app.fee_service.set_class_fee(destination, 1200, 4, 2027)

            captured = {}

            def capture_sibling_recalculation(**kwargs):
                captured["month"] = kwargs["effective_month"]
                captured["year"] = kwargs["effective_year"]

            sibling_module = __import__(
                "app.services.sibling_fee_service", fromlist=["dummy"]
            )
            with mock.patch.object(
                app.promotion_full_class_v2,
                "datetime",
                AllowedPromotionDateTime,
            ), mock.patch.object(
                sibling_module,
                "recalculate_sibling_fees_for_student_ids",
                side_effect=capture_sibling_recalculation,
            ):
                app.promotion_full_class_v2.promote_full_class(
                    source, {student_id: "P"}, created_by="regression"
                )

            self.assertEqual(captured, {"month": 4, "year": 2027})
            old_rows = fetch_all(
                app,
                """
                SELECT class_id, month_number, year, status
                FROM student_fee_records
                WHERE student_id=? AND session_year='2026-2027'
                ORDER BY year, month_number
                """,
                (student_id,),
            )
            self.assertEqual(len(old_rows), 12)
            self.assertTrue(all(int(row["class_id"]) == source for row in old_rows))
            self.assertTrue(all(row["status"] == "Unpaid" for row in old_rows))
            promoted = fetch_one(
                app, "SELECT class_id FROM students WHERE student_id=?", (student_id,)
            )
            self.assertEqual(int(promoted["class_id"]), destination)

    def test_missing_source_fee_configuration_blocks_and_rolls_back_promotion(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "1", "A")
            destination = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=source, admission_number="H1102", roll_number=1
            )
            app.fee_service.set_class_fee(destination, 1200, 4, 2027)

            with mock.patch.object(
                app.promotion_full_class_v2,
                "datetime",
                AllowedPromotionDateTime,
            ), self.assertRaisesRegex(ValueError, "configuration is missing"):
                app.promotion_full_class_v2.promote_full_class(
                    source, {student_id: "P"}, created_by="regression"
                )

            row = fetch_one(
                app, "SELECT class_id FROM students WHERE student_id=?", (student_id,)
            )
            self.assertEqual(int(row["class_id"]), source)
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=?",
                    (student_id,),
                )["n"],
                0,
            )
            self.assertEqual(
                fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_batches")["n"], 0
            )

    def test_receipt_reprint_and_finance_use_immutable_mixed_class_snapshots(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app,
                class_id=class_2a,
                admission_number="H1201",
                roll_number=7,
                name="Original Student",
                father_name="Original Father",
            )
            january = add_manual_monthly_row(
                app,
                student_id=student_id,
                class_id=class_1a,
                month=1,
                year=2027,
                amount=1000,
            )
            april = add_manual_monthly_row(
                app,
                student_id=student_id,
                class_id=class_2a,
                month=4,
                year=2027,
                amount=1200,
            )

            preview = app.fee_service.get_payment_preview(
                student_id, [january, april]
            )
            self.assertEqual(preview["class_name"], "MULTIPLE CLASSES")
            self.assertEqual(
                [item["class_name"] for item in preview["fee_items"]],
                ["1A", "2A"],
            )

            immediate = app.fee_service.process_payment(
                student_id, [january, april], received_by="admin"
            )
            self.assertEqual(immediate["class_name"], "MULTIPLE CLASSES")
            self.assertEqual(
                [item["class_name"] for item in immediate["paid_items"]],
                ["1A", "2A"],
            )

            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_number='H9999', roll_number=99,
                        student_name='EDITED NAME', father_name='EDITED FATHER',
                        class_id=?
                    WHERE student_id=?
                    """,
                    (class_1a, student_id),
                )
                connection.commit()
            finally:
                connection.close()

            reprint = app.fee_service.get_completed_payment_by_receipt_number(
                immediate["receipt_no"]
            )
            self.assertEqual(reprint["admission_number"], "H1201")
            self.assertEqual(int(reprint["roll_number"]), 7)
            self.assertEqual(reprint["student_name"], "Original Student")
            self.assertEqual(reprint["father_name"], "Original Father")
            self.assertEqual(reprint["class_name"], "MULTIPLE CLASSES")
            self.assertEqual(reprint["paid_items"], immediate["paid_items"])

            payment_date = fetch_one(
                app,
                "SELECT payment_date FROM fee_payments WHERE receipt_no=?",
                (immediate["receipt_no"],),
            )["payment_date"][:10]
            finance_rows = app.real_time_finance_service.get_fee_payments_for_date(
                payment_date
            )
            row = next(item for item in finance_rows if item["receipt_no"] == immediate["receipt_no"])
            self.assertEqual(row["admission_number"], "H1201")
            self.assertEqual(int(row["roll_number"]), 7)
            self.assertEqual(row["student_name"], "Original Student")
            self.assertEqual(row["father_name"], "Original Father")
            self.assertEqual(row["class_name"], "MULTIPLE CLASSES")

    def test_class_validation_positive_roll_and_zero_roll_search_rejection(self):
        with IsolatedApplication() as app:
            with self.assertRaisesRegex(ValueError, "Class Level"):
                app.class_service.add_class("11", "A")
            with self.assertRaisesRegex(ValueError, "Section"):
                app.class_service.add_class("1", "A-1")

            class_id = app.class_service.add_class("1", "A")
            self.assertEqual(app.class_service.update_class_capacity(class_id, 55), 55)

            base_args = dict(
                admission_number="H1301",
                class_id=class_id,
                student_name="ROLL TEST",
                father_name="FATHER",
                father_cnic="1730112345671",
                father_contact_number="+920000000000",
                school_leaving_certificate_submitted=True,
                admission_year=2026,
                admission_month=7,
                admission_day=1,
                admission_time="09:30",
                date_of_birth="01-01-2015",
                gender="Male",
            )
            for bad_roll in (0, -1):
                with self.assertRaisesRegex(ValueError, "positive Roll Number"):
                    app.student_service.add_student(roll_number=bad_roll, **base_args)

            student_id = app.student_service.add_student(roll_number=1, **base_args)
            self.assertGreater(student_id, 0)
            with self.assertRaisesRegex(ValueError, "positive number"):
                app.student_service.search_students(
                    class_id=class_id,
                    roll_number=0,
                )

    def test_fake_backup_rejected_and_restore_replaces_only_after_staged_validation(self):
        with IsolatedApplication() as app:
            fake_path = app.root / "fake.db"
            connection = sqlite3.connect(fake_path)
            try:
                connection.execute("CREATE TABLE students(fake TEXT)")
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(fake_path)
            )
            self.assertFalse(valid)
            self.assertIn("not a recognized school backup", message)

            live_class = insert_class(app, "1", "A")
            self.assertGreater(live_class, 0)
            os.makedirs(app.backup_service.INTERNAL_BACKUP_DIRECTORY, exist_ok=True)
            restore_path = Path(app.backup_service.INTERNAL_BACKUP_DIRECTORY) / "legacy_restore.db"
            shutil.copy2(app.database_path, restore_path)
            restored_connection = sqlite3.connect(restore_path)
            try:
                restored_connection.execute("DROP INDEX idx_student_monthly_fee_period_unique")
                restored_connection.execute("PRAGMA user_version=13")
                restored_connection.execute(
                    """
                    INSERT INTO classes(class_level, section, class_name, student_capacity,
                                        created_at, updated_at)
                    VALUES ('2', 'B', '2B', 40, '2026-07-28', '2026-07-28')
                    """
                )
                restored_connection.commit()
            finally:
                restored_connection.close()

            stage = app.backup_service.stage_database_restore(str(restore_path))
            self.assertTrue(stage["success"], stage.get("message"))
            self.assertIsNone(fetch_one(app, "SELECT id FROM classes WHERE class_name='2B'"))

            with mock.patch.object(
                app.backup_service,
                "create_pre_restore_backup",
                return_value={
                    "success": True,
                    "backup_path": str(app.root / "pre_restore_safety.hmsb"),
                    "message": "test safety backup",
                },
            ):
                processed = app.backup_service.process_pending_restore_before_startup()
            self.assertTrue(processed["success"], processed.get("message"))
            restored_row = fetch_one(app, "SELECT id FROM classes WHERE class_name='2B'")
            self.assertIsNotNone(restored_row)
            self.assertEqual(
                fetch_one(app, "PRAGMA user_version")[0],
                app.database.CURRENT_SCHEMA_VERSION,
            )

    def test_manual_completion_future_change_and_transfer_drop_are_rejected(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H1401", roll_number=1
            )

            with self.assertRaisesRegex(ValueError, "Class 10 completion workflow"):
                update_student_from_row(app, student_id, status="Graduated")

            # Derive the next calendar month at runtime so this regression
            # remains valid after July 2026 and does not depend on the
            # computer's current month.
            current_date = datetime.now()
            if current_date.month == 12:
                future_month = 1
                future_year = current_date.year + 1
            else:
                future_month = current_date.month + 1
                future_year = current_date.year

            with self.assertRaisesRegex(ValueError, "Future-dated"):
                update_student_from_row(
                    app,
                    student_id,
                    class_id=class_2a,
                    effective_month=future_month,
                    effective_year=future_year,
                )

            with self.assertRaisesRegex(ValueError, "Transfer and Drop"):
                update_student_from_row(
                    app,
                    student_id,
                    class_id=class_2a,
                    status="Dropped",
                    effective_month=7,
                    effective_year=2026,
                )

            row = fetch_one(
                app,
                "SELECT class_id, student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(int(row["class_id"]), class_1a)
            self.assertEqual(row["student_status"], "Active")

    def test_class_change_moves_only_unpaid_effective_month_onward(self):
        with IsolatedApplication() as app:
            old_class = insert_class(app, "1", "A")
            new_class = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=old_class, admission_number="H1501", roll_number=1
            )
            app.fee_service.set_class_fee(old_class, 1000, 4, 2026)
            app.fee_service.set_class_fee(new_class, 1500, 4, 2026)
            april = add_manual_monthly_row(
                app,
                student_id=student_id,
                class_id=old_class,
                month=4,
                year=2026,
                amount=1000,
                status="Paid",
            )
            may = add_manual_monthly_row(
                app,
                student_id=student_id,
                class_id=old_class,
                month=5,
                year=2026,
                amount=1000,
            )
            june = add_manual_monthly_row(
                app,
                student_id=student_id,
                class_id=old_class,
                month=6,
                year=2026,
                amount=1000,
            )
            july = add_manual_monthly_row(
                app,
                student_id=student_id,
                class_id=old_class,
                month=7,
                year=2026,
                amount=1000,
            )

            update_student_from_row(
                app,
                student_id,
                class_id=new_class,
                effective_month=6,
                effective_year=2026,
            )
            rows = {
                int(row["id"]): row
                for row in fetch_all(
                    app,
                    "SELECT id, class_id, amount, status FROM student_fee_records WHERE student_id=?",
                    (student_id,),
                )
            }
            self.assertEqual(int(rows[april]["class_id"]), old_class)
            self.assertEqual(int(rows[may]["class_id"]), old_class)
            self.assertEqual(float(rows[may]["amount"]), 1000)
            self.assertEqual(int(rows[june]["class_id"]), new_class)
            self.assertEqual(float(rows[june]["amount"]), 1500)
            self.assertEqual(int(rows[july]["class_id"]), new_class)
            self.assertEqual(float(rows[july]["amount"]), 1500)

    def test_monthly_fee_uniqueness_repairs_safe_duplicates_and_blocks_risky_ones(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1601", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_student_monthly_fee_period_unique")
                connection.execute("PRAGMA user_version=13")
                for amount in (1000, 1000):
                    connection.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id, class_id, session_year, fee_type, month_name,
                            month_number, year, amount, status, created_at, updated_at
                        ) VALUES (?, ?, '2026-2027', 'Monthly', 'July', 7, 2026,
                                  ?, 'Unpaid', '2026-07-28', '2026-07-28')
                        """,
                        (student_id, class_id, amount),
                    )
                final_version = app.database.run_schema_migrations(connection, 13)
                app.database.set_database_schema_version(connection, final_version)
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(
                fetch_one(
                    app,
                    """
                    SELECT COUNT(*) AS n FROM student_fee_records
                    WHERE student_id=? AND fee_type='Monthly' AND month_number=7 AND year=2026
                    """,
                    (student_id,),
                )["n"],
                1,
            )

        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1602", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_student_monthly_fee_period_unique")
                connection.execute("PRAGMA user_version=13")
                for status in ("Unpaid", "Paid"):
                    connection.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id, class_id, session_year, fee_type, month_name,
                            month_number, year, amount, status, created_at, updated_at
                        ) VALUES (?, ?, '2026-2027', 'Monthly', 'August', 8, 2026,
                                  1000, ?, '2026-07-28', '2026-07-28')
                        """,
                        (student_id, class_id, status),
                    )
                with self.assertRaisesRegex(RuntimeError, "conflicting duplicate"):
                    app.database.run_schema_migrations(connection, 13)
                connection.rollback()
            finally:
                connection.close()
            self.assertEqual(
                fetch_one(
                    app,
                    """
                    SELECT COUNT(*) AS n FROM student_fee_records
                    WHERE student_id=? AND month_number=8 AND year=2026
                    """,
                    (student_id,),
                )["n"],
                0,
            )

    def test_concurrent_fee_generation_cannot_create_duplicate_months(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1701", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            fixed = datetime(2026, 7, 28, 12, 0, 0)

            def generate():
                app.fee_service.ensure_fee_records_generated(student_id, as_of=fixed)

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(generate) for _ in range(4)]
                for future in futures:
                    future.result()

            duplicates = fetch_all(
                app,
                """
                SELECT month_number, year, COUNT(*) AS n
                FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                GROUP BY month_number, year HAVING COUNT(*) > 1
                """,
                (student_id,),
            )
            self.assertEqual(duplicates, [])
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=?",
                    (student_id,),
                )["n"],
                12,
            )

    def test_completed_rolls_are_released_before_roll_index_creation(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            first = insert_student(
                app,
                class_id=class_id,
                admission_number="H1801",
                roll_number=1,
                status="Graduated",
            )
            second = insert_student(
                app,
                class_id=class_id,
                admission_number="H1802",
                roll_number=2,
                status="Partially Graduated",
            )
            connection = app.connect()
            try:
                connection.execute("DROP INDEX idx_students_class_roll_unique")
                connection.execute(
                    "UPDATE students SET roll_number=1, released_roll_number=NULL WHERE student_id IN (?, ?)",
                    (first, second),
                )
                connection.execute("PRAGMA user_version=11")
                final_version = app.database.run_schema_migrations(connection, 11)
                app.database.set_database_schema_version(connection, final_version)
                connection.commit()
            finally:
                connection.close()
            rows = fetch_all(
                app,
                "SELECT student_id, roll_number, released_roll_number FROM students ORDER BY student_id",
            )
            self.assertEqual([int(row["released_roll_number"]) for row in rows], [1, 1])
            self.assertEqual(len({int(row["roll_number"]) for row in rows}), 2)

    def test_selected_admission_date_is_saved_and_remains_immutable(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = app.student_service.add_student(
                admission_number="H1901",
                roll_number=1,
                class_id=class_id,
                student_name="ADMISSION DATE",
                father_name="FATHER",
                father_cnic="1730112345671",
                father_contact_number="+920000000000",
                school_leaving_certificate_submitted=True,
                admission_year=2026,
                admission_month=5,
                admission_day=12,
                admission_time="14:30",
                date_of_birth="01-01-2015",
                gender="Male",
                created_by="regression",
            )
            row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(row["admission_date"], "2026-05-12 14:30:00")
            self.assertEqual(row["admission_time"], "14:30:00")
            history = fetch_one(
                app,
                "SELECT effective_month, effective_year FROM student_class_history WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual((history["effective_month"], history["effective_year"]), (5, 2026))

            update_student_from_row(
                app,
                student_id,
                admission_year=2020,
                admission_month=1,
                admission_day=1,
                admission_time="01:01",
            )
            unchanged = fetch_one(
                app,
                "SELECT admission_date, admission_year, admission_month, admission_day, admission_time FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(unchanged["admission_date"], "2026-05-12 14:30:00")
            self.assertEqual(
                (
                    unchanged["admission_year"],
                    unchanged["admission_month"],
                    unchanged["admission_day"],
                    unchanged["admission_time"],
                ),
                (2026, 5, 12, "14:30:00"),
            )

    def test_archived_student_uses_controlled_reactivation_with_fresh_roll(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            active = insert_student(
                app, class_id=class_id, admission_number="H2001", roll_number=1
            )
            archived = insert_student(
                app,
                class_id=class_id,
                admission_number="H2002",
                roll_number=2,
                status="Archived",
            )
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET released_roll_number=2, roll_number=? WHERE student_id=?",
                    (-archived, archived),
                )
                connection.commit()
            finally:
                connection.close()
            session_start = 2026
            app.fee_service.set_class_fee(class_id, 1000, 4, session_start)

            result = app.student_service.reactivate_student(
                archived, class_id=class_id, updated_by="regression"
            )
            self.assertEqual(result["student_status"], "Active")
            self.assertEqual(int(result["roll_number"]), 2)
            self.assertIsNone(result["released_roll_number"])
            self.assertEqual(int(active), 1)
            with self.assertRaisesRegex(ValueError, "Only Dropped or Archived"):
                app.student_service.reactivate_student(active, class_id=class_id)

    def test_non_finite_fee_and_expense_amounts_are_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            for value in (float("nan"), float("inf"), float("-inf")):
                self.assertFalse(app.fee_service.validate_fee_amount(value))
                self.assertFalse(app.expense_service.validate_expense_amount(value))
                with self.assertRaises(ValueError):
                    app.fee_service.set_class_fee(class_id, value, 4, 2026)
                with self.assertRaises(ValueError):
                    app.expense_service.add_expense("2026-07-28", value, "Invalid")
            self.assertEqual(
                fetch_one(app, "SELECT COUNT(*) AS n FROM fee_settings")["n"], 0
            )
            self.assertEqual(
                fetch_one(app, "SELECT COUNT(*) AS n FROM expenses")["n"], 0
            )


if __name__ == "__main__":
    unittest.main()
