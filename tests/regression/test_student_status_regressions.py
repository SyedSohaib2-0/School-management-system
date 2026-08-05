from __future__ import annotations

from datetime import datetime
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


def update_status(app: IsolatedApplication, student_id: int, new_status: str) -> None:
    row = fetch_one(
        app,
        "SELECT * FROM students WHERE student_id=?",
        (student_id,),
    )
    app.student_service.update_student(
        student_id=student_id,
        roll_number=int(row["roll_number"]),
        class_id=int(row["class_id"]),
        student_name=row["student_name"],
        father_name=row["father_name"],
        father_cnic=row["father_cnic"] or "",
        father_contact_number=row["father_contact_number"],
        student_status=new_status,
        school_leaving_certificate_submitted=bool(
            row["school_leaving_certificate_submitted"]
        ),
        admission_year=int(row["admission_year"]),
        admission_month=int(row["admission_month"]),
        admission_day=int(row["admission_day"]),
        admission_time=row["admission_time"],
        updated_by="regression",
        admission_number=None,
        gender=row["gender"] or "Male",
        whatsapp_number=row["whatsapp_number"] or "",
        current_address=row["current_address"] or "",
        permanent_address=row["permanent_address"] or "",
        date_of_birth=row["date_of_birth"] or "",
        form_b_submitted=bool(row["form_b_submitted"]),
        birth_certificate_submitted=bool(row["birth_certificate_submitted"]),
    )


class StudentStatusRegressionTests(unittest.TestCase):
    def test_active_drop_and_reactivation_preserve_history_and_fee_gap(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="H5",
                roll_number=1,
            )

            now = datetime.now()
            session_start_year = now.year if now.month >= 4 else now.year - 1
            app.fee_service.set_class_fee(
                class_1a,
                1200,
                4,
                session_start_year,
            )
            app.fee_service.ensure_fee_records_generated(student_id)

            before = fetch_all(
                app,
                """
                SELECT month_number, year FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                ORDER BY year, month_number
                """,
                (student_id,),
            )
            self.assertGreater(len(before), 1)

            update_status(app, student_id, "Dropped")
            dropped = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(dropped["student_status"], "Dropped")

            after_drop = fetch_all(
                app,
                """
                SELECT month_number, year FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                ORDER BY year, month_number
                """,
                (student_id,),
            )
            self.assertTrue(
                all(
                    (int(row["year"]), int(row["month_number"]))
                    <= (now.year, now.month)
                    for row in after_drop
                )
            )

            update_status(app, student_id, "Active")
            active = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(active["student_status"], "Active")

            after_reactivation = fetch_all(
                app,
                """
                SELECT month_number, year FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                ORDER BY year, month_number
                """,
                (student_id,),
            )
            self.assertGreater(len(after_reactivation), len(after_drop))

            status_histories = fetch_all(
                app,
                """
                SELECT old_status, new_status, effective_datetime, changed_by
                FROM student_status_history
                WHERE student_id=? ORDER BY id
                """,
                (student_id,),
            )
            self.assertEqual(
                [(row["old_status"], row["new_status"]) for row in status_histories[-2:]],
                [("Active", "Dropped"), ("Dropped", "Active")],
            )
            self.assertTrue(all(row["changed_by"] == "regression" for row in status_histories[-2:]))

            histories = fetch_all(
                app,
                """
                SELECT effective_month, effective_year, changed_by
                FROM student_class_history
                WHERE student_id=? ORDER BY history_id
                """,
                (student_id,),
            )
            self.assertEqual(int(histories[-1]["effective_month"]), now.month)
            self.assertEqual(int(histories[-1]["effective_year"]), now.year)
            self.assertEqual(histories[-1]["changed_by"], "REACTIVATION:regression")

    def test_archived_status_cannot_be_assigned_manually(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="H6",
                roll_number=1,
            )
            with self.assertRaisesRegex(ValueError, "assigned automatically"):
                update_status(app, student_id, "Archived")

            row = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(row["student_status"], "Active")


if __name__ == "__main__":
    unittest.main()
