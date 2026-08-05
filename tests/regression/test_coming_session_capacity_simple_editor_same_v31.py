"""Regression contract for the simplified capacity editor."""

from __future__ import annotations

import unittest
from pathlib import Path


class ComingSessionCapacitySimpleEditorSameV31Tests(unittest.TestCase):
    def test_capacity_editor_is_buttonless_and_flat(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (
            root / "app" / "modules" / "coming_session_capacity_report.py"
        ).read_text(encoding="utf-8")

        self.assertIn("QAbstractSpinBox", source)
        self.assertIn("QAbstractSpinBox.ButtonSymbols.NoButtons", source)
        self.assertIn("capacity_input.setFrame(False)", source)
        self.assertIn("capacity_input.setMinimumWidth(76)", source)
        self.assertIn("capacity_input.setFixedHeight(30)", source)
        self.assertIn("setDefaultSectionSize(40)", source)

    def test_capacity_calculation_and_persistence_hooks_remain(self) -> None:
        root = Path(__file__).resolve().parents[2]
        source = (
            root / "app" / "modules" / "coming_session_capacity_report.py"
        ).read_text(encoding="utf-8")

        self.assertIn("capacity_input.valueChanged.connect", source)
        self.assertIn("report_service.save_class_capacities(payload)", source)
        self.assertIn(
            'row["available_seats"] = int(value) - int(row["expected_total"])',
            source,
        )
        self.assertIn('"Over Capacity" if int(row["available_seats"]) < 0', source)


if __name__ == "__main__":
    unittest.main()
