"""Read-only school statistics for the April-to-March academic session."""

from __future__ import annotations

from datetime import datetime

from app.config import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_COMING_SESSION,
    STATUS_DROPPED,
    STATUS_GRADUATED,
    STATUS_PARTIALLY_GRADUATED,
)
from app.database import get_connection
from app.services import income_service, student_service


STATISTICS_STATUS_ORDER = (
    STATUS_ACTIVE,
    STATUS_COMING_SESSION,
    STATUS_DROPPED,
    STATUS_ARCHIVED,
    STATUS_GRADUATED,
    STATUS_PARTIALLY_GRADUATED,
)


def _validated_session_start_year(session_start_year: int) -> int:
    try:
        normalized_year = int(session_start_year)
    except (TypeError, ValueError) as error:
        raise ValueError("Please select a valid academic session.") from error

    if normalized_year < 2000 or normalized_year > 9998:
        raise ValueError("Please select a valid academic session.")

    return normalized_year


def get_available_statistics_session_years() -> list[int]:
    """Return every admission or finance session, newest first."""
    session_years = set(
        student_service.available_admission_session_start_years()
    )
    session_years.update(
        income_service.get_available_income_session_years()
    )
    session_years.add(
        student_service.current_academic_session_start_year()
    )
    return sorted(
        (int(year) for year in session_years),
        reverse=True,
    )


def _load_student_statistics(session_start_year: int) -> dict:
    """Read current strength and one admission cohort without writing data."""
    connection = get_connection(automatic_backup=False)

    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT COUNT(*) AS student_count
            FROM students
            WHERE student_status = ?
            """,
            (STATUS_ACTIVE,),
        )
        active_strength = int(
            cursor.fetchone()["student_count"] or 0
        )

        cursor.execute(
            """
            SELECT student_status, COUNT(*) AS student_count
            FROM students
            WHERE CASE
                WHEN target_session_start_year IS NOT NULL
                    THEN target_session_start_year
                WHEN admission_month >= 4 THEN admission_year
                ELSE admission_year - 1
            END = ?
            GROUP BY student_status
            """,
            (int(session_start_year),),
        )
        stored_counts = {
            str(row["student_status"]): int(
                row["student_count"] or 0
            )
            for row in cursor.fetchall()
        }
        status_counts = {
            status: stored_counts.get(status, 0)
            for status in STATISTICS_STATUS_ORDER
        }

        connection.rollback()
        return {
            "active_strength": active_strength,
            "session_admissions": sum(status_counts.values()),
            "status_counts": status_counts,
        }

    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise

    finally:
        connection.close()


def get_statistics_snapshot(session_start_year: int) -> dict:
    """Return all Statistics page values from read-only source snapshots."""
    normalized_year = _validated_session_start_year(
        session_start_year
    )
    student_snapshot = _load_student_statistics(normalized_year)
    finance_snapshot = income_service.get_income_report_snapshot(
        normalized_year
    )
    finance_summary = finance_snapshot["summary"]

    return {
        "session_start_year": normalized_year,
        "session_year": f"{normalized_year}-{normalized_year + 1}",
        "refreshed_at": datetime.now().strftime("%d-%m-%Y %I:%M %p"),
        "active_strength": student_snapshot["active_strength"],
        "session_admissions": student_snapshot["session_admissions"],
        "status_counts": student_snapshot["status_counts"],
        "collected_fee": float(finance_summary["collected_fee"]),
        "total_expenses": float(finance_summary["total_expenses"]),
        "net_balance": float(finance_summary["final_income"]),
        "monthly_rows": list(finance_snapshot["monthly_rows"]),
        "read_only": True,
    }
