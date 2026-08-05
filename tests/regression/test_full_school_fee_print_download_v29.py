from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
import unittest

from tests.regression.support import (
    IsolatedApplication,
    insert_class,
    insert_student,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FullSchoolFeePrintDownloadV29Tests(unittest.TestCase):
    def test_full_school_rows_always_include_student_status_for_report_roll_display(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "7", "A")
            insert_student(
                app,
                class_id=class_id,
                admission_number="H2901",
                roll_number=1,
                name="REPORT STUDENT",
                father_name="REPORT FATHER",
            )

            missing_fee_rows = app.fee_service.get_full_school_fee_status_rows()
            self.assertEqual(len(missing_fee_rows), 1)
            self.assertEqual(missing_fee_rows[0]["student_status"], "Active")
            self.assertTrue(missing_fee_rows[0]["fee_setup_missing"])

            app.fee_service.set_class_fee(class_id, 7000, 4, 2026)
            configured_rows = app.fee_service.get_full_school_fee_status_rows()
            self.assertEqual(len(configured_rows), 1)
            self.assertEqual(configured_rows[0]["student_status"], "Active")
            self.assertFalse(configured_rows[0]["fee_setup_missing"])

    def test_print_and_pdf_paths_use_one_class_per_page_renderer_without_ui_changes(self):
        source = (
            PROJECT_ROOT / "app/modules/full_school_fee_list.py"
        ).read_text(encoding="utf-8")

        self.assertIn('"Print Full School List"', source)
        self.assertIn('"Download Full School List PDF"', source)
        self.assertIn("def _render_report_to_printer", source)
        self.assertEqual(source.count("self._render_report_to_printer(printer)"), 2)
        self.assertIn("QPageSize.PageSizeId.A4", source)
        self.assertIn("QPageLayout.Orientation.Landscape", source)
        self.assertIn("printer.newPage()", source)
        self.assertIn("def _create_fitted_class_document", source)
        self.assertIn("page_size_points.height() / document_height", source)
        self.assertIn("def _compact_pending_items", source)

    def test_pdf_renderer_keeps_large_classes_complete_on_separate_pages(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtWidgets import QApplication
            from app.modules.full_school_fee_list import FullSchoolFeeListPage
        except (ImportError, ModuleNotFoundError):
            self.skipTest("PySide6 is unavailable in this test environment.")

        application = QApplication.instance() or QApplication([])

        class DummyMainWindow:
            def go_to_page(self, _page):
                return None

        page = FullSchoolFeeListPage(DummyMainWindow())
        rows = []
        for class_name, count, class_id in (
            ("7A", 40, 1),
            ("8A", 85, 2),
        ):
            for index in range(1, count + 1):
                rows.append(
                    {
                        "student_id": class_id * 1000 + index,
                        "class_id": class_id,
                        "class_level": class_name[:-1],
                        "section": class_name[-1],
                        "class_name": class_name,
                        "admission_number": f"H{class_id}{index:03d}",
                        "roll_number": index,
                        "student_name": f"STUDENT NUMBER {index}",
                        "father_name": f"FATHER NUMBER {index}",
                        "father_cnic": "-",
                        "failure_count": 0,
                        "student_status": "Active",
                        "last_paid": "July 2026",
                        "unpaid_items": [
                            "August 2026",
                            "September 2026",
                            "October 2026",
                            "November 2026",
                            "December 2026",
                            "January 2027",
                            "February 2027",
                            "March 2027",
                        ],
                        "monthly_unpaid_items": [],
                        "monthly_unpaid_count": 8,
                        "fee_status_text": "8",
                        "fee_color_status": "red",
                        "fee_setup_missing": False,
                        "simulated_missing_count": 0,
                        "report_read_only": True,
                    }
                )
        page.status_rows = rows

        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "full_school_fee_list.pdf"
            printer = page._prepare_printer()
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(pdf_path))
            page._render_report_to_printer(printer)

            self.assertTrue(pdf_path.is_file())
            self.assertGreater(pdf_path.stat().st_size, 0)
            page_count = len(re.findall(rb"/Type\s*/Page\b", pdf_path.read_bytes()))
            self.assertEqual(page_count, 2)

        page.deleteLater()
        application.processEvents()


if __name__ == "__main__":
    unittest.main()
