from __future__ import annotations

from datetime import datetime
from pathlib import Path
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    PROJECT_ROOT,
    allowed_promotion_window,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class FixedJulyDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 12, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


def add_student_values(class_id, admission_number, roll_number, **overrides):
    values = {
        "admission_number": admission_number,
        "roll_number": roll_number,
        "class_id": class_id,
        "student_name": "New Student",
        "father_name": "New Father",
        "father_cnic": "1730112345671",
        "father_contact_number": "+920000000000",
        "school_leaving_certificate_submitted": True,
        "admission_year": 2026,
        "admission_month": 4,
        "admission_day": 1,
        "admission_time": "09:00:00",
        "gender": "Male",
        "date_of_birth": "01-01-2015",
        "created_by": "v11-regression",
    }
    values.update(overrides)
    return values


def update_from_row(app, student_id, **overrides):
    row = app.student_service.get_student_by_id(student_id)
    values = {
        "student_id": student_id,
        "admission_number": row["admission_number"],
        "roll_number": int(row["roll_number"]),
        "class_id": int(row["class_id"]),
        "student_name": row["student_name"],
        "father_name": row["father_name"],
        "father_cnic": row["father_cnic"] or "",
        "father_contact_number": row["father_contact_number"],
        "student_status": row["student_status"],
        "school_leaving_certificate_submitted": bool(
            row["school_leaving_certificate_submitted"]
        ),
        "admission_year": int(row["admission_year"]),
        "admission_month": int(row["admission_month"]),
        "admission_day": int(row["admission_day"]),
        "admission_time": row["admission_time"] or "09:00:00",
        "gender": row["gender"] or "Male",
        "whatsapp_number": row["whatsapp_number"] or "",
        "current_address": row["current_address"] or "",
        "permanent_address": row["permanent_address"] or "",
        "date_of_birth": row["date_of_birth"] or "2015-01-01",
        "form_b_submitted": bool(row["form_b_submitted"]),
        "birth_certificate_submitted": bool(row["birth_certificate_submitted"]),
        "updated_by": "v11-regression",
    }
    values.update(overrides)
    return app.student_service.update_student(**values)


class AdditionalBugFixV11ScopeTests(unittest.TestCase):
    def test_literal_name_search_treats_percent_underscore_and_backslash_as_text(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            ordinary = insert_student(
                app, class_id=class_id, admission_number="H1101", roll_number=1,
                name="ALI KHAN", father_name="AHMAD KHAN",
            )
            percent = insert_student(
                app, class_id=class_id, admission_number="H1102", roll_number=2,
                name="100% STUDENT", father_name="PERCENT% FATHER",
            )
            underscore = insert_student(
                app, class_id=class_id, admission_number="H1103", roll_number=3,
                name="UNDER_SCORE", father_name="FATHER_NAME",
            )

            self.assertEqual(
                [int(row["student_id"]) for row in app.student_service.search_students(student_name="%", include_archived=True)],
                [percent],
            )
            self.assertEqual(
                [int(row["student_id"]) for row in app.student_service.search_students(student_name="_", include_archived=True)],
                [underscore],
            )
            self.assertEqual(
                app.student_service.search_students(student_name="\\", include_archived=True),
                [],
            )
            self.assertEqual(
                [int(row["student_id"]) for row in app.fee_service.search_students_for_fee(student_name="%", class_id=class_id)],
                [percent],
            )
            combined = app.student_service.search_students(
                student_name="ali", father_name=" ahmad   khan ", include_archived=True
            )
            self.assertEqual([int(row["student_id"]) for row in combined], [ordinary])

    def test_name_normalization_blocks_new_duplicates_and_reports_legacy_groups(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            first = insert_student(
                app, class_id=class_id, admission_number="H1110", roll_number=1,
                name="ALI KHAN", father_name="AHMAD KHAN",
            )
            second = insert_student(
                app, class_id=class_id, admission_number="H1111", roll_number=2,
                name="ALI   KHAN", father_name="AHMAD  KHAN",
            )
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE students SET father_cnic=? WHERE student_id IN (?, ?)",
                    ("1234512345671", first, second),
                )
                connection.commit()
            finally:
                connection.close()

            groups = app.student_service.find_suspected_duplicate_student_groups()
            match = next(group for group in groups if set(group["student_ids"]) == {first, second})
            self.assertEqual(match["student_name"], "ALI KHAN")
            self.assertEqual(match["father_name"], "AHMAD KHAN")
            self.assertEqual(match["father_cnic"], "1234512345671")
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM students")["n"]), 2
            )

            with self.assertRaisesRegex(ValueError, "Student Record Already Exists"):
                app.student_service.add_student(**add_student_values(
                    class_id, "H1112", 3,
                    student_name="  Ali    Khan ",
                    father_name=" Ahmad   Khan ",
                    father_cnic="1234512345671",
                ))

            unique = app.student_service.add_student(**add_student_values(
                class_id, "H1113", 3,
                student_name="  Bilal    Khan ",
                father_name=" Rashid   Khan ",
                father_cnic="1111122222333",
            ))
            stored = fetch_one(app, "SELECT student_name, father_name FROM students WHERE student_id=?", (unique,))
            self.assertEqual(stored["student_name"], "BILAL KHAN")
            self.assertEqual(stored["father_name"], "RASHID KHAN")

            with self.assertRaisesRegex(ValueError, "normalized identity"):
                update_from_row(
                    app, unique,
                    student_name="ALI   KHAN",
                    father_name=" AHMAD KHAN ",
                    father_cnic="1234512345671",
                )

    def test_class_4_to_10_section_rule_gender_lanes_and_legacy_reporting(self):
        with IsolatedApplication() as app:
            class_4a = app.class_service.add_class("4", "A")
            class_4g = app.class_service.add_class("4", "G")
            self.assertTrue(app.class_service.validate_section("Z", "10"))
            for invalid in ("AA", "AB", "A1", "4-1"):
                self.assertFalse(app.class_service.validate_section(invalid, "4"))
            with self.assertRaisesRegex(ValueError, "exactly one section letter"):
                app.class_service.add_class("4", "AA")
            with self.assertRaisesRegex(ValueError, "reserved for Male"):
                app.student_service.validate_class_4_plus_gender_assignment(
                    class_4a, "Female", "Active"
                )
            with self.assertRaisesRegex(ValueError, "reserved for Female"):
                app.student_service.validate_class_4_plus_gender_assignment(
                    class_4g, "Male", "Active"
                )

            invalid_id = insert_class(app, "4", "AA")
            operational_ids = {int(row["id"]) for row in app.class_service.get_all_classes()}
            self.assertNotIn(invalid_id, operational_ids)
            report = app.class_service.get_invalid_class_section_report()
            self.assertIn(invalid_id, {row["class_id"] for row in report})
            result = app.class_service.set_class_active(invalid_id, False)
            self.assertFalse(result["is_active"])

    def test_class_3_to_4_promotion_routes_gender_and_excludes_invalid_sections(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "3", "A")
            class_4a = insert_class(app, "4", "A")
            class_4g = insert_class(app, "4", "G")
            invalid_4aa = insert_class(app, "4", "AA")
            male = insert_student(
                app, class_id=source, admission_number="H1120", roll_number=1,
                name="Male Student", gender="MALE",
            )
            female = insert_student(
                app, class_id=source, admission_number="H1121", roll_number=2,
                name="Female Student", gender="FEMALE",
            )
            app.fee_service.set_class_fee(source, 1200, 4, 2026)
            app.fee_service.set_class_fee(class_4a, 1400, 4, 2027)
            app.fee_service.set_class_fee(class_4g, 1400, 4, 2027)

            with allowed_promotion_window(app):
                app.promotion_full_class_v2.promote_full_class(
                    source, {male: "P", female: "P"}, "v11-regression"
                )

            self.assertEqual(int(fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (male,))["class_id"]), class_4a)
            self.assertEqual(int(fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (female,))["class_id"]), class_4g)
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM students WHERE class_id=?", (invalid_4aa,))["n"]), 0
            )

    def test_class_3_to_4_missing_gender_capacity_rolls_back_everything(self):
        with IsolatedApplication() as app:
            source = insert_class(app, "3", "A")
            class_4a = insert_class(app, "4", "A")
            connection = app.connect()
            try:
                connection.execute("UPDATE classes SET student_capacity=1 WHERE id=?", (class_4a,))
                connection.commit()
            finally:
                connection.close()
            insert_student(
                app, class_id=class_4a, admission_number="H1122", roll_number=1,
                name="Existing Male", gender="MALE",
            )
            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO promotion_batches(
                        source_class_id, source_class_name, destination_class_id,
                        destination_class_name, promotion_datetime, total_students,
                        passed_students, failed_students, graduated_students,
                        created_by, is_reversed,source_session_start_year,
                        target_session_start_year,effective_date,promotion_mode
                    ) VALUES (?, '4A', NULL, 'No Passed Students',
                              '2027-03-01 09:00:00', 1, 0, 1, 0, 'test', 0,
                              2026,2027,'2027-04-01','NORMAL')
                    """,
                    (class_4a,),
                )
                connection.commit()
            finally:
                connection.close()
            incoming = insert_student(
                app, class_id=source, admission_number="H1123", roll_number=1,
                name="Incoming Male", gender="MALE",
            )
            app.fee_service.set_class_fee(source, 1200, 4, 2026)
            app.fee_service.set_class_fee(class_4a, 1400, 4, 2027)

            with self.assertRaisesRegex(ValueError, "capacity|full|additional"):
                with allowed_promotion_window(app):
                    app.promotion_full_class_v2.promote_full_class(
                        source, {incoming: "P"}, "v11-regression"
                    )
            row = fetch_one(app, "SELECT class_id, roll_number FROM students WHERE student_id=?", (incoming,))
            self.assertEqual(int(row["class_id"]), source)
            self.assertEqual(int(row["roll_number"]), 1)
            self.assertEqual(
                int(fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM promotion_batches WHERE source_class_id=?",
                    (source,),
                )["n"]),
                0,
            )

    def test_reactivation_backend_always_assigns_committed_fresh_roll(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            dropped = insert_student(
                app, class_id=class_id, admission_number="H1130", roll_number=1,
                name="Dropped Student", status="Dropped",
            )
            insert_student(
                app, class_id=class_id, admission_number="H1131", roll_number=2,
                name="Active Student",
            )
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)
            result = app.student_service.reactivate_student(
                dropped, class_id=class_id, updated_by="v11-regression"
            )
            self.assertEqual(int(result["roll_number"]), 3)
            self.assertEqual(result["student_status"], "Active")
            history = fetch_one(
                app,
                "SELECT changed_by FROM student_class_history WHERE student_id=? ORDER BY history_id DESC LIMIT 1",
                (dropped,),
            )
            self.assertTrue(str(history["changed_by"]).startswith("REACTIVATION:"))

    def test_father_name_ui_and_combined_service_search(self):
        source = (PROJECT_ROOT / "app/modules/student_management.py").read_text(encoding="utf-8")
        self.assertIn("self.father_name_search_input = QLineEdit()", source)
        self.assertIn("father_name=father_name", source)

        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            target = insert_student(
                app, class_id=class_id, admission_number="H1140", roll_number=1,
                name="ALI", father_name="AHMAD KHAN",
            )
            insert_student(
                app, class_id=class_id, admission_number="H1141", roll_number=2,
                name="ALI", father_name="RASHID KHAN",
            )
            rows = app.student_service.search_students(
                student_name=" ali ", father_name=" Ahmad   Khan ", include_archived=True
            )
            self.assertEqual([int(row["student_id"]) for row in rows], [target])

    def test_class_fee_status_ensures_once_and_preserves_status_calculation(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            first = insert_student(app, class_id=class_id, admission_number="H1150", roll_number=1)
            second = insert_student(app, class_id=class_id, admission_number="H1151", roll_number=2, name="Second Student")
            app.fee_service.set_class_fee(class_id, 1000, 4, 2026)

            original = app.fee_service.ensure_fee_records_generated
            with mock.patch.object(
                app.fee_service,
                "ensure_fee_records_generated",
                wraps=original,
            ) as ensure_mock, mock.patch.object(app.fee_service, "datetime", FixedJulyDateTime):
                rows = app.fee_service.get_class_fee_status_rows(class_id)
            self.assertEqual(ensure_mock.call_count, 0)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["report_read_only"] for row in rows))
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records")["n"]),
                0,
            )

            # Operational fee generation remains deliberate and separate from
            # opening the read-only status report.
            app.fee_service.ensure_fee_records_generated(first)

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE student_fee_records SET status='Paid', paid_date='2026-06-30' "
                    "WHERE student_id=? AND month_number IN (4,5,6)",
                    (first,),
                )
                connection.commit()
            finally:
                connection.close()

            with mock.patch.object(app.fee_service, "datetime", FixedJulyDateTime):
                snapshot = app.fee_service.get_student_fee_status_snapshot(first)
            self.assertEqual(snapshot["color_status"], "none")
            self.assertEqual(snapshot["last_paid_month"], "June 2026")
            self.assertIn("July 2026", snapshot["unpaid_months"])
            self.assertEqual(snapshot["total_unpaid"], 1000.0)

    def test_class_list_export_adds_pdf_extension_and_returns_actual_path(self):
        with IsolatedApplication() as app:
            self.assertEqual(
                app.class_service.ensure_pdf_extension("Class_3A_Active_Students"),
                "Class_3A_Active_Students.pdf",
            )
            self.assertEqual(
                app.class_service.ensure_pdf_extension("Class_3A.PDF"),
                "Class_3A.PDF",
            )
            output = app.root / "Class_3A_Active_Students"
            actual = app.class_service.export_student_list_pdf(
                "3A", "Active Students", [], str(output)
            )
            self.assertTrue(actual.lower().endswith(".pdf"))
            self.assertTrue(Path(actual).is_file())


if __name__ == "__main__":
    unittest.main()
