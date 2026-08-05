from __future__ import annotations

from datetime import datetime
from pathlib import Path
from decimal import Decimal
import calendar
import hashlib
import json
import os
import shutil
import sqlite3
import struct
import time
import unittest
from unittest import mock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tests.regression.support import (
    IsolatedApplication,
    fetch_all,
    fetch_one,
    insert_class,
    insert_student,
)


class FixedJulyDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 7, 29, 10, 0, 0)
        return value if tz is None else value.replace(tzinfo=tz)


class AdditionalBugFixV18ScopeTests(unittest.TestCase):
    def _copy_database(self, app: IsolatedApplication, name: str) -> Path:
        target = app.root / name
        shutil.copy2(app.database_path, target)
        return target

    def _validate_corruption(self, app, name, mutator, expected_text):
        candidate = self._copy_database(app, name)
        connection = sqlite3.connect(candidate)
        try:
            connection.create_function(
                "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
            )
            connection.execute("PRAGMA foreign_keys=OFF")
            mutator(connection)
            connection.commit()
        finally:
            connection.close()
        valid, message = app.backup_service.validate_school_database_schema(
            str(candidate), require_current_schema=True
        )
        self.assertFalse(valid, message)
        self.assertIn(expected_text.lower(), message.lower())

    def _insert_fee_row(
        self, app, *, student_id, class_id, month, year, amount, status="Unpaid"
    ):
        session_start = year if month >= 4 else year - 1
        now = "2026-07-29 10:00:00"
        connection = app.connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO student_fee_records(
                    student_id,class_id,session_year,fee_type,month_name,
                    month_number,year,amount,status,paid_date,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?)
                """,
                (
                    student_id,
                    class_id,
                    f"{session_start}-{session_start + 1}",
                    "Monthly",
                    calendar.month_name[month],
                    month,
                    year,
                    amount,
                    status,
                    now,
                    now,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def test_savepoint_backup_tracking_and_destination_retry_state(self):
        with IsolatedApplication() as app:
            database_path = app.root / "savepoint.db"
            connection = sqlite3.connect(
                database_path, factory=app.database.BackupAwareConnection
            )
            connection.automatic_backup_enabled = False
            connection.execute("CREATE TABLE values_table(id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
            connection.automatic_backup_enabled = True

            scheduler = __import__(
                "app.services.backup_scheduler", fromlist=["schedule_automatic_backup"]
            )
            with mock.patch.object(scheduler, "schedule_automatic_backup") as queued:
                connection.execute("BEGIN")
                connection.execute("SAVEPOINT transient")
                connection.execute("INSERT INTO values_table(value) VALUES('rolled back')")
                connection.execute("ROLLBACK TO transient")
                connection.execute("RELEASE transient")
                connection.commit()
                queued.assert_not_called()

                connection.execute("INSERT INTO values_table(value) VALUES('permanent')")
                connection.commit()
                self.assertEqual(queued.call_count, 1)

                connection.execute("BEGIN")
                connection.execute("INSERT INTO values_table(value) VALUES('outer')")
                connection.execute("SAVEPOINT inner")
                connection.execute("INSERT INTO values_table(value) VALUES('inner rollback')")
                connection.execute("ROLLBACK TO inner")
                connection.execute("RELEASE inner")
                connection.commit()
                self.assertEqual(queued.call_count, 2)

                connection.execute("BEGIN")
                connection.execute("INSERT INTO values_table(value) VALUES('full rollback')")
                connection.rollback()
                self.assertEqual(queued.call_count, 2)
            connection.close()

            with scheduler._scheduler_lock:
                if scheduler._backup_timer is not None:
                    scheduler._backup_timer.cancel()
                scheduler._backup_timer = None
                scheduler._backup_running = False
                scheduler._backup_pending = True
                scheduler._pending_destinations = {"Internal", "Google Drive"}
                scheduler._retry_attempt = 0
                scheduler._scheduler_stopping = False
                scheduler._change_generation = 1

            failure = {
                "success": False,
                "results": [
                    {"destination": "Internal", "success": True, "skipped": False},
                    {"destination": "Google Drive", "success": False, "skipped": False,
                     "message": "offline"},
                ],
            }
            with mock.patch.object(app.backup_service, "run_automatic_backups", return_value=failure):
                scheduler._execute_scheduled_backup()
            status = scheduler.get_backup_scheduler_status()
            self.assertTrue(status["backup_pending"])
            self.assertEqual(status["pending_destinations"], ["Google Drive"])
            self.assertGreaterEqual(status["retry_attempt"], 1)
            self.assertTrue(Path(scheduler.RETRY_STATE_PATH).is_file())
            with scheduler._scheduler_lock:
                if scheduler._backup_timer is not None:
                    scheduler._backup_timer.cancel()
                scheduler._backup_timer = None
                scheduler._backup_pending = False
                scheduler._pending_destinations = None
                scheduler._retry_attempt = 0
                scheduler._persist_retry_state_locked()

    def test_legacy_backup_staging_disappearing_files_and_chunked_compatibility(self):
        with IsolatedApplication() as app:
            destination = app.root / "legacy-list"
            destination.mkdir()
            legacy = destination / "Internal_Backup_2026-07-29_120000.db"
            shutil.copy2(app.database_path, legacy)
            original_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()
            metadata = app.backup_service.list_destination_backup_metadata(
                str(destination), "Internal_Backup"
            )
            self.assertEqual(metadata[0]["status_text"], "Recognized Legacy Backup")
            validated = app.backup_service.validate_destination_backup_record(str(legacy))
            self.assertTrue(validated["is_valid"], validated)
            self.assertEqual(validated["status_text"], "Validated and Restore-Ready")
            self.assertEqual(hashlib.sha256(legacy.read_bytes()).hexdigest(), original_hash)

            survivor = destination / "Internal_Backup_2026-07-29_130000.db"
            shutil.copy2(app.database_path, survivor)
            original_stat = app.backup_service.os.stat

            def intermittent_stat(path, *args, **kwargs):
                if Path(path) == legacy:
                    raise FileNotFoundError("synchronized file disappeared")
                return original_stat(path, *args, **kwargs)

            with mock.patch.object(app.backup_service.os, "stat", side_effect=intermittent_stat):
                listed = app.backup_service._get_destination_backup_files(
                    str(destination), "Internal_Backup"
                )
            self.assertEqual(listed, [str(survivor)])

            password = "V18Streaming#2027"
            self.assertTrue(
                app.backup_crypto_service.configure_backup_security(password, password)["success"]
            )
            connection = app.connect()
            try:
                connection.execute("CREATE TABLE IF NOT EXISTS large_payload(data BLOB)")
                connection.execute(
                    "INSERT INTO large_payload(data) VALUES(?)",
                    (sqlite3.Binary(os.urandom(app.backup_crypto_service.BACKUP_CHUNK_SIZE * 2 + 1234)),),
                )
                connection.commit()
            finally:
                connection.close()
            encrypted = app.root / "streamed.hmsbackup"
            created = app.backup_crypto_service.create_encrypted_backup_from_database(
                str(app.database_path), str(encrypted), "regression"
            )
            self.assertTrue(created["success"], created)
            header = app.backup_crypto_service.read_encrypted_backup_header(str(encrypted))
            self.assertEqual(int(header["format_version"]), 3)
            self.assertGreaterEqual(int(header["chunk_count"]), 3)
            restored = app.root / "streamed-restored.db"
            decrypted = app.backup_crypto_service.decrypt_encrypted_backup_to_path(
                str(encrypted), str(restored)
            )
            self.assertTrue(decrypted["success"], decrypted)
            self.assertEqual(
                hashlib.sha256(app.database_path.read_bytes()).hexdigest(),
                hashlib.sha256(restored.read_bytes()).hexdigest(),
            )

            # Build a format-2 container to verify backward readability.
            crypto = app.backup_crypto_service
            key_id, master_key, recovery_material, material_type = crypto._load_local_key_material()
            self.assertEqual(material_type, "password")
            database_bytes = app.database_path.read_bytes()
            manifest = crypto._collect_database_manifest(str(app.database_path), "legacy-v2")
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            plaintext = struct.pack(">I", len(manifest_bytes)) + manifest_bytes + database_bytes
            data_key = os.urandom(32)
            recovery_salt = os.urandom(16)
            recovery_root = crypto._derive_recovery_root(str(recovery_material), recovery_salt)
            backup_salt = os.urandom(16)
            recovery_nonce, wrapped_master = crypto._wrap_master_key(
                master_key, recovery_root, backup_salt, key_id
            )
            data_key_nonce = os.urandom(12)
            wrapped_data_key = AESGCM(master_key).encrypt(
                data_key_nonce, data_key, key_id.encode("utf-8")
            )
            payload_nonce = os.urandom(12)
            legacy_header = {
                "format_version": 2,
                "algorithm": "AES-256-GCM",
                "key_id": key_id,
                "recovery_salt": crypto._b64encode(recovery_salt),
                "scrypt_n": crypto.SCRYPT_N,
                "scrypt_r": crypto.SCRYPT_R,
                "scrypt_p": crypto.SCRYPT_P,
                "recovery_verifier": crypto._b64encode(crypto._recovery_verifier(recovery_root)),
                "backup_salt": crypto._b64encode(backup_salt),
                "recovery_nonce": crypto._b64encode(recovery_nonce),
                "wrapped_master_key": crypto._b64encode(wrapped_master),
                "data_key_nonce": crypto._b64encode(data_key_nonce),
                "wrapped_data_key": crypto._b64encode(wrapped_data_key),
                "payload_nonce": crypto._b64encode(payload_nonce),
            }
            header_bytes = json.dumps(
                legacy_header, sort_keys=True, separators=(",", ":")
            ).encode()
            ciphertext = AESGCM(data_key).encrypt(payload_nonce, plaintext, header_bytes)
            legacy_v2 = app.root / "legacy-v2.hmsbackup"
            legacy_v2.write_bytes(
                crypto.BACKUP_MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes + ciphertext
            )
            legacy_restored = app.root / "legacy-v2-restored.db"
            result = crypto.decrypt_encrypted_backup_to_path(
                str(legacy_v2), str(legacy_restored)
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(legacy_restored.read_bytes(), database_bytes)

    def test_restore_rejects_canonical_field_and_lifecycle_contradictions(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="CAN001", roll_number=1
            )
            self._validate_corruption(
                app,
                "bad-roll.db",
                lambda c: c.execute(
                    "UPDATE students SET roll_number=-50 WHERE student_id=?", (student_id,)
                ),
                "positive operational roll",
            )

            connection = app.connect()
            try:
                connection.execute(
                    """
                    INSERT INTO expenses(
                        expense_date,day_number,month_number,calendar_year,
                        session_start_year,session_year,amount,description,created_at,updated_at
                    ) VALUES('2026-07-29',29,7,2026,2026,'2026-2027',100,'Valid',
                             '2026-07-29 10:00:00','2026-07-29 10:00:00')
                    """
                )
                fee_id = connection.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,month_name,month_number,
                        year,amount,status,created_at,updated_at
                    ) VALUES(?,?, '2026-2027','Monthly','July',7,2026,100,'Unpaid',
                             '2026-07-29 10:00:00','2026-07-29 10:00:00')
                    """,
                    (student_id, class_id),
                ).lastrowid
                student = connection.execute(
                    """
                    SELECT students.tracking_id,students.admission_number,students.roll_number,
                           students.student_name,students.father_name,students.father_cnic,
                           students.student_status,classes.class_name
                    FROM students LEFT JOIN classes ON classes.id=students.class_id
                    WHERE students.student_id=?
                    """,
                    (student_id,),
                ).fetchone()
                payment_id = connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no,student_id,payment_date,total_amount,received_by,
                        created_at,payment_status,tracking_id_snapshot,
                        admission_number_snapshot,roll_number_snapshot,student_name_snapshot,
                        father_name_snapshot,father_cnic_snapshot,class_name_snapshot,
                        student_status_snapshot
                    ) VALUES('991001',?,'2026-07-29 10:00:00',100,'test',
                             '2026-07-29 10:00:00','Completed',?,?,?,?,?,?,?,?)
                    """,
                    (student_id,*student),
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id,student_fee_record_id,item_name,amount,status,created_at,
                        class_id_snapshot,class_name_snapshot,session_year_snapshot,
                        month_number_snapshot,year_snapshot
                    ) VALUES(?,?, 'July 2026',100,'Paid','2026-07-29 10:00:00',
                             ?,?,'2026-2027',7,2026)
                    """,
                    (payment_id, fee_id, class_id, student[7]),
                )
                connection.execute(
                    "UPDATE student_fee_records SET status='Paid',paid_date='2026-07-29 10:00:00' WHERE id=?",
                    (fee_id,),
                )
                connection.commit()
            finally:
                connection.close()
            app.fee_service.set_class_fee(
                class_id, 1000, 4, 2026, created_by="test", change_reason="initial"
            )

            def corrupt_expense(c):
                c.execute("DROP TRIGGER trg_expenses_canonical_update")
                c.execute("UPDATE expenses SET session_year='2030-2031'")
                c.execute(app.database.schema_integrity.INTEGRITY_TRIGGER_SQL["trg_expenses_canonical_update"])
            self._validate_corruption(
                app, "bad-expense.db", corrupt_expense, "expense"
            )
            self._validate_corruption(
                app, "bad-month.db",
                lambda c: (
                    c.execute("DROP TRIGGER IF EXISTS trg_fee_record_month_update"),
                    c.execute("UPDATE student_fee_records SET month_name='December' WHERE id=?", (fee_id,)),
                ),
                "month",
            )
            self._validate_corruption(
                app, "bad-payment-date.db",
                lambda c: c.execute("UPDATE fee_payments SET payment_date='NOT-A-DATE' WHERE id=?", (payment_id,)),
                "timestamp",
            )
            self._validate_corruption(
                app, "bad-fee-session.db",
                lambda c: (
                    c.execute("DROP TRIGGER IF EXISTS trg_fee_settings_session_period_insert"),
                    c.execute("UPDATE fee_settings SET effective_from_year=2035 WHERE class_id=?", (class_id,)),
                ),
                "session",
            )
            self._validate_corruption(
                app, "bad-admission.db",
                lambda c: c.execute("UPDATE students SET admission_year=2020 WHERE student_id=?", (student_id,)),
                "admission",
            )
            self._validate_corruption(
                app, "bad-class.db",
                lambda c: (
                    c.execute("UPDATE classes SET class_level='ELEVEN',class_name='BROKEN' WHERE id=?", (class_id,)),
                    c.execute("UPDATE fee_receipts SET class_name_snapshot='BROKEN' WHERE class_id_snapshot=?", (class_id,)),
                    c.execute("UPDATE fee_payments SET class_name_snapshot='BROKEN' WHERE student_id=?", (student_id,)),
                ),
                "class",
            )

    def test_tracking_trigger_explicit_assignment_and_normalized_identity_constraints(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            connection = app.connect()
            try:
                connection.execute("DROP TRIGGER trg_students_assign_tracking_id")
                connection.commit()
            finally:
                connection.close()
            student_id = app.student_service.add_student(
                admission_number="TRG001",
                roll_number=1,
                class_id=class_id,
                student_name="José D'Souza",
                father_name="Abdul-Rahman",
                father_cnic="1234512345671",
                father_contact_number="0300-0000000",
                school_leaving_certificate_submitted=False,
                admission_year=2026,
                admission_month=4,
                admission_day=1,
                admission_time="09:00:00",
                gender="Male",
                date_of_birth="01-01-2015",
                created_by="v18",
            )
            student = fetch_one(
                app,
                "SELECT tracking_id,student_name,father_name FROM students WHERE student_id=?",
                (student_id,),
            )
            self.assertRegex(student["tracking_id"], r"^HMS-\d{6}$")
            self.assertEqual(student["student_name"], "JOSÉ D'SOUZA")
            self.assertEqual(student["father_name"], "ABDUL-RAHMAN")

            app.database.initialize_database()
            trigger = fetch_one(
                app,
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_students_assign_tracking_id'",
            )
            self.assertIsNotNone(trigger)
            self.assertIn("HMS-", trigger["sql"])

            with self.assertRaisesRegex(ValueError, "Admission Number"):
                app.student_service.add_student(
                    admission_number=" trg001 ", roll_number=2, class_id=class_id,
                    student_name="OTHER STUDENT", father_name="OTHER FATHER",
                    father_cnic="9876598765431", father_contact_number="0300-0000001",
                    school_leaving_certificate_submitted=False,
                    admission_year=2026, admission_month=4, admission_day=1,
                    admission_time="09:00:00", gender="Male",
                    date_of_birth="01-01-2015", created_by="v18",
                )
            with self.assertRaisesRegex(ValueError, "same level and section"):
                app.class_service.add_class("1", "a")

            admission_collision = self._copy_database(app, "admission-collision.db")
            c = sqlite3.connect(admission_collision)
            c.create_function(
                "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
            )
            try:
                c.execute("DROP INDEX idx_students_admission_normalized")
                c.execute(
                    "UPDATE students SET admission_number=' trg001 ' WHERE student_id=?",
                    (student_id,),
                )
                # Add a second normalized collision while preserving all students.
                c.execute(
                    """
                    INSERT INTO students(
                        admission_number,tracking_id,roll_number,class_id,student_name,gender,
                        father_name,father_cnic,father_contact_number,date_of_birth,admission_date,
                        admission_year,admission_month,admission_day,admission_time,student_status,
                        created_at,created_by,updated_at,updated_by
                    ) VALUES('TRG001','HMS-999999',2,?,'SECOND','Male','FATHER','1234512345672',
                             '03000000000','2015-01-01','2026-04-01 09:00:00',2026,4,1,'09:00:00',
                             'Active','2026-04-01 09:00:00','test','2026-04-01 09:00:00','test')
                    """,
                    (class_id,),
                )
                c.commit()
            finally:
                c.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(admission_collision), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("admission", message.lower())

    def test_status_audit_sibling_order_and_single_graduation(self):
        with IsolatedApplication() as app:
            class_9 = insert_class(app, "9", "A")
            class_10 = insert_class(app, "10", "A")
            student_9 = insert_student(
                app, class_id=class_9, admission_number="ORD009", roll_number=1,
                name="STUDENT NINE", father_name="ORDER FATHER",
                father_cnic="1234512345671"
            )
            student_10 = insert_student(
                app, class_id=class_10, admission_number="ORD010", roll_number=1,
                name="STUDENT TEN", father_name="ORDER FATHER",
                father_cnic="1234512345671"
            )
            now = datetime.now()
            connection = app.connect()
            try:
                for student_id in (student_9, student_10):
                    connection.execute(
                        """
                        INSERT INTO sibling_fee_assignments(
                            student_id,monthly_fee_percentage,assignment_role,
                            is_sibling_policy,effective_from_month,effective_from_year,
                            created_at
                        ) VALUES(?,50,'DISCOUNTED',1,?,?,?)
                        """,
                        (student_id, now.month, now.year, now.strftime("%Y-%m-%d %H:%M:%S")),
                    )
                connection.commit()
            finally:
                connection.close()
            import importlib
            sibling_fee_service = importlib.import_module("app.services.sibling_fee_service")
            sibling_fee_service.get_connection = lambda: app.database.get_connection(automatic_backup=False)
            assignments = sibling_fee_service.list_current_sibling_assignments()
            self.assertEqual(
                [row["class_name"] for row in assignments[:2]], ["10A", "9A"]
            )

            row = fetch_one(app, "SELECT * FROM students WHERE student_id=?", (student_9,))
            app.student_service.update_student(
                student_id=student_9, roll_number=int(row["roll_number"]),
                class_id=int(row["class_id"]), student_name=row["student_name"],
                father_name=row["father_name"], father_cnic=row["father_cnic"],
                father_contact_number=row["father_contact_number"],
                student_status="Dropped",
                school_leaving_certificate_submitted=bool(row["school_leaving_certificate_submitted"]),
                admission_year=int(row["admission_year"]), admission_month=int(row["admission_month"]),
                admission_day=int(row["admission_day"]), admission_time=row["admission_time"],
                updated_by="Auditor", gender=row["gender"],
                whatsapp_number=row["whatsapp_number"] or "",
                current_address=row["current_address"] or "",
                permanent_address=row["permanent_address"] or "",
                date_of_birth=row["date_of_birth"],
                form_b_submitted=bool(row["form_b_submitted"]),
                birth_certificate_submitted=bool(row["birth_certificate_submitted"]),
            )
            status_event = fetch_one(
                app,
                "SELECT old_status,new_status,event_type,related_class_id,changed_by "
                "FROM student_status_history WHERE student_id=? ORDER BY id DESC LIMIT 1",
                (student_9,),
            )
            self.assertEqual(
                (status_event["old_status"], status_event["new_status"], status_event["event_type"]),
                ("Active", "Dropped", "DROP"),
            )
            self.assertEqual(int(status_event["related_class_id"]), class_9)
            self.assertEqual(status_event["changed_by"], "Auditor")
            fake_manual = fetch_one(
                app,
                "SELECT COUNT(*) AS n FROM student_class_history WHERE student_id=? "
                "AND old_class_id IS NULL AND change_type='MANUAL'",
                (student_9,),
            )
            self.assertEqual(int(fake_manual["n"]), 0)

            graduation_damage = self._copy_database(app, "graduation-conflict.db")
            c = sqlite3.connect(graduation_damage)
            try:
                c.create_function("HMS_TEST_FIXTURE_MODE", 0, lambda: 1)
                c.execute("DROP INDEX idx_one_active_graduation_per_student")
                for status in ("Graduated", "Partially Graduated"):
                    c.execute(
                        """
                        INSERT INTO graduation_records(
                            student_id,admission_number,last_class_id,last_class_name,
                            last_roll_number,graduation_date,status,is_reversed
                        ) VALUES(?, 'ORD010', ?, '10A', 1,
                                 '2027-03-15 10:00:00', ?, 0)
                        """,
                        (student_10, class_10, status),
                    )
                c.commit()
            finally:
                c.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(graduation_damage), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("graduation", message.lower())

    def test_fee_policy_overlap_unicode_promotion_and_expense_guards(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            app.fee_service.set_class_fee(
                class_id, 1000, 4, 2026, created_by="Alice", change_reason="Initial"
            )
            app.fee_service.set_class_fee(
                class_id, 1200, 9, 2026, created_by="Bob", change_reason="Revision"
            )
            overlap = self._copy_database(app, "class-fee-overlap.db")
            c = sqlite3.connect(overlap)
            try:
                c.execute(
                    """
                    INSERT INTO fee_history(
                        class_id,monthly_fee,session_start_year,effective_from_month,
                        effective_from_year,effective_to_month,effective_to_year,created_at,created_by
                    ) VALUES(?,1300,2026,8,2026,10,2026,'2026-08-01 10:00:00','Conflict')
                    """,
                    (class_id,),
                )
                c.commit()
            finally:
                c.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(overlap), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("overlap", message.lower())

            student_id = insert_student(
                app, class_id=class_id, admission_number="OVR001", roll_number=1
            )
            app.fee_service.set_student_fee_override(
                student_id, 50, 7, 2026, created_by="Alice", change_reason="Support"
            )
            override_overlap = self._copy_database(app, "override-overlap.db")
            c = sqlite3.connect(override_overlap)
            try:
                c.execute(
                    """
                    INSERT INTO student_fee_override(
                        student_id,monthly_fee_percentage,effective_from_month,effective_from_year,
                        effective_to_month,effective_to_year,is_active,created_at,updated_at,created_by,change_reason
                    ) VALUES(?,70,8,2026,12,2026,1,'2026-08-01 10:00:00',
                             '2026-08-01 10:00:00','Conflict','Overlap')
                    """,
                    (student_id,),
                )
                c.commit()
            finally:
                c.close()
            valid, message = app.backup_service.validate_school_database_schema(
                str(override_overlap), require_current_schema=True
            )
            self.assertFalse(valid)
            self.assertIn("individual", message.lower())

            for valid_name in ("D'SOUZA", "ABDUL-RAHMAN", "JOSÉ", "O’CONNOR"):
                self.assertTrue(app.student_service.validate_person_name(valid_name))
                self.assertEqual(
                    app.student_service.validate_person_name_length(valid_name), valid_name.upper()
                )
            for invalid_name in ("ALI2", "-ALI", "ALI--KHAN", "ALI🙂"):
                self.assertFalse(app.student_service.validate_person_name(invalid_name))

            boundary = app.promotion_service._subtract_calendar_months(
                datetime(2026, 7, 29, 10, 0, 0), 8
            )
            self.assertEqual(boundary, datetime(2025, 11, 29, 10, 0, 0))
            malformed_row = {"batch_id": 77, "promotion_datetime": "NOT-A-DATE"}
            with self.assertRaisesRegex(ValueError, "batch 77"):
                app.promotion_service._parse_batch_datetime(malformed_row)
            warning = app.promotion_service.consume_promotion_integrity_warning()
            self.assertIn("batch 77", warning)

            self.assertTrue(
                app.expense_service.validate_expense_description("  Office   supplies  ")
            )
            self.assertEqual(
                app.expense_service.normalize_expense_description("  Office   supplies  "),
                "Office supplies",
            )
            with self.assertRaisesRegex(ValueError, "1,000"):
                app.expense_service.add_expense("2020-01-01", 1, "X" * 1001)
            ui_source = (
                Path(__file__).resolve().parents[2] / "app/modules/expense_management.py"
            ).read_text(encoding="utf-8")
            self.assertIn("MAX_EXPENSE_DESCRIPTION_LENGTH", ui_source)
            self.assertIn("validate_expense_description", ui_source)

            # Bug 86 remains withdrawn: both genders and Class 4 routing remain present.
            self.assertTrue(app.student_service.validate_gender("Male"))
            self.assertTrue(app.student_service.validate_gender("Female"))
            self.assertIn("A", app.student_service.MALE_CLASS_SECTIONS)
            self.assertIn("F", app.student_service.MALE_CLASS_SECTIONS)
            self.assertIn("G", app.student_service.FEMALE_CLASS_SECTIONS)
            self.assertIn("Z", app.student_service.FEMALE_CLASS_SECTIONS)

    def test_cent_exact_payment_total_matches_quantized_receipt_items(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app, class_id=class_id, admission_number="CENT001", roll_number=1
            )
            first = self._insert_fee_row(
                app, student_id=student_id, class_id=class_id,
                month=7, year=2026, amount=0.005
            )
            second = self._insert_fee_row(
                app, student_id=student_id, class_id=class_id,
                month=8, year=2026, amount=0.005
            )
            with mock.patch.object(app.fee_service, "datetime", FixedJulyDateTime):
                result = app.fee_service.process_payment(
                    student_id, [first, second], received_by="Cent Auditor"
                )
            payment = fetch_one(
                app, "SELECT total_amount FROM fee_payments WHERE id=?", (result["payment_id"],)
            )
            items = fetch_all(
                app, "SELECT amount FROM fee_receipts WHERE payment_id=? ORDER BY id",
                (result["payment_id"],),
            )
            self.assertEqual([Decimal(str(row["amount"])) for row in items], [Decimal("0.01"), Decimal("0.01")])
            self.assertEqual(Decimal(str(payment["total_amount"])), Decimal("0.02"))
            self.assertEqual(
                Decimal(str(payment["total_amount"])),
                sum((Decimal(str(row["amount"])) for row in items), Decimal("0.00")),
            )


if __name__ == "__main__":
    unittest.main()
