from __future__ import annotations

import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_one,
    insert_class,
    insert_student,
)


def release_roll(app, student_id: int, former_roll: int) -> None:
    connection = app.connect()
    try:
        connection.execute(
            """
            UPDATE students
            SET released_roll_number = ?,
                roll_number = -student_id
            WHERE student_id = ?
            """,
            (int(former_roll), int(student_id)),
        )
        connection.commit()
    finally:
        connection.close()


class PositiveRollGenerationRegressionTests(unittest.TestCase):
    def test_new_admission_starts_at_one_when_class_contains_only_released_rolls(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            first = insert_student(
                app, class_id=class_10a, admission_number="H901",
                roll_number=1, status="Graduated",
            )
            second = insert_student(
                app, class_id=class_10a, admission_number="H902",
                roll_number=2, status="Partially Graduated",
            )
            release_roll(app, first, 1)
            release_roll(app, second, 2)

            self.assertEqual(
                app.student_service.generate_roll_number(class_10a),
                1,
            )

    def test_new_admission_uses_highest_positive_roll_and_ignores_released_rows(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            released = insert_student(
                app, class_id=class_10a, admission_number="H903",
                roll_number=8, status="Graduated",
            )
            release_roll(app, released, 8)
            insert_student(
                app, class_id=class_10a, admission_number="H904",
                roll_number=4, status="Active",
            )

            self.assertEqual(
                app.student_service.generate_roll_number(class_10a),
                5,
            )

    def test_transfer_generator_ignores_released_negative_sentinels(self):
        with IsolatedApplication() as app:
            class_6b = insert_class(app, "6", "B")
            released = insert_student(
                app, class_id=class_6b, admission_number="H905",
                roll_number=3, status="Dropped",
            )
            release_roll(app, released, 3)

            self.assertEqual(
                app.student_service.generate_transfer_roll_number(class_6b),
                1,
            )

    def test_reactivation_uses_fresh_positive_roll_when_only_released_rows_exist(self):
        with IsolatedApplication() as app:
            class_5a = insert_class(app, "5", "A")
            older = insert_student(
                app, class_id=class_5a, admission_number="H906",
                roll_number=1, status="Dropped",
            )
            target = insert_student(
                app, class_id=class_5a, admission_number="H907",
                roll_number=2, status="Dropped",
            )
            release_roll(app, older, 1)
            release_roll(app, target, 2)

            row = fetch_one(
                app, "SELECT * FROM students WHERE student_id=?",
                (target,),
            )
            from datetime import datetime
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            app.fee_service.set_class_fee(class_5a, 1200, 4, session_start)
            app.student_service.update_student(
                student_id=target,
                roll_number=int(row["roll_number"]),
                class_id=class_5a,
                student_name=row["student_name"],
                father_name=row["father_name"],
                father_cnic=row["father_cnic"] or "",
                father_contact_number=row["father_contact_number"],
                student_status="Active",
                school_leaving_certificate_submitted=bool(
                    row["school_leaving_certificate_submitted"]
                ),
                admission_year=int(row["admission_year"]),
                admission_month=int(row["admission_month"]),
                admission_day=int(row["admission_day"]),
                admission_time=row["admission_time"],
                updated_by="regression",
                gender=row["gender"] or "Male",
                whatsapp_number=row["whatsapp_number"] or "",
                current_address=row["current_address"] or "",
                permanent_address=row["permanent_address"] or "",
                date_of_birth=row["date_of_birth"] or "",
                form_b_submitted=bool(row["form_b_submitted"]),
                birth_certificate_submitted=bool(
                    row["birth_certificate_submitted"]
                ),
            )

            updated = fetch_one(
                app,
                "SELECT roll_number, released_roll_number, student_status "
                "FROM students WHERE student_id=?",
                (target,),
            )
            self.assertEqual(updated["student_status"], "Active")
            self.assertEqual(int(updated["roll_number"]), 1)
            self.assertIsNone(updated["released_roll_number"])



if __name__ == "__main__":
    unittest.main()
