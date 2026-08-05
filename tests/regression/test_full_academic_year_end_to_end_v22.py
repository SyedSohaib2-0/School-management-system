from __future__ import annotations

from datetime import date as real_date, datetime as real_datetime
import importlib
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    allowed_promotion_window,
    fetch_all,
    fetch_one,
)


CURRENT_SESSION_START = 2026
NEXT_SESSION_START = 2027
LOWER_LEVELS = ["PLAY GROUP", "NURSERY", "KG", "1", "2", "3"]
NUMERIC_LANES = [(str(level), section) for level in range(4, 11) for section in ("A", "G")]
CLASS_SPECS = [(level, "A") for level in LOWER_LEVELS] + NUMERIC_LANES


class July2026DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 20, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class January2027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 1, 20, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class March2027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 3, 25, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class March2027Date(real_date):
    @classmethod
    def today(cls):
        return cls(2027, 3, 25)


class April2027DateTime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2027, 4, 5, 9, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class FullAcademicYearEndToEndV22Tests(unittest.TestCase):
    """Simulate one complete July-to-April school cycle across every class lane."""

    @staticmethod
    def _load_coming_service(app):
        service = importlib.import_module("app.services.coming_session_service")
        service.get_connection = lambda *args, **kwargs: app.connect()
        return service

    @staticmethod
    def _class_key(level: str, section: str) -> str:
        return f"{level}:{section}"

    @staticmethod
    def _class_name(level: str, section: str) -> str:
        return f"{level} {section}" if level in {"PLAY GROUP", "NURSERY", "KG"} else f"{level}{section}"

    @staticmethod
    def _gender_for(level: str, section: str, role: str) -> str:
        if level.isdigit() and int(level) >= 4:
            return "Female" if section == "G" else "Male"
        if level == "3" and role == "PASS_ARREARS":
            return "Female"
        return "Male"

    @staticmethod
    def _add_student(
        app,
        *,
        class_id: int,
        admission_number: str,
        student_name: str,
        father_cnic: str,
        gender: str,
        admission_year: int,
        admission_month: int,
        admission_day: int,
        coming: bool,
        clock,
    ) -> int:
        roll = 999 if coming else app.student_service.generate_roll_number(class_id)
        with mock.patch.object(app.student_service, "datetime", clock):
            return app.student_service.add_student(
                admission_number=admission_number,
                roll_number=roll,
                class_id=class_id,
                student_name=student_name,
                gender=gender,
                father_name=f"{student_name} FATHER",
                father_cnic=father_cnic,
                father_contact_number="+920000000000",
                whatsapp_number="",
                current_address="CURRENT ADDRESS",
                permanent_address="PERMANENT ADDRESS",
                school_leaving_certificate_submitted=True,
                form_b_submitted=False,
                birth_certificate_submitted=True,
                date_of_birth="01-01-2014",
                admission_year=admission_year,
                admission_month=admission_month,
                admission_day=admission_day,
                admission_time="09:00:00",
                created_by="full-year-regression",
                coming_session_admission=coming,
            )

    @staticmethod
    def _fee_rows(app, student_id: int):
        return fetch_all(
            app,
            """
            SELECT id,class_id,session_year,month_number,year,amount,status
            FROM student_fee_records
            WHERE student_id=? AND fee_type='Monthly'
            ORDER BY CAST(SUBSTR(session_year,1,4) AS INTEGER),
                     CASE WHEN month_number>=4 THEN month_number ELSE month_number+12 END,
                     year,id
            """,
            (student_id,),
        )

    @staticmethod
    def _assert_continuous_rolls(testcase, app, class_id: int):
        rows = fetch_all(
            app,
            "SELECT student_id,roll_number FROM students "
            "WHERE class_id=? AND student_status='Active' ORDER BY roll_number,student_id",
            (class_id,),
        )
        rolls = [int(row["roll_number"]) for row in rows]
        testcase.assertEqual(rolls, list(range(1, len(rows) + 1)))
        testcase.assertEqual(len(rolls), len(set(rolls)))

    def test_complete_school_year_july_admission_fees_promotion_and_april_activation(self):
        with IsolatedApplication() as app:
            coming_service = self._load_coming_service(app)
            class_ids: dict[str, int] = {}
            current_fees: dict[str, float] = {}
            next_fees: dict[str, float] = {}

            # 1. Build every operational class lane. Class 4-10 have both
            #    the male A lane and female G lane.
            for index, (level, section) in enumerate(CLASS_SPECS, start=1):
                key = self._class_key(level, section)
                class_ids[key] = app.class_service.add_class(
                    level, section, student_capacity=12
                )
                current_fees[key] = float(900 + index * 25)
                next_fees[key] = float(1200 + index * 25)
                app.fee_service.set_class_fee(
                    class_ids[key], current_fees[key], 4, CURRENT_SESSION_START,
                    created_by="full-year-regression",
                    change_reason="Current-session full-year test fee",
                )
                # Deliberately omit 2A's next-session fee initially. The test
                # later proves promotion is atomically blocked until it is set.
                if key != "2:A":
                    app.fee_service.set_class_fee(
                        class_ids[key], next_fees[key], 4, NEXT_SESSION_START,
                        created_by="full-year-regression",
                        change_reason="Next-session full-year test fee",
                    )

            self.assertFalse(
                coming_service.is_coming_session_option_available(
                    2026, 7, 10, today=real_date(2026, 7, 20)
                )
            )

            # 2. July normal admissions. Every class/lane receives one fully
            #    paid pass, one partially paid pass, and one unpaid failure.
            active_students: dict[int, dict] = {}
            student_counter = 10000
            cnic_counter = 4000000000000
            for level, section in CLASS_SPECS:
                key = self._class_key(level, section)
                for role in ("PASS_PAID", "PASS_ARREARS", "FAIL_UNPAID"):
                    student_counter += 1
                    cnic_counter += 1
                    gender = self._gender_for(level, section, role)
                    name = f"JULY {level} {section} {role}"
                    student_id = self._add_student(
                        app,
                        class_id=class_ids[key],
                        admission_number=f"H{student_counter}",
                        student_name=name,
                        father_cnic=f"{cnic_counter:013d}",
                        gender=gender,
                        admission_year=2026,
                        admission_month=7,
                        admission_day=10,
                        coming=False,
                        clock=July2026DateTime,
                    )
                    active_students[student_id] = {
                        "original_class_key": key,
                        "original_class_id": class_ids[key],
                        "level": level,
                        "section": section,
                        "gender": gender,
                        "role": role,
                        "admission_number": f"H{student_counter}",
                    }

            self.assertEqual(len(active_students), len(CLASS_SPECS) * 3)

            # Class 4+ gender routing is enforced for both normal and pending
            # admissions; a female cannot be inserted into male section A.
            with self.assertRaisesRegex(ValueError, "(?i)gender|section|reserved"):
                student_counter += 1
                cnic_counter += 1
                self._add_student(
                    app,
                    class_id=class_ids["4:A"],
                    admission_number=f"H{student_counter}",
                    student_name="WRONG FEMALE ROUTE",
                    father_cnic=f"{cnic_counter:013d}",
                    gender="Female",
                    admission_year=2026,
                    admission_month=7,
                    admission_day=10,
                    coming=False,
                    clock=July2026DateTime,
                )

            # 3. July admissions must start fees in July, not April. Generate
            #    through March and create three payment states per class.
            march_as_of = March2027DateTime.now()
            total_paid_fee_rows = 0
            total_payment_transactions = 0
            for student_id, meta in active_students.items():
                app.fee_service.ensure_fee_records_generated(
                    student_id, as_of=march_as_of
                )
                rows = self._fee_rows(app, student_id)
                periods = [(int(row["month_number"]), int(row["year"])) for row in rows]
                self.assertEqual(len(rows), 9)
                self.assertEqual(periods[0], (7, 2026))
                self.assertEqual(periods[-1], (3, 2027))
                self.assertNotIn((4, 2026), periods)
                self.assertNotIn((5, 2026), periods)
                self.assertNotIn((6, 2026), periods)
                self.assertTrue(
                    all(float(row["amount"]) == current_fees[meta["original_class_key"]] for row in rows)
                )

                if meta["role"] == "PASS_PAID":
                    selected = [int(row["id"]) for row in rows]
                elif meta["role"] == "PASS_ARREARS":
                    selected = [int(row["id"]) for row in rows[:6]]
                else:
                    selected = []

                if selected:
                    with mock.patch.object(app.fee_service, "datetime", March2027DateTime):
                        payment = app.fee_service.process_payment(
                            student_id, selected, received_by="full-year-regression"
                        )
                    total_payment_transactions += 1
                    total_paid_fee_rows += len(selected)
                    receipt_items = fetch_all(
                        app,
                        "SELECT student_fee_record_id,amount FROM fee_receipts "
                        "WHERE payment_id=? AND status='Paid' ORDER BY id",
                        (int(payment["payment_id"]),),
                    )
                    self.assertEqual(len(receipt_items), len(selected))
                    self.assertEqual(
                        round(sum(float(item["amount"]) for item in receipt_items), 2),
                        round(float(payment["total_paid"]), 2),
                    )

                refreshed = self._fee_rows(app, student_id)
                paid_count = sum(row["status"] == "Paid" for row in refreshed)
                unpaid_count = sum(row["status"] == "Unpaid" for row in refreshed)
                if meta["role"] == "PASS_PAID":
                    self.assertEqual((paid_count, unpaid_count), (9, 0))
                elif meta["role"] == "PASS_ARREARS":
                    self.assertEqual((paid_count, unpaid_count), (6, 3))
                else:
                    self.assertEqual((paid_count, unpaid_count), (0, 9))

            self.assertEqual(total_payment_transactions, len(CLASS_SPECS) * 2)
            self.assertEqual(total_paid_fee_rows, len(CLASS_SPECS) * 15)

            # 4. January coming-session admissions in every lane, including
            #    both male and female sections. Play Group gets two same-date
            #    reservations to also lock Tracking-ID tie ordering.
            pending_students: dict[int, dict] = {}
            for level, section in CLASS_SPECS:
                key = self._class_key(level, section)
                additions = 2 if key == "PLAY GROUP:A" else 1
                for ordinal in range(additions):
                    student_counter += 1
                    cnic_counter += 1
                    gender = "Female" if (level.isdigit() and int(level) >= 4 and section == "G") else "Male"
                    name = f"COMING {level} {section} {ordinal + 1}"
                    student_id = self._add_student(
                        app,
                        class_id=class_ids[key],
                        admission_number=f"H{student_counter}",
                        student_name=name,
                        father_cnic=f"{cnic_counter:013d}",
                        gender=gender,
                        admission_year=2027,
                        admission_month=1,
                        admission_day=15,
                        coming=True,
                        clock=January2027DateTime,
                    )
                    pending_students[student_id] = {
                        "class_key": key,
                        "class_id": class_ids[key],
                        "gender": gender,
                    }

            self.assertEqual(len(pending_students), len(CLASS_SPECS) + 1)
            for student_id, meta in pending_students.items():
                row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
                self.assertEqual(row["student_status"], "Coming Session")
                self.assertEqual(row["coming_session_status"], "PENDING")
                self.assertEqual(int(row["target_session_start_year"]), NEXT_SESSION_START)
                self.assertEqual(int(row["roll_number"]), -student_id)
                self.assertIsNone(row["released_roll_number"])
                app.fee_service.ensure_fee_records_generated(student_id, as_of=march_as_of)
                self.assertEqual(len(self._fee_rows(app, student_id)), 0)

                details = app.class_service.get_class_capacity_details(meta["class_id"])
                self.assertGreaterEqual(int(details["coming_session_reserved"]), 1)

                search_rows = app.student_service.search_students(
                    admission_number=row["admission_number"]
                )
                self.assertEqual([int(item["student_id"]) for item in search_rows], [student_id])
                self.assertEqual(app.student_service.display_roll_number(row), "Pending")

            # Pending reservations must not appear in current fee-status lists.
            for key, class_id in class_ids.items():
                current_status_ids = {
                    int(row["student_id"])
                    for row in app.fee_service.get_class_fee_status_rows(class_id)
                }
                self.assertTrue(
                    current_status_ids.isdisjoint(
                        {sid for sid, meta in pending_students.items() if meta["class_key"] == key}
                    )
                )

            # 5. Promote in descending order. Every July admission is included;
            #    every coming-session reservation is excluded. Current dues do
            #    not block Play Group-Class 9 promotion.
            promotion_order = []
            for level in range(10, 3, -1):
                promotion_order.extend([(str(level), "A"), (str(level), "G")])
            promotion_order.extend([
                ("3", "A"), ("2", "A"), ("1", "A"),
                ("KG", "A"), ("NURSERY", "A"), ("PLAY GROUP", "A"),
            ])

            promotion_results_by_student = {
                student_id: ("F" if meta["role"] == "FAIL_UNPAID" else "P")
                for student_id, meta in active_students.items()
            }

            promotion_batches_before = int(
                fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_batches")["n"]
            )
            with allowed_promotion_window(app):
                for level, section in promotion_order:
                    key = self._class_key(level, section)
                    source_id = class_ids[key]
                    source_active = fetch_all(
                        app,
                        "SELECT student_id FROM students WHERE class_id=? AND student_status='Active' ORDER BY student_id",
                        (source_id,),
                    )
                    source_map = {
                        int(row["student_id"]): promotion_results_by_student[int(row["student_id"])]
                        for row in source_active
                    }
                    self.assertEqual(len(source_map), 3)

                    # Missing destination April fee must block atomically. This
                    # is exercised once for 1A -> 2A, then the fee is set and
                    # the exact same promotion succeeds.
                    if key == "1:A":
                        student_snapshot = [
                            tuple(row)
                            for row in fetch_all(
                                app,
                                "SELECT student_id,class_id,roll_number,student_status,failure_count "
                                "FROM students WHERE class_id=? ORDER BY student_id",
                                (source_id,),
                            )
                        ]
                        batches = int(fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_batches")["n"])
                        with self.assertRaisesRegex(ValueError, "(?i)Monthly Fee|April 2027|2A"):
                            app.promotion_full_class_v2.promote_full_class(
                                source_id, source_map, "full-year-regression"
                            )
                        self.assertEqual(
                            int(fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_batches")["n"]),
                            batches,
                        )
                        self.assertEqual(
                            [
                                tuple(row)
                                for row in fetch_all(
                                    app,
                                    "SELECT student_id,class_id,roll_number,student_status,failure_count "
                                    "FROM students WHERE class_id=? ORDER BY student_id",
                                    (source_id,),
                                )
                            ],
                            student_snapshot,
                        )
                        app.fee_service.set_class_fee(
                            class_ids["2:A"], next_fees["2:A"], 4, NEXT_SESSION_START,
                            created_by="full-year-regression",
                            change_reason="Set required 2A destination fee after atomic blocker test",
                        )

                    result = app.promotion_full_class_v2.promote_full_class(
                        source_id, source_map, "full-year-regression"
                    )
                    self.assertEqual(int(result["total_students"]), 3)
                    self.assertEqual(int(result["passed_students"]), 2)
                    self.assertEqual(int(result["failed_students"]), 1)

            promotion_batches_after = int(
                fetch_one(app, "SELECT COUNT(*) AS n FROM promotion_batches")["n"]
            )
            self.assertEqual(
                promotion_batches_after - promotion_batches_before,
                len(CLASS_SPECS),
            )

            # Coming-session students were not promoted and retain no P/F,
            # failure star, promotion history, fee, or positive roll.
            for student_id in pending_students:
                row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
                self.assertEqual(row["student_status"], "Coming Session")
                self.assertEqual(int(row["failure_count"]), 0)
                self.assertEqual(int(row["roll_number"]), -student_id)
                self.assertEqual(
                    int(fetch_one(
                        app,
                        "SELECT COUNT(*) AS n FROM promotion_history WHERE student_id=? AND is_reversed=0",
                        (student_id,),
                    )["n"]),
                    0,
                )
                self.assertEqual(len(self._fee_rows(app, student_id)), 0)

            # Every July normal admission participated in exactly one source
            # promotion. Failed students remain Active with one failure; passed
            # Class 10 students graduate according to their dues.
            for student_id, meta in active_students.items():
                history_count = int(fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM promotion_history WHERE student_id=? AND is_reversed=0",
                    (student_id,),
                )["n"])
                self.assertEqual(history_count, 1)
                row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
                if meta["level"] == "10" and meta["role"] == "PASS_PAID":
                    self.assertEqual(row["student_status"], "Graduated")
                    self.assertLess(int(row["roll_number"]), 0)
                elif meta["level"] == "10" and meta["role"] == "PASS_ARREARS":
                    self.assertEqual(row["student_status"], "Partially Graduated")
                    self.assertLess(int(row["roll_number"]), 0)
                    self.assertEqual(
                        int(fetch_one(
                            app,
                            "SELECT COUNT(*) AS n FROM student_fee_records "
                            "WHERE student_id=? AND fee_type='Monthly' AND status='Unpaid'",
                            (student_id,),
                        )["n"]),
                        3,
                    )
                elif meta["role"] == "FAIL_UNPAID":
                    self.assertEqual(row["student_status"], "Active")
                    self.assertEqual(int(row["class_id"]), meta["original_class_id"])
                    self.assertEqual(int(row["failure_count"]), 1)
                else:
                    self.assertEqual(row["student_status"], "Active")
                    self.assertEqual(int(row["failure_count"]), 0)
                    self.assertNotEqual(int(row["class_id"]), meta["original_class_id"])

            # 6. April startup activates all reservations effective 1 April,
            #    then the full final class composition receives continuous,
            #    duplicate-free rolls (including Class 10).
            activation = coming_service.activate_due_coming_session_admissions(
                as_of=April2027DateTime.now(),
                changed_by="full-year-regression",
            )
            self.assertTrue(activation["success"], activation)
            self.assertEqual(int(activation["activated_count"]), len(pending_students))
            self.assertEqual(activation["errors"], [])
            self.assertEqual(
                int(fetch_one(
                    app,
                    "SELECT COUNT(*) AS n FROM students WHERE student_status='Coming Session'",
                )["n"]),
                0,
            )

            for class_id in class_ids.values():
                self._assert_continuous_rolls(self, app, class_id)
                details = app.class_service.get_class_capacity_details(class_id)
                self.assertEqual(int(details["coming_session_reserved"]), 0)
                self.assertLessEqual(
                    int(details["active_students"]), int(details["student_capacity"])
                )

            # Play Group same-date pending admissions are ordered by Tracking ID.
            play_pending_ids = [
                sid for sid, meta in pending_students.items() if meta["class_key"] == "PLAY GROUP:A"
            ]
            play_rows = fetch_all(
                app,
                "SELECT student_id,tracking_id,roll_number FROM students "
                "WHERE student_id IN (?,?) ORDER BY roll_number",
                tuple(play_pending_ids),
            )
            self.assertEqual(
                [str(row["tracking_id"]) for row in play_rows],
                sorted(str(row["tracking_id"]) for row in play_rows),
            )

            # 7. Generate April-to-March new-session fees and verify the source
            #    of truth for every lifecycle: promoted, retained, activated,
            #    graduated, and partially graduated.
            april_as_of = April2027DateTime.now()
            for student_id in list(active_students) + list(pending_students):
                app.fee_service.ensure_fee_records_generated(
                    student_id, as_of=april_as_of
                )

            for student_id, meta in pending_students.items():
                rows = self._fee_rows(app, student_id)
                periods = [(int(row["month_number"]), int(row["year"])) for row in rows]
                self.assertEqual(len(rows), 12)
                self.assertEqual(periods[0], (4, 2027))
                self.assertEqual(periods[-1], (3, 2028))
                self.assertNotIn((3, 2027), periods)
                self.assertTrue(
                    all(int(row["class_id"]) == meta["class_id"] for row in rows)
                )
                self.assertTrue(
                    all(float(row["amount"]) == next_fees[meta["class_key"]] for row in rows)
                )
                row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
                self.assertEqual(row["student_status"], "Active")
                self.assertEqual(row["coming_session_status"], "ACTIVATED")
                self.assertEqual(row["coming_session_effective_date"], "2027-04-01")
                self.assertGreater(int(row["roll_number"]), 0)

            for student_id, meta in active_students.items():
                row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_id,))
                rows = self._fee_rows(app, student_id)
                old_rows = [fee for fee in rows if fee["session_year"] == "2026-2027"]
                new_rows = [fee for fee in rows if fee["session_year"] == "2027-2028"]
                self.assertEqual(len(old_rows), 9)
                self.assertTrue(
                    all(int(fee["class_id"]) == meta["original_class_id"] for fee in old_rows)
                )

                if row["student_status"] in {"Graduated", "Partially Graduated"}:
                    self.assertEqual(new_rows, [])
                    continue

                self.assertEqual(len(new_rows), 12)
                self.assertEqual(
                    (int(new_rows[0]["month_number"]), int(new_rows[0]["year"])),
                    (4, 2027),
                )
                current_class_id = int(row["class_id"])
                current_key = next(key for key, cid in class_ids.items() if cid == current_class_id)
                self.assertTrue(all(int(fee["class_id"]) == current_class_id for fee in new_rows))
                self.assertTrue(all(float(fee["amount"]) == next_fees[current_key] for fee in new_rows))

                if meta["role"] == "PASS_PAID":
                    self.assertEqual(sum(fee["status"] == "Unpaid" for fee in old_rows), 0)
                elif meta["role"] == "PASS_ARREARS":
                    self.assertEqual(sum(fee["status"] == "Unpaid" for fee in old_rows), 3)
                else:
                    self.assertEqual(sum(fee["status"] == "Unpaid" for fee in old_rows), 9)

            # Final male/female routing and Class 10 composition checks.
            for level in range(4, 11):
                for section, expected_gender in (("A", "Male"), ("G", "Female")):
                    class_id = class_ids[f"{level}:{section}"]
                    genders = {
                        str(row["gender"]).strip().title()
                        for row in fetch_all(
                            app,
                            "SELECT gender FROM students WHERE class_id=? AND student_status='Active'",
                            (class_id,),
                        )
                    }
                    self.assertEqual(genders, {expected_gender})

            for section in ("A", "G"):
                class_10_id = class_ids[f"10:{section}"]
                class_10_active = fetch_all(
                    app,
                    "SELECT student_id,failure_count,roll_number FROM students "
                    "WHERE class_id=? AND student_status='Active' ORDER BY roll_number",
                    (class_10_id,),
                )
                # One retained Class 10 failure + two promoted Class 9 students
                # + one activated coming-session admission.
                self.assertEqual(len(class_10_active), 4)
                self.assertEqual(
                    sum(int(row["failure_count"]) == 1 for row in class_10_active),
                    1,
                )
                self.assertEqual(
                    [int(row["roll_number"]) for row in class_10_active],
                    [1, 2, 3, 4],
                )

            # Database-level integrity and foreign keys remain valid after the
            # largest lifecycle simulation.
            schema_integrity = importlib.import_module("app.schema_integrity")
            strict = app.strict_connect()
            try:
                with mock.patch.object(schema_integrity, "datetime", April2027DateTime):
                    valid, message = schema_integrity.validate_current_schema(strict)
                foreign_key_errors = strict.execute("PRAGMA foreign_key_check").fetchall()
                integrity = strict.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                strict.close()
            self.assertTrue(valid, message)
            self.assertEqual(foreign_key_errors, [])
            self.assertEqual(str(integrity).lower(), "ok")


if __name__ == "__main__":
    unittest.main()
