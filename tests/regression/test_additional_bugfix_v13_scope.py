from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def admission_values(class_id: int, admission_number: str, roll: int, **overrides):
    today = date.today()
    values = {
        "admission_number": admission_number,
        "roll_number": roll,
        "class_id": class_id,
        "student_name": f"STUDENT {admission_number}",
        "father_name": "TEST FATHER",
        "father_cnic": "1234512345671",
        "father_contact_number": "+920000000000",
        "school_leaving_certificate_submitted": True,
        "admission_year": today.year,
        "admission_month": today.month,
        "admission_day": today.day,
        "admission_time": "00:00:00",
        "gender": "Male",
        "date_of_birth": "01-01-2015",
        "created_by": "v13-regression",
    }
    values.update(overrides)
    return values


def update_from_row(app: IsolatedApplication, student_id: int, **overrides):
    row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
    values = {
        "student_id": student_id,
        "admission_number": row["admission_number"],
        "roll_number": int(row["roll_number"]),
        "class_id": int(row["class_id"]),
        "student_name": row["student_name"],
        "father_name": row["father_name"],
        "father_cnic": row["father_cnic"] or "",
        "father_contact_number": row["father_contact_number"],
        "student_status": row["student_status"],
        "school_leaving_certificate_submitted": bool(
            row["school_leaving_certificate_submitted"]
        ),
        "admission_year": int(row["admission_year"]),
        "admission_month": int(row["admission_month"]),
        "admission_day": int(row["admission_day"]),
        "admission_time": row["admission_time"],
        "gender": row["gender"] or "Male",
        "whatsapp_number": row["whatsapp_number"] or "",
        "current_address": row["current_address"] or "",
        "permanent_address": row["permanent_address"] or "",
        "date_of_birth": row["date_of_birth"],
        "form_b_submitted": bool(row["form_b_submitted"]),
        "birth_certificate_submitted": bool(row["birth_certificate_submitted"]),
        "updated_by": "v13-regression",
    }
    values.update(overrides)
    return app.student_service.update_student(**values)


def insert_monthly_fee(
    app: IsolatedApplication,
    *,
    student_id: int,
    class_id: int,
    session_year: str,
    month_name: str,
    month_number: int,
    year: int,
    amount: float,
):
    now = "2026-07-29 00:00:00"
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id, class_id, session_year, fee_type,
                month_name, month_number, year, amount, status,
                paid_date, created_at, updated_at
            ) VALUES (?, ?, ?, 'Monthly', ?, ?, ?, ?, 'Unpaid', NULL, ?, ?)
            """,
            (
                student_id,
                class_id,
                session_year,
                month_name,
                month_number,
                year,
                amount,
                now,
                now,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


class AdditionalBugFixV13ScopeTests(unittest.TestCase):
    def test_concurrent_admission_and_edit_duplicate_identity_are_atomic(self):
        with IsolatedApplication() as app:
            class_id = app.class_service.add_class("1", "A", student_capacity=10)

            def create(index: int):
                return app.student_service.add_student(
                    **admission_values(
                        class_id,
                        f"H13{index}",
                        index,
                        student_name="  Ali   Khan ",
                        father_name=" Ahmad   Khan ",
                    )
                )

            results = []
            errors = []
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(create, 1), pool.submit(create, 2)]
                for future in futures:
                    try:
                        results.append(future.result())
                    except Exception as error:  # one duplicate is required
                        errors.append(error)

            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("same Student Name", str(errors[0]))
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]),
                1,
            )
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_class_history")["n"]),
                1,
            )

            second = app.student_service.add_student(
                **admission_values(
                    class_id,
                    "H139",
                    3,
                    student_name="BILAL KHAN",
                    father_name="RASHID KHAN",
                    father_cnic="1111122222333",
                )
            )
            before = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (second,)))
            with self.assertRaisesRegex(ValueError, "normalized identity"):
                update_from_row(
                    app,
                    second,
                    student_name="ALI KHAN",
                    father_name="AHMAD KHAN",
                    father_cnic="1234512345671",
                )
            after = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (second,)))
            self.assertEqual(after, before)

    def test_receipt_number_and_multi_session_snapshots_are_consistent(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2101",
                roll_number=1,
            )
            first = insert_monthly_fee(
                app,
                student_id=student_id,
                class_id=class_id,
                session_year="2025-2026",
                month_name="March",
                month_number=3,
                year=2026,
                amount=1000,
            )
            second = insert_monthly_fee(
                app,
                student_id=student_id,
                class_id=class_id,
                session_year="2026-2027",
                month_name="April",
                month_number=4,
                year=2026,
                amount=1200,
            )
            result = app.fee_service.process_payment(student_id, [first, second])
            reprint = app.fee_service.get_completed_payment_by_receipt_number(
                result["receipt_no"]
            )
            self.assertEqual(result["receipt_no"], reprint["receipt_no"])
            self.assertEqual(result["session_year"], "MULTIPLE SESSIONS")
            self.assertEqual(reprint["session_year"], "MULTIPLE SESSIONS")
            self.assertEqual(
                {item["session_year"] for item in reprint["paid_items"]},
                {"2025-2026", "2026-2027"},
            )

            receipt_source = (PROJECT_ROOT / "app/modules/fee_receipt.py").read_text()
            paid_segment = receipt_source.split("def _build_paid_receipt_html", 1)[1].split(
                "def _build_printable_paid_copy_html", 1
            )[0]
            print_segment = receipt_source.split(
                "def _build_printable_paid_copy_html", 1
            )[1]
            for segment in (paid_segment, print_segment):
                self.assertIn('get("receipt_no")', segment)
                self.assertNotIn('get("tracking_id")', segment)
                self.assertNotIn("<td class=\"label\">Tracking ID", segment)
            self.assertEqual(receipt_source.count('>Receipt No.</td>'), 3)

            finance_rows = app.real_time_finance_service.get_fee_payments_for_date(
                datetime.now().date()
            )
            payment_row = next(row for row in finance_rows if row["receipt_no"] == result["receipt_no"])
            self.assertEqual(payment_row["session_year"], "MULTIPLE SESSIONS")

    def test_real_time_finance_uses_one_read_snapshot(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2301",
                roll_number=1,
            )
            fee_id = insert_monthly_fee(
                app,
                student_id=student_id,
                class_id=class_id,
                session_year="2026-2027",
                month_name="July",
                month_number=7,
                year=2026,
                amount=1000,
            )
            payment = app.fee_service.process_payment(student_id, [fee_id])

            original_get_connection = app.real_time_finance_service.get_connection
            with mock.patch.object(
                app.real_time_finance_service,
                "get_connection",
                wraps=original_get_connection,
            ) as connection_factory:
                summary = app.real_time_finance_service.get_real_time_finance_summary(
                    datetime.now().date()
                )
            self.assertEqual(connection_factory.call_count, 1)
            self.assertEqual(summary["fee_payment_count"], 1)
            self.assertEqual(summary["total_fee_collected"], 1000.0)

            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no, student_id, payment_date, total_amount,
                        received_by, created_at, payment_status
                    ) VALUES ('999998', ?, ?, 9999, 'test', ?, 'Cancelled')
                    """,
                    (
                        student_id,
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            after = app.real_time_finance_service.get_real_time_finance_summary(
                datetime.now().date()
            )
            self.assertEqual(after["fee_payment_count"], 1)
            self.assertEqual(after["fee_payment_rows"][0]["receipt_no"], payment["receipt_no"])

            source = (PROJECT_ROOT / "app/services/real_time_finance_service.py").read_text()
            self.assertIn('connection.execute("BEGIN")', source)
            self.assertIn("connection=connection", source)

    def test_income_summary_table_and_graph_share_one_snapshot(self):
        with IsolatedApplication() as app:
            income_service = importlib.import_module("app.services.income_service")
            income_service.get_connection = app.real_time_finance_service.get_connection
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2401",
                roll_number=1,
            )
            connection = app.connect()
            try:
                for receipt, payment_date, amount in (
                    ("240001", "2026-04-10 09:00:00", 25000),
                    ("240002", "2026-05-10 09:00:00", 30000),
                ):
                    connection.execute(
                        """
                        INSERT INTO fee_payments(
                            receipt_no, student_id, payment_date, total_amount,
                            received_by, created_at, payment_status
                        ) VALUES (?, ?, ?, ?, 'test', ?, 'Completed')
                        """,
                        (receipt, student_id, payment_date, amount, payment_date),
                    )
                connection.commit()
            finally:
                connection.close()
            app.expense_service.add_expense("2026-04-11", 8000, "April expense")
            app.expense_service.add_expense("2026-05-11", 7500, "May expense")

            original = income_service.get_connection
            with mock.patch.object(income_service, "get_connection", wraps=original) as factory:
                snapshot = income_service.get_income_report_snapshot(2026)
            self.assertEqual(factory.call_count, 1)
            summary = snapshot["summary"]
            rows = snapshot["monthly_rows"]
            self.assertEqual(summary["collected_fee"], 55000.0)
            self.assertEqual(summary["total_expenses"], 15500.0)
            self.assertEqual(summary["final_income"], 39500.0)
            april = next(row for row in rows if row["month_number"] == 4)
            may = next(row for row in rows if row["month_number"] == 5)
            self.assertEqual((april["collected_fee"], april["total_expenses"]), (25000.0, 8000.0))
            self.assertEqual((may["collected_fee"], may["total_expenses"]), (30000.0, 7500.0))

            ui_source = (PROJECT_ROOT / "app/modules/income_management.py").read_text()
            load_segment = ui_source.split("def _load_income_data", 1)[1].split(
                "def _update_kpis", 1
            )[0]
            self.assertIn("get_income_report_snapshot", load_segment)
            self.assertNotIn("get_income_summary(", load_segment)
            self.assertNotIn("get_monthly_income_comparison(", load_segment)

    def test_dob_is_mandatory_strict_and_edit_is_atomic(self):
        with IsolatedApplication() as app:
            class_id = app.class_service.add_class("1", "A")
            for invalid in ("", "00-00-0000", "5-9-2015", "2015-09-05", "31-02-2015"):
                values = admission_values(class_id, f"H25{len(invalid)}", 1)
                values["date_of_birth"] = invalid
                with self.assertRaises(ValueError):
                    app.student_service.add_student(**values)
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]), 0
            )

            valid_id = app.student_service.add_student(
                **admission_values(class_id, "H2501", 1, date_of_birth="05-09-2015")
            )
            self.assertEqual(
                fetch_one(app, "SELECT date_of_birth FROM students WHERE student_id=?", (valid_id,))[
                    "date_of_birth"
                ],
                "2015-09-05",
            )

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET date_of_birth=NULL WHERE student_id=?",
                    (valid_id,),
                )
                connection.commit()
            finally:
                connection.close()
            before = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (valid_id,)))
            with self.assertRaisesRegex(ValueError, "required"):
                update_from_row(app, valid_id, current_address="MUST NOT SAVE")
            after = dict(fetch_one(app, "SELECT * FROM students WHERE student_id=?", (valid_id,)))
            self.assertEqual(after, before)

            ui_source = (PROJECT_ROOT / "app/modules/student_management.py").read_text()
            self.assertGreaterEqual(ui_source.count('setInputMask(\n            "00-00-0000;_"'), 2)

    def test_name_length_limit_preserves_legacy_values_without_truncation(self):
        with IsolatedApplication() as app:
            class_id = app.class_service.add_class("1", "A", student_capacity=10)
            name_200 = "A" * 200
            father_200 = "B" * 200
            accepted = app.student_service.add_student(
                **admission_values(
                    class_id,
                    "H2701",
                    1,
                    student_name=name_200,
                    father_name=father_200,
                )
            )
            stored = fetch_one(app, "SELECT student_name, father_name FROM students WHERE student_id=?", (accepted,))
            self.assertEqual(len(stored["student_name"]), 200)
            self.assertEqual(len(stored["father_name"]), 200)

            with self.assertRaisesRegex(ValueError, "200 normalized characters"):
                app.student_service.add_student(
                    **admission_values(
                        class_id,
                        "H2702",
                        2,
                        student_name="C" * 201,
                    )
                )

            legacy = insert_student(
                app,
                class_id=class_id,
                admission_number="H2703",
                roll_number=3,
                name="L" * 201,
            )
            before = fetch_one(app, "SELECT student_name, current_address FROM students WHERE student_id=?", (legacy,))
            with self.assertRaisesRegex(ValueError, "200 normalized characters"):
                update_from_row(app, legacy, current_address="SHOULD NOT SAVE")
            after = fetch_one(app, "SELECT student_name, current_address FROM students WHERE student_id=?", (legacy,))
            self.assertEqual(after["student_name"], before["student_name"])
            self.assertEqual(after["current_address"], before["current_address"])
            self.assertEqual(len(after["student_name"]), 201)

            ui_source = (PROJECT_ROOT / "app/modules/student_management.py").read_text()
            self.assertGreaterEqual(ui_source.count("setMaxLength(200)"), 5)

    def test_class_list_pdf_stays_inside_a4_and_wraps_long_names(self):
        with IsolatedApplication() as app:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import cm
            from reportlab.platypus import Paragraph, Table as RealTable
            import reportlab.platypus as platypus

            captured = {}

            def table_spy(data, *args, **kwargs):
                captured["data"] = data
                captured["col_widths"] = kwargs.get("colWidths")
                captured["repeat_rows"] = kwargs.get("repeatRows")
                return RealTable(data, *args, **kwargs)

            rows = [
                {
                    "admission_number": f"H{index}",
                    "roll_number": index,
                    "released_roll_number": None,
                    "student_status": "Active",
                    "student_name": "A" * 200,
                    "father_name": "B" * 200,
                    "failure_count": 0,
                }
                for index in range(1, 55)
            ]
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "Class_1A_List"
                with mock.patch.object(platypus, "Table", side_effect=table_spy):
                    actual = app.class_service.export_student_list_pdf(
                        "1A", "Active Students", rows, str(target)
                    )
                actual_path = Path(actual)
                self.assertEqual(actual_path.suffix.lower(), ".pdf")
                self.assertTrue(actual_path.is_file())
                self.assertTrue(actual_path.read_bytes().startswith(b"%PDF"))

            widths = captured["col_widths"]
            self.assertEqual(captured["repeat_rows"], 1)
            self.assertLessEqual(sum(widths), A4[0] - (4 * cm))
            self.assertIsInstance(captured["data"][1][2], Paragraph)
            self.assertIsInstance(captured["data"][1][3], Paragraph)


if __name__ == "__main__":
    unittest.main()
