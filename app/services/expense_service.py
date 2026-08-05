"""
Expenses Management service.

Contains all validation, date/session calculation, database insertion,
searching, and total-calculation logic for school expenses.

The school financial session runs from April to March.

Example:
April 2026 through March 2027 belongs to session 2026-2027.
"""

from datetime import datetime, date
import math

from app.database import get_connection
from app.config import (
    SESSION_START_CALENDAR_MONTH,
    get_session_year_label,
)


MAX_EXPENSE_DESCRIPTION_LENGTH = 1000


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_expense_amount(amount) -> bool:
    """Return True when the expense amount is numeric and greater than zero."""
    try:
        numeric_amount = float(amount)
    except (TypeError, ValueError, OverflowError):
        return False

    return math.isfinite(numeric_amount) and numeric_amount > 0


def validate_expense_description(description: str) -> bool:
    """Validate the required normalized expense-description length."""
    if description is None:
        return False
    normalized = normalize_expense_description(description)
    return 1 <= len(normalized) <= MAX_EXPENSE_DESCRIPTION_LENGTH


def normalize_expense_description(description: str) -> str:
    """Collapse whitespace without silently truncating expense text."""
    return " ".join(str(description or "").split())


def validate_calendar_date(
    calendar_year: int,
    month_number: int,
    day_number: int,
) -> bool:
    """Return True when the supplied year, month, and day form a real date."""
    try:
        date(
            int(calendar_year),
            int(month_number),
            int(day_number),
        )
    except (TypeError, ValueError):
        return False

    return True


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_expense_session_start_year(
    month_number: int,
    calendar_year: int,
) -> int:
    """
    Return the starting year of the April-to-March session.

    Examples:
    July 2026    -> 2026
    January 2027 -> 2026
    March 2027   -> 2026
    April 2027   -> 2027
    """
    month_number = int(month_number)
    calendar_year = int(calendar_year)

    if month_number >= SESSION_START_CALENDAR_MONTH:
        return calendar_year

    return calendar_year - 1


def get_expense_session_label(
    month_number: int,
    calendar_year: int,
) -> str:
    """Return a session label such as 2026-2027."""
    session_start_year = get_expense_session_start_year(
        month_number,
        calendar_year,
    )

    return get_session_year_label(
        session_start_year
    )


# ---------------------------------------------------------------------------
# Add Expense
# ---------------------------------------------------------------------------

def add_expense(
    expense_date,
    amount,
    description: str,
) -> int:
    """
    Add one expense and return its database id.

    expense_date may be:
    - datetime.date
    - datetime.datetime
    - text in YYYY-MM-DD format
    """
    if not validate_expense_amount(amount):
        raise ValueError(
            "Expense amount must be greater than zero."
        )

    normalized_description = normalize_expense_description(description)
    if not normalized_description:
        raise ValueError("Expense description is required.")
    if len(normalized_description) > MAX_EXPENSE_DESCRIPTION_LENGTH:
        raise ValueError("Expense description cannot exceed 1,000 characters.")

    if isinstance(expense_date, datetime):
        parsed_date = expense_date.date()

    elif isinstance(expense_date, date):
        parsed_date = expense_date

    elif isinstance(expense_date, str):
        try:
            parsed_date = datetime.strptime(
                expense_date,
                "%Y-%m-%d",
            ).date()
        except ValueError as error:
            raise ValueError(
                "Expense date must be a valid date."
            ) from error

    else:
        raise ValueError(
            "Expense date must be a valid date."
        )

    if parsed_date > date.today():
        raise ValueError("Expense Date cannot be in the future.")

    calendar_year = parsed_date.year
    month_number = parsed_date.month
    day_number = parsed_date.day

    session_start_year = (
        get_expense_session_start_year(
            month_number,
            calendar_year,
        )
    )

    session_year = get_session_year_label(
        session_start_year
    )

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            INSERT INTO expenses (
                expense_date,
                day_number,
                month_number,
                calendar_year,
                session_start_year,
                session_year,
                amount,
                description,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                parsed_date.strftime("%Y-%m-%d"),
                day_number,
                month_number,
                calendar_year,
                session_start_year,
                session_year,
                float(amount),
                normalized_description,
                now,
                now,
            ),
        )

        connection.commit()
        return cursor.lastrowid

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

def _rows_with_total(rows) -> dict:
    """Return search rows together with their total expense."""
    expense_rows = list(rows)

    total_amount = sum(
        float(row["amount"])
        for row in expense_rows
    )

    return {
        "rows": expense_rows,
        "count": len(expense_rows),
        "total_amount": total_amount,
    }


def search_expenses_by_exact_date(
    calendar_year: int,
    month_number: int,
    day_number: int,
) -> dict:
    """Search expenses for one exact calendar date."""
    if not validate_calendar_date(
        calendar_year,
        month_number,
        day_number,
    ):
        raise ValueError(
            "Please enter a valid year, month, and day."
        )

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM expenses
            WHERE calendar_year = ?
              AND month_number = ?
              AND day_number = ?
            ORDER BY id ASC
            """,
            (
                int(calendar_year),
                int(month_number),
                int(day_number),
            ),
        )
        return _rows_with_total(cursor.fetchall())
    finally:
        connection.close()


def search_expenses_by_month(
    month_number: int,
    session_start_year=None,
) -> dict:
    """
    Search expenses for a month.

    When session_start_year is supplied, only that April-to-March
    session is searched.

    When session_start_year is None, the selected calendar month is
    searched across every stored year.
    """
    try:
        month_number = int(month_number)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Please select a valid month."
        ) from error

    if month_number < 1 or month_number > 12:
        raise ValueError(
            "Please select a valid month."
        )

    connection = get_connection()
    try:
        cursor = connection.cursor()
        if session_start_year is None:
            cursor.execute(
                """
                SELECT *
                FROM expenses
                WHERE month_number = ?
                ORDER BY
                    calendar_year ASC,
                    month_number ASC,
                    day_number ASC,
                    id ASC
                """,
                (month_number,),
            )
        else:
            cursor.execute(
                """
                SELECT *
                FROM expenses
                WHERE session_start_year = ?
                  AND month_number = ?
                ORDER BY
                    calendar_year ASC,
                    month_number ASC,
                    day_number ASC,
                    id ASC
                """,
                (
                    int(session_start_year),
                    month_number,
                ),
            )
        return _rows_with_total(cursor.fetchall())
    finally:
        connection.close()


def search_expenses_by_session_year(
    session_start_year: int,
) -> dict:
    """
    Search one complete April-to-March session.

    Example:
    session_start_year 2026 returns expenses from
    April 2026 through March 2027.
    """
    try:
        session_start_year = int(
            session_start_year
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Please select a valid session year."
        ) from error

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT *
            FROM expenses
            WHERE session_start_year = ?
            ORDER BY
                CASE
                    WHEN month_number >= 4
                        THEN month_number
                    ELSE month_number + 12
                END ASC,
                calendar_year ASC,
                day_number ASC,
                id ASC
            """,
            (session_start_year,),
        )
        return _rows_with_total(cursor.fetchall())
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Session-year dropdown
# ---------------------------------------------------------------------------

def get_available_expense_session_years():
    """
    Return stored expense session years in descending order.

    The current session is included even when no expense has yet been added.
    """
    today = datetime.now()

    current_session_start_year = (
        get_expense_session_start_year(
            today.month,
            today.year,
        )
    )

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT session_start_year
            FROM expenses
            ORDER BY session_start_year DESC
            """
        )
        rows = cursor.fetchall()
    finally:
        connection.close()

    session_years = [
        int(row["session_start_year"])
        for row in rows
    ]

    if current_session_start_year not in session_years:
        session_years.insert(
            0,
            current_session_start_year,
        )

    return session_years