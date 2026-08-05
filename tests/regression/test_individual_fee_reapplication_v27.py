from __future__ import annotations

from datetime import datetime
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class March2027DateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 15, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class April2027DateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 4, 10, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class IndividualFeeReapplicationV27Tests(unittest.TestCase):
    def _monthly_rows(self, app, student_id: int):
        return fetch_all(
            app,
            "SELECT month_number,year,amount,status,paid_date "
            "FROM student_fee_records WHERE student_id=? AND fee_type='Monthly' "
            "ORDER BY year, CASE WHEN month_number>=4 THEN month_number ELSE month_number+12 END",
            (student_id,),
        )

    def test_limited_rule_save_remove_and_resave_recalculates_adjustable_rows(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "7", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2701",
                roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 7000, 4, 2026)
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE student_fee_records SET status='Paid', paid_date='2026-09-10 10:00:00' "
                    "WHERE student_id=? AND month_number=9 AND year=2026",
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            app.fee_service.set_student_fee_override(
                student_id,
                50,
                8,
                2026,
                number_of_sessions=1,
                created_by="v27-regression",
            )
            first_rows = self._monthly_rows(app, student_id)
            first_by_period = {
                (int(row["year"]), int(row["month_number"])): row
                for row in first_rows
            }
            for period in (
                (2026, 8), (2026, 10), (2026, 11), (2026, 12),
                (2027, 1), (2027, 2), (2027, 3),
            ):
                self.assertEqual(float(first_by_period[period]["amount"]), 3500.0)
                self.assertEqual(first_by_period[period]["status"], "Unpaid")
            self.assertEqual(float(first_by_period[(2026, 9)]["amount"]), 7000.0)
            self.assertEqual(first_by_period[(2026, 9)]["status"], "Paid")
            self.assertEqual(
                first_by_period[(2026, 9)]["paid_date"],
                "2026-09-10 10:00:00",
            )

            removed = app.fee_service.remove_student_fee_override(
                student_id,
                effective_from_month=8,
                session_start_year=2026,
                removed_by="v27-regression",
            )
            self.assertTrue(removed)
            removed_rows = self._monthly_rows(app, student_id)
            removed_by_period = {
                (int(row["year"]), int(row["month_number"])): row
                for row in removed_rows
            }
            for period in (
                (2026, 8), (2026, 10), (2026, 11), (2026, 12),
                (2027, 1), (2027, 2), (2027, 3),
            ):
                self.assertEqual(float(removed_by_period[period]["amount"]), 7000.0)
                self.assertEqual(removed_by_period[period]["status"], "Unpaid")
            self.assertEqual(removed_by_period[(2026, 9)]["status"], "Paid")

            app.fee_service.set_student_fee_override(
                student_id,
                50,
                8,
                2026,
                number_of_sessions=1,
                created_by="v27-regression",
            )
            reapplied_rows = self._monthly_rows(app, student_id)
            reapplied_by_period = {
                (int(row["year"]), int(row["month_number"])): row
                for row in reapplied_rows
            }
            for period in (
                (2026, 8), (2026, 10), (2026, 11), (2026, 12),
                (2027, 1), (2027, 2), (2027, 3),
            ):
                self.assertEqual(float(reapplied_by_period[period]["amount"]), 3500.0)
                self.assertEqual(reapplied_by_period[period]["status"], "Unpaid")
            self.assertEqual(float(reapplied_by_period[(2026, 9)]["amount"]), 7000.0)
            self.assertEqual(reapplied_by_period[(2026, 9)]["status"], "Paid")

            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                pay_fee_rows = app.fee_service.get_fee_table_for_student(student_id)
            pay_fee_by_period = {
                (int(row["year"]), int(row["month_number"])): row
                for row in pay_fee_rows
            }
            self.assertEqual(float(pay_fee_by_period[(2026, 8)]["amount"]), 3500.0)
            self.assertEqual(float(pay_fee_by_period[(2027, 3)]["amount"]), 3500.0)

    def test_zero_percent_reapplication_waives_only_adjustable_period_and_ends_at_session(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "8", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2702",
                roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 6000, 4, 2026)
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)

            app.fee_service.set_student_fee_override(
                student_id, 50, 8, 2026, number_of_sessions=1
            )
            app.fee_service.remove_student_fee_override(student_id, 8, 2026)
            app.fee_service.set_student_fee_override(
                student_id, 0, 8, 2026, number_of_sessions=1
            )

            rows = self._monthly_rows(app, student_id)
            by_period = {
                (int(row["year"]), int(row["month_number"])): row
                for row in rows
            }
            for period in (
                (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
                (2027, 1), (2027, 2), (2027, 3),
            ):
                self.assertEqual(float(by_period[period]["amount"]), 0.0)
                self.assertEqual(by_period[period]["status"], "Waived")
                self.assertIsNone(by_period[period]["paid_date"])

            app.fee_service.set_class_fee(class_id, 8000, 4, 2027)
            with mock.patch.object(app.fee_service, "datetime", April2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)
            april = fetch_one(
                app,
                "SELECT amount,status FROM student_fee_records "
                "WHERE student_id=? AND month_number=4 AND year=2027",
                (student_id,),
            )
            self.assertEqual(float(april["amount"]), 8000.0)
            self.assertEqual(april["status"], "Unpaid")


if __name__ == "__main__":
    unittest.main()
