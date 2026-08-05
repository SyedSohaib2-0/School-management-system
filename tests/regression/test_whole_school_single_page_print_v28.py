from __future__ import annotations

from pathlib import Path
import unittest

from app.services import class_service


class WholeSchoolSinglePagePrintV28Tests(unittest.TestCase):
    @staticmethod
    def _students(count: int, start: int = 1):
        return [
            {
                "admission_number": f"H{1500 + number}",
                "roll_number": number,
                "released_roll_number": None,
                "student_status": "Active",
                "student_name": f"STUDENT TEST NAME {number}",
                "father_name": f"FATHER TEST NAME {number}",
                "failure_count": 0,
            }
            for number in range(start, start + count)
        ]

    def test_forty_student_class_uses_compact_single_page_layout(self):
        html = class_service.build_whole_school_print_html(
            [("NURSERY A", self._students(40))]
        )

        self.assertEqual(html.count("<tr>"), 41)
        self.assertIn("Total Active Students: 40", html)
        self.assertIn("font-size: 6.5pt;", html)
        self.assertIn("padding: 1px 2px;", html)
        self.assertIn("margin: 0 0 3px 0;", html)
        self.assertNotIn("page-break-before: always;", html)

    def test_each_following_class_starts_on_a_new_page(self):
        html = class_service.build_whole_school_print_html(
            [
                ("NURSERY A", self._students(40)),
                ("KG A", self._students(40, start=41)),
            ]
        )

        self.assertEqual(html.count("page-break-before: always;"), 1)
        self.assertEqual(html.count("Total Active Students: 40"), 2)


    def test_qt_document_renders_forty_students_on_one_a4_page(self):
        try:
            from PySide6.QtCore import QSizeF
            from PySide6.QtGui import QGuiApplication, QPageSize, QTextDocument
        except ImportError:
            self.skipTest("PySide6 is unavailable in the current test interpreter.")

        application = QGuiApplication.instance()
        created_application = application is None
        if created_application:
            application = QGuiApplication([])

        try:
            html = class_service.build_whole_school_print_html(
                [("NURSERY A", self._students(40))]
            )
            a4_points = QPageSize(QPageSize.A4).size(QPageSize.Point)
            millimetres_to_points = 72.0 / 25.4
            printable_width = a4_points.width() - (8.0 * millimetres_to_points)
            printable_height = a4_points.height() - (8.0 * millimetres_to_points)

            document = QTextDocument()
            document.setDocumentMargin(0)
            document.setPageSize(QSizeF(printable_width, printable_height))
            document.setHtml(html)

            self.assertEqual(document.pageCount(), 1)
        finally:
            if created_application:
                application.quit()

    def test_main_window_uses_a4_portrait_four_mm_margins(self):
        source = (
            Path(__file__).resolve().parents[2]
            / "app"
            / "main_window.py"
        ).read_text(encoding="utf-8")

        self.assertIn("QPageSize(QPageSize.A4)", source)
        self.assertIn("QPageLayout.Portrait", source)
        self.assertIn("QMarginsF(4.0, 4.0, 4.0, 4.0)", source)
        self.assertIn("QPageLayout.Millimeter", source)
        self.assertIn("document.setDocumentMargin(0)", source)


if __name__ == "__main__":
    unittest.main()
