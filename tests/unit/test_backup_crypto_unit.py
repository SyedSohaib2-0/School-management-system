from __future__ import annotations

import json
from pathlib import Path
import unittest

from tests.regression.support import IsolatedApplication, insert_class


class BackupCryptoUnitTests(unittest.TestCase):
    def test_key_store_never_contains_plain_recovery_password(self):
        with IsolatedApplication() as app:
            password = "UniqueSalt#2027"
            result = app.backup_crypto_service.configure_backup_security(
                password,
                password,
            )
            self.assertTrue(result["success"], result)
            key_store_text = Path(
                app.backup_crypto_service.KEY_STORE_PATH
            ).read_text(encoding="utf-8")
            self.assertNotIn(password, key_store_text)
            key_store = json.loads(key_store_text)
            self.assertEqual(key_store["format_version"], 2)
            self.assertIn("protected_recovery_password", key_store)
            self.assertNotIn("protected_recovery_root", key_store)

    def test_each_backup_uses_unique_scrypt_recovery_salt(self):
        with IsolatedApplication() as app:
            insert_class(app, "1", "A")
            password = "UniqueSalt#2027"
            setup = app.backup_crypto_service.configure_backup_security(
                password,
                password,
            )
            self.assertTrue(setup["success"], setup)

            first = app.root / "first.hmsbackup"
            second = app.root / "second.hmsbackup"
            for target in (first, second):
                result = app.backup_crypto_service.create_encrypted_backup_from_database(
                    str(app.database_path),
                    str(target),
                    "unit-test",
                )
                self.assertTrue(result["success"], result)

            first_header = app.backup_crypto_service.read_encrypted_backup_header(
                str(first)
            )
            second_header = app.backup_crypto_service.read_encrypted_backup_header(
                str(second)
            )
            self.assertEqual(first_header["format_version"], 3)
            self.assertEqual(second_header["format_version"], 3)
            self.assertNotEqual(
                first_header["recovery_salt"],
                second_header["recovery_salt"],
            )
            self.assertNotEqual(
                first_header["nonce_prefix"],
                second_header["nonce_prefix"],
            )
            self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_tampered_header_is_rejected(self):
        with IsolatedApplication() as app:
            insert_class(app, "1", "A")
            password = "TamperCheck#2027"
            setup = app.backup_crypto_service.configure_backup_security(
                password,
                password,
            )
            self.assertTrue(setup["success"], setup)
            backup = app.root / "tamper.hmsbackup"
            created = app.backup_crypto_service.create_encrypted_backup_from_database(
                str(app.database_path),
                str(backup),
                "unit-test",
            )
            self.assertTrue(created["success"], created)
            payload = bytearray(backup.read_bytes())
            payload[20] ^= 1
            backup.write_bytes(payload)
            target = app.root / "tampered.db"
            result = app.backup_crypto_service.decrypt_encrypted_backup_to_path(
                str(backup),
                str(target),
                recovery_password=password,
            )
            self.assertFalse(result["success"], result)
            self.assertFalse(target.exists())


    def test_version_one_key_store_requires_and_completes_upgrade(self):
        with IsolatedApplication() as app:
            password = "LegacyUpgrade#2027"
            crypto = app.backup_crypto_service
            master_key = crypto.os.urandom(32)
            recovery_salt = crypto.os.urandom(16)
            recovery_root = crypto._derive_recovery_root(
                password,
                recovery_salt,
            )
            legacy_store = {
                "format_version": 1,
                "key_id": "legacy-key-id",
                "created_at": "2026-07-28 04:00:00",
                "recovery_salt": crypto._b64encode(recovery_salt),
                "scrypt_n": crypto.SCRYPT_N,
                "scrypt_r": crypto.SCRYPT_R,
                "scrypt_p": crypto.SCRYPT_P,
                "recovery_verifier": crypto._b64encode(
                    crypto._recovery_verifier(recovery_root)
                ),
                "protected_master_key": crypto._b64encode(
                    crypto._protect_bytes(master_key)
                ),
                "protected_recovery_root": crypto._b64encode(
                    crypto._protect_bytes(recovery_root)
                ),
            }
            crypto._atomic_write_json(crypto.KEY_STORE_PATH, legacy_store)
            status = crypto.get_backup_security_status()
            self.assertTrue(status["usable"], status)
            self.assertTrue(status["upgrade_required"], status)

            wrong = crypto.upgrade_backup_security("WrongUpgrade#2027")
            self.assertFalse(wrong["success"], wrong)
            self.assertEqual(
                json.loads(Path(crypto.KEY_STORE_PATH).read_text())["format_version"],
                1,
            )

            upgraded = crypto.upgrade_backup_security(password)
            self.assertTrue(upgraded["success"], upgraded)
            upgraded_store = json.loads(
                Path(crypto.KEY_STORE_PATH).read_text(encoding="utf-8")
            )
            self.assertEqual(upgraded_store["format_version"], 2)
            self.assertNotIn(password, Path(crypto.KEY_STORE_PATH).read_text())

            backup = app.root / "after-upgrade.hmsbackup"
            created = crypto.create_encrypted_backup_from_database(
                str(app.database_path),
                str(backup),
                "unit-test",
            )
            self.assertTrue(created["success"], created)
            header = crypto.read_encrypted_backup_header(str(backup))
            self.assertEqual(header["format_version"], 3)


if __name__ == "__main__":
    unittest.main()
