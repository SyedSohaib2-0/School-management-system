from __future__ import annotations

from datetime import datetime
import importlib
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, insert_class, insert_student


class FixedSiblingDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 9, 1, 4, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class SiblingSettingHistoryStartupV30Tests(unittest.TestCase):
    def _build_revised_sibling_setting(self, app: IsolatedApplication):
        class_id = insert_class(app, "7", "A")
        highest_class_id = insert_class(app, "10", "A")
        first_id = insert_student(
            app,
            class_id=class_id,
            admission_number="H301",
            roll_number=1,
            name="First Sibling",
            father_name="History Father",
            father_cnic="1730112345671",
        )
        second_id = insert_student(
            app,
            class_id=class_id,
            admission_number="H302",
            roll_number=2,
            name="Second Sibling",
            father_name="History Father",
            father_cnic="1730112345671",
        )
        insert_student(
            app,
            class_id=highest_class_id,
            admission_number="H303",
            roll_number=1,
            name="Highest Sibling",
            father_name="History Father",
            father_cnic="1730112345671",
        )

        with mock.patch.object(
            app.sibling_fee_service,
            "datetime",
            FixedSiblingDateTime,
        ):
            app.sibling_fee_service.save_sibling_fee_percentage(
                50,
                created_by="regression",
            )

        class LaterSiblingDateTime(FixedSiblingDateTime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 9, 1, 4, 12, 9)
                return value if tz is None else value.replace(tzinfo=tz)

        with mock.patch.object(
            app.sibling_fee_service,
            "datetime",
            LaterSiblingDateTime,
        ):
            app.sibling_fee_service.save_sibling_fee_percentage(
                80,
                created_by="regression",
            )

        return first_id, second_id

    def _validate(self, app: IsolatedApplication):
        schema_integrity = importlib.import_module("app.schema_integrity")
        connection = app.connect()
        try:
            return schema_integrity.validate_sibling_assignment_integrity(connection)
        finally:
            connection.close()

    def test_inactive_50_percent_history_remains_valid_after_same_month_80_percent_revision(self):
        with IsolatedApplication() as app:
            self._build_revised_sibling_setting(app)

            connection = app.connect()
            try:
                historical = connection.execute(
                    """
                    SELECT monthly_fee_percentage,is_active,superseded_at
                    FROM sibling_fee_assignments
                    WHERE assignment_role='DISCOUNTED'
                    ORDER BY id
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(
                [(float(row["monthly_fee_percentage"]), int(row["is_active"])) for row in historical],
                [(50.0, 0), (50.0, 0), (80.0, 1), (80.0, 1)],
            )
            valid, message = self._validate(app)
            self.assertTrue(valid, message)

    def test_tampered_historical_discount_percentage_is_still_rejected(self):
        with IsolatedApplication() as app:
            self._build_revised_sibling_setting(app)
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE sibling_fee_assignments
                    SET monthly_fee_percentage=40
                    WHERE assignment_role='DISCOUNTED' AND is_active=0
                    """
                )
                connection.commit()
            finally:
                connection.close()

            valid, message = self._validate(app)
            self.assertFalse(valid)
            self.assertIn("role fields are contradictory", message)

    def test_tampered_current_discount_percentage_is_rejected(self):
        with IsolatedApplication() as app:
            self._build_revised_sibling_setting(app)
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE sibling_fee_assignments
                    SET monthly_fee_percentage=70
                    WHERE assignment_role='DISCOUNTED' AND is_active=1
                    """
                )
                connection.commit()
            finally:
                connection.close()

            valid, message = self._validate(app)
            self.assertFalse(valid)
            self.assertIn("role fields are contradictory", message)


if __name__ == "__main__":
    unittest.main()
