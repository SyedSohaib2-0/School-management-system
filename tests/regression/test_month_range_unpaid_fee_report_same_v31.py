from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest

from tests.regression.support import (
    IsolatedApplication,
    insert_class,
    insert_student,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def fee_ids_through(app, student_id: int, last_month: int) -> list[int]:
    connection = app.connect()
    try:
        rows = connection.execute(
            """
            SELECT id, month_number
            FROM student_fee_records
            WHERE student_id=? AND fee_type='Monthly' AND year=2026
              AND month_number BETWEEN 4 AND ?
            ORDER BY month_number,id
            """,
            (int(student_id), int(last_month)),
        ).fetchall()
        return [int(row["id"]) for row in rows]
    finally:
        connection.close()


class MonthRangeUnpaidFeeReportSameV31Tests(unittest.TestCase):
    def test_period_builder_follows_selected_direction_inside_session(self):
        with IsolatedApplication() as app:
            june_to_april = app.fee_service.build_month_range_periods(2026, 6, 4)
            self.assertEqual(
                [row["label"] for row in june_to_april],
                ["June 2026", "May 2026", "April 2026"],
            )

            april_to_june = app.fee_service.build_month_range_periods(2026, 4, 6)
            self.assertEqual(
                [row["label"] for row in april_to_june],
                ["April 2026", "May 2026", "June 2026"],
            )

            same_month = app.fee_service.build_month_range_periods(2026, 7, 7)
            self.assertEqual([row["label"] for row in same_month], ["July 2026"])

            january_to_december = app.fee_service.build_month_range_periods(2026, 1, 12)
            self.assertEqual(
                [row["label"] for row in january_to_december],
                ["January 2027", "December 2026"],
            )


    def test_august_admission_is_not_in_backward_june_to_april_range(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="MRBACK01",
                roll_number=1,
                name="AUGUST STUDENT",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET admission_date='2026-08-01 09:00:00',
                        admission_year=2026, admission_month=8, admission_day=1
                    WHERE student_id=?
                    """,
                    (student_id,),
                )
                connection.execute(
                    """
                    UPDATE student_class_history
                    SET effective_month=8, effective_year=2026,
                        change_datetime='2026-08-01 09:00:00'
                    WHERE student_id=? AND change_type='ADMISSION'
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            app.fee_service.ensure_fee_records_generated(
                student_id,
                as_of=datetime(2026, 8, 15, 10, 0, 0),
            )

            report = app.fee_service.get_month_range_unpaid_fee_report(
                2026,
                6,
                4,
                as_of=datetime(2026, 8, 15, 10, 0, 0),
            )
            self.assertEqual(report["period_label"], "June 2026 to April 2026")
            self.assertEqual(
                [row["label"] for row in report["periods"]],
                ["June 2026", "May 2026", "April 2026"],
            )
            self.assertEqual(report["rows"], [])

    def test_report_shows_any_student_with_one_or_more_unpaid_selected_months(self):
        with IsolatedApplication() as app:
            class_7a = insert_class(app, "7", "A")
            class_8a = insert_class(app, "8", "A")
            student_a = insert_student(
                app,
                class_id=class_7a,
                admission_number="MR001",
                roll_number=2,
                name="ALPHA STUDENT",
            )
            student_b = insert_student(
                app,
                class_id=class_7a,
                admission_number="MR002",
                roll_number=1,
                name="FULLY PAID STUDENT",
            )
            student_c = insert_student(
                app,
                class_id=class_8a,
                admission_number="MR003",
                roll_number=1,
                name="BETA STUDENT",
            )

            app.fee_service.set_class_fee(class_7a, 1000, 4, 2026)
            app.fee_service.set_class_fee(class_8a, 1200, 4, 2026)
            reference = datetime(2026, 8, 15, 10, 0, 0)
            for student_id in (student_a, student_b, student_c):
                app.fee_service.ensure_fee_records_generated(
                    student_id,
                    as_of=reference,
                )

            # A: June paid, July and August unpaid (April-May are also paid).
            app.fee_service.process_payment(student_a, fee_ids_through(app, student_a, 6))
            # B: all selected months paid, therefore omitted.
            app.fee_service.process_payment(student_b, fee_ids_through(app, student_b, 8))
            # C: only August remains unpaid.
            app.fee_service.process_payment(student_c, fee_ids_through(app, student_c, 7))

            before = app.connect()
            try:
                before_count = before.execute(
                    "SELECT COUNT(*) FROM student_fee_records"
                ).fetchone()[0]
            finally:
                before.close()

            report = app.fee_service.get_month_range_unpaid_fee_report(
                2026,
                6,
                8,
                as_of=reference,
            )

            self.assertTrue(report["report_read_only"])
            self.assertEqual(report["period_label"], "June 2026 to August 2026")
            self.assertEqual(report["student_count"], 2)
            self.assertEqual(report["class_count"], 2)
            self.assertEqual(report["unpaid_month_entry_count"], 3)
            self.assertEqual(
                [row["admission_number"] for row in report["rows"]],
                ["MR001", "MR003"],
            )
            self.assertEqual(
                report["rows"][0]["unpaid_months"],
                ["July 2026", "August 2026"],
            )
            self.assertEqual(report["rows"][0]["unpaid_count"], 2)
            self.assertEqual(report["rows"][1]["unpaid_months"], ["August 2026"])
            self.assertEqual(report["fee_setup_warnings"], [])

            after = app.connect()
            try:
                after_count = after.execute(
                    "SELECT COUNT(*) FROM student_fee_records"
                ).fetchone()[0]
            finally:
                after.close()
            self.assertEqual(after_count, before_count)

    def test_future_selected_months_are_not_counted_as_unpaid(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "6", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="MR010",
                roll_number=1,
            )
            app.fee_service.set_class_fee(class_id, 900, 4, 2026)
            reference = datetime(2026, 8, 15, 10, 0, 0)
            app.fee_service.ensure_fee_records_generated(student_id, as_of=reference)
            app.fee_service.process_payment(student_id, fee_ids_through(app, student_id, 8))

            report = app.fee_service.get_month_range_unpaid_fee_report(
                2026,
                6,
                10,
                as_of=reference,
            )
            self.assertEqual(report["rows"], [])
            self.assertEqual(
                [row["label"] for row in report["future_periods"]],
                ["September 2026", "October 2026"],
            )

    def test_a4_renderer_keeps_normal_classes_whole_and_splits_only_oversized_class(self):
        try:
            import os
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtWidgets import QApplication
            from app.modules.month_range_fee_report import MonthRangeFeeReportPage
        except (ImportError, ModuleNotFoundError):
            self.skipTest("PySide6 is unavailable in this test environment.")

        application = QApplication.instance() or QApplication([])

        class DummyMainWindow:
            def go_to_page(self, _page):
                return None

        page = MonthRangeFeeReportPage(DummyMainWindow())
        rows = []
        for class_name, count, class_id in (("7A", 5, 1), ("8A", 6, 2), ("9A", 90, 3)):
            for roll in range(1, count + 1):
                rows.append(
                    {
                        "student_id": class_id * 1000 + roll,
                        "class_id": class_id,
                        "class_level": class_name[:-1],
                        "section": class_name[-1],
                        "class_name": class_name,
                        "admission_number": f"P{class_id}{roll:03d}",
                        "roll_number": roll,
                        "student_name": f"STUDENT {roll}",
                        "failure_count": 0,
                        "student_status": "Active",
                        "unpaid_months": ["June 2026", "July 2026"],
                        "unpaid_count": 2,
                        "fee_setup_warning": False,
                        "report_read_only": True,
                    }
                )
        page.report_rows = rows
        page.report = {
            "session_label": "2026-2027",
            "period_label": "June 2026 to July 2026",
            "student_count": len(rows),
            "class_count": 3,
            "unpaid_month_entry_count": len(rows) * 2,
        }

        printer = page._prepare_printer()
        page_points = printer.pageRect(QPrinter.Unit.Point)
        planned = page._plan_report_pages(page_points.size())
        chunks = [chunk for planned_page in planned for chunk in planned_page]
        for small_class in ("7A", "8A"):
            matching = [chunk for chunk in chunks if chunk["class_name"] == small_class]
            self.assertEqual(len(matching), 1)
            self.assertFalse(matching[0]["continued"])
        large_chunks = [chunk for chunk in chunks if chunk["class_name"] == "9A"]
        self.assertGreater(len(large_chunks), 1)
        self.assertFalse(large_chunks[0]["continued"])
        self.assertTrue(all(chunk["continued"] for chunk in large_chunks[1:]))

        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "month_range_report.pdf"
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(pdf_path))
            page._render_report_to_printer(printer)
            self.assertTrue(pdf_path.is_file())
            self.assertGreater(pdf_path.stat().st_size, 0)

        page.deleteLater()
        application.processEvents()

    def test_ui_is_registered_and_contains_requested_controls_and_a4_grouping(self):
        navigation = (PROJECT_ROOT / "app/navigation.py").read_text(encoding="utf-8")
        main_window = (PROJECT_ROOT / "app/main_window.py").read_text(encoding="utf-8")
        dashboard = (PROJECT_ROOT / "app/modules/fee_management.py").read_text(encoding="utf-8")
        page = (PROJECT_ROOT / "app/modules/month_range_fee_report.py").read_text(encoding="utf-8")

        self.assertIn('PAGE_MONTH_RANGE_FEE_REPORT = "month_range_fee_report"', navigation)
        self.assertIn("MonthRangeFeeReportPage", main_window)
        self.assertIn('QPushButton("Sort by Month")', dashboard)
        for text in (
            'QLabel("Season:")',
            'QLabel("From Month:")',
            'QLabel("To Month:")',
            'QPushButton("Search")',
            'QPushButton("Refresh")',
            'QPushButton("Print Unpaid Fee List")',
            'QPushButton("Download Unpaid Fee List PDF")',
            '"Admission No"',
            '"Student Name"',
            '"Roll No"',
            '"Unpaid Months"',
            '"No. of Unpaid Months"',
        ):
            self.assertIn(text, page)
        self.assertIn("QPageSize.PageSizeId.A4", page)
        self.assertIn("QPageLayout.Orientation.Portrait", page)
        self.assertIn("def _plan_report_pages", page)
        self.assertIn("if self._chunks_fit([full_chunk], page_size_points)", page)
        self.assertIn('"continued": serial_start > 1', page)


    def test_large_ui_result_is_paginated_and_full_report_is_retained(self):
        try:
            import os
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PySide6.QtWidgets import QApplication
            from app.modules.month_range_fee_report import (
                MonthRangeFeeReportPage,
                REPORT_PAGE_SIZE,
            )
        except (ImportError, ModuleNotFoundError):
            self.skipTest("PySide6 is unavailable in this test environment.")

        application = QApplication.instance() or QApplication([])

        class DummyMainWindow:
            def go_to_page(self, _page):
                return None

        page = MonthRangeFeeReportPage(DummyMainWindow())
        rows = []
        for index in range(842):
            roll = index + 1
            rows.append(
                {
                    "student_id": roll,
                    "class_id": 1 + index // 40,
                    "class_level": str(1 + index // 80),
                    "section": "A",
                    "class_name": f"{1 + index // 80}A",
                    "admission_number": f"H{roll:04d}",
                    "roll_number": roll,
                    "student_name": f"STUDENT {roll}",
                    "failure_count": 0,
                    "student_status": "Active",
                    "unpaid_months": ["August 2026"],
                    "unpaid_count": 1,
                    "fee_setup_warning": False,
                    "report_read_only": True,
                }
            )
        page.report_rows = rows
        page.report = {
            "session_label": "2026-2027",
            "period_label": "August 2026",
            "student_count": len(rows),
            "class_count": 11,
            "unpaid_month_entry_count": len(rows),
        }

        page._populate_table()
        self.assertEqual(page.status_table.rowCount(), REPORT_PAGE_SIZE)
        self.assertEqual(len(page.report_rows), 842)
        self.assertEqual(page._page_count(), 9)
        self.assertTrue(page.next_page_button.isEnabled())
        self.assertFalse(page.previous_page_button.isEnabled())
        self.assertIn("1-100 of 842", page.page_label.text())

        page._show_next_page()
        self.assertEqual(page.current_page, 2)
        self.assertEqual(page.status_table.rowCount(), REPORT_PAGE_SIZE)
        self.assertTrue(page.previous_page_button.isEnabled())
        self.assertIn("101-200 of 842", page.page_label.text())

        page.current_page = page._page_count()
        page._populate_table()
        self.assertEqual(page.status_table.rowCount(), 42)
        self.assertFalse(page.next_page_button.isEnabled())
        self.assertIn("801-842 of 842", page.page_label.text())

        grouped_count = sum(len(group_rows) for _name, group_rows in page._group_rows())
        self.assertEqual(grouped_count, 842)

        page.deleteLater()
        application.processEvents()

    def test_month_range_ui_uses_background_search_and_avoids_full_table_resize(self):
        page_source = (
            PROJECT_ROOT / "app/modules/month_range_fee_report.py"
        ).read_text(encoding="utf-8")
        self.assertIn("class _MonthRangeSearchWorker(QRunnable)", page_source)
        self.assertIn("self._thread_pool.start(worker)", page_source)
        self.assertIn("REPORT_PAGE_SIZE = 100", page_source)
        self.assertIn("self.report_rows[start_index:end_index]", page_source)
        self.assertNotIn("resizeRowsToContents()", page_source)


if __name__ == "__main__":
    unittest.main()
