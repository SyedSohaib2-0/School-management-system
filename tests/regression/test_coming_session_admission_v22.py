from __future__ import annotations

from datetime import date as real_date, datetime as real_datetime
import importlib
from pathlib import Path
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    allowed_promotion_window,
    fetch_all,
    fetch_one,
    insert_student,
)


class March2027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 20, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class March252027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 25, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class March252027Date(real_date):
    @classmethod
    def today(cls):
        return cls(2027, 3, 25)


class ComingSessionAdmissionV22Tests(unittest.TestCase):
    @staticmethod
    def _load_service(app):
        service = importlib.import_module("app.services.coming_session_service")
        service.get_connection = lambda *args, **kwargs: app.connect()
        return service

    @staticmethod
    def _add_class(app, level, section, capacity=40):
        return app.class_service.add_class(level, section, student_capacity=capacity)

    @staticmethod
    def _set_fee(app, class_id, amount, month, year):
        app.fee_service.set_class_fee(
            class_id=class_id,
            monthly_fee=amount,
            effective_from_month=month,
            effective_from_year=year,
            created_by="test",
            change_reason="Coming-session regression setup",
        )

    @staticmethod
    def _mark_retained_promotion_completed(
        app,
        *,
        class_id: int,
        student_ids,
        promoted_at: str = "2027-03-20 10:00:00",
    ) -> None:
        connection = app.connect()
        try:
            class_row = connection.execute(
                "SELECT class_name FROM classes WHERE id=?",
                (int(class_id),),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO promotion_batches(
                    source_class_id,source_class_name,destination_class_id,
                    destination_class_name,promotion_datetime,total_students,
                    passed_students,failed_students,graduated_students,
                    created_by,is_reversed,source_session_start_year,
                    target_session_start_year,effective_date,promotion_mode
                ) VALUES(?,?,?,?,?,?,?,?,?,'test',0,2026,2027,'2027-04-01','NORMAL')
                """,
                (
                    int(class_id),
                    class_row["class_name"],
                    int(class_id),
                    class_row["class_name"],
                    promoted_at,
                    len(list(student_ids)),
                    0,
                    len(list(student_ids)),
                    0,
                ),
            )
            batch_id = int(cursor.lastrowid)
            for student_id in student_ids:
                row = connection.execute(
                    "SELECT * FROM students WHERE student_id=?",
                    (int(student_id),),
                ).fetchone()
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
                        batch_id,
                        int(student_id),
                        row["admission_number"],
                        row["student_name"],
                        row["father_name"],
                        int(class_id),
                        class_row["class_name"],
                        int(class_id),
                        class_row["class_name"],
                        int(row["roll_number"]),
                        int(row["roll_number"]),
                        "F",
                        "Active",
                        "Active",
                        int(row["failure_count"] or 0),
                        int(row["failure_count"] or 0) + 1,
                        promoted_at,
                    ),
                )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _add_student(
        app,
        *,
        class_id,
        admission_number,
        roll_number=1,
        name="COMING STUDENT",
        gender="Male",
        admission_day=20,
        admission_month=3,
        admission_year=2027,
        coming=False,
        father_cnic="1234512345671",
    ):
        with mock.patch.object(app.student_service, "datetime", March2027DateTime):
            return app.student_service.add_student(
                admission_number=admission_number,
                roll_number=roll_number,
                class_id=class_id,
                student_name=name,
                gender=gender,
                father_name=f"{name} FATHER",
                father_cnic=father_cnic,
                father_contact_number="+920000000000",
                whatsapp_number="",
                current_address="CURRENT ADDRESS",
                permanent_address="PERMANENT ADDRESS",
                school_leaving_certificate_submitted=True,
                form_b_submitted=False,
                birth_certificate_submitted=True,
                date_of_birth="01-01-2015",
                admission_year=admission_year,
                admission_month=admission_month,
                admission_day=admission_day,
                admission_time="09:00:00",
                created_by="test",
                coming_session_admission=coming,
            )

    def test_pending_admission_has_no_roll_fee_or_promotion_membership(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_4a = self._add_class(app, "4", "A")
            class_5a = self._add_class(app, "5", "A")
            self._set_fee(app, class_4a, 900, 4, 2026)
            self._set_fee(app, class_5a, 1000, 4, 2027)
            active_id = insert_student(
                app, class_id=class_4a, admission_number="H0099",
                roll_number=1, name="CURRENT STUDENT",
                father_name="CURRENT FATHER", father_cnic="1234512345699",
                gender="MALE",
            )

            student_id = self._add_student(
                app,
                class_id=class_4a,
                admission_number="H0001",
                coming=True,
            )
            row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(row["student_status"], "Coming Session")
            self.assertEqual(int(row["roll_number"]), -student_id)
            self.assertIsNone(row["released_roll_number"])
            self.assertEqual(int(row["target_session_start_year"]), 2027)
            self.assertEqual(row["coming_session_status"], "PENDING")
            self.assertEqual(app.student_service.display_roll_number(row), "Pending")

            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=March2027DateTime.now()
            )
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=?", (student_id,))["n"]),
                0,
            )

            with allowed_promotion_window(app):
                plan = app.promotion_full_class_v2.preview_full_class_promotion_plan(
                    class_4a, {active_id: "F"}
                )
            self.assertEqual(plan["total_students"], 1)
            self.assertEqual(plan["failed_students"], 1)

            details = app.class_service.get_class_capacity_details(class_4a)
            self.assertEqual(details["active_students"], 1)
            self.assertEqual(details["coming_session_reserved"], 1)
            self.assertEqual(details["available_seats"], 38)

            with self.assertRaisesRegex(ValueError, "(?i)gender|section|reserved"):
                self._add_student(
                    app,
                    class_id=class_4a,
                    admission_number="H0002",
                    name="FEMALE WRONG ROUTE",
                    gender="Female",
                    coming=True,
                    father_cnic="1234512345672",
                )

            strict = app.strict_connect()
            schema_integrity = importlib.import_module("app.schema_integrity")
            try:
                with mock.patch.object(
                    schema_integrity, "datetime", March252027DateTime
                ):
                    valid, message = schema_integrity.validate_current_schema(strict)
            finally:
                strict.close()
            self.assertTrue(valid, message)
            del service

    def test_edit_conversion_is_reversible_before_session_and_uses_admission_month(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_4a = self._add_class(app, "4", "A")
            self._set_fee(app, class_4a, 900, 4, 2026)
            self._set_fee(app, class_4a, 1100, 4, 2027)
            student_id = self._add_student(
                app,
                class_id=class_4a,
                admission_number="H0003",
                coming=False,
            )
            app.fee_service.ensure_fee_records_generated(
                student_id, as_of=March2027DateTime.now()
            )
            self.assertEqual(
                [(int(r["month_number"]), int(r["year"])) for r in fetch_all(
                    app,
                    "SELECT month_number,year FROM student_fee_records WHERE student_id=? ORDER BY year,month_number",
                    (student_id,),
                )],
                [(3, 2027)],
            )

            profile = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            with mock.patch.object(service, "datetime", March252027DateTime):
                service.save_pending_admission_edit(
                    student_id=student_id,
                    desired_coming_session=True,
                    class_id=class_4a,
                    student_name=profile["student_name"],
                    gender=profile["gender"],
                    father_name=profile["father_name"],
                    father_cnic=profile["father_cnic"],
                    father_contact_number=profile["father_contact_number"],
                    whatsapp_number=profile["whatsapp_number"],
                    current_address=profile["current_address"],
                    permanent_address=profile["permanent_address"],
                    school_leaving_certificate_submitted=True,
                    form_b_submitted=False,
                    birth_certificate_submitted=True,
                    date_of_birth=app.student_service.format_date_of_birth_for_display(profile["date_of_birth"]),
                    updated_by="test",
                )
            pending = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(pending["student_status"], "Coming Session")
            self.assertEqual(int(pending["roll_number"]), -student_id)
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_fee_records WHERE student_id=?", (student_id,))["n"]),
                0,
            )

            with mock.patch.object(service, "datetime", March252027DateTime):
                service.save_pending_admission_edit(
                    student_id=student_id,
                    desired_coming_session=False,
                    class_id=class_4a,
                    student_name=pending["student_name"],
                    gender=pending["gender"],
                    father_name=pending["father_name"],
                    father_cnic=pending["father_cnic"],
                    father_contact_number=pending["father_contact_number"],
                    whatsapp_number=pending["whatsapp_number"],
                    current_address=pending["current_address"],
                    permanent_address=pending["permanent_address"],
                    school_leaving_certificate_submitted=True,
                    form_b_submitted=False,
                    birth_certificate_submitted=True,
                    date_of_birth=app.student_service.format_date_of_birth_for_display(pending["date_of_birth"]),
                    updated_by="test",
                )
            active = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(active["student_status"], "Active")
            self.assertGreater(int(active["roll_number"]), 0)
            self.assertIsNone(active["coming_session_status"])
            fee_rows = fetch_all(
                app,
                "SELECT month_number,year,amount FROM student_fee_records WHERE student_id=? ORDER BY year,month_number",
                (student_id,),
            )
            self.assertEqual([(int(r["month_number"]), int(r["year"])) for r in fee_rows], [(3, 2027)])
            self.assertEqual(float(fee_rows[0]["amount"]), 900.0)

    def test_pending_class_change_preserves_lifecycle_and_gender_route(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_4a = self._add_class(app, "4", "A")
            class_4b = self._add_class(app, "4", "B")
            class_4g = self._add_class(app, "4", "G")
            student_id = self._add_student(
                app, class_id=class_4a, admission_number="H0004", coming=True
            )
            row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            common = dict(
                student_id=student_id,
                desired_coming_session=True,
                student_name=row["student_name"],
                gender=row["gender"],
                father_name=row["father_name"],
                father_cnic=row["father_cnic"],
                father_contact_number=row["father_contact_number"],
                whatsapp_number=row["whatsapp_number"],
                current_address=row["current_address"],
                permanent_address=row["permanent_address"],
                school_leaving_certificate_submitted=True,
                form_b_submitted=False,
                birth_certificate_submitted=True,
                date_of_birth=app.student_service.format_date_of_birth_for_display(row["date_of_birth"]),
                updated_by="test",
            )
            with mock.patch.object(service, "datetime", March252027DateTime):
                service.save_pending_admission_edit(class_id=class_4b, **common)
            moved = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(int(moved["class_id"]), class_4b)
            self.assertEqual(moved["student_status"], "Coming Session")
            self.assertEqual(int(moved["roll_number"]), -student_id)
            with mock.patch.object(service, "datetime", March252027DateTime):
                with self.assertRaisesRegex(ValueError, "(?i)gender|section|reserved"):
                    service.save_pending_admission_edit(class_id=class_4g, **common)
            unchanged = fetch_one(app, "SELECT class_id FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(int(unchanged["class_id"]), class_4b)

    def test_april_activation_assigns_rolls_and_fee_start_candidate(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            play_group = self._add_class(app, "PLAY GROUP", "A")
            self._set_fee(app, play_group, 700, 4, 2027)
            active_id = insert_student(
                app,
                class_id=play_group,
                admission_number="H0100",
                roll_number=8,
                name="EXISTING PLAY GROUP",
                father_name="EXISTING FATHER",
                father_cnic="1234512345680",
            )
            pending_one = self._add_student(
                app,
                class_id=play_group,
                admission_number="H0101",
                name="PENDING ONE",
                admission_day=15,
                admission_month=1,
                coming=True,
                father_cnic="1234512345681",
            )
            pending_two = self._add_student(
                app,
                class_id=play_group,
                admission_number="H0102",
                name="PENDING TWO",
                admission_day=15,
                admission_month=1,
                coming=True,
                father_cnic="1234512345682",
            )
            self._mark_retained_promotion_completed(
                app, class_id=play_group, student_ids=[active_id]
            )
            result = service.activate_due_coming_session_admissions(
                as_of=real_datetime(2027, 4, 5, 9, 0, 0)
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(result["activated_count"], 2)
            rows = fetch_all(
                app,
                "SELECT student_id,student_status,roll_number,coming_session_status,coming_session_effective_date FROM students WHERE class_id=? ORDER BY roll_number",
                (play_group,),
            )
            self.assertEqual([int(r["student_id"]) for r in rows], [active_id, pending_one, pending_two])
            self.assertEqual([int(r["roll_number"]) for r in rows], [1, 2, 3])
            for row in rows[1:]:
                self.assertEqual(row["student_status"], "Active")
                self.assertEqual(row["coming_session_status"], "ACTIVATED")
                self.assertEqual(row["coming_session_effective_date"], "2027-04-01")

            app.fee_service.ensure_fee_records_generated(
                pending_one, as_of=real_datetime(2027, 4, 5, 10, 0, 0)
            )
            fee_rows = fetch_all(
                app,
                "SELECT month_number,year FROM student_fee_records WHERE student_id=? ORDER BY year,month_number",
                (pending_one,),
            )
            generated_periods = [
                (int(r["month_number"]), int(r["year"])) for r in fee_rows
            ]
            self.assertEqual(generated_periods[0], (4, 2027))
            self.assertEqual(generated_periods[-1], (3, 2028))
            self.assertEqual(len(generated_periods), 12)
            self.assertNotIn((3, 2027), generated_periods)

    def test_class_10_activation_recalculates_complete_new_session_roll_sequence(self):
        """Class 10 follows the same annual roll recalculation as Nursery-Class 9."""
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_10a = self._add_class(app, "10", "A")
            self._set_fee(app, class_10a, 1800, 4, 2027)

            retained_one = insert_student(
                app,
                class_id=class_10a,
                admission_number="H0150",
                roll_number=4,
                name="CLASS TEN RETAINED ONE",
                father_name="CLASS TEN FATHER ONE",
                father_cnic="1234512345650",
                gender="MALE",
            )
            retained_two = insert_student(
                app,
                class_id=class_10a,
                admission_number="H0151",
                roll_number=9,
                name="CLASS TEN RETAINED TWO",
                father_name="CLASS TEN FATHER TWO",
                father_cnic="1234512345651",
                gender="MALE",
            )
            pending = self._add_student(
                app,
                class_id=class_10a,
                admission_number="H0152",
                name="CLASS TEN COMING STUDENT",
                coming=True,
                father_cnic="1234512345652",
            )
            self._mark_retained_promotion_completed(
                app,
                class_id=class_10a,
                student_ids=[retained_one, retained_two],
            )

            result = service.activate_due_coming_session_admissions(
                as_of=real_datetime(2027, 4, 5, 9, 0, 0)
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(result["activated_count"], 1)

            rows = fetch_all(
                app,
                """
                SELECT student_id,student_status,roll_number,coming_session_status
                FROM students
                WHERE class_id=? AND student_status='Active'
                ORDER BY roll_number
                """,
                (class_10a,),
            )
            self.assertEqual(
                [int(row["student_id"]) for row in rows],
                [retained_one, retained_two, pending],
            )
            self.assertEqual([int(row["roll_number"]) for row in rows], [1, 2, 3])
            self.assertEqual(rows[-1]["coming_session_status"], "ACTIVATED")

    def test_permanent_cancellation_removes_pending_student_and_never_reuses_tracking_sequence(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_id = self._add_class(app, "3", "A")
            student_id = self._add_student(
                app, class_id=class_id, admission_number="H0200", coming=True
            )
            tracking = fetch_one(app, "SELECT tracking_id FROM students WHERE student_id=?", (student_id,))["tracking_id"]
            with mock.patch.object(service, "date", March252027Date):
                result = service.cancel_pending_admission(student_id, cancelled_by="test")
            self.assertTrue(result["cancelled"])
            self.assertIsNone(fetch_one(app, "SELECT 1 FROM students WHERE student_id=?", (student_id,)))
            self.assertEqual(
                int(fetch_one(app, "SELECT COUNT(*) AS n FROM student_class_history WHERE student_id=?", (student_id,))["n"]),
                0,
            )
            next_id = self._add_student(
                app,
                class_id=class_id,
                admission_number="H0201",
                coming=True,
                father_cnic="1234512345691",
            )
            next_tracking = fetch_one(app, "SELECT tracking_id FROM students WHERE student_id=?", (next_id,))["tracking_id"]
            self.assertNotEqual(tracking, next_tracking)
            self.assertGreater(next_id, student_id)

    def test_promotion_capacity_reserves_pending_coming_session_seat(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            source = self._add_class(app, "3", "A")
            destination = self._add_class(app, "4", "A", capacity=1)
            self._set_fee(app, source, 800, 4, 2026)
            self._set_fee(app, destination, 1000, 4, 2027)
            active_id = insert_student(
                app,
                class_id=source,
                admission_number="H0300",
                roll_number=1,
                name="PROMOTION STUDENT",
                father_name="PROMOTION FATHER",
                father_cnic="1234512345692",
                gender="MALE",
            )
            self._add_student(
                app,
                class_id=destination,
                admission_number="H0301",
                name="RESERVED STUDENT",
                coming=True,
                father_cnic="1234512345693",
            )
            with allowed_promotion_window(app):
                with self.assertRaisesRegex(ValueError, "coming-session|capacity"):
                    app.promotion_full_class_v2.preview_full_class_promotion_plan(
                        source, {active_id: "P"}
                    )
            del service


    def test_due_pending_student_can_correct_reserved_class_but_not_change_admission_type(self):
        with IsolatedApplication() as app:
            service = self._load_service(app)
            class_4a = self._add_class(app, "4", "A")
            class_4b = self._add_class(app, "4", "B")
            self._set_fee(app, class_4b, 1200, 4, 2027)
            student_id = self._add_student(
                app, class_id=class_4a, admission_number="H0400", coming=True
            )
            row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            common = dict(
                student_id=student_id,
                student_name=row["student_name"],
                gender=row["gender"],
                father_name=row["father_name"],
                father_cnic=row["father_cnic"],
                father_contact_number=row["father_contact_number"],
                whatsapp_number=row["whatsapp_number"],
                current_address=row["current_address"],
                permanent_address=row["permanent_address"],
                school_leaving_certificate_submitted=True,
                form_b_submitted=False,
                birth_certificate_submitted=True,
                date_of_birth=app.student_service.format_date_of_birth_for_display(
                    row["date_of_birth"]
                ),
                updated_by="test",
            )
            class April52027DateTime(real_datetime):
                @classmethod
                def now(cls, tz=None):
                    value = cls(2027, 4, 5, 10, 0, 0)
                    return value if tz is None else value.replace(tzinfo=tz)

            with mock.patch.object(service, "datetime", April52027DateTime):
                service.save_pending_admission_edit(
                    desired_coming_session=True, class_id=class_4b, **common
                )
                with self.assertRaisesRegex(ValueError, "already started"):
                    service.save_pending_admission_edit(
                        desired_coming_session=False, class_id=class_4b, **common
                    )
            moved = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(int(moved["class_id"]), class_4b)
            self.assertEqual(moved["student_status"], "Coming Session")
            activation = service.activate_due_coming_session_admissions(
                as_of=real_datetime(2027, 4, 5, 11, 0, 0)
            )
            self.assertTrue(activation["success"], activation)
            activated = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
            self.assertEqual(activated["student_status"], "Active")
            self.assertEqual(int(activated["class_id"]), class_4b)
            self.assertGreater(int(activated["roll_number"]), 0)

    def test_ui_and_schema_static_integration_contract(self):
        root = Path(__file__).resolve().parents[2]
        ui_text = (root / "app/modules/student_management.py").read_text(encoding="utf-8")
        self.assertIn('QCheckBox(\n            "Admission for Coming Session"', ui_text)
        self.assertLess(
            ui_text.index('"Admission for Coming Session"'),
            ui_text.index('"Student Name:"'),
        )
        self.assertIn("Cancel Coming-Session Admission", ui_text)
        self.assertIn('self.roll_number_input.setText("Pending")', ui_text)
        self.assertNotIn("December admission", ui_text)
        database_text = (root / "app/database.py").read_text(encoding="utf-8")
        integrity_text = (root / "app/schema_integrity.py").read_text(encoding="utf-8")
        self.assertIn("CURRENT_SCHEMA_VERSION = 23", database_text)
        self.assertIn("'Coming Session'", database_text)
        self.assertIn("validate_coming_session_integrity", integrity_text)


if __name__ == "__main__":
    unittest.main()
