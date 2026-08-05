from __future__ import annotations

from pathlib import Path
import sqlite3
import unittest

from tests.regression.support import IsolatedApplication, insert_class


class EncryptedBackupIntegrationTests(unittest.TestCase):
    def test_same_machine_and_portable_password_restore(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            password = "PortableRestore#2027"
            setup = app.backup_crypto_service.configure_backup_security(
                password,
                password,
            )
            self.assertTrue(setup["success"], setup)
            backup_path = app.root / "portable.hmsbackup"
            created = app.backup_crypto_service.create_encrypted_backup_from_database(
                str(app.database_path),
                str(backup_path),
                "integration-test",
            )
            self.assertTrue(created["success"], created)

            local_target = app.root / "local.db"
            local_result = app.backup_crypto_service.decrypt_encrypted_backup_to_path(
                str(backup_path),
                str(local_target),
            )
            self.assertTrue(local_result["success"], local_result)

            Path(app.backup_crypto_service.KEY_STORE_PATH).unlink()
            portable_target = app.root / "portable.db"
            portable_result = app.backup_crypto_service.decrypt_encrypted_backup_to_path(
                str(backup_path),
                str(portable_target),
                recovery_password=password,
            )
            self.assertTrue(portable_result["success"], portable_result)

            connection = sqlite3.connect(portable_target)
            try:
                class_name = connection.execute(
                    "SELECT class_name FROM classes WHERE id=?",
                    (class_id,),
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(class_name, "2A")


    def test_automatic_backup_cycle_has_no_usb_destination(self):
        with IsolatedApplication() as app:
            insert_class(app, "4", "A")
            password = "AutomaticCycle#2027"
            setup = app.backup_crypto_service.configure_backup_security(
                password,
                password,
            )
            self.assertTrue(setup["success"], setup)
            result = app.backup_service.run_automatic_backups()
            self.assertTrue(result["success"], result)
            destinations = [
                item.get("destination", "").lower()
                for item in result.get("results", [])
            ]
            self.assertTrue(any("internal" in item for item in destinations))
            self.assertFalse(any("usb" in item for item in destinations))
            internal_files = list(
                Path(app.backup_service.INTERNAL_BACKUP_DIRECTORY).glob(
                    "*.hmsbackup"
                )
            )
            self.assertEqual(len(internal_files), 1)


if __name__ == "__main__":
    unittest.main()
