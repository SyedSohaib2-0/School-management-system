from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from tests.regression.support import (
    PROJECT_ROOT,
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class PositiveFullClassFeeV26Tests(unittest.TestCase):
    def test_service_rejects_zero_class_fee_but_keeps_zero_individual_fee_valid(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2601",
                roll_number=1,
            )

            self.assertTrue(app.fee_service.validate_fee_amount(0))
            self.assertFalse(app.fee_service.validate_class_fee_amount(0))
            self.assertFalse(app.fee_service.validate_class_fee_amount(-1))
            self.assertTrue(app.fee_service.validate_class_fee_amount(0.01))

            with self.assertRaisesRegex(ValueError, "greater than zero"):
                app.fee_service.set_class_fee(class_id, 0, 4, 2026)

            self.assertEqual(
                fetch_all(app, "SELECT * FROM fee_settings WHERE class_id=?", (class_id,)),
                [],
            )

            app.fee_service.set_class_fee(class_id, 1500, 4, 2026)
            app.fee_service.set_student_fee_override(
                student_id,
                0,
                4,
                2026,
                number_of_sessions=1,
            )
            override = fetch_one(
                app,
                "SELECT monthly_fee_percentage FROM student_fee_override "
                "WHERE student_id=? AND is_active=1",
                (student_id,),
            )
            self.assertIsNotNone(override)
            self.assertEqual(float(override["monthly_fee_percentage"]), 0.0)

    def test_zero_level_fee_rolls_back_without_changing_any_section(self):
        with IsolatedApplication() as app:
            class_a = insert_class(app, "2", "A")
            class_g = insert_class(app, "2", "G")
            app.fee_service.set_class_fees_for_level(
                [class_a, class_g], 1800, 4, 2026
            )

            with self.assertRaisesRegex(ValueError, "greater than zero"):
                app.fee_service.set_class_fees_for_level(
                    [class_a, class_g], 0, 7, 2026
                )

            rows = fetch_all(
                app,
                "SELECT class_id,monthly_fee,effective_from_month FROM fee_settings "
                "WHERE class_id IN (?,?) ORDER BY class_id",
                (class_a, class_g),
            )
            self.assertEqual(
                [
                    (int(row["class_id"]), float(row["monthly_fee"]), int(row["effective_from_month"]))
                    for row in rows
                ],
                [(class_a, 1800.0, 4), (class_g, 1800.0, 4)],
            )
            self.assertEqual(
                fetch_all(
                    app,
                    "SELECT * FROM fee_history WHERE class_id IN (?,?)",
                    (class_a, class_g),
                ),
                [],
            )

    def test_database_guards_reject_zero_class_fee_insert_and_update(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")
            app.fee_service.set_class_fee(class_id, 2000, 4, 2026)
            app.fee_service.set_class_fee(class_id, 2200, 5, 2026)

            connection = app.strict_connect()
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, "greater than zero"):
                    connection.execute(
                        "UPDATE fee_settings SET monthly_fee=0 WHERE class_id=?",
                        (class_id,),
                    )
                connection.rollback()

                with self.assertRaisesRegex(sqlite3.IntegrityError, "greater than zero"):
                    connection.execute(
                        "UPDATE fee_history SET monthly_fee=0 WHERE class_id=?",
                        (class_id,),
                    )
                connection.rollback()

                second_class = insert_class(app, "3", "G")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "greater than zero"):
                    connection.execute(
                        """
                        INSERT INTO fee_settings(
                            class_id,monthly_fee,session_start_year,
                            effective_from_month,effective_from_year,
                            created_at,updated_at,created_by,change_reason
                        ) VALUES(?,0,2026,4,2026,?,?,?,?)
                        """,
                        (
                            second_class,
                            "2026-04-01 08:00:00",
                            "2026-04-01 08:00:00",
                            "test",
                            "test zero rejection",
                        ),
                    )
                connection.rollback()
            finally:
                connection.close()

            current = fetch_one(
                app,
                "SELECT monthly_fee FROM fee_settings WHERE class_id=?",
                (class_id,),
            )
            history = fetch_one(
                app,
                "SELECT monthly_fee FROM fee_history WHERE class_id=?",
                (class_id,),
            )
            self.assertEqual(float(current["monthly_fee"]), 2200.0)
            self.assertEqual(float(history["monthly_fee"]), 2000.0)

    def test_fresh_schema_and_class_fee_ui_contract_are_positive_only(self):
        with IsolatedApplication() as app:
            connection = app.strict_connect()
            try:
                table_sql = {
                    str(row[0]): str(row[1])
                    for row in connection.execute(
                        "SELECT name,sql FROM sqlite_master "
                        "WHERE type='table' AND name IN ('fee_settings','fee_history')"
                    ).fetchall()
                }
            finally:
                connection.close()

        normalized = {
            name: "".join(sql.upper().split()) for name, sql in table_sql.items()
        }
        self.assertIn("CHECK(MONTHLY_FEE>0)", normalized["fee_settings"])
        self.assertIn("CHECK(MONTHLY_FEE>0)", normalized["fee_history"])

        ui_source = (PROJECT_ROOT / "app/modules/fee_management.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("validate_class_fee_amount", ui_source)
        self.assertIn("Full Class Monthly Fee must be", ui_source)
        self.assertIn("individual student fee rule", ui_source)


if __name__ == "__main__":
    unittest.main()
