from __future__ import annotations

from datetime import datetime as real_datetime
import importlib
import os
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    fetch_one,
    insert_class,
    insert_student,
)


class March2027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 20, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class UiSafetyRegressionsV22Tests(unittest.TestCase):
    @staticmethod
    def _add_pending_student(app, class_id: int, admission_number: str) -> int:
        with mock.patch.object(app.student_service, "datetime", March2027DateTime):
            return app.student_service.add_student(
                admission_number=admission_number,
                roll_number=1,
                class_id=class_id,
                student_name=f"PENDING {admission_number}",
                gender="Male",
                father_name=f"FATHER {admission_number}",
                father_cnic=f"{int(admission_number[1:]) + 7000000000000:013d}",
                father_contact_number="+920000000000",
                whatsapp_number="",
                current_address="CURRENT ADDRESS",
                permanent_address="PERMANENT ADDRESS",
                school_leaving_certificate_submitted=True,
                form_b_submitted=False,
                birth_certificate_submitted=True,
                date_of_birth="01-01-2015",
                admission_year=2027,
                admission_month=3,
                admission_day=20,
                admission_time="09:00:00",
                created_by="ui-safety-test",
                coming_session_admission=True,
            )

    @staticmethod
    def _record_retained_promotion(
        app,
        *,
        class_id: int,
        student_id: int,
        promoted_at: str = "2027-03-25 10:00:00",
    ) -> None:
        connection = app.connect()
        try:
            class_row = connection.execute(
                "SELECT * FROM classes WHERE id=?", (int(class_id),)
            ).fetchone()
            student = connection.execute(
                "SELECT * FROM students WHERE student_id=?", (int(student_id),)
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO promotion_batches(
                    source_class_id,source_class_name,destination_class_id,
                    destination_class_name,promotion_datetime,total_students,
                    passed_students,failed_students,graduated_students,
                    created_by,is_reversed,source_session_start_year,
                    target_session_start_year,effective_date,promotion_mode
                ) VALUES(?,?,?,?,?,1,0,1,0,'ui-safety-test',0,2026,2027,'2027-04-01','NORMAL')
                """,
                (
                    int(class_id),
                    class_row["class_name"],
                    int(class_id),
                    class_row["class_name"],
                    promoted_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO promotion_history(
                    batch_id,student_id,admission_number,student_name_snapshot,
                    father_name_snapshot,old_class_id,old_class_name,new_class_id,
                    new_class_name,old_roll_number,new_roll_number,promotion_result,
                    previous_status,new_status,failure_count_before,
                    failure_count_after,promotion_datetime,promotion_type,is_reversed
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'Full Class',0)
                """,
                (
                    int(cursor.lastrowid),
                    int(student_id),
                    student["admission_number"],
                    student["student_name"],
                    student["father_name"],
                    int(class_id),
                    class_row["class_name"],
                    int(class_id),
                    class_row["class_name"],
                    int(student["roll_number"]),
                    int(student["roll_number"]),
                    "F",
                    "Active",
                    "Active",
                    int(student["failure_count"] or 0),
                    int(student["failure_count"] or 0) + 1,
                    promoted_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_class_settings_are_atomic_and_operational_classes_cannot_be_inactivated(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")

            saved = app.class_service.update_class_settings(
                class_id=class_id,
                student_capacity=55,
                active=False,
            )
            self.assertEqual(int(saved["student_capacity"]), 55)
            self.assertFalse(saved["is_active"])

            app.class_service.set_class_active(class_id, True)
            insert_student(
                app,
                class_id=class_id,
                admission_number="H8101",
                roll_number=1,
                name="ACTIVE CLASS STUDENT",
                father_cnic="8101000000001",
            )

            with self.assertRaisesRegex(ValueError, "cannot be made Inactive"):
                app.class_service.update_class_settings(
                    class_id=class_id,
                    student_capacity=70,
                    active=False,
                )

            unchanged = fetch_one(
                app,
                "SELECT student_capacity,is_active FROM classes WHERE id=?",
                (class_id,),
            )
            self.assertEqual(int(unchanged["student_capacity"]), 55)
            self.assertEqual(int(unchanged["is_active"]), 1)

            with self.assertRaisesRegex(ValueError, "cannot be deleted or made Inactive"):
                app.class_service.delete_class(class_id)

    def test_pending_reservation_blocks_class_inactivation_and_delete(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            self._add_pending_student(app, class_id, "H8201")

            with self.assertRaisesRegex(ValueError, "coming-session"):
                app.class_service.set_class_active(class_id, False)
            with self.assertRaisesRegex(ValueError, "coming-session"):
                app.class_service.delete_class(class_id)

            row = fetch_one(
                app,
                "SELECT is_active FROM classes WHERE id=?",
                (class_id,),
            )
            self.assertEqual(int(row["is_active"]), 1)

    def test_april_activation_blocks_unresolved_active_students_then_succeeds_after_promotion(self):
        with IsolatedApplication() as app:
            coming_service = importlib.import_module(
                "app.services.coming_session_service"
            )
            coming_service.get_connection = lambda *args, **kwargs: app.connect()

            class_id = insert_class(app, "PLAY GROUP", "A")
            active_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H8301",
                roll_number=7,
                name="UNRESOLVED ACTIVE STUDENT",
                father_cnic="8301000000001",
            )
            pending_id = self._add_pending_student(app, class_id, "H8302")

            blocked = coming_service.activate_due_coming_session_admissions(
                as_of=real_datetime(2027, 4, 5, 9, 0, 0),
                changed_by="ui-safety-test",
            )
            self.assertFalse(blocked["success"])
            self.assertEqual(int(blocked["activated_count"]), 0)
            self.assertTrue(any("promotion is incomplete" in e for e in blocked["errors"]))
            pending = fetch_one(
                app,
                "SELECT student_status,roll_number FROM students WHERE student_id=?",
                (pending_id,),
            )
            self.assertEqual(pending["student_status"], "Coming Session")
            self.assertLess(int(pending["roll_number"]), 0)

            self._record_retained_promotion(
                app,
                class_id=class_id,
                student_id=active_id,
            )
            activated = coming_service.activate_due_coming_session_admissions(
                as_of=real_datetime(2027, 4, 5, 10, 0, 0),
                changed_by="ui-safety-test",
            )
            self.assertTrue(activated["success"], activated)
            self.assertEqual(int(activated["activated_count"]), 1)
            final = fetch_one(
                app,
                "SELECT student_status,roll_number FROM students WHERE student_id=?",
                (pending_id,),
            )
            self.assertEqual(final["student_status"], "Active")
            self.assertGreater(int(final["roll_number"]), 0)

    def test_promotion_class_selection_change_clears_loaded_state(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 is unavailable in the current test interpreter.")

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from app.modules.promotion_management import FullClassPromotionPage

        app = QApplication.instance() or QApplication([])

        class WindowStub:
            def go_to_page(self, _name):
                return None

        page = FullClassPromotionPage(WindowStub())
        # Match the real dropdown structure: placeholder at index 0, then classes.
        page.class_combo.addItem("Select a class...", None)
        page.class_combo.addItem("3A", 31)
        page.class_combo.addItem("3B", 32)
        page.class_combo.setCurrentIndex(1)

        page.current_class_row = {"id": 31, "class_name": "3A"}
        page.active_students = [{"student_id": 1}]
        page.students_table.setRowCount(1)
        page.promote_button.setEnabled(True)

        page.class_combo.setCurrentIndex(2)
        app.processEvents()

        self.assertIsNone(page.current_class_row)
        self.assertEqual(page.active_students, [])
        self.assertEqual(page.students_table.rowCount(), 0)
        self.assertFalse(page.promote_button.isEnabled())
        self.assertIn("Click Load Class", page.empty_label.text())
        page.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
