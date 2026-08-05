from __future__ import annotations

from datetime import datetime as real_datetime
from pathlib import Path
import unittest
from unittest import mock

from tests.regression.support import IsolatedApplication, fetch_one, insert_class


class February2027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 2, 15, 14, 30, 45)
        return value if tz is None else value.replace(tzinfo=tz)


class SystemClockAdmissionDateV25Tests(unittest.TestCase):
    @staticmethod
    def _base_values(class_id: int, admission_number: str) -> dict:
        return {
            "admission_number": admission_number,
            "roll_number": 1,
            "class_id": class_id,
            "student_name": f"SYSTEM DATE {admission_number}",
            "gender": "Male",
            "father_name": f"FATHER {admission_number}",
            "father_cnic": f"{int(admission_number[1:]) + 7000000000000:013d}",
            "father_contact_number": "+920000000000",
            "whatsapp_number": "",
            "current_address": "CURRENT ADDRESS",
            "permanent_address": "PERMANENT ADDRESS",
            "school_leaving_certificate_submitted": True,
            "form_b_submitted": False,
            "birth_certificate_submitted": True,
            "date_of_birth": "01-01-2015",
            "admission_year": 2099,
            "admission_month": 12,
            "admission_day": 31,
            "admission_time": "23:59:59",
            "created_by": "system-date-regression",
            "use_system_admission_datetime": True,
        }

    def test_add_student_service_uses_computer_datetime_when_ui_lock_flag_is_set(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")
            values = self._base_values(class_id, "H2501")

            with mock.patch.object(
                app.student_service,
                "datetime",
                February2027DateTime,
            ):
                student_id = app.student_service.add_student(**values)

            row = fetch_one(
                app,
                "SELECT admission_date,admission_year,admission_month,"
                "admission_day,admission_time FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(row["admission_date"], "2027-02-15 14:30:45")
            self.assertEqual(int(row["admission_year"]), 2027)
            self.assertEqual(int(row["admission_month"]), 2)
            self.assertEqual(int(row["admission_day"]), 15)
            self.assertEqual(row["admission_time"], "14:30:45")

            history = fetch_one(
                app,
                "SELECT effective_year,effective_month FROM student_class_history "
                "WHERE student_id=? AND change_type='ADMISSION'",
                (student_id,),
            )
            self.assertEqual(
                (int(history["effective_year"]), int(history["effective_month"])),
                (2027, 2),
            )

    def test_coming_session_logic_uses_locked_computer_date_without_design_change(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "4", "A")
            values = self._base_values(class_id, "H2502")
            values["coming_session_admission"] = True

            with mock.patch.object(
                app.student_service,
                "datetime",
                February2027DateTime,
            ):
                student_id = app.student_service.add_student(**values)

            row = fetch_one(
                app,
                "SELECT student_status,roll_number,target_session_start_year,"
                "coming_session_status,admission_year,admission_month,admission_day "
                "FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertEqual(row["student_status"], "Coming Session")
            self.assertEqual(int(row["roll_number"]), -int(student_id))
            self.assertEqual(int(row["target_session_start_year"]), 2027)
            self.assertEqual(row["coming_session_status"], "PENDING")
            self.assertEqual(
                (
                    int(row["admission_year"]),
                    int(row["admission_month"]),
                    int(row["admission_day"]),
                ),
                (2027, 2, 15),
            )

    def test_add_student_date_controls_are_locked_and_refreshed_from_system_clock(self):
        project_root = Path(__file__).resolve().parents[2]
        source = (
            project_root / "app" / "modules" / "student_management.py"
        ).read_text(encoding="utf-8")
        add_page = source[
            source.index("class AddStudentPage"):
            source.index("class StudentResultCard")
        ]
        date_block = add_page[
            add_page.index("# Admission Date — always taken from the computer clock."):
            add_page.index("# Roll Number")
        ]

        self.assertIn("system_admission_input.setReadOnly(True)", date_block)
        self.assertIn("system_admission_input.setEnabled(False)", date_block)
        self.assertIn("Qt.FocusPolicy.NoFocus", date_block)
        self.assertIn("Admission Date (Computer Date):", date_block)
        self.assertNotIn("setEnabled(True)", date_block)
        self.assertIn(
            "def _sync_admission_date_to_system_clock(self) -> datetime:",
            add_page,
        )
        self.assertGreaterEqual(
            add_page.count("self._sync_admission_date_to_system_clock()"),
            2,
        )
        self.assertIn("use_system_admission_datetime=True", add_page)


if __name__ == "__main__":
    unittest.main()
