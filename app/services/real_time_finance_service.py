"""Consistent-snapshot Real-Time Finance service."""

from datetime import date, datetime

from app.database import get_connection


def parse_search_date(selected_date=None) -> date:
    if selected_date is None:
        return date.today()
    if isinstance(selected_date, datetime):
        return selected_date.date()
    if isinstance(selected_date, date):
        return selected_date
    if isinstance(selected_date, str):
        try:
            return datetime.strptime(selected_date.strip(), "%Y-%m-%d").date()
        except ValueError as error:
            raise ValueError("The selected date is invalid.") from error
    raise ValueError("The selected date is invalid.")


def _payment_session_sql() -> str:
    return """
        COALESCE((
            SELECT CASE
                WHEN COUNT(DISTINCT NULLIF(TRIM(fr.session_year_snapshot), '')) = 0
                    THEN '-'
                WHEN COUNT(DISTINCT NULLIF(TRIM(fr.session_year_snapshot), '')) = 1
                    THEN MIN(NULLIF(TRIM(fr.session_year_snapshot), ''))
                ELSE 'MULTIPLE SESSIONS'
            END
            FROM fee_receipts AS fr
            WHERE fr.payment_id = fp.id
              AND fr.status = 'Paid'
        ), '-') AS session_year
    """


def get_fee_payments_for_date(selected_date=None, *, connection=None) -> list:
    parsed_date = parse_search_date(selected_date)
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT
                fp.id, fp.receipt_no, fp.student_id, fp.payment_date,
                fp.total_amount, fp.received_by,
                fp.admission_number_snapshot AS admission_number,
                fp.roll_number_snapshot AS roll_number,
                fp.student_name_snapshot AS student_name,
                fp.father_name_snapshot AS father_name,
                fp.class_name_snapshot AS class_name,
                {_payment_session_sql()}
            FROM fee_payments AS fp
            WHERE fp.payment_status = 'Completed'
              AND substr(trim(fp.payment_date), 1, 10) = ?
            ORDER BY fp.payment_date ASC, fp.id ASC
            """,
            (parsed_date.strftime("%Y-%m-%d"),),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        if owns_connection:
            connection.close()


def get_total_fee_collected_for_date(selected_date=None, *, connection=None) -> float:
    return sum(
        float(row["total_amount"])
        for row in get_fee_payments_for_date(selected_date, connection=connection)
    )


def get_expenses_for_date(selected_date=None, *, connection=None) -> list:
    parsed_date = parse_search_date(selected_date)
    owns_connection = connection is None
    if owns_connection:
        connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id, expense_date, amount, description, session_year, created_at
            FROM expenses
            WHERE expense_date = ?
            ORDER BY created_at ASC, id ASC
            """,
            (parsed_date.strftime("%Y-%m-%d"),),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        if owns_connection:
            connection.close()


def get_total_expenses_for_date(selected_date=None, *, connection=None) -> float:
    return sum(
        float(row["amount"])
        for row in get_expenses_for_date(selected_date, connection=connection)
    )


def get_real_time_finance_summary(selected_date=None) -> dict:
    """Read payments, expenses and totals from one SQLite snapshot."""
    parsed_date = parse_search_date(selected_date)
    connection = get_connection()
    try:
        connection.execute("BEGIN")
        fee_payment_rows = get_fee_payments_for_date(
            parsed_date, connection=connection
        )
        expense_rows = get_expenses_for_date(parsed_date, connection=connection)
        total_fee_collected = sum(float(row["total_amount"]) for row in fee_payment_rows)
        total_expenses = sum(float(row["amount"]) for row in expense_rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    return {
        "selected_date": parsed_date.strftime("%Y-%m-%d"),
        "formatted_date": parsed_date.strftime("%d-%m-%Y"),
        "display_date": parsed_date.strftime("%d %B %Y"),
        "is_today": parsed_date == date.today(),
        "fee_payment_rows": fee_payment_rows,
        "expense_rows": expense_rows,
        "fee_payment_count": len(fee_payment_rows),
        "expense_count": len(expense_rows),
        "total_fee_collected": total_fee_collected,
        "total_expenses": total_expenses,
        "final_income": total_fee_collected - total_expenses,
    }


def get_today_finance_summary() -> dict:
    return get_real_time_finance_summary(date.today())
