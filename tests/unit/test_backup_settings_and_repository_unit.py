from __future__ import annotations

from pathlib import Path
import unittest

from tests.regression.support import PROJECT_ROOT, IsolatedApplication


class BackupSettingsAndRepositoryUnitTests(unittest.TestCase):
    def test_legacy_usb_keys_are_ignored_and_not_persisted(self):
        with IsolatedApplication() as app:
            settings_service = __import__(
                "app.services.backup_settings_service",
                fromlist=["normalise_backup_settings"],
            )
            normalised = settings_service.normalise_backup_settings(
                {
                    "usb_enabled": True,
                    "usb_path": "X:/obsolete",
                    "usb_retention": 99,
                    "selected_folder_enabled": True,
                    "selected_folder_path": "D:/backups",
                }
            )
            self.assertNotIn("usb_enabled", normalised)
            self.assertNotIn("usb_path", normalised)
            self.assertNotIn("usb_retention", normalised)
            self.assertEqual(normalised["selected_folder_path"], "D:/backups")

    def test_obsolete_repository_files_and_usb_implementation_are_absent(self):
        self.assertFalse((PROJECT_ROOT / "app/manage_classes.py").exists())
        self.assertFalse((PROJECT_ROOT / "HiraModelSchoolTest.spec").exists())
        production_files = [
            PROJECT_ROOT / "app/services/backup_service.py",
            PROJECT_ROOT / "app/services/backup_settings_service.py",
            PROJECT_ROOT / "app/modules/backup_management.py",
        ]
        for path in production_files:
            content = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("usb_backup_prefix", content, path)
            self.assertNotIn("create_manual_usb_backup", content, path)
            self.assertNotIn("_build_usb_tab", content, path)
            self.assertNotIn("usb_enabled", content, path)
            self.assertNotIn("usb_path", content, path)


    def test_promotion_reversal_is_not_exposed_or_implemented(self):
        promotion_module = (
            PROJECT_ROOT / "app" / "modules" / "promotion_management.py"
        ).read_text(encoding="utf-8")
        promotion_service = (
            PROJECT_ROOT / "app" / "services" / "promotion_service.py"
        ).read_text(encoding="utf-8")
        navigation_source = (
            PROJECT_ROOT / "app" / "navigation.py"
        ).read_text(encoding="utf-8")
        main_window_source = (
            PROJECT_ROOT / "app" / "main_window.py"
        ).read_text(encoding="utf-8")
        regression_docs = (
            PROJECT_ROOT / "docs" / "REGRESSION_TESTS.md"
        ).read_text(encoding="utf-8")

        forbidden_repository_tokens = (
            "reverse_full_class",
            "reverse_single_student",
            "Reverse Full Class Promotion",
            "PROMOTION_REVERSE",
            "FULL_CLASS_REVERSE",
            "promote_single_student",
            "SingleRecordPromotionPage",
            "PAGE_SINGLE_RECORD_PROMOTION",
            "TEST BANNER VISIBLE",
            "BANNER SHOULD BE VISIBLE NOW",
        )
        for source_path in (PROJECT_ROOT / "app").rglob("*.py"):
            source = source_path.read_text(encoding="utf-8")
            for token in forbidden_repository_tokens:
                self.assertNotIn(token, source, source_path)

        self.assertNotIn("Reverse Full Class Promotion", promotion_module)
        self.assertNotIn("ReverseFullClassPromotionPage", promotion_module)
        self.assertNotIn("reverse_full_class_batch", promotion_service)
        self.assertNotIn("reverse_single_student_promotion", promotion_service)
        self.assertNotIn("get_reversible_batch_for_class", promotion_service)
        self.assertNotIn("PAGE_REVERSE_FULL_CLASS_PROMOTION", navigation_source)
        self.assertNotIn("ReverseFullClassPromotionPage", main_window_source)
        self.assertNotIn("promote_single_student", promotion_service)
        self.assertNotIn("SingleRecordPromotionPage", promotion_module)
        self.assertNotIn("PAGE_SINGLE_RECORD_PROMOTION", navigation_source)
        self.assertNotIn("SingleRecordPromotionPage", main_window_source)
        self.assertNotIn("TEST BANNER VISIBLE", promotion_module)
        self.assertNotIn("BANNER SHOULD BE VISIBLE NOW", promotion_module)
        self.assertNotIn("single pass/fail promotions and reversals", regression_docs)
        self.assertNotIn("full-class all-pass promotion and collective reversal", regression_docs)
        self.assertIn("single-record promotion and promotion reversal remain unavailable", regression_docs)


if __name__ == "__main__":
    unittest.main()
