from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_HEX_COLORS = {"#000", "#000000", "#fff", "#ffffff"}


PRINT_FUNCTIONS = {
    "app/services/class_service.py": {
        "build_whole_school_print_html",
        "build_student_list_print_html",
        "export_student_list_pdf",
    },
    "app/modules/student_management.py": {
        "_monochrome_print_image_base64",
        "_school_print_logo_data_uri",
        "build_professional_student_print_html",
    },
    "app/modules/fee_receipt.py": {
        "_build_receipt_html",
        "_build_paid_receipt_html",
        "_build_printable_paid_copy_html",
        "_render_paid_receipt_page",
    },
    "app/modules/fee_setting_reports.py": {"_build_page_html"},
    "app/modules/coming_session_capacity_report.py": {"_build_report_html"},
    "app/modules/month_range_fee_report.py": {"_build_page_html"},
    "app/modules/full_school_fee_list.py": {"_build_class_page_html"},
    "app/modules/search_expense.py": {"_build_expense_report_html"},
    "app/modules/income_management.py": {
        "_create_chart_image",
        "_build_income_report_html",
    },
    "app/modules/real_time_finance.py": {"_build_daily_finance_report_html"},
}


class MonochromePrintTemplatesV33Tests(unittest.TestCase):
    def test_all_print_templates_use_only_black_and_white(self):
        checked = []
        for relative_path, function_names in PRINT_FUNCTIONS.items():
            path = PROJECT_ROOT / relative_path
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            found_names = set()
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name not in function_names:
                    continue
                found_names.add(node.name)
                function_source = ast.get_source_segment(source, node) or ""
                for color in re.findall(r"#[0-9A-Fa-f]{3,8}", function_source):
                    self.assertIn(
                        color.lower(),
                        ALLOWED_HEX_COLORS,
                        f"Colored print style {color} remains in {relative_path}:{node.name}",
                    )
                self.assertIsNone(
                    re.search(
                        r"(?:color|background(?:-color)?)\s*:\s*"
                        r"(?:red|blue|green|yellow|gr[ae]y)\b",
                        function_source,
                        flags=re.IGNORECASE,
                    ),
                    f"Named colored print style remains in {relative_path}:{node.name}",
                )
                checked.append((relative_path, node.name))
            self.assertEqual(
                found_names,
                function_names,
                f"The print-template audit list is stale for {relative_path}",
            )

        self.assertGreaterEqual(len(checked), 17)

    def test_print_images_are_converted_to_one_bit_black_and_white(self):
        student_source = (PROJECT_ROOT / "app/modules/student_management.py").read_text(
            encoding="utf-8"
        )
        income_source = (PROJECT_ROOT / "app/modules/income_management.py").read_text(
            encoding="utf-8"
        )
        for source in (student_source, income_source):
            self.assertIn("QImage.Format.Format_Mono", source)
            self.assertIn("Qt.ImageConversionFlag.MonoOnly", source)


if __name__ == "__main__":
    unittest.main()
