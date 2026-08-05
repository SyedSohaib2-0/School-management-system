"""
Full-class promotion routing with destination-level sequencing,
capacity protection, gender lanes, and failure-student relocation.

This module is intentionally isolated from the legacy promotion service.
app.services.promotion_service delegates its public full-class functions
here. Legacy reversal-history columns remain only for database compatibility.
"""

from __future__ import annotations

from datetime import datetime

from app.config import (
    CLASS_LEVEL_SEQUENCE,
    FEE_TYPE_MONTHLY,
    FEE_STATUS_UNPAID,
    STATUS_ACTIVE,
    STATUS_DROPPED,
    STATUS_ARCHIVED,
    STATUS_GRADUATED,
    STATUS_PARTIALLY_GRADUATED,
    build_class_name,
    get_next_class_level,
    section_rank,
)
from app.database import get_connection
from app.services import class_service
from app.services import promotion_cycle_service


PROMOTION_TYPE_FULL_CLASS = "Full Class"
PROMOTION_TYPE_FAILURE_RELOCATION = "Failure Relocation"

MALE_CLASS_SECTIONS = tuple("ABCDEF")
FEMALE_CLASS_SECTIONS = tuple("GHIJKLMNOPQRSTUVWXYZ")
DEFAULT_STUDENT_CAPACITY = 40

PROMOTION_NEXT_SESSION_FEE_GUARD_V9 = True

PROMOTION_ALLOWED_MONTHS = (1, 2, 3)
PROMOTION_WINDOW_MESSAGE = (
    "Full-class promotion and promotion preview are available only in "
    "January, February, and March. Full-class promotion is allowed only "
    "during January, February, and March."
)


def validate_promotion_window(as_of=None, class_id=None) -> datetime:
    """Validate the normal window or one class's overdue recovery context.

    Callers that do not identify a class retain the strict January-March
    behavior. Full-class preview/execution resolve the class-bound academic
    context and may continue outside the normal window only for a genuine
    unresolved cycle.
    """
    reference = as_of or datetime.now()
    if int(reference.month) in PROMOTION_ALLOWED_MONTHS:
        return reference
    if class_id is None:
        raise ValueError(
            f"{PROMOTION_WINDOW_MESSAGE} Outside that window, select and load "
            "a class with an overdue promotion cycle. "
            f"Current date: {reference.strftime('%d-%m-%Y')}."
        )
    connection = get_connection()
    try:
        promotion_cycle_service.resolve_promotion_context(
            connection.cursor(), int(class_id), as_of=reference
        )
    finally:
        connection.close()
    return reference


def get_promotion_context(class_id: int, *, as_of: datetime | None = None) -> dict:
    connection = get_connection()
    try:
        context = promotion_cycle_service.resolve_promotion_context(
            connection.cursor(), int(class_id), as_of=as_of
        )
        return {
            "source_session_start_year": context.source_session_start_year,
            "target_session_start_year": context.target_session_start_year,
            "source_session_label": context.source_session_label,
            "target_session_label": context.target_session_label,
            "effective_date": context.effective_date.isoformat(),
            "promotion_mode": context.promotion_mode,
            "executed_at": context.executed_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        connection.close()


def _format_now(as_of=None) -> str:
    reference = as_of or datetime.now()
    return reference.strftime("%Y-%m-%d %H:%M:%S")


def _parse_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _numeric_level(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalise_gender(value) -> str:
    text = str(value or "").strip().upper()
    if text == "MALE":
        return "MALE"
    if text == "FEMALE":
        return "FEMALE"
    return ""


def _section_gender(section) -> str | None:
    value = str(section or "").strip().upper()
    if value in MALE_CLASS_SECTIONS:
        return "MALE"
    if value in FEMALE_CLASS_SECTIONS:
        return "FEMALE"
    return None


def _student_reference(student_row) -> str:
    return (
        f"{str(student_row['student_name'] or '').strip()} "
        f"({str(student_row['admission_number'] or '').strip()})"
    )


def _class_capacity(class_row) -> int:
    keys = set(class_row.keys())
    raw = (
        class_row["student_capacity"]
        if "student_capacity" in keys
        else DEFAULT_STUDENT_CAPACITY
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value < 1:
        raise ValueError(
            f"{class_row['class_name']} has an invalid student capacity. "
            "Set a capacity greater than zero in Class Management."
        )
    return value


def _load_class(cursor, class_id: int):
    cursor.execute("SELECT * FROM classes WHERE id = ?", (int(class_id),))
    return cursor.fetchone()


def _load_class_by_name(cursor, class_name: str):
    cursor.execute(
        "SELECT * FROM classes WHERE class_name = ? AND is_active = 1",
        (class_name,),
    )
    return cursor.fetchone()


def _load_active_students(cursor, class_id: int):
    cursor.execute(
        """
        SELECT *
        FROM students
        WHERE class_id = ?
          AND student_status = ?
        ORDER BY roll_number ASC, student_id ASC
        """,
        (int(class_id), STATUS_ACTIVE),
    )
    return cursor.fetchall()


def _load_promotion_cohort(cursor, class_id: int, context):
    """Load only Active students belonging to the unresolved source session.

    Students admitted or moved into the class on/after the target April
    boundary already belong to the new session. They must remain Active in the
    class and must not receive a P/F result for an older overdue cycle.
    """
    boundary = context.effective_date.isoformat()
    return cursor.execute(
        """
        SELECT s.*
        FROM students s
        WHERE s.class_id=? AND s.student_status=?
          AND date(COALESCE(
                (
                    SELECT printf('%04d-%02d-01',h.effective_year,h.effective_month)
                    FROM student_class_history h
                    WHERE h.student_id=s.student_id AND h.new_class_id=s.class_id
                    ORDER BY h.effective_year DESC,h.effective_month DESC,h.history_id DESC
                    LIMIT 1
                ),
                s.admission_date
          )) < date(?)
        ORDER BY s.roll_number,s.student_id
        """,
        (int(class_id), STATUS_ACTIVE, boundary),
    ).fetchall()


def get_promotion_cohort_students(
    class_id: int, *, as_of: datetime | None = None
) -> list[dict]:
    """Return the class rows eligible for the resolved normal/overdue cycle."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        context = promotion_cycle_service.resolve_promotion_context(
            cursor, int(class_id), as_of=as_of
        )
        return [
            dict(row) for row in _load_promotion_cohort(cursor, int(class_id), context)
        ]
    finally:
        connection.close()


def _load_active_students_for_classes(cursor, class_ids):
    class_ids = [int(value) for value in class_ids]
    if not class_ids:
        return {}
    placeholders = ", ".join("?" for _ in class_ids)
    cursor.execute(
        f"""
        SELECT *
        FROM students
        WHERE class_id IN ({placeholders})
          AND student_status = ?
        ORDER BY class_id ASC, roll_number ASC, student_id ASC
        """,
        (*class_ids, STATUS_ACTIVE),
    )
    result = {class_id: [] for class_id in class_ids}
    for row in cursor.fetchall():
        result[int(row["class_id"])].append(row)
    return result


def _next_roll_from_rows(rows, excluded_student_ids=None) -> int:
    excluded = {int(value) for value in (excluded_student_ids or set())}
    maximum = 0
    for row in rows:
        if int(row["student_id"]) in excluded:
            continue
        maximum = max(maximum, int(row["roll_number"] or 0))
    return maximum


def _load_lane_classes(cursor, class_level: str, sections):
    placeholders = ", ".join("?" for _ in sections)
    cursor.execute(
        f"""
        SELECT *
        FROM classes
        WHERE class_level = ?
          AND is_active = 1
          AND section IN ({placeholders})
        """,
        (str(class_level).strip().upper(), *sections),
    )
    return sorted(
        cursor.fetchall(),
        key=lambda row: (
            section_rank(row["section"]),
            str(row["section"] or "").strip().upper(),
        ),
    )


def _next_missing_section(existing_sections, permitted_sections):
    existing = {str(value or "").strip().upper() for value in existing_sections}
    for section in permitted_sections:
        if section not in existing:
            return section
    return None


def _validate_class_population(cursor, class_row) -> None:
    numeric = _numeric_level(class_row["class_level"])
    if numeric is None or numeric < 4:
        return

    required_gender = _section_gender(class_row["section"])
    if required_gender is None:
        raise ValueError(
            f"{class_row['class_name']} has an invalid Class 4+ section. "
            "Male sections must be A-F and Female sections must be G-Z."
        )

    incompatible = []
    for student_row in _load_active_students(cursor, int(class_row["id"])):
        recorded = _normalise_gender(student_row["gender"])
        if recorded and recorded != required_gender:
            incompatible.append(student_row)

    if incompatible:
        sample = ", ".join(_student_reference(row) for row in incompatible[:5])
        extra = len(incompatible) - 5
        if extra > 0:
            sample += f", and {extra} more"
        label = "Male" if required_gender == "MALE" else "Female"
        raise ValueError(
            f"{class_row['class_name']} contains {len(incompatible)} Active "
            f"student(s) incompatible with its {label} section: {sample}. "
            "Correct the records before promotion. No student was promoted."
        )


def get_classes_in_descending_promotion_order():
    """Return 10 -> 1 -> KG -> Nursery -> Play Group, section descending."""
    rows = list(class_service.get_all_classes())
    sequence_index = {
        str(level).strip().upper(): index
        for index, level in enumerate(CLASS_LEVEL_SEQUENCE)
    }

    def key(row):
        level = str(row["class_level"] or "").strip().upper()
        return (
            -sequence_index.get(level, -1),
            section_rank(row["section"]),
            str(row["section"] or "").strip().upper(),
            str(row["class_name"] or "").strip().upper(),
        )

    return sorted(rows, key=key)


def _validate_destination_level_ready(
    cursor,
    source_class_row,
    destination_level: str,
    source_session_start_year: int,
) -> None:
    """Require destination sections to be resolved for the same source session.

    Empty sections do not block. A section whose Active population entered on
    or after the target April boundary also does not belong to the ending
    session and therefore does not require a batch for that source session.
    """
    target_year = int(source_session_start_year) + 1
    boundary = f"{target_year:04d}-04-01"
    rows = cursor.execute(
        """
        SELECT c.*,COUNT(s.student_id) AS active_count
        FROM classes c
        LEFT JOIN students s ON s.class_id=c.id AND s.student_status=?
        WHERE c.class_level=? AND c.is_active=1
        GROUP BY c.id ORDER BY c.section
        """,
        (STATUS_ACTIVE, str(destination_level).strip().upper()),
    ).fetchall()
    blocked = []
    for row in rows:
        if int(row["active_count"] or 0) <= 0:
            continue
        completed = cursor.execute(
            """
            SELECT 1 FROM promotion_batches
            WHERE source_class_id=? AND source_session_start_year=?
              AND is_reversed=0 LIMIT 1
            """,
            (int(row["id"]), int(source_session_start_year)),
        ).fetchone()
        if completed is not None:
            continue

        unresolved = cursor.execute(
            """
            SELECT 1
            FROM students s
            WHERE s.class_id=? AND s.student_status=?
              AND NOT (
                    date(s.admission_date)>=date(?)
                    OR EXISTS (
                        SELECT 1 FROM student_class_history h
                        WHERE h.student_id=s.student_id AND h.new_class_id=s.class_id
                          AND date(printf('%04d-%02d-01',h.effective_year,h.effective_month))>=date(?)
                    )
              )
            LIMIT 1
            """,
            (int(row["id"]), STATUS_ACTIVE, boundary, boundary),
        ).fetchone()
        if unresolved is None:
            continue
        blocked.append(str(row["class_name"]))

    if blocked:
        raise ValueError(
            f"Before promoting {source_class_row['class_name']} for session "
            f"{source_session_start_year}-{source_session_start_year + 1}, "
            f"complete the same-session promotion for: {', '.join(blocked)}. "
            "No student was promoted."
        )


def _validate_results(active_students, promotion_values: dict):
    if not active_students:
        raise ValueError("No Active students were found in this class.")

    normalised = {}
    for raw_id, raw_value in promotion_values.items():
        try:
            student_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        normalised[student_id] = str(raw_value or "").strip().upper()

    passed = []
    failed = []
    for row in active_students:
        student_id = int(row["student_id"])
        result = normalised.get(student_id)
        if result not in {"P", "F"}:
            raise ValueError(
                f"Promotion result for {_student_reference(row)} must be P or F."
            )
        (passed if result == "P" else failed).append(row)
    return passed, failed



def _validate_destination_session_fees(
    cursor,
    destination_summaries,
    target_start_year: int,
) -> None:
    """
    Require the next academic session's April Monthly Fee for every
    destination section that will receive promoted or relocated students.

    Current-session student dues do not block Play Group through Class 9.
    """
    from app.services import fee_service

    target_start_year = int(target_start_year)

    used_destinations = {}

    for summary in destination_summaries:
        allocated = int(
            summary.get(
                "allocated",
                0,
            )
            or 0
        )
        relocated_in = int(
            summary.get(
                "relocated_in",
                0,
            )
            or 0
        )

        if allocated <= 0 and relocated_in <= 0:
            continue

        used_destinations[
            int(summary["class_id"])
        ] = str(
            summary["class_name"]
        )

    missing = []

    for (
        destination_class_id,
        destination_class_name,
    ) in used_destinations.items():
        try:
            fee_service.validate_destination_class_fee_for_promotion(
                class_id=destination_class_id,
                session_start_year=target_start_year,
                cursor=cursor,
            )

        except ValueError:
            missing.append(
                destination_class_name
            )

    if missing:
        session_label = (
            f"{target_start_year}-"
            f"{target_start_year + 1}"
        )

        raise ValueError(
            (
                "Set the destination Monthly Fee effective "
                f"from April {target_start_year} for session "
                f"{session_label} in: "
                f"{', '.join(missing)}. "
                "Current student dues do not block promotion, "
                "but the next-session destination fee is required. "
                "No student was promoted."
            )
        )


def _capacity_error(
    destination_level: str,
    required_gender: str | None,
    existing_rows,
    current_active_students: int,
    incoming_students: int,
    unallocated_students: int,
    permitted_sections=None,
) -> ValueError:
    total_capacity = sum(_class_capacity(row) for row in existing_rows)
    needed_capacity = int(current_active_students) + int(incoming_students)
    shortage = max(
        int(unallocated_students),
        needed_capacity - total_capacity,
        1,
    )

    if required_gender == "MALE":
        permitted = tuple(permitted_sections or MALE_CLASS_SECTIONS)
        lane = (
            f"Class {destination_level} Male lane "
            f"({destination_level}{permitted[0]}-"
            f"{destination_level}{permitted[-1]})"
        )
    elif required_gender == "FEMALE":
        permitted = tuple(permitted_sections or FEMALE_CLASS_SECTIONS)
        lane = (
            f"Class {destination_level} Female lane "
            f"({destination_level}{permitted[0]}-"
            f"{destination_level}{permitted[-1]})"
        )
    else:
        permitted = tuple(permitted_sections or ())
        lane = f"Class {destination_level}"

    existing_sections = [
        str(row["section"] or "").strip().upper()
        for row in existing_rows
    ]
    next_section = (
        _next_missing_section(existing_sections, permitted)
        if permitted
        else None
    )

    if next_section:
        instruction = (
            f"Create {build_class_name(destination_level, next_section)} in "
            "Class Management, or increase capacity in an existing compatible "
            f"section by at least {shortage} seat(s)"
        )
    else:
        instruction = (
            "Increase capacity in an existing compatible section by at least "
            f"{shortage} seat(s)"
        )

    return ValueError(
        f"Destination capacity is insufficient for {lane}. "
        f"Current total capacity: {total_capacity}; "
        f"current Active students: {int(current_active_students)}; "
        f"incoming promoted students: {int(incoming_students)}; "
        f"needed total capacity: {needed_capacity}; "
        f"additional usable capacity needed: {shortage}. "
        f"{instruction}, then run promotion again. "
        "No student was promoted and the complete operation was rolled back."
    )


def _pending_coming_session_reservations(
    cursor,
    class_id: int,
    target_session_start_year: int,
) -> int:
    """Return seats already reserved by pending coming-session admissions."""
    cursor.execute(
        """
        SELECT COUNT(*) AS reserved_students
        FROM students
        WHERE class_id = ?
          AND student_status = 'Coming Session'
          AND coming_session_status = 'PENDING'
          AND target_session_start_year = ?
        """,
        (int(class_id), int(target_session_start_year)),
    )
    row = cursor.fetchone()
    return int(row["reserved_students"] or 0) if row else 0



def _plan_lane_allocation(
    cursor,
    destination_level: str,
    primary_section: str,
    permitted_sections,
    incoming_students,
    required_gender: str,
    target_session_start_year: int,
):
    if not incoming_students:
        return [], [], [], []

    primary_section = str(primary_section).strip().upper()
    permitted_sections = tuple(permitted_sections)
    if primary_section not in permitted_sections:
        raise ValueError(
            f"Section {primary_section} is not permitted for this promotion lane."
        )

    start_index = permitted_sections.index(primary_section)
    lane_sections = permitted_sections[start_index:]
    destination_rows = _load_lane_classes(cursor, destination_level, lane_sections)
    rows_by_section = {
        str(row["section"] or "").strip().upper(): row
        for row in destination_rows
    }

    primary_row = rows_by_section.get(primary_section)
    if primary_row is None:
        required_name = build_class_name(destination_level, primary_section)
        raise ValueError(
            f"Destination class {required_name} does not exist. Create it first "
            "in Class Management. No student was promoted."
        )

    for row in destination_rows:
        _validate_class_population(cursor, row)

    class_ids = [int(row["id"]) for row in destination_rows]
    students_by_class = _load_active_students_for_classes(cursor, class_ids)
    pending_by_class = {
        class_id: _pending_coming_session_reservations(
            cursor, class_id, target_session_start_year
        )
        for class_id in class_ids
    }
    row_index_by_class = {
        int(row["id"]): index
        for index, row in enumerate(destination_rows)
    }

    fixed_by_class = {}
    failed_students = []
    current_active_students = 0
    for destination_row in destination_rows:
        destination_id = int(destination_row["id"])
        current_rows = list(students_by_class.get(destination_id, []))
        current_active_students += len(current_rows)
        fixed_rows = []
        for student_row in current_rows:
            if int(student_row["failure_count"] or 0) > 0:
                failed_students.append(
                    (
                        row_index_by_class[destination_id],
                        int(student_row["roll_number"] or 0),
                        int(student_row["student_id"]),
                        student_row,
                    )
                )
            else:
                fixed_rows.append(student_row)
        fixed_by_class[destination_id] = fixed_rows

    incoming_by_class = {class_id: [] for class_id in class_ids}
    remaining_incoming = list(incoming_students)
    for destination_row in destination_rows:
        destination_id = int(destination_row["id"])
        capacity_for_incoming = max(
            0,
            _class_capacity(destination_row)
            - len(fixed_by_class[destination_id])
            - pending_by_class[destination_id],
        )
        take = min(capacity_for_incoming, len(remaining_incoming))
        incoming_by_class[destination_id].extend(remaining_incoming[:take])
        remaining_incoming = remaining_incoming[take:]
        if not remaining_incoming:
            break

    if remaining_incoming:
        raise _capacity_error(
            destination_level,
            required_gender,
            destination_rows,
            current_active_students,
            len(incoming_students),
            len(remaining_incoming),
            permitted_sections=lane_sections,
        )

    failed_by_class = {class_id: [] for class_id in class_ids}
    remaining_slots = {
        int(row["id"]): (
            _class_capacity(row)
            - len(fixed_by_class[int(row["id"])])
            - pending_by_class[int(row["id"])]
            - len(incoming_by_class[int(row["id"])])
        )
        for row in destination_rows
    }

    for origin_index, old_roll, student_id, student_row in sorted(
        failed_students,
        key=lambda item: (item[0], item[1], item[2]),
    ):
        assigned = False
        for destination_row in destination_rows[origin_index:]:
            destination_id = int(destination_row["id"])
            if remaining_slots[destination_id] <= 0:
                continue
            failed_by_class[destination_id].append(
                (origin_index, old_roll, student_id, student_row)
            )
            remaining_slots[destination_id] -= 1
            assigned = True
            break
        if not assigned:
            raise _capacity_error(
                destination_level,
                required_gender,
                destination_rows,
                current_active_students,
                len(incoming_students),
                1,
                permitted_sections=lane_sections,
            )

    incoming_allocations = []
    destination_reassignments = []
    relocation_rows = []

    for destination_row in destination_rows:
        destination_id = int(destination_row["id"])
        destination_name = destination_row["class_name"]
        next_roll = 0

        for student_row in incoming_by_class[destination_id]:
            next_roll += 1
            incoming_allocations.append(
                {
                    "student_id": int(student_row["student_id"]),
                    "student_name": student_row["student_name"],
                    "admission_number": student_row["admission_number"],
                    "gender": required_gender,
                    "destination_class_id": destination_id,
                    "destination_class_name": destination_name,
                    "new_roll_number": next_roll,
                }
            )

        for student_row in sorted(
            fixed_by_class[destination_id],
            key=lambda row: (
                int(row["roll_number"] or 0),
                int(row["student_id"]),
            ),
        ):
            next_roll += 1
            destination_reassignments.append(
                {
                    "student_id": int(student_row["student_id"]),
                    "student_name": student_row["student_name"],
                    "admission_number": student_row["admission_number"],
                    "father_name": student_row["father_name"],
                    "old_class_id": int(student_row["class_id"]),
                    "old_class_name": destination_name,
                    "old_roll_number": int(student_row["roll_number"]),
                    "new_class_id": destination_id,
                    "new_class_name": destination_name,
                    "new_roll_number": next_roll,
                    "failure_count": int(student_row["failure_count"] or 0),
                }
            )

        for origin_index, old_roll, student_id, student_row in sorted(
            failed_by_class[destination_id],
            key=lambda item: (item[0], item[1], item[2]),
        ):
            del origin_index, old_roll, student_id
            next_roll += 1
            old_class_id = int(student_row["class_id"])
            old_class_row = next(
                row for row in destination_rows
                if int(row["id"]) == old_class_id
            )
            assignment = {
                "student_id": int(student_row["student_id"]),
                "student_name": student_row["student_name"],
                "admission_number": student_row["admission_number"],
                "father_name": student_row["father_name"],
                "old_class_id": old_class_id,
                "old_class_name": old_class_row["class_name"],
                "old_roll_number": int(student_row["roll_number"]),
                "new_class_id": destination_id,
                "new_class_name": destination_name,
                "new_roll_number": next_roll,
                "failure_count": int(student_row["failure_count"] or 0),
            }
            destination_reassignments.append(assignment)
            if old_class_id != destination_id:
                relocation_rows.append(assignment)

    allocations_by_class = {}
    relocations_in_by_class = {}
    relocations_out_by_class = {}
    for item in incoming_allocations:
        class_id = int(item["destination_class_id"])
        allocations_by_class[class_id] = allocations_by_class.get(class_id, 0) + 1
    for item in relocation_rows:
        old_id = int(item["old_class_id"])
        new_id = int(item["new_class_id"])
        relocations_out_by_class[old_id] = relocations_out_by_class.get(old_id, 0) + 1
        relocations_in_by_class[new_id] = relocations_in_by_class.get(new_id, 0) + 1

    final_strength_by_class = {
        class_id: pending_by_class[class_id]
        for class_id in class_ids
    }
    for item in incoming_allocations:
        final_strength_by_class[int(item["destination_class_id"])] += 1
    for item in destination_reassignments:
        final_strength_by_class[int(item["new_class_id"])] += 1

    summaries = []
    for row in destination_rows:
        class_id = int(row["id"])
        final_strength = final_strength_by_class[class_id]
        summaries.append(
            {
                "class_id": class_id,
                "class_name": row["class_name"],
                "section": row["section"],
                "gender": required_gender,
                "capacity": _class_capacity(row),
                "current_strength": len(students_by_class.get(class_id, [])),
                "coming_session_reserved": pending_by_class[class_id],
                "relocated_out": relocations_out_by_class.get(class_id, 0),
                "relocated_in": relocations_in_by_class.get(class_id, 0),
                "allocated": allocations_by_class.get(class_id, 0),
                "strength_after": final_strength,
                "available_after": _class_capacity(row) - final_strength,
            }
        )

    return (
        incoming_allocations,
        relocation_rows,
        destination_reassignments,
        summaries,
    )


def _plan_exact_destination(
    cursor,
    passed_students,
    destination_row,
    target_session_start_year: int,
):
    current = _load_active_students(cursor, int(destination_row["id"]))
    pending_reserved = _pending_coming_session_reservations(
        cursor, int(destination_row["id"]), target_session_start_year
    )
    capacity = _class_capacity(destination_row)
    needed_capacity = len(current) + pending_reserved + len(passed_students)
    if needed_capacity > capacity:
        raise ValueError(
            f"Destination capacity is insufficient for "
            f"{destination_row['class_name']}. Current capacity: {capacity}; "
            f"current Active students: {len(current)}; coming-session reserved "
            f"students: {pending_reserved}; incoming promoted "
            f"students: {len(passed_students)}; needed capacity: "
            f"{needed_capacity}; additional capacity needed: "
            f"{needed_capacity - capacity}. Increase capacity or create the "
            "next section, then run promotion again. No student was promoted "
            "and the complete operation was rolled back."
        )

    fixed_rows = [
        row for row in current
        if int(row["failure_count"] or 0) <= 0
    ]
    failed_rows = [
        row for row in current
        if int(row["failure_count"] or 0) > 0
    ]

    allocations = []
    destination_reassignments = []
    next_roll = 0
    for student_row in passed_students:
        next_roll += 1
        allocations.append(
            {
                "student_id": int(student_row["student_id"]),
                "student_name": student_row["student_name"],
                "admission_number": student_row["admission_number"],
                "gender": _normalise_gender(student_row["gender"]),
                "destination_class_id": int(destination_row["id"]),
                "destination_class_name": destination_row["class_name"],
                "new_roll_number": next_roll,
            }
        )

    for student_row in fixed_rows + failed_rows:
        next_roll += 1
        destination_reassignments.append(
            {
                "student_id": int(student_row["student_id"]),
                "student_name": student_row["student_name"],
                "admission_number": student_row["admission_number"],
                "father_name": student_row["father_name"],
                "old_class_id": int(destination_row["id"]),
                "old_class_name": destination_row["class_name"],
                "old_roll_number": int(student_row["roll_number"]),
                "new_class_id": int(destination_row["id"]),
                "new_class_name": destination_row["class_name"],
                "new_roll_number": next_roll,
                "failure_count": int(student_row["failure_count"] or 0),
            }
        )

    summary = {
        "class_id": int(destination_row["id"]),
        "class_name": destination_row["class_name"],
        "section": destination_row["section"],
        "gender": None,
        "capacity": capacity,
        "current_strength": len(current),
        "coming_session_reserved": pending_reserved,
        "relocated_out": 0,
        "relocated_in": 0,
        "allocated": len(allocations),
        "strength_after": needed_capacity,
        "available_after": capacity - needed_capacity,
    }
    return allocations, [], destination_reassignments, [summary]


def _class10_completion_statuses(
    cursor,
    connection,
    class_id: int,
    promotion_values: dict,
    context,
):
    """Resolve Class-10 completion using dues only through source-session March."""
    normalised = {}
    for raw_id, raw_value in promotion_values.items():
        try:
            student_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        normalised[student_id] = str(raw_value or "").strip().upper()

    class_row = _load_class(cursor, int(class_id))
    if not class_row or _numeric_level(class_row["class_level"]) != 10:
        return {}

    passed_ids = [
        int(row["student_id"])
        for row in _load_promotion_cohort(cursor, int(class_id), context)
        if normalised.get(int(row["student_id"])) == "P"
    ]
    from app.services import fee_service

    horizon = datetime(
        int(context.target_session_start_year), 3, 31, 23, 59, 59
    )
    statuses = {}
    for student_id in passed_ids:
        fee_service.ensure_fee_records_generated(
            student_id, connection=connection, as_of=horizon
        )
        cursor.execute(
            """
            SELECT 1
            FROM student_fee_records
            WHERE student_id=? AND fee_type=? AND status=?
              AND (year<? OR (year=? AND month_number<=3))
            LIMIT 1
            """,
            (
                int(student_id), FEE_TYPE_MONTHLY, FEE_STATUS_UNPAID,
                int(context.target_session_start_year),
                int(context.target_session_start_year),
            ),
        )
        statuses[student_id] = (
            STATUS_PARTIALLY_GRADUATED
            if cursor.fetchone() is not None else STATUS_GRADUATED
        )
    return statuses


def _remove_post_completion_unpaid_class10_fees(
    cursor,
    student_id: int,
    context,
) -> int:
    """Remove only receipt-free unpaid/waived rows after the completion horizon."""
    rows = cursor.execute(
        """
        SELECT r.id
        FROM student_fee_records r
        WHERE r.student_id=? AND r.fee_type=?
          AND (r.year>? OR (r.year=? AND r.month_number>=4))
          AND r.status IN ('Unpaid','Waived')
          AND NOT EXISTS (
              SELECT 1 FROM fee_receipts fr
              JOIN fee_payments fp ON fp.id=fr.payment_id
              WHERE fr.student_fee_record_id=r.id
                AND fr.status='Paid' AND fp.payment_status='Completed'
          )
        """,
        (
            int(student_id), FEE_TYPE_MONTHLY,
            int(context.target_session_start_year),
            int(context.target_session_start_year),
        ),
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    if ids:
        cursor.execute(
            f"DELETE FROM student_fee_records WHERE id IN ({','.join('?' for _ in ids)})",
            ids,
        )
    return len(ids)


def _build_plan(
    cursor,
    class_id: int,
    promotion_values: dict,
    class10_statuses=None,
    context=None,
):
    source = _load_class(cursor, int(class_id))
    if not source:
        raise ValueError("Class not found.")
    if int(source["is_active"] or 0) != 1:
        raise ValueError(
            f"{source['class_name']} is Inactive and cannot be used for "
            "Full-Class Promotion. Reactivate it in Class Settings first."
        )

    if context is None:
        context = promotion_cycle_service.resolve_promotion_context(
            cursor, int(class_id)
        )
    target_session_start_year = int(context.target_session_start_year)
    active_students = _load_promotion_cohort(cursor, int(class_id), context)
    if not active_students:
        raise ValueError(
            f"{source['class_name']} has no Active students belonging to source session "
            f"{context.source_session_label}."
        )
    passed_students, failed_students = _validate_results(active_students, promotion_values)
    next_level = get_next_class_level(source["class_level"])
    source_numeric = _numeric_level(source["class_level"])

    plan = {
        "source_class_id": int(source["id"]),
        "source_class_name": source["class_name"],
        "source_class_level": source["class_level"],
        "source_section": source["section"],
        "total_students": len(active_students),
        "passed_students": len(passed_students),
        "failed_students": len(failed_students),
        "graduated_students": 0,
        "fully_graduated_students": 0,
        "partially_graduated_students": 0,
        "passed_allocations": [],
        "failure_relocations": [],
        "destination_reassignments": [],
        "failed_student_ids": [int(row["student_id"]) for row in failed_students],
        "graduated_student_ids": [],
        "partially_graduated_student_ids": [],
        "class10_completion_status_by_student_id": {},
        "destination_summaries": [],
        "routing_type": "STANDARD",
        "source_session_start_year": int(context.source_session_start_year),
        "target_session_start_year": int(context.target_session_start_year),
        "source_session_label": context.source_session_label,
        "target_session_label": context.target_session_label,
        "effective_date": context.effective_date.isoformat(),
        "promotion_mode": context.promotion_mode,
        "executed_at": context.executed_at.strftime("%Y-%m-%d %H:%M:%S"),
    }

    if next_level is None:
        statuses = dict(class10_statuses or {})
        for row in passed_students:
            student_id = int(row["student_id"])
            if student_id not in statuses:
                cursor.execute(
                    """
                    SELECT 1 FROM student_fee_records
                    WHERE student_id = ? AND fee_type = ? AND status = ?
                    LIMIT 1
                    """,
                    (student_id, FEE_TYPE_MONTHLY, FEE_STATUS_UNPAID),
                )
                statuses[student_id] = (
                    STATUS_PARTIALLY_GRADUATED
                    if cursor.fetchone() is not None
                    else STATUS_GRADUATED
                )
        plan["graduated_students"] = len(passed_students)
        plan["fully_graduated_students"] = sum(
            1 for value in statuses.values() if value == STATUS_GRADUATED
        )
        plan["partially_graduated_students"] = sum(
            1 for value in statuses.values() if value == STATUS_PARTIALLY_GRADUATED
        )
        plan["graduated_student_ids"] = [
            student_id for student_id, value in statuses.items()
            if value == STATUS_GRADUATED
        ]
        plan["partially_graduated_student_ids"] = [
            student_id for student_id, value in statuses.items()
            if value == STATUS_PARTIALLY_GRADUATED
        ]
        plan["class10_completion_status_by_student_id"] = statuses
        plan["routing_type"] = "GRADUATION"
        return plan

    if not passed_students:
        plan["routing_type"] = "NO_PASSED_STUDENTS"
        return plan

    _validate_destination_level_ready(
        cursor, source, next_level, context.source_session_start_year
    )

    if source_numeric == 3 and str(next_level).strip() == "4":
        males, females, invalid = [], [], []
        for row in passed_students:
            gender = _normalise_gender(row["gender"])
            if gender == "MALE":
                males.append(row)
            elif gender == "FEMALE":
                females.append(row)
            else:
                invalid.append(row)
        if invalid:
            sample = ", ".join(_student_reference(row) for row in invalid[:5])
            raise ValueError(
                f"{len(invalid)} passed student(s) do not have valid Male/Female "
                f"Gender for Class 4 allocation: {sample}. No student was promoted."
            )

        male_alloc, male_reloc, male_reassign, male_summary = _plan_lane_allocation(
            cursor, "4", "A", MALE_CLASS_SECTIONS, males, "MALE",
            target_session_start_year,
        )
        female_alloc, female_reloc, female_reassign, female_summary = _plan_lane_allocation(
            cursor, "4", "G", FEMALE_CLASS_SECTIONS, females, "FEMALE",
            target_session_start_year,
        )
        plan["passed_allocations"] = male_alloc + female_alloc
        plan["failure_relocations"] = male_reloc + female_reloc
        plan["destination_reassignments"] = male_reassign + female_reassign
        plan["destination_summaries"] = male_summary + female_summary
        plan["routing_type"] = "CLASS_3_TO_4_GENDER_SPLIT_FAILURE_PRIORITY"
        _validate_destination_session_fees(
            cursor,
            plan["destination_summaries"],
            context.target_session_start_year,
        )
        return plan

    if source_numeric is not None and source_numeric >= 4:
        _validate_class_population(cursor, source)
        required_gender = _section_gender(source["section"])
        if required_gender is None:
            raise ValueError(
                f"{source['class_name']} has an invalid Class 4+ section."
            )
        permitted = (
            MALE_CLASS_SECTIONS if required_gender == "MALE"
            else FEMALE_CLASS_SECTIONS
        )
        allocations, relocations, reassignments, summaries = _plan_lane_allocation(
            cursor,
            str(next_level),
            source["section"],
            permitted,
            passed_students,
            required_gender,
            target_session_start_year,
        )
        plan["passed_allocations"] = allocations
        plan["failure_relocations"] = relocations
        plan["destination_reassignments"] = reassignments
        plan["destination_summaries"] = summaries
        plan["routing_type"] = "CLASS_4_PLUS_FAILURE_PRIORITY_OVERFLOW"
        _validate_destination_session_fees(
            cursor,
            plan["destination_summaries"],
            context.target_session_start_year,
        )
        return plan

    destination_name = build_class_name(next_level, source["section"])
    destination = _load_class_by_name(cursor, destination_name)
    if destination is None:
        raise ValueError(
            f"Destination class {destination_name} does not exist. Create it first "
            "in Class Management. No student was promoted."
        )
    allocations, relocations, reassignments, summaries = _plan_exact_destination(
        cursor, passed_students, destination, target_session_start_year
    )
    plan["passed_allocations"] = allocations
    plan["failure_relocations"] = relocations
    plan["destination_reassignments"] = reassignments
    plan["destination_summaries"] = summaries
    _validate_destination_session_fees(
        cursor,
        plan["destination_summaries"],
        context.target_session_start_year,
    )
    return plan


def _source_fee_continuity_as_of(
    cursor,
    class_id: int,
    student_id: int,
    promotion_values: dict,
    execution_moment: datetime,
    context,
) -> datetime:
    """Return the last fee month that must exist before promotion.

    A passing Class-10 student academically completes the source session at
    the final March boundary, even when the overdue action is executed later.
    Generating April-onward Class-10 fees for that student would create dues
    that the completion workflow must immediately remove.  Failed Class-10
    students and every lower class continue through the actual execution month.
    """
    class_row = _load_class(cursor, int(class_id))
    if class_row and _numeric_level(class_row["class_level"]) == 10:
        result = str(promotion_values.get(int(student_id), "") or "").strip().upper()
        if result == "P":
            return datetime(
                int(context.target_session_start_year), 3, 31, 23, 59, 59
            )
    return execution_moment


def _ensure_source_fee_continuity(
    cursor,
    connection,
    class_id: int,
    promotion_values: dict,
    execution_moment: datetime,
    context,
) -> None:
    """Generate every payable source-class month through its true horizon."""
    from app.services import fee_service

    active_students = _load_promotion_cohort(cursor, int(class_id), context)
    for student_row in active_students:
        student_id = int(student_row["student_id"])
        fee_service.ensure_fee_records_generated(
            student_id,
            connection=connection,
            as_of=_source_fee_continuity_as_of(
                cursor, class_id, student_id, promotion_values, execution_moment, context
            ),
        )


def _preview_source_fee_continuity(
    cursor,
    connection,
    class_id: int,
    promotion_values: dict,
    execution_moment: datetime,
    context,
) -> dict:
    """Validate and simulate source-class fees without database writes."""
    from app.services import fee_service

    details_by_student = {}
    total_missing = 0
    for student_row in _load_promotion_cohort(cursor, int(class_id), context):
        student_id = int(student_row["student_id"])
        details = fee_service.preview_fee_records_generation(
            student_id,
            connection=connection,
            as_of=_source_fee_continuity_as_of(
                cursor, class_id, student_id, promotion_values, execution_moment, context
            ),
        )
        details_by_student[student_id] = details
        total_missing += int(details["missing_count"])
    return {
        "student_details": details_by_student,
        "missing_count": total_missing,
    }


def preview_full_class_promotion_plan(
    class_id: int,
    promotion_values: dict,
    *,
    as_of: datetime | None = None,
):
    """Return a query-only normal/overdue promotion plan."""
    execution_moment = as_of or datetime.now()
    connection = get_connection()
    cursor = connection.cursor()
    try:
        connection.execute("PRAGMA query_only = ON")
        context = promotion_cycle_service.resolve_promotion_context(
            cursor, int(class_id), as_of=execution_moment
        )
        fee_preview = _preview_source_fee_continuity(
            cursor, connection, class_id, promotion_values, execution_moment, context
        )

        normalised = {}
        for raw_id, raw_value in promotion_values.items():
            try:
                student_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            normalised[student_id] = str(raw_value or "").strip().upper()

        statuses = {}
        class10_preview_missing = 0
        source = _load_class(cursor, int(class_id))
        if source and _numeric_level(source["class_level"]) == 10:
            horizon = datetime(context.target_session_start_year, 3, 31, 23, 59, 59)
            from app.services import fee_service
            for student_row in _load_promotion_cohort(cursor, int(class_id), context):
                student_id = int(student_row["student_id"])
                if normalised.get(student_id) != "P":
                    continue
                details = fee_service.preview_fee_records_generation(
                    student_id, connection=connection, as_of=horizon
                )
                class10_preview_missing += int(details.get("missing_count", 0))
                statuses[student_id] = (
                    STATUS_PARTIALLY_GRADUATED
                    if details.get("would_have_unpaid_monthly_fee", False)
                    else STATUS_GRADUATED
                )

        plan = _build_plan(
            cursor, class_id, promotion_values, statuses, context=context
        )
        plan["preview_read_only"] = True
        plan["source_fee_rows_would_be_generated"] = int(
            fee_preview["missing_count"]
        )
        plan["class10_fee_rows_would_be_generated"] = class10_preview_missing
        return plan
    finally:
        connection.close()


def _insert_history(
    cursor,
    *,
    batch_id,
    student_row,
    old_class_id,
    old_class_name,
    new_class_id,
    new_class_name,
    old_roll,
    new_roll,
    result,
    previous_status,
    new_status,
    failure_before,
    failure_after,
    now,
    effective_date,
    promotion_type,
    changed_by,
):
    cursor.execute(
        """
        INSERT INTO promotion_history (
            batch_id, student_id, admission_number, student_name_snapshot,
            father_name_snapshot, old_class_id, old_class_name, new_class_id,
            new_class_name, old_roll_number, new_roll_number, promotion_result,
            previous_status, new_status, failure_count_before, failure_count_after,
            promotion_datetime, promotion_type, is_reversed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            batch_id,
            int(student_row["student_id"]),
            student_row["admission_number"],
            student_row["student_name"],
            student_row["father_name"],
            old_class_id,
            old_class_name,
            new_class_id,
            new_class_name,
            old_roll,
            new_roll,
            result,
            previous_status,
            new_status,
            failure_before,
            failure_after,
            now,
            promotion_type,
        ),
    )

    should_record_class_timeline = (
        promotion_type == PROMOTION_TYPE_FULL_CLASS
        or (
            promotion_type == PROMOTION_TYPE_FAILURE_RELOCATION
            and old_class_id is not None
            and new_class_id is not None
            and int(old_class_id) != int(new_class_id)
        )
    )

    if should_record_class_timeline:
        effective_at = datetime.strptime(
            str(effective_date), "%Y-%m-%d"
        )
        timeline_new_class_id = (
            int(new_class_id)
            if new_class_id is not None
            else int(old_class_id)
        )
        cursor.execute(
            """
            INSERT INTO student_class_history (
                student_id, old_class_id, new_class_id,
                effective_month, effective_year, change_type,
                change_datetime, changed_by
            ) VALUES (?, ?, ?, ?, ?, 'PROMOTION', ?, ?)
            """,
            (
                int(student_row["student_id"]),
                int(old_class_id) if old_class_id is not None else None,
                timeline_new_class_id,
                int(effective_at.month),
                int(effective_at.year),
                now,
                str(changed_by or "admin"),
            ),
        )


def promote_full_class(
    class_id: int,
    promotion_values: dict,
    created_by: str = "admin",
    *,
    as_of: datetime | None = None,
):
    execution_moment = as_of or datetime.now()
    now = _format_now(execution_moment)
    connection = get_connection()
    cursor = connection.cursor()

    try:
        connection.execute("BEGIN IMMEDIATE")
        context = promotion_cycle_service.resolve_promotion_context(
            cursor, int(class_id), as_of=execution_moment
        )
        cycle_id = promotion_cycle_service.ensure_cycle_row(
            cursor, int(class_id), context, actor=created_by
        )
        _ensure_source_fee_continuity(
            cursor, connection, class_id, promotion_values, execution_moment, context
        )
        class10_statuses = _class10_completion_statuses(
            cursor, connection, class_id, promotion_values, context
        )
        plan = _build_plan(
            cursor, class_id, promotion_values, class10_statuses, context=context
        )
        source = _load_class(cursor, int(class_id))
        active_students = _load_promotion_cohort(cursor, int(class_id), context)
        source_by_id = {int(row["student_id"]): row for row in active_students}

        used_destinations = [
            row for row in plan["destination_summaries"]
            if int(row.get("allocated", 0) or 0) > 0
            or int(row.get("relocated_in", 0) or 0) > 0
        ]
        if plan["routing_type"] == "GRADUATION":
            statuses = set(plan["class10_completion_status_by_student_id"].values())
            if statuses == {STATUS_GRADUATED}:
                destination_id, destination_name = None, STATUS_GRADUATED
            elif statuses == {STATUS_PARTIALLY_GRADUATED}:
                destination_id, destination_name = None, STATUS_PARTIALLY_GRADUATED
            else:
                destination_id, destination_name = None, "Graduated / Partially Graduated"
        elif len(used_destinations) == 1:
            destination_id = int(used_destinations[0]["class_id"])
            destination_name = used_destinations[0]["class_name"]
        elif used_destinations:
            destination_id = None
            destination_name = " / ".join(
                dict.fromkeys(str(row["class_name"]) for row in used_destinations)
            )
        else:
            destination_id, destination_name = None, "No Passed Students"

        if int(plan["passed_students"]) + int(plan["failed_students"]) != int(plan["total_students"]):
            raise RuntimeError("Promotion totals are internally inconsistent.")
        if min(
            int(plan["total_students"]), int(plan["passed_students"]),
            int(plan["failed_students"]), int(plan["graduated_students"]),
        ) < 0 or int(plan["graduated_students"]) > int(plan["passed_students"]):
            raise RuntimeError("Promotion counters contain impossible values.")

        cursor.execute(
            """
            INSERT INTO promotion_batches (
                source_class_id, source_class_name, destination_class_id,
                destination_class_name, promotion_datetime,
                source_session_start_year,target_session_start_year,
                effective_date,promotion_mode,total_students,
                passed_students, failed_students, graduated_students,
                created_by, is_reversed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                int(source["id"]), source["class_name"], destination_id,
                destination_name, now,
                int(context.source_session_start_year),
                int(context.target_session_start_year),
                context.effective_date.isoformat(), context.promotion_mode,
                int(plan["total_students"]), int(plan["passed_students"]),
                int(plan["failed_students"]), int(plan["graduated_students"]),
                created_by,
            ),
        )
        batch_id = int(cursor.lastrowid)

        from app.services import fee_service

        # Temporarily release every Active destination roll that will be
        # reordered. This prevents transient UNIQUE(class_id, roll_number)
        # collisions while incoming students receive the first rolls and
        # failed students are placed at the end or moved to a later section.
        destination_rows_by_student_id = {
            int(item["student_id"]): item
            for item in plan["destination_reassignments"]
        }
        for student_id in destination_rows_by_student_id:
            cursor.execute(
                """
                UPDATE students
                SET roll_number = ?, updated_at = ?, updated_by = ?
                WHERE student_id = ?
                  AND student_status = ?
                """,
                (
                    -(1_000_000_000 + int(student_id)),
                    now,
                    created_by,
                    int(student_id),
                    STATUS_ACTIVE,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "A planned destination-student roll reassignment became invalid."
                )

        relocation_ids = {
            int(item["student_id"])
            for item in plan["failure_relocations"]
        }
        for assignment in plan["destination_reassignments"]:
            student_id = int(assignment["student_id"])
            cursor.execute(
                "SELECT * FROM students WHERE student_id = ?",
                (student_id,),
            )
            staged_student_row = cursor.fetchone()
            if (
                staged_student_row is None
                or staged_student_row["student_status"] != STATUS_ACTIVE
            ):
                raise RuntimeError(
                    "A planned destination-student reassignment became invalid."
                )

            cursor.execute(
                """
                UPDATE students
                SET class_id = ?, roll_number = ?, released_roll_number = NULL,
                    updated_at = ?, updated_by = ?
                WHERE student_id = ?
                """,
                (
                    int(assignment["new_class_id"]),
                    int(assignment["new_roll_number"]),
                    now,
                    created_by,
                    student_id,
                ),
            )
            if int(assignment["old_class_id"]) != int(assignment["new_class_id"]):
                fee_service.recalculate_fees_after_class_change(
                    student_id=student_id,
                    old_class_id=int(assignment["old_class_id"]),
                    new_class_id=int(assignment["new_class_id"]),
                    effective_month=context.effective_month,
                    effective_year=context.effective_year,
                    connection=connection,
                )

            if student_id in relocation_ids:
                _insert_history(
                    cursor,
                    batch_id=batch_id,
                    student_row=staged_student_row,
                    old_class_id=int(assignment["old_class_id"]),
                    old_class_name=assignment["old_class_name"],
                    new_class_id=int(assignment["new_class_id"]),
                    new_class_name=assignment["new_class_name"],
                    old_roll=int(assignment["old_roll_number"]),
                    new_roll=int(assignment["new_roll_number"]),
                    result="R",
                    previous_status=STATUS_ACTIVE,
                    new_status=STATUS_ACTIVE,
                    failure_before=int(assignment["failure_count"]),
                    failure_after=int(assignment["failure_count"]),
                    now=now,
                    effective_date=context.effective_date.isoformat(),
                    promotion_type=PROMOTION_TYPE_FAILURE_RELOCATION,
                    changed_by=created_by,
                )

        allocation_by_id = {
            int(item["student_id"]): item for item in plan["passed_allocations"]
        }
        failed_ids = {int(value) for value in plan["failed_student_ids"]}
        completed_ids = (
            {int(value) for value in plan["graduated_student_ids"]}
            | {int(value) for value in plan["partially_graduated_student_ids"]}
        )
        failed_data = []

        for student_row in active_students:
            student_id = int(student_row["student_id"])
            old_roll = int(student_row["roll_number"])
            failure_before = int(student_row["failure_count"] or 0)

            if student_id in failed_ids:
                failure_after = failure_before + 1
                cursor.execute(
                    """
                    UPDATE students
                    SET failure_count = ?, updated_at = ?, updated_by = ?
                    WHERE student_id = ?
                    """,
                    (failure_after, now, created_by, student_id),
                )
                failed_data.append((student_row, old_roll, failure_before, failure_after))
                continue

            if student_id in completed_ids:
                completion_status = plan["class10_completion_status_by_student_id"][student_id]
                cursor.execute(
                    """
                    UPDATE students
                    SET student_status = ?, failure_count = 0,
                        released_roll_number = ?,
                        roll_number = ?,
                        updated_at = ?, updated_by = ?
                    WHERE student_id = ?
                    """,
                    (
                        completion_status,
                        old_roll,
                        -abs(student_id),
                        now,
                        created_by,
                        student_id,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO graduation_records (
                        student_id, admission_number, last_class_id,
                        last_class_name, last_roll_number, graduation_date,
                        completion_session_start_year,graduation_effective_date,
                        status, is_reversed
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        student_id, student_row["admission_number"], int(source["id"]),
                        source["class_name"], old_roll, now,
                        int(context.source_session_start_year),
                        context.effective_date.isoformat(), completion_status,
                    ),
                )
                graduation_record_id = int(cursor.lastrowid)
                if graduation_record_id <= 0:
                    raise RuntimeError("Graduation record creation failed.")
                _remove_post_completion_unpaid_class10_fees(
                    cursor, student_id, context
                )
                _insert_history(
                    cursor,
                    batch_id=batch_id,
                    student_row=student_row,
                    old_class_id=int(source["id"]),
                    old_class_name=source["class_name"],
                    new_class_id=None,
                    new_class_name=None,
                    old_roll=old_roll,
                    new_roll=None,
                    result="P",
                    previous_status=STATUS_ACTIVE,
                    new_status=completion_status,
                    failure_before=failure_before,
                    failure_after=0,
                    now=now,
                    effective_date=context.effective_date.isoformat(),
                    promotion_type=PROMOTION_TYPE_FULL_CLASS,
                    changed_by=created_by,
                )
                continue

            allocation = allocation_by_id.get(student_id)
            if allocation is None:
                raise RuntimeError(
                    f"Validated promotion plan has no destination for {student_row['student_name']}."
                )
            cursor.execute(
                """
                UPDATE students
                SET class_id = ?, roll_number = ?, released_roll_number = NULL,
                    failure_count = 0, updated_at = ?, updated_by = ?
                WHERE student_id = ?
                """,
                (
                    int(allocation["destination_class_id"]),
                    int(allocation["new_roll_number"]),
                    now,
                    created_by,
                    student_id,
                ),
            )
            fee_service.recalculate_fees_after_class_change(
                student_id=student_id,
                old_class_id=int(source["id"]),
                new_class_id=int(allocation["destination_class_id"]),
                effective_month=context.effective_month,
                effective_year=context.effective_year,
                connection=connection,
            )
            _insert_history(
                cursor,
                batch_id=batch_id,
                student_row=student_row,
                old_class_id=int(source["id"]),
                old_class_name=source["class_name"],
                new_class_id=int(allocation["destination_class_id"]),
                new_class_name=allocation["destination_class_name"],
                old_roll=old_roll,
                new_roll=int(allocation["new_roll_number"]),
                result="P",
                previous_status=STATUS_ACTIVE,
                new_status=STATUS_ACTIVE,
                failure_before=failure_before,
                failure_after=0,
                now=now,
                effective_date=context.effective_date.isoformat(),
                promotion_type=PROMOTION_TYPE_FULL_CLASS,
                changed_by=created_by,
            )

        # Dropped/Archived rolls remain reserved until this exact class is
        # promoted. Release them in the same promotion transaction regardless
        # of fee status, preserve lifecycle status, and record the event.
        cursor.execute(
            """
            SELECT *
            FROM students
            WHERE class_id = ?
              AND student_status IN (?, ?)
              AND released_roll_number IS NULL
              AND roll_number > 0
            ORDER BY student_id ASC
            """,
            (int(source["id"]), STATUS_DROPPED, STATUS_ARCHIVED),
        )
        inactive_roll_rows = cursor.fetchall()
        for inactive_row in inactive_roll_rows:
            inactive_student_id = int(inactive_row["student_id"])
            retained_roll = int(inactive_row["roll_number"])
            released_sentinel = -(2_000_000_000 + inactive_student_id)
            cursor.execute(
                """
                UPDATE students
                SET released_roll_number = ?,
                    roll_number = ?,
                    updated_at = ?,
                    updated_by = ?
                WHERE student_id = ?
                  AND class_id = ?
                  AND student_status IN (?, ?)
                  AND released_roll_number IS NULL
                  AND roll_number = ?
                """,
                (
                    retained_roll,
                    released_sentinel,
                    now,
                    created_by,
                    inactive_student_id,
                    int(source["id"]),
                    STATUS_DROPPED,
                    STATUS_ARCHIVED,
                    retained_roll,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "An inactive student roll changed during promotion. "
                    "The complete promotion was rolled back."
                )
            cursor.execute(
                """
                INSERT INTO student_class_history (
                    student_id, old_class_id, new_class_id,
                    effective_month, effective_year, change_type,
                    change_datetime, changed_by
                ) VALUES (?, ?, ?, ?, ?, 'PROMOTION', ?, ?)
                """,
                (
                    inactive_student_id,
                    int(source["id"]),
                    int(source["id"]),
                    int(context.effective_month),
                    int(context.effective_year),
                    now,
                    f"{created_by}: inactive roll release",
                ),
            )

        # Recalculate the final source-class rolls across the complete new-session
        # population. Target-session admissions/transfers were excluded from the
        # old P/F cohort, but they remain in the class and receive the first
        # continuous rolls; current-cycle failures follow them at the end.
        remaining_source_rows = cursor.execute(
            """
            SELECT * FROM students
            WHERE class_id=? AND student_status=?
            ORDER BY roll_number,student_id
            """,
            (int(source["id"]), STATUS_ACTIVE),
        ).fetchall()
        failed_by_id = {
            int(item[0]["student_id"]): item for item in failed_data
        }
        fixed_source_rows = [
            row for row in remaining_source_rows
            if int(row["student_id"]) not in failed_by_id
        ]
        failed_source_rows = [
            failed_by_id[int(row["student_id"])]
            for row in remaining_source_rows
            if int(row["student_id"]) in failed_by_id
        ]

        for row in remaining_source_rows:
            student_id = int(row["student_id"])
            cursor.execute(
                """
                UPDATE students SET roll_number=?,released_roll_number=NULL,
                    updated_at=?,updated_by=? WHERE student_id=?
                """,
                (-(1_100_000_000 + student_id), now, created_by, student_id),
            )

        final_source_order = [
            (row, None) for row in fixed_source_rows
        ] + [
            (item[0], item) for item in failed_source_rows
        ]
        for new_roll, (student_row, failed_item) in enumerate(
            final_source_order, start=1
        ):
            student_id = int(student_row["student_id"])
            cursor.execute(
                """
                UPDATE students SET roll_number=?,released_roll_number=NULL,
                    updated_at=?,updated_by=? WHERE student_id=?
                """,
                (new_roll, now, created_by, student_id),
            )
            if failed_item is None:
                continue
            _, old_roll, failure_before, failure_after = failed_item
            _insert_history(
                cursor,
                batch_id=batch_id,
                student_row=student_row,
                old_class_id=int(source["id"]),
                old_class_name=source["class_name"],
                new_class_id=int(source["id"]),
                new_class_name=source["class_name"],
                old_roll=old_roll,
                new_roll=new_roll,
                result="F",
                previous_status=STATUS_ACTIVE,
                new_status=STATUS_ACTIVE,
                failure_before=failure_before,
                failure_after=failure_after,
                now=now,
                effective_date=context.effective_date.isoformat(),
                promotion_type=PROMOTION_TYPE_FULL_CLASS,
                changed_by=created_by,
            )

        from app.services import sibling_fee_service

        sibling_affected_ids = {
            int(row["student_id"])
            for row in active_students
        }
        sibling_affected_ids.update(
            int(item["student_id"])
            for item in plan[
                "failure_relocations"
            ]
        )

        sibling_fee_service.recalculate_sibling_fees_for_student_ids(
            student_ids=sibling_affected_ids,
            effective_month=context.effective_month,
            effective_year=context.effective_year,
            connection=connection,
        )

        promotion_cycle_service.mark_cycle_completed(
            cursor, cycle_id, batch_id, actor=created_by,
            completed_at=execution_moment,
        )
        connection.commit()
        result = dict(plan)
        result["batch_id"] = batch_id
        result["batch_destination_class_id"] = destination_id
        result["batch_destination_class_name"] = destination_name
        result["promotion_mode"] = context.promotion_mode
        result["source_session_start_year"] = context.source_session_start_year
        result["target_session_start_year"] = context.target_session_start_year
        result["effective_date"] = context.effective_date.isoformat()
        try:
            from app.services import coming_session_service
            result["coming_session_activation"] = (
                coming_session_service.activate_due_coming_session_admissions(
                    as_of=execution_moment,
                    changed_by=f"promotion:{created_by}",
                )
            )
        except Exception as activation_error:
            result["coming_session_activation"] = {
                "success": False,
                "activated_count": 0,
                "errors": [f"{type(activation_error).__name__}: {activation_error}"],
            }
        return result

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

