from __future__ import annotations

from datetime import datetime
import threading
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


def add_student_values(class_id: int, admission_number: str, roll_number: int):
    return {
        "admission_number": admission_number,
        "roll_number": roll_number,
        "class_id": class_id,
        "student_name": f"Concurrent Student {roll_number}",
        "father_name": "Concurrent Father",
        "father_cnic": f"17301123456{roll_number:02d}"[-13:],
        "father_contact_number": "+920000000000",
        "school_leaving_certificate_submitted": True,
        "admission_year": 2026,
        "admission_month": 4,
        "admission_day": 1,
        "admission_time": "09:00:00",
        "gender": "Male",
        "date_of_birth": "01-01-2015",
        "created_by": "v12-regression",
    }


class AdditionalBugFixV12ScopeTests(unittest.TestCase):
    def test_zero_class_fee_is_rejected_and_zero_individual_fee_remains_waived(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1201", roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 1200, 4, 2026)
            with self.assertRaisesRegex(ValueError, "greater than zero"):
                app.fee_service.set_class_fee(class_id, 0, 2, 2027)
            app.fee_service.set_student_fee_override(
                student_id, 0, 4, 2026, number_of_sessions=1
            )
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)
                snapshot = app.fee_service.get_student_fee_status_snapshot(student_id)

            rows = fetch_all(
                app,
                "SELECT month_number, year, amount, status, paid_date FROM student_fee_records "
                "WHERE student_id=? ORDER BY year, CASE WHEN month_number>=4 THEN month_number ELSE month_number+12 END",
                (student_id,),
            )
            self.assertEqual(len(rows), 12)
            self.assertTrue(all(float(row["amount"]) == 0 for row in rows))
            self.assertTrue(all(row["status"] == "Waived" for row in rows))
            self.assertTrue(all(row["paid_date"] is None for row in rows))
            self.assertEqual(snapshot["last_paid_month"], "None")
            self.assertEqual(snapshot["unpaid_months"], [])
            self.assertEqual(fetch_all(app, "SELECT * FROM fee_payments"), [])
            self.assertEqual(fetch_all(app, "SELECT * FROM fee_receipts"), [])

            app.fee_service.remove_student_fee_override(student_id, 2, 2026)
            rows = fetch_all(
                app,
                "SELECT month_number, year, amount, status, paid_date FROM student_fee_records "
                "WHERE student_id=? ORDER BY year, CASE WHEN month_number>=4 THEN month_number ELSE month_number+12 END",
                (student_id,),
            )
            earlier = [row for row in rows if (int(row["year"]), int(row["month_number"])) < (2027, 2)]
            reopened = [row for row in rows if (int(row["year"]), int(row["month_number"])) >= (2027, 2)]
            self.assertTrue(all(row["status"] == "Waived" for row in earlier))
            self.assertEqual([(int(r["month_number"]), r["status"], float(r["amount"])) for r in reopened], [
                (2, "Unpaid", 1200.0), (3, "Unpaid", 1200.0)
            ])

    def test_new_session_fee_does_not_reopen_previous_session_waivers(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1202", roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            app.fee_service.set_student_fee_override(
                student_id, 0, 4, 2026, number_of_sessions=1
            )
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)
            app.fee_service.set_class_fee(class_id, 1500, 4, 2027)
            with mock.patch.object(app.fee_service, "datetime", April2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)

            old_rows = fetch_all(
                app,
                "SELECT status, amount FROM student_fee_records WHERE student_id=? AND session_year='2026-2027'",
                (student_id,),
            )
            new_april = fetch_one(
                app,
                "SELECT status, amount FROM student_fee_records WHERE student_id=? AND month_number=4 AND year=2027",
                (student_id,),
            )
            self.assertTrue(old_rows)
            self.assertTrue(all(row["status"] == "Waived" and float(row["amount"]) == 0 for row in old_rows))
            self.assertEqual(new_april["status"], "Unpaid")
            self.assertEqual(float(new_april["amount"]), 1500.0)

    def test_class10_paid_or_waived_is_graduated_but_any_unpaid_is_partial(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "10", "A")
            cleared_id = insert_student(
                app, class_id=class_id, admission_number="H1210", roll_number=1,
                name="Cleared Class Ten",
            )
            pending_id = insert_student(
                app, class_id=class_id, admission_number="H1211", roll_number=2,
                name="Pending Class Ten",
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            app.fee_service.set_student_fee_override(
                cleared_id, 0, 4, 2026, number_of_sessions=1
            )
            app.fee_service.set_student_fee_override(
                pending_id, 0, 4, 2026, number_of_sessions=1
            )
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(cleared_id)
                app.fee_service.ensure_fee_records_generated(pending_id)
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE student_fee_records SET amount=500, status='Unpaid' "
                    "WHERE student_id=? AND month_number=3 AND year=2027",
                    (pending_id,),
                )
                connection.commit()
            finally:
                connection.close()

            with (
                mock.patch.object(app.fee_service, "datetime", March2027DateTime),
                mock.patch.object(app.promotion_full_class_v2, "datetime", March2027DateTime),
            ):
                app.promotion_full_class_v2.promote_full_class(
                    class_id, {cleared_id: "P", pending_id: "P"}, "v12-regression"
                )

            cleared = fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (cleared_id,))
            pending = fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (pending_id,))
            self.assertEqual(cleared["student_status"], "Graduated")
            self.assertEqual(pending["student_status"], "Partially Graduated")

    def test_fee_history_is_incremental_and_preserves_original_audit_metadata(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")
            times = ["2026-04-01 08:00:00", "2026-05-01 08:00:00", "2026-06-01 08:00:00"]
            with mock.patch.object(app.fee_service, "_format_now", side_effect=times):
                app.fee_service.set_class_fee(class_id, 1000, 4, 2026, created_by="APRIL ADMIN")
                app.fee_service.set_class_fee(class_id, 1100, 5, 2026, created_by="MAY ADMIN")
                april_before = fetch_one(
                    app,
                    "SELECT * FROM fee_history WHERE class_id=? AND effective_from_month=4 AND effective_from_year=2026",
                    (class_id,),
                )
                app.fee_service.set_class_fee(class_id, 1200, 6, 2026, created_by="JUNE ADMIN")

            april_after = fetch_one(
                app,
                "SELECT * FROM fee_history WHERE class_id=? AND effective_from_month=4 AND effective_from_year=2026",
                (class_id,),
            )
            may_event = fetch_one(
                app,
                "SELECT * FROM fee_history WHERE class_id=? AND effective_from_month=5 AND effective_from_year=2026",
                (class_id,),
            )
            self.assertEqual(int(april_after["id"]), int(april_before["id"]))
            self.assertEqual(april_after["created_at"], "2026-04-01 08:00:00")
            self.assertEqual(april_after["created_by"], "APRIL ADMIN")
            self.assertEqual(may_event["created_at"], "2026-05-01 08:00:00")
            self.assertEqual(may_event["created_by"], "MAY ADMIN")
            self.assertEqual(
                len(fetch_all(app, "SELECT * FROM fee_history WHERE class_id=?", (class_id,))), 2
            )

    def test_individual_fee_removal_requires_period_and_reverts_only_adjustable_rows(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1240", roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 1200, 4, 2026)
            app.fee_service.set_individual_fee(student_id, 500, 4, 2026)
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)

            fee_rows = fetch_all(
                app,
                "SELECT id, month_number, year FROM student_fee_records WHERE student_id=? AND "
                "((year=2026 AND month_number>=4) OR (year=2027 AND month_number=1)) "
                "ORDER BY year, CASE WHEN month_number>=4 THEN month_number ELSE month_number+12 END",
                (student_id,),
            )
            app.fee_service.process_payment(student_id, [int(row["id"]) for row in fee_rows])

            with self.assertRaisesRegex(ValueError, "effective month and academic session"):
                app.fee_service.clear_individual_fee(student_id)

            removed = app.fee_service.remove_student_fee_override(
                student_id, effective_from_month=2, session_start_year=2026,
                removed_by="v12-regression",
            )
            self.assertTrue(removed)
            january = fetch_one(
                app,
                "SELECT amount, status, paid_date FROM student_fee_records WHERE student_id=? AND month_number=1 AND year=2027",
                (student_id,),
            )
            feb_march = fetch_all(
                app,
                "SELECT month_number, amount, status, paid_date FROM student_fee_records WHERE student_id=? "
                "AND year=2027 AND month_number IN (2,3) ORDER BY month_number",
                (student_id,),
            )
            self.assertEqual(float(january["amount"]), 500.0)
            self.assertEqual(january["status"], "Paid")
            self.assertIsNotNone(january["paid_date"])
            self.assertEqual([(int(r["month_number"]), float(r["amount"]), r["status"]) for r in feb_march], [
                (2, 1200.0, "Unpaid"), (3, 1200.0, "Unpaid")
            ])
            self.assertTrue(all(r["paid_date"] is None for r in feb_march))
            override = fetch_one(
                app,
                "SELECT effective_to_month, effective_to_year, removed_effective_month, "
                "removed_effective_year, removed_by FROM student_fee_override WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual((int(override["effective_to_month"]), int(override["effective_to_year"])), (1, 2027))
            self.assertEqual((int(override["removed_effective_month"]), int(override["removed_effective_year"])), (2, 2027))
            self.assertEqual(override["removed_by"], "v12-regression")

    def test_individual_fee_removal_reopens_receipt_free_waived_rows(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "5", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="H1250", roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 1400, 4, 2026)
            app.fee_service.set_student_fee_override(student_id, 0, 4, 2026, number_of_sessions=1)
            with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                app.fee_service.ensure_fee_records_generated(student_id)
            before = fetch_one(
                app,
                "SELECT status, amount FROM student_fee_records WHERE student_id=? AND month_number=2 AND year=2027",
                (student_id,),
            )
            self.assertEqual((before["status"], float(before["amount"])), ("Waived", 0.0))
            app.fee_service.remove_student_fee_override(student_id, 2, 2026)
            after = fetch_one(
                app,
                "SELECT status, amount, paid_date FROM student_fee_records WHERE student_id=? AND month_number=2 AND year=2027",
                (student_id,),
            )
            self.assertEqual((after["status"], float(after["amount"])), ("Unpaid", 1400.0))
            self.assertIsNone(after["paid_date"])

    def test_capacity_reduction_is_atomic_and_concurrent_admission_cannot_overfill(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "6", "A")
            insert_student(app, class_id=class_id, admission_number="H1260", roll_number=1)
            app.class_service.update_class_capacity(class_id, 2)

            barrier = threading.Barrier(3)
            outcomes = []
            lock = threading.Lock()

            def reduce_capacity():
                barrier.wait()
                try:
                    result = app.class_service.update_class_capacity(class_id, 1)
                    value = ("capacity", result)
                except Exception as error:
                    value = ("capacity_error", str(error))
                with lock:
                    outcomes.append(value)

            def admit_student():
                barrier.wait()
                try:
                    student_id = app.student_service.add_student(**add_student_values(class_id, "H1261", 2))
                    value = ("admission", student_id)
                except Exception as error:
                    value = ("admission_error", str(error))
                with lock:
                    outcomes.append(value)

            threads = [threading.Thread(target=reduce_capacity), threading.Thread(target=admit_student)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=15)
                self.assertFalse(thread.is_alive())

            state = fetch_one(
                app,
                "SELECT c.student_capacity, COUNT(CASE WHEN s.student_status='Active' THEN 1 END) AS active_students "
                "FROM classes c LEFT JOIN students s ON s.class_id=c.id WHERE c.id=? GROUP BY c.id",
                (class_id,),
            )
            self.assertLessEqual(int(state["active_students"]), int(state["student_capacity"]))
            self.assertTrue(any(name.endswith("_error") for name, _ in outcomes))

            if int(state["active_students"]) == 1:
                app.class_service.update_class_capacity(class_id, 2)
                app.student_service.add_student(**add_student_values(class_id, "H1262", 2))
                state = fetch_one(
                    app,
                    "SELECT c.student_capacity, COUNT(CASE WHEN s.student_status='Active' THEN 1 END) AS active_students "
                    "FROM classes c LEFT JOIN students s ON s.class_id=c.id WHERE c.id=? GROUP BY c.id",
                    (class_id,),
                )
            original_capacity = int(state["student_capacity"])
            self.assertGreaterEqual(int(state["active_students"]), 2)
            with self.assertRaisesRegex(ValueError, "currently contains"):
                app.class_service.update_class_capacity(class_id, int(state["active_students"]) - 1)
            unchanged = fetch_one(app, "SELECT student_capacity FROM classes WHERE id=?", (class_id,))
            self.assertEqual(int(unchanged["student_capacity"]), original_capacity)


if __name__ == "__main__":
    unittest.main()
