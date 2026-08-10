from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]


class DeploymentReadinessHardeningSameV31Tests(unittest.TestCase):
    def test_backup_retry_worker_does_not_start_during_module_import(self):
        code = textwrap.dedent(
            r'''
            import json
            import os
            from pathlib import Path
            import threading

            created = []

            class FakeTimer:
                def __init__(self, delay, target):
                    self.delay = delay
                    self.target = target
                    self.started = False
                    self.cancelled = False
                    created.append(self)
                def start(self):
                    self.started = True
                def cancel(self):
                    self.cancelled = True
                    self.started = False
                def is_alive(self):
                    return self.started and not self.cancelled

            root = Path(os.environ["LOCALAPPDATA"]) / "HiraModelSchool" / "backups"
            root.mkdir(parents=True, exist_ok=True)
            (root / "automatic_backup_retry_state.json").write_text(
                json.dumps({
                    "pending": True,
                    "destinations": ["Internal automatic backup"],
                    "retry_attempt": 0,
                }),
                encoding="utf-8",
            )
            threading.Timer = FakeTimer
            import app.services.backup_scheduler as scheduler
            assert created == [], "Import started background backup work"
            assert scheduler.get_backup_scheduler_status()["scheduler_started"] is False
            result = scheduler.start_automatic_backup_scheduler()
            assert result["success"] is True
            assert len(created) == 1
            assert created[0].started is True
            '''
        )
        with tempfile.TemporaryDirectory(prefix="hms_scheduler_import_") as temp:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["LOCALAPPDATA"] = temp
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_run_starts_backup_scheduler_only_after_restore_and_database_startup(self):
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        restore = source.index("process_pending_restore_before_startup()")
        initialize = source.index("initialize_database()")
        activation = source.index("activate_due_coming_session_admissions()")
        scheduler = source.index("start_automatic_backup_scheduler()")
        self.assertLess(restore, initialize)
        self.assertLess(initialize, activation)
        self.assertLess(activation, scheduler)

    def test_single_instance_guard_is_acquired_before_licence_and_restore(self):
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        lock_position = source.index("acquire_application_lock()")
        licence_position = source.index("verify_installed_license()")
        restore_position = source.index("process_pending_restore_before_startup()")
        self.assertLess(lock_position, licence_position)
        self.assertLess(lock_position, restore_position)
        service = (
            ROOT / "app" / "services" / "single_instance_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("QLockFile", service)
        self.assertIn("tryLock(0)", service)
        self.assertIn("setStaleLockTime(0)", service)

    def test_sqlite_connections_have_a_deployment_safe_busy_timeout(self):
        code = textwrap.dedent(
            r'''
            from app.database import get_connection
            connection = get_connection(automatic_backup=False)
            try:
                value = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
                assert value >= 30000, value
            finally:
                connection.close()
            '''
        )
        with tempfile.TemporaryDirectory(prefix="hms_busy_timeout_") as temp:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["LOCALAPPDATA"] = temp
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_backup_page_is_registered_by_its_own_registration_method(self):
        source = (ROOT / "app" / "main_window.py").read_text(encoding="utf-8")
        backup_start = source.index("def _register_backup_management_page")
        realtime_start = source.index("def _register_real_time_finance_page")
        income_start = source.index("def _register_income_management_page")
        backup_body = source[backup_start:realtime_start]
        realtime_body = source[realtime_start:income_start]
        self.assertIn("BackupManagementPage", backup_body)
        self.assertIn("PAGE_BACKUP_MANAGEMENT", backup_body)
        self.assertNotIn("BackupManagementPage", realtime_body)
        self.assertNotIn("PAGE_BACKUP_MANAGEMENT", realtime_body)

    def test_appearance_asset_failure_is_nonfatal(self):
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        loader_start = source.index("def load_global_stylesheet")
        gate_start = source.index("def run_authentication_gate")
        loader = source[loader_start:gate_start]
        self.assertIn("OSError", loader)
        self.assertIn("UnicodeError", loader)

    def test_student_search_does_not_render_the_entire_school_without_criteria(self):
        source = (
            ROOT / "app" / "modules" / "student_management.py"
        ).read_text(encoding="utf-8")
        page_start = source.index("class SearchStudentPage")
        next_page = source.index("class ViewStudentDetailsPage")
        page_source = source[page_start:next_page]
        self.assertIn("Enter Search Details", page_source)
        self.assertIn("if not any(", page_source)
        self.assertIn("Student Search Error", page_source)

    def test_full_school_fee_screen_renders_bounded_pages_but_keeps_full_report(self):
        source = (
            ROOT / "app" / "modules" / "full_school_fee_list.py"
        ).read_text(encoding="utf-8")
        self.assertIn("FULL_SCHOOL_DISPLAY_PAGE_SIZE = 100", source)
        self.assertIn("def _show_previous_page", source)
        self.assertIn("def _show_next_page", source)
        self.assertIn("page_rows = display_rows[start_index:end_index]", source)
        self.assertIn("for status_row in self._get_display_rows()", source)


    def test_expense_search_renders_bounded_pages_but_prints_full_result(self):
        source = (
            ROOT / "app" / "modules" / "search_expense.py"
        ).read_text(encoding="utf-8")
        self.assertIn("EXPENSE_DISPLAY_PAGE_SIZE = 100", source)
        self.assertIn("page_rows = rows[start_index:end_index]", source)
        self.assertIn("def _show_previous_page", source)
        self.assertIn("def _show_next_page", source)
        self.assertIn("for row in self.current_rows", source)

    def test_hidden_heavy_pages_defer_data_loading_until_navigation(self):
        statistics = (
            ROOT / "app" / "modules" / "statistics_management.py"
        ).read_text(encoding="utf-8")
        income = (
            ROOT / "app" / "modules" / "income_management.py"
        ).read_text(encoding="utf-8")
        realtime = (
            ROOT / "app" / "modules" / "real_time_finance.py"
        ).read_text(encoding="utf-8")
        backup = (
            ROOT / "app" / "modules" / "backup_management.py"
        ).read_text(encoding="utf-8")
        main_window = (ROOT / "app" / "main_window.py").read_text(encoding="utf-8")

        statistics_constructor = statistics[
            statistics.index("class StatisticsManagementPage"):
            statistics.index("    def reset_page", statistics.index("class StatisticsManagementPage"))
        ]
        income_constructor = income[
            income.index("class IncomeManagementPage"):
            income.index("    def reset_page", income.index("class IncomeManagementPage"))
        ]
        realtime_constructor = realtime[
            realtime.index("class RealTimeFinancePage"):
            realtime.index("    def reset_page", realtime.index("class RealTimeFinancePage"))
        ]
        backup_constructor = backup[
            backup.index("class BackupManagementPage"):
            backup.index("    def reset_page", backup.index("class BackupManagementPage"))
        ]
        self.assertNotIn("self.reset_page()", statistics_constructor)
        self.assertNotIn("self.reset_page()", income_constructor)
        self.assertNotIn("self.reset_page()", realtime_constructor)
        self.assertNotIn("self._load_saved_settings()", backup_constructor)
        self.assertIn("page.reset_page()", main_window[main_window.index("def _open_backup_management"):])

    def test_real_time_finance_uses_bounded_ui_pages_but_full_report_data(self):
        source = (
            ROOT / "app" / "modules" / "real_time_finance.py"
        ).read_text(encoding="utf-8")
        self.assertIn("REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE = 100", source)
        self.assertIn("def _render_fee_page", source)
        self.assertIn("def _render_expense_page", source)
        self.assertIn("def _show_next_fee_page", source)
        self.assertIn("def _show_next_expense_page", source)
        self.assertIn('summary["fee_payment_rows"]', source)
        self.assertIn('summary["expense_rows"]', source)

    def test_crash_reports_are_retained_with_a_bounded_count(self):
        code = textwrap.dedent(
            r'''
            import os
            from pathlib import Path
            from app.services.crash_reporter import write_crash_report

            for index in range(60):
                try:
                    raise RuntimeError(f"failure-{index}")
                except RuntimeError as error:
                    write_crash_report(
                        type(error), error, error.__traceback__, origin="test"
                    )
            folder = Path(os.environ["HMS_CRASH_REPORT_DIR"])
            reports = list(folder.glob("hira_crash_*.txt"))
            assert len(reports) == 50, len(reports)
            assert any("failure-59" in path.read_text(encoding="utf-8") for path in reports)
            '''
        )
        with tempfile.TemporaryDirectory(prefix="hms_crash_retention_") as temp:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["LOCALAPPDATA"] = temp
            env["HMS_CRASH_REPORT_DIR"] = str(Path(temp) / "crash")
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_deployment_preflight_checks_versions_assets_and_runtime_data(self):
        source = (ROOT / "tools" / "deployment_preflight.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('EXPECTED_APPLICATION_VERSION = "1.1.0"', source)
        self.assertIn("EXPECTED_SCHEMA_VERSION = 23", source)
        self.assertIn("runtime_data_in_package", source)
        self.assertIn("future_timestamps", source)
        self.assertIn("customer_signing_key", source)
        completed = subprocess.run(
            [sys.executable, "tools/deployment_preflight.py"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=45,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("DEPLOYMENT PREFLIGHT PASSED", completed.stdout)

    def test_installer_cleans_stale_program_files_before_copying_new_build(self):
        source = (
            ROOT / "installer" / "HiraModelSchoolInstaller.iss"
        ).read_text(encoding="utf-8")
        self.assertIn("[InstallDelete]", source)
        self.assertNotIn('Name: "{app}\\*"', source)
        self.assertIn('Type: files; Name: "{app}\\{#MyAppExeName}"', source)
        self.assertIn('Type: filesandordirs; Name: "{app}\\_internal"', source)
        self.assertLess(source.index("[InstallDelete]"), source.index("[Files]"))


if __name__ == "__main__":
    unittest.main()
