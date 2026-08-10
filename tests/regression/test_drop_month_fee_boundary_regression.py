from __future__ import annotations

from datetime import datetime
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    insert_class,
    insert_student,
)
from tests.regression.test_student_status_regressions import update_status


class DropMonthFeeBoundaryRegressionTests(unittest.TestCase):
    def test_one_receipt_cannot_cross_academic_sessions(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="ONE-SESSION-1",
                roll_number=1,
            )
            connection = app.connect()
            try:
                first_id = connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,month_name,
                        month_number,year,amount,status,paid_date,created_at,updated_at
                    ) VALUES(?,?,'2025-2026','Monthly','March',3,2026,1500,
                             'Unpaid',NULL,'2026-03-01 00:00:00','2026-03-01 00:00:00')
                    """,
                    (student_id, class_id),
                ).lastrowid
                second_id = connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,month_name,
                        month_number,year,amount,status,paid_date,created_at,updated_at
                    ) VALUES(?,?,'2026-2027','Monthly','April',4,2026,1800,
                             'Unpaid',NULL,'2026-04-01 00:00:00','2026-04-01 00:00:00')
                    """,
                    (student_id, class_id),
                ).lastrowid
                connection.commit()
            finally:
                connection.close()

            self.assertTrue(
                app.fee_service.validate_payment_selection(student_id, [first_id])
            )
            self.assertFalse(
                app.fee_service.validate_payment_selection(
                    student_id, [first_id, second_id]
                )
            )

    def test_manual_archive_removes_unpaid_fees_and_records_event(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")
            app.fee_service.set_class_fee(class_id, 1800, 4, 2026)
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="ARCHIVE-1",
                roll_number=1,
            )
            app.fee_service.ensure_fee_records_generated(
                student_id,
                as_of=datetime(2026, 8, 10, 10, 0, 0),
            )

            update_status(app, student_id, "Archived")

            student = app.student_service.get_student_by_id(student_id)
            self.assertEqual(student["student_status"], "Archived")
            self.assertEqual(student["current_status_event"], "ARCHIVE")
            self.assertEqual(int(student["released_roll_number"]), 1)
            self.assertEqual(int(student["roll_number"]), -student_id)
            self.assertEqual(app.student_service.display_roll_number(student), "1*")
            self.assertEqual(
                fetch_all(
                    app,
                    "SELECT id FROM student_fee_records WHERE student_id=? AND status='Unpaid'",
                    (student_id,),
                ),
                [],
            )

    def test_student_search_returns_current_status_action_date(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="STATUS-DATE-1",
                roll_number=1,
            )
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET student_status='Dropped' WHERE student_id=?",
                    (student_id,),
                )
                connection.execute(
                    """
                    INSERT INTO student_status_history(
                        student_id, old_status, new_status, event_type,
                        effective_datetime, reason, changed_by, created_at,
                        related_class_id, related_event_id
                    ) VALUES(?, 'Active', 'Dropped', 'DROP',
                             '2026-08-10 14:30:00', 'Regression fixture',
                             'regression', '2026-08-10 14:30:00', ?, NULL)
                    """,
                    (student_id, class_id),
                )
                connection.commit()
            finally:
                connection.close()

            row = app.student_service.get_student_by_id(student_id)
            self.assertEqual(row["current_status_event"], "DROP")
            self.assertEqual(row["current_status_date"], "2026-08-10 14:30:00")

    def test_pay_fee_does_not_regenerate_months_after_drop_month(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            app.fee_service.set_class_fee(class_id, 2000, 4, 2026)
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="DROP-AUG-1",
                roll_number=1,
            )

            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2026-08-01 09:00:00',
                        admission_year=2026, admission_month=8, admission_day=1,
                        student_status='Dropped'
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.execute(
                    """
                    INSERT INTO student_status_history(
                        student_id, old_status, new_status, event_type,
                        effective_datetime, reason, changed_by, created_at,
                        related_class_id, related_event_id
                    ) VALUES(?, 'Active', 'Dropped', 'DROP',
                             '2026-08-10 10:00:00', 'Regression fixture',
                             'regression', '2026-08-10 10:00:00', ?, NULL)
                    """,
                    (student_id, class_id),
                )
                connection.commit()
            finally:
                connection.close()

            app.fee_service.ensure_fee_records_generated(
                student_id,
                as_of=datetime(2026, 8, 10, 10, 0, 0),
            )
            # Opening Pay Fee calls the generator again; this used to recreate
            # September through March because it missed the status-history row.
            app.fee_service.get_fee_table_for_student(student_id)

            rows = fetch_all(
                app,
                """
                SELECT month_number, year
                FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                ORDER BY year, month_number
                """,
                (student_id,),
            )
            self.assertEqual(
                [(int(row["month_number"]), int(row["year"])) for row in rows],
                [(8, 2026)],
            )


if __name__ == "__main__":
    unittest.main()
