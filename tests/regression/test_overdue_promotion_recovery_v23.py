from __future__ import annotations

from datetime import datetime
import importlib
import sqlite3
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
        value = cls(2027, 3, 20, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class OverduePromotionRecoveryV23Tests(unittest.TestCase):
    @staticmethod
    def _set_fee(app, class_id: int, amount: float, session_start_year: int) -> None:
        app.fee_service.set_class_fee(
            class_id=class_id,
            monthly_fee=amount,
            effective_from_month=4,
            effective_from_year=session_start_year,
            created_by="v23-test",
            change_reason="V23 overdue-promotion regression",
        )

    @staticmethod
    def _add_coming_student(app, class_id: int, admission_number: str) -> int:
        with mock.patch.object(app.student_service, "datetime", March2027DateTime):
            return app.student_service.add_student(
                admission_number=admission_number,
                roll_number=1,
                class_id=class_id,
                student_name="COMING SESSION STUDENT",
                father_name="COMING FATHER",
                father_cnic="1234512345799",
                father_contact_number="+920000000000",
                school_leaving_certificate_submitted=True,
                admission_year=2027,
                admission_month=3,
                admission_day=20,
                admission_time="09:00:00",
                gender="Male",
                date_of_birth="01-01-2015",
                birth_certificate_submitted=True,
                created_by="v23-test",
                coming_session_admission=True,
            )

    def test_normal_promotion_records_april_effective_session_and_rejects_duplicate(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H9301", roll_number=1
            )
            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_2a, 1200, 2027)

            result = app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {student_id: "P"},
                "v23-test",
                as_of=datetime(2027, 3, 15, 10, 30),
            )
            self.assertEqual(result["promotion_mode"], "NORMAL")
            self.assertEqual(result["source_session_start_year"], 2026)
            self.assertEqual(result["target_session_start_year"], 2027)
            self.assertEqual(result["effective_date"], "2027-04-01")

            history = fetch_one(
                app,
                """
                SELECT effective_month,effective_year
                FROM student_class_history
                WHERE student_id=? AND change_type='PROMOTION'
                ORDER BY history_id DESC LIMIT 1
                """,
                (student_id,),
            )
            self.assertEqual((int(history["effective_month"]), int(history["effective_year"])), (4, 2027))
            batch = fetch_one(
                app,
                """
                SELECT source_session_start_year,target_session_start_year,
                       effective_date,promotion_mode
                FROM promotion_batches
                """,
            )
            self.assertEqual(
                tuple(batch),
                (2026, 2027, "2027-04-01", "NORMAL"),
            )
            cycle = fetch_one(
                app,
                "SELECT status,completed_batch_id FROM promotion_cycles",
            )
            self.assertEqual(cycle["status"], "COMPLETED")
            self.assertIsNotNone(cycle["completed_batch_id"])

            second_student = insert_student(
                app, class_id=class_1a, admission_number="H9304", roll_number=1
            )
            with self.assertRaisesRegex(ValueError, "no overdue promotion cycle|next normal promotion"):
                app.promotion_full_class_v2.promote_full_class(
                    class_1a,
                    {second_student: "F"},
                    "v23-test",
                    as_of=datetime(2027, 3, 20, 10, 30),
                )
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_batches")["n"]),
                1,
            )

    def test_overdue_paid_rows_remain_historical_and_unpaid_rows_follow_destination(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H9302", roll_number=1
            )
            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_1a, 1100, 2027)
            self._set_fee(app, class_2a, 1500, 2027)

            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2027, 7, 15, 9, 0)
            )
            before = fetch_all(
                app,
                """
                SELECT id,month_number,year,class_id,amount,status
                FROM student_fee_records WHERE student_id=? ORDER BY year,month_number
                """,
                (student_id,),
            )
            previous_session_ids = [
                int(row["id"])
                for row in before
                if (int(row["year"]), int(row["month_number"])) <= (2027, 3)
            ]
            current_session_ids = [
                int(row["id"])
                for row in before
                if (int(row["year"]), int(row["month_number"])) == (2027, 4)
            ]
            app.fee_service.process_payment(student_id, previous_session_ids)
            payment = app.fee_service.process_payment(student_id, current_session_ids)
            paid_april_before = dict(
                fetch_one(
                    app,
                    """
                    SELECT id,class_id,amount,status,paid_date
                    FROM student_fee_records
                    WHERE student_id=? AND month_number=4 AND year=2027
                    """,
                    (student_id,),
                )
            )
            receipt_before = dict(
                fetch_one(
                    app,
                    """
                    SELECT fr.id,fr.amount,fr.status,fr.class_id_snapshot,
                           fr.class_name_snapshot,fr.session_year_snapshot
                    FROM fee_receipts fr
                    JOIN student_fee_records r ON r.id=fr.student_fee_record_id
                    WHERE fr.payment_id=? AND r.month_number=4 AND r.year=2027
                    """,
                    (int(payment["payment_id"]),),
                )
            )

            result = app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {student_id: "P"},
                "v23-test",
                as_of=datetime(2027, 7, 15, 12, 0),
            )
            self.assertEqual(result["promotion_mode"], "OVERDUE")
            self.assertEqual(result["source_session_start_year"], 2026)
            self.assertEqual(result["target_session_start_year"], 2027)

            paid_april_after = dict(
                fetch_one(
                    app,
                    """
                    SELECT id,class_id,amount,status,paid_date
                    FROM student_fee_records
                    WHERE student_id=? AND month_number=4 AND year=2027
                    """,
                    (student_id,),
                )
            )
            receipt_after = dict(
                fetch_one(
                    app,
                    """
                    SELECT fr.id,fr.amount,fr.status,fr.class_id_snapshot,
                           fr.class_name_snapshot,fr.session_year_snapshot
                    FROM fee_receipts fr
                    JOIN student_fee_records r ON r.id=fr.student_fee_record_id
                    WHERE fr.payment_id=? AND r.month_number=4 AND r.year=2027
                    """,
                    (int(payment["payment_id"]),),
                )
            )
            self.assertEqual(paid_april_after, paid_april_before)
            self.assertEqual(receipt_after, receipt_before)

            unpaid = fetch_all(
                app,
                """
                SELECT month_number,class_id,amount,status
                FROM student_fee_records
                WHERE student_id=? AND year=2027 AND month_number BETWEEN 5 AND 7
                ORDER BY month_number
                """,
                (student_id,),
            )
            self.assertEqual(
                [(int(r["month_number"]), int(r["class_id"]), float(r["amount"]), r["status"]) for r in unpaid],
                [(5, class_2a, 1500.0, "Unpaid"), (6, class_2a, 1500.0, "Unpaid"), (7, class_2a, 1500.0, "Unpaid")],
            )

            app.fee_service.cancel_immediate_payment(
                int(payment["payment_id"]), "v23-test"
            )
            march = fetch_one(
                app,
                "SELECT class_id,amount,status FROM student_fee_records WHERE student_id=? AND month_number=3 AND year=2027",
                (student_id,),
            )
            april = fetch_one(
                app,
                "SELECT class_id,amount,status FROM student_fee_records WHERE student_id=? AND month_number=4 AND year=2027",
                (student_id,),
            )
            self.assertEqual((int(march["class_id"]), float(march["amount"]), march["status"]), (class_1a, 1000.0, "Paid"))
            self.assertEqual((int(april["class_id"]), float(april["amount"]), april["status"]), (class_2a, 1500.0, "Unpaid"))

    def test_overdue_failed_student_stays_and_unpaid_target_session_uses_source_fee(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_1a,
                admission_number="H9303",
                roll_number=1,
                failure_count=1,
            )
            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_1a, 1150, 2027)

            result = app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {student_id: "F"},
                "v23-test",
                as_of=datetime(2027, 7, 20, 11, 0),
            )
            self.assertEqual(result["promotion_mode"], "OVERDUE")
            student = fetch_one(
                app,
                "SELECT class_id,failure_count,roll_number FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(int(student["class_id"]), class_1a)
            self.assertEqual(int(student["failure_count"]), 2)
            self.assertGreater(int(student["roll_number"]), 0)
            rows = fetch_all(
                app,
                """
                SELECT month_number,class_id,amount,status
                FROM student_fee_records
                WHERE student_id=? AND year=2027 AND month_number BETWEEN 4 AND 7
                ORDER BY month_number
                """,
                (student_id,),
            )
            self.assertEqual(
                [(int(r["month_number"]), int(r["class_id"]), float(r["amount"]), r["status"]) for r in rows],
                [(4, class_1a, 1150.0, "Unpaid"), (5, class_1a, 1150.0, "Unpaid"), (6, class_1a, 1150.0, "Unpaid"), (7, class_1a, 1150.0, "Unpaid")],
            )

    def test_cancelled_paid_failed_student_row_reopens_with_target_session_source_fee(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H9305", roll_number=1
            )
            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_1a, 1100, 2027)
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2027, 7, 15, 9, 0)
            )
            rows = fetch_all(
                app,
                "SELECT id,month_number,year FROM student_fee_records WHERE student_id=? ORDER BY year,month_number",
                (student_id,),
            )
            previous_session_ids = [
                int(row["id"])
                for row in rows
                if (int(row["year"]), int(row["month_number"])) <= (2027, 3)
            ]
            current_session_ids = [
                int(row["id"])
                for row in rows
                if (int(row["year"]), int(row["month_number"])) == (2027, 4)
            ]
            app.fee_service.process_payment(student_id, previous_session_ids)
            payment = app.fee_service.process_payment(student_id, current_session_ids)

            # A later fee revision must not mutate the historical paid April row.
            self._set_fee(app, class_1a, 1200, 2027)
            app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {student_id: "F"},
                "v23-test",
                as_of=datetime(2027, 7, 20, 11, 0),
            )
            paid_april = fetch_one(
                app,
                "SELECT amount,status FROM student_fee_records WHERE student_id=? AND month_number=4 AND year=2027",
                (student_id,),
            )
            self.assertEqual((float(paid_april["amount"]), paid_april["status"]), (1100.0, "Paid"))

            app.fee_service.cancel_immediate_payment(
                int(payment["payment_id"]), "v23-test"
            )
            reopened_april = fetch_one(
                app,
                "SELECT class_id,amount,status FROM student_fee_records WHERE student_id=? AND month_number=4 AND year=2027",
                (student_id,),
            )
            self.assertEqual(
                (int(reopened_april["class_id"]), float(reopened_april["amount"]), reopened_april["status"]),
                (class_1a, 1200.0, "Unpaid"),
            )

    def test_class10_post_march_paid_fee_is_preserved_then_non_due_if_cancelled(self):
        with IsolatedApplication() as app:
            class_10a = insert_class(app, "10", "A")
            student_id = insert_student(
                app, class_id=class_10a, admission_number="H9310", roll_number=1
            )
            self._set_fee(app, class_10a, 2000, 2026)
            self._set_fee(app, class_10a, 2200, 2027)
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2027, 7, 15, 9, 0)
            )
            rows = fetch_all(
                app,
                "SELECT id,month_number,year FROM student_fee_records WHERE student_id=? ORDER BY year,month_number",
                (student_id,),
            )
            ending_ids = [
                int(r["id"])
                for r in rows
                if (int(r["year"]), int(r["month_number"])) <= (2027, 3)
            ]
            app.fee_service.process_payment(student_id, ending_ids)
            april_id = int(
                fetch_one(
                    app,
                    "SELECT id FROM student_fee_records WHERE student_id=? AND month_number=4 AND year=2027",
                    (student_id,),
                )["id"]
            )
            april_payment = app.fee_service.process_payment(student_id, [april_id])

            result = app.promotion_full_class_v2.promote_full_class(
                class_10a,
                {student_id: "P"},
                "v23-test",
                as_of=datetime(2027, 7, 15, 12, 0),
            )
            self.assertEqual(result["fully_graduated_students"], 1)
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (student_id,))["student_status"],
                "Graduated",
            )
            april = fetch_one(
                app,
                "SELECT amount,status FROM student_fee_records WHERE id=?",
                (april_id,),
            )
            self.assertEqual((float(april["amount"]), april["status"]), (2200.0, "Paid"))
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=? AND year=2027 AND month_number BETWEEN 5 AND 7", (student_id,))["n"]),
                0,
            )

            app.fee_service.cancel_immediate_payment(
                int(april_payment["payment_id"]), "v23-test"
            )
            april = fetch_one(
                app,
                "SELECT amount,status FROM student_fee_records WHERE id=?",
                (april_id,),
            )
            self.assertEqual((float(april["amount"]), april["status"]), (0.0, "Waived"))
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (student_id,))["student_status"],
                "Graduated",
            )
            graduation = fetch_one(
                app,
                "SELECT completion_session_start_year,graduation_effective_date,status FROM graduation_records WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(tuple(graduation), (2026, "2027-04-01", "Graduated"))

    def test_overdue_completion_retries_matching_coming_session_activation(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            active_id = insert_student(
                app, class_id=class_1a, admission_number="H9320", roll_number=1
            )
            pending_id = self._add_coming_student(app, class_1a, "H9321")
            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_1a, 1100, 2027)

            service = importlib.import_module("app.services.coming_session_service")
            service.get_connection = lambda *args, **kwargs: app.connect()
            blocked = service.activate_due_coming_session_admissions(
                as_of=datetime(2027, 4, 5, 9, 0)
            )
            self.assertFalse(blocked["success"])
            self.assertEqual(
                fetch_one(app, "SELECT student_status FROM students WHERE student_id=?", (pending_id,))["student_status"],
                "Coming Session",
            )

            result = app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {active_id: "F"},
                "v23-test",
                as_of=datetime(2027, 7, 15, 12, 0),
            )
            self.assertTrue(result["coming_session_activation"]["success"])
            self.assertEqual(result["coming_session_activation"]["activated_count"], 1)
            rows = fetch_all(
                app,
                "SELECT student_id,student_status,roll_number FROM students WHERE class_id=? ORDER BY roll_number",
                (class_1a,),
            )
            self.assertEqual([int(r["student_id"]) for r in rows], [active_id, pending_id])
            self.assertEqual([int(r["roll_number"]) for r in rows], [1, 2])
            self.assertTrue(all(r["student_status"] == "Active" for r in rows))

    def test_oldest_unresolved_cycle_has_priority_even_during_later_january(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H9325", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2025-07-10 09:00:00',admission_year=2025,
                        admission_month=7,admission_day=10,admission_time='09:00:00'
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.execute(
                    "UPDATE student_class_history SET effective_month=7,effective_year=2025 WHERE student_id=?",
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            context = app.promotion_full_class_v2.get_promotion_context(
                class_1a, as_of=datetime(2028, 1, 15, 10, 0)
            )
            self.assertEqual(context["source_session_start_year"], 2025)
            self.assertEqual(context["target_session_start_year"], 2026)
            self.assertEqual(context["effective_date"], "2026-04-01")
            self.assertEqual(context["promotion_mode"], "OVERDUE")

    def test_strict_v23_triggers_reject_invalid_session_metadata(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H9326", roll_number=1
            )
            strict = sqlite3.connect(str(app.database_path))
            strict.create_function("HMS_TEST_FIXTURE_MODE", 0, lambda: 0)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "academic-session context"):
                    strict.execute(
                        """
                        INSERT INTO promotion_batches(
                            source_class_id,source_class_name,promotion_datetime,
                            source_session_start_year,target_session_start_year,
                            effective_date,promotion_mode,total_students,passed_students,
                            failed_students,graduated_students,created_by,is_reversed
                        ) VALUES(?,'1A','2027-07-01 10:00:00',2026,2028,
                                 '2027-04-01','OVERDUE',1,0,1,0,'test',0)
                        """,
                        (class_1a,),
                    )
                strict.rollback()
                with self.assertRaisesRegex(sqlite3.IntegrityError, "graduation academic-session context"):
                    strict.execute(
                        """
                        INSERT INTO graduation_records(
                            student_id,admission_number,last_class_id,last_class_name,
                            last_roll_number,graduation_date,completion_session_start_year,
                            graduation_effective_date,status,is_reversed
                        ) VALUES(?, 'H9326', ?, '1A', 1, '2027-07-01 10:00:00',
                                 2026, '2028-04-01', 'Graduated', 0)
                        """,
                        (student_id, class_1a),
                    )
                strict.rollback()
            finally:
                strict.close()

    def test_overdue_cohort_excludes_target_session_admission_and_recalculates_source_rolls(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            old_student_id = insert_student(
                app, class_id=class_1a, admission_number="H9327", roll_number=1
            )
            new_student_id = insert_student(
                app, class_id=class_1a, admission_number="H9328", roll_number=2
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2027-05-10 09:00:00',admission_year=2027,
                        admission_month=5,admission_day=10,admission_time='09:00:00'
                    WHERE student_id=?
                    """,
                    (new_student_id,),
                )
                connection.execute(
                    """
                    UPDATE student_class_history
                    SET effective_month=5,effective_year=2027,
                        change_datetime='2027-05-10 09:00:00'
                    WHERE student_id=? AND change_type='ADMISSION'
                    """,
                    (new_student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_1a, 1100, 2027)
            self._set_fee(app, class_2a, 1500, 2027)

            cohort = app.promotion_full_class_v2.get_promotion_cohort_students(
                class_1a, as_of=datetime(2027, 7, 15, 11, 0)
            )
            self.assertEqual(
                [int(row["student_id"]) for row in cohort], [old_student_id]
            )

            result = app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {old_student_id: "F"},
                "v23-test",
                as_of=datetime(2027, 7, 15, 12, 0),
            )
            self.assertEqual(result["total_students"], 1)
            self.assertEqual(result["failed_students"], 1)

            source_rows = fetch_all(
                app,
                """
                SELECT student_id,roll_number,failure_count
                FROM students WHERE class_id=? AND student_status='Active'
                ORDER BY roll_number
                """,
                (class_1a,),
            )
            self.assertEqual(
                [
                    (int(row["student_id"]), int(row["roll_number"]), int(row["failure_count"]))
                    for row in source_rows
                ],
                [(new_student_id, 1, 0), (old_student_id, 2, 1)],
            )
            self.assertEqual(
                int(
                    fetch_one(
                        app,
                        "SELECT COUNT(*) AS n FROM promotion_history WHERE student_id=?",
                        (new_student_id,),
                    )["n"]
                ),
                0,
            )
            failed_history = fetch_one(
                app,
                """
                SELECT promotion_result,old_roll_number,new_roll_number
                FROM promotion_history WHERE student_id=?
                """,
                (old_student_id,),
            )
            self.assertEqual(tuple(failed_history), ("F", 1, 2))

    def test_overdue_pass_excludes_target_session_admission_from_batch(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            class_2a = insert_class(app, "2", "A")
            old_student_id = insert_student(
                app, class_id=class_1a, admission_number="H9329", roll_number=1
            )
            new_student_id = insert_student(
                app, class_id=class_1a, admission_number="H9331", roll_number=2
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2027-05-10 09:00:00',admission_year=2027,
                        admission_month=5,admission_day=10,admission_time='09:00:00'
                    WHERE student_id=?
                    """,
                    (new_student_id,),
                )
                connection.execute(
                    """
                    UPDATE student_class_history
                    SET effective_month=5,effective_year=2027,
                        change_datetime='2027-05-10 09:00:00'
                    WHERE student_id=? AND change_type='ADMISSION'
                    """,
                    (new_student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            self._set_fee(app, class_1a, 1000, 2026)
            self._set_fee(app, class_1a, 1100, 2027)
            self._set_fee(app, class_2a, 1500, 2027)

            result = app.promotion_full_class_v2.promote_full_class(
                class_1a,
                {old_student_id: "P"},
                "v23-test",
                as_of=datetime(2027, 7, 15, 12, 0),
            )
            self.assertEqual(result["total_students"], 1)
            self.assertEqual(result["passed_students"], 1)
            self.assertEqual(
                (
                    int(fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (old_student_id,))["class_id"]),
                    int(fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (new_student_id,))["class_id"]),
                ),
                (class_2a, class_1a),
            )
            self.assertEqual(
                int(fetch_one(app, "SELECT roll_number FROM students WHERE student_id=?", (new_student_id,))["roll_number"]),
                1,
            )
            batch = fetch_one(
                app,
                "SELECT total_students,passed_students,failed_students FROM promotion_batches",
            )
            self.assertEqual(tuple(batch), (1, 1, 0))
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_history WHERE student_id=?", (new_student_id,))["n"]),
                0,
            )

    def test_current_session_admission_has_no_overdue_cycle(self):
        with IsolatedApplication() as app:
            class_1a = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_1a, admission_number="H9330", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2027-04-10 09:00:00',admission_year=2027,
                        admission_month=4,admission_day=10,admission_time='09:00:00'
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.execute(
                    "UPDATE student_class_history SET effective_month=4,effective_year=2027 WHERE student_id=?",
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()
            connection = app.connect()
            try:
                with self.assertRaisesRegex(ValueError, "no overdue promotion cycle"):
                    importlib.import_module("app.services.promotion_cycle_service").resolve_promotion_context(
                        connection.cursor(), class_1a,
                        as_of=datetime(2027, 7, 15, 12, 0),
                    )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
