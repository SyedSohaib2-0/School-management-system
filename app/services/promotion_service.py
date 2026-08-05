"""
Promotion service module.
Exposes validation, warning helpers, and the supported Full Class Promotion
workflow. Single-record promotion and promotion reversal are intentionally
not exposed or supported by the operational system.

Every promotion action runs as ONE database transaction using a single
connection, so student, history, batch, and graduation changes
either all succeed or all roll back together. Screens (UI files) must
call these functions instead of writing SQL directly.
"""

from datetime import datetime
import calendar
import logging

from app.database import get_connection
from app.config import (
    STATUS_ACTIVE,
    STATUS_DROPPED,
    STATUS_PARTIALLY_GRADUATED,
    STATUS_GRADUATED,
    get_next_class_level,
    build_class_name,
    section_rank,
)
from app.services import class_service

PROMOTION_TYPE_FULL_CLASS = "Full Class"

WARNING_MONTHS = 8
_last_promotion_integrity_warning = None

# Class 4 onward gender-section rules.
MALE_CLASS_SECTIONS = tuple("ABCDEF")
FEMALE_CLASS_SECTIONS = tuple("GHIJKLMNOPQRSTUVWXYZ")

# Defensive fallback. Schema version 6 stores the actual editable
# value in classes.student_capacity.
DEFAULT_STUDENT_CAPACITY = 40



# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_promotion_value(value: str) -> bool:
    """Only 'P' or 'F' (any case) are accepted promotion values."""
    if not value:
        return False
    return value.strip().upper() in ("P", "F")


# ---------------------------------------------------------------------------
# Read helpers (used by screens to show warnings before confirming)
# ---------------------------------------------------------------------------




def get_latest_batch_for_class(class_id: int):
    """Return the most recent non-reversed batch where this class was the source."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT * FROM promotion_batches
            WHERE source_class_id = ? AND is_reversed = 0
            ORDER BY promotion_datetime DESC LIMIT 1
            """,
            (class_id,),
        )
        return cursor.fetchone()
    finally:
        connection.close()


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    """Subtract whole calendar months while preserving a valid day."""
    total_month = value.year * 12 + (value.month - 1) - int(months)
    year, month_index = divmod(total_month, 12)
    month = month_index + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _parse_batch_datetime(batch_row) -> datetime:
    """Parse a promotion timestamp or record a controlled integrity warning."""
    global _last_promotion_integrity_warning
    raw_value = str(batch_row["promotion_datetime"] or "").strip()
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        batch_id = batch_row["batch_id"] if "batch_id" in batch_row.keys() else "unknown"
        message = (
            f"Promotion history batch {batch_id} contains an invalid timestamp "
            f"({raw_value!r}). The warning calculation was skipped; no promotion "
            "data was changed."
        )
        _last_promotion_integrity_warning = message
        logging.getLogger(__name__).error(message)
        raise ValueError(message) from error


def consume_promotion_integrity_warning() -> str | None:
    """Return and clear the latest controlled promotion-integrity warning."""
    global _last_promotion_integrity_warning
    message = _last_promotion_integrity_warning
    _last_promotion_integrity_warning = None
    return message

def was_class_promoted_within_months(class_id: int, months: int = WARNING_MONTHS) -> bool:
    """Check whether this class was itself the source of a recent Full Class Promotion."""
    latest = get_latest_batch_for_class(class_id)
    if not latest:
        return False
    try:
        promoted_at = _parse_batch_datetime(latest)
    except ValueError:
        return False
    return promoted_at >= _subtract_calendar_months(datetime.now(), months)


def get_latest_incoming_batch_for_class(
    class_id: int,
):
    """
    Return the latest non-reversed Full Class Promotion batch
    that actually moved at least one passed student into this
    class.

    The promotion_batches.destination_class_id field continues
    to support legacy single-destination batches. For a new
    multi-destination Class-3-to-4 batch, the actual destination
    is determined through each promotion_history record.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT pb.*
            FROM promotion_batches pb
            WHERE pb.is_reversed = 0
              AND (
                    pb.destination_class_id = ?
                    OR EXISTS (
                        SELECT 1
                        FROM promotion_history ph
                        WHERE ph.batch_id = pb.batch_id
                          AND ph.is_reversed = 0
                          AND ph.promotion_result = 'P'
                          AND ph.new_class_id = ?
                    )
                  )
            ORDER BY
                pb.promotion_datetime DESC,
                pb.batch_id DESC
            LIMIT 1
            """,
            (
                class_id,
                class_id,
            ),
        )

        return cursor.fetchone()

    finally:
        connection.close()



def was_class_promoted_into_within_months(class_id: int, months: int = WARNING_MONTHS) -> bool:
    """Check whether this class recently received students from a Full Class Promotion."""
    latest = get_latest_incoming_batch_for_class(class_id)
    if not latest:
        return False
    try:
        promoted_at = _parse_batch_datetime(latest)
    except ValueError:
        return False
    return promoted_at >= _subtract_calendar_months(datetime.now(), months)
# ---------------------------------------------------------------------------
# Internal helpers (used only within this file's own transactions)
# ---------------------------------------------------------------------------

def _get_max_roll_number(cursor, class_id: int) -> int:
    """Return the highest positive operational roll in one class.

    Released/starred students retain a negative internal sentinel only
    for uniqueness and display history. Those values must not affect new
    admission, transfer, reactivation, or promotion roll allocation.
    """
    cursor.execute(
        """
        SELECT COALESCE(MAX(roll_number), 0) AS max_roll
        FROM students
        WHERE class_id = ?
          AND roll_number > 0
        """,
        (int(class_id),),
    )
    row = cursor.fetchone()
    return int(row["max_roll"] or 0)


# ---------------------------------------------------------------------------
# Gender and capacity promotion planning
# ---------------------------------------------------------------------------

def _normalize_student_gender(
    gender_value,
) -> str:
    """
    Return MALE, FEMALE, or an empty string.

    Class 4 onward promotion requires an explicitly recorded
    Male/Female value. Missing or incompatible values are blocked.
    """
    normalized = str(
        gender_value
        or ""
    ).strip().upper()

    if normalized == "MALE":
        return "MALE"

    if normalized == "FEMALE":
        return "FEMALE"

    return ""


def _numeric_class_level(
    class_level,
):
    """
    Return the numeric class level, or None for Play Group,
    Nursery, KG, and any non-numeric value.
    """
    try:
        return int(
            str(
                class_level
            ).strip()
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def _class_capacity(
    class_row,
) -> int:
    """
    Read and validate the destination class capacity.

    The fallback supports defensive inspection of an older database,
    but normal application use requires schema version 6.
    """
    row_keys = set(
        class_row.keys()
    )

    raw_capacity = (
        class_row["student_capacity"]
        if "student_capacity" in row_keys
        else DEFAULT_STUDENT_CAPACITY
    )

    try:
        capacity = int(
            raw_capacity
        )

    except (
        TypeError,
        ValueError,
    ):
        capacity = 0

    if capacity < 1:
        raise ValueError(
            (
                f"{class_row['class_name']} has an invalid "
                "student capacity. Set a capacity greater than "
                "zero in Class Management."
            )
        )

    return capacity


def _active_class_strength(
    cursor,
    class_id: int,
) -> int:
    """
    Count Active students only. Dropped or Graduated students do
    not occupy a current classroom seat.
    """
    cursor.execute(
        """
        SELECT COUNT(*) AS active_count
        FROM students
        WHERE class_id = ?
          AND student_status = ?
        """,
        (
            class_id,
            STATUS_ACTIVE,
        ),
    )

    row = cursor.fetchone()

    return int(
        row["active_count"]
        or 0
    )


def _gender_requirement_for_section(
    section,
):
    """
    Return the required gender for a Class 4+ section.

    A-F = Male
    G-Z = Female
    """
    normalized_section = str(
        section
        or ""
    ).strip().upper()

    if normalized_section in MALE_CLASS_SECTIONS:
        return "MALE"

    if normalized_section in FEMALE_CLASS_SECTIONS:
        return "FEMALE"

    return None


def _student_reference(
    student_row,
) -> str:
    """
    Build a concise validation reference for error messages.
    """
    student_name = str(
        student_row["student_name"]
        or ""
    ).strip()

    admission_number = str(
        student_row["admission_number"]
        or ""
    ).strip()

    return (
        f"{student_name} ({admission_number})"
    )


def _load_active_students_with_cursor(
    cursor,
    class_id: int,
):
    """
    Load active students in stable source-roll order.
    """
    cursor.execute(
        """
        SELECT *
        FROM students
        WHERE class_id = ?
          AND student_status = ?
        ORDER BY
            roll_number ASC,
            student_id ASC
        """,
        (
            class_id,
            STATUS_ACTIVE,
        ),
    )

    return cursor.fetchall()



def _validate_class_4_plus_population(
    cursor,
    class_row,
) -> None:
    """
    LEGACY_BLANK_GENDER_COMPATIBILITY_V1

    Enforce Class 4+ section segregation without breaking older
    records whose Gender was saved before the Gender field existed.

    Rules:
    - A-F sections reject explicitly recorded Female students.
    - G-Z sections reject explicitly recorded Male students.
    - Legacy blank Gender values do not block an existing Class 4+
      promotion or capacity calculation.
    - Class 3 -> Class 4 routing still requires an explicit
      Male/Female Gender through the separate allocation planner.
    """
    class_level = _numeric_class_level(
        class_row["class_level"]
    )

    if (
        class_level is None
        or class_level < 4
    ):
        return

    required_gender = (
        _gender_requirement_for_section(
            class_row["section"]
        )
    )

    if required_gender is None:
        raise ValueError(
            (
                f"{class_row['class_name']} has an invalid section "
                "for Class 4 onward. Male sections must be A-F and "
                "Female sections must be G-Z."
            )
        )

    active_students = (
        _load_active_students_with_cursor(
            cursor,
            int(
                class_row["id"]
            ),
        )
    )

    incompatible_students = []

    for student_row in active_students:
        recorded_gender = (
            _normalize_student_gender(
                student_row["gender"]
            )
        )

        # Blank Gender is tolerated only for legacy records.
        # An explicitly recorded opposite Gender remains blocked.
        if (
            recorded_gender
            and recorded_gender
            != required_gender
        ):
            incompatible_students.append(
                student_row
            )

    if not incompatible_students:
        return

    sample_references = ", ".join(
        _student_reference(
            student_row
        )
        for student_row
        in incompatible_students[:5]
    )

    remaining_count = max(
        0,
        len(incompatible_students) - 5,
    )

    extra_text = (
        f", and {remaining_count} more"
        if remaining_count
        else ""
    )

    required_label = (
        "Male"
        if required_gender == "MALE"
        else "Female"
    )

    raise ValueError(
        (
            f"{class_row['class_name']} cannot be used for "
            "promotion because "
            f"{len(incompatible_students)} Active student(s) "
            "have an explicitly incompatible Gender. "
            f"This section permits {required_label} students only. "
            "Correct the affected records in Student Management: "
            f"{sample_references}{extra_text}. "
            "No student was promoted."
        )
    )



def _validate_full_class_results(
    active_students,
    promotion_values: dict,
):
    """
    Validate P/F for all Active students and divide the records into
    passed and failed groups.
    """
    if not active_students:
        raise ValueError(
            "No Active students were found in this class."
        )

    passed_students = []
    failed_students = []

    for student_row in active_students:
        student_id = int(
            student_row["student_id"]
        )

        result = promotion_values.get(
            student_id
        )

        if result is None:
            raise ValueError(
                (
                    "Please enter P or F for all students "
                    "before promotion."
                )
            )

        normalized_result = str(
            result
        ).strip().upper()

        if normalized_result not in {
            "P",
            "F",
        }:
            raise ValueError(
                (
                    f"Promotion result for "
                    f"{_student_reference(student_row)} "
                    "must be P or F."
                )
            )

        if normalized_result == "P":
            passed_students.append(
                student_row
            )

        else:
            failed_students.append(
                student_row
            )

    return (
        passed_students,
        failed_students,
    )


def _load_class_by_name_with_cursor(
    cursor,
    class_name: str,
):
    cursor.execute(
        """
        SELECT *
        FROM classes
        WHERE class_name = ?
        """,
        (
            class_name,
        ),
    )

    return cursor.fetchone()


def _load_permitted_destination_classes(
    cursor,
    class_level: str,
    permitted_sections,
):
    """
    Return existing permitted destination sections in alphabetical
    order. Missing sections are not created automatically.
    """
    section_placeholders = ", ".join(
        "?"
        for _section in permitted_sections
    )

    cursor.execute(
        f"""
        SELECT *
        FROM classes
        WHERE class_level = ?
          AND section IN (
              {section_placeholders}
          )
        """,
        (
            str(
                class_level
            ).strip().upper(),
            *permitted_sections,
        ),
    )

    return sorted(
        cursor.fetchall(),
        key=lambda row: (
            section_rank(row["section"]),
            str(row["section"] or "").strip().upper(),
        ),
    )


def _next_missing_permitted_section(
    existing_sections,
    permitted_sections,
):
    normalized_existing = {
        str(
            section
        ).strip().upper()
        for section in existing_sections
    }

    for section in permitted_sections:
        if section not in normalized_existing:
            return section

    return None


def _allocate_gender_group_to_class_4(
    cursor,
    students,
    required_gender: str,
):
    """
    Allocate one Class-3 gender group across existing Class-4
    sections.

    Existing sections are filled alphabetically and up to their
    individual capacity. No database write occurs here.
    """
    if not students:
        return (
            [],
            [],
        )

    if required_gender == "MALE":
        permitted_sections = (
            MALE_CLASS_SECTIONS
        )

        gender_label = "Male"

    elif required_gender == "FEMALE":
        permitted_sections = (
            FEMALE_CLASS_SECTIONS
        )

        gender_label = "Female"

    else:
        raise ValueError(
            "Unsupported gender allocation group."
        )

    destination_rows = (
        _load_permitted_destination_classes(
            cursor,
            "4",
            permitted_sections,
        )
    )

    existing_sections = [
        str(
            row["section"]
        ).strip().upper()
        for row in destination_rows
    ]

    remaining_students = list(
        students
    )

    allocations = []
    destination_summaries = []

    total_available_before = 0

    for destination_row in destination_rows:
        _validate_class_4_plus_population(
            cursor,
            destination_row,
        )

        destination_id = int(
            destination_row["id"]
        )

        current_strength = (
            _active_class_strength(
                cursor,
                destination_id,
            )
        )

        capacity = _class_capacity(
            destination_row
        )

        available_seats = max(
            0,
            capacity - current_strength,
        )

        total_available_before += (
            available_seats
        )

        allocation_count = min(
            available_seats,
            len(
                remaining_students
            ),
        )

        starting_roll = (
            _get_max_roll_number(
                cursor,
                destination_id,
            )
        )

        allocated_students = (
            remaining_students[
                :allocation_count
            ]
        )

        for offset, student_row in enumerate(
            allocated_students,
            start=1,
        ):
            allocations.append(
                {
                    "student_id": int(
                        student_row[
                            "student_id"
                        ]
                    ),
                    "student_name": (
                        student_row[
                            "student_name"
                        ]
                    ),
                    "admission_number": (
                        student_row[
                            "admission_number"
                        ]
                    ),
                    "gender": required_gender,
                    "destination_class_id": (
                        destination_id
                    ),
                    "destination_class_name": (
                        destination_row[
                            "class_name"
                        ]
                    ),
                    "new_roll_number": (
                        starting_roll
                        + offset
                    ),
                }
            )

        remaining_students = (
            remaining_students[
                allocation_count:
            ]
        )

        destination_summaries.append(
            {
                "class_id": destination_id,
                "class_name": (
                    destination_row[
                        "class_name"
                    ]
                ),
                "section": (
                    destination_row[
                        "section"
                    ]
                ),
                "gender": required_gender,
                "capacity": capacity,
                "current_strength": (
                    current_strength
                ),
                "available_before": (
                    available_seats
                ),
                "allocated": (
                    allocation_count
                ),
                "strength_after": (
                    current_strength
                    + allocation_count
                ),
                "available_after": (
                    capacity
                    - current_strength
                    - allocation_count
                ),
            }
        )

        if not remaining_students:
            break

    if remaining_students:
        next_section = (
            _next_missing_permitted_section(
                existing_sections,
                permitted_sections,
            )
        )

        if next_section:
            required_class_name = (
                build_class_name(
                    "4",
                    next_section,
                )
            )

            instruction = (
                f"Create {required_class_name} in "
                "Class Management"
            )

        else:
            permitted_range = (
                "4A-4F"
                if required_gender == "MALE"
                else "4G-4Z"
            )

            instruction = (
                f"All permitted sections {permitted_range} "
                "already exist. Increase an appropriate "
                "class capacity before promotion"
            )

        raise ValueError(
            (
                f"{gender_label} Class-4 allocation cannot "
                "be completed. "
                f"{len(students)} passed {gender_label.lower()} "
                "student(s) require seats, but the existing "
                "permitted sections have only "
                f"{total_available_before} available seat(s). "
                f"{instruction} for the remaining "
                f"{len(remaining_students)} student(s), "
                "then run promotion again. "
                "No student was promoted."
            )
        )

    return (
        allocations,
        destination_summaries,
    )


def _build_exact_destination_allocations(
    cursor,
    passed_students,
    destination_row,
    enforce_capacity: bool,
):
    """
    Allocate passed students to one exact destination section.

    Class 4 onward keeps the same section letter, for example:
    4A -> 5A and 4G -> 5G.
    """
    if not passed_students:
        return (
            [],
            [],
        )

    destination_id = int(
        destination_row["id"]
    )

    if enforce_capacity:
        _validate_class_4_plus_population(
            cursor,
            destination_row,
        )

    current_strength = (
        _active_class_strength(
            cursor,
            destination_id,
        )
    )

    capacity = _class_capacity(
        destination_row
    )

    available_seats = max(
        0,
        capacity - current_strength,
    )

    if (
        enforce_capacity
        and len(passed_students)
        > available_seats
    ):
        raise ValueError(
            (
                f"{destination_row['class_name']} has only "
                f"{available_seats} available seat(s), but "
                f"{len(passed_students)} passed student(s) "
                "require promotion into this exact section. "
                "Increase its capacity before promotion. "
                "No student was promoted."
            )
        )

    starting_roll = (
        _get_max_roll_number(
            cursor,
            destination_id,
        )
    )

    required_gender = (
        _gender_requirement_for_section(
            destination_row["section"]
        )
        if enforce_capacity
        else None
    )

    allocations = []

    for offset, student_row in enumerate(
        passed_students,
        start=1,
    ):
        allocations.append(
            {
                "student_id": int(
                    student_row["student_id"]
                ),
                "student_name": (
                    student_row["student_name"]
                ),
                "admission_number": (
                    student_row[
                        "admission_number"
                    ]
                ),
                "gender": (
                    required_gender
                    or _normalize_student_gender(
                        student_row["gender"]
                    )
                ),
                "destination_class_id": (
                    destination_id
                ),
                "destination_class_name": (
                    destination_row[
                        "class_name"
                    ]
                ),
                "new_roll_number": (
                    starting_roll
                    + offset
                ),
            }
        )

    destination_summary = {
        "class_id": destination_id,
        "class_name": (
            destination_row["class_name"]
        ),
        "section": (
            destination_row["section"]
        ),
        "gender": required_gender,
        "capacity": capacity,
        "current_strength": current_strength,
        "available_before": available_seats,
        "allocated": len(
            passed_students
        ),
        "strength_after": (
            current_strength
            + len(
                passed_students
            )
        ),
        "available_after": (
            capacity
            - current_strength
            - len(
                passed_students
            )
        ),
    }

    return (
        allocations,
        [
            destination_summary,
        ],
    )


def _build_full_class_promotion_plan(
    cursor,
    class_id: int,
    promotion_values: dict,
):
    """
    Build and validate the complete Full Class Promotion plan.

    This function performs no INSERT, UPDATE, DELETE, or COMMIT.
    It either returns a complete plan or raises ValueError before
    any student is changed.
    """
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

    source_class_row = (
        cursor.fetchone()
    )

    if not source_class_row:
        raise ValueError(
            "Class not found."
        )

    active_students = (
        _load_active_students_with_cursor(
            cursor,
            class_id,
        )
    )

    (
        passed_students,
        failed_students,
    ) = _validate_full_class_results(
        active_students,
        promotion_values,
    )

    source_numeric_level = (
        _numeric_class_level(
            source_class_row[
                "class_level"
            ]
        )
    )

    next_class_level = (
        get_next_class_level(
            source_class_row[
                "class_level"
            ]
        )
    )

    plan = {
        "source_class_id": int(
            source_class_row["id"]
        ),
        "source_class_name": (
            source_class_row[
                "class_name"
            ]
        ),
        "source_class_level": (
            source_class_row[
                "class_level"
            ]
        ),
        "source_section": (
            source_class_row[
                "section"
            ]
        ),
        "total_students": len(
            active_students
        ),
        "passed_students": len(
            passed_students
        ),
        "failed_students": len(
            failed_students
        ),
        "graduated_students": 0,
        "passed_allocations": [],
        "failed_student_ids": [
            int(
                student_row[
                    "student_id"
                ]
            )
            for student_row
            in failed_students
        ],
        "graduated_student_ids": [],
        "destination_summaries": [],
        "routing_type": (
            "STANDARD"
        ),
    }

    # Class 10 passed students graduate.
    if next_class_level is None:
        plan[
            "graduated_students"
        ] = len(
            passed_students
        )

        plan[
            "graduated_student_ids"
        ] = [
            int(
                student_row[
                    "student_id"
                ]
            )
            for student_row
            in passed_students
        ]

        plan["routing_type"] = (
            "GRADUATION"
        )

        return plan

    # -------------------------------------------------------
    # Class 3 -> Class 4 gender split
    # -------------------------------------------------------

    if (
        source_numeric_level == 3
        and str(
            next_class_level
        ).strip().upper()
        == "4"
    ):
        male_students = []
        female_students = []
        invalid_gender_students = []

        for student_row in passed_students:
            gender = (
                _normalize_student_gender(
                    student_row["gender"]
                )
            )

            if gender == "MALE":
                male_students.append(
                    student_row
                )

            elif gender == "FEMALE":
                female_students.append(
                    student_row
                )

            else:
                invalid_gender_students.append(
                    student_row
                )

        if invalid_gender_students:
            sample_references = ", ".join(
                _student_reference(
                    student_row
                )
                for student_row
                in invalid_gender_students[
                    :5
                ]
            )

            remaining_count = max(
                0,
                len(
                    invalid_gender_students
                )
                - 5,
            )

            extra_text = (
                f", and {remaining_count} more"
                if remaining_count
                else ""
            )

            raise ValueError(
                (
                    f"{len(invalid_gender_students)} passed "
                    "student(s) do not have a valid Male/Female "
                    "Gender for Class 4 allocation. Update Gender "
                    "in Student Management first: "
                    f"{sample_references}{extra_text}. "
                    "No student was promoted."
                )
            )

        (
            male_allocations,
            male_summaries,
        ) = _allocate_gender_group_to_class_4(
            cursor,
            male_students,
            "MALE",
        )

        (
            female_allocations,
            female_summaries,
        ) = _allocate_gender_group_to_class_4(
            cursor,
            female_students,
            "FEMALE",
        )

        plan[
            "passed_allocations"
        ] = (
            male_allocations
            + female_allocations
        )

        plan[
            "destination_summaries"
        ] = (
            male_summaries
            + female_summaries
        )

        plan["routing_type"] = (
            "CLASS_3_TO_4_GENDER_SPLIT"
        )

        return plan

    # -------------------------------------------------------
    # Class 4 onward retains exact section letter
    # -------------------------------------------------------

    enforce_gender_capacity = (
        source_numeric_level is not None
        and source_numeric_level >= 4
    )

    if enforce_gender_capacity:
        _validate_class_4_plus_population(
            cursor,
            source_class_row,
        )

    destination_name = build_class_name(
        next_class_level,
        source_class_row["section"],
    )

    destination_row = (
        _load_class_by_name_with_cursor(
            cursor,
            destination_name,
        )
    )

    if not destination_row:
        raise ValueError(
            (
                f"Destination class {destination_name} "
                "does not exist yet. Create it first "
                "in Class Management. "
                "No student was promoted."
            )
        )

    (
        allocations,
        destination_summaries,
    ) = _build_exact_destination_allocations(
        cursor,
        passed_students,
        destination_row,
        enforce_gender_capacity,
    )

    plan[
        "passed_allocations"
    ] = allocations

    plan[
        "destination_summaries"
    ] = destination_summaries

    plan["routing_type"] = (
        "SAME_SECTION_CLASS_4_PLUS"
        if enforce_gender_capacity
        else "STANDARD"
    )

    return plan




# ---------------------------------------------------------------------------
# Full-class promotion routing v2
# ---------------------------------------------------------------------------
# PROMOTION_FULL_CLASS_V2_DELEGATION
#
# The public full-class API delegates to the destination-level sequence,
# failure-priority relocation, and compatible-section overflow planner.
# Legacy reversal-history columns remain in the audit tables for compatibility.
from app.services.promotion_full_class_v2 import (
    validate_promotion_window
        as _v2_validate_promotion_window,
    get_promotion_context
        as _v2_get_promotion_context,
    get_promotion_cohort_students
        as _v2_get_promotion_cohort_students,
    get_classes_in_descending_promotion_order
        as _v2_get_classes_in_descending_promotion_order,
    preview_full_class_promotion_plan
        as _v2_preview_full_class_promotion_plan,
    promote_full_class
        as _v2_promote_full_class,
)

validate_promotion_window = (
    _v2_validate_promotion_window
)
get_promotion_context = (
    _v2_get_promotion_context
)
get_promotion_cohort_students = (
    _v2_get_promotion_cohort_students
)
get_classes_in_descending_promotion_order = (
    _v2_get_classes_in_descending_promotion_order
)
preview_full_class_promotion_plan = (
    _v2_preview_full_class_promotion_plan
)
promote_full_class = (
    _v2_promote_full_class
)
