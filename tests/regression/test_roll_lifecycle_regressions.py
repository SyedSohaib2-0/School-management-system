from __future__ import annotations

import unittest

from tests.regression.support import (
    IsolatedApplication,
    allowed_promotion_window,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


def set_capacity(app, class_id: int, capacity: int) -> None:
    connection = app.connect()
    try:
        connection.execute(
            "UPDATE classes SET student_capacity=? WHERE id=?",
            (int(capacity), int(class_id)),
        )
        connection.commit()
    finally:
        connection.close()


def mark_destination_promoted(app, class_id: int, class_name: str) -> None:
    connection = app.connect()
    try:
        connection.execute(
            """
            INSERT INTO promotion_batches(
                source_class_id, source_class_name, destination_class_id,
                destination_class_name, promotion_datetime, total_students,
                passed_students, failed_students, graduated_students,
                created_by, is_reversed, source_session_start_year,
                target_session_start_year, effective_date, promotion_mode
            ) VALUES (?, ?, NULL, 'No Passed Students',
                      '2027-03-01 09:00:00', 1, 0, 1, 0, 'test', 0,
                      2026, 2027, '2027-04-01', 'NORMAL')
            """,
            (int(class_id), class_name),
        )
        connection.commit()
    finally:
        connection.close()


def reactivate_student(app, student_id: int, class_id: int) -> None:
    row = fetch_one(
        app,
        "SELECT * FROM students WHERE student_id=?",
        (student_id,),
    )
    app.student_service.update_student(
        student_id=student_id,
        roll_number=int(row["roll_number"]),
        class_id=int(class_id),
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
        admission_number=None,
        gender=row["gender"] or "Male",
        whatsapp_number=row["whatsapp_number"] or "",
        current_address=row["current_address"] or "",
        permanent_address=row["permanent_address"] or "",
        date_of_birth=row["date_of_birth"] or "",
        form_b_submitted=bool(row["form_b_submitted"]),
        birth_certificate_submitted=bool(row["birth_certificate_submitted"]),
    )


class RollLifecycleRegressionTests(unittest.TestCase):
    def test_incoming_students_receive_first_rolls_and_failed_student_stays_last(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "5", "A")
            destination = insert_class(app, "6", "A")
            failed = insert_student(
                app,
                class_id=destination,
                admission_number="H610",
                roll_number=1,
                name="Existing Failed",
                failure_count=1,
            )
            incoming_one = insert_student(
                app,
                class_id=source,
                admission_number="H501",
                roll_number=1,
                name="Incoming One",
            )
            incoming_two = insert_student(
                app,
                class_id=source,
                admission_number="H502",
                roll_number=2,
                name="Incoming Two",
            )
            mark_destination_promoted(app, destination, "6A")
            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(source, 1300, 4, target_session - 1)
            app.fee_service.set_class_fee(destination, 1500, 4, target_session)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    source,
                    {incoming_one: "P", incoming_two: "P"},
                    "regression",
                )

            rows = fetch_all(
                app,
                """
                SELECT student_id, class_id, roll_number, failure_count
                FROM students WHERE class_id=? ORDER BY roll_number
                """,
                (destination,),
            )
            self.assertEqual(
                [(int(row["student_id"]), int(row["roll_number"])) for row in rows],
                [(incoming_one, 1), (incoming_two, 2), (failed, 3)],
            )

    def test_failed_student_moves_to_next_section_only_when_primary_capacity_requires_it(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "5", "A")
            class_6a = insert_class(app, "6", "A")
            class_6b = insert_class(app, "6", "B")
            set_capacity(app, class_6a, 2)
            set_capacity(app, class_6b, 2)
            failed = insert_student(
                app,
                class_id=class_6a,
                admission_number="H611",
                roll_number=1,
                name="Failed To Move",
                failure_count=1,
            )
            incoming_one = insert_student(
                app,
                class_id=source,
                admission_number="H503",
                roll_number=1,
                name="Incoming Three",
            )
            incoming_two = insert_student(
                app,
                class_id=source,
                admission_number="H504",
                roll_number=2,
                name="Incoming Four",
            )
            mark_destination_promoted(app, class_6a, "6A")
            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(source, 1300, 4, target_session - 1)
            app.fee_service.set_class_fee(class_6a, 1500, 4, target_session)
            app.fee_service.set_class_fee(class_6b, 1500, 4, target_session)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    source,
                    {incoming_one: "P", incoming_two: "P"},
                    "regression",
                )

            primary = fetch_all(
                app,
                "SELECT student_id, roll_number FROM students WHERE class_id=? ORDER BY roll_number",
                (class_6a,),
            )
            moved = fetch_one(
                app,
                "SELECT class_id, roll_number, released_roll_number FROM students WHERE student_id=?",
                (failed,),
            )
            self.assertEqual(
                [(int(row["student_id"]), int(row["roll_number"])) for row in primary],
                [(incoming_one, 1), (incoming_two, 2)],
            )
            self.assertEqual(int(moved["class_id"]), class_6b)
            self.assertEqual(int(moved["roll_number"]), 1)
            self.assertIsNone(moved["released_roll_number"])

    def test_missing_overflow_section_rolls_back_and_reports_capacity(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "5", "A")
            class_6a = insert_class(app, "6", "A")
            set_capacity(app, class_6a, 2)
            failed = insert_student(
                app,
                class_id=class_6a,
                admission_number="H612",
                roll_number=1,
                name="Failed Reserved",
                failure_count=1,
            )
            incoming_one = insert_student(
                app,
                class_id=source,
                admission_number="H505",
                roll_number=1,
            )
            incoming_two = insert_student(
                app,
                class_id=source,
                admission_number="H506",
                roll_number=2,
            )
            mark_destination_promoted(app, class_6a, "6A")
            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(source, 1300, 4, target_session - 1)
            app.fee_service.set_class_fee(class_6a, 1500, 4, target_session)

            with self.assertRaisesRegex(
                ValueError,
                r"Current total capacity: 2;.*needed total capacity: 3;.*Create 6B",
            ):
                with allowed_promotion_window(app):
                    app.promotion_full_class_v2.promote_full_class(
                    source,
                    {incoming_one: "P", incoming_two: "P"},
                    "regression",
                )

            source_rows = fetch_all(
                app,
                "SELECT student_id, class_id, roll_number FROM students WHERE student_id IN (?, ?) ORDER BY student_id",
                (incoming_one, incoming_two),
            )
            failed_row = fetch_one(
                app,
                "SELECT class_id, roll_number FROM students WHERE student_id=?",
                (failed,),
            )
            self.assertTrue(all(int(row["class_id"]) == source for row in source_rows))
            self.assertEqual([int(row["roll_number"]) for row in source_rows], [1, 2])
            self.assertEqual(int(failed_row["class_id"]), class_6a)
            self.assertEqual(int(failed_row["roll_number"]), 1)

    def test_class10_completion_releases_roll_and_displays_star(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H1001",
                roll_number=7,
                name="Fee Pending",
            )
            app.fee_service.set_class_fee(class_10a, 3000, 4, 2026)
            app.fee_service.ensure_fee_records_generated(student_id)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    class_10a,
                    {student_id: "P"},
                    "regression",
                )

            row = fetch_one(
                app,
                """
                SELECT student_id, roll_number, released_roll_number, student_status
                FROM students WHERE student_id=?
                """,
                (student_id,),
            )
            self.assertEqual(row["student_status"], "Partially Graduated")
            self.assertEqual(int(row["released_roll_number"]), 7)
            self.assertLess(int(row["roll_number"]), 0)
            self.assertEqual(app.student_service.display_roll_number(row), "7*")
            self.assertEqual(
                app.student_service.display_name_with_status_marker(
                    "Fee Pending", 0, row["student_status"]
                ),
                "Fee Pending >>>",
            )

    def test_dropped_roll_is_released_when_its_class_is_promoted(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "1", "A")
            destination = insert_class(app, "2", "A")
            active = insert_student(
                app,
                class_id=source,
                admission_number="H110",
                roll_number=1,
            )
            dropped = insert_student(
                app,
                class_id=source,
                admission_number="H111",
                roll_number=2,
                status="Dropped",
            )
            target_session = app.fee_service.get_next_promotion_session_start_year()
            app.fee_service.set_class_fee(source, 1000, 4, target_session - 1)
            app.fee_service.set_class_fee(destination, 1200, 4, target_session)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    source,
                    {active: "P"},
                    "regression",
                )

            row = fetch_one(
                app,
                "SELECT roll_number, released_roll_number, student_status FROM students WHERE student_id=?",
                (dropped,),
            )
            self.assertEqual(row["student_status"], "Dropped")
            self.assertEqual(int(row["released_roll_number"]), 2)
            self.assertLess(int(row["roll_number"]), 0)
            self.assertEqual(app.student_service.display_roll_number(row), "2*")

    def test_reactivation_after_roll_release_assigns_fresh_roll(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            insert_student(
                app,
                class_id=class_1a,
                admission_number="H120",
                roll_number=1,
                name="Current Active",
            )
            released_student = insert_student(
                app,
                class_id=class_1a,
                admission_number="H121",
                roll_number=2,
                name="Released Drop",
                status="Dropped",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET released_roll_number=2, roll_number=-student_id
                    WHERE student_id=?
                    """,
                    (released_student,),
                )
                connection.commit()
            finally:
                connection.close()

            from datetime import datetime
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            app.fee_service.set_class_fee(class_1a, 1200, 4, session_start)

            reactivate_student(app, released_student, class_1a)

            row = fetch_one(
                app,
                """
                SELECT student_status, roll_number, released_roll_number
                FROM students WHERE student_id=?
                """,
                (released_student,),
            )
            self.assertEqual(row["student_status"], "Active")
            self.assertEqual(int(row["roll_number"]), 2)
            self.assertIsNone(row["released_roll_number"])


if __name__ == "__main__":
    unittest.main()
