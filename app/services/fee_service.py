"""
Fee service module.
Contains all validation rules and database operations for Fee Management:
class fee, individual student fee overrides, fee record generation,
payment processing, and fee status (yellow/red) calculations.

Screens (UI files) must call these functions instead of writing SQL or
fee logic directly, so rules stay consistent everywhere.

Ordering note: student_fee_records rows are always read in insertion
order (id ASC). Because rows are generated in real chronological order
(old-class rows exist before a promotion event, new-class rows only
appear after it), this naturally preserves "old pending fee first, then
new class fee" without needing extra bookkeeping columns.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import math

from app.database import get_connection
from app.normalization import canonicalize_tracking_id, normalize_person_name
from app.config import (
    SESSION_START_CALENDAR_MONTH,
    SESSION_MONTH_ORDER,
    FEE_TYPE_MONTHLY,
    FEE_STATUS_PAID,
    FEE_STATUS_UNPAID,
    FEE_STATUS_WAIVED,
    STATUS_ACTIVE,
    STATUS_PARTIALLY_GRADUATED,
    STATUS_GRADUATED,
    STATUS_ARCHIVED,
    STATUS_DROPPED,
    STATUS_COMING_SESSION,
    get_session_year_label,
)

PAYMENT_STATUS_COMPLETED = "Completed"
PAYMENT_STATUS_CANCELLED = "Cancelled"
_PAYMENT_PENDING_ACTOR = "__HMS_PAYMENT_PENDING__"
IMMEDIATE_PAYMENT_CANCELLATION_V11 = True
RECEIPT_NUMBER_REOPEN_V16 = True

# A small tolerance avoids false alarms from ordinary clock drift while still
# detecting a deliberately or accidentally rolled-back Windows clock.
FINANCIAL_CLOCK_ROLLBACK_TOLERANCE_SECONDS = 300
_FINANCIAL_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

MONTH_NUMBER_TO_NAME = {number: name for name, number in SESSION_MONTH_ORDER}


def _parse_financial_timestamp(value, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is missing.")
    normalised = text.replace("T", " ")
    try:
        return datetime.fromisoformat(normalised)
    except ValueError as error:
        raise ValueError(
            f"{field} has an invalid timestamp: {text!r}."
        ) from error


def _local_naive_financial_timestamp(value: datetime) -> datetime:
    """Normalize offset-aware legacy timestamps for local clock comparison."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def get_financial_clock_status(
    *,
    as_of: datetime | None = None,
    connection=None,
) -> dict:
    """Return whether the computer clock is behind saved fee transactions.

    This is a read-only chronology check. It never edits payments or receipts.
    Historical timestamps remain authoritative even when Windows time is moved
    backwards. A caller may pass an existing transaction connection.
    """
    reference = as_of or datetime.now()
    owns_connection = connection is None
    active_connection = connection or get_connection()
    try:
        rows = active_connection.execute(
            """
            SELECT id, receipt_no, payment_status, payment_date, created_at,
                   cancelled_at
            FROM fee_payments
            ORDER BY id ASC
            """
        ).fetchall()

        latest_timestamp = None
        latest_payment_id = None
        latest_receipt_no = None
        latest_field = None

        for row in rows:
            candidates = (
                ("payment_date", row["payment_date"]),
                ("created_at", row["created_at"]),
                ("cancelled_at", row["cancelled_at"]),
            )
            for field_name, raw_value in candidates:
                if raw_value is None or not str(raw_value).strip():
                    continue
                parsed = _parse_financial_timestamp(
                    raw_value,
                    field=f"fee_payments[{int(row['id'])}].{field_name}",
                )
                comparable = _local_naive_financial_timestamp(parsed)
                if latest_timestamp is None or comparable > latest_timestamp:
                    latest_timestamp = comparable
                    latest_payment_id = int(row["id"])
                    latest_receipt_no = str(row["receipt_no"] or "-")
                    latest_field = field_name

        comparable_reference = _local_naive_financial_timestamp(reference)
        rollback_detected = bool(
            latest_timestamp is not None
            and (latest_timestamp - comparable_reference).total_seconds()
            > FINANCIAL_CLOCK_ROLLBACK_TOLERANCE_SECONDS
        )

        return {
            "clock_rollback_detected": rollback_detected,
            "checked_at": reference,
            "latest_financial_timestamp": latest_timestamp,
            "latest_payment_id": latest_payment_id,
            "latest_receipt_no": latest_receipt_no,
            "latest_timestamp_field": latest_field,
            "tolerance_seconds": FINANCIAL_CLOCK_ROLLBACK_TOLERANCE_SECONDS,
        }
    finally:
        if owns_connection:
            active_connection.close()


def ensure_financial_clock_safe(
    connection,
    *,
    operation_name: str,
    as_of: datetime | None = None,
) -> None:
    """Block new payment mutations while Windows time is rolled backwards."""
    status = get_financial_clock_status(
        as_of=as_of,
        connection=connection,
    )
    if not status["clock_rollback_detected"]:
        return

    latest = status["latest_financial_timestamp"]
    latest_text = (
        latest.strftime(_FINANCIAL_TIMESTAMP_FORMAT)
        if latest is not None
        else "an unknown saved time"
    )
    receipt = status["latest_receipt_no"] or "-"
    raise ValueError(
        "Computer date/time is earlier than the latest saved fee transaction. "
        f"Cannot continue {operation_name}. Latest saved receipt: {receipt}; "
        f"latest transaction time: {latest_text}. Correct Windows date/time, "
        "use Sync now, and reopen the software. Existing receipts were not changed."
    )


def _round_percentage_fee_to_nearest_ten(
    amount,
) -> float:
    """
    Round a percentage-calculated fee to the nearest Rs. 10.

    Examples:
    Rs. 14 -> Rs. 10
    Rs. 15 -> Rs. 20
    """
    amount_decimal = Decimal(
        str(amount)
    )

    rounded_amount = (
        amount_decimal
        / Decimal("10")
    ).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    ) * Decimal("10")

    return float(
        rounded_amount
    )


def _format_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _quantize_money(value) -> Decimal:
    """Return one currency item rounded to cents using financial half-up rules."""
    try:
        amount = Decimal(str(value))
    except Exception as error:
        raise ValueError("A fee amount is not a valid currency value.") from error
    if not amount.is_finite():
        raise ValueError("A fee amount is not finite.")
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _single_active_graduation_record(cursor, student_id: int, *, required: bool) :
    rows = cursor.execute(
        "SELECT * FROM graduation_records WHERE student_id=? AND is_reversed=0 ORDER BY graduated_record_id",
        (int(student_id),),
    ).fetchall()
    if len(rows) > 1:
        raise RuntimeError(
            f"Student {int(student_id)} has multiple active graduation records. "
            "Manual reconciliation is required."
        )
    if not rows:
        if required:
            raise RuntimeError(
                f"Student {int(student_id)} has no active graduation record to synchronize."
            )
        return None
    return rows[0]


def _sync_partially_graduated_status(
    cursor,
    student_id: int,
    now: str,
) -> bool:
    """
    Change Partially Graduated to Graduated when no unpaid
    Monthly Fee remains. The caller owns the transaction.
    """
    cursor.execute(
        """
        SELECT student_status
        FROM students
        WHERE student_id = ?
        """,
        (int(student_id),),
    )

    student_row = cursor.fetchone()

    if (
        not student_row
        or student_row["student_status"]
        != STATUS_PARTIALLY_GRADUATED
    ):
        return False

    graduation_row = _single_active_graduation_record(
        cursor, student_id, required=True
    )

    cursor.execute(
        """
        SELECT 1
        FROM student_fee_records
        WHERE student_id = ?
          AND fee_type = ?
          AND status = ?
        LIMIT 1
        """,
        (
            int(student_id),
            FEE_TYPE_MONTHLY,
            FEE_STATUS_UNPAID,
        ),
    )

    if cursor.fetchone() is not None:
        return False

    cursor.execute(
        """
        UPDATE students
        SET
            student_status = ?,
            updated_at = ?,
            updated_by = ?
        WHERE student_id = ?
          AND student_status = ?
        """,
        (
            STATUS_GRADUATED,
            now,
            "fee_auto_graduation",
            int(student_id),
            STATUS_PARTIALLY_GRADUATED,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("The student graduation status could not be synchronized.")

    cursor.execute(
        """
        UPDATE graduation_records
        SET status = ?
        WHERE graduated_record_id = ?
          AND status = ?
        """,
        (
            STATUS_GRADUATED,
            int(graduation_row["graduated_record_id"]),
            STATUS_PARTIALLY_GRADUATED,
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("The graduation record could not be synchronized.")

    return True


def _mark_zero_amount_fee_records_waived(
    cursor,
    student_id: int,
    now: str,
) -> None:
    """Clear receipt-free zero-value rows without inventing a payment."""
    cursor.execute(
        """
        UPDATE student_fee_records AS records
        SET status = ?,
            paid_date = NULL,
            updated_at = ?
        WHERE records.student_id = ?
          AND records.fee_type = ?
          AND records.amount = 0
          AND records.status IN (?, ?, ?)
          AND NOT EXISTS (
                SELECT 1
                FROM fee_receipts AS receipts
                INNER JOIN fee_payments AS payments
                  ON payments.id = receipts.payment_id
                WHERE receipts.student_fee_record_id = records.id
                  AND receipts.status = 'Paid'
                  AND payments.payment_status = 'Completed'
          )
        """,
        (
            FEE_STATUS_WAIVED,
            now,
            int(student_id),
            FEE_TYPE_MONTHLY,
            FEE_STATUS_UNPAID,
            FEE_STATUS_PAID,
            FEE_STATUS_WAIVED,
        ),
    )

    _sync_partially_graduated_status(
        cursor=cursor,
        student_id=student_id,
        now=now,
    )


# Backward-compatible private alias for older internal callers.
_mark_zero_amount_fee_records_paid = _mark_zero_amount_fee_records_waived

def _month_session_start_year(month_number: int, calendar_year: int) -> int:
    """Return the session-start year that a given calendar month/year belongs to."""
    return calendar_year if month_number >= SESSION_START_CALENDAR_MONTH else calendar_year - 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_fee_amount(amount) -> bool:
    """General fee amounts may be zero but must never be negative."""
    try:
        value = float(amount)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value) and value >= 0


def validate_class_fee_amount(amount) -> bool:
    """A class-level Monthly Fee must be a finite amount greater than zero.

    Individual student fee overrides intentionally continue to allow zero so a
    student may be fully waived without weakening the class-wide fee rule.
    """
    try:
        value = float(amount)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value) and value > 0


# ---------------------------------------------------------------------------
# Class fee (Set Class Fee)
# ---------------------------------------------------------------------------

def get_class_fee(
    class_id: int,
):
    """
    Return the class fee applicable in the current month/session.

    A previous or future session's fee is never returned.
    """
    today = datetime.now()

    connection = get_connection()
    cursor = connection.cursor()

    try:
        try:
            return _get_class_fee_for_period(
                cursor=cursor,
                class_id=class_id,
                month_number=today.month,
                year=today.year,
            )
        except ValueError:
            return None

    finally:
        connection.close()


# MONTHLY_ONLY_CLASS_FEE_CORE_V8
# MONTHLY_ONLY_FEE_SERVICE_SOURCE_CLEANUP_V8
# The class-fee timeline and effective-fee core operate on Monthly Fee only.

def get_class_fee_for_period(
    class_id: int,
    month_number: int,
    year: int,
):
    """Return the class fee applicable to one exact calendar period."""
    class_id = int(class_id)
    month_number = int(month_number)
    year = int(year)
    if not 1 <= month_number <= 12:
        raise ValueError("Month must be between 1 and 12.")
    connection = get_connection()
    try:
        try:
            return _get_class_fee_for_period(
                cursor=connection.cursor(),
                class_id=class_id,
                month_number=month_number,
                year=year,
            )
        except ValueError:
            return None
    finally:
        connection.close()


def get_class_fee_schedule_summary(
    class_id: int,
    session_start_year: int,
    as_of_datetime=None,
) -> dict:
    """Return currently effective and next scheduled events separately."""
    class_id = int(class_id)
    session_start_year = int(session_start_year)
    as_of = as_of_datetime or datetime.now()
    current_period = (int(as_of.year), int(as_of.month))
    session_start = (session_start_year, SESSION_START_CALENDAR_MONTH)
    session_end = (session_start_year + 1, SESSION_START_CALENDAR_MONTH - 1)

    connection = get_connection()
    try:
        events = _load_class_fee_session_events(
            connection.cursor(), class_id, session_start_year
        )
    finally:
        connection.close()

    current_event = None
    if session_start <= current_period <= session_end:
        eligible = [
            event for event in events
            if (int(event["effective_from_year"]), int(event["effective_from_month"]))
            <= current_period
        ]
        if eligible:
            current_event = dict(eligible[-1])

    future_events = [
        event for event in events
        if (int(event["effective_from_year"]), int(event["effective_from_month"]))
        > current_period
    ]
    next_event = dict(future_events[0]) if future_events else None
    return {
        "currently_effective": current_event,
        "next_scheduled": next_event,
        "as_of_year": int(as_of.year),
        "as_of_month": int(as_of.month),
    }


def get_class_fee_for_session(
    class_id: int,
    session_start_year: int,
):
    """
    Return the latest saved Monthly Fee inside one academic session.

    Fees from past or future sessions are never returned.
    """
    class_id = int(
        class_id
    )

    session_start_year = int(
        session_start_year
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                monthly_fee,
                session_start_year,
                effective_from_month,
                effective_from_year

            FROM (
                SELECT
                    id,
                    monthly_fee,
                    session_start_year,
                    effective_from_month,
                    effective_from_year,
                    1 AS source_priority

                FROM fee_settings

                WHERE class_id = ?
                  AND session_start_year = ?

                UNION ALL

                SELECT
                    id,
                    monthly_fee,
                    session_start_year,
                    effective_from_month,
                    effective_from_year,
                    0 AS source_priority

                FROM fee_history

                WHERE class_id = ?
                  AND session_start_year = ?
            )

            ORDER BY
                effective_from_year DESC,
                effective_from_month DESC,
                source_priority DESC,
                id DESC

            LIMIT 1
            """,
            (
                class_id,
                session_start_year,
                class_id,
                session_start_year,
            ),
        )

        row = cursor.fetchone()

        if not row:
            return None

        return {
            "monthly_fee": float(
                row["monthly_fee"]
            ),

            "session_start_year": int(
                row["session_start_year"]
            ),

            "effective_from_month": int(
                row["effective_from_month"]
            ),

            "effective_from_year": int(
                row["effective_from_year"]
            ),
        }

    finally:
        connection.close()
def get_class_fee_display_schedule(
    class_id: int,
    *,
    as_of: datetime | None = None,
    selected_month: int | None = None,
    selected_year: int | None = None,
) -> dict:
    """Return the current/selected-period fee and the next scheduled event."""
    reference = as_of or datetime.now()
    period_month = int(selected_month if selected_month is not None else reference.month)
    period_year = int(selected_year if selected_year is not None else reference.year)
    if not 1 <= period_month <= 12:
        raise ValueError("Selected fee month must be between 1 and 12.")

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()
        try:
            current = _get_class_fee_for_period(
                cursor=cursor,
                class_id=int(class_id),
                month_number=period_month,
                year=period_year,
            )
        except ValueError:
            current = None

        cursor.execute(
            """
            SELECT monthly_fee, session_start_year,
                   effective_from_month, effective_from_year
            FROM (
                SELECT id, monthly_fee, session_start_year,
                       effective_from_month, effective_from_year,
                       1 AS source_priority
                FROM fee_settings WHERE class_id=?
                UNION ALL
                SELECT id, monthly_fee, session_start_year,
                       effective_from_month, effective_from_year,
                       0 AS source_priority
                FROM fee_history WHERE class_id=?
            )
            WHERE effective_from_year > ?
               OR (effective_from_year = ? AND effective_from_month > ?)
            ORDER BY effective_from_year, effective_from_month,
                     source_priority DESC, id DESC
            LIMIT 1
            """,
            (int(class_id), int(class_id), period_year, period_year, period_month),
        )
        next_row = cursor.fetchone()
        return {
            "period_month": period_month,
            "period_year": period_year,
            "currently_effective": dict(current) if current else None,
            "next_scheduled": dict(next_row) if next_row else None,
        }
    finally:
        if connection is not None:
            connection.close()



def _previous_month_period(
    month_number: int,
    year: int,
) -> tuple[int, int]:
    """Return the calendar month immediately before a period."""
    month_number = int(month_number)
    year = int(year)

    if month_number == 1:
        return 12, year - 1

    return month_number - 1, year


def _table_column_names(cursor, table_name: str) -> set[str]:
    return {str(row[1]) for row in cursor.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()}


def _load_class_fee_session_events(
    cursor,
    class_id: int,
    session_start_year: int,
) -> list[dict]:
    """Load the exact incremental fee-event timeline for one session."""
    settings_columns = _table_column_names(cursor, "fee_settings")
    history_columns = _table_column_names(cursor, "fee_history")

    setting_created_by = (
        "created_by" if "created_by" in settings_columns else "NULL AS created_by"
    )
    setting_updated_by = (
        "updated_by" if "updated_by" in settings_columns else "NULL AS updated_by"
    )
    history_created_by = (
        "created_by" if "created_by" in history_columns else "NULL AS created_by"
    )
    history_updated_at = (
        "updated_at" if "updated_at" in history_columns else "created_at AS updated_at"
    )
    history_updated_by = (
        "updated_by" if "updated_by" in history_columns else "NULL AS updated_by"
    )

    cursor.execute(
        f"""
        SELECT id, monthly_fee, effective_from_month, effective_from_year,
               NULL AS effective_to_month, NULL AS effective_to_year,
               created_at, updated_at, {setting_created_by}, {setting_updated_by},
               'settings' AS source
        FROM fee_settings
        WHERE class_id = ? AND session_start_year = ?
        UNION ALL
        SELECT id, monthly_fee, effective_from_month, effective_from_year,
               effective_to_month, effective_to_year,
               created_at, {history_updated_at}, {history_created_by}, {history_updated_by},
               'history' AS source
        FROM fee_history
        WHERE class_id = ? AND session_start_year = ?
        ORDER BY effective_from_year, effective_from_month, source, id
        """,
        (class_id, session_start_year, class_id, session_start_year),
    )

    # If a legacy duplicate exists for one start period, retain the newest
    # effective record in calculations without deleting any audit row.
    by_period = {}
    for row in cursor.fetchall():
        event = dict(row)
        key = (int(event["effective_from_year"]), int(event["effective_from_month"]))
        by_period[key] = event
    return [by_period[key] for key in sorted(by_period)]

def _rebuild_class_fee_session_timeline(
    cursor,
    class_id: int,
    session_start_year: int,
    monthly_fee: float,
    effective_from_month: int,
    effective_from_year: int,
    now: str,
    created_by: str = "admin",
    change_reason: str = "Class fee rule saved",
) -> None:
    """Apply one fee revision incrementally without rewriting audit history."""
    events = _load_class_fee_session_events(
        cursor, class_id, session_start_year
    )
    new_period = (int(effective_from_year), int(effective_from_month))
    settings_columns = _table_column_names(cursor, "fee_settings")
    history_columns = _table_column_names(cursor, "fee_history")

    existing = next((event for event in events if (
        int(event["effective_from_year"]), int(event["effective_from_month"])
    ) == new_period), None)

    if existing is not None:
        table = "fee_settings" if existing["source"] == "settings" else "fee_history"
        assignments = ["monthly_fee = ?"]
        values = [float(monthly_fee)]
        columns = settings_columns if table == "fee_settings" else history_columns
        if "updated_at" in columns:
            assignments.append("updated_at = ?")
            values.append(now)
        if "updated_by" in columns:
            assignments.append("updated_by = ?")
            values.append(created_by)
        if "change_reason" in columns:
            assignments.append("change_reason = ?")
            values.append(change_reason)
        values.append(int(existing["id"]))
        cursor.execute(
            f"UPDATE {table} SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        return

    if not events:
        columns = [
            "class_id", "monthly_fee", "session_start_year",
            "effective_from_month", "effective_from_year",
            "created_at", "updated_at",
        ]
        values = [class_id, float(monthly_fee), session_start_year,
                  effective_from_month, effective_from_year, now, now]
        if "created_by" in settings_columns:
            columns.append("created_by"); values.append(created_by)
        if "updated_by" in settings_columns:
            columns.append("updated_by"); values.append(None)
        if "change_reason" in settings_columns:
            columns.append("change_reason"); values.append(change_reason)
        cursor.execute(
            f"INSERT INTO fee_settings ({', '.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        return

    previous = None
    next_event = None
    for event in events:
        period = (int(event["effective_from_year"]), int(event["effective_from_month"]))
        if period < new_period:
            previous = event
        elif period > new_period:
            next_event = event
            break

    if next_event is None:
        current = events[-1]
        if current["source"] != "settings":
            raise RuntimeError("The class fee timeline has no current settings row.")
        to_month, to_year = _previous_month_period(
            effective_from_month, effective_from_year
        )
        history_insert_columns = [
            "class_id", "monthly_fee", "session_start_year",
            "effective_from_month", "effective_from_year",
            "effective_to_month", "effective_to_year", "created_at",
        ]
        history_values = [
            class_id, float(current["monthly_fee"]), session_start_year,
            int(current["effective_from_month"]), int(current["effective_from_year"]),
            to_month, to_year, current["created_at"] or now,
        ]
        if "created_by" in history_columns:
            history_insert_columns.append("created_by")
            history_values.append(current.get("created_by") or "legacy")
        if "updated_at" in history_columns:
            history_insert_columns.append("updated_at")
            history_values.append(current.get("updated_at") or current["created_at"] or now)
        if "updated_by" in history_columns:
            history_insert_columns.append("updated_by")
            history_values.append(current.get("updated_by"))
        if "change_reason" in history_columns:
            history_insert_columns.append("change_reason")
            history_values.append(current.get("change_reason") or "LEGACY_MIGRATION")
        cursor.execute(
            f"INSERT INTO fee_history ({', '.join(history_insert_columns)}) VALUES ({','.join('?' for _ in history_insert_columns)})",
            history_values,
        )

        assignments = [
            "monthly_fee = ?", "effective_from_month = ?",
            "effective_from_year = ?", "created_at = ?", "updated_at = ?",
        ]
        values = [float(monthly_fee), effective_from_month, effective_from_year, now, now]
        if "created_by" in settings_columns:
            assignments.append("created_by = ?"); values.append(created_by)
        if "updated_by" in settings_columns:
            assignments.append("updated_by = ?"); values.append(None)
        if "change_reason" in settings_columns:
            assignments.append("change_reason = ?"); values.append(change_reason)
        if "superseded_at" in settings_columns:
            assignments.extend(["superseded_at = NULL", "superseded_by = NULL",
                                "superseded_reason = NULL", "superseded_by_record_id = NULL"])
        values.append(int(current["id"]))
        cursor.execute(
            f"UPDATE fee_settings SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        return

    # Insert a revision before a later event. Only the previous boundary is
    # adjusted; all existing IDs and creation metadata remain untouched.
    if previous is not None:
        prev_to_month, prev_to_year = _previous_month_period(
            effective_from_month, effective_from_year
        )
        if previous["source"] != "history":
            raise RuntimeError("A non-latest fee event cannot be stored as current settings.")
        assignments = ["effective_to_month = ?", "effective_to_year = ?"]
        values = [prev_to_month, prev_to_year]
        if "updated_at" in history_columns:
            assignments.append("updated_at = ?"); values.append(now)
        if "updated_by" in history_columns:
            assignments.append("updated_by = ?"); values.append(created_by)
        if "change_reason" in history_columns:
            assignments.append("change_reason = ?"); values.append(change_reason)
        values.append(int(previous["id"]))
        cursor.execute(
            f"UPDATE fee_history SET {', '.join(assignments)} WHERE id = ?",
            values,
        )

    new_to_month, new_to_year = _previous_month_period(
        int(next_event["effective_from_month"]),
        int(next_event["effective_from_year"]),
    )
    columns = [
        "class_id", "monthly_fee", "session_start_year",
        "effective_from_month", "effective_from_year",
        "effective_to_month", "effective_to_year", "created_at",
    ]
    values = [class_id, float(monthly_fee), session_start_year,
              effective_from_month, effective_from_year,
              new_to_month, new_to_year, now]
    if "created_by" in history_columns:
        columns.append("created_by"); values.append(created_by)
    if "updated_at" in history_columns:
        columns.append("updated_at"); values.append(now)
    if "updated_by" in history_columns:
        columns.append("updated_by"); values.append(None)
    if "change_reason" in history_columns:
        columns.append("change_reason"); values.append(change_reason)
    cursor.execute(
        f"INSERT INTO fee_history ({', '.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        values,
    )

def _validate_active_class_fee_periods(cursor, class_id: int, session_start_year: int) -> None:
    """Reject overlapping current/history class-fee periods without choosing by ID."""
    session_end_year = int(session_start_year) + 1
    history_columns = _table_column_names(cursor, "fee_history")
    settings_columns = _table_column_names(cursor, "fee_settings")
    history_active = " AND superseded_at IS NULL" if "superseded_at" in history_columns else ""
    settings_active = " AND superseded_at IS NULL" if "superseded_at" in settings_columns else ""
    rows = cursor.execute(
        f"""
        SELECT 'history' AS source, id, effective_from_year, effective_from_month,
               effective_to_year, effective_to_month
        FROM fee_history
        WHERE class_id=? AND session_start_year=? {history_active}
        UNION ALL
        SELECT 'settings', id, effective_from_year, effective_from_month,
               ? AS effective_to_year, 3 AS effective_to_month
        FROM fee_settings
        WHERE class_id=? AND session_start_year=? {settings_active}
        ORDER BY effective_from_year, effective_from_month, source, id
        """,
        (int(class_id), int(session_start_year), session_end_year,
         int(class_id), int(session_start_year)),
    ).fetchall()
    intervals = []
    for row in rows:
        start = int(row["effective_from_year"]) * 12 + int(row["effective_from_month"])
        end = int(row["effective_to_year"]) * 12 + int(row["effective_to_month"])
        if end < start:
            raise ValueError(f"Class fee rule {row['source']}:{row['id']} ends before it starts.")
        for prior in intervals:
            if start <= prior[1] and prior[0] <= end:
                raise ValueError(
                    "Conflicting active class-fee periods overlap: "
                    f"{prior[2]} and {row['source']}:{row['id']}."
                )
        intervals.append((start, end, f"{row['source']}:{row['id']}"))


def set_class_fee(
    class_id: int,
    monthly_fee: float,
    effective_from_month: int,
    effective_from_year: int,
    created_by: str = "admin",
    connection=None,
    change_reason: str = "Class fee rule saved",
) -> None:
    """
    Set a class Monthly Fee from the selected month onward.

    Previous periods remain preserved in fee_history. Existing Paid
    monthly records are protected.
    """
    created_by = str(created_by or "admin").strip() or "admin"
    change_reason = str(change_reason or "").strip()
    if not change_reason:
        raise ValueError("A change reason is required for a class fee rule.")

    class_id = int(
        class_id
    )

    monthly_fee = float(
        monthly_fee
    )

    effective_from_month = int(
        effective_from_month
    )

    effective_from_year = int(
        effective_from_year
    )

    if not validate_class_fee_amount(
        monthly_fee
    ):
        raise ValueError(
            (
                "Full Class Monthly Fee must be numeric "
                "and greater than zero."
            )
        )

    if not 1 <= effective_from_month <= 12:
        raise ValueError(
            (
                "Effective month must be "
                "between 1 and 12."
            )
        )

    if (
        effective_from_month
        >= SESSION_START_CALENDAR_MONTH
    ):
        session_start_year = (
            effective_from_year
        )

    else:
        session_start_year = (
            effective_from_year - 1
        )

    session_label = (
        get_session_year_label(
            session_start_year
        )
    )

    now = _format_now()

    owns_connection = (
        connection is None
    )

    if owns_connection:
        connection = get_connection()

    cursor = connection.cursor()

    try:
        # CLASS_FEE_APRIL_BASELINE_V38
        # Every class/session timeline must start from April. Once that
        # baseline exists, later fee revisions may start from May-March.
        if (
            effective_from_month
            != SESSION_START_CALENDAR_MONTH
        ):
            existing_events = (
                _load_class_fee_session_events(
                    cursor=cursor,
                    class_id=class_id,
                    session_start_year=(
                        session_start_year
                    ),
                )
            )

            has_april_baseline = any(
                int(
                    event[
                        "effective_from_month"
                    ]
                )
                == SESSION_START_CALENDAR_MONTH
                and int(
                    event[
                        "effective_from_year"
                    ]
                )
                == session_start_year
                for event in existing_events
            )

            if not has_april_baseline:
                raise ValueError(
                    (
                        "The first Monthly Fee for session "
                        f"{session_label} must be effective "
                        "from April. Set the April fee first, "
                        "then save any later fee revision."
                    )
                )

        _validate_active_class_fee_periods(cursor, class_id, session_start_year)

        _rebuild_class_fee_session_timeline(
            cursor=cursor,
            class_id=class_id,
            session_start_year=(
                session_start_year
            ),
            monthly_fee=monthly_fee,
            effective_from_month=(
                effective_from_month
            ),
            effective_from_year=(
                effective_from_year
            ),
            now=now,
            created_by=created_by,
            change_reason=change_reason,
        )
        _validate_active_class_fee_periods(cursor, class_id, session_start_year)

        # Recalculate only adjustable Monthly Fee rows in the
        # selected class, session and effective period.
        #
        # Genuine receipt-backed Paid rows are never modified.
        cursor.execute(
            """
            SELECT
                records.id,
                records.student_id,
                records.class_id,
                records.month_number,
                records.year,
                records.amount,
                records.status,
                records.paid_date

            FROM student_fee_records
                AS records

            WHERE records.class_id = ?
              AND records.session_year = ?
              AND records.fee_type = ?

              AND (
                    records.year > ?
                    OR (
                        records.year = ?
                        AND records.month_number >= ?
                    )
                  )

              AND (
                    records.status IN (?, ?)
                    OR (
                        records.status = ?
                        AND records.amount = 0
                        AND NOT EXISTS (
                            SELECT 1
                            FROM fee_receipts
                                AS receipts
                            INNER JOIN fee_payments
                                AS payments
                              ON payments.id =
                                    receipts.payment_id
                            WHERE
                                receipts.student_fee_record_id
                                = records.id
                              AND receipts.status = 'Paid'
                              AND payments.payment_status = 'Completed'
                        )
                    )
                  )

            ORDER BY
                records.year ASC,
                records.month_number ASC,
                records.id ASC
            """,
            (
                class_id,
                session_label,
                FEE_TYPE_MONTHLY,
                effective_from_year,
                effective_from_year,
                effective_from_month,
                FEE_STATUS_UNPAID,
                FEE_STATUS_WAIVED,
                FEE_STATUS_PAID,
            ),
        )

        adjustable_records = (
            cursor.fetchall()
        )

        for fee_record in (
            adjustable_records
        ):
            effective_fee = (
                get_effective_fee(
                    cursor=cursor,
                    class_id=(
                        fee_record[
                            "class_id"
                        ]
                    ),
                    student_id=(
                        fee_record[
                            "student_id"
                        ]
                    ),
                    month_number=(
                        fee_record[
                            "month_number"
                        ]
                    ),
                    year=(
                        fee_record[
                            "year"
                        ]
                    ),
                )
            )

            recalculated_amount = round(
                float(
                    effective_fee[
                        "monthly_fee"
                    ]
                ),
                2,
            )

            if recalculated_amount == 0:
                updated_status = (
                    FEE_STATUS_WAIVED
                )

                updated_paid_date = None

            else:
                updated_status = (
                    FEE_STATUS_UNPAID
                )

                updated_paid_date = None

            cursor.execute(
                """
                UPDATE student_fee_records
                SET
                    amount = ?,
                    status = ?,
                    paid_date = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    recalculated_amount,
                    updated_status,
                    updated_paid_date,
                    now,
                    fee_record["id"],
                ),
            )

        if owns_connection:
            connection.commit()

    except Exception:
        if owns_connection:
            connection.rollback()
        raise

    finally:
        if owns_connection:
            connection.close()


def set_class_fees_for_level(
    class_ids,
    monthly_fee: float,
    effective_from_month: int,
    effective_from_year: int,
    created_by: str = "admin",
) -> None:
    # Apply one fee to every supplied section as one atomic transaction.
    normalized_class_ids = []
    seen_class_ids = set()

    for class_id in class_ids:
        normalized_class_id = int(class_id)

        if normalized_class_id <= 0:
            raise ValueError(
                "Every class ID must be a positive integer."
            )

        if normalized_class_id in seen_class_ids:
            continue

        seen_class_ids.add(normalized_class_id)
        normalized_class_ids.append(normalized_class_id)

    if not normalized_class_ids:
        raise ValueError(
            "No class sections were supplied for the fee update."
        )

    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE")

        for class_id in normalized_class_ids:
            set_class_fee(
                class_id=class_id,
                monthly_fee=monthly_fee,
                effective_from_month=effective_from_month,
                effective_from_year=effective_from_year,
                created_by=created_by,
                connection=connection,
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Student search for Fee Management
# ---------------------------------------------------------------------------

# FEE_TRACKING_ID_BACKEND_V1
# CURRENT_SESSION_FULL_FEE_HISTORY_BACKEND_V1
def normalize_fee_tracking_id(tracking_id) -> str:
    """Return the shared canonical Tracking-ID representation."""
    try:
        return canonicalize_tracking_id(tracking_id)
    except ValueError as error:
        raise ValueError(
            "Tracking ID must use a valid format, for example HMS-000001."
        ) from error


def search_students_for_fee(
    admission_number: str = None,
    student_name: str = None,
    father_name: str = None,
    father_cnic: str = None,
    roll_number: str = None,
    class_id: int = None,
    tracking_id: str = None,
):
    """Search students for Pay Fee without leaking SQLite connections."""
    normalized_tracking_id = None
    normalized_admission_number = None
    normalized_student_name = None
    normalized_father_name = None
    normalized_cnic = None
    roll_number_value = None
    released_only = False

    if tracking_id and str(tracking_id).strip():
        # Validate before opening SQLite so invalid input cannot leak a handle.
        normalized_tracking_id = normalize_fee_tracking_id(tracking_id)
    elif admission_number and str(admission_number).strip():
        normalized_admission_number = str(admission_number).strip().upper()
    elif father_cnic and str(father_cnic).strip():
        normalized_cnic = (
            str(father_cnic).replace("-", "").replace(" ", "").strip()
        )
        if len(normalized_cnic) != 13 or not normalized_cnic.isdigit():
            raise ValueError("Father CNIC must contain exactly 13 numeric digits.")
    elif roll_number is not None and str(roll_number).strip():
        if class_id is None:
            raise ValueError("Please select a class when searching by Roll Number.")
        from app.services import student_service
        roll_number_value, released_only = student_service.parse_roll_number_search(
            roll_number
        )
    elif student_name and str(student_name).strip():
        if class_id is None:
            raise ValueError("Please select a class when searching by Student Name.")
        normalized_student_name = normalize_person_name(student_name)
        if father_name and str(father_name).strip():
            normalized_father_name = normalize_person_name(father_name)
    else:
        raise ValueError(
            "Enter a Tracking ID, Admission Number, or Father CNIC, "
            "or use Roll Number + Class, or Student Name + Class."
        )

    connection = None
    try:
        connection = get_connection()
        cursor = connection.cursor()

        base_select = """
            SELECT s.*, c.class_name
            FROM students AS s
            LEFT JOIN classes AS c ON c.id = s.class_id
        """
        parameters = []

        if normalized_tracking_id is not None:
            where = "UPPER(TRIM(COALESCE(s.tracking_id, ''))) = ?"
            parameters = [normalized_tracking_id]
        elif normalized_admission_number is not None:
            where = "TRIM(s.admission_number) = ? COLLATE NOCASE"
            parameters = [normalized_admission_number]
        elif normalized_cnic is not None:
            where = "COALESCE(TRIM(s.father_cnic), '') = ?"
            parameters = [normalized_cnic]
        elif roll_number_value is not None:
            if released_only:
                where = "s.released_roll_number = ? AND s.class_id = ?"
                parameters = [roll_number_value, int(class_id)]
            else:
                where = (
                    "(s.roll_number = ? OR s.released_roll_number = ?) "
                    "AND s.class_id = ?"
                )
                parameters = [roll_number_value, roll_number_value, int(class_id)]
        else:
            where = (
                "INSTR(NORMALIZE_PERSON_NAME(s.student_name), ?) > 0 "
                "AND s.class_id = ?"
            )
            parameters = [normalized_student_name, int(class_id)]
            if normalized_father_name is not None:
                where += (
                    " AND INSTR(NORMALIZE_PERSON_NAME(s.father_name), ?) > 0"
                )
                parameters.append(normalized_father_name)

        cursor.execute(
            base_select
            + " WHERE "
            + where
            + " ORDER BY s.student_name ASC, s.father_name ASC, s.roll_number ASC",
            parameters,
        )
        return [
            row
            for row in cursor.fetchall()
            if str(row["student_status"] or "").strip() != STATUS_ARCHIVED
        ]
    finally:
        if connection is not None:
            connection.close()
# ---------------------------------------------------------------------------
# Individual student fee override (Set Individual Student Fee)
# ---------------------------------------------------------------------------

def get_active_student_override(
    student_id: int,
):
    """Return the individual fee applicable today."""
    today = datetime.now()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        columns = _table_column_names(cursor, "student_fee_override")
        removal_clause = ""
        parameters = [student_id, today.year, today.year, today.month,
                      today.year, today.year, today.month]
        if "removed_effective_year" in columns:
            removal_clause = """
              AND (removed_effective_year IS NULL
                   OR removed_effective_year > ?
                   OR (removed_effective_year = ? AND removed_effective_month > ?))
            """
            parameters.extend([today.year, today.year, today.month])
        cursor.execute(
            f"""
            SELECT * FROM student_fee_override
            WHERE student_id = ?
              AND (effective_from_year < ? OR
                   (effective_from_year = ? AND effective_from_month <= ?))
              AND (effective_to_year IS NULL OR effective_to_year > ? OR
                   (effective_to_year = ? AND effective_to_month >= ?))
              {removal_clause}
            ORDER BY effective_from_year DESC, effective_from_month DESC, id DESC
            LIMIT 1
            """,
            parameters,
        )
        return cursor.fetchone()
    finally:
        connection.close()

def get_student_override_for_session(
    student_id: int,
    session_start_year: int,
):
    """Return the latest individual fee rule overlapping one session."""
    student_id = int(student_id)
    session_start_year = int(session_start_year)
    session_end_month = SESSION_START_CALENDAR_MONTH - 1
    session_end_year = session_start_year + 1
    connection = get_connection()
    cursor = connection.cursor()
    try:
        columns = _table_column_names(cursor, "student_fee_override")
        removal_clause = ""
        parameters = [student_id, session_end_year, session_end_year, session_end_month,
                      session_start_year, session_start_year, SESSION_START_CALENDAR_MONTH]
        if "removed_effective_year" in columns:
            removal_clause = """
              AND (removed_effective_year IS NULL
                   OR removed_effective_year > ?
                   OR (removed_effective_year = ? AND removed_effective_month > ?))
            """
            parameters.extend([session_start_year, session_start_year, SESSION_START_CALENDAR_MONTH])
        cursor.execute(
            f"""
            SELECT * FROM student_fee_override
            WHERE student_id = ?
              AND (effective_from_year < ? OR
                   (effective_from_year = ? AND effective_from_month <= ?))
              AND (effective_to_year IS NULL OR effective_to_year > ? OR
                   (effective_to_year = ? AND effective_to_month >= ?))
              {removal_clause}
            ORDER BY effective_from_year DESC, effective_from_month DESC, id DESC
            LIMIT 1
            """,
            parameters,
        )
        return cursor.fetchone()
    finally:
        connection.close()

def set_student_fee_override(
    student_id: int,
    monthly_fee_percentage,
    effective_from_month: int,
    effective_from_year: int,
    number_of_sessions: int | None = None,
    created_by: str = "admin",
    change_reason: str = "Individual fee rule saved",
) -> int:
    """Append a versioned individual-fee rule and preserve prior audit rows."""
    student_id = int(student_id)
    percentage = float(monthly_fee_percentage)
    effective_from_month = int(effective_from_month)
    effective_from_year = int(effective_from_year)
    actor = str(created_by or "admin").strip() or "admin"
    reason = str(change_reason or "").strip()
    if not reason:
        raise ValueError("A change reason is required for an individual fee rule.")
    if not math.isfinite(percentage) or not 0 <= percentage <= 100:
        raise ValueError("Monthly Fee Percentage must be between 0 and 100.")
    if not 1 <= effective_from_month <= 12:
        raise ValueError("Effective month must be between 1 and 12.")

    session_start_year = _month_session_start_year(
        effective_from_month, effective_from_year
    )
    if number_of_sessions is None:
        effective_to_month = None
        effective_to_year = None
    else:
        number_of_sessions = int(number_of_sessions)
        if number_of_sessions < 1:
            raise ValueError("Number of Sessions must be at least 1.")
        effective_to_month = 3
        effective_to_year = session_start_year + number_of_sessions

    now = _format_now()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.cursor()
        student_row = cursor.execute(
            "SELECT * FROM students WHERE student_id=?", (student_id,)
        ).fetchone()
        if student_row is None:
            raise ValueError("Student not found.")
        class_id = int(student_row["class_id"])
        class_fee_at_start = _get_class_fee_for_period(
            cursor, class_id, effective_from_month, effective_from_year
        )
        calculated_monthly_fee = _round_percentage_fee_to_nearest_ten(
            float(class_fee_at_start["monthly_fee"]) * percentage / 100.0
        )

        # Prevalidate every adjustable period before superseding any rule.
        parameters = [
            student_id, FEE_TYPE_MONTHLY,
            effective_from_year, effective_from_year, effective_from_month,
        ]
        end_clause = ""
        if effective_to_year is not None:
            end_clause = (
                " AND (records.year < ? OR "
                "(records.year = ? AND records.month_number <= ?))"
            )
            # SQL placeholders for the bounded end period occur before the
            # adjustable-status placeholders below. Keep the parameter order
            # identical to the query so limited-session rules recalculate the
            # existing Unpaid/Waived rows instead of matching nothing.
            parameters.extend(
                [effective_to_year, effective_to_year, effective_to_month]
            )
        parameters.extend(
            [FEE_STATUS_UNPAID, FEE_STATUS_WAIVED, FEE_STATUS_PAID]
        )
        adjustable_rows = cursor.execute(
            f"""
            SELECT records.id, records.class_id, records.month_number,
                   records.year, records.amount, records.status
            FROM student_fee_records AS records
            WHERE records.student_id=? AND records.fee_type=?
              AND (records.year > ? OR
                   (records.year=? AND records.month_number >= ?))
              {end_clause}
              AND (
                    records.status IN (?, ?)
                    OR (
                        records.status=? AND records.amount=0
                        AND NOT EXISTS (
                            SELECT 1 FROM fee_receipts AS receipts
                            JOIN fee_payments AS payments
                              ON payments.id=receipts.payment_id
                            WHERE receipts.student_fee_record_id=records.id
                              AND receipts.status='Paid'
                              AND payments.payment_status='Completed'
                        )
                    )
                  )
            ORDER BY records.year, records.month_number, records.id
            """,
            parameters,
        ).fetchall()
        for row in adjustable_rows:
            _get_class_fee_for_period(
                cursor, int(row["class_id"]), int(row["month_number"]), int(row["year"])
            )

        previous = cursor.execute(
            """
            SELECT * FROM student_fee_override
            WHERE student_id=? AND is_active=1 AND superseded_at IS NULL
            ORDER BY effective_from_year DESC, effective_from_month DESC, id DESC
            LIMIT 1
            """,
            (student_id,),
        ).fetchone()
        if previous is not None:
            previous_start = (
                int(previous["effective_from_year"]),
                int(previous["effective_from_month"]),
            )
            if (effective_from_year, effective_from_month) < previous_start:
                raise ValueError(
                    "A new individual-fee rule cannot start before the latest active rule."
                )

        cursor.execute(
            """
            INSERT INTO student_fee_override(
                student_id, monthly_fee, monthly_fee_percentage,
                effective_from_month, effective_from_year,
                effective_to_month, effective_to_year, is_active,
                created_at, updated_at, created_by, updated_by, change_reason
            ) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?)
            """,
            (
                student_id, calculated_monthly_fee, percentage,
                effective_from_month, effective_from_year,
                effective_to_month, effective_to_year,
                now, now, actor, None, reason,
            ),
        )
        new_rule_id = int(cursor.lastrowid)

        if previous is not None:
            assignments = [
                "is_active=0", "superseded_at=?", "superseded_by=?",
                "superseded_reason=?", "superseded_by_record_id=?",
                "updated_at=?", "updated_by=?",
            ]
            values = [now, actor, reason, new_rule_id, now, actor]
            previous_start = (int(previous["effective_from_year"]), int(previous["effective_from_month"]))
            if (effective_from_year, effective_from_month) > previous_start:
                close_month, close_year = _previous_month_period(
                    effective_from_month, effective_from_year
                )
                assignments.extend(["effective_to_month=?", "effective_to_year=?"])
                values.extend([close_month, close_year])
            values.append(int(previous["id"]))
            cursor.execute(
                f"UPDATE student_fee_override SET {', '.join(assignments)} "
                "WHERE id=? AND superseded_at IS NULL",
                values,
            )

        from app.schema_integrity import validate_policy_overlaps
        valid_overlap, overlap_message = validate_policy_overlaps(connection)
        if not valid_overlap:
            raise ValueError(overlap_message)

        for row in adjustable_rows:
            effective_fee = get_effective_fee(
                cursor=cursor,
                class_id=int(row["class_id"]),
                student_id=student_id,
                month_number=int(row["month_number"]),
                year=int(row["year"]),
            )
            amount = round(float(effective_fee["monthly_fee"]), 2)
            status = FEE_STATUS_WAIVED if amount == 0 else FEE_STATUS_UNPAID
            cursor.execute(
                """
                UPDATE student_fee_records
                SET amount=?, status=?, paid_date=NULL, updated_at=?
                WHERE id=?
                """,
                (amount, status, now, int(row["id"])),
            )

        _mark_zero_amount_fee_records_waived(cursor, student_id, now)
        connection.commit()
        return new_rule_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def remove_student_fee_override(
    student_id: int,
    effective_from_month: int,
    session_start_year: int,
    removed_by: str = "admin",
) -> bool:
    """Atomically end an override only after validating every replacement fee."""
    student_id = int(student_id)
    removal_month = int(effective_from_month)
    session_start_year = int(session_start_year)
    removed_by = str(removed_by or "admin").strip() or "admin"

    if not 1 <= removal_month <= 12:
        raise ValueError("Removal effective month must be between 1 and 12.")
    if session_start_year < 2000:
        raise ValueError("Removal academic session is required.")

    removal_year = (
        session_start_year
        if removal_month >= SESSION_START_CALENDAR_MONTH
        else session_start_year + 1
    )
    removal_start = (removal_year, removal_month)
    now = _format_now()

    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN IMMEDIATE")
        columns = _table_column_names(cursor, "student_fee_override")
        required_columns = {
            "removed_effective_month", "removed_effective_year",
            "removed_at", "removed_by",
        }
        if not required_columns.issubset(columns):
            raise RuntimeError(
                "The database must be upgraded before an individual fee can be removed."
            )

        cursor.execute(
            """
            SELECT student_name, admission_number
            FROM students
            WHERE student_id = ?
            """,
            (student_id,),
        )
        student_row = cursor.fetchone()
        if student_row is None:
            raise ValueError("Student was not found.")

        cursor.execute(
            """
            SELECT * FROM student_fee_override
            WHERE student_id = ?
            ORDER BY effective_from_year, effective_from_month, id
            """,
            (student_id,),
        )
        override_rows = cursor.fetchall()
        matching_override_rows = []
        for row in override_rows:
            row_start = (
                int(row["effective_from_year"]),
                int(row["effective_from_month"]),
            )
            row_end = None if row["effective_to_year"] is None else (
                int(row["effective_to_year"]),
                int(row["effective_to_month"]),
            )
            existing_removal = None if row["removed_effective_year"] is None else (
                int(row["removed_effective_year"]),
                int(row["removed_effective_month"]),
            )
            effective_end = row_end
            if existing_removal is not None:
                last_month, last_year = _previous_month_period(
                    existing_removal[1], existing_removal[0]
                )
                existing_last = (last_year, last_month)
                if effective_end is None or existing_last < effective_end:
                    effective_end = existing_last
            if effective_end is None or effective_end >= removal_start:
                matching_override_rows.append(row)

        if not matching_override_rows:
            connection.rollback()
            return False

        cursor.execute(
            """
            SELECT records.id, records.class_id, records.month_name,
                   records.month_number, records.year, records.session_year,
                   records.status, records.amount, records.paid_date,
                   COALESCE(classes.class_name, '-') AS class_name
            FROM student_fee_records AS records
            LEFT JOIN classes ON classes.id = records.class_id
            WHERE records.student_id = ?
              AND records.fee_type = ?
              AND (records.year > ? OR
                   (records.year = ? AND records.month_number >= ?))
              AND (
                    records.status IN (?, ?)
                    OR (records.status = ? AND records.amount = 0 AND NOT EXISTS (
                        SELECT 1 FROM fee_receipts AS receipts
                        INNER JOIN fee_payments AS payments
                          ON payments.id = receipts.payment_id
                        WHERE receipts.student_fee_record_id = records.id
                          AND receipts.status = 'Paid'
                          AND payments.payment_status = 'Completed'
                    ))
              )
            ORDER BY records.year, records.month_number, records.id
            """,
            (
                student_id, FEE_TYPE_MONTHLY,
                removal_year, removal_year, removal_month,
                FEE_STATUS_UNPAID, FEE_STATUS_WAIVED, FEE_STATUS_PAID,
            ),
        )
        adjustable_rows = cursor.fetchall()

        # Validate every replacement class fee before changing the override
        # period or any monthly row. No missing period is silently skipped.
        recalculation_plan = []
        for fee_row in adjustable_rows:
            try:
                class_fee = _get_class_fee_for_period(
                    cursor,
                    int(fee_row["class_id"]),
                    int(fee_row["month_number"]),
                    int(fee_row["year"]),
                )
            except ValueError as error:
                raise ValueError(
                    "Individual-fee removal was stopped because the applicable "
                    "class fee is missing. "
                    f"Student: {student_row['student_name']} "
                    f"({student_row['admission_number']}); "
                    f"Class: {fee_row['class_name']}; "
                    f"Missing month: {fee_row['month_name']} {fee_row['year']}; "
                    f"Academic session: {fee_row['session_year']}. "
                    f"Details: {error}"
                ) from error
            amount = round(float(class_fee["monthly_fee"]), 2)
            recalculation_plan.append(
                (
                    int(fee_row["id"]),
                    amount,
                    FEE_STATUS_WAIVED if amount == 0 else FEE_STATUS_UNPAID,
                )
            )

        previous_month, previous_year = _previous_month_period(
            removal_month, removal_year
        )
        for row in matching_override_rows:
            row_start = (
                int(row["effective_from_year"]),
                int(row["effective_from_month"]),
            )
            if row_start < removal_start:
                cursor.execute(
                    """
                    UPDATE student_fee_override
                    SET effective_to_month = ?, effective_to_year = ?,
                        removed_effective_month = ?, removed_effective_year = ?,
                        removed_at = ?, removed_by = ?, is_active = 0,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        previous_month, previous_year,
                        removal_month, removal_year,
                        now, removed_by, now, int(row["id"]),
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE student_fee_override
                    SET removed_effective_month = ?, removed_effective_year = ?,
                        removed_at = ?, removed_by = ?, is_active = 0,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        removal_month, removal_year,
                        now, removed_by, now, int(row["id"]),
                    ),
                )

        for fee_record_id, amount, status in recalculation_plan:
            cursor.execute(
                """
                UPDATE student_fee_records
                SET amount = ?, status = ?, paid_date = NULL, updated_at = ?
                WHERE id = ?
                """,
                (amount, status, now, fee_record_id),
            )

        _mark_zero_amount_fee_records_waived(cursor, student_id, now)
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get_class_fee_for_period(
    cursor,
    class_id: int,
    month_number: int,
    year: int,
) -> dict:
    """
    Return the Monthly Fee applicable during one academic session.

    A fee from another session is never carried forward.
    """
    class_id = int(
        class_id
    )

    month_number = int(
        month_number
    )

    year = int(
        year
    )

    if (
        month_number
        >= SESSION_START_CALENDAR_MONTH
    ):
        session_start_year = year

    else:
        session_start_year = (
            year - 1
        )

    session_label = (
        get_session_year_label(
            session_start_year
        )
    )

    cursor.execute(
        """
        SELECT
            monthly_fee,
            session_start_year,
            effective_from_month,
            effective_from_year

        FROM fee_history

        WHERE class_id = ?
          AND session_start_year = ?

          AND (
                effective_from_year < ?
                OR (
                    effective_from_year = ?
                    AND effective_from_month <= ?
                )
              )

          AND (
                effective_to_year IS NULL
                OR effective_to_year > ?
                OR (
                    effective_to_year = ?
                    AND effective_to_month >= ?
                )
              )

        ORDER BY
            effective_from_year DESC,
            effective_from_month DESC,
            id DESC

        LIMIT 1
        """,
        (
            class_id,
            session_start_year,
            year,
            year,
            month_number,
            year,
            year,
            month_number,
        ),
    )

    class_fee_row = (
        cursor.fetchone()
    )

    if not class_fee_row:
        cursor.execute(
            """
            SELECT
                monthly_fee,
                session_start_year,
                effective_from_month,
                effective_from_year

            FROM fee_settings

            WHERE class_id = ?
              AND session_start_year = ?

              AND (
                    effective_from_year < ?
                    OR (
                        effective_from_year = ?
                        AND effective_from_month <= ?
                    )
                  )

            ORDER BY
                effective_from_year DESC,
                effective_from_month DESC,
                id DESC

            LIMIT 1
            """,
            (
                class_id,
                session_start_year,
                year,
                year,
                month_number,
            ),
        )

        class_fee_row = (
            cursor.fetchone()
        )

    if not class_fee_row:
        raise ValueError(
            (
                "No class fee has been set for "
                f"session {session_label} "
                "for this period. Please set "
                "the class fee first."
            )
        )

    return {
        "monthly_fee": float(
            class_fee_row[
                "monthly_fee"
            ]
        ),

        "session_start_year": int(
            class_fee_row[
                "session_start_year"
            ]
        ),

        "effective_from_month": int(
            class_fee_row[
                "effective_from_month"
            ]
        ),

        "effective_from_year": int(
            class_fee_row[
                "effective_from_year"
            ]
        ),
    }



def get_effective_fee(
    cursor,
    class_id: int,
    student_id: int,
    month_number: int,
    year: int,
) -> dict:
    """
    Return the effective Monthly Fee for one student and period.

    An active individual override is calculated as a percentage of
    the applicable class Monthly Fee. Legacy fixed Monthly Fee
    overrides remain readable.
    """
    class_fee = (
        _get_class_fee_for_period(
            cursor,
            class_id,
            month_number,
            year,
        )
    )

    # SIBLING_MONTHLY_FEE_PRECEDENCE_V9
    # An eligible sibling assignment controls the class-fee percentage
    # for its effective period. Outside an eligible sibling group, the
    # existing individual override rules continue unchanged.
    from app.services import sibling_fee_service

    sibling_assignment = (
        sibling_fee_service.get_sibling_assignment_for_period(
            cursor=cursor,
            student_id=student_id,
            month_number=month_number,
            year=year,
        )
    )

    if (
        sibling_assignment
        and int(
            sibling_assignment[
                "is_sibling_policy"
            ]
        ) == 1
    ):
        sibling_percentage = float(
            sibling_assignment[
                "monthly_fee_percentage"
            ]
        )

        sibling_monthly_fee = (
            sibling_fee_service.
            round_sibling_discount_to_nearest_ten(
                float(
                    class_fee[
                        "monthly_fee"
                    ]
                )
                * sibling_percentage
                / 100.0
            )
        )

        return {
            "monthly_fee": sibling_monthly_fee,
            "fee_source": "Sibling",
            "monthly_fee_percentage": (
                sibling_percentage
            ),
        }

    override_columns = _table_column_names(cursor, "student_fee_override")
    removal_clause = ""
    override_parameters = [
        student_id, year, year, month_number, year, year, month_number
    ]
    if "removed_effective_year" in override_columns:
        removal_clause = """
          AND (removed_effective_year IS NULL
               OR removed_effective_year > ?
               OR (removed_effective_year = ? AND removed_effective_month > ?))
        """
        override_parameters.extend([year, year, month_number])

    cursor.execute(
        f"""
        SELECT * FROM student_fee_override
        WHERE student_id = ?
          AND (effective_from_year < ? OR
               (effective_from_year = ? AND effective_from_month <= ?))
          AND (effective_to_year IS NULL OR effective_to_year > ? OR
               (effective_to_year = ? AND effective_to_month >= ?))
          {removal_clause}
        ORDER BY effective_from_year DESC, effective_from_month DESC, id DESC
        LIMIT 1
        """,
        override_parameters,
    )

    override_row = (
        cursor.fetchone()
    )

    if not override_row:
        return class_fee

    percentage = (
        override_row[
            "monthly_fee_percentage"
        ]
    )

    if percentage is not None:
        monthly_fee = (
            _round_percentage_fee_to_nearest_ten(
                class_fee["monthly_fee"]
                * float(percentage)
                / 100.0
            )
        )

    elif (
        override_row["monthly_fee"]
        is not None
    ):
        # Compatibility with old fixed Monthly Fee overrides.
        monthly_fee = float(
            override_row[
                "monthly_fee"
            ]
        )

    else:
        monthly_fee = float(
            class_fee[
                "monthly_fee"
            ]
        )

    return {
        "monthly_fee": monthly_fee,
    }



def calculate_individual_monthly_fee_preview(
    class_id: int,
    monthly_fee_percentage,
    month_number: int,
    year: int,
) -> float:
    """
    Calculate the individual monthly amount for UI preview.

    The percentage itself remains the controlling rule.
    """
    percentage = float(monthly_fee_percentage)

    if not 0 <= percentage <= 100:
        raise ValueError(
            "Monthly Fee Percentage must be between 0 and 100."
        )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        class_fee = _get_class_fee_for_period(
            cursor,
            class_id,
            int(month_number),
            int(year),
        )

        return _round_percentage_fee_to_nearest_ten(
            float(class_fee["monthly_fee"])
            * percentage
            / 100.0
        )

    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Fee record generation
# ---------------------------------------------------------------------------

IMMUTABLE_ADMISSION_REACTIVATION_FEE_BOUNDARY_V12 = True

def _determine_fee_start_month_year(
    cursor,
    student_row,
):
    """
    Return the start of the student's current payable period.

    Original Admission Date is immutable. A later promotion or explicit
    reactivation may start a new payable period, but must never rewrite the
    original admission fields.
    """
    candidates = [
        (
            int(student_row["admission_year"]),
            int(student_row["admission_month"]),
        )
    ]

    cursor.execute(
        """
        SELECT promotion_datetime
        FROM promotion_history
        WHERE student_id = ?
          AND new_class_id = ?
          AND promotion_result = 'P'
          AND is_reversed = 0
          AND (
                old_class_id IS NULL
                OR old_class_id <> new_class_id
          )
        ORDER BY promotion_datetime DESC
        LIMIT 1
        """,
        (
            student_row["student_id"],
            student_row["class_id"],
        ),
    )

    promotion_row = cursor.fetchone()

    if promotion_row:
        promoted_at = datetime.fromisoformat(
            promotion_row["promotion_datetime"]
        )

        promotion_start_year = (
            promoted_at.year
            if promoted_at.month <= SESSION_START_CALENDAR_MONTH
            else promoted_at.year + 1
        )

        candidates.append(
            (
                promotion_start_year,
                SESSION_START_CALENDAR_MONTH,
            )
        )

    # Coming-session activation is another payable-boundary event. It is
    # intentionally folded into this existing max(candidates) engine instead
    # of creating a second independent fee-start source of truth.
    if str(student_row["coming_session_status"] or "").strip().upper() == "ACTIVATED":
        effective_date = str(student_row["coming_session_effective_date"] or "").strip()
        if effective_date:
            activated_on = datetime.fromisoformat(effective_date)
            candidates.append((activated_on.year, activated_on.month))

    # Reactivation events remain MANUAL class-history rows for schema-9
    # compatibility. Their changed_by prefix distinguishes them from an
    # ordinary class correction without creating a second ADMISSION event.
    cursor.execute(
        """
        SELECT
            effective_year,
            effective_month
        FROM student_class_history
        WHERE student_id = ?
          AND change_type = 'MANUAL'
          AND changed_by LIKE 'REACTIVATION:%'
        ORDER BY
            effective_year DESC,
            effective_month DESC,
            history_id DESC
        LIMIT 1
        """,
        (
            student_row["student_id"],
        ),
    )

    reactivation_row = cursor.fetchone()

    if reactivation_row:
        candidates.append(
            (
                int(reactivation_row["effective_year"]),
                int(reactivation_row["effective_month"]),
            )
        )

    selected_year, selected_month = max(candidates)

    return selected_month, selected_year





# INACTIVE_FEE_GAP_PRESERVATION_V9
# Unpaid historical dues remain payable, while inactive gap months are omitted.
INACTIVE_FEE_GAP_PRESERVATION_V9 = True


def _delete_unpaid_records_after_boundary(
    cursor,
    student_id: int,
    boundary_month: int,
    boundary_year: int,
) -> int:
    """
    Remove only unpaid Monthly Fee rows after an inactive/completion boundary.

    Paid rows and receipt-backed history are never removed. The boundary month
    itself remains payable.
    """
    cursor.execute(
        """
        DELETE FROM student_fee_records
        WHERE student_id = ?
          AND fee_type = ?
          AND status = ?
          AND NOT EXISTS (
                SELECT 1
                FROM fee_receipts
                    AS receipts
                INNER JOIN fee_payments
                    AS payments
                  ON payments.id = receipts.payment_id
                WHERE receipts.student_fee_record_id =
                      student_fee_records.id
                  AND receipts.status = 'Paid'
                  AND payments.payment_status = 'Completed'
          )
          AND (
                year > ?
                OR (
                    year = ?
                    AND month_number > ?
                )
          )
        """,
        (
            int(student_id),
            FEE_TYPE_MONTHLY,
            FEE_STATUS_UNPAID,
            int(boundary_year),
            int(boundary_year),
            int(boundary_month),
        ),
    )

    return int(
        cursor.rowcount
        or 0
    )


def pause_fee_records_after_drop(
    student_id: int,
    drop_month: int,
    drop_year: int,
    connection=None,
) -> int:
    """
    Keep fees through the drop month and remove later unpaid months.

    Example: Dropped in July -> April through July remain; August onward is
    omitted until a later reactivation generates a new active period.
    """
    student_id = int(student_id)
    drop_month = int(drop_month)
    drop_year = int(drop_year)

    if not 1 <= drop_month <= 12:
        raise ValueError(
            "Drop month must be between 1 and 12."
        )

    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()

    cursor = connection.cursor()

    try:
        deleted_rows = (
            _delete_unpaid_records_after_boundary(
                cursor=cursor,
                student_id=student_id,
                boundary_month=drop_month,
                boundary_year=drop_year,
            )
        )

        if owns_connection:
            connection.commit()

        return deleted_rows

    except Exception:
        if owns_connection:
            connection.rollback()
        raise

    finally:
        if owns_connection:
            connection.close()


def remove_unpaid_fee_records_for_archive(
    student_id: int,
    connection=None,
) -> int:
    """Remove every unreceipted Unpaid fee row when a student is archived."""
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            DELETE FROM student_fee_records
            WHERE student_id = ?
              AND status = ?
              AND NOT EXISTS (
                    SELECT 1
                    FROM fee_receipts AS receipts
                    INNER JOIN fee_payments AS payments
                      ON payments.id = receipts.payment_id
                    WHERE receipts.student_fee_record_id = student_fee_records.id
                      AND receipts.status = 'Paid'
                      AND payments.payment_status = 'Completed'
              )
            """,
            (int(student_id), FEE_STATUS_UNPAID),
        )
        deleted_rows = int(cursor.rowcount or 0)
        if owns_connection:
            connection.commit()
        return deleted_rows
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def _get_latest_drop_boundary(
    cursor,
    student_id: int,
    current_class_id: int,
):
    """
    Read the latest status-pause marker.

    Current drop transitions are recorded in student_status_history.  The
    student_class_history lookup is retained only for older databases whose
    drop workflow used a same-class MANUAL marker.
    """
    cursor.execute(
        """
        SELECT effective_datetime
        FROM student_status_history
        WHERE student_id = ?
          AND event_type = 'DROP'
          AND new_status = ?
          AND related_class_id = ?
        ORDER BY effective_datetime DESC, id DESC
        LIMIT 1
        """,
        (
            int(student_id),
            STATUS_DROPPED,
            int(current_class_id),
        ),
    )

    status_row = cursor.fetchone()
    if status_row:
        try:
            dropped_at = datetime.fromisoformat(
                str(status_row["effective_datetime"])
            )
            return dropped_at.month, dropped_at.year
        except (TypeError, ValueError):
            # Fall through to the legacy marker when an old database contains
            # a malformed status-history timestamp.
            pass

    cursor.execute(
        """
        SELECT
            effective_month,
            effective_year
        FROM student_class_history
        WHERE student_id = ?
          AND old_class_id IS NULL
          AND new_class_id = ?
          AND change_type = 'MANUAL'
        ORDER BY
            effective_year DESC,
            effective_month DESC,
            history_id DESC
        LIMIT 1
        """,
        (
            int(student_id),
            int(current_class_id),
        ),
    )

    row = cursor.fetchone()

    if not row:
        return None

    return (
        int(row["effective_month"]),
        int(row["effective_year"]),
    )


def generate_reactivation_fee_records(
    student_id: int,
    class_id: int,
    reactivation_month: int,
    reactivation_year: int,
    connection=None,
) -> None:
    """Generate reactivation fees while preserving only evidenced payments."""
    student_id = int(student_id)
    class_id = int(class_id)
    reactivation_month = int(reactivation_month)
    reactivation_year = int(reactivation_year)
    if not 1 <= reactivation_month <= 12:
        raise ValueError("Reactivation month must be between 1 and 12.")

    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    cursor = connection.cursor()
    now = _format_now()
    session_start_year = _month_session_start_year(
        reactivation_month, reactivation_year
    )
    session_label = get_session_year_label(session_start_year)
    end_month, end_year = 3, session_start_year + 1

    try:
        iter_month, iter_year = reactivation_month, reactivation_year
        while (iter_year, iter_month) <= (end_year, end_month):
            effective_fee = get_effective_fee(
                cursor=cursor,
                class_id=class_id,
                student_id=student_id,
                month_number=iter_month,
                year=iter_year,
            )
            amount = round(float(effective_fee["monthly_fee"]), 2)
            target_status = FEE_STATUS_WAIVED if amount == 0 else FEE_STATUS_UNPAID
            existing = cursor.execute(
                """
                SELECT records.id, records.status, records.amount, records.paid_date,
                       EXISTS(
                           SELECT 1 FROM fee_receipts AS receipts
                           JOIN fee_payments AS payments
                             ON payments.id=receipts.payment_id
                           WHERE receipts.student_fee_record_id=records.id
                             AND receipts.status='Paid'
                             AND payments.payment_status='Completed'
                       ) AS has_completed_payment
                FROM student_fee_records AS records
                WHERE records.student_id=? AND records.fee_type=?
                  AND records.month_number=? AND records.year=?
                ORDER BY records.id ASC LIMIT 1
                """,
                (student_id, FEE_TYPE_MONTHLY, iter_month, iter_year),
            ).fetchone()

            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO student_fee_records(
                        student_id,class_id,session_year,fee_type,
                        month_name,month_number,year,amount,status,
                        paid_date,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,NULL,?,?)
                    """,
                    (
                        student_id, class_id, session_label, FEE_TYPE_MONTHLY,
                        MONTH_NUMBER_TO_NAME[iter_month], iter_month, iter_year,
                        amount, target_status, now, now,
                    ),
                )
            else:
                has_payment = bool(existing["has_completed_payment"])
                status = str(existing["status"] or "")
                old_amount = round(float(existing["amount"] or 0), 2)
                if status == FEE_STATUS_PAID and has_payment:
                    # Genuine payment-backed history is immutable.
                    pass
                elif status == FEE_STATUS_PAID and old_amount > 0:
                    raise ValueError(
                        "A positive Paid fee row has no completed payment or receipt "
                        f"evidence (fee record {existing['id']}). Manual financial "
                        "reconciliation is required before reactivation."
                    )
                elif status in {FEE_STATUS_UNPAID, FEE_STATUS_WAIVED, FEE_STATUS_PAID}:
                    cursor.execute(
                        """
                        UPDATE student_fee_records
                        SET class_id=?, session_year=?, amount=?, status=?,
                            paid_date=NULL, updated_at=?
                        WHERE id=?
                        """,
                        (
                            class_id, session_label, amount, target_status,
                            now, int(existing["id"]),
                        ),
                    )
                else:
                    raise ValueError(
                        f"Unsupported fee status {status!r} for reactivation."
                    )

            if iter_month == 12:
                iter_month, iter_year = 1, iter_year + 1
            else:
                iter_month += 1

        _mark_zero_amount_fee_records_waived(cursor, student_id, now)
        if owns_connection:
            connection.commit()
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()



# CLASS10_SESSION_END_FEE_REPAIR_V17
# Class-10 completion retains all payable rows through the final March
# of the academic session in which completion occurred.
CLASS10_SESSION_END_FEE_REPAIR_V17 = True


def _sync_class10_completion_status_from_fee_records(
    cursor,
    student_id: int,
    now: str,
) -> bool:
    """
    Keep Class-10 completion status consistent with generated fee rows.

    A student with any unpaid Monthly Fee remains Partially Graduated.
    Once no unpaid Monthly Fee remains, the student is Graduated.
    Only students with a non-reversed graduation record are adjusted.
    """
    active_graduation = _single_active_graduation_record(
        cursor, student_id, required=False
    )
    if active_graduation is None:
        return False

    cursor.execute(
        """
        SELECT 1
        FROM student_fee_records
        WHERE student_id = ?
          AND fee_type = ?
          AND status = ?
        LIMIT 1
        """,
        (
            int(student_id),
            FEE_TYPE_MONTHLY,
            FEE_STATUS_UNPAID,
        ),
    )

    desired_status = (
        STATUS_PARTIALLY_GRADUATED
        if cursor.fetchone() is not None
        else STATUS_GRADUATED
    )

    cursor.execute(
        """
        SELECT student_status
        FROM students
        WHERE student_id = ?
        """,
        (int(student_id),),
    )
    student_row = cursor.fetchone()

    if (
        not student_row
        or str(student_row["student_status"] or "").strip()
        not in {
            STATUS_GRADUATED,
            STATUS_PARTIALLY_GRADUATED,
        }
    ):
        return False

    changed = (
        str(student_row["student_status"] or "").strip()
        != desired_status
    )

    if changed:
        cursor.execute(
            """
            UPDATE students
            SET student_status = ?,
                updated_at = ?,
                updated_by = ?
            WHERE student_id = ?
            """,
            (
                desired_status,
                now,
                "fee_completion_repair",
                int(student_id),
            ),
        )

    cursor.execute(
        """
        UPDATE graduation_records
        SET status = ?
        WHERE graduated_record_id = ?
          AND status <> ?
        """,
        (
            desired_status,
            int(active_graduation["graduated_record_id"]),
            desired_status,
        ),
    )

    return changed


# MONTHLY_ONLY_FEE_RECORD_GENERATION_V8
# Fee-record generation now creates Monthly Fee rows only.


def preview_fee_records_generation(
    student_id: int,
    connection,
    as_of=None,
) -> dict:
    """
    Simulate Monthly Fee generation without inserting, updating, or deleting.

    This is used only by Full-Class Promotion Preview. It validates every
    required fee configuration and reports what the confirmed transaction
    would create while keeping the database completely read-only.
    """
    student_id = int(student_id)
    cursor = connection.cursor()
    cursor.execute(
        "SELECT * FROM students WHERE student_id = ?",
        (student_id,),
    )
    student_row = cursor.fetchone()
    if student_row is None:
        raise ValueError("Student not found.")

    class_id = int(student_row["class_id"])
    cursor.execute("SELECT * FROM classes WHERE id = ?", (class_id,))
    class_row = cursor.fetchone()
    if class_row is None:
        raise ValueError("The student's current class was not found.")

    reference = as_of or datetime.now()
    start_month, start_year = _determine_fee_start_month_year(
        cursor,
        student_row,
    )
    current_session_start = _month_session_start_year(
        int(reference.month),
        int(reference.year),
    )
    end_month = 3
    end_year = current_session_start + 1

    missing_rows = []
    simulated_unpaid = False
    iter_month = int(start_month)
    iter_year = int(start_year)
    while (iter_year, iter_month) <= (end_year, end_month):
        session_start_year = _month_session_start_year(iter_month, iter_year)
        session_label = get_session_year_label(session_start_year)
        cursor.execute(
            """
            SELECT status, amount
            FROM student_fee_records
            WHERE student_id = ?
              AND fee_type = ?
              AND month_number = ?
              AND year = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (
                student_id,
                FEE_TYPE_MONTHLY,
                iter_month,
                iter_year,
            ),
        )
        existing = cursor.fetchone()
        if existing is None:
            try:
                effective_fee = get_effective_fee(
                    cursor=cursor,
                    class_id=class_id,
                    student_id=student_id,
                    month_number=iter_month,
                    year=iter_year,
                )
            except ValueError as error:
                month_name = MONTH_NUMBER_TO_NAME[int(iter_month)]
                raise ValueError(
                    "Monthly Fee configuration is missing for "
                    f"{class_row['class_name']}, {month_name} {iter_year} "
                    f"(session {session_label}). Set the class fee for "
                    "this exact academic period before continuing. "
                    "The preview was stopped and no fee rows were created. "
                    f"Details: {error}"
                ) from error

            monthly_amount = round(float(effective_fee["monthly_fee"]), 2)
            missing_rows.append(
                {
                    "month_number": int(iter_month),
                    "year": int(iter_year),
                    "month_name": MONTH_NUMBER_TO_NAME[int(iter_month)],
                    "session_year": session_label,
                    "class_id": class_id,
                    "class_name": class_row["class_name"],
                    "amount": monthly_amount,
                }
            )
            if monthly_amount > 0:
                simulated_unpaid = True
        elif str(existing["status"] or "").strip() == FEE_STATUS_UNPAID:
            simulated_unpaid = True

        if iter_month == 12:
            iter_month = 1
            iter_year += 1
        else:
            iter_month += 1

    return {
        "student_id": student_id,
        "class_id": class_id,
        "class_name": class_row["class_name"],
        "missing_count": len(missing_rows),
        "missing_rows": missing_rows,
        "would_have_unpaid_monthly_fee": bool(simulated_unpaid),
    }


def ensure_fee_records_generated(
    student_id: int,
    connection=None,
    as_of=None,
) -> None:
    """
    Generate every missing Monthly Fee record through the final
    March of the current academic session.

    Existing records and receipt-backed Paid statuses are never
    changed or deleted. Only Monthly Fee records are generated.
    """
    student_id = int(
        student_id
    )

    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()

    cursor = connection.cursor()

    try:
        if owns_connection:
            connection.execute("BEGIN IMMEDIATE")

        cursor.execute(
            """
            SELECT *
            FROM students
            WHERE student_id = ?
            """,
            (
                student_id,
            ),
        )

        student_row = cursor.fetchone()

        if not student_row:
            raise ValueError(
                "Student not found."
            )

        class_id = int(
            student_row["class_id"]
        )

        cursor.execute(
            """
            SELECT *
            FROM classes
            WHERE id = ?
            """,
            (
                class_id,
            ),
        )

        class_row = cursor.fetchone()

        if not class_row:
            raise ValueError(
                (
                    "The student's current class "
                    "was not found."
                )
            )

        reference = as_of or datetime.now()
        now = reference.strftime("%Y-%m-%d %H:%M:%S")
        today = reference

        start_month, start_year = (
            _determine_fee_start_month_year(
                cursor,
                student_row,
            )
        )

        start_month = int(
            start_month
        )

        start_year = int(
            start_year
        )

        current_session_start = (
            _month_session_start_year(
                today.month,
                today.year,
            )
        )

        end_month = 3
        end_year = (
            current_session_start + 1
        )

        student_status = str(
            student_row["student_status"]
            or ""
        ).strip()

        # Pending coming-session admissions have not joined the payable
        # session yet. Activation on/after 1 April changes them to Active and
        # adds the April fee-start candidate above.
        if student_status == STATUS_COMING_SESSION:
            if owns_connection:
                connection.commit()
            return

        # Archived students remain available only as historical records in
        # Student Management Search. They never receive new fee rows.
        if student_status == STATUS_ARCHIVED:
            if owns_connection:
                connection.commit()
            return

        # Dropped students keep fees through the month in which they left.
        # Later unpaid months are removed and cannot be regenerated while
        # the student remains inactive.
        if student_status == "Dropped":
            drop_boundary = _get_latest_drop_boundary(
                cursor=cursor,
                student_id=student_id,
                current_class_id=class_id,
            )

            if drop_boundary:
                drop_month, drop_year = drop_boundary

                if (
                    drop_year,
                    drop_month,
                ) < (
                    end_year,
                    end_month,
                ):
                    end_year = drop_year
                    end_month = drop_month

                _delete_unpaid_records_after_boundary(
                    cursor=cursor,
                    student_id=student_id,
                    boundary_month=drop_month,
                    boundary_year=drop_year,
                )

        # Both completion statuses stop at the final March of the completed
        # Class 10 session. Partially Graduated students must not acquire
        # next-session fees until they are explicitly reactivated.
        if (
            str(class_row["class_level"]).strip()
            == "10"
            and student_status
            in {
                STATUS_GRADUATED,
                STATUS_PARTIALLY_GRADUATED,
            }
        ):
            cursor.execute(
                """
                SELECT graduation_date
                FROM graduation_records
                WHERE student_id = ?
                  AND is_reversed = 0
                ORDER BY graduation_date DESC
                LIMIT 1
                """,
                (
                    student_id,
                ),
            )

            graduation_row = cursor.fetchone()

            if graduation_row:
                graduated_at = datetime.fromisoformat(
                    graduation_row["graduation_date"]
                )

                graduation_session_start_year = (
                    _month_session_start_year(
                        graduated_at.month,
                        graduated_at.year,
                    )
                )
                graduation_end_year = (
                    graduation_session_start_year + 1
                )
                graduation_end_month = 3

                if (
                    graduation_end_year,
                    graduation_end_month,
                ) < (
                    end_year,
                    end_month,
                ):
                    end_year = graduation_end_year
                    end_month = graduation_end_month

                _delete_unpaid_records_after_boundary(
                    cursor=cursor,
                    student_id=student_id,
                    boundary_month=graduation_end_month,
                    boundary_year=graduation_end_year,
                )

        iter_month = start_month
        iter_year = start_year

        while (
            iter_year,
            iter_month,
        ) <= (
            end_year,
            end_month,
        ):
            session_start_year = (
                _month_session_start_year(
                    iter_month,
                    iter_year,
                )
            )

            session_label = (
                get_session_year_label(
                    session_start_year
                )
            )

            cursor.execute(
                """
                SELECT 1
                FROM student_fee_records
                WHERE student_id = ?
                  AND fee_type = ?
                  AND month_number = ?
                  AND year = ?
                LIMIT 1
                """,
                (
                    student_id,
                    FEE_TYPE_MONTHLY,
                    iter_month,
                    iter_year,
                ),
            )

            if cursor.fetchone() is None:
                try:
                    effective_fee = (
                        get_effective_fee(
                            cursor=cursor,
                            class_id=class_id,
                            student_id=(
                                student_id
                            ),
                            month_number=(
                                iter_month
                            ),
                            year=iter_year,
                        )
                    )

                except ValueError as error:
                    month_name = MONTH_NUMBER_TO_NAME[int(iter_month)]
                    raise ValueError(
                        (
                            "Monthly Fee configuration is missing for "
                            f"{class_row['class_name']}, {month_name} {iter_year} "
                            f"(session {session_label}). Set the class fee for "
                            "this exact academic period before continuing. "
                            "The operation was stopped and no partial new fee "
                            "rows will be saved. "
                            f"Details: {error}"
                        )
                    ) from error

                monthly_amount = round(
                    float(
                        effective_fee[
                            "monthly_fee"
                        ]
                    ),
                    2,
                )

                cursor.execute(
                    """
                    INSERT INTO student_fee_records (
                        student_id,
                        class_id,
                        session_year,
                        fee_type,
                        month_name,
                        month_number,
                        year,
                        amount,
                        status,
                        paid_date,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        NULL, ?, ?
                    )
                    """,
                    (
                        student_id,
                        class_id,
                        session_label,
                        FEE_TYPE_MONTHLY,
                        MONTH_NUMBER_TO_NAME[
                            iter_month
                        ],
                        iter_month,
                        iter_year,
                        monthly_amount,
                        FEE_STATUS_UNPAID,
                        now,
                        now,
                    ),
                )

            if iter_month == 12:
                iter_month = 1
                iter_year += 1

            else:
                iter_month += 1

        _mark_zero_amount_fee_records_waived(
            cursor=cursor,
            student_id=student_id,
            now=now,
        )

        if (
            str(class_row["class_level"]).strip() == "10"
            and student_status
            in {
                STATUS_GRADUATED,
                STATUS_PARTIALLY_GRADUATED,
            }
        ):
            _sync_class10_completion_status_from_fee_records(
                cursor=cursor,
                student_id=student_id,
                now=now,
            )

        if owns_connection:
            connection.commit()

    except Exception:
        if owns_connection:
            connection.rollback()
        raise

    finally:
        if owns_connection:
            connection.close()



# ---------------------------------------------------------------------------
# Fee table (Pay Fee screen)
# ---------------------------------------------------------------------------

# MONTHLY_ONLY_PAYMENT_PIPELINE_V8
# Fee history, selection, preview and payment now process Monthly Fee records only.

def get_fee_table_for_student(
    student_id: int,
):
    """
    Ensure Monthly Fee records exist and return the student's
    complete Monthly Fee history.

    Every row retains its historical class, academic session,
    amount, payment status, payment date and receipt number.
    """
    student_id = int(
        student_id
    )

    ensure_fee_records_generated(
        student_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                records.*,

                classes.class_name
                    AS historical_class_name,

                payments.receipt_no
                    AS receipt_no,

                COALESCE(
                    payments.payment_date,
                    records.paid_date
                ) AS payment_date

            FROM student_fee_records
                AS records

            LEFT JOIN classes
              ON classes.id =
                    records.class_id

            LEFT JOIN fee_receipts
                AS receipts
              ON receipts.id = (
                    SELECT MAX(
                        receipt_link.id
                    )

                    FROM fee_receipts
                        AS receipt_link

                    INNER JOIN fee_payments
                        AS payment_link
                      ON payment_link.id =
                            receipt_link.payment_id

                    WHERE
                        receipt_link.student_fee_record_id
                        = records.id
                      AND receipt_link.status = ?
                      AND payment_link.payment_status = ?
                )

            LEFT JOIN fee_payments
                AS payments
              ON payments.id =
                    receipts.payment_id
             AND payments.payment_status = ?

            WHERE records.student_id = ?
              AND records.fee_type = ?

            ORDER BY
                CAST(
                    SUBSTR(
                        session_year,
                        1,
                        4
                    ) AS INTEGER
                ) ASC,

                CASE
                    WHEN records.month_number >= ?
                        THEN records.month_number
                    ELSE records.month_number + 12
                END ASC,

                records.year ASC,
                records.id ASC
            """,
            (
                FEE_STATUS_PAID,
                PAYMENT_STATUS_COMPLETED,
                PAYMENT_STATUS_COMPLETED,
                student_id,
                FEE_TYPE_MONTHLY,
                SESSION_START_CALENDAR_MONTH,
            ),
        )

        return cursor.fetchall()

    finally:
        connection.close()




def validate_payment_selection(
    student_id: int,
    selected_record_ids: list,
) -> bool:
    """
    Payment must contain exactly the first N unpaid Monthly Fee
    records in April-to-March academic order.
    """
    if not selected_record_ids:
        return False

    try:
        normalised_record_ids = [
            int(record_id)
            for record_id
            in selected_record_ids
        ]

    except (
        TypeError,
        ValueError,
    ):
        return False

    if (
        len(normalised_record_ids)
        != len(
            set(
                normalised_record_ids
            )
        )
    ):
        return False

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                id,
                status,
                session_year

            FROM student_fee_records

            WHERE student_id = ?
              AND fee_type = ?

            ORDER BY
                CAST(
                    SUBSTR(
                        session_year,
                        1,
                        4
                    ) AS INTEGER
                ) ASC,

                CASE
                    WHEN month_number >= ?
                        THEN month_number
                    ELSE month_number + 12
                END ASC,

                year ASC,
                id ASC
            """,
            (
                int(student_id),
                FEE_TYPE_MONTHLY,
                SESSION_START_CALENDAR_MONTH,
            ),
        )

        rows = cursor.fetchall()

    finally:
        connection.close()

    unpaid_ids_in_order = [
        int(
            row["id"]
        )
        for row in rows
        if row["status"]
        == FEE_STATUS_UNPAID
    ]

    if not unpaid_ids_in_order:
        return False

    expected_prefix = (
        unpaid_ids_in_order[
            :len(
                normalised_record_ids
            )
        ]
    )

    selected_sessions = {
        str(row["session_year"] or "").strip()
        for row in rows
        if int(row["id"]) in set(normalised_record_ids)
    }
    if len(selected_sessions) != 1:
        return False

    return (
        len(normalised_record_ids)
        == len(expected_prefix)
        and set(
            normalised_record_ids
        )
        == set(
            expected_prefix
        )
    )




# ---------------------------------------------------------------------------
# Payment processing (Pay button + Receipt)
# ---------------------------------------------------------------------------
def get_payment_preview(
    student_id: int,
    selected_record_ids: list,
) -> dict:
    """Return an immutable-history preview without saving a payment."""
    student_id = int(student_id)
    if not selected_record_ids:
        raise ValueError("No fee items were selected.")

    normalised_record_ids = [int(record_id) for record_id in selected_record_ids]
    if not validate_payment_selection(student_id, normalised_record_ids):
        raise ValueError("The selected fees are not in the correct payment order.")

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT students.*, classes.class_name
            FROM students
            LEFT JOIN classes ON classes.id = students.class_id
            WHERE students.student_id = ?
            """,
            (student_id,),
        )
        student_row = cursor.fetchone()
        if not student_row:
            raise ValueError("Student not found.")

        placeholders = ",".join("?" for _ in normalised_record_ids)
        cursor.execute(
            f"""
            SELECT records.*, classes.class_name AS historical_class_name
            FROM student_fee_records AS records
            LEFT JOIN classes ON classes.id = records.class_id
            WHERE records.student_id = ?
              AND records.fee_type = ?
              AND records.id IN ({placeholders})
            ORDER BY
                CAST(SUBSTR(records.session_year, 1, 4) AS INTEGER) ASC,
                CASE WHEN records.month_number >= ?
                     THEN records.month_number ELSE records.month_number + 12 END ASC,
                records.year ASC,
                records.id ASC
            """,
            [student_id, FEE_TYPE_MONTHLY, *normalised_record_ids,
             SESSION_START_CALENDAR_MONTH],
        )
        selected_rows = cursor.fetchall()
        if len(selected_rows) != len(normalised_record_ids):
            raise ValueError("One or more selected fee records could not be found.")

        fee_items = []
        total_amount = 0.0
        historical_classes = []
        for row in selected_rows:
            amount = round(float(row["amount"]), 2)
            if not math.isfinite(amount):
                raise ValueError("A selected Monthly Fee has an invalid amount.")
            class_name = str(row["historical_class_name"] or "-")
            historical_classes.append(class_name)
            total_amount += amount
            fee_items.append({
                "item_name": f"{row['month_name']} {row['year']}",
                "session_year": row["session_year"],
                "class_name": class_name,
                "amount": amount,
            })

        distinct_classes = list(dict.fromkeys(historical_classes))
        distinct_sessions = {
            str(row["session_year"] or "").strip()
            for row in selected_rows
        }
        if len(distinct_sessions) != 1:
            raise ValueError(
                "One receipt can contain fees from only one academic session."
            )
        header_class = (
            distinct_classes[0]
            if len(distinct_classes) == 1
            else "MULTIPLE CLASSES"
        )
        return {
            "student_id": student_id,
            "tracking_id": student_row["tracking_id"] or "-",
            "admission_number": student_row["admission_number"],
            "roll_number": student_row["roll_number"],
            "student_name": student_row["student_name"],
            "father_name": student_row["father_name"],
            "father_cnic": student_row["father_cnic"] or "-",
            "class_name": header_class,
            "student_status": student_row["student_status"],
            "session_year": next(iter(distinct_sessions)),
            "fee_items": fee_items,
            "total_amount": round(total_amount, 2),
        }
    finally:
        connection.close()

# GAP_SAFE_RECEIPT_NUMBER_GENERATION_V8
def _generate_receipt_number(cursor, now: str | None = None) -> str:
    """Reserve the next permanent receipt number inside the payment transaction."""
    sequence_timestamp = now or _format_now()
    cursor.execute(
        """
        INSERT INTO receipt_number_sequence(
            id, last_issued_number, updated_at, updated_by
        ) VALUES(1, 0, ?, 'payment_service')
        ON CONFLICT(id) DO NOTHING
        """,
        (sequence_timestamp,),
    )
    cursor.execute(
        "SELECT last_issued_number FROM receipt_number_sequence WHERE id = 1"
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError("Receipt Number sequence is unavailable.")
    stored_number = int(row["last_issued_number"] or 0)
    cursor.execute(
        """
        SELECT COALESCE(MAX(CAST(receipt_no AS INTEGER)), 0) AS highest_receipt
        FROM fee_payments
        WHERE TRIM(receipt_no)<>''
          AND TRIM(receipt_no) NOT GLOB '*[^0-9]*'
        """
    )
    highest_row = cursor.fetchone()
    highest_existing = int(highest_row["highest_receipt"] or 0)
    # A restored/legacy database can have a stale sequence row even though its
    # permanent receipt history is valid. Never reuse a historical number.
    # Reconcile inside the same payment transaction, then reserve max + 1.
    next_number = max(stored_number, highest_existing, 0) + 1
    cursor.execute(
        """
        UPDATE receipt_number_sequence
        SET last_issued_number = ?, updated_at = ?, updated_by = 'payment_service'
        WHERE id = 1 AND last_issued_number = ?
        """,
        (next_number, sequence_timestamp, stored_number),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("Receipt Number could not be reserved safely.")
    return str(next_number).zfill(6)



def build_payment_session_label(rows, key="session_year") -> str:
    """Return one immutable session label for one payment result."""
    sessions = sorted({
        str(row[key] or "").strip()
        for row in rows
        if str(row[key] or "").strip() and str(row[key] or "").strip() != "-"
    })
    if len(sessions) == 1:
        return sessions[0]
    if len(sessions) > 1:
        return "MULTIPLE SESSIONS"
    return "-"


def _build_session_payment_history_snapshot(
    cursor,
    *,
    student_id: int,
    session_year: str,
) -> list[dict]:
    """Capture the receipt's immutable April-March paid/waived history."""
    session_year = str(session_year or "").strip()
    try:
        session_start_year = int(session_year[:4])
    except (TypeError, ValueError):
        return []

    paid_rows = cursor.execute(
        """
        SELECT receipts.month_number_snapshot AS month_number,
               receipts.year_snapshot AS calendar_year,
               SUM(receipts.amount) AS paid_amount,
               MAX(payments.payment_date) AS paid_date
        FROM fee_receipts AS receipts
        INNER JOIN fee_payments AS payments ON payments.id=receipts.payment_id
        WHERE payments.student_id=?
          AND payments.payment_status=?
          AND receipts.status=?
          AND receipts.session_year_snapshot=?
        GROUP BY receipts.month_number_snapshot, receipts.year_snapshot
        """,
        (student_id, PAYMENT_STATUS_COMPLETED, FEE_STATUS_PAID, session_year),
    ).fetchall()
    paid_by_month = {
        int(row["month_number"]): row
        for row in paid_rows
        if row["month_number"] is not None
    }
    waived_months = {
        int(row["month_number"])
        for row in cursor.execute(
            """
            SELECT month_number FROM student_fee_records
            WHERE student_id=? AND fee_type=? AND session_year=? AND status=?
            """,
            (student_id, FEE_TYPE_MONTHLY, session_year, FEE_STATUS_WAIVED),
        ).fetchall()
        if row["month_number"] is not None
    }

    history = []
    for _month_name, month_number in SESSION_MONTH_ORDER:
        calendar_year = (
            session_start_year
            if int(month_number) >= SESSION_START_CALENDAR_MONTH
            else session_start_year + 1
        )
        paid = paid_by_month.get(int(month_number))
        history.append({
            "month_number": int(month_number),
            "year": int(calendar_year),
            "status": "Paid" if paid else "Waived" if int(month_number) in waived_months else "",
            "amount": round(float(paid["paid_amount"]), 2) if paid else None,
            "paid_date": str(paid["paid_date"] or "") if paid else "",
        })
    return history

def normalize_payment_receipt_number(
    receipt_number,
) -> str:
    """Validate user input without rewriting the stored legacy value."""
    cleaned = str(receipt_number if receipt_number is not None else "").strip()
    if not cleaned:
        raise ValueError("Please enter a receipt number.")
    if not cleaned.isdigit():
        raise ValueError("Receipt Number must contain numeric digits only.")
    if int(cleaned) <= 0:
        raise ValueError("Receipt Number must be greater than zero.")
    return cleaned


def get_completed_payment_by_receipt_number(
    receipt_number,
) -> dict:
    """Load one unambiguous legacy or padded receipt from snapshots."""
    entered_receipt_number = normalize_payment_receipt_number(receipt_number)
    numeric_value = int(entered_receipt_number)
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT *
            FROM fee_payments
            WHERE TRIM(receipt_no) = ?
               OR (
                    TRIM(receipt_no) <> ''
                    AND TRIM(receipt_no) NOT GLOB '*[^0-9]*'
                    AND CAST(TRIM(receipt_no) AS INTEGER) = ?
               )
            ORDER BY id ASC
            """,
            (entered_receipt_number, numeric_value),
        )
        candidate_rows = cursor.fetchall()
        if not candidate_rows:
            raise ValueError("Receipt Number was not found.")
        if len(candidate_rows) > 1:
            raise ValueError(
                f"Multiple receipt records correspond to Receipt Number "
                f"{numeric_value}. Manual reconciliation is required."
            )

        payment_row = candidate_rows[0]
        stored_receipt_number = str(payment_row["receipt_no"] or "").strip()
        payment_status = str(
            payment_row["payment_status"] or PAYMENT_STATUS_COMPLETED
        )
        if payment_status != PAYMENT_STATUS_COMPLETED:
            raise ValueError(
                "This receipt was cancelled and cannot be printed or downloaded."
            )

        cursor.execute(
            """
            SELECT item_name, amount, status,
                   class_name_snapshot, session_year_snapshot,
                   month_number_snapshot, year_snapshot
            FROM fee_receipts
            WHERE payment_id = ? AND status = ?
            ORDER BY id ASC
            """,
            (int(payment_row["id"]), FEE_STATUS_PAID),
        )
        receipt_rows = cursor.fetchall()
        if not receipt_rows:
            raise ValueError("This completed payment has no active receipt items.")

        paid_items = [
            {
                "item_name": row["item_name"],
                "session_year": row["session_year_snapshot"] or "-",
                "class_name": row["class_name_snapshot"] or "-",
                "amount": round(float(row["amount"]), 2),
            }
            for row in receipt_rows
        ]

        raw_payment_date = str(payment_row["payment_date"] or "").strip()
        try:
            display_payment_date = datetime.strptime(
                raw_payment_date[:10], "%Y-%m-%d"
            ).strftime("%d-%m-%Y")
        except ValueError:
            display_payment_date = raw_payment_date or "-"

        return {
            "payment_id": int(payment_row["id"]),
            "receipt_no": stored_receipt_number,
            "payment_date": display_payment_date,
            "tracking_id": payment_row["tracking_id_snapshot"] or "-",
            "admission_number": payment_row["admission_number_snapshot"] or "-",
            "roll_number": payment_row["roll_number_snapshot"],
            "student_id": int(payment_row["student_id"]),
            "student_name": payment_row["student_name_snapshot"] or "-",
            "father_name": payment_row["father_name_snapshot"] or "-",
            "father_cnic": payment_row["father_cnic_snapshot"] or "-",
            "class_name": payment_row["class_name_snapshot"] or "-",
            "session_year": build_payment_session_label(
                receipt_rows, key="session_year_snapshot"
            ),
            "paid_items": paid_items,
            "total_paid": round(float(payment_row["total_amount"]), 2),
            "received_by": payment_row["received_by"],
            "student_archived": bool(payment_row["auto_archived"] or 0),
            "auto_graduated": bool(payment_row["auto_graduated"] or 0),
            "student_status_before": payment_row["student_status_before"],
            "student_status_after": payment_row["student_status_after"],
            "payment_status": payment_status,
            "historical_receipt": True,
            "session_history": [
                {
                    "month_number": int(row["month_number"]),
                    "year": int(row["calendar_year"]),
                    "status": row["history_status"],
                    "amount": row["paid_amount"],
                    "paid_date": row["paid_date"] or "",
                }
                for row in cursor.execute(
                    """
                    SELECT month_number, calendar_year, history_status,
                           paid_amount, paid_date
                    FROM fee_payment_session_history
                    WHERE payment_id=?
                    ORDER BY CASE WHEN month_number>=? THEN month_number
                                  ELSE month_number+12 END
                    """,
                    (int(payment_row["id"]), SESSION_START_CALENDAR_MONTH),
                ).fetchall()
            ],
        }
    finally:
        connection.close()


def process_payment(
    student_id: int,
    selected_record_ids: list,
    received_by: str = "admin",
) -> dict:
    """
    Validate and permanently process one Monthly Fee payment.

    Security and integrity rules:
    - selected records must belong to this student;
    - every selected record must be a Monthly Fee;
    - selected records must all still be Unpaid;
    - selected records must be exactly the first N unpaid months;
    - already-Paid records cannot be paid again;
    - one fee record cannot create duplicate receipt entries;
    - validation and payment are performed atomically.
    """
    student_id = int(
        student_id
    )

    if not selected_record_ids:
        raise ValueError(
            "No fee items were selected."
        )

    try:
        normalised_record_ids = [
            int(record_id)
            for record_id
            in selected_record_ids
        ]

    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            (
                "One or more selected fee "
                "records are invalid."
            )
        ) from error

    if any(
        record_id <= 0
        for record_id
        in normalised_record_ids
    ):
        raise ValueError(
            (
                "One or more selected fee "
                "records are invalid."
            )
        )

    if (
        len(normalised_record_ids)
        != len(
            set(
                normalised_record_ids
            )
        )
    ):
        raise ValueError(
            (
                "Duplicate fee items cannot "
                "be selected."
            )
        )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            "BEGIN IMMEDIATE"
        )

        payment_datetime = datetime.now()
        ensure_financial_clock_safe(
            connection,
            operation_name="receiving a fee payment",
            as_of=payment_datetime,
        )
        now = payment_datetime.strftime("%Y-%m-%d %H:%M:%S")
        today_str = payment_datetime.strftime("%d-%m-%Y")

        cursor.execute(
            """
            SELECT *
            FROM students
            WHERE student_id = ?
            """,
            (
                student_id,
            ),
        )

        student_row = cursor.fetchone()

        if student_row is None:
            raise ValueError(
                "Student not found."
            )

        student_status_before = str(
            student_row["student_status"]
        )

        cursor.execute(
            """
            SELECT *
            FROM classes
            WHERE id = ?
            """,
            (
                student_row[
                    "class_id"
                ],
            ),
        )

        class_row = cursor.fetchone()

        if class_row is None:
            raise ValueError(
                (
                    "Student class was "
                    "not found."
                )
            )

        # Read every currently unpaid month in the
        # authoritative April-to-March order.
        cursor.execute(
            """
            SELECT id

            FROM student_fee_records

            WHERE student_id = ?
              AND fee_type = ?
              AND status = ?

            ORDER BY
                CAST(
                    SUBSTR(
                        session_year,
                        1,
                        4
                    ) AS INTEGER
                ) ASC,

                CASE
                    WHEN month_number >= ?
                        THEN month_number
                    ELSE month_number + 12
                END ASC,

                year ASC,
                id ASC
            """,
            (
                student_id,
                FEE_TYPE_MONTHLY,
                FEE_STATUS_UNPAID,
                SESSION_START_CALENDAR_MONTH,
            ),
        )

        unpaid_ids_in_order = [
            int(
                row["id"]
            )
            for row in cursor.fetchall()
        ]

        expected_prefix = (
            unpaid_ids_in_order[
                :len(
                    normalised_record_ids
                )
            ]
        )

        if (
            len(expected_prefix)
            != len(normalised_record_ids)
            or set(
                normalised_record_ids
            )
            != set(
                expected_prefix
            )
        ):
            raise ValueError(
                (
                    "The selected fees are not "
                    "in the correct payment order."
                )
            )

        placeholders = ",".join(
            "?"
            for _ in normalised_record_ids
        )

        cursor.execute(
            f"""
            SELECT
                records.*,
                classes.class_name AS historical_class_name

            FROM student_fee_records AS records

            LEFT JOIN classes
              ON classes.id = records.class_id

            WHERE records.student_id = ?
              AND records.fee_type = ?
              AND records.status = ?
              AND records.id IN ({placeholders})

            ORDER BY
                CAST(
                    SUBSTR(
                        records.session_year,
                        1,
                        4
                    ) AS INTEGER
                ) ASC,

                CASE
                    WHEN records.month_number >= ?
                        THEN records.month_number
                    ELSE records.month_number + 12
                END ASC,

                records.year ASC,
                records.id ASC
            """,
            [
                student_id,
                FEE_TYPE_MONTHLY,
                FEE_STATUS_UNPAID,
                *normalised_record_ids,
                SESSION_START_CALENDAR_MONTH,
            ],
        )

        selected_rows = cursor.fetchall()

        if (
            len(selected_rows)
            != len(normalised_record_ids)
        ):
            raise ValueError(
                (
                    "One or more selected fee "
                    "records do not belong to "
                    "this student or have "
                    "already been paid."
                )
            )

        selected_sessions = {
            str(row["session_year"] or "").strip()
            for row in selected_rows
        }
        if len(selected_sessions) != 1:
            raise ValueError(
                "One receipt can contain fees from only one academic session. "
                "Pay each session separately."
            )
        selected_session = next(iter(selected_sessions))

        selected_ids_from_database = {
            int(
                row["id"]
            )
            for row in selected_rows
        }

        if (
            selected_ids_from_database
            != set(
                normalised_record_ids
            )
        ):
            raise ValueError(
                (
                    "One or more selected fee "
                    "records could not be verified."
                )
            )

        cursor.execute(
            f"""
            SELECT COUNT(*) AS record_count

            FROM fee_receipts AS receipts

            INNER JOIN fee_payments AS payments
              ON payments.id = receipts.payment_id

            WHERE receipts.student_fee_record_id
                  IN ({placeholders})
              AND receipts.status = ?
              AND payments.payment_status = ?
            """,
            [
                *normalised_record_ids,
                FEE_STATUS_PAID,
                PAYMENT_STATUS_COMPLETED,
            ],
        )

        existing_receipt_count = int(
            cursor.fetchone()[
                "record_count"
            ]
            or 0
        )

        if existing_receipt_count > 0:
            raise ValueError(
                (
                    "One or more selected fee "
                    "records have already been paid."
                )
            )

        quantized_item_amounts = {
            int(row["id"]): _quantize_money(row["amount"])
            for row in selected_rows
        }
        total_amount_decimal = sum(
            quantized_item_amounts.values(),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total_amount = float(total_amount_decimal)

        historical_classes = [
            str(row["historical_class_name"] or "-")
            for row in selected_rows
        ]
        distinct_classes = list(dict.fromkeys(historical_classes))
        receipt_class_name = (
            distinct_classes[0]
            if len(distinct_classes) == 1
            else "MULTIPLE CLASSES"
        )

        receipt_no = (
            _generate_receipt_number(
                cursor,
                now=now,
            )
        )

        cursor.execute(
            """
            INSERT INTO fee_payments (
                receipt_no,
                student_id,
                payment_date,
                total_amount,
                received_by,
                created_at,
                payment_status,
                student_status_before,
                student_status_after,
                auto_graduated,
                auto_archived,
                tracking_id_snapshot,
                admission_number_snapshot,
                roll_number_snapshot,
                student_name_snapshot,
                father_name_snapshot,
                father_cnic_snapshot,
                class_name_snapshot,
                student_status_snapshot
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                receipt_no,
                student_id,
                now,
                total_amount,
                _PAYMENT_PENDING_ACTOR,
                now,
                PAYMENT_STATUS_COMPLETED,
                student_status_before,
                student_status_before,
                str(student_row["tracking_id"] or "").strip(),
                str(student_row["admission_number"] or "").strip(),
                int(student_row["roll_number"]),
                str(student_row["student_name"] or "").strip(),
                str(student_row["father_name"] or "").strip(),
                str(student_row["father_cnic"] or "").strip(),
                receipt_class_name,
                student_status_before,
            ),
        )

        payment_id = int(
            cursor.lastrowid
        )

        paid_items = []

        # Insert exact receipt items before finalizing the payment. The
        # internal actor marker is never committed as a completed payment.
        for row in selected_rows:
            item_name = f"{row['month_name']} {row['year']}"
            amount_decimal = quantized_item_amounts[int(row["id"])]
            amount = float(amount_decimal)
            cursor.execute(
                """
                INSERT INTO fee_receipts (
                    payment_id, student_fee_record_id, item_name, amount,
                    status, created_at, class_id_snapshot,
                    class_name_snapshot, session_year_snapshot,
                    month_number_snapshot, year_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id, int(row["id"]), item_name, amount,
                    FEE_STATUS_PAID, now, int(row["class_id"]),
                    row["historical_class_name"] or "-",
                    row["session_year"] or "-",
                    int(row["month_number"]), int(row["year"]),
                ),
            )
            paid_items.append({
                "item_name": item_name,
                "session_year": row["session_year"],
                "class_name": row["historical_class_name"] or "-",
                "amount": amount,
            })

        receipt_integrity_rows = cursor.execute(
            """
            SELECT receipts.student_fee_record_id, records.student_id,
                   receipts.amount, records.amount AS fee_amount
            FROM fee_receipts AS receipts
            JOIN student_fee_records AS records ON records.id=receipts.student_fee_record_id
            WHERE receipts.payment_id=? AND receipts.status='Paid'
            ORDER BY receipts.id
            """,
            (payment_id,),
        ).fetchall()
        if not receipt_integrity_rows:
            raise RuntimeError("A completed fee payment requires at least one fee item.")
        if len(receipt_integrity_rows) != len(normalised_record_ids):
            raise RuntimeError("Payment receipt-item count is inconsistent.")
        integrity_fee_ids = [int(item["student_fee_record_id"]) for item in receipt_integrity_rows]
        if len(integrity_fee_ids) != len(set(integrity_fee_ids)):
            raise RuntimeError("A fee record cannot appear twice in one payment.")
        if set(integrity_fee_ids) != set(normalised_record_ids):
            raise RuntimeError("Receipt items do not exactly match the selected fee months.")
        if any(int(item["student_id"]) != student_id for item in receipt_integrity_rows):
            raise RuntimeError("A receipt item belongs to another student.")
        if any(
            _quantize_money(item["amount"]) != _quantize_money(item["fee_amount"])
            for item in receipt_integrity_rows
        ):
            raise RuntimeError(
                "Each receipt item must equal the complete selected fee amount; partial payments are not allowed."
            )
        receipt_total = sum(
            (_quantize_money(item["amount"]) for item in receipt_integrity_rows),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if receipt_total != total_amount_decimal:
            raise RuntimeError("Payment total does not equal the active receipt-item total.")

        cursor.execute(
            """
            UPDATE fee_payments
            SET received_by=?
            WHERE id=? AND payment_status=? AND received_by=?
            """,
            (received_by, payment_id, PAYMENT_STATUS_COMPLETED, _PAYMENT_PENDING_ACTOR),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("Payment transaction could not be finalized.")

        # Strict payment rule: positive fee rows become Paid only after the
        # exact completed payment and numeric Receipt Number exist.
        for row in selected_rows:
            cursor.execute(
                """
                UPDATE student_fee_records
                SET status=?, paid_date=?, updated_at=?
                WHERE id=? AND student_id=? AND fee_type=? AND status=?
                """,
                (
                    FEE_STATUS_PAID, now, now, int(row["id"]), student_id,
                    FEE_TYPE_MONTHLY, FEE_STATUS_UNPAID,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "A selected fee record was already paid or changed before the payment completed."
                )

        session_history = _build_session_payment_history_snapshot(
            cursor,
            student_id=student_id,
            session_year=selected_session,
        )
        cursor.executemany(
            """
            INSERT INTO fee_payment_session_history(
                payment_id, session_year, month_number, calendar_year,
                history_status, paid_amount, paid_date
            ) VALUES(?,?,?,?,?,?,?)
            """,
            [
                (
                    payment_id,
                    selected_session,
                    item["month_number"],
                    item["year"],
                    item["status"],
                    item["amount"],
                    item["paid_date"] or None,
                )
                for item in session_history
            ],
        )

        # Runtime financial integrity: one payment owns every receipt item,
        # no fee record is repeated, and the item total equals the payment total.
        receipt_integrity_rows = cursor.execute(
            """
            SELECT receipts.student_fee_record_id, records.student_id,
                   receipts.amount, records.amount AS fee_amount
            FROM fee_receipts AS receipts
            JOIN student_fee_records AS records
              ON records.id=receipts.student_fee_record_id
            WHERE receipts.payment_id=? AND receipts.status='Paid'
            ORDER BY receipts.id
            """,
            (payment_id,),
        ).fetchall()
        if len(receipt_integrity_rows) != len(normalised_record_ids):
            raise RuntimeError("Payment receipt-item count is inconsistent.")
        integrity_fee_ids = [int(row["student_fee_record_id"]) for row in receipt_integrity_rows]
        if len(integrity_fee_ids) != len(set(integrity_fee_ids)):
            raise RuntimeError("A fee record cannot appear twice in one payment.")
        if set(integrity_fee_ids) != set(normalised_record_ids):
            raise RuntimeError("Receipt items do not exactly match the selected fee months.")
        if any(int(row["student_id"]) != student_id for row in receipt_integrity_rows):
            raise RuntimeError("A receipt item belongs to another student.")
        if any(
            _quantize_money(row["amount"]) != _quantize_money(row["fee_amount"])
            for row in receipt_integrity_rows
        ):
            raise RuntimeError(
                "Each receipt item must equal the complete selected fee amount; partial payments are not allowed."
            )
        receipt_total = sum(
            (_quantize_money(row["amount"])
             for row in receipt_integrity_rows),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        payment_total = _quantize_money(total_amount)
        if receipt_total != payment_total:
            raise RuntimeError(
                "Payment total does not equal the active receipt-item total."
            )

        auto_graduated = (
            _sync_partially_graduated_status(
                cursor=cursor,
                student_id=student_id,
                now=now,
            )
        )

        # A Dropped student is archived only after the session following
        # the drop has started and the final historical due is cleared.
        from app.services import student_service

        archive_result = (
            student_service.
            archive_eligible_dropped_students(
                as_of=payment_datetime,
                student_ids=[student_id],
                connection=connection,
            )
        )

        cursor.execute(
            """
            SELECT student_status
            FROM students
            WHERE student_id = ?
            """,
            (student_id,),
        )

        final_student_row = cursor.fetchone()
        student_status_after = str(
            final_student_row["student_status"]
        )

        auto_archived = bool(
            archive_result["archived_count"]
        )

        cursor.execute(
            """
            UPDATE fee_payments
            SET
                student_status_after = ?,
                auto_graduated = ?,
                auto_archived = ?
            WHERE id = ?
              AND payment_status = ?
            """,
            (
                student_status_after,
                1 if auto_graduated else 0,
                1 if auto_archived else 0,
                payment_id,
                PAYMENT_STATUS_COMPLETED,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "Payment side effects could not be recorded."
            )

        connection.commit()

        return {
            "payment_id": payment_id,
            "receipt_no": receipt_no,
            "payment_date": today_str,
            "tracking_id": (
                student_row["tracking_id"]
                or "-"
            ),
            "admission_number": student_row[
                "admission_number"
            ],
            "roll_number": student_row[
                "roll_number"
            ],
            "student_name": student_row[
                "student_name"
            ],
            "father_name": student_row[
                "father_name"
            ],
            "father_cnic": (
                student_row["father_cnic"]
                or "-"
            ),
            "class_name": receipt_class_name,
            "session_year": selected_session,
            "session_history": session_history,
            "paid_items": paid_items,
            "total_paid": total_amount,
            "received_by": received_by,
            "student_archived": auto_archived,
            "auto_graduated": bool(auto_graduated),
            "student_status_before": student_status_before,
            "student_status_after": student_status_after,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()




def _effective_class_for_fee_period(cursor, student_id: int, month: int, year: int) -> int:
    row = cursor.execute(
        """
        SELECT new_class_id
        FROM student_class_history
        WHERE student_id=?
          AND (effective_year<? OR (effective_year=? AND effective_month<=?))
        ORDER BY effective_year DESC,effective_month DESC,history_id DESC
        LIMIT 1
        """,
        (int(student_id), int(year), int(year), int(month)),
    ).fetchone()
    if row is not None and row["new_class_id"] is not None:
        return int(row["new_class_id"])
    student = cursor.execute(
        "SELECT class_id FROM students WHERE student_id=?", (int(student_id),)
    ).fetchone()
    if student is None:
        raise ValueError("Student was not found.")
    return int(student["class_id"])


def _promotion_effective_for_fee_period(
    cursor, student_id: int, month: int, year: int
) -> bool:
    """Return whether a completed session-bound promotion governs this period."""
    batch_columns = {
        str(row[1])
        for row in cursor.execute("PRAGMA table_info(promotion_batches)").fetchall()
    }
    if "effective_date" not in batch_columns:
        return False
    period_start = f"{int(year):04d}-{int(month):02d}-01"
    row = cursor.execute(
        """
        SELECT 1
        FROM promotion_history ph
        JOIN promotion_batches pb ON pb.batch_id=ph.batch_id
        WHERE ph.student_id=? AND ph.is_reversed=0 AND pb.is_reversed=0
          AND pb.effective_date IS NOT NULL
          AND date(pb.effective_date)<=date(?)
        LIMIT 1
        """,
        (int(student_id), period_start),
    ).fetchone()
    return row is not None


def _recalculate_cancelled_payment_fee_rows(
    cursor,
    *,
    student_id: int,
    fee_record_ids,
    now: str,
) -> None:
    """Apply the academic-effective class after a historical payment is cancelled.

    Older focused regression fixtures intentionally expose only the legacy
    cancellation columns. In those schemas the original cancellation behavior
    remains authoritative; V23 recalculation runs only when the complete fee
    period/class metadata is available.
    """
    fee_columns = {
        str(row[1])
        for row in cursor.execute("PRAGMA table_info(student_fee_records)").fetchall()
    }
    if not {"class_id", "month_number", "year"}.issubset(fee_columns):
        return

    graduation_columns = {
        str(row[1])
        for row in cursor.execute("PRAGMA table_info(graduation_records)").fetchall()
    }
    graduation = None
    graduation_boundary = None
    if {"last_class_id", "graduation_effective_date"}.issubset(
        graduation_columns
    ):
        graduation = cursor.execute(
            """
            SELECT last_class_id,graduation_effective_date
            FROM graduation_records
            WHERE student_id=? AND is_reversed=0
            ORDER BY graduated_record_id DESC LIMIT 1
            """,
            (int(student_id),),
        ).fetchone()
        if graduation is not None and graduation["graduation_effective_date"]:
            graduation_boundary = datetime.strptime(
                str(graduation["graduation_effective_date"]), "%Y-%m-%d"
            )

    for fee_record_id in fee_record_ids:
        row = cursor.execute(
            """
            SELECT id,class_id,month_number,year
            FROM student_fee_records
            WHERE id=? AND student_id=? AND fee_type=?
            """,
            (int(fee_record_id), int(student_id), FEE_TYPE_MONTHLY),
        ).fetchone()
        if row is None:
            raise RuntimeError("A reopened fee row could not be found.")
        month = int(row["month_number"])
        year = int(row["year"])
        period_start = datetime(year, month, 1)

        if graduation_boundary is not None and period_start >= graduation_boundary:
            cursor.execute(
                """
                UPDATE student_fee_records
                SET class_id=COALESCE(?,class_id),amount=0,status=?,paid_date=NULL,updated_at=?
                WHERE id=? AND student_id=? AND status=?
                """,
                (
                    graduation["last_class_id"], FEE_STATUS_WAIVED, now,
                    int(fee_record_id), int(student_id), FEE_STATUS_UNPAID,
                ),
            )
            continue

        effective_class_id = _effective_class_for_fee_period(
            cursor, int(student_id), month, year
        )
        # Ordinary immediate cancellation preserves the fee row's original
        # class and amount. V23 needs a fresh fee lookup only when an academic
        # class change (for example overdue promotion effective April) means
        # the reopened Unpaid row belongs to a different class.
        requires_academic_recalculation = _promotion_effective_for_fee_period(
            cursor, int(student_id), month, year
        )
        if (
            int(effective_class_id) == int(row["class_id"])
            and not requires_academic_recalculation
        ):
            continue
        fee = get_effective_fee(
            cursor=cursor,
            class_id=effective_class_id,
            student_id=int(student_id),
            month_number=month,
            year=year,
        )
        amount = round(float(fee["monthly_fee"]), 2)
        status = FEE_STATUS_WAIVED if amount == 0 else FEE_STATUS_UNPAID
        cursor.execute(
            """
            UPDATE student_fee_records
            SET class_id=?,amount=?,status=?,paid_date=NULL,updated_at=?
            WHERE id=? AND student_id=? AND status IN (?,?)
            """,
            (
                effective_class_id, amount, status, now,
                int(fee_record_id), int(student_id),
                FEE_STATUS_UNPAID, FEE_STATUS_WAIVED,
            ),
        )


def cancel_immediate_payment(
    payment_id: int,
    cancelled_by: str = "admin",
) -> dict:
    """
    Cancel the just-confirmed payment as one atomic rollback.

    The payment and receipt rows remain as Cancelled audit records.
    Linked Monthly Fee rows become Unpaid again. Any automatic
    Partially-Graduated-to-Graduated or Dropped-to-Archived side effect
    created by this payment is restored to its pre-payment state.

    Only the student's latest still-Completed payment can be cancelled.
    The UI exposes this operation only until the user leaves the paid
    receipt screen.
    """
    payment_id = int(payment_id)

    if payment_id <= 0:
        raise ValueError("Invalid payment transaction.")

    cancellation_datetime = datetime.now()
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")
        ensure_financial_clock_safe(
            connection,
            operation_name="cancelling a fee payment",
            as_of=cancellation_datetime,
        )
        now = cancellation_datetime.strftime(_FINANCIAL_TIMESTAMP_FORMAT)

        cursor.execute(
            """
            SELECT *
            FROM fee_payments
            WHERE id = ?
            """,
            (payment_id,),
        )

        payment_row = cursor.fetchone()

        if payment_row is None:
            raise ValueError("Payment transaction was not found.")

        if str(payment_row["payment_status"]) != PAYMENT_STATUS_COMPLETED:
            raise ValueError(
                "This payment has already been cancelled or is not active."
            )

        student_id = int(payment_row["student_id"])

        cursor.execute(
            """
            SELECT MAX(id) AS latest_payment_id
            FROM fee_payments
            WHERE student_id = ?
              AND payment_status = ?
            """,
            (student_id, PAYMENT_STATUS_COMPLETED),
        )

        latest_payment_id = int(
            cursor.fetchone()["latest_payment_id"] or 0
        )

        if latest_payment_id != payment_id:
            raise ValueError(
                "Only the student's latest completed payment can be cancelled."
            )

        cursor.execute(
            """
            SELECT student_status
            FROM students
            WHERE student_id = ?
            """,
            (student_id,),
        )

        student_row = cursor.fetchone()

        if student_row is None:
            raise ValueError("Student was not found.")

        expected_status_after = str(
            payment_row["student_status_after"]
            or payment_row["student_status_before"]
            or student_row["student_status"]
        )

        current_status = str(student_row["student_status"])
        post_payment_graduation = None

        if current_status != expected_status_after:
            graduation_columns = {
                str(row[1])
                for row in cursor.execute(
                    "PRAGMA table_info(graduation_records)"
                ).fetchall()
            }
            if {
                "completion_session_start_year",
                "graduation_effective_date",
            }.issubset(graduation_columns):
                post_payment_graduation = cursor.execute(
                    """
                    SELECT * FROM graduation_records
                    WHERE student_id=? AND is_reversed=0
                      AND completion_session_start_year IS NOT NULL
                      AND graduation_effective_date IS NOT NULL
                    ORDER BY graduated_record_id DESC LIMIT 1
                    """,
                    (student_id,),
                ).fetchone()

            allowed_after_academic_completion = (
                post_payment_graduation is not None
                and expected_status_after == STATUS_ACTIVE
                and current_status in {
                    STATUS_GRADUATED,
                    STATUS_PARTIALLY_GRADUATED,
                }
            )
            if not allowed_after_academic_completion:
                raise ValueError(
                    "The student's status changed after this payment. "
                    "The payment cannot be cancelled safely."
                )

        cursor.execute(
            """
            SELECT
                receipts.id AS receipt_id,
                receipts.student_fee_record_id,
                receipts.status AS receipt_status,
                records.status AS fee_status,
                records.student_id AS fee_student_id
            FROM fee_receipts AS receipts
            INNER JOIN student_fee_records AS records
              ON records.id = receipts.student_fee_record_id
            WHERE receipts.payment_id = ?
            ORDER BY receipts.id ASC
            """,
            (payment_id,),
        )

        receipt_rows = cursor.fetchall()

        if not receipt_rows:
            raise ValueError(
                "This payment has no receipt items and cannot be cancelled."
            )

        seen_fee_ids = set()
        for row in receipt_rows:
            fee_record_id = int(row["student_fee_record_id"])
            if fee_record_id in seen_fee_ids:
                raise ValueError("This payment contains a duplicate receipt item.")
            seen_fee_ids.add(fee_record_id)
            if int(row["fee_student_id"]) != student_id:
                raise ValueError(
                    "A receipt item belongs to another student and cannot be cancelled safely."
                )
            if str(row["receipt_status"]) != FEE_STATUS_PAID:
                raise ValueError(
                    "One or more receipt items are no longer active."
                )

            if str(row["fee_status"]) != FEE_STATUS_PAID:
                raise ValueError(
                    "One or more fee items changed after payment."
                )

        fee_record_ids = [
            int(row["student_fee_record_id"])
            for row in receipt_rows
        ]

        placeholders = ",".join("?" for _ in fee_record_ids)

        cursor.execute(
            f"""
            UPDATE student_fee_records
            SET
                status = ?,
                paid_date = NULL,
                updated_at = ?
            WHERE id IN ({placeholders})
              AND student_id = ?
              AND fee_type = ?
              AND status = ?
            """,
            [
                FEE_STATUS_UNPAID,
                now,
                *fee_record_ids,
                student_id,
                FEE_TYPE_MONTHLY,
                FEE_STATUS_PAID,
            ],
        )

        if cursor.rowcount != len(fee_record_ids):
            raise RuntimeError(
                "Not every paid fee item could be reopened."
            )

        cursor.execute(
            """
            UPDATE fee_receipts
            SET status = ?
            WHERE payment_id = ?
              AND status = ?
            """,
            (
                PAYMENT_STATUS_CANCELLED,
                payment_id,
                FEE_STATUS_PAID,
            ),
        )

        if cursor.rowcount != len(receipt_rows):
            raise RuntimeError(
                "Not every receipt item could be cancelled."
            )

        _recalculate_cancelled_payment_fee_rows(
            cursor,
            student_id=student_id,
            fee_record_ids=fee_record_ids,
            now=now,
        )

        status_before = str(
            payment_row["student_status_before"]
            or current_status
        )

        if int(payment_row["auto_graduated"] or 0) == 1:
            if status_before != STATUS_PARTIALLY_GRADUATED:
                raise RuntimeError(
                    "Stored graduation rollback state is invalid."
                )

            cursor.execute(
                """
                UPDATE graduation_records
                SET status = ?
                WHERE student_id = ?
                  AND is_reversed = 0
                  AND status = ?
                """,
                (
                    STATUS_PARTIALLY_GRADUATED,
                    student_id,
                    STATUS_GRADUATED,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "The automatic graduation record could not be restored."
                )

        if int(payment_row["auto_archived"] or 0) == 1:
            if status_before != STATUS_DROPPED:
                raise RuntimeError(
                    "Stored archive rollback state is invalid."
                )

            cursor.execute(
                """
                DELETE FROM student_status_history
                WHERE id = (
                    SELECT id
                    FROM student_status_history
                    WHERE student_id = ?
                      AND old_status = ?
                      AND new_status = ?
                      AND changed_by = 'automatic_archive'
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (student_id, STATUS_DROPPED, STATUS_ARCHIVED),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "The automatic archive history could not be restored."
                )

        if post_payment_graduation is not None:
            # The payment predates a later Class-10 completion. Reopened rows
            # through the completion March determine Graduated vs Partially
            # Graduated; post-boundary rows were already converted to non-due
            # Waived history by the V23 recalculation helper.
            _sync_class10_completion_status_from_fee_records(
                cursor, student_id, now
            )
            refreshed = cursor.execute(
                "SELECT student_status FROM students WHERE student_id=?",
                (student_id,),
            ).fetchone()
            status_before = str(refreshed["student_status"])
        elif status_before != current_status:
            cursor.execute(
                """
                UPDATE students
                SET
                    student_status = ?,
                    updated_at = ?,
                    updated_by = ?
                WHERE student_id = ?
                  AND student_status = ?
                """,
                (
                    status_before,
                    now,
                    "immediate_payment_cancel",
                    student_id,
                    current_status,
                ),
            )

            if cursor.rowcount != 1:
                raise RuntimeError(
                    "The student's automatic payment status could not be restored."
                )

        cursor.execute(
            """
            UPDATE fee_payments
            SET
                payment_status = ?,
                cancelled_at = ?,
                cancelled_by = ?,
                cancellation_reason = ?
            WHERE id = ?
              AND payment_status = ?
            """,
            (
                PAYMENT_STATUS_CANCELLED,
                now,
                cancelled_by,
                "Immediate cancellation before leaving paid receipt screen",
                payment_id,
                PAYMENT_STATUS_COMPLETED,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError("Payment cancellation could not be saved.")

        connection.commit()

        return {
            "payment_id": payment_id,
            "receipt_no": payment_row["receipt_no"],
            "student_id": student_id,
            "total_amount": float(payment_row["total_amount"]),
            "reopened_fee_count": len(fee_record_ids),
            "restored_student_status": status_before,
            "payment_status": PAYMENT_STATUS_CANCELLED,
        }

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Unpaid fee recalculation
# ---------------------------------------------------------------------------

def regenerate_future_fee_records(
    student_id: int,
) -> None:
    """
    Recalculate adjustable Monthly Fee records for one student.

    Receipt-backed Paid records are protected. Receipt-free Paid
    zero-amount rows may reopen when their recalculated amount
    becomes greater than zero.
    """
    student_id = int(
        student_id
    )

    connection = get_connection()
    cursor = connection.cursor()

    now = _format_now()

    try:
        cursor.execute(
            """
            SELECT
                records.id,
                records.class_id,
                records.month_number,
                records.year,
                records.amount,
                records.status,
                records.paid_date

            FROM student_fee_records
                AS records

            WHERE records.student_id = ?
              AND records.fee_type = ?

              AND (
                    records.status IN (?, ?)
                    OR (
                        records.status = ?
                        AND records.amount = 0
                        AND NOT EXISTS (
                            SELECT 1
                            FROM fee_receipts
                                AS receipts
                            INNER JOIN fee_payments
                                AS payments
                              ON payments.id =
                                    receipts.payment_id
                            WHERE
                                receipts.student_fee_record_id
                                = records.id
                              AND receipts.status = 'Paid'
                              AND payments.payment_status = 'Completed'
                        )
                    )
                  )

            ORDER BY
                records.year ASC,
                CASE
                    WHEN records.month_number >= 4
                        THEN records.month_number
                    ELSE records.month_number + 12
                END ASC,
                records.id ASC
            """,
            (
                student_id,
                FEE_TYPE_MONTHLY,
                FEE_STATUS_UNPAID,
                FEE_STATUS_WAIVED,
                FEE_STATUS_PAID,
            ),
        )

        adjustable_rows = cursor.fetchall()

        for fee_record in adjustable_rows:
            try:
                effective_fee = (
                    get_effective_fee(
                        cursor=cursor,
                        class_id=int(
                            fee_record[
                                "class_id"
                            ]
                        ),
                        student_id=student_id,
                        month_number=int(
                            fee_record[
                                "month_number"
                            ]
                        ),
                        year=int(
                            fee_record["year"]
                        ),
                    )
                )

            except ValueError:
                continue

            recalculated_amount = round(
                float(
                    effective_fee[
                        "monthly_fee"
                    ]
                ),
                2,
            )

            if recalculated_amount == 0:
                updated_status = (
                    FEE_STATUS_WAIVED
                )
                updated_paid_date = None

            else:
                updated_status = (
                    FEE_STATUS_UNPAID
                )

                updated_paid_date = None

            cursor.execute(
                """
                UPDATE student_fee_records
                SET
                    amount = ?,
                    status = ?,
                    paid_date = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    recalculated_amount,
                    updated_status,
                    updated_paid_date,
                    now,
                    fee_record["id"],
                ),
            )

        _mark_zero_amount_fee_records_waived(
            cursor=cursor,
            student_id=student_id,
            now=now,
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()



# MONTHLY_ONLY_PROMOTION_FEE_RECALCULATION_V8
# Promotion and manual class changes recalculate Monthly Fee records only.

def recalculate_fees_after_class_change(
    student_id: int,
    old_class_id: int,
    new_class_id: int,
    effective_month: int,
    effective_year: int,
    connection=None,
    manual_promotion_after_failure: bool = False,
    manual_backward_repeat: bool = False,
) -> None:
    """
    Recalculate Monthly Fee records following a manual class change.

    Rules:
    - only unpaid Monthly Fees from the effective month onward in the
      selected academic session follow the destination class;
    - Paid and Unpaid months before the effective month remain attached
      to their original class and amount;
    - each amount follows the new class fee applicable to its month;
    - Paid records remain unchanged;
    - previous academic sessions remain unchanged;
    - the existing function signature is retained for compatibility
      with student and promotion services.
    """
    student_id = int(
        student_id
    )

    old_class_id = int(
        old_class_id
    )

    new_class_id = int(
        new_class_id
    )

    effective_month = int(
        effective_month
    )

    effective_year = int(
        effective_year
    )

    if not 1 <= effective_month <= 12:
        raise ValueError(
            (
                "Effective month must be "
                "between 1 and 12."
            )
        )

    # These compatibility arguments no longer alter fee behavior.
    del old_class_id
    del manual_promotion_after_failure
    del manual_backward_repeat

    owns_connection = (
        connection is None
    )

    if owns_connection:
        connection = get_connection()

    cursor = connection.cursor()
    now = _format_now()

    session_start_year = (
        effective_year
        if effective_month
        >= SESSION_START_CALENDAR_MONTH
        else effective_year - 1
    )

    session_label = (
        get_session_year_label(
            session_start_year
        )
    )

    try:
        # Preserve the existing validation that the destination
        # class has a fee structure for the selected session.
        try:
            get_effective_fee(
                cursor=cursor,
                class_id=new_class_id,
                student_id=student_id,
                month_number=(
                    SESSION_START_CALENDAR_MONTH
                ),
                year=session_start_year,
            )

        except ValueError:
            get_effective_fee(
                cursor=cursor,
                class_id=new_class_id,
                student_id=student_id,
                month_number=effective_month,
                year=effective_year,
            )

        cursor.execute(
            """
            SELECT
                id,
                month_number,
                year

            FROM student_fee_records

            WHERE student_id = ?
              AND session_year = ?
              AND fee_type = ?
              AND status IN (?, ?)
              AND (
                    year > ?
                    OR (year = ? AND month_number >= ?)
                  )

            ORDER BY
                CASE
                    WHEN month_number >= ?
                        THEN month_number
                    ELSE month_number + 12
                END ASC,
                year ASC,
                id ASC
            """,
            (
                student_id,
                session_label,
                FEE_TYPE_MONTHLY,
                FEE_STATUS_UNPAID,
                FEE_STATUS_WAIVED,
                effective_year,
                effective_year,
                effective_month,
                SESSION_START_CALENDAR_MONTH,
            ),
        )

        unpaid_monthly_rows = (
            cursor.fetchall()
        )

        for monthly_row in unpaid_monthly_rows:
            destination_fee = (
                get_effective_fee(
                    cursor=cursor,
                    class_id=new_class_id,
                    student_id=student_id,
                    month_number=int(
                        monthly_row[
                            "month_number"
                        ]
                    ),
                    year=int(
                        monthly_row["year"]
                    ),
                )
            )

            monthly_amount = round(
                float(
                    destination_fee[
                        "monthly_fee"
                    ]
                ),
                2,
            )

            if monthly_amount == 0:
                updated_status = (
                    FEE_STATUS_WAIVED
                )

                updated_paid_date = None

            else:
                updated_status = (
                    FEE_STATUS_UNPAID
                )

                updated_paid_date = None

            cursor.execute(
                """
                UPDATE student_fee_records
                SET
                    class_id = ?,
                    amount = ?,
                    status = ?,
                    paid_date = ?,
                    updated_at = ?
                WHERE id = ?
                  AND student_id = ?
                  AND fee_type = ?
                  AND status IN (?, ?)
                """,
                (
                    new_class_id,
                    monthly_amount,
                    updated_status,
                    updated_paid_date,
                    now,
                    int(
                        monthly_row["id"]
                    ),
                    student_id,
                    FEE_TYPE_MONTHLY,
                    FEE_STATUS_UNPAID,
                    FEE_STATUS_WAIVED,
                ),
            )

        _mark_zero_amount_fee_records_waived(
            cursor=cursor,
            student_id=student_id,
            now=now,
        )

        if owns_connection:
            connection.commit()

    except Exception:
        if owns_connection:
            connection.rollback()

        raise

    finally:
        if owns_connection:
            connection.close()





# ---------------------------------------------------------------------------
# Individual student fee overrides
# ---------------------------------------------------------------------------

def get_individual_fee(
    student_id: int,
):
    """
    Return the currently active individual Monthly Fee override.

    This legacy compatibility function returns None when the student
    follows the normal class Monthly Fee.
    """
    row = get_active_student_override(
        int(student_id)
    )

    return (
        dict(row)
        if row
        else None
    )



def set_individual_fee(
    student_id: int,
    monthly_fee: float,
    effective_from_month: int,
    effective_from_year: int,
    created_by: str = "admin",
    change_reason: str = "Fixed individual fee rule saved",
) -> int:
    """Append an audited fixed-amount individual fee rule."""
    student_id = int(student_id)
    monthly_fee = float(monthly_fee)
    effective_from_month = int(effective_from_month)
    effective_from_year = int(effective_from_year)
    actor = str(created_by or "admin").strip() or "admin"
    reason = str(change_reason or "").strip()
    if not validate_fee_amount(monthly_fee):
        raise ValueError("Monthly Fee must be numeric and cannot be negative.")
    if not 1 <= effective_from_month <= 12:
        raise ValueError("Effective month must be between 1 and 12.")
    if not reason:
        raise ValueError("A change reason is required for an individual fee rule.")

    now = _format_now()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.cursor()
        if cursor.execute(
            "SELECT 1 FROM students WHERE student_id=?", (student_id,)
        ).fetchone() is None:
            raise ValueError("Student not found.")

        adjustable_rows = cursor.execute(
            """
            SELECT records.id, records.status, records.amount
            FROM student_fee_records AS records
            WHERE records.student_id=? AND records.fee_type=?
              AND (records.year > ? OR (records.year=? AND records.month_number>=?))
              AND (
                    records.status IN (?, ?)
                    OR (records.status=? AND records.amount=0 AND NOT EXISTS (
                        SELECT 1 FROM fee_receipts AS receipts
                        JOIN fee_payments AS payments ON payments.id=receipts.payment_id
                        WHERE receipts.student_fee_record_id=records.id
                          AND receipts.status='Paid'
                          AND payments.payment_status='Completed'
                    ))
                  )
            ORDER BY records.year, records.month_number, records.id
            """,
            (student_id, FEE_TYPE_MONTHLY, effective_from_year, effective_from_year,
             effective_from_month, FEE_STATUS_UNPAID, FEE_STATUS_WAIVED, FEE_STATUS_PAID),
        ).fetchall()

        previous = cursor.execute(
            """
            SELECT * FROM student_fee_override
            WHERE student_id=? AND is_active=1 AND superseded_at IS NULL
            ORDER BY effective_from_year DESC, effective_from_month DESC, id DESC
            LIMIT 1
            """,
            (student_id,),
        ).fetchone()
        if previous is not None and (effective_from_year, effective_from_month) < (
            int(previous["effective_from_year"]), int(previous["effective_from_month"])
        ):
            raise ValueError(
                "A new individual-fee rule cannot start before the latest active rule."
            )

        cursor.execute(
            """
            INSERT INTO student_fee_override(
                student_id, monthly_fee, monthly_fee_percentage,
                effective_from_month, effective_from_year,
                effective_to_month, effective_to_year, is_active,
                created_at, updated_at, created_by, updated_by, change_reason
            ) VALUES(?,?,NULL,?,?,NULL,NULL,1,?,?,?,?,?)
            """,
            (student_id, monthly_fee, effective_from_month, effective_from_year,
             now, now, actor, None, reason),
        )
        new_rule_id = int(cursor.lastrowid)
        if previous is not None:
            assignments = [
                "is_active=0", "superseded_at=?", "superseded_by=?",
                "superseded_reason=?", "superseded_by_record_id=?",
                "updated_at=?", "updated_by=?",
            ]
            values = [now, actor, reason, new_rule_id, now, actor]
            previous_start = (int(previous["effective_from_year"]), int(previous["effective_from_month"]))
            if (effective_from_year, effective_from_month) > previous_start:
                close_month, close_year = _previous_month_period(
                    effective_from_month, effective_from_year
                )
                assignments.extend(["effective_to_month=?", "effective_to_year=?"])
                values.extend([close_month, close_year])
            values.append(int(previous["id"]))
            cursor.execute(
                f"UPDATE student_fee_override SET {', '.join(assignments)} "
                "WHERE id=? AND superseded_at IS NULL",
                values,
            )
        from app.schema_integrity import validate_policy_overlaps
        valid_overlap, overlap_message = validate_policy_overlaps(connection)
        if not valid_overlap:
            raise ValueError(overlap_message)
        target_status = FEE_STATUS_WAIVED if monthly_fee == 0 else FEE_STATUS_UNPAID
        for row in adjustable_rows:
            cursor.execute(
                """
                UPDATE student_fee_records
                SET amount=?, status=?, paid_date=NULL, updated_at=?
                WHERE id=?
                """,
                (round(monthly_fee, 2), target_status, now, int(row["id"])),
            )
        _mark_zero_amount_fee_records_waived(cursor, student_id, now)
        connection.commit()
        return new_rule_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def clear_individual_fee(
    student_id: int,
    effective_from_month: int | None = None,
    session_start_year: int | None = None,
    removed_by: str = "admin",
) -> bool:
    """Compatibility wrapper for the explicit effective-month removal flow."""
    if effective_from_month is None or session_start_year is None:
        raise ValueError(
            "Individual-fee removal requires an effective month and academic session."
        )
    return remove_student_fee_override(
        student_id=int(student_id),
        effective_from_month=int(effective_from_month),
        session_start_year=int(session_start_year),
        removed_by=removed_by,
    )

def get_student_fee_status_snapshot(
    student_id: int,
    connection=None,
    ensure_records: bool = True,
    as_of=None,
) -> dict:
    """Ensure and read Monthly Fee status once using one controlled connection."""
    student_id = int(student_id)
    owns_connection = connection is None
    reference = as_of or datetime.now()

    if owns_connection:
        connection = get_connection()
        connection.execute("BEGIN IMMEDIATE")

    cursor = connection.cursor()
    try:
        if ensure_records:
            ensure_fee_records_generated(
                student_id,
                connection=connection,
                as_of=reference,
            )

        cursor.execute(
            """
            SELECT month_name, month_number, year, session_year,
                   status, amount, id
            FROM student_fee_records
            WHERE student_id = ? AND fee_type = ?
            ORDER BY
                CAST(SUBSTR(session_year, 1, 4) AS INTEGER) ASC,
                CASE WHEN month_number >= ?
                     THEN month_number ELSE month_number + 12 END ASC,
                year ASC, id ASC
            """,
            (student_id, FEE_TYPE_MONTHLY, SESSION_START_CALENDAR_MONTH),
        )
        rows = cursor.fetchall()

        last_paid_label = "None"
        unpaid_labels = []
        total_unpaid = 0.0
        overdue_count = 0
        current_period = (reference.year, reference.month)

        for row in rows:
            label = f"{row['month_name']} {row['year']}"
            if row["status"] == FEE_STATUS_PAID:
                last_paid_label = label
                continue
            if row["status"] == FEE_STATUS_WAIVED:
                continue

            fee_period = (int(row["year"]), int(row["month_number"]))
            if fee_period <= current_period:
                unpaid_labels.append(label)
                total_unpaid += float(row["amount"] or 0.0)
            if row["status"] == FEE_STATUS_UNPAID and fee_period < current_period:
                overdue_count += 1

        if overdue_count == 0:
            color_status = "none"
        elif overdue_count == 1:
            color_status = "yellow"
        else:
            color_status = "red"

        if owns_connection:
            connection.commit()

        return {
            "color_status": color_status,
            "last_paid_month": last_paid_label,
            "unpaid_months": unpaid_labels,
            "total_unpaid": round(total_unpaid, 2),
            "overdue_count": overdue_count,
        }

    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def get_read_only_student_fee_status_snapshot(
    student_id: int,
    connection,
    as_of=None,
) -> dict:
    """Simulate missing Monthly Fee rows without changing the database."""
    student_id = int(student_id)
    reference = as_of or datetime.now()
    preview = preview_fee_records_generation(
        student_id=student_id,
        connection=connection,
        as_of=reference,
    )
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT records.month_name, records.month_number, records.year,
               records.session_year, records.status, records.amount, records.id,
               EXISTS (
                   SELECT 1
                   FROM fee_receipts AS receipts
                   INNER JOIN fee_payments AS payments
                     ON payments.id = receipts.payment_id
                   WHERE receipts.student_fee_record_id = records.id
                     AND receipts.status = 'Paid'
                     AND payments.payment_status = 'Completed'
               ) AS receipt_backed
        FROM student_fee_records AS records
        WHERE records.student_id = ?
          AND records.fee_type = ?
        ORDER BY
            CAST(SUBSTR(records.session_year, 1, 4) AS INTEGER),
            CASE WHEN records.month_number >= ?
                 THEN records.month_number ELSE records.month_number + 12 END,
            records.year,
            records.id
        """,
        (student_id, FEE_TYPE_MONTHLY, SESSION_START_CALENDAR_MONTH),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    for missing_row in preview["missing_rows"]:
        amount = round(float(missing_row["amount"] or 0.0), 2)
        rows.append(
            {
                "month_name": missing_row["month_name"],
                "month_number": int(missing_row["month_number"]),
                "year": int(missing_row["year"]),
                "session_year": missing_row["session_year"],
                "status": FEE_STATUS_WAIVED if amount == 0 else FEE_STATUS_UNPAID,
                "amount": amount,
                "id": 2_000_000_000 + len(rows),
                "receipt_backed": 0,
                "simulated": True,
            }
        )

    rows.sort(
        key=lambda row: (
            int(str(row["session_year"] or "0").split("-")[0] or 0),
            int(row["month_number"])
            if int(row["month_number"]) >= SESSION_START_CALENDAR_MONTH
            else int(row["month_number"]) + 12,
            int(row["year"]),
            int(row["id"]),
        )
    )

    last_paid_label = "None"
    unpaid_labels = []
    total_unpaid = 0.0
    overdue_count = 0
    current_period = (reference.year, reference.month)
    for row in rows:
        amount = round(float(row["amount"] or 0.0), 2)
        status = str(row["status"] or "").strip()
        if amount == 0 and not bool(row.get("receipt_backed")):
            status = FEE_STATUS_WAIVED
        label = f"{row['month_name']} {row['year']}"
        if status == FEE_STATUS_PAID:
            last_paid_label = label
            continue
        if status == FEE_STATUS_WAIVED:
            continue
        fee_period = (int(row["year"]), int(row["month_number"]))
        if fee_period <= current_period:
            unpaid_labels.append(label)
            total_unpaid += amount
        if status == FEE_STATUS_UNPAID and fee_period < current_period:
            overdue_count += 1

    if overdue_count == 0:
        color_status = "none"
    elif overdue_count == 1:
        color_status = "yellow"
    else:
        color_status = "red"

    return {
        "color_status": color_status,
        "last_paid_month": last_paid_label,
        "unpaid_months": unpaid_labels,
        "total_unpaid": round(total_unpaid, 2),
        "overdue_count": overdue_count,
        "simulated_missing_count": int(preview["missing_count"]),
        "report_read_only": True,
    }


def get_fee_color_status(student_id: int) -> str:
    """Return the existing yellow/red/none fee colour rule."""
    return get_student_fee_status_snapshot(student_id)["color_status"]


# FULL_HISTORICAL_UNPAID_STATUS_V10
FULL_HISTORICAL_UNPAID_STATUS_V10 = True

# MONTHLY_ONLY_FEE_STATUS_FUNCTIONS_V8
def get_last_paid_and_unpaid_months(student_id: int):
    """Return the existing last-paid label and due unpaid Monthly Fee labels."""
    snapshot = get_student_fee_status_snapshot(student_id)
    return snapshot["last_paid_month"], snapshot["unpaid_months"]


# PROMOTION_NEXT_SESSION_FEE_GUARD_V9
# Play Group through Class 9 promotion ignores current unpaid dues.
# Promotion requires the receiving class fee from April of the next session.
PROMOTION_NEXT_SESSION_FEE_GUARD_V9 = True


def get_next_promotion_session_start_year(
    as_of=None,
) -> int:
    """
    Return the April-start year of the academic session being entered.

    Example:
    February 2027 is within session 2026-2027, so promotion targets
    session 2027-2028 and this function returns 2027.
    """
    reference = as_of or datetime.now()

    current_session_start = (
        _month_session_start_year(
            int(reference.month),
            int(reference.year),
        )
    )

    return int(
        current_session_start + 1
    )


def validate_destination_class_fee_for_promotion(
    class_id: int,
    session_start_year: int,
    cursor=None,
) -> dict:
    """
    Require a Monthly Fee applicable from April of the incoming session.

    The fee must belong to the destination class and the exact incoming
    academic session. A fee from an earlier session is never carried forward.
    """
    class_id = int(
        class_id
    )
    session_start_year = int(
        session_start_year
    )
    owns_connection = (
        cursor is None
    )

    if owns_connection:
        connection = get_connection()
        cursor = connection.cursor()
    else:
        connection = None

    try:
        return _get_class_fee_for_period(
            cursor=cursor,
            class_id=class_id,
            month_number=(
                SESSION_START_CALENDAR_MONTH
            ),
            year=session_start_year,
        )

    finally:
        if owns_connection:
            connection.close()


def student_has_pending_fee(student_id: int) -> bool:
    """Used by Promotion Management's Class 10 rule: True if any Monthly Fee item is unpaid."""
    ensure_fee_records_generated(student_id)
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT 1
            FROM student_fee_records
            WHERE student_id = ?
              AND fee_type = ?
              AND status = ?
            LIMIT 1
            """,
            (
                student_id,
                FEE_TYPE_MONTHLY,
                FEE_STATUS_UNPAID,
            ),
        )
        return cursor.fetchone() is not None
    finally:
        connection.close()
    # ---------------------------------------------------------------------------
# Class Status screen
# ---------------------------------------------------------------------------

def get_class_fee_status_rows(class_id: int):
    """Return a completely read-only simulated class fee-status report."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN")
        cursor.execute(
            """
            SELECT student_id, admission_number, roll_number, student_name,
                   father_name, father_cnic, student_status, failure_count
            FROM students
            WHERE class_id = ? AND student_status = 'Active'
            ORDER BY CAST(roll_number AS INTEGER), student_name
            """,
            (int(class_id),),
        )
        student_rows = cursor.fetchall()
        status_rows = []
        for student_row in student_rows:
            student_id = int(student_row["student_id"])
            snapshot = get_read_only_student_fee_status_snapshot(
                student_id,
                connection=connection,
            )
            unpaid_months = snapshot["unpaid_months"]
            status_rows.append(
                {
                    "student_id": student_id,
                    "admission_number": student_row["admission_number"],
                    "roll_number": student_row["roll_number"],
                    "student_name": student_row["student_name"],
                    "father_name": student_row["father_name"],
                    "father_cnic": student_row["father_cnic"] or "-",
                    "failure_count": student_row["failure_count"],
                    "last_paid": snapshot["last_paid_month"],
                    "unpaid_months": unpaid_months,
                    "unpaid_count": len(unpaid_months),
                    "total_unpaid": snapshot["total_unpaid"],
                    "fee_color_status": snapshot["color_status"],
                    "simulated_missing_count": snapshot["simulated_missing_count"],
                    "report_read_only": True,
                }
            )
        connection.rollback()
        return status_rows
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# MONTH_RANGE_UNPAID_FEE_REPORT_V31
# Read-only report support for the Fee Management "Sort by Month" screen.

def build_month_range_periods(
    session_start_year: int,
    from_month_number: int,
    to_month_number: int,
) -> list[dict]:
    """
    Build an inclusive month range inside the selected school session.

    The direction follows the user's From/To selection in session order
    (April through March). If To appears after From, the range moves forward.
    If To appears before From, the range moves backward. Therefore, for the
    2026-2027 session, June -> April means June 2026, May 2026, April 2026;
    it does not continue forward through August or into the next session.

    The range is always between one and twelve months long and never leaves
    the selected session.
    """
    session_start_year = int(session_start_year)
    from_month_number = int(from_month_number)
    to_month_number = int(to_month_number)

    if not 1900 <= session_start_year <= 9998:
        raise ValueError("Session start year is invalid.")

    session_periods = []
    for month_name, month_number in SESSION_MONTH_ORDER:
        month_number = int(month_number)
        calendar_year = (
            session_start_year
            if month_number >= SESSION_START_CALENDAR_MONTH
            else session_start_year + 1
        )
        session_periods.append(
            {
                "month_name": str(month_name),
                "month_number": month_number,
                "year": calendar_year,
                "label": f"{month_name} {calendar_year}",
                "session_year": get_session_year_label(session_start_year),
            }
        )

    index_by_month = {
        int(period["month_number"]): index
        for index, period in enumerate(session_periods)
    }
    if from_month_number not in index_by_month:
        raise ValueError("From month is invalid.")
    if to_month_number not in index_by_month:
        raise ValueError("To month is invalid.")

    from_index = index_by_month[from_month_number]
    to_index = index_by_month[to_month_number]
    step = 1 if to_index >= from_index else -1
    selected = [
        dict(session_periods[index])
        for index in range(from_index, to_index + step, step)
    ]
    if not selected or len(selected) > 12:
        raise ValueError("The selected month range is invalid.")
    return selected


def _actual_unpaid_months_for_periods(
    cursor,
    *,
    student_id: int,
    period_keys: set[tuple[int, int]],
) -> list[str]:
    """Return persisted Unpaid Monthly Fee labels for a selected range."""
    rows = cursor.execute(
        """
        SELECT month_name, month_number, year, status
        FROM student_fee_records
        WHERE student_id = ?
          AND fee_type = ?
        ORDER BY year, month_number, id
        """,
        (int(student_id), FEE_TYPE_MONTHLY),
    ).fetchall()
    labels = []
    for row in rows:
        key = (int(row["year"]), int(row["month_number"]))
        if key not in period_keys:
            continue
        if str(row["status"] or "").strip() != FEE_STATUS_UNPAID:
            continue
        month_name = MONTH_NUMBER_TO_NAME.get(
            int(row["month_number"]),
            str(row["month_name"] or "").strip(),
        )
        labels.append(f"{month_name} {int(row['year'])}")
    return labels


def get_month_range_unpaid_fee_report(
    session_start_year: int,
    from_month_number: int,
    to_month_number: int,
    *,
    as_of=None,
) -> dict:
    """
    Return active students with at least one unpaid due month in a range.

    This report is read-only. It uses the same simulated fee-status engine as
    Class Status and Full School List, so missing expected rows can be shown
    without inserting records. Future months are retained in the selected
    range metadata but are not classified as unpaid until their calendar month
    begins, matching the application's existing fee-status rule.
    """
    periods = build_month_range_periods(
        session_start_year,
        from_month_number,
        to_month_number,
    )
    reference = as_of or datetime.now()
    current_period = (int(reference.year), int(reference.month))
    due_periods = [
        period
        for period in periods
        if (int(period["year"]), int(period["month_number"])) <= current_period
    ]
    future_periods = [
        period
        for period in periods
        if (int(period["year"]), int(period["month_number"])) > current_period
    ]
    due_period_keys = {
        (int(period["year"]), int(period["month_number"]))
        for period in due_periods
    }

    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN")
        student_rows = cursor.execute(
            """
            SELECT
                students.student_id,
                students.admission_number,
                students.roll_number,
                students.student_name,
                students.failure_count,
                students.student_status,
                classes.id AS class_id,
                classes.class_level,
                classes.section,
                classes.class_name
            FROM students
            INNER JOIN classes ON classes.id = students.class_id
            WHERE students.student_status = ?
            """,
            (STATUS_ACTIVE,),
        ).fetchall()

        report_rows = []
        setup_warnings = []
        for student_row in student_rows:
            student_id = int(student_row["student_id"])
            fee_setup_warning = False
            try:
                snapshot = get_read_only_student_fee_status_snapshot(
                    student_id,
                    connection=connection,
                    as_of=reference,
                )
                unpaid_set = {
                    str(label).strip()
                    for label in snapshot["unpaid_months"]
                    if str(label).strip()
                }
                unpaid_months = [
                    str(period["label"])
                    for period in due_periods
                    if str(period["label"]) in unpaid_set
                ]
            except ValueError as error:
                # Preserve existing persisted dues in the report even when a
                # separate missing fee configuration prevents full simulation.
                fee_setup_warning = True
                unpaid_months = _actual_unpaid_months_for_periods(
                    cursor,
                    student_id=student_id,
                    period_keys=due_period_keys,
                )
                setup_warnings.append(
                    {
                        "student_id": student_id,
                        "admission_number": student_row["admission_number"],
                        "class_name": student_row["class_name"],
                        "message": str(error),
                    }
                )

            if not unpaid_months:
                continue

            report_rows.append(
                {
                    "student_id": student_id,
                    "class_id": int(student_row["class_id"]),
                    "class_level": str(student_row["class_level"] or ""),
                    "section": str(student_row["section"] or ""),
                    "class_name": str(student_row["class_name"] or "-"),
                    "admission_number": student_row["admission_number"],
                    "roll_number": student_row["roll_number"],
                    "student_name": student_row["student_name"],
                    "failure_count": int(student_row["failure_count"] or 0),
                    "student_status": str(student_row["student_status"] or STATUS_ACTIVE),
                    "unpaid_months": unpaid_months,
                    "unpaid_count": len(unpaid_months),
                    "fee_setup_warning": fee_setup_warning,
                    "report_read_only": True,
                }
            )

        connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    class_level_order = [
        "PLAY GROUP", "NURSERY", "KG", "1", "2", "3", "4",
        "5", "6", "7", "8", "9", "10",
    ]

    def roll_sort_value(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 999999

    def sort_key(row):
        try:
            class_index = class_level_order.index(row["class_level"])
        except ValueError:
            class_index = 999
        from app.config import section_rank
        return (
            class_index,
            section_rank(row["section"]),
            roll_sort_value(row["roll_number"]),
            str(row["student_name"] or ""),
        )

    report_rows.sort(key=sort_key)
    class_count = len({int(row["class_id"]) for row in report_rows})
    unpaid_entry_count = sum(int(row["unpaid_count"]) for row in report_rows)

    return {
        "session_start_year": int(session_start_year),
        "session_label": get_session_year_label(int(session_start_year)),
        "from_month_number": int(from_month_number),
        "to_month_number": int(to_month_number),
        "periods": periods,
        "due_periods": due_periods,
        "future_periods": future_periods,
        "period_label": (
            f"{periods[0]['label']} to {periods[-1]['label']}"
            if len(periods) > 1
            else str(periods[0]["label"])
        ),
        "rows": report_rows,
        "student_count": len(report_rows),
        "class_count": class_count,
        "unpaid_month_entry_count": unpaid_entry_count,
        "fee_setup_warnings": setup_warnings,
        "report_read_only": True,
    }


# MONTHLY_ONLY_FULL_SCHOOL_STATUS_OUTPUT_V8
# Full-school status rows expose Monthly Fee fields only.
def get_full_school_fee_status_rows():
    """Return a completely read-only simulated full-school fee report."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN")
        cursor.execute(
            """
            SELECT
                students.student_id,
                students.admission_number,
                students.roll_number,
                students.student_name,
                students.father_name,
                students.father_cnic,
                students.failure_count,
                students.student_status,
                classes.id AS class_id,
                classes.class_level,
                classes.section,
                classes.class_name
            FROM students
            LEFT JOIN classes ON classes.id = students.class_id
            WHERE students.student_status = 'Active'
            """
        )
        student_rows = cursor.fetchall()
        status_rows = []
        for student_row in student_rows:
            student_id = int(student_row["student_id"])
            try:
                snapshot = get_read_only_student_fee_status_snapshot(
                    student_id,
                    connection=connection,
                )
            except ValueError:
                status_rows.append(
                    {
                        "student_id": student_id,
                        "class_id": student_row["class_id"],
                        "class_level": student_row["class_level"] or "",
                        "section": student_row["section"] or "",
                        "class_name": student_row["class_name"] or "-",
                        "admission_number": student_row["admission_number"],
                        "roll_number": student_row["roll_number"],
                        "student_name": student_row["student_name"],
                        "father_name": student_row["father_name"],
                        "father_cnic": student_row["father_cnic"] or "-",
                        "failure_count": student_row["failure_count"],
                        "student_status": student_row["student_status"],
                        "last_paid": "-",
                        "unpaid_items": ["Fee Not Set"],
                        "monthly_unpaid_items": [],
                        "monthly_unpaid_count": 0,
                        "fee_status_text": "Fee Not Set",
                        "fee_color_status": "none",
                        "fee_setup_missing": True,
                        "simulated_missing_count": 0,
                        "report_read_only": True,
                    }
                )
                continue

            unpaid_items = list(snapshot["unpaid_months"])
            status_rows.append(
                {
                    "student_id": student_id,
                    "class_id": student_row["class_id"],
                    "class_level": student_row["class_level"] or "",
                    "section": student_row["section"] or "",
                    "class_name": student_row["class_name"] or "-",
                    "admission_number": student_row["admission_number"],
                    "roll_number": student_row["roll_number"],
                    "student_name": student_row["student_name"],
                    "father_name": student_row["father_name"],
                    "father_cnic": student_row["father_cnic"] or "-",
                    "failure_count": student_row["failure_count"],
                    "student_status": student_row["student_status"],
                    "last_paid": snapshot["last_paid_month"],
                    "unpaid_items": unpaid_items,
                    "monthly_unpaid_items": unpaid_items,
                    "monthly_unpaid_count": len(unpaid_items),
                    "fee_status_text": str(len(unpaid_items)),
                    "fee_color_status": snapshot["color_status"],
                    "fee_setup_missing": False,
                    "simulated_missing_count": snapshot["simulated_missing_count"],
                    "report_read_only": True,
                }
            )
        connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    class_level_order = [
        "PLAY GROUP", "NURSERY", "KG", "1", "2", "3", "4",
        "5", "6", "7", "8", "9", "10",
    ]

    def roll_number_sort_value(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 999999

    def status_sort_key(row):
        try:
            class_index = class_level_order.index(row["class_level"])
        except ValueError:
            class_index = 999
        from app.config import section_rank
        return (
            class_index,
            section_rank(row["section"]),
            roll_number_sort_value(row["roll_number"]),
            row["student_name"],
        )

    return sorted(status_rows, key=status_sort_key)
