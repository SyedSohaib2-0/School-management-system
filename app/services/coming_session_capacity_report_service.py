"""Class-capacity summary for the Statistics Coming Session report.

The report is intentionally class-level only.  It reconstructs the Active
student state at the end of a selected April-to-March base session, combines
that snapshot with admissions reserved for the immediately following session,
and exposes the existing stored class capacity for editing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from app.config import (
    ALL_CLASS_LEVELS,
    STATUS_ACTIVE,
    section_rank,
)
from app.database import get_connection
from app.services import class_service, student_service


MIN_SESSION_START_YEAR = 2000
MAX_SESSION_START_YEAR = 9998


def _validated_base_session_start_year(value: int) -> int:
    try:
        year = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Please select a valid base academic session.") from error
    if year < MIN_SESSION_START_YEAR or year > MAX_SESSION_START_YEAR:
        raise ValueError("Please select a valid base academic session.")
    return year


def _local_naive_datetime(value, *, field: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field} is blank.")
    normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(f"{field} is not a valid ISO date/time: {raw}") from error
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _class_sort_key(row: dict) -> tuple:
    level = str(row.get("class_level") or "").strip().upper()
    try:
        level_rank = ALL_CLASS_LEVELS.index(level)
    except ValueError:
        level_rank = 999
    section = str(row.get("section") or "").strip().upper()
    return (
        level_rank,
        section_rank(section),
        section,
        str(row.get("class_name") or "").strip().upper(),
        int(row.get("class_id") or 0),
    )


def get_available_base_session_years() -> list[int]:
    """Return reportable base sessions, newest first."""
    connection = get_connection(automatic_backup=False)
    try:
        connection.execute("PRAGMA query_only = ON")
        years: set[int] = {
            int(student_service.current_academic_session_start_year())
        }

        for row in connection.execute(
            """
            SELECT admission_year, admission_month, target_session_start_year
            FROM students
            """
        ).fetchall():
            admission_year = int(row["admission_year"])
            admission_month = int(row["admission_month"])
            years.add(
                admission_year if admission_month >= 4 else admission_year - 1
            )
            if row["target_session_start_year"] is not None:
                target_year = int(row["target_session_start_year"])
                years.add(target_year - 1)
                years.add(target_year)

        for row in connection.execute(
            """
            SELECT effective_year, effective_month
            FROM student_class_history
            """
        ).fetchall():
            effective_year = int(row["effective_year"])
            effective_month = int(row["effective_month"])
            years.add(
                effective_year if effective_month >= 4 else effective_year - 1
            )

        return sorted(
            (
                year
                for year in years
                if MIN_SESSION_START_YEAR <= int(year) <= MAX_SESSION_START_YEAR
            ),
            reverse=True,
        )
    finally:
        connection.close()


def _load_class_snapshot_by_student(cursor, cutoff_year: int) -> dict[int, int]:
    """Return each student's latest class at or before March cutoff_year."""
    rows = cursor.execute(
        """
        SELECT student_id, new_class_id, effective_year, effective_month, history_id
        FROM student_class_history
        WHERE effective_year < ?
           OR (effective_year = ? AND effective_month <= 3)
        ORDER BY student_id, effective_year, effective_month, history_id
        """,
        (int(cutoff_year), int(cutoff_year)),
    ).fetchall()
    class_by_student: dict[int, int] = {}
    for row in rows:
        if row["new_class_id"] is not None:
            class_by_student[int(row["student_id"])] = int(row["new_class_id"])
    return class_by_student


def _load_first_status_after_cutoff(cursor, cutoff: datetime) -> dict[int, str]:
    """Return the status immediately before the first event after cutoff."""
    result: dict[int, tuple[datetime, int, str]] = {}
    rows = cursor.execute(
        """
        SELECT id, student_id, old_status, effective_datetime
        FROM student_status_history
        ORDER BY student_id, id
        """
    ).fetchall()
    for row in rows:
        effective = _local_naive_datetime(
            row["effective_datetime"],
            field="Student status-history effective date/time",
        )
        if effective <= cutoff:
            continue
        student_id = int(row["student_id"])
        candidate = (effective, int(row["id"]), str(row["old_status"] or ""))
        current = result.get(student_id)
        if current is None or candidate[:2] < current[:2]:
            result[student_id] = candidate
    return {student_id: candidate[2] for student_id, candidate in result.items()}


def get_coming_session_capacity_report(base_session_start_year: int) -> dict:
    """Return the class-level Coming Session capacity report.

    Existing Active Students are reconstructed at 31 March, 23:59:59, at
    the end of the selected base session.  New Coming Students are admissions
    whose target_session_start_year is the immediately following session.
    """
    base_year = _validated_base_session_start_year(base_session_start_year)
    target_year = base_year + 1
    cutoff = datetime(target_year, 3, 31, 23, 59, 59, 999999)

    connection = get_connection(automatic_backup=False)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        cursor = connection.cursor()

        class_rows = cursor.execute(
            """
            SELECT
                c.id AS class_id,
                c.class_name,
                c.class_level,
                c.section,
                c.student_capacity,
                c.is_active,
                COUNT(CASE
                    WHEN s.student_status='Active'
                      OR (s.student_status='Coming Session'
                          AND s.coming_session_status='PENDING')
                    THEN 1 END
                ) AS operational_reserved_strength
            FROM classes AS c
            LEFT JOIN students AS s ON s.class_id=c.id
            GROUP BY c.id
            """
        ).fetchall()
        classes = {int(row["class_id"]): dict(row) for row in class_rows}

        class_at_cutoff = _load_class_snapshot_by_student(cursor, target_year)
        status_before_first_event_after_cutoff = _load_first_status_after_cutoff(
            cursor, cutoff
        )

        active_counts: defaultdict[int, int] = defaultdict(int)
        coming_counts: defaultdict[int, int] = defaultdict(int)

        students = cursor.execute(
            """
            SELECT student_id, class_id, student_status, admission_date,
                   target_session_start_year
            FROM students
            """
        ).fetchall()
        for student in students:
            admitted_at = _local_naive_datetime(
                student["admission_date"],
                field="Student admission date/time",
            )
            if admitted_at > cutoff:
                continue

            student_id = int(student["student_id"])
            snapshot_class_id = class_at_cutoff.get(
                student_id, int(student["class_id"])
            )
            snapshot_status = status_before_first_event_after_cutoff.get(
                student_id, str(student["student_status"] or "")
            )

            if snapshot_status == STATUS_ACTIVE:
                active_counts[snapshot_class_id] += 1

            if (
                student["target_session_start_year"] is not None
                and int(student["target_session_start_year"]) == target_year
            ):
                coming_counts[snapshot_class_id] += 1

        report_rows: list[dict] = []
        relevant_class_ids = {
            class_id
            for class_id, row in classes.items()
            if int(row.get("is_active") or 0) == 1
        }
        relevant_class_ids.update(active_counts)
        relevant_class_ids.update(coming_counts)

        for class_id in relevant_class_ids:
            class_row = classes.get(class_id)
            if class_row is None:
                continue
            existing_active = int(active_counts.get(class_id, 0))
            new_coming = int(coming_counts.get(class_id, 0))
            capacity = int(
                class_row.get("student_capacity")
                or class_service.DEFAULT_CLASS_CAPACITY
            )
            expected_total = existing_active + new_coming
            available_seats = capacity - expected_total
            report_rows.append(
                {
                    "class_id": class_id,
                    "class_name": str(class_row.get("class_name") or ""),
                    "class_level": str(class_row.get("class_level") or ""),
                    "section": str(class_row.get("section") or ""),
                    "is_active": bool(int(class_row.get("is_active") or 0)),
                    "new_coming_students": new_coming,
                    "existing_active_students": existing_active,
                    "class_capacity": capacity,
                    "minimum_capacity": max(
                        1,
                        int(class_row.get("operational_reserved_strength") or 0),
                    ),
                    "expected_total": expected_total,
                    "available_seats": available_seats,
                    "status": (
                        "Over Capacity" if available_seats < 0 else "Available"
                    ),
                }
            )

        report_rows.sort(key=_class_sort_key)
        connection.rollback()

        return {
            "base_session_start_year": base_year,
            "base_session_label": f"{base_year}-{base_year + 1}",
            "coming_session_start_year": target_year,
            "coming_session_label": f"{target_year}-{target_year + 1}",
            "snapshot_cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            "rows": report_rows,
            "class_count": len(report_rows),
            "new_coming_total": sum(
                int(row["new_coming_students"]) for row in report_rows
            ),
            "existing_active_total": sum(
                int(row["existing_active_students"]) for row in report_rows
            ),
            "expected_total": sum(int(row["expected_total"]) for row in report_rows),
            "over_capacity_class_count": sum(
                1 for row in report_rows if row["status"] == "Over Capacity"
            ),
            "generated_at": datetime.now().strftime("%d-%m-%Y %I:%M %p"),
        }
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def save_class_capacities(capacity_by_class_id: dict[int, int]) -> dict[int, int]:
    """Atomically persist report-page capacity edits using class rules."""
    if not capacity_by_class_id:
        return {}

    normalized: dict[int, int] = {}
    for class_id, capacity in capacity_by_class_id.items():
        normalized[int(class_id)] = class_service.validate_student_capacity(capacity)

    connection = get_connection()
    cursor = connection.cursor()
    saved: dict[int, int] = {}
    try:
        connection.execute("BEGIN IMMEDIATE")
        for class_id in sorted(normalized):
            class_row = cursor.execute(
                "SELECT * FROM classes WHERE id=?", (int(class_id),)
            ).fetchone()
            if class_row is None:
                raise ValueError(f"Class ID {class_id} could not be found.")
            result = class_service._save_class_settings_with_cursor(
                cursor,
                class_row,
                student_capacity=normalized[class_id],
                active=bool(int(class_row["is_active"] or 0)),
            )
            saved[class_id] = int(result["student_capacity"])
        connection.commit()
        return saved
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
