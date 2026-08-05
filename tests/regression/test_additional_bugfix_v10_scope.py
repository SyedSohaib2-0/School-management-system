from __future__ import annotations

from datetime import date, datetime, timedelta
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    PROJECT_ROOT,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class FixedNowDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 12, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class MidnightSequenceDateTime(datetime):
    calls = 0
    connection_getter = None

    @classmethod
    def now(cls, tz=None):
        cls.calls += 1
        if cls.connection_getter is not None:
            connection = cls.connection_getter()
            if connection is None or not connection.in_transaction:
                raise AssertionError(
                    "Payment timestamp was captured before BEGIN IMMEDIATE."
                )
        value = (
            cls(2026, 7, 29, 23, 59, 59)
            if cls.calls == 1
            else cls(2026, 7, 30, 0, 0, 1)
        )
        return value if tz is None else value.replace(tzinfo=tz)


def add_student_arguments(class_id: int, admission_number: str, roll_number: int):
    return {
        "admission_number": admission_number,
        "roll_number": roll_number,
        "class_id": class_id,
        "student_name": f"Student {roll_number}",
        "father_name": f"Father {roll_number}",
        "father_cnic": f"17301123456{roll_number:02d}"[-13:],
        "father_contact_number": "+920000000000",
        "school_leaving_certificate_submitted": True,
        "gender": "Male",
        "date_of_birth": "01-01-2015",
        "created_by": "v10-regression",
    }


def update_student_from_row(app, student_id: int, **overrides):
    row = app.student_service.get_student_by_id(student_id)
    values = {
        "student_id": student_id,
        "admission_number": row["admission_number"],
        "roll_number": int(row["roll_number"]),
        "class_id": int(row["class_id"]),
        "student_name": row["student_name"],
        "gender": row["gender"] or "Male",
        "father_name": row["father_name"],
        "father_cnic": row["father_cnic"] or "",
        "father_contact_number": row["father_contact_number"],
        "whatsapp_number": row["whatsapp_number"] or "",
        "current_address": row["current_address"] or "Address",
        "permanent_address": row["permanent_address"] or "Address",
        "student_status": row["student_status"],
        "school_leaving_certificate_submitted": bool(
            row["school_leaving_certificate_submitted"]
        ),
        "form_b_submitted": bool(row["form_b_submitted"]),
        "birth_certificate_submitted": bool(row["birth_certificate_submitted"]),
        "date_of_birth": row["date_of_birth"] or "2015-01-01",
        "admission_year": int(row["admission_year"]),
        "admission_month": int(row["admission_month"]),
        "admission_day": int(row["admission_day"]),
        "admission_time": row["admission_time"] or "09:00:00",
        "updated_by": "v10-regression",
    }
    values.update(overrides)
    return app.student_service.update_student(**values)


def add_monthly_fee_row(app, student_id: int, class_id: int) -> int:
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id, class_id, session_year, fee_type,
                month_name, month_number, year, amount, status,
                paid_date, created_at, updated_at
            ) VALUES (?, ?, '2026-2027', 'Monthly', 'July', 7, 2026,
                      1000, 'Unpaid', NULL, ?, ?)
            """,
            (
                student_id,
                class_id,
                "2026-07-01 10:00:00",
                "2026-07-01 10:00:00",
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


class AdditionalBugFixV10ScopeTests(unittest.TestCase):
    def test_admission_number_lookup_is_case_and_space_insensitive(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number=" H123 ",
                roll_number=1,
            )

            for supplied in ("H123", "h123", " H123 "):
                student_rows = app.student_service.search_students(
                    admission_number=supplied,
                    include_archived=True,
                )
                fee_rows = app.fee_service.search_students_for_fee(
                    admission_number=supplied,
                )
                self.assertEqual(
                    [int(row["student_id"]) for row in student_rows],
                    [student_id],
                )
                self.assertEqual(
                    [int(row["student_id"]) for row in fee_rows],
                    [student_id],
                )

            self.assertEqual(
                app.student_service.search_students(
                    admission_number="H124",
                    include_archived=True,
                ),
                [],
            )
            self.assertFalse(
                app.student_service.is_admission_number_unique(" h123 ")
            )
            self.assertTrue(
                app.student_service.is_admission_number_unique(
                    " h123 ",
                    exclude_student_id=student_id,
                )
            )

            with mock.patch.object(app.student_service, "datetime", FixedNowDateTime):
                with self.assertRaisesRegex(ValueError, "already used"):
                    app.student_service.add_student(
                        **add_student_arguments(class_id, "h123", 2),
                        admission_year=2026,
                        admission_month=7,
                        admission_day=1,
                        admission_time="09:00:00",
                    )

    def test_roll_search_validation_and_released_only_marker(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            active_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H201",
                roll_number=1,
                name="Active Roll",
            )
            released_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H202",
                roll_number=2,
                name="Released Roll",
                status="Graduated",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET roll_number=-student_id, released_roll_number=1
                    WHERE student_id=?
                    """,
                    (released_id,),
                )
                connection.commit()
            finally:
                connection.close()

            both = app.student_service.search_students(
                class_id=class_id,
                roll_number="1",
                include_archived=True,
            )
            self.assertEqual(
                {int(row["student_id"]) for row in both},
                {active_id, released_id},
            )
            released_only = app.student_service.search_students(
                class_id=class_id,
                roll_number="1*",
                include_archived=True,
            )
            self.assertEqual(
                [int(row["student_id"]) for row in released_only],
                [released_id],
            )
            fee_released_only = app.fee_service.search_students_for_fee(
                class_id=class_id,
                roll_number="1*",
            )
            self.assertEqual(
                [int(row["student_id"]) for row in fee_released_only],
                [released_id],
            )

            for invalid in ("ABC", "1A", "-1", "0", "**", "1**"):
                with self.assertRaisesRegex(ValueError, "positive number"):
                    app.student_service.search_students(
                        class_id=class_id,
                        roll_number=invalid,
                        include_archived=True,
                    )
                with self.assertRaisesRegex(ValueError, "positive number"):
                    app.fee_service.search_students_for_fee(
                        class_id=class_id,
                        roll_number=invalid,
                    )

            blank_roll = app.student_service.search_students(
                admission_number="h201",
                roll_number="",
                include_archived=True,
            )
            self.assertEqual(
                [int(row["student_id"]) for row in blank_roll],
                [active_id],
            )

    def test_admission_and_birth_date_integrity_is_atomic(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")

            with mock.patch.object(app.student_service, "datetime", FixedNowDateTime):
                today_id = app.student_service.add_student(
                    **add_student_arguments(class_id, "H301", 1),
                    admission_year=2026,
                    admission_month=7,
                    admission_day=29,
                    admission_time="12:00:00",
                )
                historical_id = app.student_service.add_student(
                    **add_student_arguments(class_id, "H302", 2),
                    admission_year=2020,
                    admission_month=4,
                    admission_day=1,
                    admission_time="09:00:00",
                )
                self.assertGreater(today_id, 0)
                self.assertGreater(historical_id, 0)

                before = int(
                    fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]
                )
                for admission_number, year, month, day in (
                    ("H303", 2026, 7, 30),
                    ("H304", 2099, 1, 1),
                ):
                    with self.assertRaisesRegex(ValueError, "cannot be in the future"):
                        app.student_service.add_student(
                            **add_student_arguments(class_id, admission_number, 3),
                            admission_year=year,
                            admission_month=month,
                            admission_day=day,
                            admission_time="09:00:00",
                        )
                self.assertEqual(
                    int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]),
                    before,
                )

                for admission_number, dob in (
                    ("H305", "01-04-2020"),
                    ("H306", "01-01-2025"),
                ):
                    arguments = add_student_arguments(class_id, admission_number, 3)
                    arguments["date_of_birth"] = dob
                    with self.assertRaisesRegex(ValueError, "earlier than"):
                        app.student_service.add_student(
                            **arguments,
                            admission_year=2020,
                            admission_month=4,
                            admission_day=1,
                            admission_time="09:00:00",
                        )

            original = fetch_one(
                app,
                "SELECT student_name, date_of_birth, admission_date "
                "FROM students WHERE student_id=?",
                (historical_id,),
            )
            with self.assertRaisesRegex(ValueError, "earlier than"):
                update_student_from_row(
                    app,
                    historical_id,
                    student_name="Should Not Persist",
                    date_of_birth="01-04-2020",
                )
            unchanged = fetch_one(
                app,
                "SELECT student_name, date_of_birth, admission_date "
                "FROM students WHERE student_id=?",
                (historical_id,),
            )
            self.assertEqual(dict(unchanged), dict(original))

    def test_future_expenses_are_rejected_without_finance_changes(self):
        with IsolatedApplication() as app:
            today = date.today()
            past_id = app.expense_service.add_expense(
                today - timedelta(days=1),
                100,
                "Past expense",
            )
            today_id = app.expense_service.add_expense(
                today,
                200,
                "Today expense",
            )
            self.assertGreater(past_id, 0)
            self.assertGreater(today_id, 0)

            before = fetch_one(
                app,
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total "
                "FROM expenses",
            )
            for future_date in (
                today + timedelta(days=1),
                date(2099, 1, 1),
            ):
                with self.assertRaisesRegex(ValueError, "cannot be in the future"):
                    app.expense_service.add_expense(
                        future_date,
                        300,
                        "Future expense",
                    )
            after = fetch_one(
                app,
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total "
                "FROM expenses",
            )
            self.assertEqual(int(after["n"]), int(before["n"]))
            self.assertEqual(float(after["total"]), float(before["total"]))

    def test_payment_uses_one_timestamp_after_write_lock(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H401",
                roll_number=1,
            )
            fee_record_id = add_monthly_fee_row(app, student_id, class_id)

            with self.assertRaises(ValueError):
                app.fee_service.process_payment(student_id, [999999])
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM fee_payments")["n"]),
                0,
            )
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT status FROM student_fee_records WHERE id=?",
                    (fee_record_id,),
                )["status"],
                "Unpaid",
            )

            original_get_connection = app.fee_service.get_connection
            state = {"connection": None}

            def tracked_connection():
                connection = original_get_connection()
                state["connection"] = connection
                return connection

            MidnightSequenceDateTime.calls = 0
            MidnightSequenceDateTime.connection_getter = lambda: state["connection"]
            try:
                with (
                    mock.patch.object(
                        app.fee_service,
                        "get_connection",
                        side_effect=tracked_connection,
                    ),
                    mock.patch.object(
                        app.fee_service,
                        "datetime",
                        MidnightSequenceDateTime,
                    ),
                ):
                    result = app.fee_service.process_payment(
                        student_id,
                        [fee_record_id],
                        received_by="v10-regression",
                    )
            finally:
                MidnightSequenceDateTime.connection_getter = None

            self.assertEqual(MidnightSequenceDateTime.calls, 1)
            self.assertEqual(result["payment_date"], "29-07-2026")
            authoritative = "2026-07-29 23:59:59"

            payment = fetch_one(
                app,
                "SELECT * FROM fee_payments WHERE id=?",
                (result["payment_id"],),
            )
            fee_row = fetch_one(
                app,
                "SELECT * FROM student_fee_records WHERE id=?",
                (fee_record_id,),
            )
            receipt_row = fetch_one(
                app,
                "SELECT * FROM fee_receipts WHERE payment_id=?",
                (result["payment_id"],),
            )
            self.assertEqual(payment["payment_date"], authoritative)
            self.assertEqual(payment["created_at"], authoritative)
            self.assertEqual(fee_row["paid_date"], authoritative)
            self.assertEqual(fee_row["updated_at"], authoritative)
            self.assertEqual(receipt_row["created_at"], authoritative)

            reprint = app.fee_service.get_completed_payment_by_receipt_number(
                result["receipt_no"]
            )
            self.assertEqual(reprint["payment_date"], "29-07-2026")
            finance_rows = app.real_time_finance_service.get_fee_payments_for_date(
                "2026-07-29"
            )
            finance_row = next(
                row for row in finance_rows
                if row["receipt_no"] == result["receipt_no"]
            )
            self.assertEqual(finance_row["payment_date"], authoritative)

    def test_ui_layers_contain_v10_prevalidation_guards(self):
        student_ui = (PROJECT_ROOT / "app/modules/student_management.py").read_text(
            encoding="utf-8"
        )
        pay_fee_ui = (PROJECT_ROOT / "app/modules/pay_fee.py").read_text(
            encoding="utf-8"
        )
        fee_management_ui = (
            PROJECT_ROOT / "app/modules/fee_management.py"
        ).read_text(encoding="utf-8")
        expense_ui = (PROJECT_ROOT / "app/modules/expense_management.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("parse_roll_number_search(roll_number)", student_ui)
        self.assertIn("parse_roll_number_search(roll_number)", pay_fee_ui)
        self.assertIn("parse_roll_number_search(roll_number)", fee_management_ui)
        self.assertIn(".text().strip().upper()", student_ui)
        self.assertIn(".text().strip().upper()", pay_fee_ui)
        self.assertIn(".text().strip().upper()", fee_management_ui)
        self.assertIn("Admission Date cannot be in the future.", student_ui)
        self.assertIn("setMaximumDate", expense_ui)
        self.assertIn("Expense Date cannot be in the future.", expense_ui)

    @unittest.skipUnless(
        importlib.util.find_spec("PySide6") is not None,
        "PySide6 is unavailable in the current test interpreter.",
    )
    def test_invalid_student_search_roll_stops_before_service_call(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox

        with IsolatedApplication():
            from app.modules.student_management import SearchStudentPage
            from app.services import student_service

            application = QApplication.instance() or QApplication([])

            class MainWindow:
                def go_to_page(self, *_args, **_kwargs):
                    return None

            page = SearchStudentPage(MainWindow())
            page.roll_number_input.setText("ABC")
            with (
                mock.patch.object(student_service, "search_students") as search,
                mock.patch.object(QMessageBox, "warning"),
            ):
                page._perform_search()
            search.assert_not_called()
            page.deleteLater()
            application.processEvents()


if __name__ == "__main__":
    unittest.main()
