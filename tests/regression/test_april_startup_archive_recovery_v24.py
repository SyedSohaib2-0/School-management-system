from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_one,
    insert_class,
    insert_student,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AprilStartupArchiveRecoveryV24Tests(unittest.TestCase):
    def test_empty_archive_selection_is_a_no_op_not_archive_all(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = self._prepare_dropped_student(
                app,
                class_id=class_id,
                admission_number="H2400",
                roll_number=1,
            )

            result = app.student_service.archive_eligible_dropped_students(
                as_of=datetime(2027, 4, 1, 9, 0, 0),
                student_ids=[],
            )

            self.assertEqual(result["archived_count"], 0)
            self.assertEqual(result["archived_student_ids"], [])
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT student_status FROM students WHERE student_id=?",
                    (student_id,),
                )["student_status"],
                "Dropped",
            )

    def test_same_schema_hotfix_releases_legacy_archived_positive_roll(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2399",
                roll_number=7,
                status="Archived",
            )
            connection = app.connect()
            try:
                migrated = app.database.run_schema_migrations(
                    connection,
                    app.database.CURRENT_SCHEMA_VERSION,
                )
                connection.commit()
                self.assertEqual(migrated, app.database.CURRENT_SCHEMA_VERSION)
                row = connection.execute(
                    "SELECT roll_number, released_roll_number FROM students WHERE student_id=?",
                    (student_id,),
                ).fetchone()
                self.assertEqual(row["roll_number"], -student_id)
                self.assertEqual(row["released_roll_number"], 7)
                valid, message = (
                    app.database.schema_integrity.validate_student_roll_invariants(
                        connection
                    )
                )
                self.assertTrue(valid, message)
            finally:
                connection.close()

    def test_same_schema_hotfix_clears_stale_released_roll_on_held_rolls(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            active_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2397",
                roll_number=6,
                status="Active",
            )
            dropped_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2398",
                roll_number=7,
                status="Dropped",
            )
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET released_roll_number=99 WHERE student_id IN (?,?)",
                    (active_id, dropped_id),
                )
                app.database.run_schema_migrations(
                    connection,
                    app.database.CURRENT_SCHEMA_VERSION,
                )
                connection.commit()
                rows = connection.execute(
                    "SELECT student_id,released_roll_number FROM students WHERE student_id IN (?,?)",
                    (active_id, dropped_id),
                ).fetchall()
                self.assertTrue(all(row["released_roll_number"] is None for row in rows))
                valid, message = (
                    app.database.schema_integrity.validate_student_roll_invariants(
                        connection
                    )
                )
                self.assertTrue(valid, message)
            finally:
                connection.close()

    def test_same_schema_hotfix_repairs_zero_released_dropped_roll(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H2396",
                roll_number=8,
                status="Dropped",
            )
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET roll_number=0,released_roll_number=8 "
                    "WHERE student_id=?",
                    (student_id,),
                )
                app.database.run_schema_migrations(
                    connection,
                    app.database.CURRENT_SCHEMA_VERSION,
                )
                connection.commit()
                row = connection.execute(
                    "SELECT roll_number,released_roll_number FROM students WHERE student_id=?",
                    (student_id,),
                ).fetchone()
                self.assertEqual(row["roll_number"], -student_id)
                self.assertEqual(row["released_roll_number"], 8)
                valid, message = (
                    app.database.schema_integrity.validate_student_roll_invariants(
                        connection
                    )
                )
                self.assertTrue(valid, message)
            finally:
                connection.close()

    @staticmethod
    def _prepare_dropped_student(
        app,
        *,
        class_id: int,
        admission_number: str,
        roll_number: int,
        drop_datetime: str = "2026-03-10 10:00:00",
    ) -> int:
        student_id = insert_student(
            app,
            class_id=class_id,
            admission_number=admission_number,
            roll_number=roll_number,
            status="Dropped",
        )
        connection = app.connect()
        try:
            connection.execute(
                """
                UPDATE students
                SET admission_date='2025-04-01 09:00:00',
                    admission_year=2025,
                    admission_month=4,
                    admission_day=1,
                    updated_at=?
                WHERE student_id=?
                """,
                (drop_datetime, int(student_id)),
            )
            connection.execute(
                """
                INSERT INTO student_status_history(
                    student_id,old_status,new_status,event_type,
                    effective_datetime,reason,changed_by,created_at,
                    related_class_id
                ) VALUES(?, 'Active', 'Dropped', 'DROP', ?, ?, ?, ?, ?)
                """,
                (
                    int(student_id),
                    drop_datetime,
                    "Regression setup",
                    "test",
                    drop_datetime,
                    int(class_id),
                ),
            )
            connection.execute(
                """
                INSERT INTO student_class_history(
                    student_id,old_class_id,new_class_id,effective_month,
                    effective_year,change_type,change_datetime,changed_by
                ) VALUES(?, NULL, ?, 3, 2026, 'MANUAL', ?, 'DROP:test')
                """,
                (int(student_id), int(class_id), drop_datetime),
            )
            connection.commit()
        finally:
            connection.close()
        return student_id

    def test_startup_safe_archive_skips_missing_fee_and_archives_other_students(self):
        with IsolatedApplication() as app:
            configured_class = insert_class(app, "1", "A")
            missing_fee_class = insert_class(app, "PLAY GROUP", "A")
            app.fee_service.set_class_fee(
                configured_class,
                1000,
                4,
                2025,
                created_by="test",
                change_reason="Configured comparison class",
            )

            clear_student = self._prepare_dropped_student(
                app,
                class_id=configured_class,
                admission_number="H2401",
                roll_number=1,
            )
            app.fee_service.set_student_fee_override(
                clear_student, 0, 4, 2025, number_of_sessions=1
            )
            missing_fee_student = self._prepare_dropped_student(
                app,
                class_id=missing_fee_class,
                admission_number="H2402",
                roll_number=1,
            )

            connection = app.connect()
            try:
                for year, month, month_name in (
                    (2025, 4, "April"),
                    (2025, 5, "May"),
                    (2025, 6, "June"),
                    (2025, 7, "July"),
                    (2025, 8, "August"),
                    (2025, 9, "September"),
                    (2025, 10, "October"),
                    (2025, 11, "November"),
                    (2025, 12, "December"),
                    (2026, 1, "January"),
                    (2026, 2, "February"),
                ):
                    connection.execute(
                        """
                        INSERT INTO student_fee_records(
                            student_id,class_id,session_year,fee_type,
                            month_name,month_number,year,amount,status,
                            created_at,updated_at
                        ) VALUES(?,?,'2025-2026','Monthly',?,?,?,0,'Waived',?,?)
                        """,
                        (
                            missing_fee_student,
                            missing_fee_class,
                            month_name,
                            month,
                            year,
                            "2026-02-28 10:00:00",
                            "2026-02-28 10:00:00",
                        ),
                    )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaisesRegex(
                ValueError,
                "Monthly Fee configuration is missing for PLAY GROUPA, March 2026",
            ):
                app.student_service.archive_eligible_dropped_students(
                    as_of=datetime(2027, 4, 28, 14, 12, 13),
                    student_ids=[missing_fee_student],
                )

            result = app.student_service.archive_eligible_dropped_students(
                as_of=datetime(2027, 4, 28, 14, 12, 13),
                tolerate_missing_fee_configuration=True,
            )

            self.assertEqual(result["archived_student_ids"], [clear_student])
            self.assertEqual(
                result["configuration_blocked_student_ids"],
                [missing_fee_student],
            )
            self.assertIn(
                "Monthly Fee configuration is missing for PLAY GROUPA",
                result["configuration_errors"][missing_fee_student],
            )
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT student_status FROM students WHERE student_id=?",
                    (clear_student,),
                )["student_status"],
                "Archived",
            )
            archived_roll = fetch_one(
                app,
                "SELECT roll_number, released_roll_number FROM students WHERE student_id=?",
                (clear_student,),
            )
            self.assertEqual(archived_roll["roll_number"], -clear_student)
            self.assertEqual(archived_roll["released_roll_number"], 1)
            self.assertEqual(
                fetch_one(
                    app,
                    "SELECT student_status FROM students WHERE student_id=?",
                    (missing_fee_student,),
                )["student_status"],
                "Dropped",
            )
            self.assertEqual(
                int(
                    fetch_one(
                        app,
                        "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=?",
                        (missing_fee_student,),
                    )["n"]
                ),
                11,
            )

    def test_main_window_uses_safe_mode_and_run_reports_actionable_warning(self):
        main_window_source = (PROJECT_ROOT / "app/main_window.py").read_text(
            encoding="utf-8"
        )
        run_source = (PROJECT_ROOT / "run.py").read_text(encoding="utf-8")

        self.assertIn(
            "tolerate_missing_fee_configuration=True",
            main_window_source,
        )
        self.assertIn(
            "Historical Fee Configuration Required",
            run_source,
        )
        self.assertIn(
            "No partial fee rows were saved.",
            run_source,
        )


if __name__ == "__main__":
    unittest.main()
