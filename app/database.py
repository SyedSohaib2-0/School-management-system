"""
Shared database module.
Every module in the application must use get_connection() from this file
instead of opening its own SQLite connection. This keeps everything in
one database file (data\\school.db) as required by the project rules.
"""

import os
import re
import sqlite3
from datetime import datetime

from app.config import (
    DATABASE_PATH,
    BACKUPS_DIR,
)
from app.normalization import (
    canonicalize_tracking_id,
    normalize_person_name,
)
from app import schema_integrity


# Increment this number whenever a future application update changes
# the database schema through a migration.
# FEE_TRACKING_ID_SCHEMA_V7
CURRENT_SCHEMA_VERSION = 23


_AUTOMATIC_BACKUP_IN_PROGRESS = False

def get_database_schema_version(
    connection: sqlite3.Connection,
) -> int:
    """Return the schema version stored inside the SQLite database."""
    row = connection.execute(
        "PRAGMA user_version;"
    ).fetchone()

    if not row:
        return 0

    return int(
        row[0]
    )

def set_database_schema_version(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    """Store the successfully installed schema version."""
    safe_version = int(
        version
    )

    if safe_version < 0:
        raise ValueError(
            "Database schema version cannot be negative."
        )

    connection.execute(
        f"PRAGMA user_version = {safe_version};"
    )




def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """Return the column names for one SQLite table."""
    safe_table_name = str(table_name).replace('"', '""')
    return {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{safe_table_name}");'
        ).fetchall()
    }


def _ensure_table_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """Add a migration column only when it is not already present."""
    if column_name in _table_columns(connection, table_name):
        return
    connection.execute(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
    )


def _release_completed_student_rolls(
    connection: sqlite3.Connection,
) -> None:
    """Release positive rolls held by completed Class-10 students."""
    _ensure_table_column(
        connection,
        "students",
        "released_roll_number",
        "INTEGER",
    )
    connection.execute(
        """
        UPDATE students
        SET released_roll_number = CASE
                WHEN released_roll_number IS NULL
                     AND roll_number > 0
                THEN roll_number
                ELSE released_roll_number
            END,
            roll_number = -ABS(student_id)
        WHERE student_status IN (
                'Graduated',
                'Partially Graduated'
            )
          AND (
                roll_number > 0
                OR released_roll_number IS NULL
              )
        """
    )


def _raise_for_duplicate_class_rolls(
    connection: sqlite3.Connection,
) -> None:
    duplicate_roll_numbers = connection.execute(
        "SELECT class_id, roll_number, COUNT(*) AS duplicate_count "
        "FROM students "
        "GROUP BY class_id, roll_number "
        "HAVING COUNT(*) > 1 "
        "ORDER BY class_id, roll_number"
    ).fetchall()

    if not duplicate_roll_numbers:
        return

    duplicate_summary = "; ".join(
        (
            f"class_id={int(row[0])}, "
            f"roll_number={int(row[1])}, "
            f"records={int(row[2])}"
        )
        for row in duplicate_roll_numbers[:20]
    )
    raise RuntimeError(
        "Database migration cannot create the roll-number uniqueness "
        "index because genuine duplicate roll numbers remain in the "
        "same class after completed rolls were released. Resolve these "
        f"records first. Duplicates: {duplicate_summary}"
    )


def _install_receipt_snapshot_columns(
    connection: sqlite3.Connection,
) -> None:
    """Add and backfill immutable payment-time identity/class snapshots."""
    payment_columns = {
        "tracking_id_snapshot": "TEXT",
        "admission_number_snapshot": "TEXT",
        "roll_number_snapshot": "INTEGER",
        "student_name_snapshot": "TEXT",
        "father_name_snapshot": "TEXT",
        "father_cnic_snapshot": "TEXT",
        "class_name_snapshot": "TEXT",
        "student_status_snapshot": "TEXT",
    }
    for column_name, definition in payment_columns.items():
        _ensure_table_column(
            connection,
            "fee_payments",
            column_name,
            definition,
        )

    receipt_columns = {
        "class_id_snapshot": "INTEGER",
        "class_name_snapshot": "TEXT",
        "session_year_snapshot": "TEXT",
        "month_number_snapshot": "INTEGER",
        "year_snapshot": "INTEGER",
    }
    for column_name, definition in receipt_columns.items():
        _ensure_table_column(
            connection,
            "fee_receipts",
            column_name,
            definition,
        )

    # Old receipts can only freeze the best data that is still available at
    # migration time. Information edited before this schema existed cannot be
    # reconstructed retrospectively.
    connection.execute(
        """
        UPDATE fee_receipts
        SET class_id_snapshot = COALESCE(
                class_id_snapshot,
                (SELECT records.class_id
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id)
            ),
            class_name_snapshot = COALESCE(
                NULLIF(TRIM(class_name_snapshot), ''),
                (SELECT classes.class_name
                 FROM student_fee_records AS records
                 LEFT JOIN classes ON classes.id = records.class_id
                 WHERE records.id = fee_receipts.student_fee_record_id),
                '-'
            ),
            session_year_snapshot = COALESCE(
                NULLIF(TRIM(session_year_snapshot), ''),
                (SELECT NULLIF(TRIM(records.session_year), '')
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id),
                '-'
            ),
            month_number_snapshot = COALESCE(
                month_number_snapshot,
                (SELECT records.month_number
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id)
            ),
            year_snapshot = COALESCE(
                year_snapshot,
                (SELECT records.year
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id)
            )
        """
    )

    connection.execute(
        """
        UPDATE fee_payments
        SET tracking_id_snapshot = COALESCE(
                NULLIF(TRIM(tracking_id_snapshot), ''),
                (SELECT NULLIF(TRIM(students.tracking_id), '')
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            ),
            admission_number_snapshot = COALESCE(
                NULLIF(TRIM(admission_number_snapshot), ''),
                (SELECT NULLIF(TRIM(students.admission_number), '')
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            ),
            roll_number_snapshot = COALESCE(
                roll_number_snapshot,
                (SELECT students.roll_number
                 FROM students
                 WHERE students.student_id = fee_payments.student_id)
            ),
            student_name_snapshot = COALESCE(
                NULLIF(TRIM(student_name_snapshot), ''),
                (SELECT NULLIF(TRIM(students.student_name), '')
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            ),
            father_name_snapshot = COALESCE(
                NULLIF(TRIM(father_name_snapshot), ''),
                (SELECT NULLIF(TRIM(students.father_name), '')
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            ),
            father_cnic_snapshot = COALESCE(
                NULLIF(TRIM(father_cnic_snapshot), ''),
                (SELECT NULLIF(TRIM(students.father_cnic), '')
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            ),
            student_status_snapshot = COALESCE(
                NULLIF(TRIM(student_status_snapshot), ''),
                student_status_before,
                (SELECT students.student_status
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            ),
            class_name_snapshot = COALESCE(
                NULLIF(TRIM(class_name_snapshot), ''),
                CASE
                    WHEN (
                        SELECT COUNT(DISTINCT COALESCE(
                            NULLIF(TRIM(receipts.class_name_snapshot), ''),
                            '-'
                        ))
                        FROM fee_receipts AS receipts
                        WHERE receipts.payment_id = fee_payments.id
                          AND receipts.status = 'Paid'
                    ) > 1
                    THEN 'MULTIPLE CLASSES'
                    ELSE (
                        SELECT COALESCE(
                            NULLIF(TRIM(receipts.class_name_snapshot), ''),
                            '-'
                        )
                        FROM fee_receipts AS receipts
                        WHERE receipts.payment_id = fee_payments.id
                          AND receipts.status = 'Paid'
                        ORDER BY receipts.id ASC
                        LIMIT 1
                    )
                END,
                (SELECT classes.class_name
                 FROM students
                 LEFT JOIN classes ON classes.id = students.class_id
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            )
        """
    )


def _refresh_placeholder_receipt_snapshots_after_canonical_repairs(
    connection: sqlite3.Connection,
) -> None:
    """Complete only migration placeholders after canonical source rows exist.

    V14 introduced receipt/payment snapshots before later migrations had
    canonicalized Tracking IDs and Monthly Fee session labels. A placeholder
    created at V14 must be refreshed once those authoritative source values are
    repaired; already populated historical snapshots are never overwritten.
    """
    connection.execute(
        """
        UPDATE fee_payments
        SET tracking_id_snapshot = COALESCE(
                (SELECT NULLIF(TRIM(students.tracking_id), '')
                 FROM students
                 WHERE students.student_id = fee_payments.student_id),
                '-'
            )
        WHERE tracking_id_snapshot IS NULL
           OR TRIM(tracking_id_snapshot) IN ('', '-')
        """
    )

    connection.execute(
        """
        UPDATE fee_receipts
        SET class_id_snapshot = COALESCE(
                class_id_snapshot,
                (SELECT records.class_id
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id)
            ),
            class_name_snapshot = CASE
                WHEN class_name_snapshot IS NULL
                  OR TRIM(class_name_snapshot) IN ('', '-')
                THEN COALESCE(
                    (SELECT NULLIF(TRIM(classes.class_name), '')
                     FROM student_fee_records AS records
                     LEFT JOIN classes ON classes.id = records.class_id
                     WHERE records.id = fee_receipts.student_fee_record_id),
                    '-'
                )
                ELSE class_name_snapshot
            END,
            session_year_snapshot = CASE
                WHEN session_year_snapshot IS NULL
                  OR TRIM(session_year_snapshot) IN ('', '-')
                THEN COALESCE(
                    (SELECT NULLIF(TRIM(records.session_year), '')
                     FROM student_fee_records AS records
                     WHERE records.id = fee_receipts.student_fee_record_id),
                    '-'
                )
                ELSE session_year_snapshot
            END,
            month_number_snapshot = COALESCE(
                month_number_snapshot,
                (SELECT records.month_number
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id)
            ),
            year_snapshot = COALESCE(
                year_snapshot,
                (SELECT records.year
                 FROM student_fee_records AS records
                 WHERE records.id = fee_receipts.student_fee_record_id)
            )
        WHERE class_id_snapshot IS NULL
           OR class_name_snapshot IS NULL
           OR TRIM(class_name_snapshot) IN ('', '-')
           OR session_year_snapshot IS NULL
           OR TRIM(session_year_snapshot) IN ('', '-')
           OR month_number_snapshot IS NULL
           OR year_snapshot IS NULL
        """
    )


def _repair_duplicate_monthly_fee_records(
    connection: sqlite3.Connection,
) -> int:
    """Delete only exact redundant fee copies; preserve every conflict."""
    duplicate_groups = connection.execute(
        """
        SELECT student_id, month_number, year, COUNT(*) AS duplicate_count
        FROM student_fee_records
        WHERE UPPER(TRIM(fee_type)) = 'MONTHLY'
          AND month_number IS NOT NULL
          AND year IS NOT NULL
        GROUP BY student_id, month_number, year
        HAVING COUNT(*) > 1
        ORDER BY student_id, year, month_number
        """
    ).fetchall()

    safe_duplicate_ids = []
    conflict_reports = []
    for student_id, month_number, calendar_year, _count in duplicate_groups:
        fee_rows = connection.execute(
            """
            SELECT
                fees.id,
                fees.student_id,
                fees.class_id,
                COALESCE(classes.class_name, '-') AS class_name,
                COALESCE(students.admission_number, '-') AS admission_number,
                fees.session_year,
                fees.fee_type,
                fees.month_name,
                fees.month_number,
                fees.year,
                fees.amount,
                fees.status,
                fees.paid_date
            FROM student_fee_records AS fees
            LEFT JOIN classes ON classes.id = fees.class_id
            LEFT JOIN students ON students.student_id = fees.student_id
            WHERE fees.student_id = ?
              AND UPPER(TRIM(fees.fee_type)) = 'MONTHLY'
              AND fees.month_number = ?
              AND fees.year = ?
            ORDER BY fees.id ASC
            """,
            (int(student_id), int(month_number), int(calendar_year)),
        ).fetchall()
        fee_ids = [int(row[0]) for row in fee_rows]
        placeholders = ",".join("?" for _ in fee_ids)
        receipt_rows = connection.execute(
            f"""
            SELECT receipts.student_fee_record_id,
                   payments.receipt_no,
                   payments.id,
                   payments.payment_status,
                   receipts.status
            FROM fee_receipts AS receipts
            LEFT JOIN fee_payments AS payments
              ON payments.id = receipts.payment_id
            WHERE receipts.student_fee_record_id IN ({placeholders})
            ORDER BY receipts.student_fee_record_id, receipts.id
            """,
            fee_ids,
        ).fetchall()

        statuses = {str(row[11] or '').strip().lower() for row in fee_rows}
        material_signatures = {
            (
                int(row[1]),
                int(row[2]),
                str(row[5] or '').strip(),
                str(row[6] or '').strip().upper(),
                str(row[7] or '').strip(),
                int(row[8]),
                int(row[9]),
                round(float(row[10] or 0.0), 2),
                str(row[11] or '').strip().lower(),
                str(row[12] or '').strip(),
            )
            for row in fee_rows
        }
        safe_statuses = statuses.issubset({'unpaid', 'waived'})
        is_exact_redundancy = (
            safe_statuses
            and not receipt_rows
            and len(material_signatures) == 1
        )
        if is_exact_redundancy:
            safe_duplicate_ids.extend(fee_ids[1:])
            continue

        receipt_text = ", ".join(
            (
                f"fee_id={int(row[0])}, receipt={row[1] or '-'}, "
                f"payment_id={row[2] or '-'}, payment_status={row[3] or '-'}, "
                f"receipt_status={row[4] or '-'}"
            )
            for row in receipt_rows
        ) or "none"
        row_text = " | ".join(
            (
                f"fee_id={int(row[0])}, class={row[3]}, session={row[5]}, "
                f"amount={float(row[10] or 0.0):.2f}, status={row[11] or '-'}, "
                f"paid_date={row[12] or '-'}"
            )
            for row in fee_rows
        )
        admission_number = fee_rows[0][4] if fee_rows else '-'
        conflict_reports.append(
            f"student_id={int(student_id)}, admission_number={admission_number}, "
            f"month={int(month_number):02d}/{int(calendar_year)}, "
            f"fee_record_ids={fee_ids}, rows=[{row_text}], receipts=[{receipt_text}]"
        )

    if conflict_reports:
        raise RuntimeError(
            "Database migration found conflicting duplicate Monthly Fee records. "
            "No duplicate rows were deleted. Reconcile these records manually: "
            + "; ".join(conflict_reports[:20])
        )

    if safe_duplicate_ids:
        placeholders = ",".join("?" for _ in safe_duplicate_ids)
        connection.execute(
            f"DELETE FROM student_fee_records WHERE id IN ({placeholders})",
            safe_duplicate_ids,
        )
    return len(safe_duplicate_ids)



def _write_tracking_id_reconciliation_report(
    malformed_rows,
    collision_groups,
) -> str:
    """Write an audit-safe reconciliation report and return its path."""
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(
        BACKUPS_DIR,
        f"Tracking_ID_Reconciliation_{timestamp}.txt",
    )
    lines = [
        "TRACKING ID RECONCILIATION REQUIRED",
        f"Created at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "No student record was deleted or merged.",
        "",
    ]
    if malformed_rows:
        lines.append("Malformed Tracking IDs:")
        for student_id, raw_value in malformed_rows:
            lines.append(f"- Student ID {student_id}: {raw_value!r}")
        lines.append("")
    if collision_groups:
        lines.append("Canonical Tracking-ID collisions:")
        for canonical_value, rows in sorted(collision_groups.items()):
            details = ", ".join(
                f"Student ID {student_id} ({raw_value!r})"
                for student_id, raw_value in rows
            )
            lines.append(f"- {canonical_value}: {details}")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")
    return report_path


def _canonicalize_tracking_ids_for_migration(
    connection: sqlite3.Connection,
) -> None:
    """Canonicalize every valid tracking ID and fail without data loss on conflict."""
    rows = connection.execute(
        "SELECT student_id, tracking_id FROM students ORDER BY student_id"
    ).fetchall()
    canonical_rows = []
    malformed_rows = []
    grouped = {}
    for row in rows:
        student_id = int(row[0])
        raw_value = row[1]
        try:
            canonical_value = canonicalize_tracking_id(
                raw_value,
                fallback_number=student_id,
            )
        except ValueError:
            malformed_rows.append((student_id, raw_value))
            continue
        canonical_rows.append((student_id, raw_value, canonical_value))
        grouped.setdefault(canonical_value, []).append((student_id, raw_value))

    collision_groups = {
        canonical_value: values
        for canonical_value, values in grouped.items()
        if len(values) > 1
    }
    if malformed_rows or collision_groups:
        report_path = _write_tracking_id_reconciliation_report(
            malformed_rows,
            collision_groups,
        )
        raise RuntimeError(
            "Tracking-ID migration requires manual reconciliation.\n\n"
            f"Report: {report_path}"
        )

    connection.execute("DROP INDEX IF EXISTS idx_students_tracking_id_unique")
    connection.execute("DROP INDEX IF EXISTS idx_students_tracking_id_normalized_unique")
    for student_id, _raw_value, canonical_value in canonical_rows:
        connection.execute(
            "UPDATE students SET tracking_id = ? WHERE student_id = ?",
            (canonical_value, student_id),
        )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_students_tracking_id_normalized_unique
        ON students (UPPER(TRIM(tracking_id)))
        """
    )


def _migrate_students_table_for_coming_session_v22(
    connection: sqlite3.Connection,
) -> None:
    """Rebuild students with the V22 Coming Session lifecycle safely."""
    columns = _table_columns(connection, "students")
    required_new = {
        "target_session_start_year",
        "coming_session_status",
        "coming_session_effective_date",
        "coming_session_activated_at",
    }
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='students'"
    ).fetchone()
    table_sql = str(table_sql_row[0] or "") if table_sql_row else ""
    status_extended = "'Coming Session'" in table_sql
    if required_new.issubset(columns) and status_extended:
        return

    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        # Every current integrity trigger is schema-bound and several triggers
        # on child tables reference main.students. SQLite reparses those
        # triggers during ALTER TABLE; drop them transactionally before the
        # parent-table rebuild and reinstall the exact V22 definitions after
        # this helper returns. A rollback restores the original triggers.
        trigger_names = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        ]
        for trigger_name in trigger_names:
            escaped_name = trigger_name.replace('"', '""')
            connection.execute(f'DROP TRIGGER IF EXISTS "{escaped_name}"')
        connection.execute("DROP TABLE IF EXISTS students_v22")
        connection.execute(
            """
            CREATE TABLE students_v22 (
                student_id INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_number TEXT NOT NULL UNIQUE,
                tracking_id TEXT,
                roll_number INTEGER NOT NULL,
                released_roll_number INTEGER,
                class_id INTEGER NOT NULL,
                student_name TEXT NOT NULL,
                gender TEXT NOT NULL DEFAULT '',
                father_name TEXT NOT NULL,
                father_cnic TEXT,
                father_contact_number TEXT NOT NULL,
                whatsapp_number TEXT,
                current_address TEXT,
                permanent_address TEXT,
                school_leaving_certificate_submitted INTEGER NOT NULL DEFAULT 0,
                form_b_submitted INTEGER NOT NULL DEFAULT 0,
                birth_certificate_submitted INTEGER NOT NULL DEFAULT 0,
                date_of_birth TEXT,
                admission_date TEXT NOT NULL,
                admission_year INTEGER NOT NULL,
                admission_month INTEGER NOT NULL,
                admission_day INTEGER NOT NULL,
                admission_time TEXT NOT NULL,
                student_status TEXT NOT NULL DEFAULT 'Active'
                    CHECK (student_status IN (
                        'Active','Dropped','Archived','Graduated',
                        'Partially Graduated','Coming Session'
                    )),
                target_session_start_year INTEGER,
                coming_session_status TEXT CHECK (
                    coming_session_status IS NULL OR
                    coming_session_status IN ('PENDING','ACTIVATED')
                ),
                coming_session_effective_date TEXT,
                coming_session_activated_at TEXT,
                failure_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                created_by TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT,
                FOREIGN KEY (class_id) REFERENCES classes (id)
            )
            """
        )
        source_columns = [
            "student_id", "admission_number", "tracking_id", "roll_number",
            "released_roll_number", "class_id", "student_name", "gender",
            "father_name", "father_cnic", "father_contact_number",
            "whatsapp_number", "current_address", "permanent_address",
            "school_leaving_certificate_submitted", "form_b_submitted",
            "birth_certificate_submitted", "date_of_birth", "admission_date",
            "admission_year", "admission_month", "admission_day",
            "admission_time", "student_status", "failure_count", "created_at",
            "created_by", "updated_at", "updated_by",
        ]
        target_columns = source_columns[:24] + [
            "target_session_start_year", "coming_session_status",
            "coming_session_effective_date", "coming_session_activated_at",
        ] + source_columns[24:]
        select_parts = []
        for name in target_columns:
            if name in columns:
                select_parts.append(name)
            else:
                select_parts.append("NULL")
        connection.execute(
            f"INSERT INTO students_v22 ({','.join(target_columns)}) "
            f"SELECT {','.join(select_parts)} FROM students"
        )
        before_count = int(connection.execute("SELECT COUNT(*) FROM students").fetchone()[0])
        after_count = int(connection.execute("SELECT COUNT(*) FROM students_v22").fetchone()[0])
        if before_count != after_count:
            raise RuntimeError("V22 students-table migration changed the student count.")
        connection.execute("DROP TABLE students")
        connection.execute("ALTER TABLE students_v22 RENAME TO students")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(
            "V22 students-table migration created foreign-key violations: "
            + repr([tuple(row) for row in violations[:20]])
        )



def _install_overdue_promotion_schema_v23(
    connection: sqlite3.Connection,
) -> None:
    """Install session-bound promotion metadata without changing lifecycle data."""
    for column_name, definition in (
        ("source_session_start_year", "INTEGER"),
        ("target_session_start_year", "INTEGER"),
        ("effective_date", "TEXT"),
        ("promotion_mode", "TEXT"),
    ):
        _ensure_table_column(
            connection, "promotion_batches", column_name, definition
        )

    for column_name, definition in (
        ("completion_session_start_year", "INTEGER"),
        ("graduation_effective_date", "TEXT"),
    ):
        _ensure_table_column(
            connection, "graduation_records", column_name, definition
        )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_cycles (
            cycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            source_session_start_year INTEGER NOT NULL,
            target_session_start_year INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','COMPLETED','SKIPPED_EMPTY')),
            completed_batch_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            FOREIGN KEY (class_id) REFERENCES classes(id),
            FOREIGN KEY (completed_batch_id) REFERENCES promotion_batches(batch_id),
            CHECK (target_session_start_year = source_session_start_year + 1),
            CHECK (
                (status='COMPLETED' AND completed_batch_id IS NOT NULL AND completed_at IS NOT NULL)
                OR (status IN ('PENDING','SKIPPED_EMPTY') AND completed_batch_id IS NULL)
            )
        )
        """
    )

    # V22 promotion was restricted to January-March. Therefore the execution
    # year is the target session start year and the academic effect is April 1.
    connection.execute(
        """
        UPDATE promotion_batches
        SET target_session_start_year = CAST(strftime('%Y', promotion_datetime) AS INTEGER),
            source_session_start_year = CAST(strftime('%Y', promotion_datetime) AS INTEGER) - 1,
            effective_date = printf('%04d-04-01', CAST(strftime('%Y', promotion_datetime) AS INTEGER)),
            promotion_mode = 'NORMAL'
        WHERE source_session_start_year IS NULL
           OR target_session_start_year IS NULL
           OR effective_date IS NULL
           OR promotion_mode IS NULL
        """
    )

    invalid_batches = connection.execute(
        """
        SELECT batch_id,source_session_start_year,target_session_start_year,
               effective_date,promotion_mode
        FROM promotion_batches
        WHERE source_session_start_year IS NULL
           OR target_session_start_year <> source_session_start_year + 1
           OR effective_date <> printf('%04d-04-01', target_session_start_year)
           OR promotion_mode NOT IN ('NORMAL','OVERDUE')
        LIMIT 20
        """
    ).fetchall()
    if invalid_batches:
        raise RuntimeError(
            "V23 promotion-session backfill could not resolve promotion batches: "
            + repr([tuple(row) for row in invalid_batches])
        )

    duplicate_batches = connection.execute(
        """
        SELECT source_class_id,source_session_start_year,COUNT(*)
        FROM promotion_batches
        WHERE is_reversed=0
        GROUP BY source_class_id,source_session_start_year
        HAVING COUNT(*)>1
        LIMIT 20
        """
    ).fetchall()
    if duplicate_batches:
        raise RuntimeError(
            "V23 requires one active promotion batch per class/source session. "
            "Reconcile duplicates first: "
            + repr([tuple(row) for row in duplicate_batches])
        )

    connection.execute(
        """
        INSERT INTO promotion_cycles(
            class_id,source_session_start_year,target_session_start_year,status,
            completed_batch_id,created_at,completed_at,updated_at,updated_by
        )
        SELECT pb.source_class_id,pb.source_session_start_year,
               pb.target_session_start_year,'COMPLETED',pb.batch_id,
               pb.promotion_datetime,pb.promotion_datetime,pb.promotion_datetime,
               COALESCE(pb.created_by,'migration_v23')
        FROM promotion_batches pb
        WHERE pb.is_reversed=0
          AND NOT EXISTS (
              SELECT 1 FROM promotion_cycles pc
              WHERE pc.class_id=pb.source_class_id
                AND pc.source_session_start_year=pb.source_session_start_year
          )
        """
    )

    # Resolve graduation completion from the linked full-class promotion when
    # available. Legacy orphan repairs use the January-March execution year.
    connection.execute(
        """
        UPDATE graduation_records
        SET completion_session_start_year = COALESCE(
                completion_session_start_year,
                (
                    SELECT pb.source_session_start_year
                    FROM promotion_history ph
                    JOIN promotion_batches pb ON pb.batch_id=ph.batch_id
                    WHERE ph.student_id=graduation_records.student_id
                      AND ph.is_reversed=0 AND pb.is_reversed=0
                      AND ph.new_status IN ('Graduated','Partially Graduated')
                    ORDER BY ph.promotion_id DESC LIMIT 1
                ),
                CAST(strftime('%Y', graduation_date) AS INTEGER)-1
            ),
            graduation_effective_date = COALESCE(
                graduation_effective_date,
                (
                    SELECT pb.effective_date
                    FROM promotion_history ph
                    JOIN promotion_batches pb ON pb.batch_id=ph.batch_id
                    WHERE ph.student_id=graduation_records.student_id
                      AND ph.is_reversed=0 AND pb.is_reversed=0
                      AND ph.new_status IN ('Graduated','Partially Graduated')
                    ORDER BY ph.promotion_id DESC LIMIT 1
                ),
                printf('%04d-04-01', CAST(strftime('%Y', graduation_date) AS INTEGER))
            )
        WHERE completion_session_start_year IS NULL
           OR graduation_effective_date IS NULL
        """
    )

    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_batch_class_source_session_unique
        ON promotion_batches(source_class_id,source_session_start_year)
        WHERE is_reversed=0
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_cycle_class_source_unique
        ON promotion_cycles(class_id,source_session_start_year)
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_promotion_cycle_completed_batch_unique
        ON promotion_cycles(completed_batch_id)
        WHERE completed_batch_id IS NOT NULL
        """
    )


def run_schema_migrations(
    connection: sqlite3.Connection,
    installed_version: int,
) -> int:
    """
    Apply database migrations in numerical order.

    Each future schema change must have its own version block.
    The function returns the final successfully migrated version.
    """
    # Migrations may need to load legacy or deliberately inconsistent rows
    # before the final current-schema validator decides whether they can be
    # repaired or must be reconciled. Runtime guards remain strict after the
    # migration transaction finishes.
    connection.create_function(
        "HMS_TEST_FIXTURE_MODE", 0, lambda: 1, deterministic=True
    )

    current_version = int(
        installed_version
    )

    if current_version < 0:
        raise ValueError(
            "Installed database schema version cannot be negative."
        )

    if current_version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            (
                "The database schema is newer than this "
                "application supports."
            )
        )

    # Migration: version 0 -> version 1
    #
    # Version 1 is the existing baseline schema. Its tables and indexes
    # are created through CREATE TABLE/INDEX IF NOT EXISTS statements
    # before this migration runner is called.
    if current_version < 1:
        current_version = 1

    # Migration: version 1 -> version 2
    #
    # Individual monthly fees are stored as a percentage of the
    # applicable class monthly fee.
    if current_version < 2:
        override_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(student_fee_override);"
            ).fetchall()
        }

        if "monthly_fee_percentage" not in override_columns:
            connection.execute(
                """
                ALTER TABLE student_fee_override
                ADD COLUMN monthly_fee_percentage REAL
                """
            )

        current_version = 2

    # Migration: version 2 -> version 3
    #
    # Class fee schedules are isolated by April-March academic
    # session. A fee from one session must never carry into or
    # overwrite another session.
    if current_version < 3:
        fee_settings_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(fee_settings);"
            ).fetchall()
        }

        if (
            "session_start_year"
            not in fee_settings_columns
        ):
            connection.execute(
                """
                ALTER TABLE fee_settings
                ADD COLUMN session_start_year INTEGER
                """
            )

        fee_history_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(fee_history);"
            ).fetchall()
        }

        if (
            "session_start_year"
            not in fee_history_columns
        ):
            connection.execute(
                """
                ALTER TABLE fee_history
                ADD COLUMN session_start_year INTEGER
                """
            )

        # April-December belong to the calendar year's session.
        # January-March belong to the session starting last year.
        connection.execute(
            """
            UPDATE fee_settings
            SET session_start_year =
                CASE
                    WHEN effective_from_month >= 4
                        THEN effective_from_year
                    ELSE effective_from_year - 1
                END
            WHERE session_start_year IS NULL
            """
        )

        connection.execute(
            """
            UPDATE fee_history
            SET session_start_year =
                CASE
                    WHEN effective_from_month >= 4
                        THEN effective_from_year
                    ELSE effective_from_year - 1
                END
            WHERE session_start_year IS NULL
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_fee_settings_class_session
            ON fee_settings (
                class_id,
                session_start_year
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_fee_history_class_session
            ON fee_history (
                class_id,
                session_start_year
            )
            """
        )

        current_version = 3

    # Migration: version 3 -> version 4
    #
    # Record every student class movement so fee recalculation can use
    # an explicit effective month/year for promotions, reversals, and
    # manual class changes.
    if current_version < 4:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS student_class_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                old_class_id INTEGER,
                new_class_id INTEGER NOT NULL,
                effective_month INTEGER NOT NULL CHECK (
                    effective_month BETWEEN 1 AND 12
                ),
                effective_year INTEGER NOT NULL,
                change_type TEXT NOT NULL CHECK (
                    change_type IN (
                        'ADMISSION',
                        'PROMOTION',
                        'REVERSE',
                        'MANUAL'
                    )
                ),
                change_datetime TEXT NOT NULL,
                changed_by TEXT,
                FOREIGN KEY (student_id)
                    REFERENCES students (student_id),
                FOREIGN KEY (old_class_id)
                    REFERENCES classes (id),
                FOREIGN KEY (new_class_id)
                    REFERENCES classes (id)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_student_class_history_student_effective
            ON student_class_history (
                student_id,
                effective_year,
                effective_month,
                history_id
            )
            """
        )

        # Version 3 databases predate class-history recording. Backfill one
        # immutable Admission event per existing student so the current
        # startup invariant is satisfiable without changing the student's
        # class, admission period, or any financial record. NOT EXISTS keeps
        # this migration safe and idempotent for partially upgraded databases.
        connection.execute(
            """
            INSERT INTO student_class_history(
                student_id, old_class_id, new_class_id,
                effective_month, effective_year, change_type,
                change_datetime, changed_by
            )
            SELECT
                students.student_id,
                NULL,
                students.class_id,
                students.admission_month,
                students.admission_year,
                'ADMISSION',
                COALESCE(NULLIF(TRIM(students.admission_date), ''), students.created_at),
                COALESCE(NULLIF(TRIM(students.created_by), ''), 'migration_v4')
            FROM students
            WHERE NOT EXISTS (
                SELECT 1
                FROM student_class_history AS history
                WHERE history.student_id = students.student_id
                  AND history.change_type = 'ADMISSION'
            )
            """
        )

        current_version = 4

    # Migration: version 4 -> version 5
    #
    # Student admission records now support gender, WhatsApp,
    # addresses, date of birth, Form B, and Birth Certificate.
    # Existing student and fee records remain unchanged.
    if current_version < 5:
        student_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(students);"
            ).fetchall()
        }

        if "gender" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN gender TEXT NOT NULL DEFAULT ''
                """
            )

        if "whatsapp_number" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN whatsapp_number TEXT
                """
            )

        if "current_address" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN current_address TEXT
                """
            )

        if "permanent_address" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN permanent_address TEXT
                """
            )

        if "date_of_birth" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN date_of_birth TEXT
                """
            )

        if "form_b_submitted" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN form_b_submitted
                    INTEGER NOT NULL DEFAULT 0
                """
            )

        if "birth_certificate_submitted" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN birth_certificate_submitted
                    INTEGER NOT NULL DEFAULT 0
                """
            )

        current_version = 5

    # Migration: version 5 -> version 6
    #
    # Every class receives an editable student-capacity limit.
    # The initial school-wide default is 40 students per class.
    # Existing class, student, promotion, and fee records remain
    # unchanged.
    if current_version < 6:
        class_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(classes);"
            ).fetchall()
        }

        if "student_capacity" not in class_columns:
            connection.execute(
                """
                ALTER TABLE classes
                ADD COLUMN student_capacity
                    INTEGER NOT NULL DEFAULT 40
                    CHECK (student_capacity > 0)
                """
            )

        # Defensive normalization for any future imported or
        # manually edited database containing an invalid value.
        connection.execute(
            """
            UPDATE classes
            SET student_capacity = 40
            WHERE student_capacity IS NULL
               OR student_capacity < 1
            """
        )

        current_version = 6

    # Migration: version 6 -> version 7
    #
    # Every student receives one immutable Tracking ID for Fee
    # Management searches, complete fee history, and fee slips.
    #
    # Existing student IDs are converted to:
    # HMS-000001, HMS-000002, and so on.
    #
    # Existing student, class, promotion, and fee records remain
    # unchanged.
    if current_version < 7:
        student_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(students);"
            ).fetchall()
        }

        if "tracking_id" not in student_columns:
            connection.execute(
                """
                ALTER TABLE students
                ADD COLUMN tracking_id TEXT
                """
            )

        connection.execute(
            """
            UPDATE students
            SET tracking_id =
                'HMS-' || printf('%06d', student_id)
            WHERE tracking_id IS NULL
               OR TRIM(tracking_id) = ''
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_students_tracking_id_unique
            ON students (
                tracking_id
            )
            """
        )

        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS
                trg_students_assign_tracking_id
            AFTER INSERT ON students
            FOR EACH ROW
            WHEN NEW.tracking_id IS NULL
              OR TRIM(NEW.tracking_id) = ''
            BEGIN
                UPDATE students
                SET tracking_id =
                    'HMS-' || printf(
                        '%06d',
                        NEW.student_id
                    )
                WHERE student_id = NEW.student_id;
            END
            """
        )

        current_version = 7

    # Migration: version 7 -> version 8
    #
    # ADMISSION_FEE_SCHEMA_REMOVAL_V8
    #
    # Admission Fee is permanently removed from Fee Management.
    #
    # This migration:
    # - preserves every student, class, monthly fee, and monthly receipt;
    # - removes Admission Fee receipt items and fee records;
    # - removes payment transactions containing only Admission Fee;
    # - recalculates totals of mixed payment transactions;
    # - physically removes admission_fee columns from fee-setting tables.
    if current_version < 8:
        # Preserve the highest historically issued numeric receipt before
        # Admission-Fee cleanup can delete a payment row.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_number_sequence (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_issued_number INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            )
            """
        )
        highest_receipt_before_cleanup = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(CAST(TRIM(receipt_no) AS INTEGER)), 0)
                FROM fee_payments
                WHERE TRIM(receipt_no) <> ''
                  AND TRIM(receipt_no) NOT GLOB '*[^0-9]*'
                """
            ).fetchone()[0] or 0
        )
        connection.execute(
            """
            INSERT INTO receipt_number_sequence(
                id, last_issued_number, updated_at, updated_by
            ) VALUES(1, ?, ?, 'migration_v8')
            ON CONFLICT(id) DO UPDATE SET
                last_issued_number = MAX(
                    receipt_number_sequence.last_issued_number,
                    excluded.last_issued_number
                ),
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (highest_receipt_before_cleanup, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

        student_count_before = int(
            connection.execute(
                "SELECT COUNT(*) FROM students"
            ).fetchone()[0]
        )

        class_count_before = int(
            connection.execute(
                "SELECT COUNT(*) FROM classes"
            ).fetchone()[0]
        )

        monthly_record_count_before = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM student_fee_records
                WHERE fee_type = 'Monthly'
                """
            ).fetchone()[0]
        )

        monthly_receipt_count_before = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM fee_receipts AS receipts

                INNER JOIN student_fee_records
                    AS records
                  ON records.id =
                        receipts.student_fee_record_id

                WHERE records.fee_type = 'Monthly'
                """
            ).fetchone()[0]
        )

        connection.execute(
            """
            CREATE TEMP TABLE
                admission_fee_affected_payments_v8
            AS
            SELECT DISTINCT
                receipts.payment_id

            FROM fee_receipts AS receipts

            INNER JOIN student_fee_records
                AS records
              ON records.id =
                    receipts.student_fee_record_id

            WHERE records.fee_type = 'Admission'
            """
        )

        connection.execute(
            """
            DELETE FROM fee_receipts

            WHERE student_fee_record_id IN (
                SELECT id
                FROM student_fee_records
                WHERE fee_type = 'Admission'
            )
            """
        )

        connection.execute(
            """
            DELETE FROM student_fee_records
            WHERE fee_type = 'Admission'
            """
        )

        connection.execute(
            """
            UPDATE fee_payments

            SET total_amount = COALESCE(
                (
                    SELECT ROUND(
                        SUM(receipts.amount),
                        2
                    )

                    FROM fee_receipts AS receipts

                    WHERE receipts.payment_id =
                          fee_payments.id
                ),
                0
            )

            WHERE id IN (
                SELECT payment_id
                FROM admission_fee_affected_payments_v8
            )
            """
        )

        connection.execute(
            """
            DELETE FROM fee_payments

            WHERE id IN (
                SELECT payment_id
                FROM admission_fee_affected_payments_v8
            )

              AND NOT EXISTS (
                    SELECT 1
                    FROM fee_receipts AS receipts
                    WHERE receipts.payment_id =
                          fee_payments.id
                  )
            """
        )

        connection.execute(
            """
            DROP TABLE
                admission_fee_affected_payments_v8
            """
        )

        connection.execute(
            """
            DROP INDEX IF EXISTS
                idx_fee_settings_class_session
            """
        )

        connection.execute(
            """
            DROP INDEX IF EXISTS
                idx_fee_history_class_session
            """
        )

        connection.execute(
            """
            DROP TABLE IF EXISTS
                fee_settings_v8
            """
        )

        connection.execute(
            """
            CREATE TABLE fee_settings_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL,
                monthly_fee REAL NOT NULL CHECK (monthly_fee > 0),
                session_start_year INTEGER NOT NULL,
                effective_from_month INTEGER NOT NULL,
                effective_from_year INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (class_id)
                    REFERENCES classes (id)
            )
            """
        )

        connection.execute(
            """
            INSERT INTO fee_settings_v8 (
                id,
                class_id,
                monthly_fee,
                session_start_year,
                effective_from_month,
                effective_from_year,
                created_at,
                updated_at
            )
            SELECT
                id,
                class_id,
                monthly_fee,

                COALESCE(
                    session_start_year,
                    CASE
                        WHEN effective_from_month >= 4
                            THEN effective_from_year
                        ELSE effective_from_year - 1
                    END
                ),

                effective_from_month,
                effective_from_year,
                created_at,
                updated_at

            FROM fee_settings
            """
        )

        connection.execute(
            """
            DROP TABLE fee_settings
            """
        )

        connection.execute(
            """
            ALTER TABLE fee_settings_v8
            RENAME TO fee_settings
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX
                idx_fee_settings_class_session

            ON fee_settings (
                class_id,
                session_start_year
            )
            """
        )

        connection.execute(
            """
            DROP TABLE IF EXISTS
                fee_history_v8
            """
        )

        connection.execute(
            """
            CREATE TABLE fee_history_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                class_id INTEGER NOT NULL,
                monthly_fee REAL NOT NULL CHECK (monthly_fee > 0),
                session_start_year INTEGER NOT NULL,
                effective_from_month INTEGER NOT NULL,
                effective_from_year INTEGER NOT NULL,
                effective_to_month INTEGER,
                effective_to_year INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (class_id)
                    REFERENCES classes (id)
            )
            """
        )

        connection.execute(
            """
            INSERT INTO fee_history_v8 (
                id,
                class_id,
                monthly_fee,
                session_start_year,
                effective_from_month,
                effective_from_year,
                effective_to_month,
                effective_to_year,
                created_at
            )
            SELECT
                id,
                class_id,
                monthly_fee,

                COALESCE(
                    session_start_year,
                    CASE
                        WHEN effective_from_month >= 4
                            THEN effective_from_year
                        ELSE effective_from_year - 1
                    END
                ),

                effective_from_month,
                effective_from_year,
                effective_to_month,
                effective_to_year,
                created_at

            FROM fee_history
            """
        )

        connection.execute(
            """
            DROP TABLE fee_history
            """
        )

        connection.execute(
            """
            ALTER TABLE fee_history_v8
            RENAME TO fee_history
            """
        )

        connection.execute(
            """
            CREATE INDEX
                idx_fee_history_class_session

            ON fee_history (
                class_id,
                session_start_year
            )
            """
        )

        connection.execute(
            """
            DROP TABLE IF EXISTS
                student_fee_override_v8
            """
        )

        connection.execute(
            """
            CREATE TABLE student_fee_override_v8 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                monthly_fee REAL,
                monthly_fee_percentage REAL,
                effective_from_month INTEGER NOT NULL,
                effective_from_year INTEGER NOT NULL,
                effective_to_month INTEGER,
                effective_to_year INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (student_id)
                    REFERENCES students (student_id)
            )
            """
        )

        connection.execute(
            """
            INSERT INTO student_fee_override_v8 (
                id,
                student_id,
                monthly_fee,
                monthly_fee_percentage,
                effective_from_month,
                effective_from_year,
                effective_to_month,
                effective_to_year,
                is_active,
                created_at,
                updated_at
            )
            SELECT
                id,
                student_id,
                monthly_fee,
                monthly_fee_percentage,
                effective_from_month,
                effective_from_year,
                effective_to_month,
                effective_to_year,
                is_active,
                created_at,
                updated_at

            FROM student_fee_override
            """
        )

        connection.execute(
            """
            DROP TABLE student_fee_override
            """
        )

        connection.execute(
            """
            ALTER TABLE student_fee_override_v8
            RENAME TO student_fee_override
            """
        )

        student_count_after = int(
            connection.execute(
                "SELECT COUNT(*) FROM students"
            ).fetchone()[0]
        )

        class_count_after = int(
            connection.execute(
                "SELECT COUNT(*) FROM classes"
            ).fetchone()[0]
        )

        monthly_record_count_after = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM student_fee_records
                WHERE fee_type = 'Monthly'
                """
            ).fetchone()[0]
        )

        monthly_receipt_count_after = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM fee_receipts AS receipts

                INNER JOIN student_fee_records
                    AS records
                  ON records.id =
                        receipts.student_fee_record_id

                WHERE records.fee_type = 'Monthly'
                """
            ).fetchone()[0]
        )

        remaining_admission_records = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM student_fee_records
                WHERE fee_type = 'Admission'
                """
            ).fetchone()[0]
        )

        remaining_admission_receipts = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM fee_receipts
                WHERE LOWER(TRIM(item_name)) =
                      'admission fee'
                """
            ).fetchone()[0]
        )

        orphan_receipts = int(
            connection.execute(
                """
                SELECT COUNT(*)

                FROM fee_receipts AS receipts

                LEFT JOIN fee_payments AS payments
                  ON payments.id =
                        receipts.payment_id

                LEFT JOIN student_fee_records
                    AS records
                  ON records.id =
                        receipts.student_fee_record_id

                WHERE payments.id IS NULL
                   OR records.id IS NULL
                """
            ).fetchone()[0]
        )

        payment_total_mismatches = int(
            connection.execute(
                """
                SELECT COUNT(*)

                FROM fee_payments AS payments

                WHERE ROUND(
                    payments.total_amount,
                    2
                ) <> ROUND(
                    COALESCE(
                        (
                            SELECT SUM(
                                receipts.amount
                            )

                            FROM fee_receipts
                                AS receipts

                            WHERE receipts.payment_id =
                                  payments.id
                        ),
                        0
                    ),
                    2
                )
                """
            ).fetchone()[0]
        )

        if student_count_after != student_count_before:
            raise RuntimeError(
                "Version 8 migration changed the student count."
            )

        if class_count_after != class_count_before:
            raise RuntimeError(
                "Version 8 migration changed the class count."
            )

        if (
            monthly_record_count_after
            != monthly_record_count_before
        ):
            raise RuntimeError(
                "Version 8 migration changed monthly fee records."
            )

        if (
            monthly_receipt_count_after
            != monthly_receipt_count_before
        ):
            raise RuntimeError(
                "Version 8 migration changed monthly receipt items."
            )

        if remaining_admission_records != 0:
            raise RuntimeError(
                "Admission Fee records remain after version 8."
            )

        if remaining_admission_receipts != 0:
            raise RuntimeError(
                "Admission Fee receipt items remain after version 8."
            )

        if orphan_receipts != 0:
            raise RuntimeError(
                "Version 8 migration created orphan receipt rows."
            )

        if payment_total_mismatches != 0:
            raise RuntimeError(
                "Version 8 migration left incorrect payment totals."
            )

        for table_name in (
            "fee_settings",
            "fee_history",
            "student_fee_override",
        ):
            table_columns = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({table_name});"
                ).fetchall()
            }

            if "admission_fee" in table_columns:
                raise RuntimeError(
                    (
                        "Admission Fee column remains in "
                        f"{table_name}."
                    )
                )

        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check;"
        ).fetchall()

        if foreign_key_violations:
            raise RuntimeError(
                (
                    "Version 8 migration created "
                    "foreign-key violations."
                )
            )

        current_version = 8

    # Migration: version 8 -> version 9
    #
    # SIBLING_MONTHLY_FEE_SCHEMA_V9
    #
    # Store the global sibling-fee percentage timeline and each
    # student's period-based sibling assignment. Earlier months and
    # receipt-backed Paid records are preserved by service logic.
    if current_version < 9:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sibling_fee_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monthly_fee_percentage REAL NOT NULL
                    CHECK (monthly_fee_percentage BETWEEN 0 AND 100),
                effective_from_month INTEGER NOT NULL
                    CHECK (effective_from_month BETWEEN 1 AND 12),
                effective_from_year INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_sibling_fee_settings_effective
            ON sibling_fee_settings (
                effective_from_year,
                effective_from_month
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sibling_fee_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                normalized_father_cnic TEXT,
                monthly_fee_percentage REAL NOT NULL
                    CHECK (monthly_fee_percentage BETWEEN 0 AND 100),
                is_sibling_policy INTEGER NOT NULL DEFAULT 0
                    CHECK (is_sibling_policy IN (0, 1)),
                assignment_role TEXT NOT NULL
                    CHECK (assignment_role IN (
                        'FULL',
                        'DISCOUNTED',
                        'STANDARD'
                    )),
                effective_from_month INTEGER NOT NULL
                    CHECK (effective_from_month BETWEEN 1 AND 12),
                effective_from_year INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (student_id)
                    REFERENCES students (student_id)
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_sibling_fee_assignment_student_effective
            ON sibling_fee_assignments (
                student_id,
                effective_from_year,
                effective_from_month
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_sibling_fee_assignment_cnic_effective
            ON sibling_fee_assignments (
                normalized_father_cnic,
                effective_from_year,
                effective_from_month
            )
            """
        )

        current_version = 9

    # Migration: version 9 -> version 10
    #
    # SINGLE_ADMIN_AUTHENTICATION_SCHEMA_V10
    #
    # Store one offline administrator username together with secure
    # password and recovery digests. Plain passwords, CNIC values, and
    # dates of birth are never stored in the database.
    if current_version < 10:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_credentials (
                user_id INTEGER PRIMARY KEY
                    CHECK (user_id = 1),
                username TEXT NOT NULL COLLATE NOCASE,
                password_digest TEXT NOT NULL,
                recovery_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                created_by TEXT,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            )
            """
        )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_admin_credentials_username
            ON admin_credentials (
                username COLLATE NOCASE
            )
            """
        )

        current_version = 10

    # Migration: version 10 -> version 11
    #
    # IMMEDIATE_PAYMENT_CANCELLATION_SCHEMA_V11
    #
    # Keep cancelled payments and their receipt rows for audit while
    # excluding them from active fee collection and receipt history.
    if current_version < 11:
        payment_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(fee_payments);"
            ).fetchall()
        }

        payment_column_definitions = {
            "payment_status": (
                "TEXT NOT NULL DEFAULT 'Completed'"
            ),
            "cancelled_at": "TEXT",
            "cancelled_by": "TEXT",
            "cancellation_reason": "TEXT",
            "student_status_before": "TEXT",
            "student_status_after": "TEXT",
            "auto_graduated": (
                "INTEGER NOT NULL DEFAULT 0"
            ),
            "auto_archived": (
                "INTEGER NOT NULL DEFAULT 0"
            ),
        }

        for column_name, definition in (
            payment_column_definitions.items()
        ):
            if column_name in payment_columns:
                continue

            connection.execute(
                f"ALTER TABLE fee_payments "
                f"ADD COLUMN {column_name} {definition}"
            )

        connection.execute(
            """
            UPDATE fee_payments
            SET payment_status = 'Completed'
            WHERE payment_status IS NULL
               OR TRIM(payment_status) = ''
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_fee_payments_status_date
            ON fee_payments (
                payment_status,
                payment_date,
                id
            )
            """
        )

        current_version = 11

    # Migration: version 11 -> version 12
    # ROLL_NUMBER_UNIQUENESS_SCHEMA_V12
    #
    # Completed students release their positive rolls before genuine
    # duplicate detection. This ordering prevents legacy Graduated or
    # Partially Graduated rows from blocking a safe upgrade.
    if current_version < 12:
        _release_completed_student_rolls(connection)
        _raise_for_duplicate_class_rolls(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_students_class_roll_unique "
            "ON students (class_id, roll_number)"
        )
        current_version = 12

    # Migration: version 12 -> version 13
    # RELEASED_ROLL_NUMBER_SCHEMA_V13
    if current_version < 13:
        _release_completed_student_rolls(connection)
        _raise_for_duplicate_class_rolls(connection)
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_students_class_roll_unique "
            "ON students (class_id, roll_number)"
        )
        current_version = 13

    # Migration: version 13 -> version 14
    # IMMUTABLE_RECEIPT_AND_MONTHLY_FEE_UNIQUENESS_SCHEMA_V14
    if current_version < 14:
        _install_receipt_snapshot_columns(connection)
        _repair_duplicate_monthly_fee_records(connection)
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                idx_student_monthly_fee_period_unique
            ON student_fee_records (
                student_id,
                fee_type,
                month_number,
                year
            )
            WHERE fee_type = 'Monthly'
              AND month_number IS NOT NULL
              AND year IS NOT NULL
            """
        )
        current_version = 14

    # Migration: version 14 -> version 15
    # CLASS_LIFECYCLE_AND_ADMISSION_NUMBER_AUDIT_SCHEMA_V15
    if current_version < 15:
        _ensure_table_column(
            connection,
            "classes",
            "is_active",
            "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))",
        )
        connection.execute(
            "UPDATE classes SET is_active = 1 "
            "WHERE is_active IS NULL OR is_active NOT IN (0, 1)"
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_classes_active_level_section
            ON classes (
                is_active,
                class_level,
                section
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admission_number_history (
                history_id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                old_admission_number TEXT NOT NULL,
                new_admission_number TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                changed_by TEXT,
                FOREIGN KEY (student_id)
                    REFERENCES students (student_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_admission_number_history_student_changed
            ON admission_number_history (
                student_id,
                changed_at,
                history_id
            )
            """
        )
        current_version = 15

    # Migration: version 15 -> version 16
    # WAIVED_FEES_INCREMENTAL_HISTORY_AND_OVERRIDE_PERIODS_SCHEMA_V16
    if current_version < 16:
        _ensure_table_column(
            connection,
            "fee_settings",
            "created_by",
            "TEXT",
        )
        _ensure_table_column(
            connection,
            "fee_settings",
            "updated_by",
            "TEXT",
        )
        _ensure_table_column(
            connection,
            "fee_history",
            "created_by",
            "TEXT",
        )
        _ensure_table_column(
            connection,
            "fee_history",
            "updated_at",
            "TEXT",
        )
        _ensure_table_column(
            connection,
            "fee_history",
            "updated_by",
            "TEXT",
        )
        _ensure_table_column(
            connection,
            "student_fee_override",
            "removed_effective_month",
            "INTEGER",
        )
        _ensure_table_column(
            connection,
            "student_fee_override",
            "removed_effective_year",
            "INTEGER",
        )
        _ensure_table_column(
            connection,
            "student_fee_override",
            "removed_at",
            "TEXT",
        )
        _ensure_table_column(
            connection,
            "student_fee_override",
            "removed_by",
            "TEXT",
        )

        connection.execute(
            "UPDATE fee_settings SET created_by = COALESCE(created_by, 'legacy')"
        )
        connection.execute(
            "UPDATE fee_history SET created_by = COALESCE(created_by, 'legacy'), "
            "updated_at = COALESCE(updated_at, created_at)"
        )

        # Receipt-free zero-value rows were historically marked Paid even
        # though no payment occurred. Freeze them as Waived and clear the
        # artificial payment date. Genuine receipt-backed payments remain Paid.
        connection.execute(
            """
            UPDATE student_fee_records AS records
            SET status = 'Waived',
                paid_date = NULL,
                updated_at = COALESCE(updated_at, created_at)
            WHERE records.fee_type = 'Monthly'
              AND records.amount = 0
              AND records.status = 'Paid'
              AND NOT EXISTS (
                    SELECT 1
                    FROM fee_receipts AS receipts
                    INNER JOIN fee_payments AS payments
                      ON payments.id = receipts.payment_id
                    WHERE receipts.student_fee_record_id = records.id
                      AND receipts.status = 'Paid'
                      AND payments.payment_status = 'Completed'
              )
            """
        )
        current_version = 16

    # Migration: version 16 -> version 17
    # SIBLING_AUDIT_STATUS_RECEIPT_AND_TRACKING_ID_SCHEMA_V17
    if current_version < 17:
        # Append-only sibling percentage settings and assignment history.
        _ensure_table_column(connection, "sibling_fee_settings", "superseded_at", "TEXT")
        _ensure_table_column(connection, "sibling_fee_settings", "superseded_by", "TEXT")
        _ensure_table_column(
            connection,
            "sibling_fee_settings",
            "is_active",
            "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))",
        )
        _ensure_table_column(
            connection,
            "sibling_fee_assignments",
            "normalized_father_name",
            "TEXT",
        )
        _ensure_table_column(connection, "sibling_fee_assignments", "superseded_at", "TEXT")
        _ensure_table_column(connection, "sibling_fee_assignments", "superseded_by", "TEXT")
        _ensure_table_column(
            connection,
            "sibling_fee_assignments",
            "is_active",
            "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))",
        )
        connection.execute("DROP INDEX IF EXISTS idx_sibling_fee_settings_effective")
        connection.execute("DROP INDEX IF EXISTS idx_sibling_fee_assignment_student_effective")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sibling_fee_settings_effective_version
            ON sibling_fee_settings(effective_from_year, effective_from_month, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sibling_fee_assignment_student_effective_version
            ON sibling_fee_assignments(student_id, effective_from_year, effective_from_month, id)
            """
        )

        # Explicit lifecycle/status audit history.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS student_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                old_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'STATUS_CORRECTION',
                effective_datetime TEXT NOT NULL,
                reason TEXT,
                changed_by TEXT,
                created_at TEXT NOT NULL,
                related_class_id INTEGER,
                related_event_id INTEGER,
                FOREIGN KEY(student_id) REFERENCES students(student_id),
                FOREIGN KEY(related_class_id) REFERENCES classes(id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_student_status_history_student_effective
            ON student_status_history(student_id, effective_datetime, id)
            """
        )
        legacy_archive_rows = connection.execute(
            """
            SELECT history_id, student_id, new_class_id, change_datetime
            FROM student_class_history
            WHERE old_class_id = new_class_id
              AND change_type = 'MANUAL'
              AND changed_by = 'automatic_archive'
            ORDER BY history_id
            """
        ).fetchall()
        for row in legacy_archive_rows:
            connection.execute(
                """
                INSERT INTO student_status_history(
                    student_id, old_status, new_status, event_type, effective_datetime,
                    reason, changed_by, created_at, related_class_id, related_event_id
                ) VALUES(?, 'Dropped', 'Archived', 'ARCHIVE', ?, ?, 'automatic_archive', ?, ?, ?)
                """,
                (
                    int(row[1]),
                    str(row[3]),
                    "Migrated safely identified automatic archive event.",
                    str(row[3]),
                    int(row[2]),
                    int(row[0]),
                ),
            )
        if legacy_archive_rows:
            placeholders = ",".join("?" for _ in legacy_archive_rows)
            connection.execute(
                f"DELETE FROM student_class_history WHERE history_id IN ({placeholders})",
                [int(row[0]) for row in legacy_archive_rows],
            )

        # Permanent monotonic receipt-number sequence.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS receipt_number_sequence (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_issued_number INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                updated_by TEXT
            )
            """
        )
        highest_existing_receipt = int(
            connection.execute(
                """
                SELECT COALESCE(MAX(CAST(TRIM(receipt_no) AS INTEGER)), 0)
                FROM fee_payments
                WHERE TRIM(receipt_no) <> ''
                  AND TRIM(receipt_no) NOT GLOB '*[^0-9]*'
                """
            ).fetchone()[0] or 0
        )
        connection.execute(
            """
            INSERT INTO receipt_number_sequence(id, last_issued_number, updated_at, updated_by)
            VALUES(1, ?, ?, 'migration_v17')
            ON CONFLICT(id) DO UPDATE SET
                last_issued_number = MAX(
                    receipt_number_sequence.last_issued_number,
                    excluded.last_issued_number
                ),
                updated_at = excluded.updated_at,
                updated_by = excluded.updated_by
            """,
            (highest_existing_receipt, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )

        # Canonical, case-insensitive Tracking-ID identity.
        _canonicalize_tracking_ids_for_migration(connection)
        _refresh_placeholder_receipt_snapshots_after_canonical_repairs(connection)
        current_version = 17

    # Migration: version 17 -> version 18
    # CURRENT_SCHEMA_FINANCIAL_AND_AUDIT_INTEGRITY_V18
    if current_version < 18:
        for table_name in ("fee_settings", "fee_history"):
            _ensure_table_column(connection, table_name, "change_reason", "TEXT")
            _ensure_table_column(connection, table_name, "superseded_at", "TEXT")
            _ensure_table_column(connection, table_name, "superseded_by", "TEXT")
            _ensure_table_column(connection, table_name, "superseded_reason", "TEXT")
            _ensure_table_column(connection, table_name, "superseded_by_record_id", "INTEGER")
        for column_name, definition in (
            ("created_by", "TEXT"),
            ("updated_by", "TEXT"),
            ("change_reason", "TEXT"),
            ("superseded_at", "TEXT"),
            ("superseded_by", "TEXT"),
            ("superseded_reason", "TEXT"),
            ("superseded_by_record_id", "INTEGER"),
        ):
            _ensure_table_column(connection, "student_fee_override", column_name, definition)
        connection.execute(
            "UPDATE fee_settings SET created_by=COALESCE(NULLIF(TRIM(created_by),''),'LEGACY_UNKNOWN'), "
            "change_reason=COALESCE(NULLIF(TRIM(change_reason),''),'LEGACY_MIGRATION')"
        )
        connection.execute(
            "UPDATE fee_history SET created_by=COALESCE(NULLIF(TRIM(created_by),''),'LEGACY_UNKNOWN'), "
            "change_reason=COALESCE(NULLIF(TRIM(change_reason),''),'LEGACY_MIGRATION')"
        )
        connection.execute(
            "UPDATE student_fee_override SET created_by=COALESCE(NULLIF(TRIM(created_by),''),'LEGACY_UNKNOWN'), "
            "change_reason=COALESCE(NULLIF(TRIM(change_reason),''),'LEGACY_MIGRATION')"
        )
        schema_integrity.install_integrity_triggers(connection)
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)
        current_version = 18

    # Migration: version 18 -> version 19
    # CANONICAL_IDENTITY_LIFECYCLE_POLICY_AND_BACKUP_SCHEMA_V19
    if current_version < 19:
        _ensure_table_column(
            connection,
            "student_status_history",
            "event_type",
            "TEXT NOT NULL DEFAULT 'STATUS_CORRECTION'",
        )
        connection.execute(
            """
            UPDATE student_status_history
            SET event_type = CASE
                WHEN old_status='Active' AND new_status='Dropped' THEN 'DROP'
                WHEN old_status='Dropped' AND new_status='Archived' THEN 'ARCHIVE'
                WHEN new_status='Active' AND old_status IN ('Dropped','Archived') THEN 'REACTIVATION'
                WHEN new_status IN ('Graduated','Partially Graduated') THEN 'GRADUATION'
                ELSE 'STATUS_CORRECTION'
            END
            WHERE event_type IS NULL
               OR TRIM(event_type)=''
               OR event_type='STATUS_CORRECTION'
            """
        )
        # Every new admission now assigns Tracking ID explicitly, but the
        # canonical trigger remains as secondary defence and is definition-
        # checked by final schema validation.
        valid, message = schema_integrity.validate_tracking_id_trigger(
            connection,
            repair_missing=True,
        )
        if not valid:
            raise RuntimeError(message)

        # All new normalized uniqueness and active-lifecycle indexes are
        # recreated only when existing records are conflict-free. No student,
        # class, graduation, or financial record is deleted automatically.
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)

        current_version = 19

    # Migration: version 19 -> version 20
    # PAYMENT_EVIDENCE_AND_CROSS_RECORD_INTEGRITY_V20
    if current_version < 20:
        # Canonicalize only recognized fee-type spellings. Unknown values are
        # preserved and rejected by validation for controlled reconciliation.
        # Repair exact case-variant duplicate Monthly rows first; otherwise the
        # V14 partial unique index can make canonicalization fail abruptly.
        _repair_duplicate_monthly_fee_records(connection)
        connection.execute("UPDATE student_fee_records SET fee_type='Monthly' WHERE UPPER(TRIM(fee_type))='MONTHLY'")
        connection.execute("UPDATE student_fee_records SET fee_type='Admission' WHERE UPPER(TRIM(fee_type))='ADMISSION'")
        connection.execute(
            """
            UPDATE student_fee_records
            SET session_year=printf('%04d-%04d',
                CASE WHEN month_number>=4 THEN year ELSE year-1 END,
                CASE WHEN month_number>=4 THEN year+1 ELSE year END)
            WHERE fee_type='Monthly' AND month_number BETWEEN 1 AND 12 AND year IS NOT NULL
            """
        )
        _refresh_placeholder_receipt_snapshots_after_canonical_repairs(connection)
        # Legacy completed students without a graduation row are repaired
        # deterministically during migration only. Current-schema restores are
        # still rejected when this invariant is missing.
        connection.execute(
            """
            INSERT INTO graduation_records(
                student_id, admission_number, last_class_id, last_class_name,
                last_roll_number, graduation_date, status, is_reversed
            )
            SELECT s.student_id, s.admission_number, s.class_id, c.class_name,
                   COALESCE(s.released_roll_number, ABS(s.roll_number)),
                   COALESCE(NULLIF(TRIM(s.updated_at),''), NULLIF(TRIM(s.admission_date),''), datetime('now')),
                   s.student_status, 0
            FROM students s
            LEFT JOIN classes c ON c.id=s.class_id
            WHERE s.student_status IN ('Graduated','Partially Graduated')
              AND NOT EXISTS (
                  SELECT 1 FROM graduation_records g
                  WHERE g.student_id=s.student_id AND g.is_reversed=0
              )
            """
        )
        schema_integrity.install_integrity_triggers(connection)
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)
        # All remaining inconsistencies are unsafe to guess or rewrite.
        for check in (
            schema_integrity.validate_payment_receipt_integrity,
            schema_integrity.validate_promotion_integrity,
            schema_integrity.validate_fee_record_semantics,
            schema_integrity.validate_lifecycle_graduation_integrity,
            schema_integrity.validate_admission_history_integrity,
            schema_integrity.validate_sibling_assignment_integrity,
        ):
            valid, message = check(connection)
            if not valid:
                raise RuntimeError(message)
        current_version = 20

    # Migration: version 20 -> version 21
    # GLOBAL_ADMISSION_SEQUENCE_SIBLING_ELIGIBILITY_AND_PAID_DATE_V21
    if current_version < 21:
        schema_integrity.install_integrity_triggers(connection)
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)
        for check in (
            schema_integrity.validate_admission_number_sequence_integrity,
            schema_integrity.validate_payment_receipt_integrity,
            schema_integrity.validate_sibling_assignment_integrity,
        ):
            valid, message = check(connection)
            if not valid:
                raise RuntimeError(message)
        current_version = 21

    # Migration: version 21 -> version 22
    # COMING_SESSION_ADMISSION_SCHEMA_V22
    if current_version < 22:
        _migrate_students_table_for_coming_session_v22(connection)
        schema_integrity.install_integrity_triggers(connection)
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)
        valid, message = schema_integrity.validate_tracking_id_trigger(
            connection, repair_missing=True
        )
        if not valid:
            raise RuntimeError(message)
        current_version = 22

    # Migration: version 22 -> version 23
    # SESSION_BOUND_OVERDUE_PROMOTION_RECOVERY_V23
    if current_version < 23:
        _install_overdue_promotion_schema_v23(connection)
        schema_integrity.install_integrity_triggers(connection)
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)
        valid, message = schema_integrity.validate_promotion_integrity(connection)
        if not valid:
            raise RuntimeError(message)
        valid, message = schema_integrity.validate_promotion_session_integrity(connection)
        if not valid:
            raise RuntimeError(message)
        current_version = 23

    connection.create_function(
        "HMS_TEST_FIXTURE_MODE", 0, lambda: 0, deterministic=True
    )
    return current_version


def _write_automatic_backup_error(
    message: str,
) -> None:
    """
    Record an automatic-backup error without interrupting the
    original school database operation.
    """
    try:
        os.makedirs(
            BACKUPS_DIR,
            exist_ok=True,
        )

        log_path = os.path.join(
            BACKUPS_DIR,
            "automatic_backup_errors.log",
        )

        timestamp = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        with open(
            log_path,
            "a",
            encoding="utf-8",
        ) as log_file:
            log_file.write(
                f"[{timestamp}] {message}\n"
            )

    except OSError:
        # Logging failure must never stop the software.
        pass


class BackupAwareCursor(sqlite3.Cursor):
    """Cursor that reports transaction/savepoint changes to its connection."""

    def execute(self, sql, parameters=()):
        connection = self.connection
        before = connection.total_changes
        result = super().execute(sql, parameters)
        connection._record_executed_statement(sql, connection.total_changes - before)
        return result

    def executemany(self, sql, seq_of_parameters):
        connection = self.connection
        before = connection.total_changes
        result = super().executemany(sql, seq_of_parameters)
        connection._record_executed_statement(sql, connection.total_changes - before)
        return result

    def executescript(self, sql_script):
        connection = self.connection
        before = connection.total_changes
        result = super().executescript(sql_script)
        # executescript may contain several statements.  It is used mainly by
        # schema/migration code, where any successful DML/DDL must be backed up.
        connection._record_executed_script(sql_script, connection.total_changes - before)
        return result


class BackupAwareConnection(sqlite3.Connection):
    """
    SQLite connection that starts automatic backups after a
    successful database-changing commit.

    Empty commits and rolled-back transactions do not create backups.
    """

    def __init__(
        self,
        *args,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.automatic_backup_enabled = True

        self._pending_committed_change_units = 0
        self._savepoint_change_snapshots = []

    def cursor(self, factory=BackupAwareCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        # sqlite3.Connection.execute() bypasses an overridden cursor() on some
        # Python builds. Route shortcut calls through BackupAwareCursor so
        # savepoint and committed-change tracking is authoritative.
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        return self.cursor().executescript(sql_script)

    @staticmethod
    def _sql_command(sql) -> str:
        text = re.sub(r"^\s*(?:--[^\n]*\n\s*)*", "", str(sql or ""))
        return re.sub(r"\s+", " ", text.strip()).upper()

    @staticmethod
    def _savepoint_name(command: str, prefix: str) -> str:
        remainder = command[len(prefix):].strip()
        if remainder.startswith("SAVEPOINT "):
            remainder = remainder[len("SAVEPOINT "):].strip()
        return remainder.split(" ", 1)[0].strip('"`[]')

    def _record_executed_statement(self, sql, total_change_delta: int) -> None:
        command = self._sql_command(sql)
        if not command:
            return

        if command.startswith("SAVEPOINT "):
            name = self._savepoint_name(command, "SAVEPOINT ")
            self._savepoint_change_snapshots.append(
                (name, self._pending_committed_change_units)
            )
            return

        if command.startswith("ROLLBACK TO "):
            name = self._savepoint_name(command, "ROLLBACK TO ")
            for index in range(len(self._savepoint_change_snapshots) - 1, -1, -1):
                saved_name, saved_units = self._savepoint_change_snapshots[index]
                if saved_name == name:
                    self._pending_committed_change_units = saved_units
                    self._savepoint_change_snapshots = self._savepoint_change_snapshots[: index + 1]
                    break
            return

        if command.startswith("RELEASE "):
            name = self._savepoint_name(command, "RELEASE ")
            for index in range(len(self._savepoint_change_snapshots) - 1, -1, -1):
                if self._savepoint_change_snapshots[index][0] == name:
                    self._savepoint_change_snapshots = self._savepoint_change_snapshots[:index]
                    break
            return

        if command in {"ROLLBACK", "ROLLBACK TRANSACTION"}:
            self._reset_pending_change_tracking()
            return

        if command in {"COMMIT", "END", "END TRANSACTION"}:
            self._queue_backup_for_committed_changes()
            return

        if command.startswith(("BEGIN", "PRAGMA")):
            return

        ddl_change = command.startswith(
            ("CREATE ", "DROP ", "ALTER ", "REINDEX ", "VACUUM")
        )
        if int(total_change_delta or 0) > 0:
            self._pending_committed_change_units += int(total_change_delta)
        elif ddl_change:
            self._pending_committed_change_units += 1

    def _record_executed_script(self, sql_script, total_change_delta: int) -> None:
        if int(total_change_delta or 0) > 0:
            self._pending_committed_change_units += int(total_change_delta)
        elif re.search(r"\b(CREATE|DROP|ALTER|REINDEX)\b", str(sql_script), re.I):
            self._pending_committed_change_units += 1

    def _reset_pending_change_tracking(self) -> None:
        self._pending_committed_change_units = 0
        self._savepoint_change_snapshots.clear()

    def _queue_backup_for_committed_changes(self) -> None:
        database_was_changed = self._pending_committed_change_units > 0
        self._reset_pending_change_tracking()
        if not database_was_changed or not self.automatic_backup_enabled:
            return
        try:
            from app.services.backup_scheduler import schedule_automatic_backup
            schedule_automatic_backup()
        except Exception as error:
            _write_automatic_backup_error(
                f"Could not queue automatic backup: {error}"
            )

    def commit(
        self,
    ) -> None:
        """
        Commit the transaction first, then queue an automatic backup.

        Rapid database commits are combined by the backup scheduler.
        Backup errors cannot cancel successfully saved school data.
        """
        super().commit()
        self._queue_backup_for_committed_changes()


    def rollback(
        self,
    ) -> None:
        """
        Roll back the transaction without creating a backup.
        """
        super().rollback()
        self._reset_pending_change_tracking()

def get_connection(
    automatic_backup: bool = True,
) -> sqlite3.Connection:
    """
    Create and return a connection to the shared school database.

    Automatic backup is enabled for normal service operations and can
    be disabled for database initialization or maintenance operations.
    """
    os.makedirs(
        os.path.dirname(
            DATABASE_PATH
        ),
        exist_ok=True,
    )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30.0,
        factory=BackupAwareConnection,
    )

    connection.automatic_backup_enabled = (
        automatic_backup
    )

    connection.row_factory = sqlite3.Row

    connection.create_function(
        "NORMALIZE_PERSON_NAME",
        1,
        normalize_person_name,
        deterministic=True,
    )
    # Fail-closed runtime hook used by integrity triggers. Production and
    # normal application connections always return 0. The isolated regression
    # harness may override it per connection only to construct legacy damage
    # fixtures that the restore validator must reject.
    connection.create_function(
        "HMS_TEST_FIXTURE_MODE",
        0,
        lambda: 0,
        deterministic=True,
    )

    connection.execute(
        "PRAGMA foreign_keys = ON;"
    )
    connection.execute(
        "PRAGMA busy_timeout = 30000;"
    )

    return connection


def _create_schema_upgrade_rollback_snapshot(
    connection: sqlite3.Connection,
) -> str:
    """Create a transactionally consistent pre-upgrade SQLite snapshot."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    snapshot_path = (
        f"{DATABASE_PATH}.pre_schema_upgrade_{os.getpid()}_{timestamp}.rollback"
    )
    snapshot_connection = sqlite3.connect(snapshot_path)
    try:
        connection.backup(snapshot_connection)
        snapshot_connection.commit()
    finally:
        snapshot_connection.close()
    return snapshot_path


def _restore_schema_upgrade_rollback_snapshot(snapshot_path: str) -> None:
    """Atomically restore the exact pre-upgrade database after any failure."""
    for suffix in ("-wal", "-shm"):
        sidecar = f"{DATABASE_PATH}{suffix}"
        try:
            os.remove(sidecar)
        except FileNotFoundError:
            pass
    os.replace(snapshot_path, DATABASE_PATH)


def _discard_schema_upgrade_rollback_snapshot(snapshot_path: str | None) -> None:
    if not snapshot_path:
        return
    try:
        os.remove(snapshot_path)
    except FileNotFoundError:
        pass


def initialize_database() -> None:
    """
    Create every required table if it does not already exist.
    This function must NEVER drop or recreate an existing table, so that
    existing school data is always preserved when the application is
    updated with new modules or fields.
    """
    connection = get_connection(
        automatic_backup=False
    )
    cursor = connection.cursor()

    installed_schema_version = (
        get_database_schema_version(
            connection
        )
    )

    if (
        installed_schema_version
        > CURRENT_SCHEMA_VERSION
    ):
        connection.close()

        raise RuntimeError(
            (
                "This school database was created by a newer "
                "version of the software.\n\n"
                f"Database schema version: "
                f"{installed_schema_version}\n"
                f"Supported schema version: "
                f"{CURRENT_SCHEMA_VERSION}"
            )
        )

    schema_upgrade_snapshot = None
    if installed_schema_version < CURRENT_SCHEMA_VERSION:
        schema_upgrade_snapshot = _create_schema_upgrade_rollback_snapshot(
            connection
        )

    # -----------------------------------------------------------------
    # Classes table (owned by Class Management)
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_level TEXT NOT NULL,
            section TEXT NOT NULL,
            class_name TEXT NOT NULL UNIQUE,
            student_capacity INTEGER NOT NULL DEFAULT 40
                CHECK (student_capacity > 0),
            is_active INTEGER NOT NULL DEFAULT 1
                CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    # A legacy classes table may predate the is_active column. Do not
    # create an index that references that migration column until the
    # column exists; schema migration v15 will add the column and index.
    if "is_active" in _table_columns(connection, "classes"):
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS
                idx_classes_active_level_section
            ON classes (
                is_active,
                class_level,
                section
            );
        """)

    # -----------------------------------------------------------------
    # Students table (owned by Student Management)
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id INTEGER PRIMARY KEY AUTOINCREMENT,
            admission_number TEXT NOT NULL UNIQUE,
            tracking_id TEXT,
            roll_number INTEGER NOT NULL,
            released_roll_number INTEGER,
            class_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            gender TEXT NOT NULL DEFAULT '',
            father_name TEXT NOT NULL,
            father_cnic TEXT,
            father_contact_number TEXT NOT NULL,
            whatsapp_number TEXT,
            current_address TEXT,
            permanent_address TEXT,
            school_leaving_certificate_submitted INTEGER NOT NULL DEFAULT 0,
            form_b_submitted INTEGER NOT NULL DEFAULT 0,
            birth_certificate_submitted INTEGER NOT NULL DEFAULT 0,
            date_of_birth TEXT,
            admission_date TEXT NOT NULL,
            admission_year INTEGER NOT NULL,
            admission_month INTEGER NOT NULL,
            admission_day INTEGER NOT NULL,
            admission_time TEXT NOT NULL,
            student_status TEXT NOT NULL DEFAULT 'Active' CHECK (student_status IN ('Active','Dropped','Archived','Graduated','Partially Graduated','Coming Session')),
            target_session_start_year INTEGER,
            coming_session_status TEXT CHECK (coming_session_status IS NULL OR coming_session_status IN ('PENDING','ACTIVATED')),
            coming_session_effective_date TEXT,
            coming_session_activated_at TEXT,
            failure_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            created_by TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            FOREIGN KEY (class_id) REFERENCES classes (id)
        );
    """)

    # -----------------------------------------------------------------
    # Admission-number audit history
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admission_number_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            old_admission_number TEXT NOT NULL,
            new_admission_number TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            changed_by TEXT,
            FOREIGN KEY (student_id) REFERENCES students (student_id)
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
            idx_admission_number_history_student_changed
        ON admission_number_history (
            student_id,
            changed_at,
            history_id
        );
    """)

    # -----------------------------------------------------------------
    # Student class history table
    # One row for every admission, supported Full Class Promotion event,
    # or manual class change. The legacy REVERSE value remains accepted
    # only so older databases stay readable; no reversal workflow exists.
    # The effective month/year controls unpaid fee recalculation.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_class_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            old_class_id INTEGER,
            new_class_id INTEGER NOT NULL,
            effective_month INTEGER NOT NULL CHECK (
                effective_month BETWEEN 1 AND 12
            ),
            effective_year INTEGER NOT NULL,
            change_type TEXT NOT NULL CHECK (
                change_type IN (
                    'ADMISSION',
                    'PROMOTION',
                    'REVERSE',
                    'MANUAL'
                )
            ),
            change_datetime TEXT NOT NULL,
            changed_by TEXT,
            FOREIGN KEY (student_id) REFERENCES students (student_id),
            FOREIGN KEY (old_class_id) REFERENCES classes (id),
            FOREIGN KEY (new_class_id) REFERENCES classes (id)
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
            idx_student_class_history_student_effective
        ON student_class_history (
            student_id,
            effective_year,
            effective_month,
            history_id
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            old_status TEXT NOT NULL,
            new_status TEXT NOT NULL,
            event_type TEXT NOT NULL DEFAULT 'STATUS_CORRECTION',
            effective_datetime TEXT NOT NULL,
            reason TEXT,
            changed_by TEXT,
            created_at TEXT NOT NULL,
            related_class_id INTEGER,
            related_event_id INTEGER,
            FOREIGN KEY(student_id) REFERENCES students(student_id),
            FOREIGN KEY(related_class_id) REFERENCES classes(id)
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_student_status_history_student_effective
        ON student_status_history(student_id, effective_datetime, id);
    """)

    # -----------------------------------------------------------------
    # Promotion batch table (owned by Promotion Management)
    # One row per Full Class Promotion operation.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promotion_batches (
            batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_class_id INTEGER NOT NULL,
            source_class_name TEXT NOT NULL,
            destination_class_id INTEGER,
            destination_class_name TEXT,
            promotion_datetime TEXT NOT NULL,
            source_session_start_year INTEGER,
            target_session_start_year INTEGER,
            effective_date TEXT,
            promotion_mode TEXT CHECK (promotion_mode IN ('NORMAL','OVERDUE')),
            total_students INTEGER NOT NULL CHECK (total_students >= 0),
            passed_students INTEGER NOT NULL CHECK (passed_students >= 0),
            failed_students INTEGER NOT NULL CHECK (failed_students >= 0),
            graduated_students INTEGER NOT NULL CHECK (graduated_students >= 0),
            created_by TEXT,
            is_reversed INTEGER NOT NULL DEFAULT 0,
            reverse_datetime TEXT,
            CHECK (target_session_start_year = source_session_start_year + 1),
            CHECK (effective_date = printf('%04d-04-01', target_session_start_year))
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promotion_cycles (
            cycle_id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            source_session_start_year INTEGER NOT NULL,
            target_session_start_year INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK (status IN ('PENDING','COMPLETED','SKIPPED_EMPTY')),
            completed_batch_id INTEGER,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT,
            FOREIGN KEY (class_id) REFERENCES classes(id),
            FOREIGN KEY (completed_batch_id) REFERENCES promotion_batches(batch_id),
            CHECK (target_session_start_year = source_session_start_year + 1),
            CHECK (
                (status='COMPLETED' AND completed_batch_id IS NOT NULL AND completed_at IS NOT NULL)
                OR (status IN ('PENDING','SKIPPED_EMPTY') AND completed_batch_id IS NULL)
            )
        );
    """)

    # -----------------------------------------------------------------
    # Promotion history table (owned by Promotion Management)
    # One row per student per promotion action (single or full class).
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promotion_history (
            promotion_id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER,
            student_id INTEGER NOT NULL,
            admission_number TEXT NOT NULL,
            student_name_snapshot TEXT NOT NULL,
            father_name_snapshot TEXT NOT NULL,
            old_class_id INTEGER,
            old_class_name TEXT,
            new_class_id INTEGER,
            new_class_name TEXT,
            old_roll_number INTEGER,
            new_roll_number INTEGER,
            promotion_result TEXT NOT NULL,
            previous_status TEXT NOT NULL,
            new_status TEXT NOT NULL,
            failure_count_before INTEGER NOT NULL CHECK (failure_count_before >= 0),
            failure_count_after INTEGER NOT NULL CHECK (failure_count_after >= 0),
            promotion_datetime TEXT NOT NULL,
            promotion_type TEXT NOT NULL,
            is_reversed INTEGER NOT NULL DEFAULT 0,
            reverse_datetime TEXT,
            FOREIGN KEY (student_id) REFERENCES students (student_id),
            FOREIGN KEY (batch_id) REFERENCES promotion_batches (batch_id)
        );
    """)

    # -----------------------------------------------------------------
    # Reverse history table (owned by Promotion Management)
    # One row per reverse action (single student or full class).
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reverse_history (
            reverse_id INTEGER PRIMARY KEY AUTOINCREMENT,
            promotion_id INTEGER,
            batch_id INTEGER,
            student_id INTEGER NOT NULL,
            admission_number TEXT NOT NULL,
            student_name_snapshot TEXT NOT NULL,
            father_name_snapshot TEXT NOT NULL,
            class_before_reverse TEXT,
            roll_number_before_reverse INTEGER,
            restored_class TEXT,
            requested_restored_roll_number INTEGER,
            actual_restored_roll_number INTEGER,
            status_before_reverse TEXT,
            restored_status TEXT,
            failure_count_before_reverse INTEGER,
            restored_failure_count INTEGER,
            original_promotion_result TEXT,
            original_promotion_datetime TEXT,
            reverse_datetime TEXT NOT NULL,
            reverse_type TEXT NOT NULL,
            reversed_by TEXT,
            FOREIGN KEY (student_id) REFERENCES students (student_id),
            FOREIGN KEY (promotion_id) REFERENCES promotion_history (promotion_id),
            FOREIGN KEY (batch_id) REFERENCES promotion_batches (batch_id)
        );
    """)

    # -----------------------------------------------------------------
    # Graduation records table (owned by Promotion Management)
    # References the same student_id; does not duplicate student data.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS graduation_records (
            graduated_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            admission_number TEXT NOT NULL,
            last_class_id INTEGER,
            last_class_name TEXT,
            last_roll_number INTEGER,
            graduation_date TEXT NOT NULL,
            completion_session_start_year INTEGER,
            graduation_effective_date TEXT,
            status TEXT NOT NULL DEFAULT 'Graduated',
            is_reversed INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (student_id) REFERENCES students (student_id)
        );
    """)

    # -----------------------------------------------------------------
    # Fee settings table (owned by Fee Management)
    # Current class-wise fee. One active row per class at any time.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fee_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            monthly_fee REAL NOT NULL CHECK (monthly_fee > 0),
            session_start_year INTEGER NOT NULL,
            effective_from_month INTEGER NOT NULL,
            effective_from_year INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            created_by TEXT,
            updated_by TEXT,
            change_reason TEXT,
            superseded_at TEXT,
            superseded_by TEXT,
            superseded_reason TEXT,
            superseded_by_record_id INTEGER,
            FOREIGN KEY (class_id) REFERENCES classes (id)
        );
    """)

    # -----------------------------------------------------------------
    # Fee history table (owned by Fee Management)
    # Old class-wise fee periods, so previous months never change.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fee_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER NOT NULL,
            monthly_fee REAL NOT NULL CHECK (monthly_fee > 0),
            session_start_year INTEGER NOT NULL,
            effective_from_month INTEGER NOT NULL,
            effective_from_year INTEGER NOT NULL,
            effective_to_month INTEGER,
            effective_to_year INTEGER,
            created_at TEXT NOT NULL,
            created_by TEXT,
            updated_at TEXT,
            updated_by TEXT,
            change_reason TEXT,
            superseded_at TEXT,
            superseded_by TEXT,
            superseded_reason TEXT,
            superseded_by_record_id INTEGER,
            FOREIGN KEY (class_id) REFERENCES classes (id)
        );
    """)

    # -----------------------------------------------------------------
    # Student fee override table (owned by Fee Management)
    # Individual student fee, overriding class fee when active.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_override (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            monthly_fee REAL CHECK (monthly_fee IS NULL OR monthly_fee >= 0),
            monthly_fee_percentage REAL CHECK (monthly_fee_percentage IS NULL OR (monthly_fee_percentage >= 0 AND monthly_fee_percentage <= 100)),
            effective_from_month INTEGER NOT NULL,
            effective_from_year INTEGER NOT NULL,
            effective_to_month INTEGER,
            effective_to_year INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            removed_effective_month INTEGER,
            removed_effective_year INTEGER,
            removed_at TEXT,
            removed_by TEXT,
            created_by TEXT,
            updated_by TEXT,
            change_reason TEXT,
            superseded_at TEXT,
            superseded_by TEXT,
            superseded_reason TEXT,
            superseded_by_record_id INTEGER,
            FOREIGN KEY (student_id) REFERENCES students (student_id)
        );
    """)

    # -----------------------------------------------------------------
    # Sibling fee setting and assignment history
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sibling_fee_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monthly_fee_percentage REAL NOT NULL
                CHECK (monthly_fee_percentage BETWEEN 0 AND 100),
            effective_from_month INTEGER NOT NULL
                CHECK (effective_from_month BETWEEN 1 AND 12),
            effective_from_year INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT,
            superseded_at TEXT,
            superseded_by TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
                CHECK (is_active IN (0, 1))
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
            idx_sibling_fee_settings_effective_version
        ON sibling_fee_settings (
            effective_from_year,
            effective_from_month,
            id
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sibling_fee_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            normalized_father_cnic TEXT,
            normalized_father_name TEXT,
            monthly_fee_percentage REAL NOT NULL
                CHECK (monthly_fee_percentage BETWEEN 0 AND 100),
            is_sibling_policy INTEGER NOT NULL DEFAULT 0
                CHECK (is_sibling_policy IN (0, 1)),
            assignment_role TEXT NOT NULL
                CHECK (assignment_role IN (
                    'FULL', 'DISCOUNTED', 'STANDARD'
                )),
            effective_from_month INTEGER NOT NULL
                CHECK (effective_from_month BETWEEN 1 AND 12),
            effective_from_year INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            superseded_at TEXT,
            superseded_by TEXT,
            is_active INTEGER NOT NULL DEFAULT 1
                CHECK (is_active IN (0, 1)),
            FOREIGN KEY (student_id)
                REFERENCES students (student_id)
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
            idx_sibling_fee_assignment_student_effective_version
        ON sibling_fee_assignments (
            student_id,
            effective_from_year,
            effective_from_month,
            id
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
            idx_sibling_fee_assignment_cnic_effective
        ON sibling_fee_assignments (
            normalized_father_cnic,
            effective_from_year,
            effective_from_month
        );
    """)

    # -----------------------------------------------------------------
    # Student fee records table (owned by Fee Management)
    # One row per student per monthly fee item.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS student_fee_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            class_id INTEGER NOT NULL,
            session_year TEXT NOT NULL,
            fee_type TEXT NOT NULL CHECK (fee_type IN ('Monthly','Admission')),
            month_name TEXT,
            month_number INTEGER,
            year INTEGER,
            amount REAL NOT NULL CHECK (amount >= 0),
            status TEXT NOT NULL DEFAULT 'Unpaid' CHECK (status IN ('Unpaid','Paid','Waived')),
            paid_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students (student_id),
            FOREIGN KEY (class_id) REFERENCES classes (id)
        );
    """)

    # -----------------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS receipt_number_sequence (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_issued_number INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        );
    """)

    # -----------------------------------------------------------------
    # Fee payments table (owned by Fee Management)
    # One row per payment transaction (a Pay click can cover many items).
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fee_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_no TEXT NOT NULL UNIQUE CHECK (
                TRIM(receipt_no) <> '' AND TRIM(receipt_no) NOT GLOB '*[^0-9]*'
            ),
            student_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL,
            total_amount REAL NOT NULL CHECK (total_amount >= 0),
            received_by TEXT,
            created_at TEXT NOT NULL,
            payment_status TEXT NOT NULL DEFAULT 'Completed' CHECK (payment_status IN ('Completed','Cancelled')),
            cancelled_at TEXT,
            cancelled_by TEXT,
            cancellation_reason TEXT,
            student_status_before TEXT,
            student_status_after TEXT,
            auto_graduated INTEGER NOT NULL DEFAULT 0,
            auto_archived INTEGER NOT NULL DEFAULT 0,
            tracking_id_snapshot TEXT,
            admission_number_snapshot TEXT,
            roll_number_snapshot INTEGER,
            student_name_snapshot TEXT,
            father_name_snapshot TEXT,
            father_cnic_snapshot TEXT,
            class_name_snapshot TEXT,
            student_status_snapshot TEXT,
            FOREIGN KEY (student_id) REFERENCES students (student_id)
        );
    """)

    # -----------------------------------------------------------------
    # Fee receipts table (owned by Fee Management)
    # Item-wise breakdown of each payment, for the receipt printout.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fee_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL,
            student_fee_record_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            amount REAL NOT NULL CHECK (amount >= 0),
            status TEXT NOT NULL CHECK (status IN ('Paid','Cancelled')),
            created_at TEXT NOT NULL,
            class_id_snapshot INTEGER,
            class_name_snapshot TEXT,
            session_year_snapshot TEXT,
            month_number_snapshot INTEGER,
            year_snapshot INTEGER,
            FOREIGN KEY (payment_id) REFERENCES fee_payments (id),
            FOREIGN KEY (student_fee_record_id) REFERENCES student_fee_records (id)
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fee_payment_session_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL,
            session_year TEXT NOT NULL,
            month_number INTEGER NOT NULL CHECK (month_number BETWEEN 1 AND 12),
            calendar_year INTEGER NOT NULL,
            history_status TEXT NOT NULL CHECK (history_status IN ('', 'Paid', 'Waived')),
            paid_amount REAL,
            paid_date TEXT,
            UNIQUE(payment_id, month_number, calendar_year),
            FOREIGN KEY (payment_id) REFERENCES fee_payments (id) ON DELETE CASCADE
        );
    """)

    # -----------------------------------------------------------------
    # Expenses table
    #
    # expense_date is stored as YYYY-MM-DD.
    # session_start_year identifies the April-to-March school session.
    # Example:
    # April 2026 to March 2027 = session_start_year 2026.
    # -----------------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            expense_date TEXT NOT NULL,
            day_number INTEGER NOT NULL,
            month_number INTEGER NOT NULL,
            calendar_year INTEGER NOT NULL,

            session_start_year INTEGER NOT NULL,
            session_year TEXT NOT NULL,

            amount REAL NOT NULL CHECK (amount >= 0),
            description TEXT NOT NULL,

            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_expenses_date
        ON expenses (
            calendar_year,
            month_number,
            day_number
        );
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_expenses_session
        ON expenses (
            session_start_year,
            month_number
        );
    """)

    # -----------------------------------------------------------------
    # Single administrator authentication table
    #
    # Exactly one administrator is permitted. Password and recovery
    # identity values are stored only as salted PBKDF2 digests.
    # -----------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_credentials (
            user_id INTEGER PRIMARY KEY
                CHECK (user_id = 1),
            username TEXT NOT NULL COLLATE NOCASE,
            password_digest TEXT NOT NULL,
            recovery_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT,
            updated_at TEXT NOT NULL,
            updated_by TEXT
        );
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
            idx_admin_credentials_username
        ON admin_credentials (
            username COLLATE NOCASE
        );
    """)

    try:
        migrated_schema_version = run_schema_migrations(
            connection=connection,
            installed_version=installed_schema_version,
        )

        if migrated_schema_version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "Database migration did not reach the required schema version.\n\n"
                f"Migration result: {migrated_schema_version}\n"
                f"Required version: {CURRENT_SCHEMA_VERSION}"
            )

        set_database_schema_version(
            connection,
            migrated_schema_version,
        )

        schema_integrity.install_integrity_triggers(connection)
        integrity_valid, integrity_message = schema_integrity.validate_current_schema(
            connection,
            repair_missing_indexes=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not integrity_valid:
            raise RuntimeError(
                "The current school database failed final schema verification.\n\n"
                + integrity_message
            )

        connection.commit()

    except Exception as migration_error:
        try:
            connection.rollback()
        finally:
            connection.close()

        if schema_upgrade_snapshot:
            try:
                _restore_schema_upgrade_rollback_snapshot(
                    schema_upgrade_snapshot
                )
                schema_upgrade_snapshot = None
            except Exception as rollback_error:
                raise RuntimeError(
                    "Database upgrade failed and the pre-upgrade database "
                    "could not be restored atomically.\n\n"
                    f"Upgrade error: {type(migration_error).__name__}: "
                    f"{migration_error}\n"
                    f"Rollback error: {type(rollback_error).__name__}: "
                    f"{rollback_error}"
                ) from migration_error
        raise

    else:
        connection.close()
        _discard_schema_upgrade_rollback_snapshot(schema_upgrade_snapshot)
