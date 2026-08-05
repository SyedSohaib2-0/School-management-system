from __future__ import annotations

import os
import unittest


class PromotionUiIntegrationTests(unittest.TestCase):
    def test_promotion_dashboard_has_no_reverse_control(self):
        try:
            from PySide6.QtWidgets import QApplication, QPushButton
        except ImportError:
            self.skipTest("PySide6 is unavailable in the current test interpreter.")

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from app.modules.promotion_management import PromotionDashboardPage

        app = QApplication.instance() or QApplication([])

        class WindowStub:
            pages = {}

            def go_to_page(self, _name):
                return None

        page = PromotionDashboardPage(WindowStub())
        labels = {
            button.text().strip()
            for button in page.findChildren(QPushButton)
        }
        self.assertIn("Full Class Promotion", labels)
        self.assertNotIn("Single Record Promotion", labels)
        self.assertNotIn("Reverse Full Class Promotion", labels)
        self.assertFalse(any("Reverse" in label for label in labels))
        page.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
