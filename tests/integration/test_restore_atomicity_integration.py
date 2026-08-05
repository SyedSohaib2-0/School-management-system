from __future__ import annotations

from pathlib import Path
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, insert_class


class RestoreAtomicityIntegrationTests(unittest.TestCase):
    def _prepare_backup(self, app):
        class_id = insert_class(app, "3", "A")
        password = "RestoreAtomic#2027"
        setup = app.backup_crypto_service.configure_backup_security(
            password,
            password,
        )
        self.assertTrue(setup["success"], setup)
        destination = app.root / "external"
        destination.mkdir()
        settings_service = __import__(
            "app.services.backup_settings_service",
            fromlist=["load_backup_settings"],
        )
        settings = settings_service.load_backup_settings()
        settings["selected_folder_enabled"] = True
        settings["selected_folder_path"] = str(destination)
        settings_service.save_backup_settings(settings)
        backup = app.backup_service.create_backup_in_destination(
            str(destination),
            app.backup_service.SELECTED_FOLDER_BACKUP_PREFIX,
            10,
            "Atomicity integration backup",
        )
        self.assertTrue(backup["success"], backup)
        return class_id, Path(backup["backup_path"])

    def test_history_failure_cancels_before_live_database_replacement(self):
        with IsolatedApplication() as app:
            class_id, backup_path = self._prepare_backup(app)
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE classes SET class_name='MUTATED' WHERE id=?",
                    (class_id,),
                )
                connection.commit()
            finally:
                connection.close()

            staged = app.backup_service.stage_database_restore(str(backup_path))
            self.assertTrue(staged["success"], staged)
            with mock.patch.object(
                app.backup_service,
                "record_restore_operation",
                side_effect=OSError("simulated state write failure"),
            ):
                result = app.backup_service.process_pending_restore_before_startup()
            self.assertFalse(result["success"], result)

            connection = app.connect()
            try:
                current_name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(current_name, "MUTATED")

    def test_database_replace_failure_rolls_back_prepared_restore_state(self):
        with IsolatedApplication() as app:
            class_id, backup_path = self._prepare_backup(app)
            staged = app.backup_service.stage_database_restore(str(backup_path))
            self.assertTrue(staged["success"], staged)
            original_state = app.backup_service.load_restore_state()
            real_replace = app.backup_service.os.replace

            def fail_only_live_replace(source, destination):
                if str(destination) == str(app.config.DATABASE_PATH):
                    raise OSError("simulated live replace failure")
                return real_replace(source, destination)

            with mock.patch.object(
                app.backup_service.os,
                "replace",
                side_effect=fail_only_live_replace,
            ):
                result = app.backup_service.process_pending_restore_before_startup()
            self.assertFalse(result["success"], result)
            self.assertEqual(
                app.backup_service.load_restore_state(),
                original_state,
            )
            connection = app.connect()
            try:
                current_name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(current_name, "3A")


if __name__ == "__main__":
    unittest.main()
