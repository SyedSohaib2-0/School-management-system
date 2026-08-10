from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, insert_class, insert_student


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _RaisingCursor:
    def execute(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic read failure")


class _TrackedConnection:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return _RaisingCursor()

    def close(self):
        self.closed = True


class DeploymentThirdPassTechnicalHardeningSameV31Tests(unittest.TestCase):
    def test_timezone_aware_payment_uses_stored_local_calendar_date(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="TZ3101",
                roll_number=1,
            )
            connection = app.connect()
            try:
                for receipt, timestamp, amount in (
                    ("310001", "2026-04-01T00:30:00+05:00", 1200.0),
                    ("310002", "2027-03-31T23:30:00-05:00", 800.0),
                ):
                    connection.execute(
                        """
                        INSERT INTO fee_payments(
                            receipt_no, student_id, payment_date, total_amount,
                            received_by, created_at, payment_status
                        ) VALUES (?, ?, ?, ?, 'test', ?, 'Completed')
                        """,
                        (receipt, student_id, timestamp, amount, timestamp),
                    )
                connection.commit()
            finally:
                connection.close()

            income_service = importlib.import_module("app.services.income_service")
            april = income_service.get_income_report_snapshot(2026, 4)
            march = income_service.get_income_report_snapshot(2026, 3)
            self.assertEqual(april["summary"]["collected_fee"], 1200.0)
            self.assertEqual(march["summary"]["collected_fee"], 800.0)
            self.assertIn(2026, income_service.get_available_income_session_years())

            april_rows = app.real_time_finance_service.get_fee_payments_for_date(
                "2026-04-01"
            )
            march_rows = app.real_time_finance_service.get_fee_payments_for_date(
                "2027-03-31"
            )
            self.assertEqual([row["receipt_no"] for row in april_rows], ["310001"])
            self.assertEqual([row["receipt_no"] for row in march_rows], ["310002"])

    def test_income_month_labels_are_locale_independent_english(self):
        with IsolatedApplication() as app:
            income_service = importlib.import_module("app.services.income_service")
            rows = income_service.get_monthly_income_comparison(2026)
            self.assertEqual(rows[0]["month_name"], "April")
            self.assertEqual(rows[0]["short_month_name"], "Apr")
            self.assertEqual(rows[-1]["month_name"], "March")
            self.assertNotIn("calendar.month", (PROJECT_ROOT / "app/services/income_service.py").read_text(encoding="utf-8"))

    def test_expense_read_connections_close_when_sqlite_read_fails(self):
        with IsolatedApplication() as app:
            service = app.expense_service
            calls = (
                lambda: service.search_expenses_by_exact_date(2026, 4, 1),
                lambda: service.search_expenses_by_month(4, 2026),
                lambda: service.search_expenses_by_session_year(2026),
                lambda: service.get_available_expense_session_years(),
            )
            for call in calls:
                tracked = _TrackedConnection()
                with mock.patch.object(service, "get_connection", return_value=tracked):
                    with self.assertRaises(sqlite3.OperationalError):
                        call()
                self.assertTrue(tracked.closed)

    def test_damaged_backup_settings_recover_previous_valid_configuration(self):
        with IsolatedApplication() as app:
            settings_service = importlib.import_module(
                "app.services.backup_settings_service"
            )
            settings_service.load_backup_settings()

            first = settings_service.load_backup_settings()
            first["selected_folder_enabled"] = True
            first["selected_folder_path"] = str(app.root / "first-backup-folder")
            settings_service.save_backup_settings(first)

            second = dict(first)
            second["selected_folder_path"] = str(app.root / "second-backup-folder")
            settings_service.save_backup_settings(second)

            settings_path = Path(settings_service.SETTINGS_FILE_PATH)
            previous_path = Path(settings_service.SETTINGS_PREVIOUS_FILE_PATH)
            self.assertTrue(previous_path.is_file())
            settings_path.write_text('{"selected_folder_enabled": ', encoding="utf-8")

            recovered = settings_service.load_backup_settings()
            self.assertTrue(recovered["selected_folder_enabled"])
            self.assertEqual(
                recovered["selected_folder_path"],
                str(app.root / "first-backup-folder"),
            )
            self.assertTrue(settings_path.is_file())
            self.assertTrue(list(settings_path.parent.glob("backup_settings.json.corrupt_*")))

    def test_damaged_backup_settings_evidence_is_preserved_before_defaults(self):
        with IsolatedApplication() as app:
            settings_service = importlib.import_module(
                "app.services.backup_settings_service"
            )
            settings_path = Path(settings_service.SETTINGS_FILE_PATH)
            previous_path = Path(settings_service.SETTINGS_PREVIOUS_FILE_PATH)
            settings_path.parent.mkdir(parents=True, exist_ok=True)
            settings_path.write_text("not-json-primary", encoding="utf-8")
            previous_path.write_text("not-json-previous", encoding="utf-8")

            loaded = settings_service.load_backup_settings()
            self.assertEqual(
                loaded,
                settings_service.normalise_backup_settings(
                    settings_service.DEFAULT_BACKUP_SETTINGS
                ),
            )
            self.assertTrue(list(settings_path.parent.glob("backup_settings.json.corrupt_*")))
            self.assertTrue(list(settings_path.parent.glob("backup_settings.json.previous.corrupt_*")))

    def test_malformed_backup_retry_state_is_quarantined_once(self):
        with IsolatedApplication() as app:
            scheduler = importlib.import_module("app.services.backup_scheduler")
            retry_path = Path(scheduler.RETRY_STATE_PATH)
            retry_path.parent.mkdir(parents=True, exist_ok=True)
            retry_path.write_text("{malformed", encoding="utf-8")

            scheduler._restore_retry_state_after_restart()

            self.assertFalse(retry_path.exists())
            self.assertTrue(list(retry_path.parent.glob("automatic_backup_retry_state.json.corrupt_*")))


    def test_core_read_services_close_connections_when_queries_fail(self):
        with IsolatedApplication() as app:
            student_service = app.student_service
            promotion_service = app.promotion_service
            fee_service = app.fee_service
            calls = (
                (student_service, lambda: student_service.generate_transfer_roll_number(1)),
                (student_service, lambda: student_service.get_student_by_id(1)),
                (student_service, lambda: student_service.search_students()),
                (student_service, lambda: student_service.class_has_any_students(1)),
                (student_service, lambda: student_service.count_students_by_status(1, "Active")),
                (student_service, lambda: student_service.load_students_by_status(1, "Active")),
                (promotion_service, lambda: promotion_service.get_latest_batch_for_class(1)),
            )
            for service, call in calls:
                tracked = _TrackedConnection()
                with mock.patch.object(service, "get_connection", return_value=tracked):
                    with self.assertRaises(sqlite3.OperationalError):
                        call()
                self.assertTrue(tracked.closed)

            tracked = _TrackedConnection()
            with mock.patch.object(fee_service, "get_connection", return_value=tracked), mock.patch.object(
                fee_service, "ensure_fee_records_generated", return_value=None
            ):
                with self.assertRaises(sqlite3.OperationalError):
                    fee_service.student_has_pending_fee(1)
            self.assertTrue(tracked.closed)

    def test_unknown_retry_destination_retries_all_enabled_destinations(self):
        with IsolatedApplication() as app:
            scheduler = importlib.import_module("app.services.backup_scheduler")
            retry_path = Path(scheduler.RETRY_STATE_PATH)
            retry_path.parent.mkdir(parents=True, exist_ok=True)
            retry_path.write_text(
                '{"pending": true, "destinations": ["Old removed destination"], "retry_attempt": 2}',
                encoding="utf-8",
            )
            scheduler._backup_pending = False
            scheduler._pending_destinations = set()
            scheduler._scheduler_started = False

            scheduler._restore_retry_state_after_restart()

            self.assertTrue(scheduler._backup_pending)
            self.assertIsNone(scheduler._pending_destinations)
            self.assertEqual(scheduler._retry_attempt, 2)
            self.assertTrue(retry_path.exists())


    def test_source_package_manifest_matches_current_corrected_files(self):
        manifest_path = PROJECT_ROOT / "SOURCE_PACKAGE_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["application_version"], "1.1.0")
        self.assertTrue(str(manifest.get("patch", "")).strip())
        self.assertEqual(manifest["file_count"], len(manifest["files"]))
        generated_prefixes = (
            "build/",
            "dist/",
            "installer_output/",
            "venv/",
        )
        for item in manifest["files"]:
            normalized_path = str(item["path"]).replace("\\", "/")
            self.assertFalse(
                normalized_path.startswith(generated_prefixes),
                normalized_path,
            )
            path = PROJECT_ROOT / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            data = path.read_bytes()
            self.assertEqual(len(data), item["size_bytes"], item["path"])
            self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"], item["path"])

    def test_post_import_licence_verification_is_exception_guarded(self):
        source = (PROJECT_ROOT / "run.py").read_text(encoding="utf-8")
        segment = source.split(
            "# Verify the newly imported licence independently before", 1
        )[1].split("# Apply a staged database restore", 1)[0]
        self.assertIn("try:", segment)
        self.assertIn("except Exception as error:", segment)
        self.assertIn("No school database was opened", segment)
        self.assertIn("return", segment)


if __name__ == "__main__":
    unittest.main()
