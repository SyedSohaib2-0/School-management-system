from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

from tests.regression.support import (
    IsolatedApplication,
    insert_class,
    insert_student,
)


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def insert_monthly_fee(
    app: IsolatedApplication,
    *,
    student_id: int,
    class_id: int,
    month_number: int,
    year: int = 2026,
    amount: float = 1000.0,
) -> int:
    session_start = year if month_number >= 4 else year - 1
    connection = app.connect()
    try:
        cursor = connection.execute(
            """
            INSERT INTO student_fee_records(
                student_id,class_id,session_year,fee_type,month_name,
                month_number,year,amount,status,paid_date,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,'Unpaid',NULL,
                     '2026-07-01 09:00:00','2026-07-01 09:00:00')
            """,
            (
                int(student_id), int(class_id),
                f"{session_start}-{session_start + 1}", "Monthly",
                MONTH_NAMES[int(month_number)], int(month_number), int(year),
                float(amount),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)
    finally:
        connection.close()


def make_paid_fee(
    app: IsolatedApplication,
    *,
    admission_number: str = "H9101",
    month_number: int = 7,
    roll_number: int = 1,
):
    class_id = insert_class(app, "1", "A")
    student_id = insert_student(
        app,
        class_id=class_id,
        admission_number=admission_number,
        roll_number=roll_number,
    )
    fee_id = insert_monthly_fee(
        app,
        student_id=student_id,
        class_id=class_id,
        month_number=month_number,
    )
    payment = app.fee_service.process_payment(student_id, [fee_id])
    return class_id, student_id, fee_id, payment


def configure_external_backup(app: IsolatedApplication):
    setup = app.backup_crypto_service.configure_backup_security(
        "FurtherTechnical#2028",
        "FurtherTechnical#2028",
    )
    if not setup.get("success"):
        raise AssertionError(setup)
    external = app.root / "approved-external"
    external.mkdir(parents=True, exist_ok=True)
    settings_service = importlib.import_module(
        "app.services.backup_settings_service"
    )
    settings = settings_service.load_backup_settings()
    settings["selected_folder_enabled"] = True
    settings["selected_folder_path"] = str(external)
    settings_service.save_backup_settings(settings)
    return external


class FurtherTechnicalIntegrityRestoreBackupSameV31Tests(unittest.TestCase):
    def test_paid_receipt_cannot_point_to_unpaid_fee_at_startup(self):
        with IsolatedApplication() as app:
            _class_id, _student_id, fee_id, payment = make_paid_fee(app)
            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE student_fee_records SET status='Unpaid',paid_date=NULL WHERE id=?",
                    (fee_id,),
                )
                connection.commit()
                schema = importlib.import_module("app.schema_integrity")
                valid, message = schema.validate_payment_receipt_integrity(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("Active receipt items", message)
            with self.assertRaisesRegex(RuntimeError, "Active receipt items"):
                app.database.initialize_database()

    def test_duplicate_active_completed_payment_evidence_is_rejected(self):
        with IsolatedApplication() as app:
            _class_id, _student_id, fee_id, payment = make_paid_fee(app)
            first_payment_id = int(payment["payment_id"])
            connection = app.connect()
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no,student_id,payment_date,total_amount,received_by,
                        created_at,payment_status,cancelled_at,cancelled_by,
                        cancellation_reason,student_status_before,student_status_after,
                        auto_graduated,auto_archived,tracking_id_snapshot,
                        admission_number_snapshot,roll_number_snapshot,
                        student_name_snapshot,father_name_snapshot,
                        father_cnic_snapshot,class_name_snapshot,
                        student_status_snapshot
                    )
                    SELECT '999991',student_id,payment_date,total_amount,received_by,
                           created_at,'Completed',NULL,NULL,NULL,
                           student_status_before,student_status_after,
                           auto_graduated,auto_archived,tracking_id_snapshot,
                           admission_number_snapshot,roll_number_snapshot,
                           student_name_snapshot,father_name_snapshot,
                           father_cnic_snapshot,class_name_snapshot,
                           student_status_snapshot
                    FROM fee_payments WHERE id=?
                    """,
                    (first_payment_id,),
                )
                second_payment_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id,student_fee_record_id,item_name,amount,status,
                        created_at,class_id_snapshot,class_name_snapshot,
                        session_year_snapshot,month_number_snapshot,year_snapshot
                    )
                    SELECT ?,student_fee_record_id,item_name,amount,'Paid',created_at,
                           class_id_snapshot,class_name_snapshot,
                           session_year_snapshot,month_number_snapshot,year_snapshot
                    FROM fee_receipts
                    WHERE payment_id=? AND student_fee_record_id=?
                    """,
                    (second_payment_id, first_payment_id, fee_id),
                )
                connection.commit()
                schema = importlib.import_module("app.schema_integrity")
                valid, message = schema.validate_payment_receipt_integrity(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("more than one active", message)

    def test_partial_item_cancellation_under_completed_payment_is_rejected(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "1", "A")
            student_id = insert_student(
                app,
                class_id=class_id,
                admission_number="H9102",
                roll_number=1,
            )
            july = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, month_number=7,
                amount=1000.0,
            )
            august = insert_monthly_fee(
                app, student_id=student_id, class_id=class_id, month_number=8,
                amount=1200.0,
            )
            payment = app.fee_service.process_payment(student_id, [july, august])
            payment_id = int(payment["payment_id"])

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE fee_receipts SET status='Cancelled' "
                    "WHERE payment_id=? AND student_fee_record_id=?",
                    (payment_id, august),
                )
                connection.execute(
                    "UPDATE student_fee_records SET status='Unpaid',paid_date=NULL "
                    "WHERE id=?",
                    (august,),
                )
                connection.execute(
                    "UPDATE fee_payments SET total_amount=1000 WHERE id=?",
                    (payment_id,),
                )
                connection.commit()
                schema = importlib.import_module("app.schema_integrity")
                valid, message = schema.validate_payment_receipt_integrity(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("Payment status and receipt-item statuses", message)

    def test_cancelled_payment_must_preserve_cancelled_receipt_items(self):
        with IsolatedApplication() as app:
            _class_id, _student_id, _fee_id, payment = make_paid_fee(app)
            payment_id = int(payment["payment_id"])
            app.fee_service.cancel_immediate_payment(payment_id)
            connection = app.connect()
            try:
                connection.execute(
                    "DELETE FROM fee_receipts WHERE payment_id=?", (payment_id,)
                )
                connection.commit()
                schema = importlib.import_module("app.schema_integrity")
                valid, message = schema.validate_payment_receipt_integrity(connection)
            finally:
                connection.close()
            self.assertFalse(valid)
            self.assertIn("no preserved receipt items", message)

    def test_final_payment_trigger_blocks_duplicate_active_evidence(self):
        with IsolatedApplication() as app:
            _class_id, _student_id, fee_id, payment = make_paid_fee(app)
            first_payment_id = int(payment["payment_id"])
            connection = app.strict_connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT INTO fee_payments(
                        receipt_no,student_id,payment_date,total_amount,received_by,
                        created_at,payment_status,cancelled_at,cancelled_by,
                        cancellation_reason,student_status_before,student_status_after,
                        auto_graduated,auto_archived,tracking_id_snapshot,
                        admission_number_snapshot,roll_number_snapshot,
                        student_name_snapshot,father_name_snapshot,
                        father_cnic_snapshot,class_name_snapshot,
                        student_status_snapshot
                    )
                    SELECT '999992',student_id,payment_date,total_amount,
                           '__HMS_PAYMENT_PENDING__',created_at,'Completed',
                           NULL,NULL,NULL,student_status_before,student_status_after,
                           auto_graduated,auto_archived,tracking_id_snapshot,
                           admission_number_snapshot,roll_number_snapshot,
                           student_name_snapshot,father_name_snapshot,
                           father_cnic_snapshot,class_name_snapshot,
                           student_status_snapshot
                    FROM fee_payments WHERE id=?
                    """,
                    (first_payment_id,),
                )
                second_payment_id = int(cursor.lastrowid)
                connection.execute(
                    """
                    INSERT INTO fee_receipts(
                        payment_id,student_fee_record_id,item_name,amount,status,
                        created_at,class_id_snapshot,class_name_snapshot,
                        session_year_snapshot,month_number_snapshot,year_snapshot
                    )
                    SELECT ?,student_fee_record_id,item_name,amount,'Paid',created_at,
                           class_id_snapshot,class_name_snapshot,
                           session_year_snapshot,month_number_snapshot,year_snapshot
                    FROM fee_receipts
                    WHERE payment_id=? AND student_fee_record_id=?
                    """,
                    (second_payment_id, first_payment_id, fee_id),
                )
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "one consistent set",
                ):
                    connection.execute(
                        "UPDATE fee_payments SET received_by='admin' WHERE id=?",
                        (second_payment_id,),
                    )
            finally:
                connection.rollback()
                connection.close()

    def test_same_schema_restore_copy_repairs_code_owned_triggers(self):
        with IsolatedApplication() as app:
            candidate = app.root / "same-schema-old-trigger.db"
            shutil.copy2(app.database_path, candidate)
            connection = sqlite3.connect(candidate)
            try:
                connection.execute(
                    'DROP TRIGGER IF EXISTS "trg_completed_payment_evidence_update"'
                )
                connection.commit()
            finally:
                connection.close()

            valid, message = app.backup_service.migrate_and_validate_school_database_copy(
                str(candidate)
            )
            self.assertTrue(valid, message)
            connection = sqlite3.connect(candidate)
            try:
                trigger = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                    ("trg_completed_payment_evidence_update",),
                ).fetchone()
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            finally:
                connection.close()
            self.assertIsNotNone(trigger)
            self.assertIn("one consistent set", str(trigger[0]))
            self.assertEqual(version, 23)

    def test_staged_restore_survives_original_source_disappearing(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "2", "A")
            external = configure_external_backup(app)
            backup = app.backup_service.create_backup_in_destination(
                str(external),
                app.backup_service.SELECTED_FOLDER_BACKUP_PREFIX,
                10,
                "Removable source regression",
            )
            self.assertTrue(backup["success"], backup)
            backup_path = Path(backup["backup_path"])

            connection = app.connect()
            try:
                connection.execute(
                    "UPDATE classes SET student_capacity=33 WHERE id=?", (class_id,)
                )
                connection.commit()
            finally:
                connection.close()

            staged = app.backup_service.stage_database_restore(str(backup_path))
            self.assertTrue(staged["success"], staged)
            backup_path.unlink()
            processed = app.backup_service.process_pending_restore_before_startup()
            self.assertTrue(processed["success"], processed)
            self.assertTrue(processed["restore_processed"], processed)

            connection = app.connect()
            try:
                capacity = int(connection.execute(
                    "SELECT student_capacity FROM classes WHERE id=?", (class_id,)
                ).fetchone()[0])
            finally:
                connection.close()
            self.assertEqual(capacity, 40)

    def test_unknown_pending_restore_format_is_quarantined(self):
        with IsolatedApplication() as app:
            external = configure_external_backup(app)
            backup = app.backup_service.create_backup_in_destination(
                str(external),
                app.backup_service.SELECTED_FOLDER_BACKUP_PREFIX,
                10,
                "Format regression",
            )
            self.assertTrue(backup["success"], backup)
            staged = app.backup_service.stage_database_restore(backup["backup_path"])
            self.assertTrue(staged["success"], staged)

            request_path = Path(app.backup_service.PENDING_RESTORE_REQUEST_PATH)
            request = json.loads(request_path.read_text(encoding="utf-8"))
            request["backup_format"] = "zip"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            before = app.database_path.read_bytes()

            processed = app.backup_service.process_pending_restore_before_startup()
            self.assertTrue(processed["success"], processed)
            self.assertTrue(processed["restore_quarantined"], processed)
            self.assertEqual(app.database_path.read_bytes(), before)
            self.assertFalse(request_path.exists())

    def test_clock_rollback_protected_backup_counts_inside_retention_limit(self):
        with IsolatedApplication() as app:
            destination = app.root / "retention"
            destination.mkdir()
            names = [
                "clock_2026-08-03_12-00-00-000001.hmsbackup",
                "clock_2026-08-03_11-00-00-000001.hmsbackup",
                "clock_2026-08-03_10-00-00-000001.hmsbackup",
                # Newly created while the clock is wrong; it sorts oldest.
                "clock_2025-01-01_01-00-00-000001.hmsbackup",
            ]
            for name in names:
                (destination / name).write_bytes(name.encode("ascii"))
            protected = destination / names[-1]

            result = app.backup_service.apply_backup_retention(
                str(destination), "clock", 2, protected_backup_path=str(protected)
            )
            self.assertTrue(result["success"], result)
            remaining = sorted(path.name for path in destination.iterdir())
            self.assertEqual(
                remaining,
                sorted([names[0], names[-1]]),
            )
            self.assertEqual(result["retained_count"], 2)

    def test_retention_only_partial_success_does_not_schedule_duplicate_backup(self):
        with IsolatedApplication() as app:
            scheduler = importlib.import_module("app.services.backup_scheduler")
            with scheduler._scheduler_lock:
                if scheduler._backup_timer is not None:
                    scheduler._backup_timer.cancel()
                scheduler._backup_timer = None
                scheduler._backup_running = False
                scheduler._backup_pending = True
                scheduler._pending_destinations = {"Internal automatic backup"}
                scheduler._retry_attempt = 0
                scheduler._scheduler_stopping = False
                scheduler._change_generation = 50

            partial = {
                "success": False,
                "results": [
                    {
                        "destination": "Internal automatic backup",
                        "success": False,
                        "skipped": False,
                        "status": "partial_success",
                        "backup_created": True,
                        "retention_success": False,
                        "message": "Backup created; old locked file could not be deleted.",
                    }
                ],
            }
            with mock.patch.object(
                app.backup_service,
                "run_automatic_backups",
                return_value=partial,
            ):
                scheduler._execute_scheduled_backup()

            status = scheduler.get_backup_scheduler_status()
            self.assertFalse(status["backup_pending"], status)
            self.assertIsNone(status["pending_destinations"])
            self.assertEqual(status["retry_attempt"], 0)
            self.assertFalse(status["timer_active"])

    def test_restore_checkpoints_sidecars_created_after_pre_restore_backup(self):
        with IsolatedApplication() as app:
            class_id = insert_class(app, "3", "A")
            external = configure_external_backup(app)
            backup = app.backup_service.create_backup_in_destination(
                str(external),
                app.backup_service.SELECTED_FOLDER_BACKUP_PREFIX,
                10,
                "WAL sidecar regression",
            )
            self.assertTrue(backup["success"], backup)
            staged = app.backup_service.stage_database_restore(backup["backup_path"])
            self.assertTrue(staged["success"], staged)

            real_pre_restore = app.backup_service.create_pre_restore_backup

            def backup_then_leave_crash_wal():
                result = real_pre_restore()
                if not result.get("success"):
                    return result
                script = (
                    "import os,sqlite3; "
                    f"c=sqlite3.connect({str(app.database_path)!r}); "
                    "c.execute('PRAGMA journal_mode=WAL'); "
                    "c.execute('PRAGMA wal_autocheckpoint=0'); "
                    f"c.execute('UPDATE classes SET student_capacity=31 WHERE id={class_id}'); "
                    "c.commit(); os._exit(0)"
                )
                completed = subprocess.run([sys.executable, "-c", script])
                self.assertEqual(completed.returncode, 0)
                self.assertTrue(Path(str(app.database_path) + "-wal").exists())
                return result

            with mock.patch.object(
                app.backup_service,
                "create_pre_restore_backup",
                side_effect=backup_then_leave_crash_wal,
            ):
                processed = app.backup_service.process_pending_restore_before_startup()
            self.assertTrue(processed["success"], processed)
            self.assertFalse(Path(str(app.database_path) + "-wal").exists())
            self.assertFalse(Path(str(app.database_path) + "-shm").exists())
            connection = app.connect()
            try:
                capacity = int(connection.execute(
                    "SELECT student_capacity FROM classes WHERE id=?", (class_id,)
                ).fetchone()[0])
            finally:
                connection.close()
            self.assertEqual(capacity, 40)


if __name__ == "__main__":
    unittest.main()
