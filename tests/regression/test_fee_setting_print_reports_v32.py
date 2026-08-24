from __future__ import annotations

import unittest
from datetime import datetime

from tests.regression.support import IsolatedApplication, insert_class, insert_student

FATHER_CNIC = "1730112345671"


class FeeSettingPrintReportsV32Tests(unittest.TestCase):
    def test_sibling_and_individual_snapshots_show_current_fee_details(self):
        with IsolatedApplication() as app:
            now = datetime.now()  # noqa: DTZ005 - matches the application's local clock
            session_start = now.year if now.month >= 4 else now.year - 1
            class_ids = [
                insert_class(app, "3", "A"),
                insert_class(app, "5", "A"),
                insert_class(app, "10", "A"),
            ]
            for class_id in class_ids:
                app.fee_service.set_class_fee(class_id, 6000, 4, session_start)

            student_ids = []
            for index, class_id in enumerate(class_ids, start=1):
                student_ids.append(
                    insert_student(
                        app,
                        class_id=class_id,
                        admission_number=f"H32R{index}",
                        roll_number=index,
                        name=f"Report Brother {index}",
                        father_name="Report Father",
                        father_cnic=FATHER_CNIC,
                    )
                )

            app.sibling_fee_service.save_sibling_fee_percentage(
                50, created_by="v32-report-test"
            )
            app.fee_service.set_student_fee_override(
                student_ids[0],
                70,
                now.month,
                now.year,
                number_of_sessions=1,
                created_by="v32-report-test",
            )

            sibling_report = (
                app.fee_setting_report_service.get_sibling_fee_report_snapshot(now)
            )
            self.assertTrue(sibling_report["report_read_only"])
            self.assertEqual(sibling_report["family_count"], 1)
            self.assertEqual(sibling_report["student_count"], 3)
            family = sibling_report["families"][0]
            self.assertEqual(family["father_name"], "Report Father")
            self.assertEqual(family["father_cnic"], FATHER_CNIC)
            self.assertEqual(len(family["students"]), 3)
            students = {
                int(student["student_id"]): student for student in family["students"]
            }
            self.assertEqual(students[student_ids[0]]["fee_source"], "Individual")
            self.assertEqual(students[student_ids[0]]["current_fee"], 4200.0)
            self.assertEqual(students[student_ids[1]]["current_fee"], 3000.0)
            self.assertEqual(students[student_ids[2]]["current_fee"], 6000.0)

            individual_report = (
                app.fee_setting_report_service.get_individual_fee_report_snapshot(now)
            )
            self.assertTrue(individual_report["report_read_only"])
            self.assertEqual(individual_report["student_count"], 1)
            individual = individual_report["students"][0]
            self.assertEqual(individual["student_id"], student_ids[0])
            self.assertEqual(individual["class_fee"], 6000.0)
            self.assertEqual(individual["monthly_fee_percentage"], 70.0)
            self.assertEqual(individual["individual_fee"], 4200.0)


if __name__ == "__main__":
    unittest.main()
