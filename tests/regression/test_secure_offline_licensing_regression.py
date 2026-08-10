from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app.services import license_service


ROOT = Path(__file__).resolve().parents[2]


class SecureOfflineLicensingRegressionTests(unittest.TestCase):
    def test_request_code_round_trip_recovers_exact_machine_fingerprint(self):
        request_code = license_service.get_activation_request_code()
        recovered = license_service.machine_fingerprint_from_request_code(
            request_code
        )
        self.assertEqual(recovered, license_service.get_machine_fingerprint())

    def test_malformed_request_codes_are_rejected(self):
        for code in ("", "ABC-1234", "HMS-1234", "HMS-!!!!"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    license_service.machine_fingerprint_from_request_code(code)

    def test_customer_spec_excludes_private_generator_and_signing_key(self):
        source = (ROOT / "HiraModelSchool.spec").read_text(encoding="utf-8")
        self.assertNotIn("encrypted_signing_key.json", source)
        self.assertIn("excludes=['app.licence_generator']", source)

    def test_customer_startup_uses_import_dialog_not_private_generator(self):
        source = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("LicenseActivationDialog", source)
        self.assertNotIn("ActivationPasswordDialog", source)
        self.assertNotIn("LicenceGeneratorDialog", source)

    def test_owner_spec_contains_private_generator_key(self):
        source = (ROOT / "HiraModelSchoolLicenseGenerator.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn("encrypted_signing_key.json", source)
        self.assertIn("owner_license_generator.py", source)

    def test_oversized_licence_is_rejected_before_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            oversized = Path(directory) / "oversized.lic"
            oversized.write_bytes(
                b"x" * (license_service.MAX_LICENSE_FILE_BYTES + 1)
            )
            result = license_service.install_license_file(str(oversized))
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "license_too_large")

    def test_unexpected_verification_failure_restores_previous_licence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed.lic"
            selected = root / "replacement.lic"
            installed.write_text("previous", encoding="utf-8")
            selected.write_text("replacement", encoding="utf-8")

            with (
                mock.patch.object(
                    license_service,
                    "LICENSE_DIRECTORY",
                    str(root),
                ),
                mock.patch.object(
                    license_service,
                    "LICENSE_FILE_PATH",
                    str(installed),
                ),
                mock.patch.object(
                    license_service,
                    "verify_installed_license",
                    side_effect=RuntimeError("synthetic verifier failure"),
                ),
            ):
                result = license_service.install_license_file(str(selected))

            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "installation_error")
            self.assertEqual(
                installed.read_text(encoding="utf-8"),
                "previous",
            )

    def test_permission_denied_license_directory_returns_controlled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "selected.lic"
            selected.write_text("replacement", encoding="utf-8")

            with mock.patch.object(
                license_service.os,
                "makedirs",
                side_effect=PermissionError("synthetic permission denial"),
            ):
                result = license_service.install_license_file(str(selected))

            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "installation_error")
            self.assertIn("permission denial", result["message"])


if __name__ == "__main__":
    unittest.main()
