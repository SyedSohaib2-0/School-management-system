from __future__ import annotations

import importlib
import os
from pathlib import Path
import sqlite3
import unittest

from tests.regression.support import IsolatedApplication, insert_class


class BackupRestoreRegressionTests(unittest.TestCase):
    def test_encrypted_backup_restore_reverse_and_portable_password_recovery(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")

            setup = app.backup_crypto_service.configure_backup_security(
                "StrongBackup#2026",
                "StrongBackup#2026",
            )
            self.assertTrue(setup["success"], setup)

            external = app.root / "approved_external"
            external.mkdir(parents=True, exist_ok=True)

            settings_service = importlib.import_module(
                "app.services.backup_settings_service"
            )
            settings = settings_service.load_backup_settings()
            settings["selected_folder_enabled"] = True
            settings["selected_folder_path"] = str(external)
            settings_service.save_backup_settings(settings)

            backup = app.backup_service.create_backup_in_destination(
                str(external),
                app.backup_service.SELECTED_FOLDER_BACKUP_PREFIX,
                10,
                "Regression backup",
            )
            self.assertTrue(backup["success"], backup)
            backup_path = Path(backup["backup_path"])
            self.assertTrue(backup_path.is_file())
            self.assertEqual(backup_path.suffix.lower(), ".hmsbackup")

            with backup_path.open("rb") as handle:
                self.assertEqual(
                    handle.read(len(app.backup_crypto_service.BACKUP_MAGIC)),
                    app.backup_crypto_service.BACKUP_MAGIC,
                )

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE classes SET student_capacity=35 WHERE id=?",
                    (class_id,),
                )
                connection.commit()
            finally:
                connection.close()

            staged = app.backup_service.stage_database_restore(str(backup_path))
            self.assertTrue(staged["success"], staged)
            processed = app.backup_service.process_pending_restore_before_startup()
            self.assertTrue(processed["success"], processed)
            self.assertTrue(processed["restore_processed"], processed)

            connection = app.connect()
            try:
                restored_capacity = connection.execute(
                    "SELECT student_capacity FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(int(restored_capacity), 40)

            reversible = app.backup_service.get_reversible_restore_operation()
            self.assertTrue(reversible["success"], reversible)
            reverse_staged = app.backup_service.reverse_last_restore()
            self.assertTrue(reverse_staged["success"], reverse_staged)
            reverse_processed = (
                app.backup_service.process_pending_restore_before_startup()
            )
            self.assertTrue(reverse_processed["success"], reverse_processed)
            self.assertTrue(
                reverse_processed["restore_processed"], reverse_processed
            )

            connection = app.connect()
            try:
                reversed_capacity = connection.execute(
                    "SELECT student_capacity FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(int(reversed_capacity), 35)

            key_store = Path(app.backup_crypto_service.KEY_STORE_PATH)
            self.assertTrue(key_store.is_file())
            key_store.unlink()

            wrong_target = app.root / "wrong_password.db"
            wrong = app.backup_crypto_service.decrypt_encrypted_backup_to_path(
                str(backup_path),
                str(wrong_target),
                recovery_password="WrongPassword#2026",
            )
            self.assertFalse(wrong["success"], wrong)

            portable_target = app.root / "portable_restore.db"
            portable = app.backup_crypto_service.decrypt_encrypted_backup_to_path(
                str(backup_path),
                str(portable_target),
                recovery_password="StrongBackup#2026",
            )
            self.assertTrue(portable["success"], portable)
            self.assertTrue(portable_target.is_file())

            connection = sqlite3.connect(portable_target)
            try:
                portable_name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(portable_name, "1A")

    def test_corrupt_encrypted_backup_is_rejected_without_touching_database(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            setup = app.backup_crypto_service.configure_backup_security(
                "StrongBackup#2026",
                "StrongBackup#2026",
            )
            self.assertTrue(setup["success"], setup)

            corrupt = app.root / "corrupt.hmsbackup"
            corrupt.write_bytes(app.backup_crypto_service.BACKUP_MAGIC + b"corrupt")

            before_hash = app.database_path.read_bytes()
            staged = app.backup_service.stage_database_restore(str(corrupt))
            self.assertFalse(staged["success"], staged)
            self.assertEqual(app.database_path.read_bytes(), before_hash)

            connection = app.connect()
            try:
                name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(name, "1A")


if __name__ == "__main__":
    unittest.main()
