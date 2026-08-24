"""Read-only snapshots for Fee Setting sibling and individual-fee reports."""

from __future__ import annotations

from calendar import month_name
from datetime import datetime

from app.config import STATUS_ACTIVE
from app.database import get_connection
from app.services import fee_service, student_service


def _money(value) -> float | None:
    return None if value is None else round(float(value), 2)


def _period_label(month_number: int, year: int) -> str:
    return f"{month_name[int(month_number)]} {int(year)}"


def _previous_period(month_number: int, year: int) -> tuple[int, int]:
    if int(month_number) == 1:
        return 12, int(year) - 1
    return int(month_number) - 1, int(year)


def _current_class_fee(cursor, class_id: int, month_number: int, year: int):
    try:
        row = fee_service._get_class_fee_for_period(
            cursor, int(class_id), int(month_number), int(year)
        )
    except ValueError:
        return None
    return _money(row["monthly_fee"])


def get_sibling_fee_report_snapshot(as_of: datetime | None = None) -> dict:
    """Return current active sibling families without altering fee data."""
    as_of = as_of or datetime.now()  # noqa: DTZ005 - school-local reporting time
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT s.student_id, s.tracking_id, s.admission_number,
                   s.student_name, s.roll_number, s.released_roll_number,
                   s.student_status,
                   s.father_name, s.father_cnic, s.father_contact_number,
                   s.class_id, c.class_name, c.class_level, c.section,
                   a.normalized_father_name, a.normalized_father_cnic,
                   a.monthly_fee_percentage, a.assignment_role
            FROM students AS s
            LEFT JOIN classes AS c ON c.id = s.class_id
            JOIN sibling_fee_assignments AS a
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
            ORDER BY a.normalized_father_name, a.normalized_father_cnic,
                     s.student_id
            """,
            (as_of.year, as_of.year, as_of.month, STATUS_ACTIVE),
        )
        grouped: dict[tuple[str, str], list] = {}
        for row in cursor.fetchall():
            key = (
                str(row["normalized_father_name"] or ""),
                str(row["normalized_father_cnic"] or ""),
            )
            grouped.setdefault(key, []).append(row)

        families = []
        for family_number, (key, rows) in enumerate(sorted(grouped.items()), start=1):
            students = []
            for row in rows:
                class_fee = _current_class_fee(
                    cursor, row["class_id"], as_of.month, as_of.year
                )
                try:
                    effective_fee = fee_service.get_effective_fee(
                        cursor,
                        int(row["class_id"]),
                        int(row["student_id"]),
                        as_of.month,
                        as_of.year,
                    )
                    current_fee = _money(effective_fee["monthly_fee"])
                    fee_source = str(effective_fee.get("fee_source") or "Class")
                except ValueError:
                    current_fee = None
                    fee_source = "Fee Not Set"
                students.append(
                    {
                        "student_id": int(row["student_id"]),
                        "tracking_id": str(row["tracking_id"] or "-"),
                        "admission_number": str(row["admission_number"] or "-"),
                        "student_name": str(row["student_name"] or "-"),
                        "roll_number": student_service.display_roll_number(row),
                        "class_name": str(row["class_name"] or "-"),
                        "class_level": str(row["class_level"] or ""),
                        "section": str(row["section"] or ""),
                        "assignment_role": str(row["assignment_role"] or "-"),
                        "sibling_percentage": float(row["monthly_fee_percentage"]),
                        "class_fee": class_fee,
                        "current_fee": current_fee,
                        "fee_source": fee_source,
                    }
                )

            students.sort(
                key=lambda student: (
                    0 if student["assignment_role"] == "FULL" else 1,
                    student["class_name"],
                    student["student_name"],
                    student["student_id"],
                )
            )
            display_row = rows[0]
            families.append(
                {
                    "family_number": family_number,
                    "family_key": key,
                    "father_name": str(display_row["father_name"] or key[0] or "-"),
                    "father_cnic": str(display_row["father_cnic"] or key[1] or "-"),
                    "father_contact_number": str(
                        next(
                            (
                                row["father_contact_number"]
                                for row in rows
                                if str(row["father_contact_number"] or "").strip()
                            ),
                            "-",
                        )
                    ),
                    "students": students,
                }
            )

        return {
            "as_of_month": as_of.month,
            "as_of_year": as_of.year,
            "as_of_label": _period_label(as_of.month, as_of.year),
            "generated_at": as_of.strftime("%d-%m-%Y %I:%M %p"),
            "family_count": len(families),
            "student_count": sum(len(family["students"]) for family in families),
            "families": families,
            "report_read_only": True,
        }
    finally:
        connection.close()


def get_individual_fee_report_snapshot(as_of: datetime | None = None) -> dict:
    """Return every student whose individual fee rule applies now."""
    as_of = as_of or datetime.now()  # noqa: DTZ005 - school-local reporting time
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT s.student_id, s.tracking_id, s.admission_number,
                   s.student_name, s.roll_number, s.released_roll_number,
                   s.student_status, s.class_id, c.class_name,
                   overrides.monthly_fee, overrides.monthly_fee_percentage,
                   overrides.effective_from_month, overrides.effective_from_year,
                   overrides.effective_to_month, overrides.effective_to_year,
                   overrides.removed_effective_month,
                   overrides.removed_effective_year
            FROM students AS s
            LEFT JOIN classes AS c ON c.id = s.class_id
            JOIN student_fee_override AS overrides
              ON overrides.id = (
                    SELECT candidate.id
                    FROM student_fee_override AS candidate
                    WHERE candidate.student_id = s.student_id
                      AND (
                            candidate.effective_from_year < ?
                            OR (
                                candidate.effective_from_year = ?
                                AND candidate.effective_from_month <= ?
                            )
                          )
                      AND (
                            candidate.effective_to_year IS NULL
                            OR candidate.effective_to_year > ?
                            OR (
                                candidate.effective_to_year = ?
                                AND candidate.effective_to_month >= ?
                            )
                          )
                      AND (
                            candidate.removed_effective_year IS NULL
                            OR candidate.removed_effective_year > ?
                            OR (
                                candidate.removed_effective_year = ?
                                AND candidate.removed_effective_month > ?
                            )
                          )
                    ORDER BY candidate.effective_from_year DESC,
                             candidate.effective_from_month DESC,
                             candidate.id DESC
                    LIMIT 1
                 )
            ORDER BY c.class_level, c.section, s.student_name, s.student_id
            """,
            (
                as_of.year, as_of.year, as_of.month,
                as_of.year, as_of.year, as_of.month,
                as_of.year, as_of.year, as_of.month,
            ),
        )
        students = []
        for row in cursor.fetchall():
            class_fee = _current_class_fee(
                cursor, row["class_id"], as_of.month, as_of.year
            )
            try:
                current = fee_service.get_effective_fee(
                    cursor,
                    int(row["class_id"]),
                    int(row["student_id"]),
                    as_of.month,
                    as_of.year,
                )
                individual_fee = _money(current["monthly_fee"])
            except ValueError:
                individual_fee = None

            end_period = None
            if row["effective_to_year"] is not None:
                end_period = (
                    int(row["effective_to_month"]),
                    int(row["effective_to_year"]),
                )
            if row["removed_effective_year"] is not None:
                removal_end = _previous_period(
                    row["removed_effective_month"], row["removed_effective_year"]
                )
                if end_period is None or (removal_end[1], removal_end[0]) < (
                    end_period[1], end_period[0]
                ):
                    end_period = removal_end

            students.append(
                {
                    "student_id": int(row["student_id"]),
                    "tracking_id": str(row["tracking_id"] or "-"),
                    "admission_number": str(row["admission_number"] or "-"),
                    "student_name": str(row["student_name"] or "-"),
                    "roll_number": student_service.display_roll_number(row),
                    "student_status": str(row["student_status"] or "-"),
                    "class_name": str(row["class_name"] or "-"),
                    "monthly_fee_percentage": (
                        None
                        if row["monthly_fee_percentage"] is None
                        else float(row["monthly_fee_percentage"])
                    ),
                    "class_fee": class_fee,
                    "individual_fee": individual_fee,
                    "effective_from": _period_label(
                        row["effective_from_month"], row["effective_from_year"]
                    ),
                    "effective_to": (
                        "Ongoing"
                        if end_period is None
                        else _period_label(end_period[0], end_period[1])
                    ),
                }
            )

        return {
            "as_of_month": as_of.month,
            "as_of_year": as_of.year,
            "as_of_label": _period_label(as_of.month, as_of.year),
            "generated_at": as_of.strftime("%d-%m-%Y %I:%M %p"),
            "student_count": len(students),
            "students": students,
            "report_read_only": True,
        }
    finally:
        connection.close()
