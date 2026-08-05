from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class AdditionalBugFixV20ScopeTests(unittest.TestCase):
    @staticmethod
    def _strict_add(
        app: IsolatedApplication,
        *,
        admission_number: str,
        class_id: int,
        roll_number: int,
        student_name: str,
        father_name: str,
        father_cnic: str,
    ) -> int:
        original_get_connection = app.student_service.get_connection
        app.student_service.get_connection = app.strict_connect
        try:
            return app.student_service.add_student(
                admission_number=admission_number,
                roll_number=roll_number,
                class_id=class_id,
                student_name=student_name,
                father_name=father_name,
                father_cnic=father_cnic,
                father_contact_number="+923000000000",
                school_leaving_certificate_submitted=True,
                admission_year=2026,
                admission_month=7,
                admission_day=1,
                admission_time="09:00:00",
                gender="Male",
                date_of_birth="01-01-2015",
                created_by="v20-test",
            )
        finally:
            app.student_service.get_connection = original_get_connection

    @staticmethod
    def _copy_database(app: IsolatedApplication, name: str) -> Path:
        target = app.root / name
        shutil.copy2(app.database_path, target)
        return target

    def test_global_admission_sequence_generation_and_immutability(self):
        with IsolatedApplication() as app:
            self.assertEqual(app.database.CURRENT_SCHEMA_VERSION, 23)
            class_id = insert_class(app, "1", "A")

            self.assertEqual(app.student_service.generate_admission_number("H"), "H1001")
            first = self._strict_add(
                app,
                admission_number="H1001",
                class_id=class_id,
                roll_number=1,
                student_name="FIRST STUDENT",
                father_name="FIRST FATHER",
                father_cnic="1234512345671",
            )
            self.assertEqual(app.student_service.generate_admission_number("P"), "P1002")
            second = self._strict_add(
                app,
                admission_number="P1002",
                class_id=class_id,
                roll_number=2,
                student_name="SECOND STUDENT",
                father_name="SECOND FATHER",
                father_cnic="9876598765431",
            )
            self.assertNotEqual(first, second)

            # Jump the global sequence to the four/five-digit boundary without
            # creating thousands of irrelevant fixture students.
            insert_student(
                app,
                class_id=class_id,
                admission_number="H9999",
                roll_number=3,
                name="BOUNDARY STUDENT",
                father_name="BOUNDARY FATHER",
                father_cnic="1234598765431",
            )
            self.assertEqual(app.student_service.generate_admission_number("P"), "P10000")
            self._strict_add(
                app,
                admission_number="P10000",
                class_id=class_id,
                roll_number=4,
                student_name="TEN THOUSAND",
                father_name="THIRD FATHER",
                father_cnic="1234567890123",
            )
            self.assertEqual(app.student_service.generate_admission_number("P"), "P10001")
            self._strict_add(
                app,
                admission_number="P10001",
                class_id=class_id,
                roll_number=5,
                student_name="TEN THOUSAND ONE",
                father_name="FOURTH FATHER",
                father_cnic="2345678901234",
            )
            self.assertEqual(app.student_service.generate_admission_number("H"), "H10002")

            # P10001 consumes numeric sequence 10001 globally, so H10001 is
            # rejected even though the prefix differs.
            with self.assertRaises(sqlite3.IntegrityError):
                insert_student(
                    app,
                    class_id=class_id,
                    admission_number="H10001",
                    roll_number=6,
                    name="DUPLICATE SEQUENCE",
                    father_name="FIFTH FATHER",
                    father_cnic="3456789012345",
                )

            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "permanent"):
                    strict.execute(
                        "UPDATE students SET admission_number='H5555' WHERE student_id=?",
                        (first,),
                    )
                strict.rollback()
            finally:
                strict.close()

            module_text = (
                Path(__file__).resolve().parents[2]
                / "app" / "modules" / "student_management.py"
            ).read_text(encoding="utf-8")
            self.assertIn("self.generate_admission_button", module_text)
            self.assertIn("self.admission_number_digits_input.setReadOnly(True)", module_text)
            self.assertIn("self.admission_number_input.setReadOnly(True)", module_text)

            insert_student(
                app,
                class_id=class_id,
                admission_number="H99999",
                roll_number=6,
                name="FINAL SEQUENCE",
                father_name="FINAL FATHER",
                father_cnic="4567890123456",
            )
            with self.assertRaisesRegex(ValueError, "99999"):
                app.student_service.generate_admission_number("P")

    def test_single_active_student_cannot_hold_sibling_policy_role(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "5", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2001",
                roll_number=1,
                name="ONLY CHILD",
                father_name="AHMAD KHAN",
                father_cnic="1234512345671",
            )
            result = app.sibling_fee_service.save_sibling_fee_percentage(
                50, created_by="v20-test"
            )
            self.assertEqual(result["discounted_students"], 0)
            assignment = fetch_one(
                app,
                "SELECT assignment_role,is_sibling_policy,monthly_fee_percentage "
                "FROM sibling_fee_assignments WHERE student_id=? "
                "ORDER BY id DESC LIMIT 1",
                (student_id,),
            )
            self.assertEqual(assignment["assignment_role"], "STANDARD")
            self.assertEqual(int(assignment["is_sibling_policy"]), 0)
            self.assertEqual(float(assignment["monthly_fee_percentage"]), 100.0)

            today = datetime.now()
            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "sibling assignment"):
                    strict.execute(
                        """
                        INSERT INTO sibling_fee_assignments(
                            student_id,normalized_father_cnic,normalized_father_name,
                            monthly_fee_percentage,is_sibling_policy,assignment_role,
                            effective_from_month,effective_from_year,created_at,is_active
                        ) VALUES(?,?,?,?,?,?,?,?,?,1)
                        """,
                        (
                            student_id,
                            "1234512345671",
                            "AHMAD KHAN",
                            50,
                            1,
                            "DISCOUNTED",
                            today.month,
                            today.year,
                            "2026-07-30 03:00:00",
                        ),
                    )
                strict.rollback()
            finally:
                strict.close()

            # Restore/startup validation also rejects a stale latest discounted
            # assignment constructed in fixture mode.
            candidate = self._copy_database(app, "single-sibling-discount.db")
            connection = sqlite3.connect(candidate)
            connection.create_function(
                "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
            )
            connection.create_function(
                "NORMALIZE_PERSON_NAME", 1, app.database.normalize_person_name,
                deterministic=True,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO sibling_fee_assignments(
                        student_id,normalized_father_cnic,normalized_father_name,
                        monthly_fee_percentage,is_sibling_policy,assignment_role,
                        effective_from_month,effective_from_year,created_at,is_active
                    ) VALUES(?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        student_id,
                        "1234512345671",
                        "AHMAD KHAN",
                        50,
                        1,
                        "DISCOUNTED",
                        today.month,
                        today.year,
                        "2026-07-30 03:01:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(candidate), require_current_schema=True
            )
            self.assertFalse(valid, message)
            self.assertIn("at least two matching Active students", message)

            second_id = insert_student(
                app,
                class_id=class_id,
                admission_number="P2002",
                roll_number=2,
                name="SECOND CHILD",
                father_name="  Ahmad   Khan ",
                father_cnic="12345-1234567-1",
            )
            app.sibling_fee_service.recalculate_sibling_fees_for_student_ids(
                {student_id, second_id}, today.month, today.year,
                changed_by="v20-test",
            )
            roles = {
                int(row["student_id"]): row["assignment_role"]
                for row in fetch_all(
                    app,
                    "SELECT student_id,assignment_role FROM sibling_fee_assignments "
                    "WHERE effective_from_month=? AND effective_from_year=? "
                    "ORDER BY id",
                    (today.month, today.year),
                )
            }
            self.assertEqual(roles[student_id], "FULL")
            self.assertEqual(roles[second_id], "DISCOUNTED")

    def test_fee_status_and_paid_date_are_consistent(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H3001",
                roll_number=1,
                name="FEE STUDENT",
                father_name="FEE FATHER",
                father_cnic="5678901234567",
            )
            connection = app.connect()
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,month_name,
                        month_number,year,amount,status,paid_date,created_at,updated_at
                    ) VALUES(?,?,'2026-2027','Monthly','July',7,2026,1000,
                             'Unpaid',NULL,'2026-07-01 10:00:00','2026-07-01 10:00:00')
                    """,
                    (student_id, class_id),
                )
                fee_id = int(cursor.lastrowid)
                connection.commit()
            finally:
                connection.close()

            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "cannot have a paid date"):
                    strict.execute(
                        "UPDATE student_fee_records SET paid_date='2026-07-30 03:00:00' WHERE id=?",
                        (fee_id,),
                    )
                strict.rollback()
            finally:
                strict.close()

            payment = app.fee_service.process_payment(student_id, [fee_id])
            paid = fetch_one(
                app,
                "SELECT status,paid_date FROM student_fee_records WHERE id=?",
                (fee_id,),
            )
            self.assertEqual(paid["status"], "Paid")
            self.assertTrue(str(paid["paid_date"] or "").strip())

            strict = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "Pay Fee"):
                    strict.execute(
                        "UPDATE student_fee_records SET paid_date=NULL WHERE id=?",
                        (fee_id,),
                    )
                strict.rollback()
            finally:
                strict.close()

            app.fee_service.cancel_immediate_payment(
                int(payment["payment_id"]), cancelled_by="v20-test"
            )
            reopened = fetch_one(
                app,
                "SELECT status,paid_date FROM student_fee_records WHERE id=?",
                (fee_id,),
            )
            self.assertEqual(reopened["status"], "Unpaid")
            self.assertIsNone(reopened["paid_date"])

            candidate = self._copy_database(app, "contradictory-paid-date.db")
            connection = sqlite3.connect(candidate)
            connection.create_function(
                "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
            )
            try:
                connection.execute(
                    "UPDATE student_fee_records SET status='Unpaid',paid_date='2026-07-30 03:00:00' WHERE id=?",
                    (fee_id,),
                )
                connection.commit()
            finally:
                connection.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(candidate), require_current_schema=True
            )
            self.assertFalse(valid, message)
            self.assertIn("paid_date", message)


if __name__ == "__main__":
    unittest.main()
