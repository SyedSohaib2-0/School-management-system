"""Income Management service using one consistent session snapshot."""

from datetime import datetime

from app.config import SESSION_MONTH_ORDER
from app.database import get_connection
from app.services.expense_service import get_expense_session_start_year

SESSION_MONTHS = [month_number for _month_name, month_number in SESSION_MONTH_ORDER]
MONTH_NUMBER_TO_NAME = {month_number: month_name for month_name, month_number in SESSION_MONTH_ORDER}
MONTH_NUMBER_TO_ABBR = {month_number: month_name[:3] for month_name, month_number in SESSION_MONTH_ORDER}


def get_calendar_year_for_session_month(session_start_year: int, month_number: int) -> int:
    return int(session_start_year) if int(month_number) >= 4 else int(session_start_year) + 1


def get_session_label(session_start_year: int) -> str:
    start = int(session_start_year)
    return f"{start}-{start + 1}"


def _load_monthly_snapshot(connection, session_start_year: int) -> list:
    session_start_year = int(session_start_year)
    start_text = f"{session_start_year:04d}-04-01"
    end_text = f"{session_start_year + 1:04d}-04-01"
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT CAST(substr(trim(payment_date), 1, 4) AS INTEGER) AS calendar_year,
               CAST(substr(trim(payment_date), 6, 2) AS INTEGER) AS month_number,
               COALESCE(SUM(total_amount), 0) AS collected_fee
        FROM fee_payments
        WHERE payment_status = 'Completed'
          AND substr(trim(payment_date), 1, 10) >= ?
          AND substr(trim(payment_date), 1, 10) < ?
        GROUP BY calendar_year, month_number
        """,
        (start_text, end_text),
    )
    fees = {
        (int(row["calendar_year"]), int(row["month_number"])): float(row["collected_fee"] or 0)
        for row in cursor.fetchall()
    }
    cursor.execute(
        """
        SELECT calendar_year, month_number, COALESCE(SUM(amount), 0) AS total_expenses
        FROM expenses
        WHERE session_start_year = ?
        GROUP BY calendar_year, month_number
        """,
        (session_start_year,),
    )
    expenses = {
        (int(row["calendar_year"]), int(row["month_number"])): float(row["total_expenses"] or 0)
        for row in cursor.fetchall()
    }
    rows = []
    for month_number in SESSION_MONTHS:
        calendar_year = get_calendar_year_for_session_month(session_start_year, month_number)
        collected_fee = fees.get((calendar_year, month_number), 0.0)
        total_expenses = expenses.get((calendar_year, month_number), 0.0)
        rows.append({
            "month_number": month_number,
            "month_name": MONTH_NUMBER_TO_NAME[month_number],
            "short_month_name": MONTH_NUMBER_TO_ABBR[month_number],
            "calendar_year": calendar_year,
            "month_label": f"{MONTH_NUMBER_TO_ABBR[month_number]} {str(calendar_year)[-2:]}",
            "collected_fee": collected_fee,
            "total_expenses": total_expenses,
            "final_income": collected_fee - total_expenses,
        })
    return rows


def get_income_report_snapshot(session_start_year: int, month_number=None) -> dict:
    """Return KPIs, table and graph rows from one read transaction."""
    session_start_year = int(session_start_year)
    if month_number is not None:
        month_number = int(month_number)
        if month_number not in SESSION_MONTHS:
            raise ValueError("Please select a valid month.")
    connection = get_connection()
    try:
        connection.execute("BEGIN")
        monthly_rows = _load_monthly_snapshot(connection, session_start_year)
        if month_number is None:
            selected_rows = monthly_rows
            search_label = f"Session {get_session_label(session_start_year)}"
        else:
            selected_rows = [row for row in monthly_rows if row["month_number"] == month_number]
            calendar_year = get_calendar_year_for_session_month(session_start_year, month_number)
            search_label = f"{MONTH_NUMBER_TO_NAME[month_number]} {calendar_year}"
        collected_fee = sum(row["collected_fee"] for row in selected_rows)
        total_expenses = sum(row["total_expenses"] for row in selected_rows)
        summary = {
            "session_start_year": session_start_year,
            "session_year": get_session_label(session_start_year),
            "month_number": month_number,
            "search_label": search_label,
            "collected_fee": collected_fee,
            "total_expenses": total_expenses,
            "final_income": collected_fee - total_expenses,
        }
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"summary": summary, "monthly_rows": monthly_rows}


def get_collected_fee_for_month(session_start_year: int, month_number: int) -> float:
    snapshot = get_income_report_snapshot(session_start_year, month_number)
    return float(snapshot["summary"]["collected_fee"])


def get_collected_fee_for_session(session_start_year: int) -> float:
    return float(get_income_report_snapshot(session_start_year)["summary"]["collected_fee"])


def get_expenses_for_month(session_start_year: int, month_number: int) -> float:
    snapshot = get_income_report_snapshot(session_start_year, month_number)
    return float(snapshot["summary"]["total_expenses"])


def get_expenses_for_session(session_start_year: int) -> float:
    return float(get_income_report_snapshot(session_start_year)["summary"]["total_expenses"])


def get_income_summary(session_start_year: int, month_number=None) -> dict:
    return get_income_report_snapshot(session_start_year, month_number)["summary"]


def get_monthly_income_comparison(session_start_year: int) -> list:
    return get_income_report_snapshot(session_start_year)["monthly_rows"]


def get_available_income_session_years() -> list:
    today = datetime.now()
    current = get_expense_session_start_year(today.month, today.year)
    session_years = {current}
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT DISTINCT session_start_year FROM expenses")
        for row in cursor.fetchall():
            session_years.add(int(row["session_start_year"]))
        cursor.execute(
            """
            SELECT DISTINCT
                CAST(substr(trim(payment_date), 1, 4) AS INTEGER) AS payment_year,
                CAST(substr(trim(payment_date), 6, 2) AS INTEGER) AS payment_month
            FROM fee_payments
            WHERE payment_status = 'Completed'
              AND payment_date IS NOT NULL
              AND length(trim(payment_date)) >= 10
            """
        )
        for row in cursor.fetchall():
            if row["payment_year"] is not None and row["payment_month"] is not None:
                session_years.add(get_expense_session_start_year(
                    int(row["payment_month"]), int(row["payment_year"])
                ))
    finally:
        connection.close()
    return sorted(session_years, reverse=True)
