from __future__ import annotations

import calendar
from datetime import datetime
import importlib
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, insert_class, insert_student


class _FrenchMonthNames:
    _values = (
        "", "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    )

    def __getitem__(self, index):
        return self._values[index]


class DeepStartupMigrationLocaleHardeningTests(unittest.TestCase):
    def _schema(self):
        return importlib.import_module("app.schema_integrity")

    def _assert_full_schema_valid(self, app):
        connection = app.strict_connect()
        try:
            valid, message = self._schema().validate_current_schema(connection)
        finally:
            connection.close()
        self.assertTrue(valid, message)

    def test_multi_session_individual_override_survives_reopen(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="MULTI-V31", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            override_id = app.fee_service.set_student_fee_override(
                student_id,
                50,
                4,
                2026,
                number_of_sessions=2,
                created_by="test",
                change_reason="Two-session scholarship",
            )

            connection = app.connect()
            try:
                row = connection.execute(
                    """
                    SELECT effective_from_month, effective_from_year,
                           effective_to_month, effective_to_year
                    FROM student_fee_override WHERE id=?
                    """,
                    (override_id,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(tuple(row), (4, 2026, 3, 2028))

            self._assert_full_schema_valid(app)
            app.database.initialize_database()
            self._assert_full_schema_valid(app)

    def test_all_future_override_removed_in_later_session_survives_reopen(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="REMOVE-V31", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            app.fee_service.set_class_fee(class_id, 1100, 4, 2027)
            app.fee_service.set_student_fee_override(
                student_id,
                60,
                4,
                2026,
                number_of_sessions=None,
                created_by="test",
                change_reason="Open-ended scholarship",
            )
            self.assertTrue(
                app.fee_service.remove_student_fee_override(
                    student_id, 4, 2027, removed_by="test"
                )
            )

            connection = app.connect()
            try:
                row = connection.execute(
                    """
                    SELECT removed_effective_month, removed_effective_year, is_active
                    FROM student_fee_override WHERE student_id=?
                    """,
                    (student_id,),
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(tuple(row), (4, 2027, 0))

            self._assert_full_schema_valid(app)
            app.database.initialize_database()
            self._assert_full_schema_valid(app)

    def test_individual_override_period_before_start_is_still_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="BAD-PERIOD", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            override_id = app.fee_service.set_student_fee_override(
                student_id,
                50,
                4,
                2026,
                number_of_sessions=2,
                created_by="test",
                change_reason="Fixture",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE student_fee_override
                    SET effective_to_month=3, effective_to_year=2026
                    WHERE id=?
                    """,
                    (override_id,),
                )
                connection.commit()
            finally:
                connection.close()

            connection = app.strict_connect()
            try:
                valid, message = self._schema().validate_fee_policy_periods(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("before its start", message)

    def test_fee_month_validation_is_independent_of_windows_locale(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="LOCALE-V31", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2026, 4, 1, 9, 0, 0)
            )

            connection = app.strict_connect()
            try:
                with mock.patch.object(calendar, "month_name", _FrenchMonthNames()):
                    valid, message = self._schema().validate_fee_period_labels(connection)
            finally:
                connection.close()
            self.assertTrue(valid, message)

    def test_genuinely_wrong_fee_month_label_is_still_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="WRONG-MONTH", roll_number=1
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=datetime(2026, 4, 1, 9, 0, 0)
            )
            connection = app.connect()
            try:
                connection.execute("DROP TRIGGER IF EXISTS trg_fee_record_month_update")
                connection.execute(
                    """
                    UPDATE student_fee_records SET month_name='May'
                    WHERE student_id=? AND month_number=4 AND year=2026
                    """,
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            connection = app.strict_connect()
            try:
                valid, message = self._schema().validate_fee_period_labels(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("conflicts with month_number", message)

    def test_version_three_migration_backfills_one_admission_history(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="MIG-V3", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    "DROP INDEX IF EXISTS idx_student_class_history_student_effective"
                )
                connection.execute("DROP TABLE student_class_history")
                connection.execute("PRAGMA user_version=3")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()

            connection = app.strict_connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                rows = connection.execute(
                    """
                    SELECT old_class_id, new_class_id, effective_month,
                           effective_year, change_type
                    FROM student_class_history
                    WHERE student_id=? AND change_type='ADMISSION'
                    """,
                    (student_id,),
                ).fetchall()
                valid, message = self._schema().validate_current_schema(connection)
            finally:
                connection.close()
            self.assertEqual(version, 23)
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(rows[0]), (None, class_id, 4, 2026, "ADMISSION"))
            self.assertTrue(valid, message)

            app.database.initialize_database()
            connection = app.strict_connect()
            try:
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM student_class_history
                        WHERE student_id=? AND change_type='ADMISSION'
                        """,
                        (student_id,),
                    ).fetchone()[0]
                )
            finally:
                connection.close()
            self.assertEqual(count, 1)

    def test_version_four_legacy_blank_dob_migrates_without_fabrication(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="MIG-V4", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute("ALTER TABLE students DROP COLUMN date_of_birth")
                connection.execute("PRAGMA user_version=4")
                connection.commit()
            finally:
                connection.close()

            app.database.initialize_database()

            connection = app.strict_connect()
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                stored_dob = connection.execute(
                    "SELECT date_of_birth FROM students WHERE student_id=?",
                    (student_id,),
                ).fetchone()[0]
                valid, message = self._schema().validate_current_schema(connection)
            finally:
                connection.close()
            self.assertEqual(version, 23)
            self.assertIsNone(stored_dob)
            self.assertTrue(valid, message)

    def test_nonblank_malformed_legacy_dob_is_still_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="BAD-DOB", roll_number=1
            )
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET date_of_birth='not-a-date' WHERE student_id=?",
                    (student_id,),
                )
                connection.commit()
            finally:
                connection.close()

            connection = app.strict_connect()
            try:
                valid, message = self._schema().validate_student_admission_fields(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("invalid date of birth", message)


if __name__ == "__main__":
    unittest.main()
