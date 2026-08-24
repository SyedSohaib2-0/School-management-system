from __future__ import annotations

from datetime import datetime
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    insert_class,
    insert_student,
)


FATHER_CNIC = "1730112345671"


class IndividualFeeSiblingPrecedenceV32Tests(unittest.TestCase):
    def test_individual_fee_survives_new_siblings_without_changing_family_count(self):
        with IsolatedApplication() as app:
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            lower_class = insert_class(app, "3", "A")
            middle_class = insert_class(app, "5", "A")
            highest_class = insert_class(app, "10", "A")
            for class_id in (lower_class, middle_class, highest_class):
                app.fee_service.set_class_fee(class_id, 6000, 4, session_start)

            first_id = insert_student(
                app,
                class_id=lower_class,
                admission_number="H3201",
                roll_number=1,
                name="First Brother",
                father_name="Test Father",
                father_cnic=FATHER_CNIC,
            )
            app.fee_service.ensure_fee_records_generated(first_id)
            app.sibling_fee_service.save_sibling_fee_percentage(
                50, created_by="v32-regression"
            )
            app.fee_service.set_student_fee_override(
                first_id,
                70,
                now.month,
                now.year,
                number_of_sessions=1,
                created_by="v32-regression",
            )

            second_id = insert_student(
                app,
                class_id=middle_class,
                admission_number="H3202",
                roll_number=1,
                name="Second Brother",
                father_name="Test Father",
                father_cnic=FATHER_CNIC,
            )
            third_id = insert_student(
                app,
                class_id=highest_class,
                admission_number="H3203",
                roll_number=1,
                name="Third Brother",
                father_name="Test Father",
                father_cnic=FATHER_CNIC,
            )
            result = app.sibling_fee_service.recalculate_sibling_fees_for_student_ids(
                {first_id, second_id, third_id},
                now.month,
                now.year,
                changed_by="v32-regression",
            )

            self.assertEqual(result["affected_students"], 3)
            self.assertEqual(result["full_fee_students"], 1)
            self.assertEqual(result["discounted_students"], 2)

            assignments = {
                int(row["student_id"]): row
                for row in fetch_all(
                    app,
                    "SELECT student_id,assignment_role,is_sibling_policy "
                    "FROM sibling_fee_assignments "
                    "WHERE effective_from_month=? AND effective_from_year=? "
                    "AND is_active=1 ORDER BY id",
                    (now.month, now.year),
                )
            }
            self.assertEqual(len(assignments), 3)
            self.assertEqual(assignments[first_id]["assignment_role"], "DISCOUNTED")
            self.assertEqual(assignments[second_id]["assignment_role"], "DISCOUNTED")
            self.assertEqual(assignments[third_id]["assignment_role"], "FULL")
            self.assertTrue(
                all(int(row["is_sibling_policy"]) == 1 for row in assignments.values())
            )

            connection = app.connect()
            try:
                cursor = connection.cursor()
                first_fee = app.fee_service.get_effective_fee(
                    cursor, lower_class, first_id, now.month, now.year
                )
                second_fee = app.fee_service.get_effective_fee(
                    cursor, middle_class, second_id, now.month, now.year
                )
                third_fee = app.fee_service.get_effective_fee(
                    cursor, highest_class, third_id, now.month, now.year
                )
            finally:
                connection.close()

            self.assertEqual(first_fee["fee_source"], "Individual")
            self.assertEqual(float(first_fee["monthly_fee"]), 4200.0)
            self.assertEqual(second_fee["fee_source"], "Sibling")
            self.assertEqual(float(second_fee["monthly_fee"]), 3000.0)
            self.assertEqual(third_fee["fee_source"], "Sibling")
            self.assertEqual(float(third_fee["monthly_fee"]), 6000.0)

            current_record = next(
                row
                for row in fetch_all(
                    app,
                    "SELECT month_number,year,amount,status "
                    "FROM student_fee_records "
                    "WHERE student_id=? AND fee_type='Monthly'",
                    (first_id,),
                )
                if int(row["month_number"]) == now.month
                and int(row["year"]) == now.year
            )
            self.assertEqual(float(current_record["amount"]), 4200.0)
            self.assertEqual(current_record["status"], "Unpaid")


if __name__ == "__main__":
    unittest.main()
