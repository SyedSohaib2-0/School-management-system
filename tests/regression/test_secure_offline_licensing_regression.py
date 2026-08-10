from __future__ import annotations

from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
