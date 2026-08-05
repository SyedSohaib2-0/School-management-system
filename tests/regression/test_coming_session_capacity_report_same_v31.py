from __future__ import annotations

import importlib
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_one,
    insert_class,
    insert_student,
)


class ComingSessionCapacityReportSameV31Tests(unittest.TestCase):
    @staticmethod
    def _load_service(app):
        service = importlib.import_module(
            "app.services.coming_session_capacity_report_service"
        )
        service.get_connection = lambda *args, **kwargs: app.connect()
        return service

    @staticmethod
    def _set_capacity(app, class_id: int, capacity: int) -> None:
        connection = app.connect()
        try:
            connection.execute(
                "UPDATE classes SET student_capacity=? WHERE id=?",
                (int(capacity), int(class_id)),
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _make_coming_student(app, class_id: int, student_id: int, target_year: int):
        connection = app.connect()
        try:
            connection.execute(
                """
                UPDATE students
                SET student_status='Coming Session',
                    coming_session_status='PENDING',
                    target_session_start_year=?,
                    admission_date=?, admission_year=?, admission_month=2,
                    admission_day=10, roll_number=?
                WHERE student_id=?
                """,
                (
                    int(target_year),
                    f"{target_year:04d}-02-10 09:00:00",
                    int(target_year),
                    -abs(int(student_id)),
                    int(student_id),
                ),
            )
            connection.execute(
                """
                UPDATE student_class_history
                SET effective_month=2, effective_year=?, change_datetime=?
                WHERE student_id=? AND change_type='ADMISSION'
                """,
                (
                    int(target_year),
                    f"{target_year:04d}-02-10 09:00:00",
                    int(student_id),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_report_reconstructs_end_of_base_session_and_next_session_admissions(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_5a = insert_class(app, "5", "A")
            class_6a = insert_class(app, "6", "A")
            self._set_capacity(app, class_5a, 4)

            insert_student(
                app,
                class_id=class_5a,
                admission_number="CSR-001",
                roll_number=1,
                name="Still Active",
            )

            dropped_after = insert_student(
                app,
                class_id=class_5a,
                admission_number="CSR-002",
                roll_number=2,
                name="Dropped After Cutoff",
            )
            dropped_before = insert_student(
                app,
                class_id=class_5a,
                admission_number="CSR-003",
                roll_number=3,
                name="Dropped Before Cutoff",
            )
            promoted_after = insert_student(
                app,
                class_id=class_5a,
                admission_number="CSR-004",
                roll_number=4,
                name="Promoted After Cutoff",
            )
            coming_one = insert_student(
                app,
                class_id=class_5a,
                admission_number="CSR-005",
                roll_number=5,
                name="Coming One",
            )
            coming_two = insert_student(
                app,
                class_id=class_5a,
                admission_number="CSR-006",
                roll_number=6,
                name="Coming Two",
            )
            self._make_coming_student(app, class_5a, coming_one, 2027)
            self._make_coming_student(app, class_5a, coming_two, 2027)

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET student_status='Dropped' WHERE student_id=?",
                    (dropped_after,),
                )
                connection.execute(
                    """
                    INSERT INTO student_status_history(
                        student_id,old_status,new_status,event_type,
                        effective_datetime,reason,changed_by,created_at,
                        related_class_id,related_event_id
                    ) VALUES(?, 'Active','Dropped','DROP',
                             '2027-04-10 10:00:00','test','test',
                             '2027-04-10 10:00:00',?,NULL)
                    """,
                    (dropped_after, class_5a),
                )

                connection.execute(
                    "UPDATE students SET student_status='Dropped' WHERE student_id=?",
                    (dropped_before,),
                )
                connection.execute(
                    """
                    INSERT INTO student_status_history(
                        student_id,old_status,new_status,event_type,
                        effective_datetime,reason,changed_by,created_at,
                        related_class_id,related_event_id
                    ) VALUES(?, 'Active','Dropped','DROP',
                             '2027-03-01 10:00:00','test','test',
                             '2027-03-01 10:00:00',?,NULL)
                    """,
                    (dropped_before, class_5a),
                )

                connection.execute(
                    "UPDATE students SET class_id=? WHERE student_id=?",
                    (class_6a, promoted_after),
                )
                connection.execute(
                    """
                    INSERT INTO student_class_history(
                        student_id,old_class_id,new_class_id,
                        effective_month,effective_year,change_type,
                        change_datetime,changed_by
                    ) VALUES(?,?,?,4,2027,'PROMOTION',
                             '2027-04-01 00:00:00','test')
                    """,
                    (promoted_after, class_5a, class_6a),
                )
                connection.commit()
            finally:
                connection.close()

            report = service.get_coming_session_capacity_report(2026)
            row = next(
                item for item in report["rows"] if item["class_id"] == class_5a
            )
            self.assertEqual(report["base_session_label"], "2026-2027")
            self.assertEqual(report["coming_session_label"], "2027-2028")
            self.assertEqual(row["existing_active_students"], 3)
            self.assertEqual(row["new_coming_students"], 2)
            self.assertEqual(row["class_capacity"], 4)
            self.assertEqual(row["expected_total"], 5)
            self.assertEqual(row["available_seats"], -1)
            self.assertEqual(row["status"], "Over Capacity")
            self.assertIn(2026, service.get_available_base_session_years())

    def test_capacity_edits_are_persistent_and_atomic(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_5a = insert_class(app, "5", "A")
            class_6a = insert_class(app, "6", "A")
            insert_student(
                app,
                class_id=class_5a,
                admission_number="CAP-001",
                roll_number=1,
            )

            saved = service.save_class_capacities({class_5a: 55, class_6a: 60})
            self.assertEqual(saved, {class_5a: 55, class_6a: 60})
            self.assertEqual(
                int(fetch_one(app, "SELECT student_capacity FROM classes WHERE id=?", (class_5a,))["student_capacity"]),
                55,
            )
            self.assertEqual(
                int(fetch_one(app, "SELECT student_capacity FROM classes WHERE id=?", (class_6a,))["student_capacity"]),
                60,
            )

            with self.assertRaisesRegex(ValueError, "cannot be reduced|capacity"):
                service.save_class_capacities({class_5a: 0, class_6a: 80})

            self.assertEqual(
                int(fetch_one(app, "SELECT student_capacity FROM classes WHERE id=?", (class_5a,))["student_capacity"]),
                55,
            )
            self.assertEqual(
                int(fetch_one(app, "SELECT student_capacity FROM classes WHERE id=?", (class_6a,))["student_capacity"]),
                60,
            )

    def test_report_read_does_not_change_school_records(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_id = insert_class(app, "4", "A")
            insert_student(
                app,
                class_id=class_id,
                admission_number="READ-001",
                roll_number=1,
            )
            before = {
                "classes": int(fetch_one(app, "SELECT COUNT(*) AS n FROM classes")["n"]),
                "students": int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]),
                "history": int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_class_history")["n"]),
            }
            service.get_coming_session_capacity_report(2026)
            after = {
                "classes": int(fetch_one(app, "SELECT COUNT(*) AS n FROM classes")["n"]),
                "students": int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]),
                "history": int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_class_history")["n"]),
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
