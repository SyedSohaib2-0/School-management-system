from __future__ import annotations

from datetime import datetime as RealDateTime
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
        value = cls(2027, 3, 27, 19, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


def set_admission_to_march_2026(app, student_id: int, class_id: int) -> None:
    connection = app.connect()
    try:
        connection.execute(
            """
            UPDATE students
            SET admission_date='2026-03-28 09:00:00',
                admission_year=2026,
                admission_month=3,
                admission_day=28
            WHERE student_id=?
            """,
            (int(student_id),),
        )
        connection.execute(
            """
            UPDATE student_class_history
            SET effective_month=3,
                effective_year=2026,
                change_datetime='2026-03-28 09:00:00'
            WHERE student_id=?
              AND change_type='ADMISSION'
              AND new_class_id=?
            """,
            (int(student_id), int(class_id)),
        )
        connection.commit()
    finally:
        connection.close()


def mark_first_three_months_paid(app, student_id: int) -> None:
    connection = app.connect()
    try:
        rows = connection.execute(
            """
            SELECT id
            FROM student_fee_records
            WHERE student_id=?
              AND fee_type='Monthly'
            ORDER BY
                year,
                CASE WHEN month_number >= 4
                     THEN month_number
                     ELSE month_number + 12
                END,
                id
            LIMIT 3
            """,
            (int(student_id),),
        ).fetchall()
        if len(rows) != 3:
            raise AssertionError("Expected three generated fee rows.")
        connection.executemany(
            """
            UPDATE student_fee_records
            SET status='Paid',
                paid_date='2026-07-29 12:00:00'
            WHERE id=?
            """,
            [(int(row["id"]),) for row in rows],
        )
        connection.commit()
    finally:
        connection.close()


class Class10FeeHorizonRegressionTests(unittest.TestCase):
    def test_missing_fee_configuration_raises_and_rolls_back_generated_rows(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H1010",
                roll_number=1,
                name="March Admission",
            )
            set_admission_to_march_2026(app, student_id, class_10a)
            app.fee_service.set_class_fee(class_10a, 5000, 4, 2026)

            with mock.patch.object(
                app.fee_service,
                "datetime",
                March2027DateTime,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    r"10A, March 2026 \(session 2025-2026\).*no partial new fee rows",
                ):
                    app.fee_service.ensure_fee_records_generated(student_id)

            rows = fetch_all(
                app,
                "SELECT * FROM student_fee_records WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(rows, [])

    def test_class10_promotion_missing_fee_rolls_back_entire_operation(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H1013",
                roll_number=4,
                name="Missing Fee Promotion",
            )

            with (
                mock.patch.object(app.fee_service, "datetime", March2027DateTime),
                mock.patch.object(
                    app.promotion_full_class_v2,
                    "datetime",
                    March2027DateTime,
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    r"10A, April 2026 \(session 2026-2027\)",
                ):
                    app.promotion_full_class_v2.promote_full_class(
                        class_10a,
                        {student_id: "P"},
                        "regression",
                    )

            student = fetch_one(
                app,
                "SELECT student_status, roll_number FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(student["student_status"], "Active")
            self.assertEqual(int(student["roll_number"]), 4)
            self.assertEqual(
                fetch_all(app, "SELECT * FROM student_fee_records WHERE student_id=?", (student_id,)),
                [],
            )
            self.assertEqual(fetch_all(app, "SELECT * FROM promotion_batches"), [])
            self.assertEqual(fetch_all(app, "SELECT * FROM promotion_history"), [])
            self.assertEqual(
                fetch_all(
                    app,
                    "SELECT * FROM student_class_history WHERE change_type='PROMOTION'",
                ),
                [],
            )

    def test_class10_mid_session_promotion_retains_rows_through_final_march(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H1011",
                roll_number=2,
                name="Three Months Paid",
            )
            app.fee_service.set_class_fee(class_10a, 5000, 4, 2026)

            with mock.patch.object(
                app.fee_service,
                "datetime",
                March2027DateTime,
            ):
                app.fee_service.ensure_fee_records_generated(student_id)
            mark_first_three_months_paid(app, student_id)

            with (
                mock.patch.object(app.fee_service, "datetime", March2027DateTime),
                mock.patch.object(
                    app.promotion_full_class_v2,
                    "datetime",
                    March2027DateTime,
                ),
            ):
                app.promotion_full_class_v2.promote_full_class(
                    class_10a,
                    {student_id: "P"},
                    "regression",
                )
                rows = app.fee_service.get_fee_table_for_student(student_id)

            self.assertEqual(len(rows), 12)
            self.assertEqual(sum(1 for row in rows if row["status"] == "Paid"), 3)
            self.assertEqual(sum(1 for row in rows if row["status"] == "Unpaid"), 9)
            self.assertEqual(
                [(int(row["month_number"]), int(row["year"])) for row in rows],
                [
                    (4, 2026), (5, 2026), (6, 2026), (7, 2026),
                    (8, 2026), (9, 2026), (10, 2026), (11, 2026),
                    (12, 2026), (1, 2027), (2, 2027), (3, 2027),
                ],
            )
            student = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(student["student_status"], "Partially Graduated")

    def test_existing_incorrect_graduated_record_repairs_to_partial(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H1012",
                roll_number=3,
                name="Existing Repair",
            )
            app.fee_service.set_class_fee(class_10a, 5000, 4, 2026)

            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET student_status='Graduated',
                        released_roll_number=3,
                        roll_number=?
                    WHERE student_id=?
                    """,
                    (-abs(student_id), student_id),
                )
                connection.execute(
                    """
                    INSERT INTO graduation_records(
                        student_id, admission_number, last_class_id,
                        last_class_name, last_roll_number, graduation_date,
                        status, is_reversed
                    ) VALUES (?, 'H1012', ?, '10A', 3,
                              '2027-03-15 12:00:00', 'Graduated', 0)
                    """,
                    (student_id, class_10a),
                )
                connection.commit()
            finally:
                connection.close()

            with mock.patch.object(
                app.fee_service,
                "datetime",
                March2027DateTime,
            ):
                rows = app.fee_service.get_fee_table_for_student(student_id)

            self.assertEqual(len(rows), 12)
            self.assertTrue(all(row["status"] == "Unpaid" for row in rows))
            student = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(student["student_status"], "Partially Graduated")


if __name__ == "__main__":
    unittest.main()
