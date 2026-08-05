from __future__ import annotations

import importlib
import unittest

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class StatisticsModuleTests(unittest.TestCase):
    @staticmethod
    def _load_service(app):
        service = importlib.import_module(
            "app.services.statistics_service"
        )
        income_service = importlib.import_module(
            "app.services.income_service"
        )
        service.get_connection = lambda *args, **kwargs: app.connect()
        income_service.get_connection = (
            lambda *args, **kwargs: app.connect()
        )
        return service

    @staticmethod
    def _set_admission_period(
        app,
        student_id: int,
        *,
        year: int,
        month: int,
        day: int,
    ) -> None:
        connection = app.connect()
        try:
            connection.execute(
                """
                UPDATE students
                SET admission_date=?, admission_year=?,
                    admission_month=?, admission_day=?
                WHERE student_id=?
                """,
                (
                    f"{year:04d}-{month:02d}-{day:02d} 09:00:00",
                    year,
                    month,
                    day,
                    int(student_id),
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_session_snapshot_reconciles_and_is_read_only(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_id = insert_class(app, "1", "A")

            active_id = insert_student(
                app,
                class_id=class_id,
                admission_number="STAT-001",
                roll_number=1,
                status="Active",
            )
            dropped_id = insert_student(
                app,
                class_id=class_id,
                admission_number="STAT-002",
                roll_number=2,
                status="Dropped",
            )
            graduated_id = insert_student(
                app,
                class_id=class_id,
                admission_number="STAT-003",
                roll_number=3,
                status="Graduated",
            )
            january_normal_id = insert_student(
                app,
                class_id=class_id,
                admission_number="STAT-004",
                roll_number=4,
                status="Active",
            )
            self._set_admission_period(
                app,
                january_normal_id,
                year=2027,
                month=1,
                day=10,
            )

            coming_id = insert_student(
                app,
                class_id=class_id,
                admission_number="STAT-005",
                roll_number=-5,
                status="Active",
            )
            self._set_admission_period(
                app,
                coming_id,
                year=2027,
                month=1,
                day=15,
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    UPDATE students
                    SET student_status='Coming Session',
                        target_session_start_year=2027,
                        coming_session_status='PENDING'
                    WHERE student_id=?
                    """,
                    (coming_id,),
                )
                connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no,student_id,payment_date,total_amount,
                        received_by,created_at,payment_status
                    ) VALUES ('9001',?,'2026-04-20 10:00:00',1200,
                              'test','2026-04-20 10:00:00','Completed')
                    """,
                    (active_id,),
                )
                connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no,student_id,payment_date,total_amount,
                        received_by,created_at,payment_status
                    ) VALUES ('9002',?,'2026-05-20 10:00:00',800,
                              'test','2026-05-20 10:00:00','Cancelled')
                    """,
                    (active_id,),
                )
                connection.execute(
                    """
                    INSERT INTO expenses(
                        expense_date,day_number,month_number,calendar_year,
                        session_start_year,session_year,amount,description,
                        created_at,updated_at
                    ) VALUES ('2026-07-01',1,7,2026,2026,'2026-2027',
                              300,'Test expense','2026-07-01 09:00:00',
                              '2026-07-01 09:00:00')
                    """
                )
                connection.commit()
            finally:
                connection.close()

            protected_tables = (
                "students",
                "student_fee_records",
                "fee_payments",
                "fee_receipts",
                "expenses",
                "student_status_history",
                "graduation_records",
            )
            before_counts = {
                table: int(
                    fetch_one(
                        app,
                        f"SELECT COUNT(*) AS n FROM {table}",
                    )["n"]
                )
                for table in protected_tables
            }
            before_statuses = [
                tuple(row)
                for row in fetch_all(
                    app,
                    "SELECT student_id,student_status FROM students "
                    "ORDER BY student_id",
                )
            ]

            snapshot = service.get_statistics_snapshot(2026)

            self.assertTrue(snapshot["read_only"])
            self.assertEqual(snapshot["active_strength"], 2)
            self.assertEqual(snapshot["session_admissions"], 4)
            self.assertEqual(snapshot["status_counts"]["Active"], 2)
            self.assertEqual(snapshot["status_counts"]["Dropped"], 1)
            self.assertEqual(snapshot["status_counts"]["Graduated"], 1)
            self.assertEqual(snapshot["collected_fee"], 1200.0)
            self.assertEqual(snapshot["total_expenses"], 300.0)
            self.assertEqual(snapshot["net_balance"], 900.0)

            next_snapshot = service.get_statistics_snapshot(2027)
            self.assertEqual(next_snapshot["session_admissions"], 1)
            self.assertEqual(
                next_snapshot["status_counts"]["Coming Session"],
                1,
            )

            after_counts = {
                table: int(
                    fetch_one(
                        app,
                        f"SELECT COUNT(*) AS n FROM {table}",
                    )["n"]
                )
                for table in protected_tables
            }
            after_statuses = [
                tuple(row)
                for row in fetch_all(
                    app,
                    "SELECT student_id,student_status FROM students "
                    "ORDER BY student_id",
                )
            ]
            self.assertEqual(after_counts, before_counts)
            self.assertEqual(after_statuses, before_statuses)
            self.assertIn(
                2027,
                service.get_available_statistics_session_years(),
            )


if __name__ == "__main__":
    unittest.main()
