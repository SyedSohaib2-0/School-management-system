from __future__ import annotations

from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAY_FEE_SOURCE = PROJECT_ROOT / "app" / "modules" / "pay_fee.py"


class PayFeeSearchReceiptRoutingSameV31Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PAY_FEE_SOURCE.read_text(encoding="utf-8")

    def test_tracking_id_example_is_explicitly_a_placeholder(self):
        self.assertIn(
            '"Enter Tracking ID, e.g. HMS-000001"',
            self.source,
        )
        self.assertIn(
            "The example text is not an entered value.",
            self.source,
        )

    def test_receipt_has_a_visible_open_action(self):
        self.assertIn('"Open Receipt"', self.source)
        self.assertIn(
            "open_receipt_button.clicked.connect(\n"
            "            self._open_paid_receipt_by_number",
            self.source,
        )

    def test_receipt_only_main_search_routes_to_receipt_lookup(self):
        search_start = self.source.index("    def _search_students(self):")
        receipt_start = self.source.index(
            "    def _open_paid_receipt_by_number(self):"
        )
        search_body = self.source[search_start:receipt_start]

        routing = (
            "if receipt_number and not has_student_search_criteria:\n"
            "            self._open_paid_receipt_by_number()\n"
            "            return"
        )
        self.assertIn(routing, search_body)
        self.assertLess(
            search_body.index(routing),
            search_body.index("fee_service.search_students_for_fee("),
        )

    def test_receipt_routing_does_not_replace_student_criteria(self):
        search_start = self.source.index("    def _search_students(self):")
        receipt_start = self.source.index(
            "    def _open_paid_receipt_by_number(self):"
        )
        search_body = self.source[search_start:receipt_start]

        for criterion in (
            "tracking_id",
            "admission_number",
            "student_name",
            "father_cnic",
            "roll_number",
        ):
            self.assertIn(criterion, search_body)
        self.assertIn("not has_student_search_criteria", search_body)


if __name__ == "__main__":
    unittest.main()
