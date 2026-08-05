from __future__ import annotations

import base64
import copy
from pathlib import Path
import unittest
import sys
import types

# license_service imports winreg on Windows. A minimal stub is sufficient for
# testing package-parameter validation on non-Windows CI.
if "winreg" not in sys.modules:
    stub = types.ModuleType("winreg")
    stub.HKEY_LOCAL_MACHINE = object()
    stub.KEY_READ = 0
    stub.KEY_WOW64_64KEY = 0
    stub.OpenKey = lambda *a, **k: (_ for _ in ()).throw(OSError())
    stub.QueryValueEx = lambda *a, **k: (_ for _ in ()).throw(OSError())
    stub.CloseKey = lambda *a, **k: None
    sys.modules["winreg"] = stub

from app.services import auth_service
from app.licence_generator import licence_security


ROOT = Path(__file__).resolve().parents[2]


class DeploymentSecondPassHardeningSameV31Tests(unittest.TestCase):
    def test_auth_digest_rejects_unbounded_iterations_before_pbkdf(self):
        salt = base64.urlsafe_b64encode(b"1" * 16).decode("ascii")
        digest = base64.urlsafe_b64encode(b"2" * 32).decode("ascii")
        stored = f"pbkdf2_sha256$999999999999${salt}${digest}"
        original = auth_service.hashlib.pbkdf2_hmac
        called = []
        def forbidden(*args, **kwargs):
            called.append(True)
            raise AssertionError("PBKDF must not run for unsafe parameters")
        auth_service.hashlib.pbkdf2_hmac = forbidden
        try:
            self.assertFalse(auth_service._verify_secret("password", stored))
        finally:
            auth_service.hashlib.pbkdf2_hmac = original
        self.assertEqual(called, [])

    def test_auth_digest_rejects_oversized_decoded_hash_before_pbkdf(self):
        salt = base64.urlsafe_b64encode(b"1" * 16).decode("ascii")
        digest = base64.urlsafe_b64encode(b"2" * 4096).decode("ascii")
        stored = f"pbkdf2_sha256$390000${salt}${digest}"
        original = auth_service.hashlib.pbkdf2_hmac
        called = []
        auth_service.hashlib.pbkdf2_hmac = lambda *a, **k: called.append(True)
        try:
            self.assertFalse(auth_service._verify_secret("password", stored))
        finally:
            auth_service.hashlib.pbkdf2_hmac = original
        self.assertEqual(called, [])

    def test_valid_current_auth_digest_still_verifies(self):
        stored = auth_service._hash_secret("Correct#Password")
        self.assertTrue(auth_service._verify_secret("Correct#Password", stored))
        self.assertFalse(auth_service._verify_secret("Wrong#Password", stored))

    def test_signing_key_package_rejects_unsafe_scrypt_before_derivation(self):
        package = licence_security._load_key_package()
        damaged = copy.deepcopy(package)
        damaged["key_derivation"]["n"] = 2 ** 30
        with self.assertRaisesRegex(ValueError, "unsafe or unsupported"):
            licence_security._derive_decryption_key("anything", damaged)

    def test_signing_key_package_requires_expected_algorithm(self):
        package = licence_security._load_key_package()
        damaged = copy.deepcopy(package)
        damaged["key_derivation"]["algorithm"] = "PBKDF2"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            licence_security._derive_decryption_key("anything", damaged)

    def test_prelogin_exit_explicitly_flushes_pending_backups(self):
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        gate = source.index("if not run_authentication_gate():")
        window = source.index("main_window = MainWindow()")
        branch = source[gate:window]
        self.assertIn("complete_pending_backups_before_exit()", branch)

    def test_startup_dependencies_have_controlled_failure_paths(self):
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("Licence Verification Failed", source)
        self.assertIn("Database Restore Could Not Be Checked", source)
        self.assertIn("Automatic Backup Requires Attention", source)
        self.assertIn("Automatic Coming Session activation could not be completed", source)


    def test_deployment_preflight_compiles_without_writing_bytecode(self):
        source = (ROOT / "tools" / "deployment_preflight.py").read_text(encoding="utf-8")
        self.assertNotIn("import compileall", source)
        self.assertIn('compile(source_text, str(source_path), "exec")', source)
        self.assertIn("must not create __pycache__", source)

    def test_versions_remain_unchanged(self):
        database_source = (ROOT / "app" / "database.py").read_text(encoding="utf-8")
        installer_source = (ROOT / "installer" / "HiraModelSchoolInstaller.iss").read_text(encoding="utf-8")
        self.assertIn("CURRENT_SCHEMA_VERSION = 23", database_source)
        self.assertIn('#define MyAppVersion "1.1.0"', installer_source)


if __name__ == "__main__":
    unittest.main()
