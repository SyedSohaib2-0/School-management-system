from __future__ import annotations

from datetime import datetime
import unittest

from tests.regression.support import (
    IsolatedApplication,
    AllowedPromotionDateTime,
    allowed_promotion_window,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)
from tests.regression.test_student_status_regressions import update_status


FATHER_CNIC = "1730112345671"


def set_father_cnic(app, student_id: int, cnic: str = FATHER_CNIC) -> None:
    connection = app.connect()
    try:
        connection.execute(
            "UPDATE students SET father_cnic=? WHERE student_id=?",
            (cnic, int(student_id)),
        )
        connection.commit()
    finally:
        connection.close()


def fee_rows(app, student_id: int):
    return fetch_all(
        app,
        """
        SELECT month_number, year, amount, status, paid_date
        FROM student_fee_records
        WHERE student_id=? AND fee_type='Monthly'
        ORDER BY year, month_number
        """,
        (int(student_id),),
    )


def row_by_period(rows):
    return {
        (int(row["year"]), int(row["month_number"])): row
        for row in rows
    }


class SiblingEventMonthRegressionTests(unittest.TestCase):
    def test_higher_class_new_admission_discounts_existing_lower_class_from_same_month(self):
        with IsolatedApplication() as app:
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            class_5a = insert_class(app, "5", "A")
            class_10a = insert_class(app, "10", "A")
            app.fee_service.set_class_fee(class_5a, 2000, 4, session_start)
            app.fee_service.set_class_fee(class_10a, 3000, 4, session_start)

            existing_id = insert_student(
                app,
                class_id=class_5a,
                admission_number="H11",
                roll_number=1,
                name="Existing Brother",
            )
            set_father_cnic(app, existing_id)
            app.fee_service.ensure_fee_records_generated(existing_id)

            sibling_service = __import__(
                "app.services.sibling_fee_service",
                fromlist=["sibling_fee_service"],
            )
            sibling_service.save_sibling_fee_percentage(
                50,
                created_by="regression",
            )

            new_id = app.student_service.add_student(
                admission_number="H12",
                roll_number=1,
                class_id=class_10a,
                student_name="New Higher Brother",
                father_name="Test Father",
                father_cnic=FATHER_CNIC,
                father_contact_number="+920000000000",
                school_leaving_certificate_submitted=True,
                admission_year=now.year,
                admission_month=now.month,
                admission_day=now.day,
                date_of_birth="01-01-2015",
                gender="Male",
                created_by="regression",
            )
            app.fee_service.ensure_fee_records_generated(new_id)

            existing = row_by_period(fee_rows(app, existing_id))
            new_rows = row_by_period(fee_rows(app, new_id))
            event_period = (now.year, now.month)

            self.assertEqual(float(existing[event_period]["amount"]), 1000.0)
            self.assertEqual(float(new_rows[event_period]["amount"]), 3000.0)
            self.assertTrue(
                all(
                    float(row["amount"]) == 2000.0
                    for period, row in existing.items()
                    if period < event_period
                )
            )
            self.assertTrue(
                all(period >= event_period for period in new_rows)
            )

            existing_assignment = fetch_one(
                app,
                """
                SELECT monthly_fee_percentage, assignment_role
                FROM sibling_fee_assignments
                WHERE student_id=?
                  AND effective_from_month=?
                  AND effective_from_year=?
                ORDER BY id DESC LIMIT 1
                """,
                (existing_id, now.month, now.year),
            )
            self.assertEqual(existing_assignment["assignment_role"], "DISCOUNTED")
            self.assertEqual(
                float(existing_assignment["monthly_fee_percentage"]), 50.0
            )

    def test_dropped_sibling_changes_remaining_student_from_drop_month_only(self):
        with IsolatedApplication() as app:
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            class_5a = insert_class(app, "5", "A")
            class_10a = insert_class(app, "10", "A")
            app.fee_service.set_class_fee(class_5a, 2000, 4, session_start)
            app.fee_service.set_class_fee(class_10a, 3000, 4, session_start)

            lower_id = insert_student(
                app,
                class_id=class_5a,
                admission_number="H13",
                roll_number=1,
                name="Lower Brother",
            )
            higher_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H14",
                roll_number=1,
                name="Higher Brother",
            )
            set_father_cnic(app, lower_id)
            set_father_cnic(app, higher_id)
            app.fee_service.ensure_fee_records_generated(lower_id)
            app.fee_service.ensure_fee_records_generated(higher_id)

            sibling_service = __import__(
                "app.services.sibling_fee_service",
                fromlist=["sibling_fee_service"],
            )
            sibling_service.save_sibling_fee_percentage(
                50,
                created_by="regression",
            )
            before = row_by_period(fee_rows(app, lower_id))
            event_period = (now.year, now.month)
            self.assertEqual(float(before[event_period]["amount"]), 1000.0)

            update_status(app, higher_id, "Dropped")
            after = row_by_period(fee_rows(app, lower_id))
            self.assertEqual(float(after[event_period]["amount"]), 2000.0)
            self.assertTrue(
                all(
                    float(row["amount"]) == 2000.0
                    for period, row in after.items()
                    if (session_start, 4) <= period < event_period
                )
            )

            update_status(app, higher_id, "Active")
            reactivated = row_by_period(fee_rows(app, lower_id))
            self.assertEqual(float(reactivated[event_period]["amount"]), 1000.0)

    def test_partial_graduation_changes_remaining_sibling_from_same_month(self):
        with IsolatedApplication() as app:
            now = AllowedPromotionDateTime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            class_5a = insert_class(app, "5", "A")
            class_10a = insert_class(app, "10", "A")
            app.fee_service.set_class_fee(class_5a, 2000, 4, session_start)
            app.fee_service.set_class_fee(class_10a, 3000, 4, session_start)
            app.fee_service.set_class_fee(class_5a, 2000, 4, session_start + 1)

            lower_id = insert_student(
                app,
                class_id=class_5a,
                admission_number="H17",
                roll_number=1,
                name="Lower Brother",
            )
            higher_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H18",
                roll_number=1,
                name="Class Ten Brother",
            )
            set_father_cnic(app, lower_id)
            set_father_cnic(app, higher_id)
            app.fee_service.ensure_fee_records_generated(lower_id)
            app.fee_service.ensure_fee_records_generated(higher_id)

            sibling_service = __import__(
                "app.services.sibling_fee_service",
                fromlist=["sibling_fee_service"],
            )
            from unittest import mock
            with mock.patch.object(
                sibling_service, "datetime", AllowedPromotionDateTime
            ):
                sibling_service.save_sibling_fee_percentage(
                    50,
                    created_by="regression",
                )
            event_period = (now.year, now.month)
            before = row_by_period(fee_rows(app, lower_id))
            self.assertEqual(float(before[event_period]["amount"]), 1000.0)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    class_10a,
                    {higher_id: "P"},
                    "regression",
                )

            status = fetch_one(
                app,
                "SELECT student_status FROM students WHERE student_id=?",
                (higher_id,),
            )
            self.assertEqual(status["student_status"], "Partially Graduated")
            after = row_by_period(fee_rows(app, lower_id))
            # Promotion executes in March but changes sibling eligibility from
            # the target session's April boundary.
            self.assertEqual(float(after[event_period]["amount"]), 1000.0)
            self.assertEqual(
                {
                    period: float(row["amount"])
                    for period, row in after.items()
                    if (session_start, 4) <= period <= event_period
                },
                {
                    period: float(row["amount"])
                    for period, row in before.items()
                    if (session_start, 4) <= period <= event_period
                },
            )
            app.fee_service.ensure_fee_records_generated(
                lower_id, as_of=datetime(session_start + 1, 4, 5, 10, 0, 0)
            )
            next_session = row_by_period(fee_rows(app, lower_id))
            self.assertEqual(
                float(next_session[(session_start + 1, 4)]["amount"]), 2000.0
            )

    def test_receipt_free_zero_waived_row_reopens_when_sibling_policy_ends(self):
        with IsolatedApplication() as app:
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            class_5a = insert_class(app, "5", "A")
            class_10a = insert_class(app, "10", "A")
            app.fee_service.set_class_fee(class_5a, 2000, 4, session_start)
            app.fee_service.set_class_fee(class_10a, 3000, 4, session_start)

            lower_id = insert_student(
                app,
                class_id=class_5a,
                admission_number="H19",
                roll_number=1,
                name="Zero Fee Lower Brother",
            )
            higher_id = insert_student(
                app,
                class_id=class_10a,
                admission_number="H20",
                roll_number=1,
                name="Higher Brother",
            )
            set_father_cnic(app, lower_id)
            set_father_cnic(app, higher_id)
            app.fee_service.ensure_fee_records_generated(lower_id)
            app.fee_service.ensure_fee_records_generated(higher_id)

            sibling_service = __import__(
                "app.services.sibling_fee_service",
                fromlist=["sibling_fee_service"],
            )
            sibling_service.save_sibling_fee_percentage(
                0,
                created_by="regression",
            )
            event_period = (now.year, now.month)
            before = row_by_period(fee_rows(app, lower_id))[event_period]
            self.assertEqual(float(before["amount"]), 0.0)
            self.assertEqual(before["status"], "Waived")

            update_status(app, higher_id, "Dropped")

            after = row_by_period(fee_rows(app, lower_id))[event_period]
            self.assertEqual(float(after["amount"]), 2000.0)
            self.assertEqual(after["status"], "Unpaid")
            self.assertIsNone(after["paid_date"])

    def test_receipt_backed_paid_event_month_is_not_changed(self):
        with IsolatedApplication() as app:
            now = datetime.now()
            session_start = now.year if now.month >= 4 else now.year - 1
            class_5a = insert_class(app, "5", "A")
            class_10a = insert_class(app, "10", "A")
            app.fee_service.set_class_fee(class_5a, 2000, 4, session_start)
            app.fee_service.set_class_fee(class_10a, 3000, 4, session_start)

            lower_id = insert_student(
                app,
                class_id=class_5a,
                admission_number="H15",
                roll_number=1,
                name="Paid Lower Brother",
            )
            set_father_cnic(app, lower_id)
            app.fee_service.ensure_fee_records_generated(lower_id)

            event_row = fetch_one(
                app,
                """
                SELECT id FROM student_fee_records
                WHERE student_id=? AND fee_type='Monthly'
                  AND month_number=? AND year=?
                """,
                (lower_id, now.month, now.year),
            )
            connection = app.connect()
            try:
                cursor = connection.cursor()
                cursor.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no, student_id, payment_date, total_amount,
                        received_by, created_at, payment_status
                    ) VALUES ('999991', ?, ?, 2000, 'regression', ?, 'Completed')
                    """,
                    (lower_id, now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S")),
                )
                payment_id = int(cursor.lastrowid)
                cursor.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id, student_fee_record_id, item_name,
                        amount, status, created_at
                    ) VALUES (?, ?, 'Monthly Fee', 2000, 'Paid', ?)
                    """,
                    (payment_id, int(event_row["id"]), now.strftime("%Y-%m-%d %H:%M:%S")),
                )
                cursor.execute(
                    """
                    UPDATE student_fee_records
                    SET status='Paid', paid_date=?, amount=2000
                    WHERE id=?
                    """,
                    (now.strftime("%Y-%m-%d %H:%M:%S"), int(event_row["id"])),
                )
                connection.commit()
            finally:
                connection.close()

            app.student_service.add_student(
                admission_number="H16",
                roll_number=1,
                class_id=class_10a,
                student_name="New Higher Brother",
                father_name="Test Father",
                father_cnic=FATHER_CNIC,
                father_contact_number="+920000000000",
                school_leaving_certificate_submitted=True,
                admission_year=2000,
                admission_month=1,
                admission_day=1,
                date_of_birth="01-01-1990",
                gender="Male",
                created_by="regression",
            )

            protected = fetch_one(
                app,
                "SELECT amount, status FROM student_fee_records WHERE id=?",
                (int(event_row["id"]),),
            )
            self.assertEqual(float(protected["amount"]), 2000.0)
            self.assertEqual(protected["status"], "Paid")


if __name__ == "__main__":
    unittest.main()
