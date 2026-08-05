from __future__ import annotations

import importlib.util
import os
import unittest

from tests.regression.support import IsolatedApplication


PYSIDE6_AVAILABLE = importlib.util.find_spec("PySide6") is not None


class _MainWindowStub:
    def go_to_page(self, _page):
        return None


@unittest.skipUnless(
    PYSIDE6_AVAILABLE,
    "PySide6 is unavailable in the current test interpreter.",
)
class BackupManagementUiIntegrationTests(unittest.TestCase):
    def test_backup_management_exposes_only_supported_external_tabs(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        with IsolatedApplication():
            application = QApplication.instance() or QApplication([])
            module = __import__(
                "app.modules.backup_management",
                fromlist=["BackupManagementPage"],
            )
            page = module.BackupManagementPage(_MainWindowStub())
            labels = [
                page.backup_tabs.tabText(index)
                for index in range(page.backup_tabs.count())
            ]
            self.assertEqual(labels, ["Selected Folder", "Google Drive"])
            self.assertFalse(hasattr(page, "usb_tab"))
            self.assertFalse(hasattr(page, "usb_table"))
            page.deleteLater()
            application.processEvents()


if __name__ == "__main__":
    unittest.main()
