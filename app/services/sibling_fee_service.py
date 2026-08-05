"""
Sibling Monthly Fee policy.

A valid sibling group consists only of Active students sharing both the
same normalized Father Name and the same valid 13-digit Father CNIC. One
student pays 100% of the class Monthly Fee; every other Active sibling pays
the configured percentage. Assignment history is append-only and period-based
so earlier settings, assignments, months and receipt-backed rows never change.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from app.config import (
    FEE_STATUS_PAID,
    FEE_STATUS_UNPAID,
    FEE_STATUS_WAIVED,
    FEE_TYPE_MONTHLY,
    STATUS_ACTIVE,
    CLASS_LEVEL_SEQUENCE,
    section_rank,
)
from app.database import get_connection
from app.normalization import normalize_person_name


SIBLING_ACTIVE_ONLY_AND_ROUNDING_V10 = True
SIBLING_EVENT_MONTH_ADJUSTABLE_ROWS_V11 = True


CLASS_LEVEL_ORDER = {level: index for index, level in enumerate(CLASS_LEVEL_SEQUENCE)}


def _format_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_father_cnic(value) -> str | None:
    raw = str(value or "").strip()
    if not raw or any(
        character not in "0123456789 -\t\r\n"
        for character in raw
    ):
        return None
    digits = "".join(character for character in raw if character.isdigit())
    if len(digits) != 13 or len(set(digits)) == 1:
        return None
    return digits


def sibling_group_key(father_name, father_cnic) -> tuple[str, str] | None:
    normalized_name = normalize_person_name(father_name)
    normalized_cnic = normalize_father_cnic(father_cnic)
    if not normalized_name or normalized_cnic is None:
        return None
    return normalized_name, normalized_cnic


def _validate_period(month_number: int, year: int) -> tuple[int, int]:
    month_number = int(month_number)
    year = int(year)
    if not 1 <= month_number <= 12:
        raise ValueError("Effective month must be between 1 and 12.")
    if not 2000 <= year <= 2100:
        raise ValueError("Effective year must be between 2000 and 2100.")
    return month_number, year


def _period_key(month_number: int, year: int) -> int:
    return int(year) * 100 + int(month_number)


def round_sibling_discount_to_nearest_ten(amount) -> float:
    amount_decimal = Decimal(str(amount))
    rounded_amount = (
        amount_decimal / Decimal("10")
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * Decimal("10")
    return float(rounded_amount)


# Backward-compatible private alias for older callers/tests.
_round_percentage_fee_to_nearest_ten = (
    round_sibling_discount_to_nearest_ten
)


def _setting_for_period(cursor, month_number: int, year: int):
    cursor.execute(
        """
        SELECT monthly_fee_percentage, effective_from_month,
               effective_from_year, created_at, created_by
        FROM sibling_fee_settings
        WHERE effective_from_year < ?
           OR (effective_from_year = ? AND effective_from_month <= ?)
        ORDER BY effective_from_year DESC,
                 effective_from_month DESC,
                 id DESC
        LIMIT 1
        """,
        (int(year), int(year), int(month_number)),
    )
    return cursor.fetchone()


def get_current_sibling_fee_setting():
    today = datetime.now()
    connection = get_connection()
    try:
        row = _setting_for_period(connection.cursor(), today.month, today.year)
        return dict(row) if row else None
    finally:
        connection.close()


def get_sibling_assignment_for_period(
    cursor,
    student_id: int,
    month_number: int,
    year: int,
):
    cursor.execute(
        """
        SELECT monthly_fee_percentage, is_sibling_policy,
               assignment_role, normalized_father_cnic,
               effective_from_month, effective_from_year
        FROM sibling_fee_assignments
        WHERE student_id = ?
          AND (
                effective_from_year < ?
                OR (
                    effective_from_year = ?
                    AND effective_from_month <= ?
                )
              )
        ORDER BY effective_from_year DESC,
                 effective_from_month DESC,
                 id DESC
        LIMIT 1
        """,
        (int(student_id), int(year), int(year), int(month_number)),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _admission_sort_value(row) -> str:
    text = str(row["admission_date"] or "").strip()
    return text[:10] if len(text) >= 10 else "9999-12-31"


def _tracking_sort_value(row) -> tuple[int, int]:
    digits = "".join(character for character in str(row["tracking_id"] or "") if character.isdigit())
    numeric = int(digits) if digits else 10**18
    return numeric, int(row["student_id"])


def _class_rank(row) -> int:
    return CLASS_LEVEL_ORDER.get(str(row["class_level"] or "").strip().upper(), -1)


def _load_active_students(cursor):
    cursor.execute(
        """
        SELECT s.student_id, s.tracking_id, s.father_name, s.father_cnic,
               s.admission_date, s.student_status,
               c.class_level, c.class_name
        FROM students AS s
        LEFT JOIN classes AS c ON c.id = s.class_id
        WHERE s.student_status = ?
        ORDER BY s.student_id ASC
        """,
        (STATUS_ACTIVE,),
    )
    return cursor.fetchall()


def _group_active_students(cursor) -> dict[tuple[str, str], list]:
    groups: dict[tuple[str, str], list] = {}
    for row in _load_active_students(cursor):
        group_key = sibling_group_key(row["father_name"], row["father_cnic"])
        if group_key is None:
            continue
        groups.setdefault(group_key, []).append(row)
    return groups


def _full_fee_student_id(group_rows: list) -> int:
    highest_rank = max(_class_rank(row) for row in group_rows)
    highest_rows = [row for row in group_rows if _class_rank(row) == highest_rank]
    highest_rows.sort(key=lambda row: (_admission_sort_value(row), _tracking_sort_value(row)))
    return int(highest_rows[0]["student_id"])


def _write_assignment(
    cursor,
    *,
    student_id: int,
    normalized_father_name: str | None,
    normalized_father_cnic: str | None,
    percentage: float,
    is_sibling_policy: bool,
    role: str,
    effective_month: int,
    effective_year: int,
    now: str,
    changed_by: str = "system",
) -> bool:
    """Append a changed assignment and leave identical audit rows untouched."""
    role = str(role)
    policy_value = 1 if is_sibling_policy else 0
    percentage_value = round(float(percentage), 6)
    if role == "FULL" and (policy_value != 1 or percentage_value != 100.0):
        raise ValueError("FULL sibling assignment must use policy Yes and 100% fee.")
    if role == "STANDARD" and (policy_value != 0 or percentage_value != 100.0):
        raise ValueError("STANDARD sibling assignment must use policy No and 100% fee.")
    if role == "DISCOUNTED":
        if policy_value != 1:
            raise ValueError("DISCOUNTED sibling assignment must use sibling policy Yes.")
        configured_setting = _setting_for_period(
            cursor, int(effective_month), int(effective_year)
        )
        if configured_setting is None or round(
            float(configured_setting["monthly_fee_percentage"]), 6
        ) != percentage_value:
            raise ValueError(
                "DISCOUNTED sibling assignment must use the configured sibling percentage."
            )
    if role in {"FULL", "DISCOUNTED"}:
        current_groups = _group_active_students(cursor)
        eligible_count = len(current_groups.get((
            str(normalized_father_name or ""),
            str(normalized_father_cnic or ""),
        ), []))
        if int(eligible_count) < 2:
            raise ValueError(
                "Sibling policy requires at least two matching Active students."
            )
    identity_row = cursor.execute(
        "SELECT father_cnic FROM students WHERE student_id=?", (int(student_id),)
    ).fetchone()
    current_cnic = "" if identity_row is None else str(identity_row["father_cnic"] or "").replace("-", "").replace(" ", "").strip()
    if str(normalized_father_cnic or "") != current_cnic:
        raise ValueError("Sibling assignment Father CNIC does not match the student record.")

    cursor.execute(
        """
        SELECT id, normalized_father_name, normalized_father_cnic,
               monthly_fee_percentage, is_sibling_policy, assignment_role
        FROM sibling_fee_assignments
        WHERE student_id = ?
          AND effective_from_month = ?
          AND effective_from_year = ?
          AND COALESCE(is_active, 1) = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (int(student_id), int(effective_month), int(effective_year)),
    )
    existing = cursor.fetchone()
    expected = (
        normalized_father_name,
        normalized_father_cnic,
        round(float(percentage), 6),
        1 if is_sibling_policy else 0,
        str(role),
    )
    if existing is not None:
        current = (
            existing["normalized_father_name"],
            existing["normalized_father_cnic"],
            round(float(existing["monthly_fee_percentage"]), 6),
            int(existing["is_sibling_policy"]),
            str(existing["assignment_role"]),
        )
        if current == expected:
            return False
        cursor.execute(
            """
            UPDATE sibling_fee_assignments
            SET is_active = 0, superseded_at = ?, superseded_by = ?
            WHERE id = ? AND COALESCE(is_active, 1) = 1
            """,
            (now, str(changed_by or "system"), int(existing["id"])),
        )

    cursor.execute(
        """
        INSERT INTO sibling_fee_assignments (
            student_id, normalized_father_name, normalized_father_cnic,
            monthly_fee_percentage, is_sibling_policy, assignment_role,
            effective_from_month, effective_from_year, created_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            int(student_id), normalized_father_name, normalized_father_cnic,
            float(percentage), 1 if is_sibling_policy else 0, str(role),
            int(effective_month), int(effective_year), now,
        ),
    )
    return True


def _recalculate_records(
    cursor,
    student_ids: set[int],
    effective_month: int,
    effective_year: int,
    now: str,
) -> int:
    if not student_ids:
        return 0
    placeholders = ",".join("?" for _ in student_ids)
    parameters = list(sorted(student_ids)) + [
        FEE_TYPE_MONTHLY,
        FEE_STATUS_UNPAID,
        FEE_STATUS_WAIVED,
        FEE_STATUS_PAID,
        int(effective_year),
        int(effective_year),
        int(effective_month),
    ]
    cursor.execute(
        f"""
        SELECT id, student_id, class_id, month_number, year,
               amount, status, paid_date
        FROM student_fee_records AS records
        WHERE student_id IN ({placeholders})
          AND fee_type = ?
          AND (
                records.status IN (?, ?)
                OR (
                    records.status = ?
                    AND records.amount = 0
                    AND NOT EXISTS (
                        SELECT 1
                        FROM fee_receipts AS receipts
                        INNER JOIN fee_payments AS payments
                          ON payments.id = receipts.payment_id
                        WHERE receipts.student_fee_record_id = records.id
                          AND receipts.status = 'Paid'
                          AND payments.payment_status = 'Completed'
                    )
                )
              )
          AND (
                year > ?
                OR (year = ? AND month_number >= ?)
              )
        ORDER BY year ASC, month_number ASC, id ASC
        """,
        parameters,
    )
    rows = cursor.fetchall()
    if not rows:
        return 0

    from app.services import fee_service

    updated = 0
    for row in rows:
        effective_fee = fee_service.get_effective_fee(
            cursor=cursor,
            class_id=int(row["class_id"]),
            student_id=int(row["student_id"]),
            month_number=int(row["month_number"]),
            year=int(row["year"]),
        )
        amount = round(float(effective_fee["monthly_fee"]), 2)
        status = FEE_STATUS_WAIVED if amount == 0 else FEE_STATUS_UNPAID
        paid_date = None
        cursor.execute(
            """
            UPDATE student_fee_records
            SET amount = ?, status = ?, paid_date = ?, updated_at = ?
            WHERE id = ?
            """,
            (amount, status, paid_date, now, int(row["id"])),
        )
        updated += cursor.rowcount
    return updated


def _recalculate_groups(
    cursor,
    group_keys: set[tuple[str, str]],
    affected_student_ids: set[int],
    effective_month: int,
    effective_year: int,
    now: str,
    changed_by: str = "system",
) -> dict:
    groups = _group_active_students(cursor)
    setting = _setting_for_period(cursor, effective_month, effective_year)
    configured_percentage = float(setting["monthly_fee_percentage"]) if setting else 100.0

    assignment_ids: set[int] = set()
    full_fee_ids: set[int] = set()
    discounted_ids: set[int] = set()
    changed_assignments = 0

    for normalized_name, normalized_cnic in sorted(group_keys):
        group_rows = groups.get((normalized_name, normalized_cnic), [])
        group_ids = {int(row["student_id"]) for row in group_rows}
        assignment_ids.update(group_ids)
        policy_applies = setting is not None and len(group_rows) >= 2
        full_id = _full_fee_student_id(group_rows) if policy_applies else None

        for row in group_rows:
            student_id = int(row["student_id"])
            if policy_applies and student_id == full_id:
                percentage, role = 100.0, "FULL"
                full_fee_ids.add(student_id)
            elif policy_applies:
                percentage, role = configured_percentage, "DISCOUNTED"
                discounted_ids.add(student_id)
            else:
                percentage, role = 100.0, "STANDARD"

            changed_assignments += int(_write_assignment(
                cursor,
                student_id=student_id,
                normalized_father_name=normalized_name,
                normalized_father_cnic=normalized_cnic,
                percentage=percentage,
                is_sibling_policy=policy_applies,
                role=role,
                effective_month=effective_month,
                effective_year=effective_year,
                now=now,
                changed_by=changed_by,
            ))

    for student_id in sorted(affected_student_ids - assignment_ids):
        cursor.execute(
            "SELECT father_name, father_cnic FROM students WHERE student_id = ?",
            (int(student_id),),
        )
        row = cursor.fetchone()
        current_key = sibling_group_key(
            row["father_name"] if row else None,
            row["father_cnic"] if row else None,
        )
        current_name = current_key[0] if current_key else None
        current_cnic = current_key[1] if current_key else None
        changed_assignments += int(_write_assignment(
            cursor,
            student_id=student_id,
            normalized_father_name=current_name,
            normalized_father_cnic=current_cnic,
            percentage=100.0,
            is_sibling_policy=False,
            role="STANDARD",
            effective_month=effective_month,
            effective_year=effective_year,
            now=now,
            changed_by=changed_by,
        ))

    all_ids = assignment_ids | affected_student_ids
    updated_records = _recalculate_records(
        cursor, all_ids, effective_month, effective_year, now
    )
    return {
        "affected_students": len(all_ids),
        "full_fee_students": len(full_fee_ids),
        "discounted_students": len(discounted_ids),
        "updated_fee_records": updated_records,
        "changed_assignments": changed_assignments,
    }


def recalculate_sibling_fees_for_student_ids(
    student_ids,
    effective_month: int,
    effective_year: int,
    connection=None,
    changed_by: str = "system",
):
    effective_month, effective_year = _validate_period(effective_month, effective_year)
    ids = {int(value) for value in student_ids if value is not None}
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    cursor = connection.cursor()
    now = _format_now()
    try:
        group_keys: set[tuple[str, str]] = set()
        if ids:
            placeholders = ",".join("?" for _ in ids)
            cursor.execute(
                f"SELECT student_id, father_name, father_cnic FROM students "
                f"WHERE student_id IN ({placeholders})",
                sorted(ids),
            )
            for row in cursor.fetchall():
                key = sibling_group_key(row["father_name"], row["father_cnic"])
                if key:
                    group_keys.add(key)
        result = _recalculate_groups(
            cursor, group_keys, ids, effective_month, effective_year, now, changed_by
        )
        if owns_connection:
            connection.commit()
        return result
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def recalculate_sibling_fees_after_student_change(
    student_id: int,
    old_father_cnic,
    new_father_cnic,
    effective_month: int,
    effective_year: int,
    connection=None,
    old_father_name=None,
    new_father_name=None,
    changed_by: str = "system",
):
    effective_month, effective_year = _validate_period(effective_month, effective_year)
    group_keys = {
        key
        for key in (
            sibling_group_key(old_father_name, old_father_cnic),
            sibling_group_key(new_father_name, new_father_cnic),
        )
        if key is not None
    }
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    cursor = connection.cursor()
    now = _format_now()
    try:
        result = _recalculate_groups(
            cursor, group_keys, {int(student_id)},
            effective_month, effective_year, now, changed_by
        )
        if owns_connection:
            connection.commit()
        return result
    except Exception:
        if owns_connection:
            connection.rollback()
        raise
    finally:
        if owns_connection:
            connection.close()


def save_sibling_fee_percentage(
    monthly_fee_percentage,
    created_by: str = "admin",
):
    percentage = float(monthly_fee_percentage)
    if not 0 <= percentage <= 100:
        raise ValueError("Sibling Monthly Fee Percentage must be between 0 and 100.")
    today = datetime.now()
    month, year = _validate_period(today.month, today.year)
    now = _format_now()

    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            UPDATE sibling_fee_settings
            SET is_active = 0, superseded_at = ?, superseded_by = ?
            WHERE COALESCE(is_active, 1) = 1
            """,
            (now, str(created_by or "admin")),
        )
        cursor.execute(
            """
            INSERT INTO sibling_fee_settings (
                monthly_fee_percentage, effective_from_month,
                effective_from_year, created_at, created_by, is_active
            ) VALUES (?, ?, ?, ?, ?, 1)
            """,
            (percentage, month, year, now, str(created_by or "admin")),
        )

        groups = _group_active_students(cursor)
        normalized_cnics = set(groups)
        all_active_ids = {
            int(row["student_id"])
            for rows in groups.values()
            for row in rows
        }
        result = _recalculate_groups(
            cursor,
            normalized_cnics,
            all_active_ids,
            month,
            year,
            now,
            str(created_by or "admin"),
        )
        connection.commit()
        result.update(
            {
                "monthly_fee_percentage": percentage,
                "effective_month": month,
                "effective_year": year,
            }
        )
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_current_sibling_assignments():
    today = datetime.now()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT s.student_id, s.tracking_id, s.student_name,
                   s.father_name, s.father_cnic, c.class_name,
                   c.class_level, c.section,
                   a.monthly_fee_percentage, a.assignment_role,
                   a.is_sibling_policy
            FROM students AS s
            LEFT JOIN classes AS c ON c.id = s.class_id
            LEFT JOIN sibling_fee_assignments AS a
              ON a.id = (
                    SELECT a2.id
                    FROM sibling_fee_assignments AS a2
                    WHERE a2.student_id = s.student_id
                      AND (
                            a2.effective_from_year < ?
                            OR (
                                a2.effective_from_year = ?
                                AND a2.effective_from_month <= ?
                            )
                          )
                    ORDER BY a2.effective_from_year DESC,
                             a2.effective_from_month DESC,
                             a2.id DESC
                    LIMIT 1
                 )
            WHERE s.student_status = ?
              AND a.is_sibling_policy = 1
            ORDER BY s.student_id ASC
            """,
            (today.year, today.year, today.month, STATUS_ACTIVE),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows.sort(
            key=lambda row: (
                -CLASS_LEVEL_ORDER.get(
                    str(row.get("class_level") or "").strip().upper(), -1
                ),
                section_rank(row.get("section")),
                str(row.get("student_name") or ""),
                int(row.get("student_id") or 0),
            )
        )
        return rows
    finally:
        connection.close()
