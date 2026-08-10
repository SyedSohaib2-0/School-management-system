"""Current-schema integrity verification shared by startup and restore validation."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import json
import os
import re
import sqlite3
from datetime import datetime

from app.config import (
    ALL_CLASS_LEVELS,
    SESSION_MONTH_ORDER,
    SESSION_START_CALENDAR_MONTH,
    build_class_name,
)


class SchemaIntegrityError(RuntimeError):
    """Raised when a database cannot safely satisfy the current schema."""


CRITICAL_INDEX_SPECS = {
    "idx_students_class_roll_unique": {
        "table": "students",
        "unique": True,
        "columns": ["class_id", "roll_number"],
        "collations": ["BINARY", "BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_students_class_roll_unique ON students (class_id, roll_number)",
        "duplicate_sql": """
            SELECT class_id, roll_number, GROUP_CONCAT(student_id), COUNT(*)
            FROM students
            GROUP BY class_id, roll_number
            HAVING COUNT(*) > 1
        """,
    },
    "idx_students_tracking_id_normalized_unique": {
        "table": "students",
        "unique": True,
        "columns": [None],
        "collations": ["BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_students_tracking_id_normalized_unique ON students (UPPER(TRIM(tracking_id)))",
        "duplicate_sql": """
            SELECT UPPER(TRIM(tracking_id)), GROUP_CONCAT(student_id), COUNT(*)
            FROM students
            WHERE tracking_id IS NOT NULL AND TRIM(tracking_id) <> ''
            GROUP BY UPPER(TRIM(tracking_id))
            HAVING COUNT(*) > 1
        """,
    },
    "idx_fee_settings_class_session": {
        "table": "fee_settings",
        "unique": True,
        "columns": ["class_id", "session_start_year"],
        "collations": ["BINARY", "BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_fee_settings_class_session ON fee_settings (class_id, session_start_year)",
        "duplicate_sql": """
            SELECT class_id, session_start_year, GROUP_CONCAT(id), COUNT(*)
            FROM fee_settings
            GROUP BY class_id, session_start_year
            HAVING COUNT(*) > 1
        """,
    },
    "idx_student_monthly_fee_period_unique": {
        "table": "student_fee_records",
        "unique": True,
        "columns": ["student_id", "fee_type", "month_number", "year"],
        "collations": ["BINARY", "BINARY", "BINARY", "BINARY"],
        "partial": True,
        "sql": """
            CREATE UNIQUE INDEX idx_student_monthly_fee_period_unique
            ON student_fee_records (student_id, fee_type, month_number, year)
            WHERE fee_type = 'Monthly' AND month_number IS NOT NULL AND year IS NOT NULL
        """,
        "duplicate_sql": """
            SELECT student_id, fee_type, month_number, year, GROUP_CONCAT(id), COUNT(*)
            FROM student_fee_records
            WHERE fee_type = 'Monthly' AND month_number IS NOT NULL AND year IS NOT NULL
            GROUP BY student_id, fee_type, month_number, year
            HAVING COUNT(*) > 1
        """,
    },
    "idx_fee_receipts_payment_fee_unique": {
        "table": "fee_receipts",
        "unique": True,
        "columns": ["payment_id", "student_fee_record_id"],
        "collations": ["BINARY", "BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_fee_receipts_payment_fee_unique ON fee_receipts (payment_id, student_fee_record_id)",
        "duplicate_sql": """
            SELECT payment_id, student_fee_record_id, GROUP_CONCAT(id), COUNT(*)
            FROM fee_receipts
            GROUP BY payment_id, student_fee_record_id
            HAVING COUNT(*) > 1
        """,
    },
    "idx_students_admission_normalized": {
        "table": "students",
        "unique": True,
        "columns": [None],
        "collations": ["BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_students_admission_normalized ON students (UPPER(TRIM(admission_number)))",
        "duplicate_sql": """
            SELECT UPPER(TRIM(admission_number)), GROUP_CONCAT(student_id), COUNT(*)
            FROM students GROUP BY UPPER(TRIM(admission_number)) HAVING COUNT(*) > 1
        """,
    },
    "idx_students_admission_numeric_sequence": {
        "table": "students",
        "unique": True,
        "columns": [None],
        "collations": ["BINARY"],
        "partial": True,
        "sql": """
            CREATE UNIQUE INDEX idx_students_admission_numeric_sequence
            ON students (CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER))
            WHERE LENGTH(UPPER(TRIM(admission_number))) IN (5,6)
              AND SUBSTR(UPPER(TRIM(admission_number)),1,1) IN ('H','P')
              AND SUBSTR(UPPER(TRIM(admission_number)),2) <> ''
              AND SUBSTR(UPPER(TRIM(admission_number)),2) NOT GLOB '*[^0-9]*'
              AND CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER) BETWEEN 1001 AND 99999
        """,
        "duplicate_sql": """
            SELECT CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER),
                   GROUP_CONCAT(student_id), COUNT(*)
            FROM students
            WHERE LENGTH(UPPER(TRIM(admission_number))) IN (5,6)
              AND SUBSTR(UPPER(TRIM(admission_number)),1,1) IN ('H','P')
              AND SUBSTR(UPPER(TRIM(admission_number)),2) <> ''
              AND SUBSTR(UPPER(TRIM(admission_number)),2) NOT GLOB '*[^0-9]*'
              AND CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER) BETWEEN 1001 AND 99999
            GROUP BY CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER)
            HAVING COUNT(*) > 1
        """,
    },
    "idx_classes_name_normalized": {
        "table": "classes",
        "unique": True,
        "columns": [None],
        "collations": ["BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_classes_name_normalized ON classes (UPPER(TRIM(class_name)))",
        "duplicate_sql": """
            SELECT UPPER(TRIM(class_name)), GROUP_CONCAT(id), COUNT(*)
            FROM classes GROUP BY UPPER(TRIM(class_name)) HAVING COUNT(*) > 1
        """,
    },
    "idx_classes_level_section_normalized": {
        "table": "classes",
        "unique": True,
        "columns": [None, None],
        "collations": ["BINARY", "BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_classes_level_section_normalized ON classes (UPPER(TRIM(class_level)), UPPER(TRIM(section)))",
        "duplicate_sql": """
            SELECT UPPER(TRIM(class_level)), UPPER(TRIM(section)), GROUP_CONCAT(id), COUNT(*)
            FROM classes GROUP BY UPPER(TRIM(class_level)), UPPER(TRIM(section)) HAVING COUNT(*) > 1
        """,
    },
    "idx_one_active_graduation_per_student": {
        "table": "graduation_records",
        "unique": True,
        "columns": ["student_id"],
        "collations": ["BINARY"],
        "partial": True,
        "sql": "CREATE UNIQUE INDEX idx_one_active_graduation_per_student ON graduation_records (student_id) WHERE is_reversed = 0",
        "duplicate_sql": """
            SELECT student_id, GROUP_CONCAT(graduated_record_id), COUNT(*)
            FROM graduation_records WHERE is_reversed=0
            GROUP BY student_id HAVING COUNT(*) > 1
        """,
    },    "idx_promotion_history_batch_student_unique": {
        "table": "promotion_history",
        "unique": True,
        "columns": ["batch_id", "student_id"],
        "collations": ["BINARY", "BINARY"],
        "partial": True,
        "sql": "CREATE UNIQUE INDEX idx_promotion_history_batch_student_unique ON promotion_history (batch_id, student_id) WHERE batch_id IS NOT NULL",
        "duplicate_sql": """
            SELECT batch_id, student_id, GROUP_CONCAT(promotion_id), COUNT(*)
            FROM promotion_history WHERE batch_id IS NOT NULL
            GROUP BY batch_id, student_id HAVING COUNT(*) > 1
        """,
    },
    "idx_one_admission_history_per_student": {
        "table": "student_class_history",
        "unique": True,
        "columns": ["student_id"],
        "collations": ["BINARY"],
        "partial": True,
        "sql": "CREATE UNIQUE INDEX idx_one_admission_history_per_student ON student_class_history (student_id) WHERE change_type = 'ADMISSION'",
        "duplicate_sql": """
            SELECT student_id, GROUP_CONCAT(history_id), COUNT(*)
            FROM student_class_history WHERE change_type='ADMISSION'
            GROUP BY student_id HAVING COUNT(*) > 1
        """,
    },    "idx_promotion_batch_class_source_session_unique": {
        "table": "promotion_batches",
        "unique": True,
        "columns": ["source_class_id", "source_session_start_year"],
        "collations": ["BINARY", "BINARY"],
        "partial": True,
        "sql": "CREATE UNIQUE INDEX idx_promotion_batch_class_source_session_unique ON promotion_batches(source_class_id,source_session_start_year) WHERE is_reversed=0",
        "duplicate_sql": """
            SELECT source_class_id,source_session_start_year,GROUP_CONCAT(batch_id),COUNT(*)
            FROM promotion_batches WHERE is_reversed=0
            GROUP BY source_class_id,source_session_start_year HAVING COUNT(*)>1
        """,
    },
    "idx_promotion_cycle_class_source_unique": {
        "table": "promotion_cycles",
        "unique": True,
        "columns": ["class_id", "source_session_start_year"],
        "collations": ["BINARY", "BINARY"],
        "partial": False,
        "sql": "CREATE UNIQUE INDEX idx_promotion_cycle_class_source_unique ON promotion_cycles(class_id,source_session_start_year)",
        "duplicate_sql": """
            SELECT class_id,source_session_start_year,GROUP_CONCAT(cycle_id),COUNT(*)
            FROM promotion_cycles GROUP BY class_id,source_session_start_year HAVING COUNT(*)>1
        """,
    },
    "idx_promotion_cycle_completed_batch_unique": {
        "table": "promotion_cycles",
        "unique": True,
        "columns": ["completed_batch_id"],
        "collations": ["BINARY"],
        "partial": True,
        "sql": "CREATE UNIQUE INDEX idx_promotion_cycle_completed_batch_unique ON promotion_cycles(completed_batch_id) WHERE completed_batch_id IS NOT NULL",
        "duplicate_sql": """
            SELECT completed_batch_id,GROUP_CONCAT(cycle_id),COUNT(*)
            FROM promotion_cycles WHERE completed_batch_id IS NOT NULL
            GROUP BY completed_batch_id HAVING COUNT(*)>1
        """,
    },
}

REQUIRED_FOREIGN_KEYS = {
    "students": {("class_id", "classes", "id", "NO ACTION", "NO ACTION")},
    "fee_settings": {("class_id", "classes", "id", "NO ACTION", "NO ACTION")},
    "fee_history": {("class_id", "classes", "id", "NO ACTION", "NO ACTION")},
    "student_fee_override": {("student_id", "students", "student_id", "NO ACTION", "NO ACTION")},
    "student_fee_records": {
        ("student_id", "students", "student_id", "NO ACTION", "NO ACTION"),
        ("class_id", "classes", "id", "NO ACTION", "NO ACTION"),
    },
    "fee_payments": {("student_id", "students", "student_id", "NO ACTION", "NO ACTION")},
    "fee_receipts": {
        ("payment_id", "fee_payments", "id", "NO ACTION", "NO ACTION"),
        ("student_fee_record_id", "student_fee_records", "id", "NO ACTION", "NO ACTION"),
    },
    "promotion_cycles": {
        ("class_id", "classes", "id", "NO ACTION", "NO ACTION"),
        ("completed_batch_id", "promotion_batches", "batch_id", "NO ACTION", "NO ACTION"),
    },
}

ALLOWED_VALUES = {
    ("students", "student_status"): {
        "Active", "Dropped", "Archived", "Graduated", "Partially Graduated", "Coming Session"
    },
    ("student_fee_records", "status"): {"Unpaid", "Paid", "Waived"},
    ("fee_payments", "payment_status"): {"Completed", "Cancelled"},
    ("fee_receipts", "status"): {"Paid", "Cancelled"},
    ("student_class_history", "change_type"): {
        "ADMISSION", "PROMOTION", "REVERSE", "MANUAL"
    },
    ("student_status_history", "old_status"): {
        "Active", "Dropped", "Archived", "Graduated", "Partially Graduated", "Coming Session"
    },
    ("student_status_history", "new_status"): {
        "Active", "Dropped", "Archived", "Graduated", "Partially Graduated", "Coming Session"
    },
    ("student_status_history", "event_type"): {
        "DROP", "ARCHIVE", "REACTIVATION", "GRADUATION", "STATUS_CORRECTION",
        "ADMISSION_FOR_COMING_SESSION", "COMING_SESSION_ACTIVATION",
        "COMING_SESSION_CONVERSION", "COMING_SESSION_CANCELLATION"
    },
    ("graduation_records", "status"): {"Graduated", "Partially Graduated"},
    # "R" is written by the supported failure-relocation workflow when an
    # existing failed student is moved to another section during full-class
    # promotion. Treat it as a first-class, valid audit result at startup.
    ("promotion_history", "promotion_result"): {"P", "F", "R", "GRADUATED"},
    ("promotion_history", "promotion_type"): {"Full Class", "Failure Relocation"},
    ("promotion_batches", "promotion_mode"): {"NORMAL", "OVERDUE"},
    ("promotion_cycles", "status"): {"PENDING", "COMPLETED", "SKIPPED_EMPTY"},
}



REQUIRED_SCHEMA_COLUMNS = {
    "classes": {"id", "class_level", "section", "class_name", "student_capacity"},
    "students": {
        "student_id", "admission_number", "tracking_id", "roll_number",
        "released_roll_number", "class_id", "student_name", "father_name",
        "father_cnic", "student_status", "admission_year", "admission_month",
        "admission_day", "admission_time", "admission_date",
        "target_session_start_year", "coming_session_status",
        "coming_session_effective_date", "coming_session_activated_at",
    },
    "fee_settings": {
        "id", "class_id", "monthly_fee", "session_start_year",
        "effective_from_month", "effective_from_year", "created_at",
        "updated_at", "created_by", "updated_by", "change_reason",
    },
    "fee_history": {
        "id", "class_id", "monthly_fee", "session_start_year",
        "effective_from_month", "effective_from_year", "created_at",
        "created_by", "updated_at", "updated_by", "change_reason",
    },
    "student_fee_override": {
        "id", "student_id", "monthly_fee", "monthly_fee_percentage",
        "effective_from_month", "effective_from_year", "effective_to_month",
        "effective_to_year", "is_active", "created_at", "updated_at",
        "created_by", "updated_by", "change_reason", "superseded_at",
        "superseded_by", "superseded_by_record_id",
    },
    "student_fee_records": {
        "id", "student_id", "class_id", "session_year", "fee_type",
        "month_name", "month_number", "year", "amount", "status",
        "paid_date", "created_at", "updated_at",
    },
    "fee_payments": {
        "id", "receipt_no", "student_id", "payment_date", "total_amount",
        "payment_status", "created_at",
    },
    "fee_receipts": {
        "id", "payment_id", "student_fee_record_id", "item_name",
        "amount", "status", "created_at",
    },
    "expenses": {
        "id", "expense_date", "day_number", "month_number", "calendar_year",
        "session_start_year", "session_year", "amount", "description"
    },
    "student_class_history": {
        "history_id", "student_id", "new_class_id", "effective_month",
        "effective_year", "change_type",
    },
    "student_status_history": {
        "id", "student_id", "old_status", "new_status", "event_type", "effective_datetime",
    },
    "promotion_batches": {
        "batch_id", "source_class_id", "promotion_datetime",
        "source_session_start_year", "target_session_start_year",
        "effective_date", "promotion_mode", "is_reversed",
    },
    "promotion_cycles": {
        "cycle_id", "class_id", "source_session_start_year",
        "target_session_start_year", "status", "completed_batch_id",
        "created_at", "updated_at",
    },
    "graduation_records": {
        "graduated_record_id", "student_id", "graduation_date", "status",
        "completion_session_start_year", "graduation_effective_date", "is_reversed",
    },
    "receipt_number_sequence": {"id", "last_issued_number", "updated_at"},
}

CRITICAL_NOT_NULL_COLUMNS = {
    ("students", "student_id"), ("students", "admission_number"),
    ("students", "roll_number"), ("students", "class_id"),
    ("students", "student_name"), ("students", "father_name"),
    ("students", "student_status"),
    ("fee_settings", "monthly_fee"),
    ("student_fee_records", "amount"), ("student_fee_records", "status"),
    ("fee_payments", "receipt_no"), ("fee_payments", "total_amount"),
    ("fee_payments", "payment_status"),
    ("fee_receipts", "payment_id"),
    ("fee_receipts", "student_fee_record_id"),
    ("fee_receipts", "amount"), ("fee_receipts", "status"),
    ("promotion_cycles", "source_session_start_year"),
    ("promotion_cycles", "target_session_start_year"),
    ("promotion_cycles", "status"),
}

FINANCIAL_COLUMNS = {
    "fee_settings": {"monthly_fee": False},
    "fee_history": {"monthly_fee": False},
    "student_fee_override": {
        "monthly_fee": True,
        "monthly_fee_percentage": True,
    },
    "student_fee_records": {"amount": False},
    "fee_payments": {"total_amount": False},
    "fee_receipts": {"amount": False},
    "expenses": {"amount": False},
    "sibling_fee_settings": {"monthly_fee_percentage": False},
    "sibling_fee_assignments": {"monthly_fee_percentage": False},
}


def _normalise_sql(value: str | None) -> str:
    text = str(value or "").upper()
    text = text.replace('"', '').replace('`', '').replace('[', '').replace(']', '')
    text = re.sub(r"\s+", "", text)
    return text.replace("IFNOTEXISTS", "")


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _write_report(report_directory: str | None, title: str, details) -> str | None:
    if not report_directory:
        return None
    try:
        directory = Path(report_directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = directory / f"Schema_Reconciliation_{stamp}.txt"
        body = [title, "", f"Created: {datetime.now().isoformat(sep=' ', timespec='seconds')}", ""]
        if isinstance(details, str):
            body.append(details)
        else:
            body.append(json.dumps(details, indent=2, default=str))
        path.write_text("\n".join(body), encoding="utf-8")
        return str(path)
    except OSError:
        return None


def _index_row(connection: sqlite3.Connection, table: str, name: str):
    for row in connection.execute(f'PRAGMA index_list("{table}")').fetchall():
        if str(row[1]) == name:
            return row
    return None


def _index_definition_error(connection: sqlite3.Connection, name: str, spec: dict) -> str | None:
    row = _index_row(connection, spec["table"], name)
    if row is None:
        return "missing"
    if bool(row[2]) != bool(spec["unique"]):
        return f"unique={bool(row[2])}, expected {bool(spec['unique'])}"
    if bool(row[4]) != bool(spec["partial"]):
        return f"partial={bool(row[4])}, expected {bool(spec['partial'])}"
    xinfo = [r for r in connection.execute(f'PRAGMA index_xinfo("{name}")').fetchall() if int(r[5]) == 1]
    actual_columns = [None if r[2] is None else str(r[2]) for r in xinfo]
    actual_collations = [str(r[4] or "BINARY").upper() for r in xinfo]
    if actual_columns != spec["columns"]:
        return f"columns={actual_columns}, expected {spec['columns']}"
    if actual_collations != spec["collations"]:
        return f"collations={actual_collations}, expected {spec['collations']}"
    master = connection.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    if master is None:
        return "sqlite_master definition is missing"
    if str(master[0]) != spec["table"]:
        return f"table={master[0]}, expected {spec['table']}"
    actual_sql = _normalise_sql(master[1])
    expected_sql = _normalise_sql(spec["sql"])
    if actual_sql != expected_sql:
        return f"SQL={master[1]!r}, expected {spec['sql']!r}"
    return None


def _duplicate_rows(connection: sqlite3.Connection, sql: str):
    return [tuple(row) for row in connection.execute(sql).fetchall()]


def validate_critical_indexes(
    connection: sqlite3.Connection,
    *,
    repair_missing: bool = False,
    report_directory: str | None = None,
) -> tuple[bool, str]:
    for name, spec in CRITICAL_INDEX_SPECS.items():
        error = _index_definition_error(connection, name, spec)
        if error is None:
            continue
        if error == "missing" and repair_missing:
            duplicates = _duplicate_rows(connection, spec["duplicate_sql"])
            if duplicates:
                report = _write_report(
                    report_directory,
                    f"Unsafe recreation blocked for {name}",
                    duplicates,
                )
                suffix = f" Reconciliation report: {report}" if report else ""
                return False, (
                    f"Critical index {name} is missing and cannot be recreated because "
                    f"conflicting data exists.{suffix}"
                )
            connection.execute(spec["sql"])
            error = _index_definition_error(connection, name, spec)
            if error is None:
                continue
        return False, (
            f"Critical index {name} has an invalid definition ({error}). "
            f"Expected: {spec['sql']}"
        )
    return True, "Critical index definitions are valid."


def validate_foreign_key_definitions(connection: sqlite3.Connection) -> tuple[bool, str]:
    for table, expected in REQUIRED_FOREIGN_KEYS.items():
        actual = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[5]).upper(), str(row[6]).upper())
            for row in connection.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        }
        missing = expected - actual
        if missing:
            return False, f"Table {table} is missing required foreign-key definitions: {sorted(missing)}"
    broken = connection.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        return False, "Broken foreign-key rows: " + "; ".join(str(tuple(row)) for row in broken[:20])
    return True, "Foreign-key definitions and rows are valid."


def _bad_numeric_rows(connection: sqlite3.Connection, table: str, column: str, nullable: bool):
    columns = _table_columns(connection, table)
    if column not in columns:
        return []
    conditions = [f'typeof("{column}") NOT IN (\'integer\', \'real\')', f'"{column}" < 0', f'ABS("{column}") > 1.0e308']
    if nullable:
        conditions[0] = f'("{column}" IS NOT NULL AND typeof("{column}") NOT IN (\'integer\', \'real\'))'
    else:
        conditions.append(f'"{column}" IS NULL')
    sql = f'SELECT rowid, "{column}", typeof("{column}") FROM "{table}" WHERE ' + " OR ".join(conditions) + " LIMIT 20"
    return [tuple(row) for row in connection.execute(sql).fetchall()]


def validate_financial_values(connection: sqlite3.Connection) -> tuple[bool, str]:
    for table, columns in FINANCIAL_COLUMNS.items():
        if table not in {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
            continue
        for column, nullable in columns.items():
            bad = _bad_numeric_rows(connection, table, column, nullable)
            if bad:
                return False, f"Invalid financial values in {table}.{column}: {bad}"
    for table in ("student_fee_override", "sibling_fee_settings", "sibling_fee_assignments"):
        if "monthly_fee_percentage" in _table_columns(connection, table):
            bad = connection.execute(
                f'SELECT rowid, monthly_fee_percentage FROM "{table}" '
                "WHERE monthly_fee_percentage IS NOT NULL AND "
                "(typeof(monthly_fee_percentage) NOT IN ('integer','real') OR "
                "monthly_fee_percentage < 0 OR monthly_fee_percentage > 100) LIMIT 20"
            ).fetchall()
            if bad:
                return False, f"Invalid percentage values in {table}: {[tuple(r) for r in bad]}"
    columns = _table_columns(connection, "student_fee_records")
    if {"original_amount", "discount_amount"}.issubset(columns):
        bad = connection.execute(
            "SELECT id, original_amount, discount_amount FROM student_fee_records "
            "WHERE original_amount < 0 OR discount_amount < 0 OR discount_amount > original_amount LIMIT 20"
        ).fetchall()
        if bad:
            return False, f"Invalid fee discounts: {[tuple(r) for r in bad]}"
    zero_paid = connection.execute(
        """
        SELECT records.id, records.student_id
        FROM student_fee_records AS records
        WHERE records.status='Paid' AND records.amount=0
          AND NOT EXISTS (
              SELECT 1 FROM fee_receipts AS receipts
              JOIN fee_payments AS payments ON payments.id=receipts.payment_id
              WHERE receipts.student_fee_record_id=records.id
                AND receipts.status='Paid'
                AND payments.payment_status='Completed'
          )
        LIMIT 20
        """
    ).fetchall()
    if zero_paid:
        return False, f"Zero-value Paid fee rows lack payment evidence: {[tuple(r) for r in zero_paid]}"
    return True, "Financial values are valid."


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as error:
        raise SchemaIntegrityError(f"Invalid currency value: {value!r}") from error


def validate_payment_receipt_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    invalid_receipts = connection.execute(
        "SELECT id, receipt_no FROM fee_payments "
        "WHERE receipt_no IS NULL OR TRIM(receipt_no)='' OR TRIM(receipt_no) GLOB '*[^0-9]*' LIMIT 20"
    ).fetchall()
    if invalid_receipts:
        return False, f"Receipt Numbers must contain digits only: {[tuple(r) for r in invalid_receipts]}"

    contradictory_paid_dates = connection.execute(
        """
        SELECT id,student_id,status,paid_date
        FROM student_fee_records
        WHERE (status='Paid' AND (paid_date IS NULL OR TRIM(paid_date)=''))
           OR (status IN ('Unpaid','Waived') AND paid_date IS NOT NULL AND TRIM(paid_date)<>'')
        LIMIT 20
        """
    ).fetchall()
    if contradictory_paid_dates:
        return False, (
            "Fee status and paid_date are contradictory: "
            f"{[tuple(row) for row in contradictory_paid_dates]}"
        )
    paid_date_rows = connection.execute(
        "SELECT id,paid_date FROM student_fee_records WHERE status='Paid'"
    ).fetchall()
    for row in paid_date_rows:
        try:
            datetime.fromisoformat(str(row[1]).strip())
        except ValueError:
            return False, f"Paid fee record {row[0]} has invalid paid_date {row[1]!r}."

    ownership = connection.execute(
        """
        SELECT payments.id, payments.student_id, receipts.id,
               records.id, records.student_id
        FROM fee_receipts AS receipts
        JOIN fee_payments AS payments ON payments.id=receipts.payment_id
        JOIN student_fee_records AS records ON records.id=receipts.student_fee_record_id
        WHERE payments.student_id <> records.student_id
        LIMIT 20
        """
    ).fetchall()
    if ownership:
        return False, f"Receipt items belong to another student's fee record: {[tuple(r) for r in ownership]}"

    duplicates = connection.execute(
        """
        SELECT payment_id, student_fee_record_id, GROUP_CONCAT(id), COUNT(*)
        FROM fee_receipts
        GROUP BY payment_id, student_fee_record_id
        HAVING COUNT(*) > 1
        LIMIT 20
        """
    ).fetchall()
    if duplicates:
        return False, f"Duplicate receipt items require reconciliation: {[tuple(r) for r in duplicates]}"

    # One fee obligation may have only one active payment evidence row. A
    # cancelled receipt remains valid audit history, but two Paid receipt items
    # attached to Completed payments would charge the same obligation twice.
    duplicate_active_evidence = connection.execute(
        """
        SELECT receipts.student_fee_record_id,
               GROUP_CONCAT(receipts.id),
               GROUP_CONCAT(payments.id),
               GROUP_CONCAT(payments.receipt_no),
               COUNT(*)
        FROM fee_receipts AS receipts
        JOIN fee_payments AS payments ON payments.id=receipts.payment_id
        WHERE receipts.status='Paid'
          AND payments.payment_status='Completed'
        GROUP BY receipts.student_fee_record_id
        HAVING COUNT(*)>1
        LIMIT 20
        """
    ).fetchall()
    if duplicate_active_evidence:
        return False, (
            "A fee obligation has more than one active completed-payment "
            "receipt: "
            f"{[tuple(row) for row in duplicate_active_evidence]}"
        )

    incomplete_receipt_snapshots = connection.execute(
        """
        SELECT receipts.id, receipts.payment_id, receipts.student_fee_record_id,
               receipts.class_id_snapshot, receipts.class_name_snapshot,
               receipts.session_year_snapshot, receipts.month_number_snapshot,
               receipts.year_snapshot
        FROM fee_receipts AS receipts
        WHERE receipts.class_id_snapshot IS NULL
           OR receipts.class_id_snapshot<=0
           OR COALESCE(TRIM(receipts.class_name_snapshot),'')=''
           OR COALESCE(TRIM(receipts.session_year_snapshot),'')=''
           OR receipts.month_number_snapshot IS NULL
           OR receipts.month_number_snapshot NOT BETWEEN 1 AND 12
           OR receipts.year_snapshot IS NULL
           OR receipts.year_snapshot<1900
           OR receipts.session_year_snapshot<>printf(
                '%04d-%04d',
                CASE WHEN receipts.month_number_snapshot>=4
                     THEN receipts.year_snapshot ELSE receipts.year_snapshot-1 END,
                CASE WHEN receipts.month_number_snapshot>=4
                     THEN receipts.year_snapshot+1 ELSE receipts.year_snapshot END
              )
        LIMIT 20
        """
    ).fetchall()
    if incomplete_receipt_snapshots:
        return False, (
            "Receipt snapshots are incomplete or internally inconsistent: "
            f"{[tuple(r) for r in incomplete_receipt_snapshots]}"
        )

    # Active receipt snapshots must still match the linked Paid fee row.
    # Cancelled receipt snapshots are immutable historical facts, while the
    # reopened fee row is intentionally mutable: cancellation, class changes,
    # overdue promotion, fee-policy recalculation, and a later repayment can
    # all change that row. Wall-clock timestamps (including same-second writes
    # and clock rollback) cannot reliably prove whether a cancelled snapshot
    # should equal the row's present state, so cancelled receipts are validated
    # for completeness and status consistency but are not compared with the
    # current mutable fee row.
    snapshot_mismatch = connection.execute(
        """
        SELECT receipts.id, receipts.payment_id, receipts.student_fee_record_id,
               receipts.status, receipts.created_at, records.updated_at,
               receipts.class_id_snapshot, records.class_id,
               receipts.session_year_snapshot, records.session_year,
               receipts.month_number_snapshot, records.month_number,
               receipts.year_snapshot, records.year,
               receipts.class_name_snapshot, classes.class_name
        FROM fee_receipts AS receipts
        JOIN student_fee_records AS records ON records.id=receipts.student_fee_record_id
        JOIN classes ON classes.id=records.class_id
        WHERE receipts.status='Paid'
          AND (
                receipts.class_id_snapshot<>records.class_id
             OR COALESCE(TRIM(receipts.session_year_snapshot),'')<>COALESCE(TRIM(records.session_year),'')
             OR receipts.month_number_snapshot<>records.month_number
             OR receipts.year_snapshot<>records.year
             OR COALESCE(TRIM(receipts.class_name_snapshot),'')<>TRIM(classes.class_name)
          )
        LIMIT 20
        """
    ).fetchall()
    if snapshot_mismatch:
        return False, f"Receipt snapshots contradict linked fee records: {[tuple(r) for r in snapshot_mismatch]}"

    # Bug 97: partial payments are not supported. Each receipt item must
    # equal the complete linked fee obligation at cent precision.
    receipt_amount_rows = connection.execute(
        """
        SELECT receipts.id, receipts.payment_id, receipts.student_fee_record_id,
               receipts.amount, records.amount
        FROM fee_receipts AS receipts
        JOIN student_fee_records AS records
          ON records.id=receipts.student_fee_record_id
        WHERE receipts.status='Paid'
        ORDER BY receipts.id
        """
    ).fetchall()
    for receipt in receipt_amount_rows:
        try:
            receipt_amount = _money(receipt[3])
            fee_amount = _money(receipt[4])
        except SchemaIntegrityError as error:
            return False, str(error)
        if receipt_amount != fee_amount:
            return False, (
                "Receipt-item amount does not equal the complete linked fee obligation: "
                f"receipt_item_id={receipt[0]}, payment_id={receipt[1]}, "
                f"fee_record_id={receipt[2]}, receipt_amount={receipt_amount}, "
                f"fee_amount={fee_amount}. Partial payments are not allowed."
            )

    incomplete_payment_snapshots = connection.execute(
        """
        SELECT payments.id, payments.student_id, payments.receipt_no,
               payments.tracking_id_snapshot, payments.admission_number_snapshot,
               payments.student_name_snapshot, payments.father_name_snapshot,
               payments.class_name_snapshot, payments.student_status_snapshot
        FROM fee_payments AS payments
        WHERE COALESCE(TRIM(payments.tracking_id_snapshot),'')=''
           OR COALESCE(TRIM(payments.admission_number_snapshot),'')=''
           OR COALESCE(TRIM(payments.student_name_snapshot),'')=''
           OR COALESCE(TRIM(payments.father_name_snapshot),'')=''
           OR COALESCE(TRIM(payments.class_name_snapshot),'')=''
           OR COALESCE(TRIM(payments.student_status_snapshot),'')=''
        LIMIT 20
        """
    ).fetchall()
    if incomplete_payment_snapshots:
        return False, (
            "Payment identity snapshots are incomplete: "
            f"{[tuple(r) for r in incomplete_payment_snapshots]}"
        )

    # Payment identity snapshots are historical while the student master row
    # is editable. Startup may compare them only when the master row still has
    # its original creation timestamp and the payment was definitely created
    # later. This preserves corruption detection for a never-edited student,
    # but avoids false failures after a legitimate same-second edit or after
    # Windows clock rollback makes a later edit appear chronologically earlier.
    payment_snapshot_mismatch = connection.execute(
        """
        SELECT payments.id, payments.student_id, payments.created_at,
               students.created_at, students.updated_at
        FROM fee_payments AS payments
        JOIN students ON students.student_id=payments.student_id
        WHERE COALESCE(students.updated_at, '')=COALESCE(students.created_at, '')
          AND COALESCE(students.created_at, '')<COALESCE(payments.created_at, '')
          AND (
                COALESCE(TRIM(payments.tracking_id_snapshot),'')<>COALESCE(TRIM(students.tracking_id),'')
             OR COALESCE(TRIM(payments.admission_number_snapshot),'')<>COALESCE(TRIM(students.admission_number),'')
             OR COALESCE(TRIM(payments.student_name_snapshot),'')<>COALESCE(TRIM(students.student_name),'')
             OR COALESCE(TRIM(payments.father_name_snapshot),'')<>COALESCE(TRIM(students.father_name),'')
             OR COALESCE(TRIM(payments.father_cnic_snapshot),'')<>COALESCE(TRIM(students.father_cnic),'')
          )
        LIMIT 20
        """
    ).fetchall()
    if payment_snapshot_mismatch:
        return False, f"Payment identity snapshots do not match their student records: {[tuple(r) for r in payment_snapshot_mismatch]}"

    orphan_completed = connection.execute(
        """
        SELECT payments.id, payments.receipt_no, payments.student_id
        FROM fee_payments AS payments
        WHERE payments.payment_status='Completed'
          AND NOT EXISTS (
              SELECT 1 FROM fee_receipts AS receipts
              WHERE receipts.payment_id=payments.id AND receipts.status='Paid'
          )
        LIMIT 20
        """
    ).fetchall()
    if orphan_completed:
        return False, f"Completed payments have no active fee items: {[tuple(r) for r in orphan_completed]}"

    orphan_cancelled = connection.execute(
        """
        SELECT payments.id, payments.receipt_no, payments.student_id
        FROM fee_payments AS payments
        WHERE payments.payment_status='Cancelled'
          AND NOT EXISTS (
              SELECT 1 FROM fee_receipts AS receipts
              WHERE receipts.payment_id=payments.id
          )
        LIMIT 20
        """
    ).fetchall()
    if orphan_cancelled:
        return False, (
            "Cancelled payments have no preserved receipt items: "
            f"{[tuple(row) for row in orphan_cancelled]}"
        )

    cancelled_inconsistent = connection.execute(
        """
        SELECT payments.id, payments.receipt_no
        FROM fee_payments AS payments
        WHERE payments.payment_status='Cancelled'
          AND EXISTS (
              SELECT 1
              FROM fee_receipts AS receipts
              WHERE receipts.payment_id=payments.id
                AND receipts.status<>'Cancelled'
          )
        LIMIT 20
        """
    ).fetchall()
    if cancelled_inconsistent:
        return False, (
            "Cancelled payments have active receipt items: "
            f"{[tuple(r) for r in cancelled_inconsistent]}"
        )


    # Payment cancellation is all-or-nothing. A Completed payment cannot own
    # Cancelled items, and a Cancelled payment cannot own active Paid items.
    # The latter is also checked below with a focused error message; this
    # combined check additionally rejects mixed item states under Completed.
    payment_item_status_mismatch = connection.execute(
        """
        SELECT payments.id, payments.receipt_no, payments.payment_status,
               GROUP_CONCAT(DISTINCT receipts.status)
        FROM fee_payments AS payments
        JOIN fee_receipts AS receipts ON receipts.payment_id=payments.id
        GROUP BY payments.id, payments.receipt_no, payments.payment_status
        HAVING SUM(CASE
                    WHEN payments.payment_status='Completed'
                         AND receipts.status<>'Paid' THEN 1
                    WHEN payments.payment_status='Cancelled'
                         AND receipts.status<>'Cancelled' THEN 1
                    ELSE 0
                   END)>0
        LIMIT 20
        """
    ).fetchall()
    if payment_item_status_mismatch:
        return False, (
            "Payment status and receipt-item statuses are inconsistent: "
            f"{[tuple(row) for row in payment_item_status_mismatch]}"
        )

    invalid_paid = connection.execute(
        """
        SELECT records.id, records.student_id, records.amount
        FROM student_fee_records AS records
        WHERE records.status='Paid'
          AND (
                records.amount<=0
                OR NOT EXISTS (
                    SELECT 1 FROM fee_receipts AS receipts
                    JOIN fee_payments AS payments ON payments.id=receipts.payment_id
                    WHERE receipts.student_fee_record_id=records.id
                      AND receipts.status='Paid'
                      AND payments.payment_status='Completed'
                      AND TRIM(payments.receipt_no)<>''
                      AND TRIM(payments.receipt_no) NOT GLOB '*[^0-9]*'
                )
              )
        LIMIT 20
        """
    ).fetchall()
    if invalid_paid:
        return False, f"Paid fee rows lack valid completed payment evidence: {[tuple(r) for r in invalid_paid]}"

    payments = connection.execute(
        "SELECT id, receipt_no, student_id, total_amount FROM fee_payments WHERE payment_status='Completed'"
    ).fetchall()
    for payment in payments:
        receipt_total = connection.execute(
            "SELECT COALESCE(SUM(amount),0) FROM fee_receipts WHERE payment_id=? AND status='Paid'",
            (int(payment[0]),),
        ).fetchone()[0]
        try:
            stored = _money(payment[3])
            calculated = _money(receipt_total)
        except SchemaIntegrityError as error:
            return False, str(error)
        if stored != calculated:
            difference = stored - calculated
            return False, (
                "Completed payment total does not equal active receipt-item total: "
                f"payment_id={payment[0]}, receipt_no={payment[1]}, student_id={payment[2]}, "
                f"stored={stored}, receipt_total={calculated}, difference={difference}"
            )

    # A Paid receipt is active evidence only while the linked fee row is also
    # Paid. This catches interrupted/manual partial cancellation states that
    # previously passed startup verification in the opposite direction.
    active_receipt_fee_mismatch = connection.execute(
        """
        SELECT receipts.id, receipts.payment_id, payments.receipt_no,
               receipts.student_fee_record_id, records.status, records.paid_date
        FROM fee_receipts AS receipts
        JOIN fee_payments AS payments ON payments.id=receipts.payment_id
        JOIN student_fee_records AS records
          ON records.id=receipts.student_fee_record_id
        WHERE receipts.status='Paid'
          AND payments.payment_status='Completed'
          AND (records.status<>'Paid'
               OR records.paid_date IS NULL
               OR TRIM(records.paid_date)='')
        LIMIT 20
        """
    ).fetchall()
    if active_receipt_fee_mismatch:
        return False, (
            "Active receipt items point to fee rows that are not Paid: "
            f"{[tuple(row) for row in active_receipt_fee_mismatch]}"
        )

    return True, "Payment, receipt, snapshot, cancellation, and Paid-fee evidence are valid."

def validate_allowed_values(connection: sqlite3.Connection) -> tuple[bool, str]:
    existing_tables = {str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for (table, column), allowed in ALLOWED_VALUES.items():
        if table not in existing_tables or column not in _table_columns(connection, table):
            continue
        placeholders = ",".join("?" for _ in allowed)
        rows = connection.execute(
            f'SELECT rowid, "{column}" FROM "{table}" WHERE "{column}" IS NULL OR "{column}" NOT IN ({placeholders}) LIMIT 20',
            tuple(sorted(allowed)),
        ).fetchall()
        if rows:
            return False, (
                f"Unsupported values in {table}.{column}: {[tuple(r) for r in rows]}. "
                f"Allowed values: {sorted(allowed)}"
            )
    return True, "Enum-like status values are valid."


def _parse_datetime(value, *, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise SchemaIntegrityError(f"{field} is blank.")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise SchemaIntegrityError(f"{field} contains invalid timestamp {raw!r}.") from error
    return parsed


def _local_naive_datetime(value: datetime) -> datetime:
    """Return one timestamp in local wall-clock form for safe comparison.

    Historical/imported SQLite rows can contain ISO-8601 timezone offsets while
    current HMS writes are local, offset-naive timestamps. Python refuses to
    compare aware and naive datetimes. Convert only for chronology checks; the
    original timestamp remains authoritative for its stored date/time fields.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def _session_start_for_period(year: int, month: int) -> int:
    return int(year) if int(month) >= SESSION_START_CALENDAR_MONTH else int(year) - 1


def _period_index(year: int, month: int) -> int:
    return int(year) * 12 + int(month) - 1


def _period_belongs_to_session(year: int, month: int, session_start: int) -> bool:
    return _session_start_for_period(int(year), int(month)) == int(session_start)


def validate_student_roll_invariants(connection: sqlite3.Connection) -> tuple[bool, str]:
    rows = connection.execute(
        "SELECT student_id, admission_number, student_status, roll_number, released_roll_number "
        "FROM students ORDER BY student_id"
    ).fetchall()
    for row in rows:
        status = str(row[2])
        operational = int(row[3])
        released = row[4]
        violation = None
        if status == "Active" and (operational <= 0 or released is not None):
            violation = (
                "Active student requires a positive operational roll and no "
                "released roll."
            )
        elif status == "Coming Session":
            if operational >= 0 or released is not None:
                violation = (
                    "Coming Session student requires a unique negative pending "
                    "sentinel and no released roll."
                )
        elif status == "Archived":
            if operational >= 0 or released is None or int(released) <= 0:
                violation = (
                    "Archived student requires a unique negative sentinel and "
                    "a positive released roll."
                )
        elif status == "Dropped":
            if operational > 0 and released is not None:
                violation = (
                    "Dropped student holding a positive roll cannot also have "
                    "released-roll metadata."
                )
            elif operational <= 0 and (
                operational == 0 or released is None or int(released) <= 0
            ):
                violation = (
                    "Released Dropped student requires a unique negative "
                    "sentinel and a positive released roll."
                )
        elif status in {"Graduated", "Partially Graduated"}:
            if operational >= 0 or released is None or int(released) <= 0:
                violation = "Completed student requires a negative sentinel and positive released roll."
        if violation:
            return False, (
                f"Student roll invariant failed: student_id={row[0]}, "
                f"admission_number={row[1]!r}, status={status}, roll={operational}, "
                f"released_roll={released!r}. {violation}"
            )
    return True, "Student lifecycle and roll invariants are valid."


def validate_coming_session_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    """Validate pending/activated coming-session lifecycle metadata."""
    rows = connection.execute(
        "SELECT student_id,admission_number,student_status,roll_number,"
        "released_roll_number,admission_year,admission_month,"
        "target_session_start_year,coming_session_status,"
        "coming_session_effective_date,coming_session_activated_at "
        "FROM students ORDER BY student_id"
    ).fetchall()
    for row in rows:
        status = str(row[2] or "").strip()
        target = row[7]
        coming_status = str(row[8] or "").strip().upper()
        effective = str(row[9] or "").strip()
        activated_at = str(row[10] or "").strip()
        admission_month = int(row[6])
        admission_year = int(row[5])
        if status == "Coming Session":
            if admission_month not in {1, 2, 3}:
                return False, (
                    f"Coming-session student {row[0]} has admission month "
                    f"{admission_month}; only January-March are allowed."
                )
            if target is None or int(target) != admission_year:
                return False, (
                    f"Coming-session student {row[0]} has invalid target session "
                    f"{target!r}; expected {admission_year}."
                )
            if coming_status != "PENDING" or effective or activated_at:
                return False, (
                    f"Coming-session student {row[0]} has contradictory activation metadata."
                )
            class_row = connection.execute(
                "SELECT class_level,section,is_active FROM classes WHERE id=("
                "SELECT class_id FROM students WHERE student_id=?)",
                (int(row[0]),),
            ).fetchone()
            if class_row is None or int(class_row[2] or 0) != 1:
                return False, (
                    f"Coming-session student {row[0]} is reserved in a missing or Inactive class."
                )
            class_level = str(class_row[0] or "").strip().upper()
            section = str(class_row[1] or "").strip().upper()
            if class_level.isdigit() and 4 <= int(class_level) <= 10:
                gender_row = connection.execute(
                    "SELECT UPPER(TRIM(gender)) FROM students WHERE student_id=?",
                    (int(row[0]),),
                ).fetchone()
                gender = str(gender_row[0] or "") if gender_row else ""
                valid_route = (
                    gender == "MALE" and section in {"A", "B", "C", "D", "E", "F"}
                ) or (
                    gender == "FEMALE" and section in {
                        "G", "H", "I", "J", "K", "L", "M", "N", "O",
                        "P", "Q", "R", "S", "T", "U", "V", "W", "X",
                        "Y", "Z",
                    }
                )
                if not valid_route:
                    return False, (
                        f"Coming-session student {row[0]} has an invalid Class 4+ gender route."
                    )
            for table_name in (
                "student_fee_records", "fee_payments", "promotion_history",
                "graduation_records",
            ):
                evidence = connection.execute(
                    f"SELECT 1 FROM {table_name} WHERE student_id=? LIMIT 1",
                    (int(row[0]),),
                ).fetchone()
                if evidence is not None:
                    return False, (
                        f"Pending coming-session student {row[0]} has forbidden "
                        f"dependent data in {table_name}."
                    )
        elif coming_status == "PENDING":
            return False, (
                f"Student {row[0]} is marked PENDING without Coming Session status."
            )
        elif coming_status == "ACTIVATED":
            if (
                status == "Coming Session"
                or status not in {
                    "Active", "Dropped", "Archived",
                    "Graduated", "Partially Graduated",
                }
                or target is None
                or int(target) != admission_year
                or effective != f"{int(target):04d}-04-01"
                or not activated_at
            ):
                return False, (
                    f"Activated coming-session student {row[0]} has incomplete lifecycle metadata."
                )
        elif any((target is not None, effective, activated_at)):
            return False, (
                f"Student {row[0]} has coming-session metadata without a valid status."
            )
    return True, "Coming-session lifecycle metadata is consistent."


def validate_expense_derived_fields(connection: sqlite3.Connection) -> tuple[bool, str]:
    for row in connection.execute(
        "SELECT id, expense_date, day_number, month_number, calendar_year, "
        "session_start_year, session_year FROM expenses ORDER BY id"
    ).fetchall():
        try:
            parsed = datetime.strptime(str(row[1]), "%Y-%m-%d").date()
        except ValueError:
            return False, f"Expense {row[0]} has invalid canonical expense_date {row[1]!r}."
        expected_start = _session_start_for_period(parsed.year, parsed.month)
        expected_label = f"{expected_start}-{expected_start + 1}"
        actual = (int(row[2]), int(row[3]), int(row[4]), int(row[5]), str(row[6]))
        expected = (parsed.day, parsed.month, parsed.year, expected_start, expected_label)
        if actual != expected:
            return False, f"Expense {row[0]} derived fields contradict expense_date: actual={actual}, expected={expected}."
    return True, "Expense date-derived fields are consistent."


def validate_fee_period_labels(connection: sqlite3.Connection) -> tuple[bool, str]:
    bad_months = connection.execute(
        "SELECT id, month_name, month_number, year FROM student_fee_records "
        "WHERE fee_type='Monthly' AND (month_number IS NULL OR month_number NOT BETWEEN 1 AND 12) LIMIT 20"
    ).fetchall()
    if bad_months:
        return False, f"Monthly fee records contain invalid month numbers: {[tuple(r) for r in bad_months]}"
    canonical_month_names = {
        int(month_number): str(month_name)
        for month_name, month_number in SESSION_MONTH_ORDER
    }
    for row in connection.execute(
        "SELECT id, month_name, month_number FROM student_fee_records WHERE fee_type='Monthly'"
    ).fetchall():
        expected = canonical_month_names[int(row[2])]
        if str(row[1] or "").strip() != expected:
            return False, f"Fee record {row[0]} month label {row[1]!r} conflicts with month_number {row[2]} ({expected})."
    return True, "Fee month labels match canonical month numbers."


def _truncate_datetime_to_stored_precision(parsed: datetime, raw_value: str):
    """Return a comparable value at the precision actually stored in SQLite.

    Historical databases can contain ISO dates or minute-precision timestamps,
    while current writes use seconds. Treating a date-only value as midnight
    creates a false chronology failure when ``created_at`` is later on the same
    day. This helper preserves strict ordering at the precision the record owns.
    """
    raw = str(raw_value or "").strip()
    if "T" not in raw and " " not in raw:
        return parsed.date()
    time_part = raw.split("T", 1)[1] if "T" in raw else raw.split(" ", 1)[1]
    # Remove an ISO timezone suffix only for precision detection. ``parsed``
    # itself retains whatever timezone information datetime.fromisoformat read.
    time_part = time_part.split("+", 1)[0]
    if time_part.endswith("Z"):
        time_part = time_part[:-1]
    if "." in time_part or "," in time_part:
        return parsed
    colon_count = time_part.count(":")
    if colon_count >= 2:
        return parsed.replace(microsecond=0)
    if colon_count == 1:
        return parsed.replace(second=0, microsecond=0)
    return parsed.replace(minute=0, second=0, microsecond=0)


def validate_payment_timestamps(connection: sqlite3.Connection) -> tuple[bool, str]:
    """Validate stored chronology without trusting the changeable system clock.

    A payment later than ``datetime.now()`` can be caused by Windows time being
    moved backwards after a legitimate transaction. That condition is handled
    non-fatally by the runtime clock guard; it must not make a healthy database
    impossible to open or restore. Internal timestamp contradictions remain fatal.
    """
    now = datetime.now()
    later_than_clock = []
    for row in connection.execute(
        "SELECT id, receipt_no, payment_status, payment_date, created_at, cancelled_at "
        "FROM fee_payments"
    ).fetchall():
        payment_raw = str(row[3] or "").strip()
        created_raw = str(row[4] or "").strip()
        cancelled_raw = str(row[5] or "").strip()
        try:
            payment_time = _parse_datetime(
                payment_raw, field=f"fee_payments[{row[0]}].payment_date"
            )
            created_time = _parse_datetime(
                created_raw, field=f"fee_payments[{row[0]}].created_at"
            )
            if cancelled_raw:
                _parse_datetime(
                    cancelled_raw, field=f"fee_payments[{row[0]}].cancelled_at"
                )
        except SchemaIntegrityError as error:
            return False, str(error)

        local_payment = _local_naive_datetime(payment_time)
        local_created = _local_naive_datetime(created_time)
        comparable_payment = _truncate_datetime_to_stored_precision(
            local_payment, payment_raw
        )
        comparable_created = _truncate_datetime_to_stored_precision(
            local_created, payment_raw
        )
        if comparable_created > comparable_payment:
            return False, (
                f"Payment {row[0]} creation time is later than payment time."
            )
        if local_payment > now or local_created > now:
            later_than_clock.append((int(row[0]), str(row[1])))
    if later_than_clock:
        return True, (
            "Completed payment timestamps are internally consistent; "
            f"{len(later_than_clock)} payment(s) are later than the current "
            "computer clock and will be protected by the runtime clock guard."
        )
    return True, "Completed payment timestamps are valid and internally consistent."


def validate_fee_policy_periods(connection: sqlite3.Connection) -> tuple[bool, str]:
    for table in ("fee_settings", "fee_history"):
        columns = _table_columns(connection, table)
        select_columns = ["id", "session_start_year", "effective_from_year", "effective_from_month"]
        if "effective_to_year" in columns:
            select_columns += ["effective_to_year", "effective_to_month"]
        for row in connection.execute(
            f"SELECT {', '.join(select_columns)} FROM {table}"
        ).fetchall():
            row = tuple(row)
            row_id, session_start, start_year, start_month = map(int, row[:4])
            if not 1 <= start_month <= 12 or not _period_belongs_to_session(start_year, start_month, session_start):
                return False, f"{table} row {row_id} has an effective period outside session {session_start}-{session_start + 1}."
            if len(row) > 4 and row[4] is not None:
                end_year, end_month = int(row[4]), int(row[5])
                if not 1 <= end_month <= 12 or not _period_belongs_to_session(end_year, end_month, session_start):
                    return False, f"{table} row {row_id} has an end period outside its academic session."
                if _period_index(end_year, end_month) < _period_index(start_year, start_month):
                    return False, f"{table} row {row_id} ends before it starts."

    for row in connection.execute(
        "SELECT id, effective_from_year, effective_from_month, effective_to_year, effective_to_month, "
        "removed_effective_year, removed_effective_month FROM student_fee_override"
    ).fetchall():
        row_id = int(row[0])
        start_year, start_month = int(row[1]), int(row[2])
        if not 1 <= start_month <= 12:
            return False, f"Individual fee row {row_id} has an invalid effective month."
        # Individual fee rules can intentionally span more than one academic
        # session (for example, "N Sessions" or "All Future Sessions").
        # Validate complete, chronological calendar periods without forcing
        # the end/removal into the start session. Class-level fee rows remain
        # session-bounded above.
        for label, end_year, end_month in (
            ("effective end", row[3], row[4]),
            ("removal", row[5], row[6]),
        ):
            if end_year is None and end_month is None:
                continue
            if end_year is None or end_month is None:
                return False, f"Individual fee row {row_id} has an incomplete {label} period."
            end_year, end_month = int(end_year), int(end_month)
            if not 1 <= end_month <= 12:
                return False, f"Individual fee row {row_id} has an invalid {label} month."
            if _period_index(end_year, end_month) < _period_index(start_year, start_month):
                return False, f"Individual fee row {row_id} has {label} before its start."
    return True, "Fee-policy effective periods are valid."


def validate_student_admission_fields(connection: sqlite3.Connection) -> tuple[bool, str]:
    now = datetime.now()
    later_than_clock = []
    rows = connection.execute(
        "SELECT student_id, admission_number, admission_date, admission_year, admission_month, "
        "admission_day, admission_time, date_of_birth FROM students"
    ).fetchall()
    for row in rows:
        try:
            admitted = _parse_datetime(row[2], field=f"students[{row[0]}].admission_date")
        except SchemaIntegrityError as error:
            return False, str(error)
        if _local_naive_datetime(admitted) > now:
            # The computer clock may have been moved backwards after a valid
            # admission was stored. Internal admission components remain
            # strict, but being later than the current clock is non-fatal.
            later_than_clock.append((int(row[0]), str(row[1])))
        expected = (admitted.year, admitted.month, admitted.day, admitted.strftime("%H:%M:%S"))
        actual = (int(row[3]), int(row[4]), int(row[5]), str(row[6]))
        if actual != expected:
            return False, f"Student {row[0]} admission components contradict admission_date: actual={actual}, expected={expected}."
        stored_dob = str(row[7] or "").strip()
        if stored_dob:
            try:
                dob = datetime.strptime(stored_dob, "%Y-%m-%d").date()
            except ValueError:
                return False, f"Student {row[0]} has an invalid date of birth."
            if dob >= admitted.date():
                return False, f"Student {row[0]} date of birth is not earlier than admission_date."
        # DOB was introduced by schema migration 4 -> 5. Historical students
        # can therefore legitimately have NULL/blank DOB until their profile is
        # edited. Current add/edit services still require and validate DOB, so
        # this compatibility allowance cannot create new incomplete admissions.
        history = connection.execute(
            "SELECT effective_year, effective_month FROM student_class_history "
            "WHERE student_id=? AND change_type='ADMISSION' ORDER BY history_id LIMIT 1",
            (int(row[0]),),
        ).fetchone()
        if history and (int(history[0]), int(history[1])) != (admitted.year, admitted.month):
            return False, f"Student {row[0]} admission history period conflicts with admission_date."
    if later_than_clock:
        return True, (
            "Student admission timestamps are internally consistent; "
            f"{len(later_than_clock)} admission(s) are later than the current "
            "computer clock and are treated as a clock-rollback condition."
        )
    return True, "Student admission timestamps and derived fields are consistent."


def validate_class_definitions(connection: sqlite3.Connection) -> tuple[bool, str]:
    seen_names = {}
    seen_parts = {}
    for row in connection.execute(
        "SELECT id, class_level, section, class_name, is_active FROM classes ORDER BY id"
    ).fetchall():
        class_id = int(row[0])
        level = str(row[1] or "").strip().upper()
        section = str(row[2] or "").strip().upper()
        if level not in ALL_CLASS_LEVELS:
            return False, f"Class {class_id} has unsupported class_level {row[1]!r}."
        if not section.isalpha() or not section.isascii() or not section.isupper():
            return False, f"Class {class_id} has invalid section {row[2]!r}."
        legacy_invalid_section = (
            level.isdigit()
            and 4 <= int(level) <= 10
            and len(section) != 1
        )
        if legacy_invalid_section:
            # V11 deliberately keeps legacy Class 4-10 multi-letter sections
            # reportable but non-operational. Such a row must not prevent the
            # whole school database from opening when no operational student is
            # assigned to it. New classes and routing still enforce one letter.
            operational_student = connection.execute(
                """
                SELECT student_id,student_status
                FROM students
                WHERE class_id=?
                  AND student_status IN ('Active','Coming Session','Partially Graduated')
                LIMIT 1
                """,
                (class_id,),
            ).fetchone()
            if operational_student is not None:
                return False, (
                    f"Class {class_id} violates the Class 4-10 single-letter "
                    "section rule and contains an operational student: "
                    f"student_id={operational_student[0]}, "
                    f"status={operational_student[1]!r}."
                )
        expected_name = build_class_name(level, section)
        if str(row[3] or "").strip() != expected_name:
            return False, f"Class {class_id} class_name {row[3]!r} is not canonical {expected_name!r}."
        name_key = expected_name.upper()
        part_key = (level, section)
        if name_key in seen_names or part_key in seen_parts:
            return False, f"Normalized class collision between IDs {seen_names.get(name_key) or seen_parts.get(part_key)} and {class_id}."
        seen_names[name_key] = class_id
        seen_parts[part_key] = class_id
    return True, "Class definitions and normalized uniqueness are valid."


def validate_promotion_timestamps(connection: sqlite3.Connection) -> tuple[bool, str]:
    for table, id_column in (("promotion_batches", "batch_id"), ("promotion_history", "promotion_id")):
        for row in connection.execute(
            f"SELECT {id_column}, promotion_datetime FROM {table}"
        ).fetchall():
            try:
                _parse_datetime(row[1], field=f"{table}[{row[0]}].promotion_datetime")
            except SchemaIntegrityError as error:
                return False, str(error)
    return True, "Promotion timestamps are valid."


def _intervals_overlap(a_start, a_end, b_start, b_end) -> bool:
    return a_start <= b_end and b_start <= a_end


def validate_policy_overlaps(connection: sqlite3.Connection) -> tuple[bool, str]:
    # Class fee history: active, non-superseded periods must never overlap.
    rows = connection.execute(
        """
        SELECT 'history' AS source, id, class_id, session_start_year,
               effective_from_year, effective_from_month,
               effective_to_year, effective_to_month, monthly_fee,
               created_by, created_at
        FROM fee_history
        WHERE superseded_at IS NULL
        UNION ALL
        SELECT 'settings', id, class_id, session_start_year,
               effective_from_year, effective_from_month,
               session_start_year + 1, 3, monthly_fee,
               created_by, created_at
        FROM fee_settings
        WHERE superseded_at IS NULL
        ORDER BY class_id, session_start_year, effective_from_year,
                 effective_from_month, source, id
        """
    ).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault((int(row[2]), int(row[3])), []).append(row)
    for key, values in grouped.items():
        for index, first in enumerate(values):
            first_start = _period_index(first[4], first[5])
            first_end = _period_index(first[6], first[7]) if first[6] is not None else first_start
            for second in values[index + 1:]:
                second_start = _period_index(second[4], second[5])
                second_end = _period_index(second[6], second[7]) if second[6] is not None else second_start
                if _intervals_overlap(first_start, first_end, second_start, second_end):
                    return False, (
                        f"Overlapping active class-fee history for class/session {key}: "
                        f"records {first[0]}:{first[1]} and {second[0]}:{second[1]}, "
                        f"amounts {first[8]} and {second[8]}, creators "
                        f"{first[9]!r}/{second[9]!r}, timestamps {first[10]!r}/{second[10]!r}."
                    )

    overrides = connection.execute(
        "SELECT id, student_id, effective_from_year, effective_from_month, effective_to_year, effective_to_month "
        "FROM student_fee_override WHERE is_active=1 AND superseded_at IS NULL ORDER BY student_id, id"
    ).fetchall()
    grouped = {}
    for row in overrides:
        grouped.setdefault(int(row[1]), []).append(row)
    infinite = 10**12
    for student_id, values in grouped.items():
        for index, first in enumerate(values):
            first_start = _period_index(first[2], first[3])
            first_end = _period_index(first[4], first[5]) if first[4] is not None else infinite
            for second in values[index + 1:]:
                second_start = _period_index(second[2], second[3])
                second_end = _period_index(second[4], second[5]) if second[4] is not None else infinite
                if _intervals_overlap(first_start, first_end, second_start, second_end):
                    return False, f"Student {student_id} has overlapping active individual-fee rules {first[0]} and {second[0]}."

    duplicates = connection.execute(
        "SELECT student_id, GROUP_CONCAT(graduated_record_id), COUNT(*) FROM graduation_records "
        "WHERE is_reversed=0 GROUP BY student_id HAVING COUNT(*)>1"
    ).fetchall()
    if duplicates:
        return False, f"Multiple active graduation records require reconciliation: {[tuple(r) for r in duplicates]}"
    return True, "Fee-policy periods and active graduation records do not conflict."



def validate_promotion_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    """Validate promotion facts that exist in every supported legacy schema.

    Academic-session/cycle validation is deliberately separate so legacy
    migrations can validate V12-V22 rows before the V23 metadata backfill has
    run.
    """
    bad_batches = connection.execute(
        """
        SELECT batch_id,total_students,passed_students,failed_students,graduated_students
        FROM promotion_batches
        WHERE total_students<0 OR passed_students<0 OR failed_students<0 OR graduated_students<0
           OR passed_students+failed_students<>total_students
           OR graduated_students>passed_students
        LIMIT 20
        """
    ).fetchall()
    if bad_batches:
        return False, f"Promotion batch counters are inconsistent: {[tuple(r) for r in bad_batches]}"
    bad_failures = connection.execute(
        """
        SELECT promotion_id,batch_id,student_id,failure_count_before,failure_count_after
        FROM promotion_history
        WHERE failure_count_before<0 OR failure_count_after<0
        LIMIT 20
        """
    ).fetchall()
    if bad_failures:
        return False, f"Promotion history contains negative failure counts: {[tuple(r) for r in bad_failures]}"
    duplicates = connection.execute(
        """
        SELECT batch_id,student_id,GROUP_CONCAT(promotion_id),COUNT(*)
        FROM promotion_history WHERE batch_id IS NOT NULL
        GROUP BY batch_id,student_id HAVING COUNT(*)>1 LIMIT 20
        """
    ).fetchall()
    if duplicates:
        return False, f"A student appears more than once in one promotion batch: {[tuple(r) for r in duplicates]}"
    return True, "Promotion counters, failure counts, and batch membership are valid."


def validate_promotion_session_integrity(
    connection: sqlite3.Connection,
) -> tuple[bool, str]:
    """Validate V23 session-bound promotion and graduation metadata."""
    bad_context = connection.execute(
        """
        SELECT batch_id,source_session_start_year,target_session_start_year,
               effective_date,promotion_mode
        FROM promotion_batches
        WHERE source_session_start_year IS NULL
           OR target_session_start_year<>source_session_start_year+1
           OR effective_date<>printf('%04d-04-01',target_session_start_year)
           OR promotion_mode NOT IN ('NORMAL','OVERDUE')
        LIMIT 20
        """
    ).fetchall()
    if bad_context:
        return False, f"Promotion batches have invalid academic-session context: {[tuple(r) for r in bad_context]}"

    duplicate_cycles = connection.execute(
        """
        SELECT source_class_id,source_session_start_year,GROUP_CONCAT(batch_id),COUNT(*)
        FROM promotion_batches WHERE is_reversed=0
        GROUP BY source_class_id,source_session_start_year HAVING COUNT(*)>1
        LIMIT 20
        """
    ).fetchall()
    if duplicate_cycles:
        return False, f"A class has duplicate active promotion batches for one source session: {[tuple(r) for r in duplicate_cycles]}"

    bad_cycles = connection.execute(
        """
        SELECT cycle_id,class_id,source_session_start_year,target_session_start_year,
               status,completed_batch_id,completed_at
        FROM promotion_cycles
        WHERE target_session_start_year<>source_session_start_year+1
           OR status NOT IN ('PENDING','COMPLETED','SKIPPED_EMPTY')
           OR (status='COMPLETED' AND (completed_batch_id IS NULL OR completed_at IS NULL))
           OR (status IN ('PENDING','SKIPPED_EMPTY') AND completed_batch_id IS NOT NULL)
        LIMIT 20
        """
    ).fetchall()
    if bad_cycles:
        return False, f"Promotion cycles are inconsistent: {[tuple(r) for r in bad_cycles]}"

    mismatched_cycles = connection.execute(
        """
        SELECT pc.cycle_id,pc.completed_batch_id,pb.batch_id
        FROM promotion_cycles pc
        LEFT JOIN promotion_batches pb ON pb.batch_id=pc.completed_batch_id
        WHERE pc.status='COMPLETED' AND (
              pb.batch_id IS NULL OR pb.is_reversed<>0 OR pb.source_class_id<>pc.class_id
              OR pb.source_session_start_year<>pc.source_session_start_year
              OR pb.target_session_start_year<>pc.target_session_start_year
        )
        LIMIT 20
        """
    ).fetchall()
    if mismatched_cycles:
        return False, f"Completed promotion cycles do not match their batches: {[tuple(r) for r in mismatched_cycles]}"

    bad_graduation_sessions = connection.execute(
        """
        SELECT graduated_record_id,completion_session_start_year,graduation_effective_date
        FROM graduation_records
        WHERE completion_session_start_year IS NULL
           OR graduation_effective_date<>printf('%04d-04-01',completion_session_start_year+1)
        LIMIT 20
        """
    ).fetchall()
    if bad_graduation_sessions:
        return False, f"Graduation records have invalid completion sessions: {[tuple(r) for r in bad_graduation_sessions]}"

    return True, "Promotion sessions, cycles, and graduation completion metadata are valid."


def validate_fee_record_semantics(connection: sqlite3.Connection) -> tuple[bool, str]:
    bad_types = connection.execute(
        "SELECT id,fee_type FROM student_fee_records WHERE fee_type NOT IN ('Monthly','Admission') LIMIT 20"
    ).fetchall()
    if bad_types:
        return False, f"Unsupported or noncanonical fee types: {[tuple(r) for r in bad_types]}"
    bad_monthly = connection.execute(
        """
        SELECT id,fee_type,month_number,year,session_year
        FROM student_fee_records
        WHERE fee_type='Monthly' AND (
              month_number IS NULL OR month_number NOT BETWEEN 1 AND 12
              OR year IS NULL OR year<1900
              OR session_year<>printf('%04d-%04d',
                    CASE WHEN month_number>=4 THEN year ELSE year-1 END,
                    CASE WHEN month_number>=4 THEN year+1 ELSE year END)
        )
        LIMIT 20
        """
    ).fetchall()
    if bad_monthly:
        return False, f"Monthly fee periods or academic sessions are invalid: {[tuple(r) for r in bad_monthly]}"
    return True, "Fee types, months, years, and academic sessions are canonical."


def validate_lifecycle_graduation_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    active_with_graduation = connection.execute(
        """
        SELECT students.student_id,students.admission_number,students.student_status,
               graduation_records.graduated_record_id,graduation_records.status
        FROM students JOIN graduation_records
          ON graduation_records.student_id=students.student_id AND graduation_records.is_reversed=0
        WHERE students.student_status='Active'
        LIMIT 20
        """
    ).fetchall()
    if active_with_graduation:
        return False, f"Active students cannot have active graduation records: {[tuple(r) for r in active_with_graduation]}"
    completed_without_record = connection.execute(
        """
        SELECT students.student_id,students.admission_number,students.student_status
        FROM students
        LEFT JOIN graduation_records ON graduation_records.student_id=students.student_id AND graduation_records.is_reversed=0
        WHERE students.student_status IN ('Graduated','Partially Graduated')
        GROUP BY students.student_id
        HAVING COUNT(graduation_records.graduated_record_id)<>1
        LIMIT 20
        """
    ).fetchall()
    if completed_without_record:
        return False, f"Completed students require exactly one active graduation record: {[tuple(r) for r in completed_without_record]}"
    status_mismatch = connection.execute(
        """
        SELECT students.student_id,students.student_status,graduation_records.graduated_record_id,graduation_records.status
        FROM students JOIN graduation_records
          ON graduation_records.student_id=students.student_id AND graduation_records.is_reversed=0
        WHERE students.student_status IN ('Graduated','Partially Graduated')
          AND graduation_records.status<>students.student_status
        LIMIT 20
        """
    ).fetchall()
    if status_mismatch:
        return False, f"Student and graduation statuses disagree: {[tuple(r) for r in status_mismatch]}"
    return True, "Student and graduation lifecycle states are consistent."


def validate_admission_number_sequence_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    """Validate the global H/P numeric namespace without rewriting legacy IDs."""
    conflicts = connection.execute(
        """
        SELECT CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER) AS sequence,
               GROUP_CONCAT(student_id), GROUP_CONCAT(admission_number), COUNT(*)
        FROM students
        WHERE LENGTH(UPPER(TRIM(admission_number))) IN (5,6)
          AND SUBSTR(UPPER(TRIM(admission_number)),1,1) IN ('H','P')
          AND SUBSTR(UPPER(TRIM(admission_number)),2)<>''
          AND SUBSTR(UPPER(TRIM(admission_number)),2) NOT GLOB '*[^0-9]*'
          AND CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER) BETWEEN 1001 AND 99999
        GROUP BY sequence
        HAVING COUNT(*)>1
        LIMIT 20
        """
    ).fetchall()
    if conflicts:
        return False, (
            "Admission-number numeric sequences are duplicated across H/P prefixes: "
            f"{[tuple(row) for row in conflicts]}"
        )
    return True, "Generated H/P admission-number sequences are globally unique."


def validate_admission_history_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    bad = connection.execute(
        """
        SELECT students.student_id,students.admission_number,COUNT(history.history_id) AS admission_count
        FROM students
        LEFT JOIN student_class_history AS history
          ON history.student_id=students.student_id AND history.change_type='ADMISSION'
        GROUP BY students.student_id
        HAVING COUNT(history.history_id)<>1
        LIMIT 20
        """
    ).fetchall()
    if bad:
        return False, f"Each student must have exactly one Admission history event: {[tuple(r) for r in bad]}"
    return True, "Every student has exactly one Admission history event."


def validate_sibling_assignment_integrity(connection: sqlite3.Connection) -> tuple[bool, str]:
    # Sibling assignments are effective-dated history. Older rows may preserve
    # a previous Father CNIC/name after a legitimate student edit. Compare only
    # the latest active assignment for each student with the current master row.
    bad_identity = connection.execute(
        """
        WITH latest_assignments AS (
            SELECT assignments.*
            FROM sibling_fee_assignments AS assignments
            WHERE COALESCE(assignments.is_active,1)=1
              AND assignments.id=(
                    SELECT candidate.id
                    FROM sibling_fee_assignments AS candidate
                    WHERE candidate.student_id=assignments.student_id
                      AND COALESCE(candidate.is_active,1)=1
                    ORDER BY candidate.effective_from_year DESC,
                             candidate.effective_from_month DESC,
                             candidate.id DESC
                    LIMIT 1
              )
        )
        SELECT assignments.id,assignments.student_id,
               assignments.normalized_father_cnic,students.father_cnic
        FROM latest_assignments AS assignments
        JOIN students ON students.student_id=assignments.student_id
        WHERE COALESCE(assignments.normalized_father_cnic,'')
              <>REPLACE(REPLACE(TRIM(students.father_cnic),'-',''),' ','')
        LIMIT 20
        """
    ).fetchall()
    if bad_identity:
        return False, f"Sibling assignment CNIC does not match the student record: {[tuple(r) for r in bad_identity]}"
    bad_roles = connection.execute(
        """
        SELECT assignments.id,assignments.student_id,assignments.assignment_role,
               assignments.is_sibling_policy,assignments.monthly_fee_percentage
        FROM sibling_fee_assignments AS assignments
        WHERE (assignments.assignment_role='FULL' AND (
                    assignments.is_sibling_policy<>1
                    OR assignments.monthly_fee_percentage<>100
               ))
           OR (assignments.assignment_role='STANDARD' AND (
                    assignments.is_sibling_policy<>0
                    OR assignments.monthly_fee_percentage<>100
               ))
           OR (assignments.assignment_role='DISCOUNTED'
               AND assignments.is_sibling_policy<>1)
        LIMIT 20
        """
    ).fetchall()
    if bad_roles:
        return False, f"Sibling assignment role fields are contradictory: {[tuple(r) for r in bad_roles]}"

    setting_rows = connection.execute(
        """
        SELECT id,monthly_fee_percentage,effective_from_year,effective_from_month,
               created_at,superseded_at,is_active
        FROM sibling_fee_settings
        ORDER BY effective_from_year,effective_from_month,id
        """
    ).fetchall()
    discounted_rows = connection.execute(
        """
        SELECT id,student_id,monthly_fee_percentage,effective_from_year,
               effective_from_month,created_at,superseded_at,is_active,
               assignment_role,is_sibling_policy
        FROM sibling_fee_assignments
        WHERE assignment_role='DISCOUNTED'
        ORDER BY id
        """
    ).fetchall()

    def _setting_is_relevant(setting, assignment) -> bool:
        return (int(setting[2]), int(setting[3])) <= (
            int(assignment[3]), int(assignment[4])
        )

    def _percentage(value) -> float:
        return round(float(value), 6)

    contradictory_discounted = []
    for assignment in discounted_rows:
        relevant = [
            setting for setting in setting_rows
            if _setting_is_relevant(setting, assignment)
        ]
        expected_setting = None
        if int(assignment[7] or 0) == 1:
            if relevant:
                expected_setting = max(
                    relevant,
                    key=lambda setting: (
                        int(setting[2]), int(setting[3]), int(setting[0])
                    ),
                )
        else:
            assignment_created = str(assignment[5] or "").strip()
            assignment_superseded = str(assignment[6] or "").strip()
            exact_supersession = [
                setting for setting in relevant
                if assignment_superseded
                and str(setting[5] or "").strip() == assignment_superseded
            ]
            if exact_supersession:
                expected_setting = max(
                    exact_supersession, key=lambda setting: int(setting[0])
                )
            else:
                temporal = [
                    setting for setting in relevant
                    if str(setting[4] or "").strip() <= assignment_created
                    and (
                        not str(setting[5] or "").strip()
                        or str(setting[5] or "").strip() >= assignment_created
                    )
                ]
                if temporal:
                    expected_setting = max(
                        temporal,
                        key=lambda setting: (
                            int(setting[2]), int(setting[3]),
                            str(setting[4] or ""), int(setting[0])
                        ),
                    )
                else:
                    # A backwards Windows clock can make a later assignment
                    # timestamp earlier than the setting that produced it. Use
                    # percentage evidence only when chronology is demonstrably
                    # non-monotonic; ordinary histories remain strictly mapped
                    # to one exact/temporal setting version.
                    clock_anomaly = bool(
                        assignment_superseded
                        and assignment_created
                        and assignment_superseded < assignment_created
                    ) or any(
                        (
                            str(setting[4] or "").strip() > assignment_created
                            if assignment_created else False
                        )
                        or (
                            bool(str(setting[5] or "").strip())
                            and str(setting[5] or "").strip()
                                < str(setting[4] or "").strip()
                        )
                        for setting in relevant
                    )
                    if clock_anomaly:
                        matching = [
                            setting for setting in relevant
                            if _percentage(setting[1]) == _percentage(assignment[2])
                        ]
                        if matching:
                            expected_setting = max(
                                matching,
                                key=lambda setting: (
                                    int(setting[2]), int(setting[3]), int(setting[0])
                                ),
                            )

        if (
            expected_setting is None
            or _percentage(expected_setting[1]) != _percentage(assignment[2])
        ):
            contradictory_discounted.append(
                (
                    int(assignment[0]), int(assignment[1]),
                    str(assignment[8]), int(assignment[9]), float(assignment[2])
                )
            )
            if len(contradictory_discounted) >= 20:
                break
    if contradictory_discounted:
        return False, (
            "Sibling assignment role fields are contradictory: "
            f"{contradictory_discounted}"
        )
    # Do not anchor startup integrity to the current computer month. If the
    # system clock is rolled back, selecting an older assignment while checking
    # present-day student statuses creates a false contradiction. The latest
    # stored active assignment is the only row that represents current state.
    invalid_current_policy = connection.execute(
        """
        WITH latest_assignments AS (
            SELECT assignments.*
            FROM sibling_fee_assignments AS assignments
            WHERE COALESCE(assignments.is_active,1)=1
              AND assignments.id = (
                    SELECT candidate.id
                    FROM sibling_fee_assignments AS candidate
                    WHERE candidate.student_id=assignments.student_id
                      AND COALESCE(candidate.is_active,1)=1
                    ORDER BY candidate.effective_from_year DESC,
                             candidate.effective_from_month DESC,
                             candidate.id DESC
                    LIMIT 1
              )
        )
        SELECT assignments.id,assignments.student_id,assignments.assignment_role,
               assignments.normalized_father_name,assignments.normalized_father_cnic
        FROM latest_assignments AS assignments
        JOIN students ON students.student_id=assignments.student_id
        WHERE students.student_status='Active'
          AND assignments.assignment_role IN ('FULL','DISCOUNTED')
          AND (
                SELECT COUNT(*)
                FROM students AS family
                WHERE family.student_status='Active'
                  AND NORMALIZE_PERSON_NAME(family.father_name)=COALESCE(assignments.normalized_father_name,'')
                  AND REPLACE(REPLACE(TRIM(family.father_cnic),'-',''),' ','')=COALESCE(assignments.normalized_father_cnic,'')
              ) < 2
        LIMIT 20
        """
    ).fetchall()
    if invalid_current_policy:
        return False, (
            "Current sibling-policy roles require at least two matching Active students: "
            f"{[tuple(row) for row in invalid_current_policy]}"
        )
    return True, "Sibling assignment identity, role fields, and Active-family eligibility are consistent."

TRACKING_ID_TRIGGER_SQL = """
    CREATE TRIGGER IF NOT EXISTS trg_students_assign_tracking_id
    AFTER INSERT ON students
    FOR EACH ROW
    WHEN NEW.tracking_id IS NULL OR TRIM(NEW.tracking_id) = ''
    BEGIN
        UPDATE students
        SET tracking_id = 'HMS-' || printf('%06d', NEW.student_id)
        WHERE student_id = NEW.student_id;
    END
"""


def validate_tracking_id_trigger(
    connection: sqlite3.Connection,
    *,
    repair_missing: bool = False,
) -> tuple[bool, str]:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_students_assign_tracking_id'"
    ).fetchone()
    if row is None and repair_missing:
        collisions = connection.execute(
            "SELECT UPPER(TRIM(tracking_id)), COUNT(*) FROM students "
            "WHERE tracking_id IS NOT NULL AND TRIM(tracking_id)<>'' "
            "GROUP BY UPPER(TRIM(tracking_id)) HAVING COUNT(*)>1"
        ).fetchall()
        if collisions:
            return False, "Tracking-ID trigger cannot be recreated while normalized collisions exist."
        connection.execute(TRACKING_ID_TRIGGER_SQL)
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_students_assign_tracking_id'"
        ).fetchone()
    if row is None:
        return False, "Required Tracking-ID trigger trg_students_assign_tracking_id is missing."
    if _normalise_sql(row[0]) != _normalise_sql(TRACKING_ID_TRIGGER_SQL):
        return False, "Tracking-ID trigger has an unexpected definition."
    return True, "Tracking-ID trigger definition is valid."


INTEGRITY_TRIGGER_SQL = {
    "trg_fee_settings_nonnegative_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_settings_nonnegative_insert
        BEFORE INSERT ON fee_settings
        WHEN NEW.monthly_fee IS NULL OR typeof(NEW.monthly_fee) NOT IN ('integer','real') OR NEW.monthly_fee <= 0
        BEGIN SELECT RAISE(ABORT, 'Full class fee must be greater than zero'); END
    """,
    "trg_fee_settings_nonnegative_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_settings_nonnegative_update
        BEFORE UPDATE OF monthly_fee ON fee_settings
        WHEN NEW.monthly_fee IS NULL OR typeof(NEW.monthly_fee) NOT IN ('integer','real') OR NEW.monthly_fee <= 0
        BEGIN SELECT RAISE(ABORT, 'Full class fee must be greater than zero'); END
    """,
    "trg_fee_history_nonnegative_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_history_nonnegative_insert
        BEFORE INSERT ON fee_history
        WHEN NEW.monthly_fee IS NULL OR typeof(NEW.monthly_fee) NOT IN ('integer','real') OR NEW.monthly_fee <= 0
        BEGIN SELECT RAISE(ABORT, 'Class fee history must be greater than zero'); END
    """,
    "trg_fee_history_nonnegative_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_history_nonnegative_update
        BEFORE UPDATE OF monthly_fee ON fee_history
        WHEN NEW.monthly_fee IS NULL OR typeof(NEW.monthly_fee) NOT IN ('integer','real') OR NEW.monthly_fee <= 0
        BEGIN SELECT RAISE(ABORT, 'Class fee history must be greater than zero'); END
    """,
    "trg_student_fee_override_integrity_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_student_fee_override_integrity_insert
        BEFORE INSERT ON student_fee_override
        WHEN (NEW.monthly_fee IS NOT NULL AND (typeof(NEW.monthly_fee) NOT IN ('integer','real') OR NEW.monthly_fee < 0))
          OR (NEW.monthly_fee_percentage IS NOT NULL AND (typeof(NEW.monthly_fee_percentage) NOT IN ('integer','real') OR NEW.monthly_fee_percentage < 0 OR NEW.monthly_fee_percentage > 100))
          OR NEW.is_active NOT IN (0,1)
        BEGIN SELECT RAISE(ABORT, 'Invalid individual fee rule'); END
    """,
    "trg_student_fee_override_integrity_update": """
        CREATE TRIGGER IF NOT EXISTS trg_student_fee_override_integrity_update
        BEFORE UPDATE OF monthly_fee,monthly_fee_percentage,is_active ON student_fee_override
        WHEN (NEW.monthly_fee IS NOT NULL AND (typeof(NEW.monthly_fee) NOT IN ('integer','real') OR NEW.monthly_fee < 0))
          OR (NEW.monthly_fee_percentage IS NOT NULL AND (typeof(NEW.monthly_fee_percentage) NOT IN ('integer','real') OR NEW.monthly_fee_percentage < 0 OR NEW.monthly_fee_percentage > 100))
          OR NEW.is_active NOT IN (0,1)
        BEGIN SELECT RAISE(ABORT, 'Invalid individual fee rule'); END
    """,
    "trg_student_fee_records_integrity_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_student_fee_records_integrity_insert
        BEFORE INSERT ON student_fee_records
        WHEN NEW.amount IS NULL OR typeof(NEW.amount) NOT IN ('integer','real') OR NEW.amount < 0
             OR NEW.status NOT IN ('Unpaid','Paid','Waived')
        BEGIN SELECT RAISE(ABORT, 'Invalid student fee record'); END
    """,
    "trg_student_fee_records_integrity_update": """
        CREATE TRIGGER IF NOT EXISTS trg_student_fee_records_integrity_update
        BEFORE UPDATE OF amount,status ON student_fee_records
        WHEN NEW.amount IS NULL OR typeof(NEW.amount) NOT IN ('integer','real') OR NEW.amount < 0
             OR NEW.status NOT IN ('Unpaid','Paid','Waived')
        BEGIN SELECT RAISE(ABORT, 'Invalid student fee record'); END
    """,
    "trg_fee_payments_integrity_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_payments_integrity_insert
        BEFORE INSERT ON fee_payments
        WHEN NEW.total_amount IS NULL OR typeof(NEW.total_amount) NOT IN ('integer','real') OR NEW.total_amount < 0
             OR NEW.payment_status NOT IN ('Completed','Cancelled')
        BEGIN SELECT RAISE(ABORT, 'Invalid fee payment'); END
    """,
    "trg_fee_payments_status_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_payments_status_update
        BEFORE UPDATE OF total_amount,payment_status ON fee_payments
        WHEN NEW.total_amount IS NULL OR typeof(NEW.total_amount) NOT IN ('integer','real') OR NEW.total_amount < 0
             OR NEW.payment_status NOT IN ('Completed','Cancelled')
        BEGIN SELECT RAISE(ABORT, 'Invalid fee payment'); END
    """,
    "trg_fee_receipts_integrity_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_receipts_integrity_insert
        BEFORE INSERT ON fee_receipts
        WHEN NEW.amount IS NULL OR typeof(NEW.amount) NOT IN ('integer','real') OR NEW.amount < 0
             OR NEW.status NOT IN ('Paid','Cancelled')
             OR NOT EXISTS (
                 SELECT 1 FROM fee_payments AS p
                 JOIN student_fee_records AS r ON r.id=NEW.student_fee_record_id
                 WHERE p.id=NEW.payment_id AND p.student_id=r.student_id
             )
             OR (NEW.status='Paid' AND NOT EXISTS (
                 SELECT 1 FROM fee_payments AS p
                 JOIN student_fee_records AS r ON r.id=NEW.student_fee_record_id
                 WHERE p.id=NEW.payment_id
                   AND p.student_id=r.student_id
                   AND ROUND(NEW.amount,2)=ROUND(r.amount,2)
             ))
        BEGIN SELECT RAISE(ABORT, 'Receipt item must equal the complete fee amount; partial payments are not allowed'); END
    """,
    "trg_fee_receipts_integrity_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_receipts_integrity_update
        BEFORE UPDATE OF payment_id,student_fee_record_id,amount,status ON fee_receipts
        WHEN NEW.amount IS NULL OR typeof(NEW.amount) NOT IN ('integer','real') OR NEW.amount < 0
             OR NEW.status NOT IN ('Paid','Cancelled')
             OR NOT EXISTS (
                 SELECT 1 FROM fee_payments AS p
                 JOIN student_fee_records AS r ON r.id=NEW.student_fee_record_id
                 WHERE p.id=NEW.payment_id AND p.student_id=r.student_id
             )
             OR (NEW.status='Paid' AND NOT EXISTS (
                 SELECT 1 FROM fee_payments AS p
                 JOIN student_fee_records AS r ON r.id=NEW.student_fee_record_id
                 WHERE p.id=NEW.payment_id
                   AND p.student_id=r.student_id
                   AND ROUND(NEW.amount,2)=ROUND(r.amount,2)
             ))
        BEGIN SELECT RAISE(ABORT, 'Receipt item must equal the complete fee amount; partial payments are not allowed'); END
    """,
    "trg_expenses_nonnegative_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_expenses_nonnegative_insert
        BEFORE INSERT ON expenses
        WHEN NEW.amount IS NULL OR typeof(NEW.amount) NOT IN ('integer','real') OR NEW.amount < 0
        BEGIN SELECT RAISE(ABORT, 'Invalid expense amount'); END
    """,
    "trg_expenses_nonnegative_update": """
        CREATE TRIGGER IF NOT EXISTS trg_expenses_nonnegative_update
        BEFORE UPDATE OF amount ON expenses
        WHEN NEW.amount IS NULL OR typeof(NEW.amount) NOT IN ('integer','real') OR NEW.amount < 0
        BEGIN SELECT RAISE(ABORT, 'Invalid expense amount'); END
    """,
    "trg_sibling_setting_percentage_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_sibling_setting_percentage_insert
        BEFORE INSERT ON sibling_fee_settings
        WHEN typeof(NEW.monthly_fee_percentage) NOT IN ('integer','real') OR NEW.monthly_fee_percentage < 0 OR NEW.monthly_fee_percentage > 100
        BEGIN SELECT RAISE(ABORT, 'Invalid sibling fee percentage'); END
    """,
    "trg_sibling_assignment_percentage_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_sibling_assignment_percentage_insert
        BEFORE INSERT ON sibling_fee_assignments
        WHEN typeof(NEW.monthly_fee_percentage) NOT IN ('integer','real') OR NEW.monthly_fee_percentage < 0 OR NEW.monthly_fee_percentage > 100
        BEGIN SELECT RAISE(ABORT, 'Invalid sibling assignment percentage'); END
    """,
    "trg_students_status_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_students_status_insert
        BEFORE INSERT ON students
        WHEN NEW.student_status NOT IN ('Active','Dropped','Archived','Graduated','Partially Graduated','Coming Session')
        BEGIN SELECT RAISE(ABORT, 'Invalid student status'); END
    """,
    "trg_students_status_update": """
        CREATE TRIGGER IF NOT EXISTS trg_students_status_update
        BEFORE UPDATE OF student_status ON students
        WHEN NEW.student_status NOT IN ('Active','Dropped','Archived','Graduated','Partially Graduated','Coming Session')
        BEGIN SELECT RAISE(ABORT, 'Invalid student status'); END
    """,
    "trg_students_roll_lifecycle_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_students_roll_lifecycle_insert
        BEFORE INSERT ON students
        WHEN (NEW.student_status='Active' AND
              (NEW.roll_number<=0 OR NEW.released_roll_number IS NOT NULL))
          OR (NEW.student_status='Coming Session' AND
              (NEW.roll_number>=0 OR NEW.released_roll_number IS NOT NULL))
          OR (NEW.student_status='Archived' AND
              (NEW.roll_number>=0 OR NEW.released_roll_number IS NULL OR NEW.released_roll_number<=0))
          OR (NEW.student_status='Dropped' AND (
              (NEW.roll_number>0 AND NEW.released_roll_number IS NOT NULL)
              OR (NEW.roll_number<=0 AND
                  (NEW.roll_number=0 OR NEW.released_roll_number IS NULL OR NEW.released_roll_number<=0))))
          OR (NEW.student_status IN ('Graduated','Partially Graduated')
              AND (NEW.roll_number>=0 OR NEW.released_roll_number IS NULL OR NEW.released_roll_number<=0))
        BEGIN SELECT RAISE(ABORT, 'Invalid student lifecycle roll state'); END
    """,
    "trg_students_roll_lifecycle_update": """
        CREATE TRIGGER IF NOT EXISTS trg_students_roll_lifecycle_update
        BEFORE UPDATE OF student_status,roll_number,released_roll_number ON students
        WHEN (NEW.student_status='Active' AND
              (NEW.roll_number<=0 OR NEW.released_roll_number IS NOT NULL))
          OR (NEW.student_status='Coming Session' AND
              (NEW.roll_number>=0 OR NEW.released_roll_number IS NOT NULL))
          OR (NEW.student_status='Archived' AND
              (NEW.roll_number>=0 OR NEW.released_roll_number IS NULL OR NEW.released_roll_number<=0))
          OR (NEW.student_status='Dropped' AND (
              (NEW.roll_number>0 AND NEW.released_roll_number IS NOT NULL)
              OR (NEW.roll_number<=0 AND
                  (NEW.roll_number=0 OR NEW.released_roll_number IS NULL OR NEW.released_roll_number<=0))))
          OR (NEW.student_status IN ('Graduated','Partially Graduated')
              AND (NEW.roll_number>=0 OR NEW.released_roll_number IS NULL OR NEW.released_roll_number<=0))
        BEGIN SELECT RAISE(ABORT, 'Invalid student lifecycle roll state'); END
    """,
    "trg_expenses_canonical_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_expenses_canonical_insert
        BEFORE INSERT ON expenses
        WHEN date(NEW.expense_date) IS NULL OR date(NEW.expense_date)<>NEW.expense_date
          OR CAST(strftime('%d',NEW.expense_date) AS INTEGER)<>NEW.day_number
          OR CAST(strftime('%m',NEW.expense_date) AS INTEGER)<>NEW.month_number
          OR CAST(strftime('%Y',NEW.expense_date) AS INTEGER)<>NEW.calendar_year
          OR NEW.session_start_year<>(CASE WHEN NEW.month_number>=4 THEN NEW.calendar_year ELSE NEW.calendar_year-1 END)
          OR NEW.session_year<>(printf('%04d-%04d',NEW.session_start_year,NEW.session_start_year+1))
        BEGIN SELECT RAISE(ABORT, 'Expense date-derived fields are inconsistent'); END
    """,
    "trg_expenses_canonical_update": """
        CREATE TRIGGER IF NOT EXISTS trg_expenses_canonical_update
        BEFORE UPDATE OF expense_date,day_number,month_number,calendar_year,session_start_year,session_year ON expenses
        WHEN date(NEW.expense_date) IS NULL OR date(NEW.expense_date)<>NEW.expense_date
          OR CAST(strftime('%d',NEW.expense_date) AS INTEGER)<>NEW.day_number
          OR CAST(strftime('%m',NEW.expense_date) AS INTEGER)<>NEW.month_number
          OR CAST(strftime('%Y',NEW.expense_date) AS INTEGER)<>NEW.calendar_year
          OR NEW.session_start_year<>(CASE WHEN NEW.month_number>=4 THEN NEW.calendar_year ELSE NEW.calendar_year-1 END)
          OR NEW.session_year<>(printf('%04d-%04d',NEW.session_start_year,NEW.session_start_year+1))
        BEGIN SELECT RAISE(ABORT, 'Expense date-derived fields are inconsistent'); END
    """,
    "trg_fee_record_month_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_record_month_insert
        BEFORE INSERT ON student_fee_records
        WHEN NEW.fee_type='Monthly' AND (
             NEW.month_number IS NULL OR NEW.month_number NOT BETWEEN 1 AND 12
             OR NEW.month_name<>CASE NEW.month_number
                WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March'
                WHEN 4 THEN 'April' WHEN 5 THEN 'May' WHEN 6 THEN 'June'
                WHEN 7 THEN 'July' WHEN 8 THEN 'August' WHEN 9 THEN 'September'
                WHEN 10 THEN 'October' WHEN 11 THEN 'November' WHEN 12 THEN 'December' END)
        BEGIN SELECT RAISE(ABORT, 'Monthly fee name and number are inconsistent'); END
    """,
    "trg_fee_record_month_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_record_month_update
        BEFORE UPDATE OF fee_type,month_name,month_number ON student_fee_records
        WHEN NEW.fee_type='Monthly' AND (
             NEW.month_number IS NULL OR NEW.month_number NOT BETWEEN 1 AND 12
             OR NEW.month_name<>CASE NEW.month_number
                WHEN 1 THEN 'January' WHEN 2 THEN 'February' WHEN 3 THEN 'March'
                WHEN 4 THEN 'April' WHEN 5 THEN 'May' WHEN 6 THEN 'June'
                WHEN 7 THEN 'July' WHEN 8 THEN 'August' WHEN 9 THEN 'September'
                WHEN 10 THEN 'October' WHEN 11 THEN 'November' WHEN 12 THEN 'December' END)
        BEGIN SELECT RAISE(ABORT, 'Monthly fee name and number are inconsistent'); END
    """,
    "trg_students_admission_components_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_students_admission_components_insert
        BEFORE INSERT ON students
        WHEN datetime(NEW.admission_date) IS NULL
          OR CAST(strftime('%Y',NEW.admission_date) AS INTEGER)<>NEW.admission_year
          OR CAST(strftime('%m',NEW.admission_date) AS INTEGER)<>NEW.admission_month
          OR CAST(strftime('%d',NEW.admission_date) AS INTEGER)<>NEW.admission_day
          OR strftime('%H:%M:%S',NEW.admission_date)<>NEW.admission_time
          OR date(NEW.date_of_birth) IS NULL OR date(NEW.date_of_birth)>=date(NEW.admission_date)
        BEGIN SELECT RAISE(ABORT, 'Student admission fields are inconsistent'); END
    """,
    "trg_students_admission_components_update": """
        CREATE TRIGGER IF NOT EXISTS trg_students_admission_components_update
        BEFORE UPDATE OF admission_date,admission_year,admission_month,admission_day,admission_time,date_of_birth ON students
        WHEN datetime(NEW.admission_date) IS NULL
          OR CAST(strftime('%Y',NEW.admission_date) AS INTEGER)<>NEW.admission_year
          OR CAST(strftime('%m',NEW.admission_date) AS INTEGER)<>NEW.admission_month
          OR CAST(strftime('%d',NEW.admission_date) AS INTEGER)<>NEW.admission_day
          OR strftime('%H:%M:%S',NEW.admission_date)<>NEW.admission_time
          OR date(NEW.date_of_birth) IS NULL OR date(NEW.date_of_birth)>=date(NEW.admission_date)
        BEGIN SELECT RAISE(ABORT, 'Student admission fields are inconsistent'); END
    """,
    "trg_classes_canonical_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_classes_canonical_insert
        BEFORE INSERT ON classes
        WHEN NEW.class_level<>UPPER(TRIM(NEW.class_level))
          OR NEW.section<>UPPER(TRIM(NEW.section))
          OR NEW.section='' OR NEW.section GLOB '*[^A-Z]*'
          OR (NEW.class_level IN ('4','5','6','7','8','9','10') AND LENGTH(NEW.section)<>1)
          OR NEW.class_name<>(CASE WHEN NEW.class_level IN ('PLAY GROUP','NURSERY','KG')
              THEN NEW.class_level||' '||NEW.section ELSE NEW.class_level||NEW.section END)
        BEGIN SELECT RAISE(ABORT, 'Class definition is not canonical'); END
    """,
    "trg_classes_canonical_update": """
        CREATE TRIGGER IF NOT EXISTS trg_classes_canonical_update
        BEFORE UPDATE OF class_level,section,class_name ON classes
        WHEN NEW.class_level<>UPPER(TRIM(NEW.class_level))
          OR NEW.section<>UPPER(TRIM(NEW.section))
          OR NEW.section='' OR NEW.section GLOB '*[^A-Z]*'
          OR (NEW.class_level IN ('4','5','6','7','8','9','10') AND LENGTH(NEW.section)<>1)
          OR NEW.class_name<>(CASE WHEN NEW.class_level IN ('PLAY GROUP','NURSERY','KG')
              THEN NEW.class_level||' '||NEW.section ELSE NEW.class_level||NEW.section END)
        BEGIN SELECT RAISE(ABORT, 'Class definition is not canonical'); END
    """,
    "trg_fee_settings_session_period_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_settings_session_period_insert
        BEFORE INSERT ON fee_settings
        WHEN NEW.effective_from_month NOT BETWEEN 1 AND 12
          OR (CASE WHEN NEW.effective_from_month>=4 THEN NEW.effective_from_year ELSE NEW.effective_from_year-1 END)<>NEW.session_start_year
        BEGIN SELECT RAISE(ABORT, 'Class fee effective period is outside its session'); END
    """,
    "trg_fee_history_session_period_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_history_session_period_insert
        BEFORE INSERT ON fee_history
        WHEN NEW.effective_from_month NOT BETWEEN 1 AND 12
          OR (CASE WHEN NEW.effective_from_month>=4 THEN NEW.effective_from_year ELSE NEW.effective_from_year-1 END)<>NEW.session_start_year
          OR (NEW.effective_to_month IS NOT NULL AND (
                NEW.effective_to_month NOT BETWEEN 1 AND 12
                OR (CASE WHEN NEW.effective_to_month>=4 THEN NEW.effective_to_year ELSE NEW.effective_to_year-1 END)<>NEW.session_start_year
                OR (NEW.effective_to_year*12+NEW.effective_to_month)<(NEW.effective_from_year*12+NEW.effective_from_month)))
        BEGIN SELECT RAISE(ABORT, 'Class fee history period is invalid'); END
    """,    "trg_student_admission_number_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_student_admission_number_insert
        BEFORE INSERT ON students
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.admission_number<>UPPER(TRIM(NEW.admission_number))
             OR LENGTH(NEW.admission_number) NOT IN (5,6)
             OR SUBSTR(NEW.admission_number,1,1) NOT IN ('H','P')
             OR SUBSTR(NEW.admission_number,2)=''
             OR SUBSTR(NEW.admission_number,2) GLOB '*[^0-9]*'
             OR CAST(SUBSTR(NEW.admission_number,2) AS INTEGER) NOT BETWEEN 1001 AND 99999
        )
        BEGIN SELECT RAISE(ABORT, 'Admission Number must be generated as H/P plus 1001-99999'); END
    """,
    "trg_student_admission_number_immutable": """
        CREATE TRIGGER IF NOT EXISTS trg_student_admission_number_immutable
        BEFORE UPDATE OF admission_number ON students
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND NEW.admission_number IS NOT OLD.admission_number
        BEGIN SELECT RAISE(ABORT, 'Admission Number is permanent and cannot be edited'); END
    """,
    "trg_fee_nonpaid_date_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_nonpaid_date_insert
        BEFORE INSERT ON student_fee_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND NEW.status IN ('Unpaid','Waived')
          AND NEW.paid_date IS NOT NULL AND TRIM(NEW.paid_date)<>''
        BEGIN SELECT RAISE(ABORT, 'Unpaid or Waived fee cannot have a paid date'); END
    """,
    "trg_fee_nonpaid_date_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_nonpaid_date_update
        BEFORE UPDATE OF status,paid_date ON student_fee_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND NEW.status IN ('Unpaid','Waived')
          AND NEW.paid_date IS NOT NULL AND TRIM(NEW.paid_date)<>''
        BEGIN SELECT RAISE(ABORT, 'Unpaid or Waived fee cannot have a paid date'); END
    """,
    "trg_receipt_number_numeric_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_receipt_number_numeric_insert
        BEFORE INSERT ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.receipt_no IS NULL OR TRIM(NEW.receipt_no)='' OR TRIM(NEW.receipt_no) GLOB '*[^0-9]*')
        BEGIN SELECT RAISE(ABORT, 'Receipt Number must contain digits only'); END
    """,
    "trg_receipt_number_numeric_update": """
        CREATE TRIGGER IF NOT EXISTS trg_receipt_number_numeric_update
        BEFORE UPDATE OF receipt_no ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.receipt_no IS NULL OR TRIM(NEW.receipt_no)='' OR TRIM(NEW.receipt_no) GLOB '*[^0-9]*')
        BEGIN SELECT RAISE(ABORT, 'Receipt Number must contain digits only'); END
    """,
    "trg_positive_paid_fee_evidence_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_positive_paid_fee_evidence_insert
        BEFORE INSERT ON student_fee_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.status='Paid')
        BEGIN SELECT RAISE(ABORT, 'A fee can be Paid only through Pay Fee'); END
    """,
    "trg_positive_paid_fee_evidence_update": """
        CREATE TRIGGER IF NOT EXISTS trg_positive_paid_fee_evidence_update
        BEFORE UPDATE OF status,amount,paid_date ON student_fee_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.status='Paid' AND (
             NEW.paid_date IS NULL OR TRIM(NEW.paid_date)=''
             OR NEW.amount<=0 OR NOT EXISTS (
                SELECT 1 FROM fee_receipts AS receipts
                JOIN fee_payments AS payments ON payments.id=receipts.payment_id
                WHERE receipts.student_fee_record_id=NEW.id
                  AND receipts.status='Paid'
                  AND payments.payment_status='Completed'
                  AND TRIM(payments.receipt_no)<>''
                  AND TRIM(payments.receipt_no) NOT GLOB '*[^0-9]*'
             )))
        BEGIN SELECT RAISE(ABORT, 'A fee can be Paid only through Pay Fee'); END
    """,
    "trg_completed_payment_evidence_update": """
        CREATE TRIGGER IF NOT EXISTS trg_completed_payment_evidence_update
        BEFORE UPDATE OF received_by,payment_status,total_amount ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.payment_status='Completed' AND COALESCE(NEW.received_by,'')<>'__HMS_PAYMENT_PENDING__' AND (
             NOT EXISTS (SELECT 1 FROM fee_receipts r WHERE r.payment_id=NEW.id AND r.status='Paid')
             OR EXISTS (SELECT 1 FROM fee_receipts r WHERE r.payment_id=NEW.id AND r.status<>'Paid')
             OR EXISTS (
                SELECT 1
                FROM fee_receipts own
                JOIN fee_receipts other
                  ON other.student_fee_record_id=own.student_fee_record_id
                 AND other.id<>own.id
                 AND other.status='Paid'
                JOIN fee_payments other_payment
                  ON other_payment.id=other.payment_id
                 AND other_payment.payment_status='Completed'
                WHERE own.payment_id=NEW.id AND own.status='Paid'
             )
             OR ROUND(NEW.total_amount,2)<>ROUND((SELECT COALESCE(SUM(r.amount),0) FROM fee_receipts r WHERE r.payment_id=NEW.id AND r.status='Paid'),2)
        ))
        BEGIN SELECT RAISE(ABORT, 'Completed payment requires one consistent set of linked Paid fee items'); END
    """,
    "trg_completed_payment_insert_guard": """
        CREATE TRIGGER IF NOT EXISTS trg_completed_payment_insert_guard
        BEFORE INSERT ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.payment_status='Completed' AND COALESCE(NEW.received_by,'')<>'__HMS_PAYMENT_PENDING__')
        BEGIN SELECT RAISE(ABORT, 'Completed payment must be created through Pay Fee'); END
    """,
    "trg_cancelled_payment_consistency": """
        CREATE TRIGGER IF NOT EXISTS trg_cancelled_payment_consistency
        BEFORE UPDATE OF payment_status ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.payment_status='Cancelled' AND (
             EXISTS (SELECT 1 FROM fee_receipts r WHERE r.payment_id=NEW.id AND r.status<>'Cancelled')
             OR EXISTS (SELECT 1 FROM fee_receipts r JOIN student_fee_records f ON f.id=r.student_fee_record_id WHERE r.payment_id=NEW.id AND f.status='Paid')
        ))
        BEGIN SELECT RAISE(ABORT, 'Cancelled payment has active receipt or fee rows'); END
    """,
    "trg_receipt_snapshot_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_receipt_snapshot_insert
        BEFORE INSERT ON fee_receipts
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NOT EXISTS (
          SELECT 1 FROM student_fee_records f JOIN classes c ON c.id=f.class_id
          WHERE f.id=NEW.student_fee_record_id
            AND NEW.class_id_snapshot=f.class_id
            AND TRIM(NEW.class_name_snapshot)=TRIM(c.class_name)
            AND TRIM(NEW.session_year_snapshot)=TRIM(f.session_year)
            AND NEW.month_number_snapshot=f.month_number
            AND NEW.year_snapshot=f.year
        ))
        BEGIN SELECT RAISE(ABORT, 'Receipt snapshot does not match fee record'); END
    """,
    "trg_receipt_snapshot_update": """
        CREATE TRIGGER IF NOT EXISTS trg_receipt_snapshot_update
        BEFORE UPDATE OF student_fee_record_id,class_id_snapshot,class_name_snapshot,session_year_snapshot,month_number_snapshot,year_snapshot ON fee_receipts
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NOT EXISTS (
          SELECT 1 FROM student_fee_records f JOIN classes c ON c.id=f.class_id
          WHERE f.id=NEW.student_fee_record_id
            AND NEW.class_id_snapshot=f.class_id
            AND TRIM(NEW.class_name_snapshot)=TRIM(c.class_name)
            AND TRIM(NEW.session_year_snapshot)=TRIM(f.session_year)
            AND NEW.month_number_snapshot=f.month_number
            AND NEW.year_snapshot=f.year
        ))
        BEGIN SELECT RAISE(ABORT, 'Receipt snapshot does not match fee record'); END
    """,
    "trg_payment_snapshot_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_payment_snapshot_insert
        BEFORE INSERT ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NOT EXISTS (
          SELECT 1 FROM students s WHERE s.student_id=NEW.student_id
            AND TRIM(COALESCE(NEW.tracking_id_snapshot,''))=TRIM(COALESCE(s.tracking_id,''))
            AND TRIM(COALESCE(NEW.admission_number_snapshot,''))=TRIM(COALESCE(s.admission_number,''))
            AND TRIM(COALESCE(NEW.student_name_snapshot,''))=TRIM(COALESCE(s.student_name,''))
            AND TRIM(COALESCE(NEW.father_name_snapshot,''))=TRIM(COALESCE(s.father_name,''))
            AND TRIM(COALESCE(NEW.father_cnic_snapshot,''))=TRIM(COALESCE(s.father_cnic,''))
        ))
        BEGIN SELECT RAISE(ABORT, 'Payment snapshot does not match student'); END
    """,
    "trg_payment_snapshot_update": """
        CREATE TRIGGER IF NOT EXISTS trg_payment_snapshot_update
        BEFORE UPDATE OF student_id,tracking_id_snapshot,admission_number_snapshot,student_name_snapshot,father_name_snapshot,father_cnic_snapshot ON fee_payments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NOT EXISTS (
          SELECT 1 FROM students s WHERE s.student_id=NEW.student_id
            AND TRIM(COALESCE(NEW.tracking_id_snapshot,''))=TRIM(COALESCE(s.tracking_id,''))
            AND TRIM(COALESCE(NEW.admission_number_snapshot,''))=TRIM(COALESCE(s.admission_number,''))
            AND TRIM(COALESCE(NEW.student_name_snapshot,''))=TRIM(COALESCE(s.student_name,''))
            AND TRIM(COALESCE(NEW.father_name_snapshot,''))=TRIM(COALESCE(s.father_name,''))
            AND TRIM(COALESCE(NEW.father_cnic_snapshot,''))=TRIM(COALESCE(s.father_cnic,''))
        ))
        BEGIN SELECT RAISE(ABORT, 'Payment snapshot does not match student'); END
    """,
    "trg_promotion_batch_integrity_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_batch_integrity_insert
        BEFORE INSERT ON promotion_batches
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.total_students<0 OR NEW.passed_students<0 OR NEW.failed_students<0 OR NEW.graduated_students<0
          OR NEW.passed_students+NEW.failed_students<>NEW.total_students OR NEW.graduated_students>NEW.passed_students)
        BEGIN SELECT RAISE(ABORT, 'Invalid promotion batch totals'); END
    """,
    "trg_promotion_batch_integrity_update": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_batch_integrity_update
        BEFORE UPDATE OF total_students,passed_students,failed_students,graduated_students ON promotion_batches
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.total_students<0 OR NEW.passed_students<0 OR NEW.failed_students<0 OR NEW.graduated_students<0
          OR NEW.passed_students+NEW.failed_students<>NEW.total_students OR NEW.graduated_students>NEW.passed_students)
        BEGIN SELECT RAISE(ABORT, 'Invalid promotion batch totals'); END
    """,
    "trg_promotion_session_context_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_session_context_insert
        BEFORE INSERT ON promotion_batches
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.source_session_start_year IS NULL
             OR NEW.target_session_start_year IS NULL
             OR NEW.effective_date IS NULL
             OR NEW.promotion_mode IS NULL
             OR NEW.target_session_start_year<>NEW.source_session_start_year+1
             OR NEW.effective_date<>printf('%04d-04-01',NEW.target_session_start_year)
             OR NEW.promotion_mode NOT IN ('NORMAL','OVERDUE')
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid promotion academic-session context'); END
    """,
    "trg_promotion_session_context_update": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_session_context_update
        BEFORE UPDATE OF source_session_start_year,target_session_start_year,effective_date,promotion_mode
        ON promotion_batches
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.source_session_start_year IS NULL
             OR NEW.target_session_start_year IS NULL
             OR NEW.effective_date IS NULL
             OR NEW.promotion_mode IS NULL
             OR NEW.target_session_start_year<>NEW.source_session_start_year+1
             OR NEW.effective_date<>printf('%04d-04-01',NEW.target_session_start_year)
             OR NEW.promotion_mode NOT IN ('NORMAL','OVERDUE')
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid promotion academic-session context'); END
    """,
    "trg_promotion_cycle_integrity_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_cycle_integrity_insert
        BEFORE INSERT ON promotion_cycles
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.target_session_start_year<>NEW.source_session_start_year+1
             OR NEW.status NOT IN ('PENDING','COMPLETED','SKIPPED_EMPTY')
             OR (NEW.status='COMPLETED' AND (NEW.completed_batch_id IS NULL OR NEW.completed_at IS NULL))
             OR (NEW.status IN ('PENDING','SKIPPED_EMPTY') AND NEW.completed_batch_id IS NOT NULL)
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid promotion cycle'); END
    """,
    "trg_promotion_cycle_integrity_update": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_cycle_integrity_update
        BEFORE UPDATE ON promotion_cycles
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.target_session_start_year<>NEW.source_session_start_year+1
             OR NEW.status NOT IN ('PENDING','COMPLETED','SKIPPED_EMPTY')
             OR (NEW.status='COMPLETED' AND (NEW.completed_batch_id IS NULL OR NEW.completed_at IS NULL))
             OR (NEW.status IN ('PENDING','SKIPPED_EMPTY') AND NEW.completed_batch_id IS NOT NULL)
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid promotion cycle'); END
    """,
    "trg_graduation_session_context_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_graduation_session_context_insert
        BEFORE INSERT ON graduation_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.completion_session_start_year IS NULL
             OR NEW.graduation_effective_date IS NULL
             OR NEW.graduation_effective_date<>printf('%04d-04-01',NEW.completion_session_start_year+1)
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid graduation academic-session context'); END
    """,
    "trg_graduation_session_context_update": """
        CREATE TRIGGER IF NOT EXISTS trg_graduation_session_context_update
        BEFORE UPDATE OF completion_session_start_year,graduation_effective_date
        ON graduation_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.completion_session_start_year IS NULL
             OR NEW.graduation_effective_date IS NULL
             OR NEW.graduation_effective_date<>printf('%04d-04-01',NEW.completion_session_start_year+1)
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid graduation academic-session context'); END
    """,
    "trg_fee_record_semantics_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_record_semantics_insert
        BEFORE INSERT ON student_fee_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.fee_type NOT IN ('Monthly','Admission')
             OR (NEW.fee_type='Monthly' AND (
                 NEW.month_number IS NULL OR NEW.month_number NOT BETWEEN 1 AND 12
                 OR NEW.year IS NULL OR NEW.year<1900
                 OR NEW.session_year<>printf('%04d-%04d',
                    CASE WHEN NEW.month_number>=4 THEN NEW.year ELSE NEW.year-1 END,
                    CASE WHEN NEW.month_number>=4 THEN NEW.year+1 ELSE NEW.year END)
             ))
        )
        BEGIN SELECT RAISE(ABORT, 'Fee type, month, year, or academic session is invalid'); END
    """,
    "trg_fee_record_semantics_update": """
        CREATE TRIGGER IF NOT EXISTS trg_fee_record_semantics_update
        BEFORE UPDATE OF fee_type,month_number,year,session_year ON student_fee_records
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (
             NEW.fee_type NOT IN ('Monthly','Admission')
             OR (NEW.fee_type='Monthly' AND (
                 NEW.month_number IS NULL OR NEW.month_number NOT BETWEEN 1 AND 12
                 OR NEW.year IS NULL OR NEW.year<1900
                 OR NEW.session_year<>printf('%04d-%04d',
                    CASE WHEN NEW.month_number>=4 THEN NEW.year ELSE NEW.year-1 END,
                    CASE WHEN NEW.month_number>=4 THEN NEW.year+1 ELSE NEW.year END)
             ))
        )
        BEGIN SELECT RAISE(ABORT, 'Fee type, month, year, or academic session is invalid'); END
    """,
    "trg_promotion_failure_count_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_failure_count_insert
        BEFORE INSERT ON promotion_history
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.failure_count_before<0 OR NEW.failure_count_after<0)
        BEGIN SELECT RAISE(ABORT, 'Failure count cannot be negative'); END
    """,
    "trg_promotion_failure_count_update": """
        CREATE TRIGGER IF NOT EXISTS trg_promotion_failure_count_update
        BEFORE UPDATE OF failure_count_before,failure_count_after ON promotion_history
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND (NEW.failure_count_before<0 OR NEW.failure_count_after<0)
        BEGIN SELECT RAISE(ABORT, 'Failure count cannot be negative'); END
    """,
    "trg_sibling_assignment_consistency_insert": """
        CREATE TRIGGER IF NOT EXISTS trg_sibling_assignment_consistency_insert
        BEFORE INSERT ON sibling_fee_assignments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND ((NEW.assignment_role='FULL' AND (NEW.is_sibling_policy<>1 OR NEW.monthly_fee_percentage<>100))
          OR (NEW.assignment_role='STANDARD' AND (NEW.is_sibling_policy<>0 OR NEW.monthly_fee_percentage<>100))
          OR (NEW.assignment_role='DISCOUNTED' AND (
                NEW.is_sibling_policy<>1
                OR NEW.monthly_fee_percentage<>COALESCE((
                    SELECT settings.monthly_fee_percentage
                    FROM sibling_fee_settings AS settings
                    WHERE settings.effective_from_year<NEW.effective_from_year
                       OR (settings.effective_from_year=NEW.effective_from_year
                           AND settings.effective_from_month<=NEW.effective_from_month)
                    ORDER BY settings.effective_from_year DESC,
                             settings.effective_from_month DESC,
                             settings.created_at DESC,
                             settings.id DESC
                    LIMIT 1
                ), -1)
          ))
          OR NOT EXISTS (SELECT 1 FROM students s WHERE s.student_id=NEW.student_id AND REPLACE(REPLACE(TRIM(s.father_cnic),'-',''),' ','')=COALESCE(NEW.normalized_father_cnic,''))
          OR (NEW.is_active=1 AND NEW.assignment_role IN ('FULL','DISCOUNTED') AND (
                SELECT COUNT(*) FROM students family
                WHERE family.student_status='Active'
                  AND NORMALIZE_PERSON_NAME(family.father_name)=COALESCE(NEW.normalized_father_name,'')
                  AND REPLACE(REPLACE(TRIM(family.father_cnic),'-',''),' ','')=COALESCE(NEW.normalized_father_cnic,'')
              )<2))
        BEGIN SELECT RAISE(ABORT, 'Invalid sibling assignment consistency'); END
    """,
    "trg_sibling_assignment_consistency_update": """
        CREATE TRIGGER IF NOT EXISTS trg_sibling_assignment_consistency_update
        BEFORE UPDATE OF normalized_father_name,normalized_father_cnic,monthly_fee_percentage,is_sibling_policy,assignment_role,effective_from_month,effective_from_year ON sibling_fee_assignments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND ((NEW.assignment_role='FULL' AND (NEW.is_sibling_policy<>1 OR NEW.monthly_fee_percentage<>100))
          OR (NEW.assignment_role='STANDARD' AND (NEW.is_sibling_policy<>0 OR NEW.monthly_fee_percentage<>100))
          OR (NEW.assignment_role='DISCOUNTED' AND (
                NEW.is_sibling_policy<>1
                OR NEW.monthly_fee_percentage<>COALESCE((
                    SELECT settings.monthly_fee_percentage
                    FROM sibling_fee_settings AS settings
                    WHERE settings.effective_from_year<NEW.effective_from_year
                       OR (settings.effective_from_year=NEW.effective_from_year
                           AND settings.effective_from_month<=NEW.effective_from_month)
                    ORDER BY settings.effective_from_year DESC,
                             settings.effective_from_month DESC,
                             settings.created_at DESC,
                             settings.id DESC
                    LIMIT 1
                ), -1)
          ))
          OR NOT EXISTS (SELECT 1 FROM students s WHERE s.student_id=NEW.student_id AND REPLACE(REPLACE(TRIM(s.father_cnic),'-',''),' ','')=COALESCE(NEW.normalized_father_cnic,''))
          OR (NEW.is_active=1 AND NEW.assignment_role IN ('FULL','DISCOUNTED') AND (
                SELECT COUNT(*) FROM students family
                WHERE family.student_status='Active'
                  AND NORMALIZE_PERSON_NAME(family.father_name)=COALESCE(NEW.normalized_father_name,'')
                  AND REPLACE(REPLACE(TRIM(family.father_cnic),'-',''),' ','')=COALESCE(NEW.normalized_father_cnic,'')
              )<2))
        BEGIN SELECT RAISE(ABORT, 'Invalid sibling assignment consistency'); END
    """,
    "trg_sibling_assignment_activation_consistency": """
        CREATE TRIGGER IF NOT EXISTS trg_sibling_assignment_activation_consistency
        BEFORE UPDATE OF is_active ON sibling_fee_assignments
        WHEN HMS_TEST_FIXTURE_MODE()=0 AND NEW.is_active=1 AND COALESCE(OLD.is_active,1)<>1 AND (
             NOT EXISTS (
                SELECT 1 FROM students s
                WHERE s.student_id=NEW.student_id
                  AND REPLACE(REPLACE(TRIM(s.father_cnic),'-',''),' ','')=COALESCE(NEW.normalized_father_cnic,'')
             )
             OR (NEW.assignment_role IN ('FULL','DISCOUNTED') AND (
                SELECT COUNT(*) FROM students family
                WHERE family.student_status='Active'
                  AND NORMALIZE_PERSON_NAME(family.father_name)=COALESCE(NEW.normalized_father_name,'')
                  AND REPLACE(REPLACE(TRIM(family.father_cnic),'-',''),' ','')=COALESCE(NEW.normalized_father_cnic,'')
             )<2)
        )
        BEGIN SELECT RAISE(ABORT, 'Invalid sibling assignment activation'); END
    """,
}


# These invariants remain authoritative in service, restore, and startup
# validation. Broad table triggers would prevent administrators and the
# recovery tooling from loading legacy/conflicting rows for controlled
# reconciliation, so they are intentionally not required runtime triggers.
for _optional_trigger in (
    "trg_students_roll_lifecycle_insert",
    "trg_students_roll_lifecycle_update",
    "trg_students_admission_components_insert",
    "trg_students_admission_components_update",
    "trg_classes_canonical_insert",
    "trg_classes_canonical_update",
):
    INTEGRITY_TRIGGER_SQL.pop(_optional_trigger, None)


def validate_required_schema_structure(connection: sqlite3.Connection) -> tuple[bool, str]:
    existing_tables = {
        str(row[0]) for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing_tables = set(REQUIRED_SCHEMA_COLUMNS) - existing_tables
    if missing_tables:
        return False, "Missing required current-schema tables: " + ", ".join(sorted(missing_tables))
    for table, required_columns in REQUIRED_SCHEMA_COLUMNS.items():
        info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        by_name = {str(row[1]): row for row in info}
        missing = required_columns - set(by_name)
        if missing:
            return False, f"Table {table} is missing required columns: {sorted(missing)}"
        for table_name, column_name in CRITICAL_NOT_NULL_COLUMNS:
            if table_name != table or column_name not in by_name:
                continue
            row = by_name[column_name]
            # SQLite reports INTEGER PRIMARY KEY columns as pk even when notnull=0.
            if not bool(row[3]) and not bool(row[5]):
                return False, f"Critical column {table}.{column_name} is unexpectedly nullable."
    return True, "Required tables, columns, and critical nullability are valid."


V23_TRIGGER_REQUIREMENTS = {
    "trg_promotion_session_context_insert": (
        "promotion_batches",
        {"source_session_start_year", "target_session_start_year", "effective_date", "promotion_mode"},
    ),
    "trg_promotion_session_context_update": (
        "promotion_batches",
        {"source_session_start_year", "target_session_start_year", "effective_date", "promotion_mode"},
    ),
    "trg_promotion_cycle_integrity_insert": (
        "promotion_cycles",
        {"source_session_start_year", "target_session_start_year", "status", "completed_batch_id"},
    ),
    "trg_promotion_cycle_integrity_update": (
        "promotion_cycles",
        {"source_session_start_year", "target_session_start_year", "status", "completed_batch_id"},
    ),
    "trg_graduation_session_context_insert": (
        "graduation_records",
        {"completion_session_start_year", "graduation_effective_date"},
    ),
    "trg_graduation_session_context_update": (
        "graduation_records",
        {"completion_session_start_year", "graduation_effective_date"},
    ),
}


def _trigger_requirements_available(
    connection: sqlite3.Connection, name: str
) -> bool:
    requirement = V23_TRIGGER_REQUIREMENTS.get(name)
    if requirement is None:
        return True
    table_name, required_columns = requirement
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    if table is None:
        return False
    actual_columns = {
        str(row[1])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()
    }
    return set(required_columns).issubset(actual_columns)


def install_integrity_triggers(connection: sqlite3.Connection) -> None:
    """Install constraints available at the database's migration stage.

    V23 triggers are deferred while a genuine older database still lacks their
    tables or columns. The final V23 installation pass installs every trigger,
    and current-schema validation remains strict.
    """
    for name, sql in INTEGRITY_TRIGGER_SQL.items():
        connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        if not _trigger_requirements_available(connection, name):
            continue
        connection.execute(sql)


def validate_integrity_triggers(connection: sqlite3.Connection) -> tuple[bool, str]:
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    actual = {str(row[0]): str(row[1] or "") for row in rows}
    for name, expected_sql in INTEGRITY_TRIGGER_SQL.items():
        if name not in actual:
            return False, f"Required integrity trigger {name} is missing."
        if _normalise_sql(actual[name]) != _normalise_sql(expected_sql):
            return False, f"Integrity trigger {name} has an unexpected definition."
    return True, "Required integrity-trigger definitions are valid."


def validate_current_schema(
    connection: sqlite3.Connection,
    *,
    repair_missing_indexes: bool = False,
    report_directory: str | None = None,
) -> tuple[bool, str]:
    checks = (
        lambda: validate_required_schema_structure(connection),
        lambda: validate_critical_indexes(
            connection,
            repair_missing=repair_missing_indexes,
            report_directory=report_directory,
        ),
        lambda: validate_foreign_key_definitions(connection),
        lambda: validate_integrity_triggers(connection),
        lambda: validate_tracking_id_trigger(
            connection, repair_missing=repair_missing_indexes
        ),
        lambda: validate_financial_values(connection),
        lambda: validate_payment_receipt_integrity(connection),
        lambda: validate_allowed_values(connection),
        lambda: validate_student_roll_invariants(connection),
        lambda: validate_coming_session_integrity(connection),
        lambda: validate_expense_derived_fields(connection),
        lambda: validate_fee_period_labels(connection),
        lambda: validate_payment_timestamps(connection),
        lambda: validate_fee_policy_periods(connection),
        lambda: validate_student_admission_fields(connection),
        lambda: validate_admission_number_sequence_integrity(connection),
        lambda: validate_class_definitions(connection),
        lambda: validate_promotion_timestamps(connection),
        lambda: validate_policy_overlaps(connection),
        lambda: validate_promotion_integrity(connection),
        lambda: validate_promotion_session_integrity(connection),
        lambda: validate_fee_record_semantics(connection),
        lambda: validate_lifecycle_graduation_integrity(connection),
        lambda: validate_admission_history_integrity(connection),
        lambda: validate_sibling_assignment_integrity(connection),
    )
    for check in checks:
        valid, message = check()
        if not valid:
            return False, message
    return True, "The current school database schema and financial integrity are valid."
