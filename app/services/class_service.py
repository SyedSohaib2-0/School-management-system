"""
Class service module.
Contains all validation rules and database operations for classes.
Screens (UI files) must call these functions instead of writing SQL
or validation logic directly, so that rules stay consistent everywhere.
"""

import re
from datetime import datetime

from app.database import get_connection
from app.services import student_service
from app.config import (
    ALL_CLASS_LEVELS,
    SECTION_NAME_PATTERN,
    STATUS_ACTIVE,
    STATUS_COMING_SESSION,
    STATUS_PARTIALLY_GRADUATED,
    build_class_name,
    section_rank,
)


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------


# CLASS_CAPACITY_SERVICE_BACKEND_V1

DEFAULT_CLASS_CAPACITY = 40


def validate_student_capacity(
    student_capacity,
) -> int:
    """
    Convert and validate a class capacity value.

    Capacity must be a whole number greater than zero.
    """
    try:
        capacity = int(
            student_capacity
        )

    except (
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            "Student capacity must be a whole number."
        ) from error

    if capacity < 1:
        raise ValueError(
            "Student capacity must be greater than zero."
        )

    return capacity


def get_class_capacity_details(
    class_id: int,
):
    """
    Return the class record together with its Active-student
    count and remaining available seats.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                c.*,
                COUNT(
                    CASE
                        WHEN s.student_status = 'Active'
                        THEN 1
                    END
                ) AS active_students,
                COUNT(
                    CASE
                        WHEN s.student_status = 'Coming Session'
                         AND s.coming_session_status = 'PENDING'
                        THEN 1
                    END
                ) AS coming_session_reserved
            FROM classes c
            LEFT JOIN students s
              ON s.class_id = c.id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (
                int(
                    class_id
                ),
            ),
        )

        row = cursor.fetchone()

        if not row:
            return None

        details = dict(
            row
        )

        capacity = int(
            details.get(
                "student_capacity",
                DEFAULT_CLASS_CAPACITY,
            )
            or DEFAULT_CLASS_CAPACITY
        )

        active_students = int(
            details.get(
                "active_students",
                0,
            )
            or 0
        )

        details[
            "student_capacity"
        ] = capacity

        coming_session_reserved = int(
            details.get(
                "coming_session_reserved",
                0,
            )
            or 0
        )

        details[
            "active_students"
        ] = active_students

        details[
            "coming_session_reserved"
        ] = coming_session_reserved

        details[
            "new_session_reserved_strength"
        ] = active_students + coming_session_reserved

        details[
            "available_seats"
        ] = max(
            0,
            capacity - active_students - coming_session_reserved,
        )

        return details

    finally:
        connection.close()


def _class_student_state_counts(cursor, class_id: int) -> dict:
    """Return occupancy and operational references for one class."""
    row = cursor.execute(
        """
        SELECT
            COUNT(CASE WHEN student_status = ? THEN 1 END) AS active_students,
            COUNT(CASE
                WHEN student_status = ?
                 AND coming_session_status = 'PENDING'
                THEN 1 END) AS coming_session_reserved,
            COUNT(CASE WHEN student_status = ? THEN 1 END) AS partially_graduated
        FROM students
        WHERE class_id = ?
        """,
        (
            STATUS_ACTIVE,
            STATUS_COMING_SESSION,
            STATUS_PARTIALLY_GRADUATED,
            int(class_id),
        ),
    ).fetchone()
    return {
        "active_students": int(row["active_students"] or 0),
        "coming_session_reserved": int(row["coming_session_reserved"] or 0),
        "partially_graduated": int(row["partially_graduated"] or 0),
    }


def _save_class_settings_with_cursor(
    cursor,
    class_row,
    *,
    student_capacity,
    active: bool,
) -> dict:
    """Validate and save class capacity/status in the caller transaction."""
    class_id = int(class_row["id"])
    desired_active = 1 if bool(active) else 0
    capacity = validate_student_capacity(student_capacity)
    level = str(class_row["class_level"] or "").strip().upper()
    section = str(class_row["section"] or "").strip().upper()

    if not validate_class_level(level):
        raise ValueError(
            "The class has an invalid level and its settings cannot be changed."
        )

    valid_section = validate_section(section, level)
    if desired_active and not valid_section:
        raise ValueError(
            "This class has an invalid section for its level. "
            "Classes 4 through 10 require one letter A-Z; keep it Inactive "
            "until corrected."
        )

    if desired_active:
        duplicate = cursor.execute(
            """
            SELECT 1 FROM classes
            WHERE id <> ?
              AND UPPER(TRIM(class_level)) = ?
              AND UPPER(TRIM(section)) = ?
              AND is_active = 1
            LIMIT 1
            """,
            (class_id, level, section),
        ).fetchone()
        if duplicate is not None:
            raise ValueError(
                "Another Active class already uses this class level and section."
            )

    counts = _class_student_state_counts(cursor, class_id)
    operational_total = (
        counts["active_students"]
        + counts["coming_session_reserved"]
        + counts["partially_graduated"]
    )
    if not desired_active and operational_total:
        raise ValueError(
            f"{class_row['class_name']} cannot be made Inactive because it contains "
            f"{counts['active_students']} Active student(s), "
            f"{counts['coming_session_reserved']} pending coming-session "
            f"reservation(s), and {counts['partially_graduated']} Partially "
            "Graduated student(s). Move or complete these operational records first."
        )

    reserved_strength = (
        counts["active_students"] + counts["coming_session_reserved"]
    )
    if capacity < reserved_strength:
        raise ValueError(
            f"Class capacity cannot be reduced to {capacity} because the class "
            f"currently contains {counts['active_students']} Active student(s) and "
            f"{counts['coming_session_reserved']} pending coming-session "
            "reservation(s)."
        )

    # A legacy invalid section may be made/stay Inactive, but its capacity is
    # intentionally left untouched until the section itself is corrected.
    if not valid_section and not desired_active:
        capacity = int(class_row["student_capacity"] or DEFAULT_CLASS_CAPACITY)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        """
        UPDATE classes
        SET student_capacity = ?, is_active = ?, updated_at = ?
        WHERE id = ?
        """,
        (capacity, desired_active, now, class_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("The class settings could not be saved.")

    invariant = cursor.execute(
        """
        SELECT c.student_capacity, c.is_active,
               COUNT(CASE WHEN s.student_status = ? THEN 1 END) AS active_students,
               COUNT(CASE
                   WHEN s.student_status = ?
                    AND s.coming_session_status = 'PENDING'
                   THEN 1 END) AS coming_session_reserved,
               COUNT(CASE WHEN s.student_status = ? THEN 1 END) AS partially_graduated
        FROM classes AS c
        LEFT JOIN students AS s ON s.class_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
        """,
        (
            STATUS_ACTIVE,
            STATUS_COMING_SESSION,
            STATUS_PARTIALLY_GRADUATED,
            class_id,
        ),
    ).fetchone()
    if invariant is None:
        raise RuntimeError("Class settings invariant failed; no change was saved.")
    final_active = int(invariant["active_students"] or 0)
    final_reserved = int(invariant["coming_session_reserved"] or 0)
    final_partial = int(invariant["partially_graduated"] or 0)
    if final_active + final_reserved > int(invariant["student_capacity"]):
        raise RuntimeError("Class capacity invariant failed; no change was saved.")
    if int(invariant["is_active"] or 0) != desired_active:
        raise RuntimeError("Class status invariant failed; no change was saved.")
    if not desired_active and final_active + final_reserved + final_partial:
        raise RuntimeError(
            "Inactive-class operational-student invariant failed; no change was saved."
        )

    return {
        "class_id": class_id,
        "class_name": class_row["class_name"],
        "student_capacity": int(invariant["student_capacity"]),
        "is_active": bool(desired_active),
        "active_students": final_active,
        "coming_session_reserved": final_reserved,
        "partially_graduated": final_partial,
        "action": "activated" if desired_active else "inactivated",
    }


def update_class_settings(
    class_id: int,
    student_capacity,
    active: bool,
) -> dict:
    """Atomically save class capacity and Active/Inactive status."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN IMMEDIATE")
        class_row = cursor.execute(
            "SELECT * FROM classes WHERE id = ?",
            (int(class_id),),
        ).fetchone()
        if class_row is None:
            raise ValueError("Class not found.")
        result = _save_class_settings_with_cursor(
            cursor,
            class_row,
            student_capacity=student_capacity,
            active=active,
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_class_capacity(
    class_id: int,
    student_capacity,
) -> int:
    """Update capacity through the same atomic class-settings transaction."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN IMMEDIATE")
        class_row = cursor.execute(
            "SELECT * FROM classes WHERE id = ?",
            (int(class_id),),
        ).fetchone()
        if class_row is None:
            raise ValueError("Class not found.")
        result = _save_class_settings_with_cursor(
            cursor,
            class_row,
            student_capacity=student_capacity,
            active=bool(class_row["is_active"]),
        )
        connection.commit()
        return int(result["student_capacity"])
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def validate_class_level(class_level: str) -> bool:
    """Check that the class level is one of the approved values."""
    if not class_level:
        return False
    return class_level.strip().upper() in ALL_CLASS_LEVELS


def validate_section(section: str, class_level: str = None) -> bool:
    """Validate a section, including the Class 4-10 single-letter rule."""
    if not section:
        return False
    normalized_section = str(section).strip().upper()
    if not re.fullmatch(SECTION_NAME_PATTERN, normalized_section):
        return False

    if class_level is not None:
        normalized_level = str(class_level).strip().upper()
        if normalized_level.isdigit() and 4 <= int(normalized_level) <= 10:
            return len(normalized_section) == 1

    return True


def is_operational_class_row(class_row) -> bool:
    """Return True only for an Active class with a valid level/section."""
    return bool(class_row["is_active"]) and validate_class_level(
        class_row["class_level"]
    ) and validate_section(class_row["section"], class_row["class_level"])


def get_invalid_class_section_report():
    """Report legacy Class 4-10 multi-letter/invalid sections without editing them."""
    connection = get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM classes ORDER BY class_level, section, id"
        ).fetchall()
        return [
            {
                "class_id": int(row["id"]),
                "class_name": row["class_name"],
                "class_level": row["class_level"],
                "section": row["section"],
                "is_active": bool(row["is_active"]),
            }
            for row in rows
            if not validate_section(row["section"], row["class_level"])
        ]
    finally:
        connection.close()


def is_class_name_unique(class_name: str) -> bool:
    """Check that no other class already uses this generated class name."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM classes WHERE UPPER(TRIM(class_name)) = UPPER(TRIM(?))",
            (class_name,),
        )
        exists = cursor.fetchone() is not None
    finally:
        connection.close()
    return not exists


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def add_class(
    class_level: str,
    section: str,
    student_capacity=DEFAULT_CLASS_CAPACITY,
) -> int:
    """
    Create a new class record.

    The class name is generated automatically. New classes use
    capacity 40 unless another valid positive capacity is supplied.
    """
    level = str(class_level or "").strip().upper()
    sec = str(section or "").strip().upper()

    if not validate_class_level(level):
        raise ValueError(
            "Class Level is blank, malformed, or unsupported."
        )

    if not validate_section(sec, level):
        if level.isdigit() and 4 <= int(level) <= 10:
            raise ValueError(
                "Classes 4 through 10 must use exactly one section letter from A to Z."
            )
        raise ValueError(
            "Section must contain uppercase letters only."
        )

    capacity = validate_student_capacity(
        student_capacity
    )

    class_name = build_class_name(
        level,
        sec,
    )

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")
        duplicate = cursor.execute(
            """
            SELECT id FROM classes
            WHERE UPPER(TRIM(class_name)) = UPPER(TRIM(?))
               OR (UPPER(TRIM(class_level))=? AND UPPER(TRIM(section))=?)
            LIMIT 1
            """,
            (class_name, level, sec),
        ).fetchone()
        if duplicate is not None:
            raise ValueError("A class with the same level and section already exists.")

        cursor.execute(
            """
            INSERT INTO classes (
                class_level,
                section,
                class_name,
                student_capacity,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                level,
                sec,
                class_name,
                capacity,
                now,
                now,
            ),
        )

        new_class_id = int(
            cursor.lastrowid
        )

        connection.commit()

        return new_class_id

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()



# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_all_classes(include_inactive: bool = False):
    """
    Return classes in natural school order.

    Operational dropdowns receive Active classes only by default. Class
    Settings and historical screens may pass include_inactive=True.
    """
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM classes")
        rows = cursor.fetchall()
        if not include_inactive:
            rows = [row for row in rows if is_operational_class_row(row)]
    finally:
        connection.close()

    order_reference = ALL_CLASS_LEVELS

    def sort_key(row):
        level = str(row["class_level"] or "").strip().upper()
        level_index = order_reference.index(level) if level in order_reference else 999
        return (
            level_index,
            section_rank(row["section"]),
            str(row["section"] or "").strip().upper(),
        )

    return sorted(rows, key=sort_key)

def get_class_by_id(class_id: int):
    """Return a single class record, or None if not found."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM classes WHERE id = ?", (class_id,))
        row = cursor.fetchone()
    finally:
        connection.close()
    return row

def get_classes_by_level(
    class_level: str,
    include_inactive: bool = False,
):
    """Return sections for a class level, Active-only by default."""
    normalized_level = str(class_level).strip().upper()
    connection = get_connection()
    try:
        cursor = connection.cursor()
        query = "SELECT * FROM classes WHERE class_level = ?"
        parameters = [normalized_level]
        if not include_inactive:
            query += " AND is_active = 1"
        cursor.execute(query, parameters)
        rows = cursor.fetchall()
        if not include_inactive:
            rows = [row for row in rows if is_operational_class_row(row)]
        rows = sorted(
            rows,
            key=lambda row: (
                section_rank(row["section"]),
                str(row["section"] or "").strip().upper(),
            ),
        )
    finally:
        connection.close()
    return rows

def get_class_by_name(class_name: str):
    """Return a single class record by its exact generated name."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM classes WHERE class_name = ?", (class_name,))
        row = cursor.fetchone()
    finally:
        connection.close()
    return row


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def _class_dependency_counts(cursor, class_id: int) -> dict:
    """Return current and historical references that protect a class."""
    class_id = int(class_id)
    checks = {
        "students": ("students", "class_id"),
        "fee settings": ("fee_settings", "class_id"),
        "fee history": ("fee_history", "class_id"),
        "student fee records": ("student_fee_records", "class_id"),
        "class history (old)": ("student_class_history", "old_class_id"),
        "class history (new)": ("student_class_history", "new_class_id"),
        "promotion batches (source)": ("promotion_batches", "source_class_id"),
        "promotion batches (destination)": ("promotion_batches", "destination_class_id"),
        "promotion history (old)": ("promotion_history", "old_class_id"),
        "promotion history (new)": ("promotion_history", "new_class_id"),
        "graduation records": ("graduation_records", "last_class_id"),
        "receipt snapshots": ("fee_receipts", "class_id_snapshot"),
    }
    counts = {}
    for label, (table_name, column_name) in checks.items():
        columns = {
            str(row[1])
            for row in cursor.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()
        }
        if column_name not in columns:
            continue
        count = int(
            cursor.execute(
                f'SELECT COUNT(*) FROM "{table_name}" WHERE "{column_name}" = ?',
                (class_id,),
            ).fetchone()[0]
        )
        if count:
            counts[label] = count
    return counts


def set_class_active(class_id: int, active: bool) -> dict:
    """Activate/inactivate through the atomic class-settings transaction."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN IMMEDIATE")
        class_row = cursor.execute(
            "SELECT * FROM classes WHERE id = ?",
            (int(class_id),),
        ).fetchone()
        if class_row is None:
            raise ValueError("Class not found.")
        result = _save_class_settings_with_cursor(
            cursor,
            class_row,
            student_capacity=class_row["student_capacity"],
            active=active,
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def delete_class(class_id: int) -> dict:
    """
    Delete only a never-used class. A referenced class becomes Inactive.

    This keeps historical fees, receipts, class history, promotion history,
    and graduation records intact and avoids exposing foreign-key errors.
    """
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute("SELECT * FROM classes WHERE id = ?", (int(class_id),))
        class_row = cursor.fetchone()
        if class_row is None:
            raise ValueError("Class not found.")

        operational = _class_student_state_counts(cursor, int(class_id))
        operational_total = (
            operational["active_students"]
            + operational["coming_session_reserved"]
            + operational["partially_graduated"]
        )
        if operational_total:
            raise ValueError(
                f"{class_row['class_name']} cannot be deleted or made Inactive "
                f"because it contains {operational['active_students']} Active "
                f"student(s), {operational['coming_session_reserved']} pending "
                f"coming-session reservation(s), and "
                f"{operational['partially_graduated']} Partially Graduated "
                "student(s)."
            )

        dependencies = _class_dependency_counts(cursor, int(class_id))
        if dependencies:
            cursor.execute(
                "UPDATE classes SET is_active = 0, updated_at = ? WHERE id = ?",
                (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    int(class_id),
                ),
            )
            connection.commit()
            return {
                "action": "inactivated",
                "class_id": int(class_id),
                "class_name": class_row["class_name"],
                "dependencies": dependencies,
            }

        cursor.execute("DELETE FROM classes WHERE id = ?", (int(class_id),))
        if cursor.rowcount != 1:
            raise RuntimeError("The class could not be deleted.")
        connection.commit()
        return {
            "action": "deleted",
            "class_id": int(class_id),
            "class_name": class_row["class_name"],
            "dependencies": {},
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# PDF export (used by the Class Search Result screen)
# ---------------------------------------------------------------------------

def build_pdf_file_name(class_name: str, list_type_label: str) -> str:
    """
    Suggest a safe PDF file name from the class name and list type.
    Example: "3A" + "Active Students" -> "3A_Active_Students_2026-07-19.pdf"
    """
    today = datetime.now().strftime("%Y-%m-%d")
    safe_class_name = class_name.strip().replace(" ", "_")
    safe_list_type = list_type_label.strip().replace(" ", "_")
    return f"{safe_class_name}_{safe_list_type}_{today}.pdf"


def build_whole_school_print_html(
    class_groups,
) -> str:
    """
    Build a printable whole-school Active-student list.

    class_groups contains:
        (class_name, active_students)
    """
    from html import escape

    generated_on = datetime.now().strftime(
        "%d-%m-%Y %I:%M %p"
    )

    class_sections_html = ""

    for class_index, (
        class_name,
        students,
    ) in enumerate(class_groups):

        page_break_style = (
            ""
            if class_index == 0
            else "page-break-before: always;"
        )

        student_rows_html = ""

        for student_row in students:
            display_name = (
                student_service.display_name_with_stars(
                    student_row["student_name"],
                    student_row["failure_count"],
                )
            )

            student_rows_html += f"""
                <tr>
                    <td>
                        {escape(str(student_row["admission_number"]))}
                    </td>
                    <td>
                        {escape(student_service.display_roll_number(student_row))}
                    </td>
                    <td class="left">
                        {escape(str(display_name))}
                    </td>
                    <td class="left">
                        {escape(str(student_row["father_name"]))}
                    </td>
                </tr>
            """

        class_sections_html += f"""
            <div
                class="class-section"
                style="{page_break_style}"
            >
                <div class="school-name">
                    Hira Model School
                </div>

                <div class="class-name">
                    Class: {escape(str(class_name))}
                </div>

                <div class="list-type">
                    Active Students
                </div>

                <div class="generated">
                    Generated: {generated_on}
                </div>

                <table class="student-table">
                    <tr>
                        <th style="width:22%;">Admission Number</th>
                        <th style="width:16%;">Roll Number</th>
                        <th style="width:31%;">Student Name</th>
                        <th style="width:31%;">Father Name</th>
                    </tr>

                    {student_rows_html}
                </table>

                <div class="total">
                    Total Active Students: {len(students)}
                </div>
            </div>
        """

    return f"""
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, Helvetica, sans-serif;
                font-size: 6.5pt;
                color: #000000;
                margin: 0;
                padding: 0;
            }}

            .class-section {{
                page-break-inside: avoid;
                margin: 0;
                padding: 0;
            }}

            .school-name {{
                text-align: center;
                font-size: 12pt;
                font-weight: bold;
                line-height: 1;
                margin: 0 0 1px 0;
            }}

            .class-name {{
                text-align: center;
                font-size: 10pt;
                font-weight: bold;
                line-height: 1;
                margin: 0 0 1px 0;
            }}

            .list-type {{
                text-align: center;
                font-size: 8pt;
                font-weight: bold;
                line-height: 1;
                margin: 0 0 1px 0;
            }}

            .generated {{
                text-align: center;
                font-size: 6pt;
                line-height: 1;
                margin: 0 0 3px 0;
            }}

            .student-table {{
                width: 100%;
                border-collapse: collapse;
                border-spacing: 0;
                page-break-inside: avoid;
                font-size: 6.5pt;
                line-height: 1;
            }}

            th {{
                border: 1px solid #444444;
                padding: 1px 2px;
                background-color: #e7e9ee;
                text-align: center;
                font-weight: bold;
                font-size: 6.25pt;
                line-height: 1;
                white-space: nowrap;
            }}

            td {{
                border: 1px solid #555555;
                padding: 1px 2px;
                text-align: center;
                font-size: 6.5pt;
                line-height: 1;
                white-space: nowrap;
            }}

            td.left {{
                text-align: left;
            }}

            .total {{
                margin-top: 3px;
                font-size: 6.5pt;
                line-height: 1;
                font-weight: bold;
            }}
        </style>
    </head>

    <body>
        {class_sections_html}
    </body>
    </html>
    """


def build_student_list_print_html(
    class_name: str,
    students,
) -> str:
    """Build a printable Active-student list for one class."""
    from html import escape

    generated_on = datetime.now().strftime(
        "%d-%m-%Y %I:%M %p"
    )

    student_rows_html = ""

    for student_row in students:
        display_name = (
            student_service.display_name_with_stars(
                student_row["student_name"],
                student_row["failure_count"],
            )
        )

        student_rows_html += f"""
            <tr>
                <td>
                    {escape(str(student_row["admission_number"]))}
                </td>
                <td>
                    {escape(student_service.display_roll_number(student_row))}
                </td>
                <td class="left">
                    {escape(str(display_name))}
                </td>
                <td class="left">
                    {escape(str(student_row["father_name"]))}
                </td>
            </tr>
        """

    return f"""
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, Helvetica, sans-serif;
                font-size: 10pt;
                color: #000000;
            }}

            .school-name {{
                text-align: center;
                font-size: 18pt;
                font-weight: bold;
                margin-bottom: 4px;
            }}

            .class-name {{
                text-align: center;
                font-size: 15pt;
                font-weight: bold;
                margin-bottom: 3px;
            }}

            .list-type {{
                text-align: center;
                font-size: 11pt;
                font-weight: bold;
                margin-bottom: 3px;
            }}

            .generated {{
                text-align: center;
                font-size: 9pt;
                margin-bottom: 12px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
            }}

            th {{
                border: 1px solid #444444;
                padding: 6px;
                background-color: #e7e9ee;
                text-align: center;
                font-weight: bold;
            }}

            td {{
                border: 1px solid #555555;
                padding: 5px;
                text-align: center;
            }}

            td.left {{
                text-align: left;
            }}

            .total {{
                margin-top: 8px;
                font-weight: bold;
            }}
        </style>
    </head>

    <body>
        <div class="school-name">
            Hira Model School
        </div>

        <div class="class-name">
            Class: {escape(str(class_name))}
        </div>

        <div class="list-type">
            Active Students
        </div>

        <div class="generated">
            Generated: {generated_on}
        </div>

        <table>
            <tr>
                <th style="width:22%;">Admission Number</th>
                <th style="width:16%;">Roll Number</th>
                <th style="width:31%;">Student Name</th>
                <th style="width:31%;">Father Name</th>
            </tr>

            {student_rows_html}
        </table>

        <div class="total">
            Total Active Students: {len(students)}
        </div>
    </body>
    </html>
    """


def ensure_pdf_extension(save_path: str) -> str:
    """Return the actual PDF path, adding .pdf case-insensitively when needed."""
    normalized_path = str(save_path or "").strip()
    if not normalized_path:
        raise ValueError("A PDF save path is required.")
    if not normalized_path.lower().endswith(".pdf"):
        normalized_path += ".pdf"
    return normalized_path


def export_student_list_pdf(class_name: str, list_type_label: str, students, save_path: str) -> str:
    """
    Save the given student list as a PDF file at save_path.
    'students' rows must provide admission_number, roll_number,
    student_name, and father_name. Class Management only reads this
    data - it never creates, edits, or deletes student records.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    now = datetime.now()
    save_path = ensure_pdf_extension(save_path)

    document = SimpleDocTemplate(
        save_path,
        pagesize=A4,
        title=f"{class_name} - {list_type_label}",
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.8 * cm,
    )
    elements = []

    elements.append(Paragraph(f"Class: {class_name}", styles["Title"]))
    elements.append(Paragraph(f"List Type: {list_type_label}", styles["Normal"]))
    elements.append(Paragraph(f"Generated On: {now.strftime('%d %B %Y')}", styles["Normal"]))
    elements.append(Paragraph(f"Generated At: {now.strftime('%I:%M %p')}", styles["Normal"]))
    elements.append(Paragraph(f"Total Students: {len(students)}", styles["Normal"]))
    elements.append(Spacer(1, 0.6 * cm))

    name_style = ParagraphStyle(
        "ClassListName",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=9.5,
        wordWrap="CJK",
    )
    table_data = [["Admission Number", "Roll Number", "Student Name", "Father Name"]]
    for student_row in students:
        student_name = student_service.display_name_with_stars(
            student_row["student_name"], student_row["failure_count"]
        )
        table_data.append([
            student_row["admission_number"],
            student_service.display_roll_number(student_row),
            Paragraph(str(student_name), name_style),
            Paragraph(str(student_row["father_name"]), name_style),
        ])

    # 15.5 cm total stays inside the 17.0 cm A4 frame created by 2 cm margins.
    table = Table(
        table_data,
        colWidths=[3.0 * cm, 2.3 * cm, 5.0 * cm, 5.2 * cm],
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F6FED")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9CED6")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F6FA")]),
    ]))
    elements.append(table)

    document.build(elements)
    return save_path