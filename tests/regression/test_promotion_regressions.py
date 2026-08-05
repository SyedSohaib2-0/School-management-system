from __future__ import annotations

import unittest

from tests.regression.support import (
    IsolatedApplication,
    allowed_promotion_window,
    blocked_promotion_window,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class PromotionRegressionTests(unittest.TestCase):
    def test_full_class_all_pass_promotion_assigns_rolls_and_class_history(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_one = insert_student(
                app,
                class_id=class_1a,
                admission_number="H3",
                roll_number=1,
                name="Student One",
            )
            student_two = insert_student(
                app,
                class_id=class_1a,
                admission_number="H4",
                roll_number=2,
                name="Student Two",
            )

            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(class_1a, 1000, 4, target_session - 1)
            app.fee_service.set_class_fee(class_2a, 1200, 4, target_session)

            with allowed_promotion_window(app):
                result = app.promotion_full_class_v2.promote_full_class(
                    class_1a,
                    {student_one: "P", student_two: "P"},
                    "regression",
                )
            self.assertEqual(int(result["passed_students"]), 2)

            promoted_rows = fetch_all(
                app,
                """
                SELECT student_id, class_id, roll_number
                FROM students ORDER BY student_id
                """,
            )
            self.assertEqual(
                [(int(r["class_id"]), int(r["roll_number"])) for r in promoted_rows],
                [(class_2a, 1), (class_2a, 2)],
            )
            batch = fetch_one(
                app,
                "SELECT is_reversed FROM promotion_batches WHERE batch_id=?",
                (int(result["batch_id"]),),
            )
            self.assertEqual(int(batch["is_reversed"]), 0)

            class_history = fetch_all(
                app,
                """
                SELECT student_id, old_class_id, new_class_id,
                       effective_month, effective_year, change_type
                FROM student_class_history
                WHERE change_type='PROMOTION'
                ORDER BY student_id
                """,
            )
            self.assertEqual(len(class_history), 2)
            self.assertEqual(
                [
                    (
                        int(row["student_id"]),
                        int(row["old_class_id"]),
                        int(row["new_class_id"]),
                        int(row["effective_month"]),
                        int(row["effective_year"]),
                        row["change_type"],
                    )
                    for row in class_history
                ],
                [
                    (student_one, class_1a, class_2a, 4, 2027, "PROMOTION"),
                    (student_two, class_1a, class_2a, 4, 2027, "PROMOTION"),
                ],
            )

    def test_april_uses_overdue_recovery_for_the_ending_session(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="H5",
                roll_number=1,
            )
            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(class_1a, 1000, 4, target_session - 1)
            app.fee_service.set_class_fee(class_1a, 1100, 4, target_session)
            app.fee_service.set_class_fee(class_2a, 1200, 4, target_session)

            with blocked_promotion_window(app):
                result = app.promotion_full_class_v2.promote_full_class(
                    class_1a,
                    {student_id: "P"},
                    "regression",
                )

            self.assertEqual(result["promotion_mode"], "OVERDUE")
            self.assertEqual(result["source_session_start_year"], target_session - 1)
            self.assertEqual(result["target_session_start_year"], target_session)
            self.assertEqual(result["effective_date"], f"{target_session:04d}-04-01")
            student = fetch_one(
                app,
                "SELECT class_id, roll_number FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(int(student["class_id"]), class_2a)
            self.assertEqual(int(student["roll_number"]), 1)
            self.assertEqual(
                len(fetch_all(app, "SELECT * FROM promotion_batches")),
                1,
            )

    def test_single_promotion_and_reversal_apis_are_absent(self):
        with IsolatedApplication() as app:
            self.assertFalse(hasattr(app.promotion_service, "promote_single_student"))
            self.assertFalse(
                hasattr(app.promotion_service, "reverse_single_student_promotion")
            )
            self.assertFalse(
                hasattr(app.promotion_service, "reverse_full_class_batch")
            )
            self.assertFalse(
                hasattr(app.promotion_service, "get_reversible_batch_for_class")
            )


if __name__ == "__main__":
    unittest.main()
