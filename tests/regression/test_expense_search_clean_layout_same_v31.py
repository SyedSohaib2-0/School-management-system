"""Regression contract for the non-overlapping Expense Search layout."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "app" / "modules" / "search_expense.py"


class ExpenseSearchCleanLayoutSameV31Tests(unittest.TestCase):
    def setUp(self):
        self.source = SOURCE.read_text(encoding="utf-8")

    def test_search_filters_use_independent_vertical_field_containers(self):
        self.assertIn('search_card.setObjectName("expenseSearchCard")', self.source)
        self.assertIn("self.filters_row = QHBoxLayout()", self.source)
        self.assertIn("def build_filter_field(label_text, editor, minimum_width):", self.source)
        self.assertNotIn("self.filters_grid = QGridLayout()", self.source)

    def test_irrelevant_fields_are_hidden_as_complete_units(self):
        self.assertIn("self.exact_date_field.setVisible(exact_date_required)", self.source)
        self.assertIn("self.session_year_field.setVisible(session_required)", self.source)
        self.assertIn("self.month_field.setVisible(month_required)", self.source)
        self.assertIn("self.session_information_label.setVisible(session_required)", self.source)

    def test_session_banner_has_reserved_height_and_alignment(self):
        self.assertIn("self.session_information_label.setMinimumHeight(38)", self.source)
        self.assertIn("Qt.AlignLeft | Qt.AlignVCenter", self.source)

    def test_mode_change_forces_geometry_refresh(self):
        self.assertIn("self.search_type_field.updateGeometry()", self.source)
        self.assertIn("self.session_information_label.updateGeometry()", self.source)

    def test_results_table_receives_remaining_vertical_space(self):
        self.assertIn("self.expense_table.setMinimumHeight(250)", self.source)
        self.assertIn("results_layout.addWidget(self.expense_table, 1)", self.source)

    def test_session_year_editor_is_plain_and_compact(self):
        self.assertIn("self.session_year_input.setButtonSymbols(QSpinBox.NoButtons)", self.source)
        self.assertIn("self.session_year_input.setAlignment(Qt.AlignCenter)", self.source)


if __name__ == "__main__":
    unittest.main()
