"""
Student service module.
Contains all validation rules and database operations for students.
Screens (UI files) must call these functions instead of writing SQL
or validation logic directly, so that rules stay consistent everywhere.
"""

import re
from datetime import datetime

from app.database import get_connection
from app.normalization import (
    normalize_person_name,
    is_valid_person_name,
    canonicalize_tracking_id,
)
from app.config import (
    STATUS_ACTIVE,
    STATUS_DROPPED,
    STATUS_PARTIALLY_GRADUATED,
    STATUS_GRADUATED,
    STATUS_ARCHIVED,
    STATUS_COMING_SESSION,
    ALL_STUDENT_STATUSES,
    CONTACT_COUNTRY_CODE,
    CONTACT_DIGITS_REQUIRED,
    CNIC_DIGITS_REQUIRED,
    SLC_OPTIONAL_CLASS_LEVELS,
)


INACTIVE_FEE_STATUS_TRANSITION_V9 = True

COMPLETION_REACTIVATION_LIKE_DROPPED_V10 = True

MAX_PERSON_NAME_LENGTH = 200
DOB_REQUIRED_ERROR = "Date of Birth is required."
DOB_FORMAT_ERROR = "Date of Birth must use DD-MM-YYYY format."


# ---------------------------------------------------------------------------
# Validation functions
# ---------------------------------------------------------------------------

def validate_person_name(name: str) -> bool:
    """Validate a normalized student or father name."""
    normalized_name = normalize_person_name(name)
    if not 1 <= len(normalized_name) <= MAX_PERSON_NAME_LENGTH:
        return False
    return is_valid_person_name(normalized_name)


def validate_person_name_length(name: str) -> str:
    """Return the normalized name after enforcing the approved length."""
    normalized_name = normalize_person_name(name)
    if not 1 <= len(normalized_name) <= MAX_PERSON_NAME_LENGTH:
        raise ValueError(
            "Student Name and Father Name must contain between "
            "1 and 200 normalized characters."
        )
    return normalized_name


def normalize_father_cnic(cnic: str, required: bool = True) -> str:
    """Return the approved 13-digit Father CNIC representation."""
    raw_value = str(cnic or "").strip()
    if not raw_value:
        if required:
            raise ValueError("Father CNIC is required and must contain exactly 13 digits.")
        return ""

    if re.search(r"[^0-9\s-]", raw_value):
        raise ValueError("Father CNIC must contain exactly 13 numeric digits.")

    normalized_value = re.sub(r"[\s-]+", "", raw_value)
    if not re.fullmatch(rf"\d{{{CNIC_DIGITS_REQUIRED}}}", normalized_value):
        raise ValueError("Father CNIC must contain exactly 13 numeric digits.")
    if len(set(normalized_value)) == 1:
        raise ValueError(
            "Father CNIC cannot contain the same digit repeated 13 times."
        )
    return normalized_value


def validate_cnic(cnic: str) -> bool:
    """Return True only for a complete mandatory Father CNIC."""
    try:
        normalize_father_cnic(cnic, required=True)
        return True
    except ValueError:
        return False


def validate_contact_number(digits_only: str) -> bool:
    """
    Validate the 10 digits entered after the fixed +92 country code.
    Only numeric digits are allowed, and exactly 10 digits are required.
    """
    if not digits_only:
        return False
    return bool(re.fullmatch(rf"\d{{{CONTACT_DIGITS_REQUIRED}}}", digits_only.strip()))


def format_contact_number(digits_only: str) -> str:
    """Combine the fixed country code with the entered digits for storage."""
    return f"{CONTACT_COUNTRY_CODE}{digits_only.strip()}"


def validate_optional_contact_number(
    digits_only: str,
) -> bool:
    """
    Validate an optional Pakistan contact number.

    Empty input is accepted. A supplied number must contain exactly
    ten digits after the fixed +92 prefix.
    """
    cleaned = (
        digits_only.strip()
        if digits_only
        else ""
    )

    if not cleaned:
        return True

    return validate_contact_number(
        cleaned
    )


def format_optional_contact_number(
    digits_only: str,
) -> str:
    """Format an optional Pakistan contact number for storage."""
    cleaned = (
        digits_only.strip()
        if digits_only
        else ""
    )

    if not cleaned:
        return ""

    return format_contact_number(
        cleaned
    )


def validate_gender(
    gender: str,
) -> bool:
    """Accept the supported Add Student gender values."""
    return (
        gender.strip().upper()
        in {
            "MALE",
            "FEMALE",
        }
    )


ADMISSION_NUMBER_PREFIXES = frozenset({"H", "P"})
ADMISSION_NUMBER_MIN_SEQUENCE = 1001
ADMISSION_NUMBER_MAX_SEQUENCE = 99999


def parse_generated_admission_number(admission_number: str) -> tuple[str, int]:
    """Return the canonical H/P prefix and global numeric sequence."""
    normalized = normalize_admission_number_search(admission_number)
    match = re.fullmatch(r"([HP])(\d{4,5})", normalized)
    if match is None:
        raise ValueError(
            "Admission Number must start with H or P and contain four or five digits."
        )
    sequence = int(match.group(2))
    if not ADMISSION_NUMBER_MIN_SEQUENCE <= sequence <= ADMISSION_NUMBER_MAX_SEQUENCE:
        raise ValueError(
            "Admission-number sequence must be between 1001 and 99999."
        )
    return match.group(1), sequence


def build_manual_admission_number(
    prefix: str,
    numeric_part: str,
) -> str:
    """Build and validate a generated H/P admission number."""
    normalized_prefix = str(prefix or "").strip().upper()
    normalized_number = str(numeric_part or "").strip()
    if normalized_prefix not in ADMISSION_NUMBER_PREFIXES:
        raise ValueError("Admission-number prefix must be H or P.")
    candidate = f"{normalized_prefix}{normalized_number}"
    parse_generated_admission_number(candidate)
    return candidate


def normalize_date_of_birth(
    date_text: str,
) -> str:
    """Convert mandatory strict DD-MM-YYYY input to YYYY-MM-DD storage."""
    cleaned = str(date_text or "").strip()
    if not cleaned:
        raise ValueError(DOB_REQUIRED_ERROR)
    if cleaned == "00-00-0000":
        raise ValueError(DOB_FORMAT_ERROR)
    if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", cleaned):
        raise ValueError(DOB_FORMAT_ERROR)
    try:
        parsed_date = datetime.strptime(cleaned, "%d-%m-%Y").date()
    except ValueError as error:
        raise ValueError("Date of Birth must be a valid calendar date.") from error
    if parsed_date > datetime.now().date():
        raise ValueError("Date of birth cannot be in the future.")
    return parsed_date.strftime("%Y-%m-%d")

def format_date_of_birth_for_display(
    stored_date: str,
) -> str:
    """Convert a stored YYYY-MM-DD date to DD-MM-YYYY."""
    cleaned = (
        stored_date.strip()
        if stored_date
        else ""
    )

    if not cleaned:
        return "-"

    try:
        parsed_date = datetime.strptime(
            cleaned,
            "%Y-%m-%d",
        )

    except ValueError:
        return cleaned

    return parsed_date.strftime(
        "%d-%m-%Y"
    )


def is_slc_required(class_level: str) -> bool:
    """
    The School Leaving Certificate checkbox is required for every class
    except Play Group, Play Group A, and Play Group B.
    """
    return class_level.strip().upper() not in SLC_OPTIONAL_CLASS_LEVELS


def validate_admission_date(year: int, month: int, day: int) -> bool:
    """Check that the given year, month, and day form a real calendar date."""
    try:
        datetime(year=year, month=month, day=day)
        return True
    except ValueError:
        return False


ROLL_SEARCH_ERROR_MESSAGE = (
    "Roll Number must be a positive number. For a released student, "
    "you may enter a value such as 1*."
)


def normalize_admission_number_search(admission_number) -> str:
    """Normalize a user-supplied admission identifier for exact lookup."""
    return str(admission_number or "").strip().upper()


def parse_roll_number_search(roll_number):
    """Return (positive displayed roll, released-only flag) for UI searches."""
    if roll_number is None:
        return None, False

    roll_text = str(roll_number).strip()
    if not roll_text:
        return None, False

    released_only = roll_text.endswith("*")
    if released_only:
        roll_text = roll_text[:-1].strip()

    if (
        not roll_text
        or not roll_text.isdigit()
        or int(roll_text) <= 0
    ):
        raise ValueError(ROLL_SEARCH_ERROR_MESSAGE)

    return int(roll_text), released_only


def validate_date_of_birth_before_admission(
    date_of_birth,
    admission_datetime: datetime,
    *,
    allow_unchanged_stored_iso: bool = False,
):
    """Require DOB, normalize it, and enforce birth before admission."""
    cleaned = str(date_of_birth or "").strip()
    if not cleaned:
        raise ValueError(DOB_REQUIRED_ERROR)

    if allow_unchanged_stored_iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        try:
            parsed_date = datetime.strptime(cleaned, "%Y-%m-%d").date()
        except ValueError as error:
            raise ValueError("Stored Date of Birth is invalid and must be corrected.") from error
    else:
        stored_value = normalize_date_of_birth(cleaned)
        parsed_date = datetime.strptime(stored_value, "%Y-%m-%d").date()

    if parsed_date > datetime.now().date():
        raise ValueError("Date of birth cannot be in the future.")
    if parsed_date >= admission_datetime.date():
        raise ValueError("Date of Birth must be earlier than the Admission Date.")
    return parsed_date.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Admission Number generation
# ---------------------------------------------------------------------------

def _next_admission_sequence_with_cursor(cursor) -> int:
    """Return MAX(global H/P numeric sequence)+1 without ever reusing a number."""
    rows = cursor.execute(
        "SELECT admission_number FROM students"
    ).fetchall()
    highest_sequence = ADMISSION_NUMBER_MIN_SEQUENCE - 1
    for row in rows:
        raw_value = row["admission_number"] if hasattr(row, "keys") else row[0]
        try:
            _prefix, sequence = parse_generated_admission_number(raw_value)
        except ValueError:
            # Existing legacy identifiers remain untouched and do not consume
            # the new generated H/P numeric sequence unless they match it.
            continue
        highest_sequence = max(highest_sequence, sequence)
    next_sequence = highest_sequence + 1
    if next_sequence > ADMISSION_NUMBER_MAX_SEQUENCE:
        raise ValueError(
            "The admission-number sequence has reached 99999. A new Admission Number cannot be generated."
        )
    return next_sequence


def generate_admission_number(prefix: str) -> str:
    """Generate the next global H/P admission number for the selected prefix."""
    normalized_prefix = str(prefix or "").strip().upper()
    if normalized_prefix not in ADMISSION_NUMBER_PREFIXES:
        raise ValueError("Admission-number prefix must be H or P.")
    connection = get_connection()
    try:
        next_sequence = _next_admission_sequence_with_cursor(connection.cursor())
        return f"{normalized_prefix}{next_sequence}"
    finally:
        connection.close()


def is_admission_number_unique(
    admission_number: str,
    exclude_student_id: int = None,
) -> bool:
    """
    Check that no other student uses the admission number.

    During Edit Student, exclude_student_id allows the student's
    own existing admission number.
    """
    normalized_number = normalize_admission_number_search(admission_number)
    sequence = None
    try:
        _prefix, sequence = parse_generated_admission_number(normalized_number)
    except ValueError:
        # Legacy identifiers retain exact normalized uniqueness.
        pass

    connection = get_connection()
    try:
        cursor = connection.cursor()
        exclusions = "" if exclude_student_id is None else " AND student_id != ?"
        parameters = [normalized_number]
        if exclude_student_id is not None:
            parameters.append(int(exclude_student_id))
        cursor.execute(
            "SELECT 1 FROM students WHERE TRIM(admission_number)=? COLLATE NOCASE"
            + exclusions + " LIMIT 1",
            parameters,
        )
        if cursor.fetchone() is not None:
            return False
        if sequence is None:
            return True
        parameters = [int(sequence)]
        if exclude_student_id is not None:
            parameters.append(int(exclude_student_id))
        cursor.execute(
            """
            SELECT 1 FROM students
            WHERE LENGTH(UPPER(TRIM(admission_number))) IN (5,6)
              AND SUBSTR(UPPER(TRIM(admission_number)),1,1) IN ('H','P')
              AND SUBSTR(UPPER(TRIM(admission_number)),2)<>''
              AND SUBSTR(UPPER(TRIM(admission_number)),2) NOT GLOB '*[^0-9]*'
              AND CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER)=?
            """ + exclusions + " LIMIT 1",
            parameters,
        )
        return cursor.fetchone() is None
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Roll Number generation
# ---------------------------------------------------------------------------

def generate_roll_number(class_id: int) -> int:
    # COUNT(*) + 1 is unsafe after transfers or promotions because
    # student count can fall while a higher operational roll remains.
    # Released/starred records use negative internal sentinels and must
    # never participate in operational roll allocation.
    connection = get_connection()

    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COALESCE(MAX(roll_number), 0) AS maximum_roll_number "
            "FROM students WHERE class_id = ? AND roll_number > 0",
            (int(class_id),),
        )
        row = cursor.fetchone()
        maximum = (
            int(row["maximum_roll_number"])
            if row is not None
            else 0
        )
        return maximum + 1

    finally:
        connection.close()


def generate_transfer_roll_number(
    class_id: int,
    exclude_student_id: int = None,
) -> int:
    """
    Return the next available roll number for a destination
    class or section.

    The result is MAX(existing positive operational roll number) + 1.
    Released/starred negative sentinel values are ignored.
    """
    connection = get_connection()
    try:
        cursor = connection.cursor()
        if exclude_student_id is None:
            cursor.execute(
                """
                SELECT COALESCE(
                    MAX(roll_number),
                    0
                ) AS highest_roll_number
                FROM students
                WHERE class_id = ?
                  AND roll_number > 0
                """,
                (int(class_id),),
            )
        else:
            cursor.execute(
                """
                SELECT COALESCE(
                    MAX(roll_number),
                    0
                ) AS highest_roll_number
                FROM students
                WHERE class_id = ?
                  AND roll_number > 0
                  AND student_id != ?
                """,
                (int(class_id), int(exclude_student_id)),
            )
        row = cursor.fetchone()
        return int(row["highest_roll_number"] or 0) + 1
    finally:
        connection.close()


def is_roll_number_unique_in_class(class_id: int, roll_number: int, exclude_student_id: int = None) -> bool:
    connection = get_connection()

    try:
        cursor = connection.cursor()

        if exclude_student_id is None:
            cursor.execute(
                "SELECT 1 FROM students "
                "WHERE class_id = ? AND roll_number = ? LIMIT 1",
                (int(class_id), int(roll_number)),
            )
        else:
            cursor.execute(
                "SELECT 1 FROM students "
                "WHERE class_id = ? AND roll_number = ? "
                "AND student_id != ? LIMIT 1",
                (
                    int(class_id),
                    int(roll_number),
                    int(exclude_student_id),
                ),
            )

        return cursor.fetchone() is None

    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def _find_existing_student_identity_with_cursor(
    cursor,
    student_name: str,
    father_name: str,
    father_cnic: str = "",
    exclude_student_id: int = None,
):
    """Return one normalized identity match using the supplied transaction."""
    normalized_student_name = normalize_person_name(student_name)
    normalized_father_name = normalize_person_name(father_name)
    normalized_father_cnic = normalize_father_cnic(
        father_cnic,
        required=False,
    )

    query = """
        SELECT
            s.student_id, s.admission_number, s.class_id,
            s.student_name, s.father_name, s.father_cnic,
            s.student_status, c.class_name
        FROM students AS s
        LEFT JOIN classes AS c ON c.id = s.class_id
        WHERE NORMALIZE_PERSON_NAME(s.student_name) = ?
          AND NORMALIZE_PERSON_NAME(s.father_name) = ?
          AND COALESCE(TRIM(s.father_cnic), '') = ?
    """
    parameters = [
        normalized_student_name,
        normalized_father_name,
        normalized_father_cnic,
    ]
    if exclude_student_id is not None:
        query += " AND s.student_id <> ?"
        parameters.append(int(exclude_student_id))
    query += " ORDER BY s.student_id ASC LIMIT 1"
    cursor.execute(query, parameters)
    return cursor.fetchone()


def find_existing_student_identity(
    student_name: str,
    father_name: str,
    father_cnic: str = "",
    exclude_student_id: int = None,
):
    """Return an existing student whose normalized identity is identical."""
    connection = get_connection()
    try:
        return _find_existing_student_identity_with_cursor(
            connection.cursor(),
            student_name=student_name,
            father_name=father_name,
            father_cnic=father_cnic,
            exclude_student_id=exclude_student_id,
        )
    finally:
        connection.close()


def find_suspected_duplicate_student_groups():
    """Report normalized duplicate identities without modifying any record."""
    connection = get_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                NORMALIZE_PERSON_NAME(student_name) AS student_name_key,
                NORMALIZE_PERSON_NAME(father_name) AS father_name_key,
                COALESCE(TRIM(father_cnic), '') AS father_cnic_key,
                GROUP_CONCAT(student_id, ',') AS student_ids,
                COUNT(*) AS record_count
            FROM students
            GROUP BY student_name_key, father_name_key, father_cnic_key
            HAVING COUNT(*) > 1
            ORDER BY student_name_key, father_name_key, father_cnic_key
            """
        )
        return [
            {
                "student_ids": [int(value) for value in str(row["student_ids"]).split(",")],
                "student_name": row["student_name_key"],
                "father_name": row["father_name_key"],
                "father_cnic": row["father_cnic_key"],
                "record_count": int(row["record_count"]),
            }
            for row in cursor.fetchall()
        ]
    finally:
        connection.close()


def student_identity_exists(
    student_name: str,
    father_name: str,
    father_cnic: str = "",
    exclude_student_id: int = None,
) -> bool:
    return find_existing_student_identity(
        student_name=student_name,
        father_name=father_name,
        father_cnic=father_cnic,
        exclude_student_id=exclude_student_id,
    ) is not None


# CLASS_4_PLUS_SHARED_GENDER_RESTRICTION_V1

MALE_CLASS_SECTIONS = frozenset(
    "ABCDEF"
)

FEMALE_CLASS_SECTIONS = frozenset(
    "GHIJKLMNOPQRSTUVWXYZ"
)


def _normalize_assignment_gender(
    gender,
) -> str:
    return str(
        gender
        or ""
    ).strip().upper()


def _numeric_class_level(
    class_level,
):
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


def _load_class_gender_route_with_cursor(
    cursor,
    class_id: int,
):
    """
    Return the class row and the required Class 4+ gender.

    Classes below level 4 return required_gender=None.
    """
    cursor.execute(
        """
        SELECT
            id,
            class_level,
            section,
            class_name,
            is_active
        FROM classes
        WHERE id = ?
        """,
        (
            int(
                class_id
            ),
        ),
    )

    class_row = cursor.fetchone()

    if not class_row:
        raise ValueError(
            "The selected class could not be found."
        )

    class_level = _numeric_class_level(
        class_row[
            "class_level"
        ]
    )

    if (
        class_level is None
        or class_level < 4
    ):
        return class_row, None

    section = str(
        class_row[
            "section"
        ]
        or ""
    ).strip().upper()

    if (
        len(section) == 1
        and section
        in MALE_CLASS_SECTIONS
    ):
        return class_row, "MALE"

    if (
        len(section) == 1
        and section
        in FEMALE_CLASS_SECTIONS
    ):
        return class_row, "FEMALE"

    raise ValueError(
        (
            f"{class_row['class_name']} has an invalid "
            "Class 4+ section. Class 4 and above must "
            "use A-F for Male students or G-Z for "
            "Female students."
        )
    )


def _validate_class_4_plus_gender_assignment_with_cursor(
    cursor,
    *,
    class_id: int,
    gender,
    student_status,
    existing_class_id=None,
    existing_gender=None,
    allow_legacy_blank_route: bool = False,
) -> None:
    """
    Enforce gender-separated Class 4+ Active assignments.

    A-F sections are Male.
    G-Z sections are Female.

    Existing legacy blank-gender records remain compatible when
    they stay in the same class or move within the same section
    gender lane. They cannot enter Class 4+ from a lower class or
    cross between the A-F and G-Z lanes without setting Gender.
    """
    destination_class, required_gender = (
        _load_class_gender_route_with_cursor(
            cursor,
            class_id,
        )
    )

    if required_gender is None:
        return

    normalized_status = str(
        student_status
        or ""
    ).strip().upper()

    active_statuses = {
        str(STATUS_ACTIVE).strip().upper(),
        str(STATUS_COMING_SESSION).strip().upper(),
    }

    # The segregation rule applies to current Active and reserved
    # coming-session assignments. Historical inactive records remain untouched.
    if normalized_status not in active_statuses:
        return

    normalized_gender = (
        _normalize_assignment_gender(
            gender
        )
    )

    if normalized_gender not in {
        "",
        "MALE",
        "FEMALE",
    }:
        raise ValueError(
            "Gender must be Male or Female."
        )

    if not normalized_gender:
        legacy_route_allowed = False

        if (
            allow_legacy_blank_route
            and existing_class_id is not None
            and not _normalize_assignment_gender(
                existing_gender
            )
        ):
            old_class_id = int(
                existing_class_id
            )

            new_class_id = int(
                class_id
            )

            if old_class_id == new_class_id:
                legacy_route_allowed = True

            else:
                (
                    _source_class,
                    source_required_gender,
                ) = (
                    _load_class_gender_route_with_cursor(
                        cursor,
                        old_class_id,
                    )
                )

                if (
                    source_required_gender
                    is not None
                    and source_required_gender
                    == required_gender
                ):
                    legacy_route_allowed = True

        if legacy_route_allowed:
            return

        raise ValueError(
            (
                "Gender is required before assigning an "
                "Active student to Class 4 or above. "
                "Select Male for sections A-F or Female "
                "for sections G-Z."
            )
        )

    if normalized_gender != required_gender:
        required_label = (
            "Male"
            if required_gender == "MALE"
            else "Female"
        )

        supplied_label = (
            "Male"
            if normalized_gender == "MALE"
            else "Female"
        )

        raise ValueError(
            (
                f"{destination_class['class_name']} is "
                f"reserved for {required_label} students. "
                f"An Active {supplied_label} student cannot "
                "be assigned to this class."
            )
        )



# EDIT_STUDENT_GENDER_SECTION_AUTO_ROUTING_V1

AUTO_ROUTE_DEFAULT_CAPACITY = 40


def _class_capacity_snapshot_with_cursor(
    cursor,
    class_id: int,
):
    """
    Return one class with its current Active-student strength
    and configured capacity.
    """
    cursor.execute(
        """
        SELECT
            c.*,
            COUNT(
                CASE
                    WHEN s.student_status = ?
                    THEN 1
                END
            ) AS active_students
        FROM classes AS c
        LEFT JOIN students AS s
          ON s.class_id = c.id
        WHERE c.id = ?
        GROUP BY c.id
        """,
        (
            STATUS_ACTIVE,
            int(
                class_id
            ),
        ),
    )

    row = cursor.fetchone()

    if row is None:
        raise ValueError(
            "The selected class could not be found."
        )

    details = dict(
        row
    )

    capacity = int(
        details.get(
            "student_capacity",
            AUTO_ROUTE_DEFAULT_CAPACITY,
        )
        or AUTO_ROUTE_DEFAULT_CAPACITY
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

    details[
        "active_students"
    ] = active_students

    details[
        "available_seats"
    ] = max(
        0,
        capacity - active_students,
    )

    return details


def _class_can_accept_active_student(
    class_details,
    *,
    candidate_class_id: int,
    existing_class_id: int,
    existing_student_status,
) -> bool:
    """
    Check capacity after accounting for the student's current seat.

    An already-Active student remaining in the same class does not
    require an additional seat.
    """
    normalized_existing_status = str(
        existing_student_status
        or ""
    ).strip().upper()

    normalized_active_status = str(
        STATUS_ACTIVE
    ).strip().upper()

    already_occupies_candidate = (
        int(
            candidate_class_id
        )
        == int(
            existing_class_id
        )
        and normalized_existing_status
        == normalized_active_status
    )

    additional_active_students = (
        0
        if already_occupies_candidate
        else 1
    )

    resulting_strength = (
        int(
            class_details[
                "active_students"
            ]
        )
        + additional_active_students
    )

    return (
        resulting_strength
        <= int(
            class_details[
                "student_capacity"
            ]
        )
    )


def _next_transfer_roll_with_cursor(
    cursor,
    *,
    class_id: int,
    student_id: int,
) -> int:
    """
    Generate MAX(existing positive operational Roll Number) + 1
    inside the same transaction used for the student update. Released
    negative sentinel values are ignored.
    """
    cursor.execute(
        """
        SELECT COALESCE(
            MAX(roll_number),
            0
        ) AS highest_roll_number
        FROM students
        WHERE class_id = ?
          AND roll_number > 0
          AND student_id != ?
        """,
        (
            int(
                class_id
            ),
            int(
                student_id
            ),
        ),
    )

    row = cursor.fetchone()

    return (
        int(
            row[
                "highest_roll_number"
            ]
            or 0
        )
        + 1
    )


def _gender_lane_sections(
    normalized_gender: str,
):
    if normalized_gender == "MALE":
        return MALE_CLASS_SECTIONS

    if normalized_gender == "FEMALE":
        return FEMALE_CLASS_SECTIONS

    return frozenset()


def _gender_display_label(
    normalized_gender: str,
) -> str:
    if normalized_gender == "MALE":
        return "Male"

    if normalized_gender == "FEMALE":
        return "Female"

    return "student"


def _resolve_student_gender_section_route_with_cursor(
    cursor,
    *,
    student_id: int,
    selected_class_id: int,
    gender,
    student_status,
    existing_class_id: int,
    existing_gender,
    existing_student_status,
    existing_roll_number: int,
    existing_released_roll_number=None,
):
    """
    Resolve the actual class/section for an Edit Student operation.

    Class 4 and above:
    - A-F are Male sections.
    - G-Z are Female sections.
    - A compatible selected section is retained when it has capacity.
    - The student's compatible current section is retained when possible.
    - Otherwise the first alphabetical compatible existing section with
      a free seat is selected.
    - No class is created automatically.
    """
    requested_class_id = int(
        selected_class_id
    )

    original_requested_class_id = (
        requested_class_id
    )

    old_class_id = int(
        existing_class_id
    )

    requested_class, requested_gender_rule = (
        _load_class_gender_route_with_cursor(
            cursor,
            requested_class_id,
        )
    )

    original_requested_class = (
        requested_class
    )

    old_class, old_gender_rule = (
        _load_class_gender_route_with_cursor(
            cursor,
            old_class_id,
        )
    )

    if (
        requested_class_id != old_class_id
        and not bool(requested_class["is_active"])
    ):
        raise ValueError(
            "The destination class is Inactive. Reactivate it in Class Settings first."
        )

    normalized_status = str(
        student_status
        or ""
    ).strip().upper()

    normalized_active_status = str(
        STATUS_ACTIVE
    ).strip().upper()

    normalized_existing_status = str(
        existing_student_status
        or ""
    ).strip().upper()

    completion_reactivation_statuses = {
        str(STATUS_GRADUATED).strip().upper(),
        str(STATUS_PARTIALLY_GRADUATED).strip().upper(),
    }

    class10_completion_reactivation = (
        normalized_status == normalized_active_status
        and normalized_existing_status
        in completion_reactivation_statuses
    )

    # Graduated and Partially Graduated students follow the same
    # reactivation class-selection rules as Dropped students. The user may
    # select any existing class; capacity, gender routing, prior-study
    # history, roll-number and fee-resumption rules are applied below.

    normalized_gender = (
        _normalize_assignment_gender(
            gender
        )
    )

    requested_level_number = (
        _numeric_class_level(
            requested_class[
                "class_level"
            ]
        )
    )

    requested_class_details = (
        _class_capacity_snapshot_with_cursor(
            cursor,
            requested_class_id,
        )
    )

    resolved_class = requested_class
    resolved_class_details = (
        requested_class_details
    )

    route_reason = (
        "COMPLETION_REACTIVATION_SELECTED_CLASS"
        if class10_completion_reactivation
        else "SELECTED_CLASS"
    )

    # Historical Dropped or Graduated records are not forced into
    # gender-separated sections until they are reactivated.
    if normalized_status != normalized_active_status:
        pass

    # Classes below Class 4 remain co-education.
    elif (
        requested_level_number is None
        or requested_level_number < 4
    ):
        if not _class_can_accept_active_student(
            requested_class_details,
            candidate_class_id=requested_class_id,
            existing_class_id=old_class_id,
            existing_student_status=(
                existing_student_status
            ),
        ):
            raise ValueError(
                (
                    f"{requested_class['class_name']} has no "
                    "available seat. Increase its capacity or "
                    "select another class."
                )
            )

    # Preserve the earlier compatibility rule for direct backend
    # calls that intentionally omit an existing legacy Gender.
    elif not normalized_gender:
        _validate_class_4_plus_gender_assignment_with_cursor(
            cursor,
            class_id=requested_class_id,
            gender=gender,
            student_status=student_status,
            existing_class_id=old_class_id,
            existing_gender=existing_gender,
            allow_legacy_blank_route=True,
        )

        if not _class_can_accept_active_student(
            requested_class_details,
            candidate_class_id=requested_class_id,
            existing_class_id=old_class_id,
            existing_student_status=(
                existing_student_status
            ),
        ):
            raise ValueError(
                (
                    f"{requested_class['class_name']} has no "
                    "available seat. Set Gender and select an "
                    "appropriate section with capacity."
                )
            )

    else:
        if normalized_gender not in {
            "MALE",
            "FEMALE",
        }:
            raise ValueError(
                "Gender must be Male or Female."
            )

        required_sections = (
            _gender_lane_sections(
                normalized_gender
            )
        )

        gender_label = (
            _gender_display_label(
                normalized_gender
            )
        )

        target_level = str(
            requested_class[
                "class_level"
            ]
        ).strip().upper()

        requested_is_compatible = (
            requested_gender_rule
            == normalized_gender
        )

        requested_has_capacity = (
            requested_is_compatible
            and _class_can_accept_active_student(
                requested_class_details,
                candidate_class_id=(
                    requested_class_id
                ),
                existing_class_id=old_class_id,
                existing_student_status=(
                    existing_student_status
                ),
            )
        )

        if requested_has_capacity:
            resolved_class = (
                requested_class
            )

            resolved_class_details = (
                requested_class_details
            )

        else:
            old_level = str(
                old_class[
                    "class_level"
                ]
            ).strip().upper()

            current_class_can_be_retained = (
                old_level == target_level
                and old_gender_rule
                == normalized_gender
            )

            if current_class_can_be_retained:
                old_class_details = (
                    _class_capacity_snapshot_with_cursor(
                        cursor,
                        old_class_id,
                    )
                )

                if _class_can_accept_active_student(
                    old_class_details,
                    candidate_class_id=(
                        old_class_id
                    ),
                    existing_class_id=(
                        old_class_id
                    ),
                    existing_student_status=(
                        existing_student_status
                    ),
                ):
                    resolved_class = (
                        old_class
                    )

                    resolved_class_details = (
                        old_class_details
                    )

                    route_reason = (
                        "CURRENT_COMPATIBLE_CLASS"
                    )

                    current_class_can_be_retained = True

                else:
                    current_class_can_be_retained = False

            if (
                not current_class_can_be_retained
                or int(
                    resolved_class[
                        "id"
                    ]
                )
                == requested_class_id
            ):
                cursor.execute(
                    """
                    SELECT
                        c.*,
                        COUNT(
                            CASE
                                WHEN s.student_status = ?
                                THEN 1
                            END
                        ) AS active_students
                    FROM classes AS c
                    LEFT JOIN students AS s
                      ON s.class_id = c.id
                    WHERE UPPER(
                        TRIM(
                            c.class_level
                        )
                    ) = ?
                      AND c.is_active = 1
                    GROUP BY c.id
                    ORDER BY
                        UPPER(
                            TRIM(
                                c.section
                            )
                        ) ASC
                    """,
                    (
                        STATUS_ACTIVE,
                        target_level,
                    ),
                )

                candidate_rows = (
                    cursor.fetchall()
                )

                compatible_classes_exist = False
                selected_candidate = None
                selected_candidate_details = None

                for candidate_row in candidate_rows:
                    candidate = dict(
                        candidate_row
                    )

                    section = str(
                        candidate[
                            "section"
                        ]
                        or ""
                    ).strip().upper()

                    if (
                        len(section) != 1
                        or section
                        not in required_sections
                    ):
                        continue

                    compatible_classes_exist = True

                    capacity = int(
                        candidate.get(
                            "student_capacity",
                            AUTO_ROUTE_DEFAULT_CAPACITY,
                        )
                        or AUTO_ROUTE_DEFAULT_CAPACITY
                    )

                    active_students = int(
                        candidate.get(
                            "active_students",
                            0,
                        )
                        or 0
                    )

                    candidate[
                        "student_capacity"
                    ] = capacity

                    candidate[
                        "active_students"
                    ] = active_students

                    candidate[
                        "available_seats"
                    ] = max(
                        0,
                        capacity - active_students,
                    )

                    if not _class_can_accept_active_student(
                        candidate,
                        candidate_class_id=int(
                            candidate[
                                "id"
                            ]
                        ),
                        existing_class_id=(
                            old_class_id
                        ),
                        existing_student_status=(
                            existing_student_status
                        ),
                    ):
                        continue

                    selected_candidate = (
                        candidate
                    )

                    selected_candidate_details = (
                        candidate
                    )

                    break

                if selected_candidate is None:
                    if not compatible_classes_exist:
                        section_range = (
                            "A-F"
                            if normalized_gender == "MALE"
                            else "G-Z"
                        )

                        raise ValueError(
                            (
                                f"No existing {gender_label} section "
                                f"was found for Class {target_level}. "
                                f"Create a section in the "
                                f"{section_range} range in Class "
                                "Management first."
                            )
                        )

                    raise ValueError(
                        (
                            f"All existing {gender_label} sections "
                            f"for Class {target_level} are full. "
                            "Increase capacity or create another "
                            "compatible section."
                        )
                    )

                resolved_class = (
                    selected_candidate
                )

                resolved_class_details = (
                    selected_candidate_details
                )

                if requested_is_compatible:
                    route_reason = (
                        "SELECTED_SECTION_FULL"
                    )

                else:
                    route_reason = (
                        "GENDER_SECTION_MISMATCH"
                    )

        _validate_class_4_plus_gender_assignment_with_cursor(
            cursor,
            class_id=int(
                resolved_class[
                    "id"
                ]
            ),
            gender=gender,
            student_status=student_status,
            existing_class_id=old_class_id,
            existing_gender=existing_gender,
            allow_legacy_blank_route=False,
        )

    resolved_class_id = int(
        resolved_class[
            "id"
        ]
    )

    class_changed = (
        resolved_class_id
        != old_class_id
    )

    auto_routed = (
        resolved_class_id
        != original_requested_class_id
    )

    reactivating_to_active = (
        normalized_status == normalized_active_status
        and normalized_existing_status != normalized_active_status
    )

    fresh_roll_required = (
        class_changed
        or reactivating_to_active
    )

    if fresh_roll_required:
        resolved_roll_number = (
            _next_transfer_roll_with_cursor(
                cursor,
                class_id=resolved_class_id,
                student_id=student_id,
            )
        )

    else:
        resolved_roll_number = int(
            existing_roll_number
        )

    currently_occupies_resolved_class = (
        resolved_class_id
        == old_class_id
        and str(
            existing_student_status
            or ""
        ).strip().upper()
        == normalized_active_status
    )

    additional_active_students = (
        0
        if currently_occupies_resolved_class
        else (
            1
            if normalized_status
            == normalized_active_status
            else 0
        )
    )

    resulting_strength = (
        int(
            resolved_class_details[
                "active_students"
            ]
        )
        + additional_active_students
    )

    return {
        "student_id": int(
            student_id
        ),
        "requested_class_id": (
            original_requested_class_id
        ),
        "requested_class_name": (
            original_requested_class[
                "class_name"
            ]
        ),
        "requested_class_level": (
            original_requested_class[
                "class_level"
            ]
        ),
        "class10_completion_reactivation": (
            class10_completion_reactivation
        ),
        "resolved_class_id": (
            resolved_class_id
        ),
        "resolved_class_name": (
            resolved_class[
                "class_name"
            ]
        ),
        "resolved_class_level": (
            resolved_class[
                "class_level"
            ]
        ),
        "resolved_section": (
            resolved_class[
                "section"
            ]
        ),
        "resolved_roll_number": (
            resolved_roll_number
        ),
        "gender": (
            normalized_gender.title()
            if normalized_gender
            else ""
        ),
        "student_status": (
            student_status
        ),
        "class_changed": (
            class_changed
        ),
        "auto_routed": (
            auto_routed
        ),
        "route_reason": (
            route_reason
        ),
        "student_capacity": int(
            resolved_class_details[
                "student_capacity"
            ]
        ),
        "active_students_before": int(
            resolved_class_details[
                "active_students"
            ]
        ),
        "resulting_active_strength": (
            resulting_strength
        ),
        "available_seats_after": max(
            0,
            int(
                resolved_class_details[
                    "student_capacity"
                ]
            )
            - resulting_strength,
        ),
    }


def preview_student_gender_section_route(
    student_id: int,
    selected_class_id: int,
    gender,
    student_status=STATUS_ACTIVE,
):
    """
    Read-only preview of the actual class, section and Roll Number
    that Edit Student will use.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT
                student_id,
                class_id,
                roll_number,
                released_roll_number,
                gender,
                student_status
            FROM students
            WHERE student_id = ?
            """,
            (
                int(
                    student_id
                ),
            ),
        )

        existing_student = (
            cursor.fetchone()
        )

        if existing_student is None:
            raise ValueError(
                f"Student not found: {student_id}"
            )

        return (
            _resolve_student_gender_section_route_with_cursor(
                cursor,
                student_id=int(
                    student_id
                ),
                selected_class_id=int(
                    selected_class_id
                ),
                gender=gender,
                student_status=student_status,
                existing_class_id=int(
                    existing_student[
                        "class_id"
                    ]
                ),
                existing_gender=(
                    existing_student[
                        "gender"
                    ]
                ),
                existing_student_status=(
                    existing_student[
                        "student_status"
                    ]
                ),
                existing_roll_number=int(
                    existing_student[
                        "roll_number"
                    ]
                ),
                existing_released_roll_number=(
                    existing_student[
                        "released_roll_number"
                    ]
                ),
            )
        )

    finally:
        connection.close()

def validate_class_4_plus_gender_assignment(
    class_id: int,
    gender,
    student_status=STATUS_ACTIVE,
) -> bool:
    """
    Public strict validation for UI previews and future workflows.

    Existing-legacy compatibility is intentionally handled only
    inside update_student(), where the old class and old gender are
    available.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        _validate_class_4_plus_gender_assignment_with_cursor(
            cursor,
            class_id=class_id,
            gender=gender,
            student_status=student_status,
            allow_legacy_blank_route=False,
        )

        return True

    finally:
        connection.close()

# IMMUTABLE_ORIGINAL_ADMISSION_AND_ATOMIC_CAPACITY_V12
# Admission Date is created once from the system clock. Activation and
# reactivation are separate status/class-history events.
IMMUTABLE_ORIGINAL_ADMISSION_AND_ATOMIC_CAPACITY_V12 = True
SYSTEM_CLOCK_ADMISSION_DATE_LOCK_V25 = True


def add_student(
    admission_number: str,
    roll_number: int,
    class_id: int,
    student_name: str,
    father_name: str,
    father_cnic: str,
    father_contact_number: str,
    school_leaving_certificate_submitted: bool,
    admission_year: int,
    admission_month: int,
    admission_day: int,
    admission_time: str = "",
    gender: str = "",
    whatsapp_number: str = "",
    current_address: str = "",
    permanent_address: str = "",
    date_of_birth: str = "",
    form_b_submitted: bool = False,
    birth_certificate_submitted: bool = False,
    created_by: str = "admin",
    coming_session_admission: bool = False,
    use_system_admission_datetime: bool = False,
) -> int:
    """
    Insert a new student record. Student and father names are stored in
    uppercase. Returns the new student_id.

    The Add Student screen sets ``use_system_admission_datetime`` so the
    permanent admission timestamp is taken directly from the computer clock,
    regardless of any caller-supplied date components. The default remains
    available only for existing internal regression fixtures and historical
    import-compatible service calls.
    """
    if coming_session_admission:
        roll_number = -1
    else:
        try:
            roll_number = int(roll_number)
        except (TypeError, ValueError) as error:
            raise ValueError("Roll Number must be a positive integer.") from error

        if roll_number <= 0:
            raise ValueError("Active students must have a positive Roll Number.")

    normalized_admission_number = normalize_admission_number_search(
        admission_number
    )
    if not normalized_admission_number:
        raise ValueError("Admission Number cannot be blank.")

    normalized_student_name = validate_person_name_length(student_name)
    normalized_father_name = validate_person_name_length(father_name)
    normalized_father_cnic = normalize_father_cnic(father_cnic)

    current_time = datetime.now()

    if use_system_admission_datetime:
        admission_year = current_time.year
        admission_month = current_time.month
        admission_day = current_time.day
        cleaned_admission_time = current_time.strftime("%H:%M:%S")
    else:
        try:
            admission_year = int(admission_year)
            admission_month = int(admission_month)
            admission_day = int(admission_day)
        except (TypeError, ValueError) as error:
            raise ValueError("Admission Date must be a valid calendar date.") from error

        cleaned_admission_time = str(admission_time or "").strip()
        if not cleaned_admission_time:
            cleaned_admission_time = current_time.strftime("%H:%M:%S")
        else:
            parsed_time = None
            for time_format in ("%H:%M:%S", "%H:%M"):
                try:
                    parsed_time = datetime.strptime(cleaned_admission_time, time_format)
                    break
                except ValueError:
                    continue
            if parsed_time is None:
                raise ValueError("Admission Time must use HH:MM or HH:MM:SS.")
            cleaned_admission_time = parsed_time.strftime("%H:%M:%S")

    if not validate_admission_date(
        admission_year, admission_month, admission_day
    ):
        raise ValueError("Admission Date must be a valid calendar date.")

    admitted_at = datetime(
        admission_year, admission_month, admission_day,
        *[int(part) for part in cleaned_admission_time.split(":")],
    )
    if admitted_at > current_time:
        raise ValueError("Admission Date cannot be in the future.")

    target_session_start_year = None
    coming_status = None
    stored_student_status = STATUS_ACTIVE
    if coming_session_admission:
        from app.services import coming_session_service
        if not coming_session_service.is_coming_session_option_available(
            admission_year, admission_month, admission_day,
            today=current_time.date(),
        ):
            raise ValueError(
                "Admission for Coming Session is available only for a January, "
                "February or March admission date before 1 April."
            )
        target_session_start_year = (
            coming_session_service.target_session_start_year(
                admission_year, admission_month
            )
        )
        coming_status = coming_session_service.COMING_STATUS_PENDING
        stored_student_status = STATUS_COMING_SESSION

    normalized_date_of_birth = validate_date_of_birth_before_admission(
        date_of_birth,
        admitted_at,
    )

    admission_date = admitted_at.strftime("%Y-%m-%d %H:%M:%S")
    now = current_time.strftime("%Y-%m-%d %H:%M:%S")

    connection = get_connection()
    cursor = connection.cursor()
    try:
        # BEGIN IMMEDIATE makes the Active-strength check and INSERT one
        # atomic operation. A second admission cannot cross the capacity
        # between the preflight and the final student INSERT.
        connection.execute("BEGIN IMMEDIATE")

        fixture_mode = bool(cursor.execute("SELECT HMS_TEST_FIXTURE_MODE()").fetchone()[0])
        admission_sequence = None
        if not fixture_mode:
            _admission_prefix, admission_sequence = parse_generated_admission_number(
                normalized_admission_number
            )
            expected_sequence = _next_admission_sequence_with_cursor(cursor)
            if admission_sequence != expected_sequence:
                raise ValueError(
                    "This Admission Number is no longer the next available number. "
                    "Return to Add Student and click Generate again."
                )

        # Protect every current or future service caller, not only the UI.
        # This check is inside BEGIN IMMEDIATE, so check and INSERT are atomic.
        cursor.execute(
            "SELECT 1 FROM students "
            "WHERE TRIM(admission_number) = ? COLLATE NOCASE LIMIT 1",
            (normalized_admission_number,),
        )
        if cursor.fetchone() is not None:
            raise ValueError(
                "This Admission Number is already used by another student."
            )
        if admission_sequence is not None:
            cursor.execute(
                """
                SELECT 1 FROM students
                WHERE LENGTH(UPPER(TRIM(admission_number))) IN (5,6)
                  AND SUBSTR(UPPER(TRIM(admission_number)),1,1) IN ('H','P')
                  AND SUBSTR(UPPER(TRIM(admission_number)),2)<>''
                  AND SUBSTR(UPPER(TRIM(admission_number)),2) NOT GLOB '*[^0-9]*'
                  AND CAST(SUBSTR(UPPER(TRIM(admission_number)),2) AS INTEGER)=?
                LIMIT 1
                """,
                (int(admission_sequence),),
            )
            if cursor.fetchone() is not None:
                raise ValueError(
                    "The numeric part of this Admission Number has already been used. "
                    "Click Generate again."
                )

        duplicate_student = _find_existing_student_identity_with_cursor(
            cursor,
            student_name=normalized_student_name,
            father_name=normalized_father_name,
            father_cnic=normalized_father_cnic,
        )
        if duplicate_student is not None:
            raise ValueError(
                "Student Record Already Exists. A student with the same "
                "Student Name, Father Name and Father CNIC already exists."
            )

        if not coming_session_admission:
            cursor.execute(
                "SELECT 1 FROM students "
                "WHERE class_id = ? AND roll_number = ? LIMIT 1",
                (int(class_id), int(roll_number)),
            )

            if cursor.fetchone() is not None:
                raise ValueError(
                    f"Roll number {int(roll_number)} is already assigned "
                    "in the selected class. No student was added."
                )

        capacity_details = _class_capacity_snapshot_with_cursor(
            cursor,
            class_id=int(class_id),
        )

        pending_count = int(cursor.execute(
            "SELECT COUNT(*) FROM students WHERE class_id=? AND student_status='Coming Session'",
            (int(class_id),),
        ).fetchone()[0])
        reserved_strength = int(capacity_details["active_students"]) + pending_count
        if reserved_strength >= int(capacity_details["student_capacity"]):
            raise ValueError(
                (
                    f"{capacity_details['class_name']} is full for the current/coming session. "
                    f"Capacity: {capacity_details['student_capacity']}; "
                    f"Active and reserved students: {reserved_strength}. "
                    "Increase the class capacity or select another class. "
                    "No student was added."
                )
            )

        _validate_class_4_plus_gender_assignment_with_cursor(
            cursor,
            class_id=class_id,
            gender=gender,
            student_status=stored_student_status,
            allow_legacy_blank_route=False,
        )

        if coming_session_admission:
            sequence_row = cursor.execute(
                "SELECT COALESCE(seq,0) FROM sqlite_sequence WHERE name='students'"
            ).fetchone()
            sequence_value = int(sequence_row[0]) if sequence_row else 0
            maximum_id = int(cursor.execute(
                "SELECT COALESCE(MAX(student_id),0) FROM students"
            ).fetchone()[0])
            expected_student_id = max(sequence_value, maximum_id) + 1
            roll_number = -expected_student_id

        cursor.execute(
            """
            INSERT INTO students (
                admission_number,
                roll_number,
                class_id,
                student_name,
                gender,
                father_name,
                father_cnic,
                father_contact_number,
                whatsapp_number,
                current_address,
                permanent_address,
                school_leaving_certificate_submitted,
                form_b_submitted,
                birth_certificate_submitted,
                date_of_birth,
                admission_date,
                admission_year,
                admission_month,
                admission_day,
                admission_time,
                student_status,
                target_session_start_year,
                coming_session_status,
                coming_session_effective_date,
                coming_session_activated_at,
                failure_count,
                created_at,
                created_by,
                updated_at,
                updated_by
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                normalized_admission_number,
                roll_number,
                class_id,
                normalized_student_name,
                gender.strip().title(),
                normalized_father_name,
                normalized_father_cnic,
                father_contact_number,
                (
                    whatsapp_number.strip()
                    if whatsapp_number
                    else None
                ),
                (
                    current_address.strip()
                    if current_address
                    else None
                ),
                (
                    permanent_address.strip()
                    if permanent_address
                    else None
                ),
                (
                    1
                    if school_leaving_certificate_submitted
                    else 0
                ),
                (
                    1
                    if form_b_submitted
                    else 0
                ),
                (
                    1
                    if birth_certificate_submitted
                    else 0
                ),
                normalized_date_of_birth,
                admission_date,
                admission_year,
                admission_month,
                admission_day,
                cleaned_admission_time,
                stored_student_status,
                target_session_start_year,
                coming_status,
                None,
                None,
                0,
                now,
                created_by,
                now,
                created_by,
            ),
        )
        new_student_id = cursor.lastrowid
        if coming_session_admission:
            expected_sentinel = -int(new_student_id)
            if int(roll_number) != expected_sentinel:
                cursor.execute(
                    "UPDATE students SET roll_number=? WHERE student_id=?",
                    (expected_sentinel, int(new_student_id)),
                )

        # Assign the immutable canonical Tracking ID explicitly inside the
        # admission transaction. The database trigger remains only as a
        # secondary defence and is not the authoritative creation path.
        new_tracking_id = canonicalize_tracking_id(
            None,
            fallback_number=int(new_student_id),
        )
        cursor.execute(
            "UPDATE students SET tracking_id=? WHERE student_id=?",
            (new_tracking_id, int(new_student_id)),
        )
        verified_tracking = cursor.execute(
            "SELECT tracking_id FROM students WHERE student_id=?",
            (int(new_student_id),),
        ).fetchone()
        if (
            verified_tracking is None
            or str(verified_tracking["tracking_id"] or "").strip().upper()
            != new_tracking_id
        ):
            raise RuntimeError(
                "The student Tracking ID could not be assigned safely. No admission was saved."
            )

        cursor.execute(
            """
            INSERT INTO student_class_history (
                student_id,
                old_class_id,
                new_class_id,
                effective_month,
                effective_year,
                change_type,
                change_datetime,
                changed_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_student_id,
                None,
                class_id,
                admission_month,
                admission_year,
                "ADMISSION",
                now,
                created_by,
            ),
        )

        if coming_session_admission:
            cursor.execute(
                """
                INSERT INTO student_status_history(
                    student_id,old_status,new_status,event_type,effective_datetime,
                    reason,changed_by,created_at,related_class_id,related_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    int(new_student_id), STATUS_ACTIVE, STATUS_COMING_SESSION,
                    "ADMISSION_FOR_COMING_SESSION", now,
                    "Student admitted early for the coming academic session.",
                    str(created_by or "admin"), now, int(class_id),
                ),
            )

        from app.services import sibling_fee_service

        sibling_fee_service.recalculate_sibling_fees_after_student_change(
            student_id=int(new_student_id),
            old_father_cnic=None,
            new_father_cnic=normalized_father_cnic,
            old_father_name=None,
            new_father_name=normalized_father_name,
            effective_month=int(admission_month),
            effective_year=int(admission_year),
            connection=connection,
            changed_by=str(created_by or "admin"),
        )

        connection.commit()
        return new_student_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



# ARCHIVED_DROPPED_STUDENT_LIFECYCLE_V10
# A Dropped student is archived after both conditions are true:
# 1. a later April-to-March session has started; and
# 2. no unpaid Monthly Fee remains.
# Archived students remain searchable only in Student Management.
ARCHIVED_DROPPED_STUDENT_LIFECYCLE_V10 = True


def _academic_session_start_year(month_number: int, year: int) -> int:
    month_number = int(month_number)
    year = int(year)
    return year if month_number >= 4 else year - 1


def _archive_drop_boundary(
    cursor,
    student_id: int,
    class_id: int,
    reference,
):
    """
    Return the best durable drop boundary available.

    Current transitions write a MANUAL same-class row with old_class_id NULL.
    Legacy records fall back to the latest fee period, then updated_at.
    """
    cursor.execute(
        """
        SELECT effective_datetime
        FROM student_status_history
        WHERE student_id = ?
          AND event_type = 'DROP'
          AND new_status = ?
          AND related_class_id = ?
        ORDER BY effective_datetime DESC, id DESC
        LIMIT 1
        """,
        (int(student_id), STATUS_DROPPED, int(class_id)),
    )
    row = cursor.fetchone()
    if row:
        try:
            dropped_at = datetime.fromisoformat(str(row["effective_datetime"]))
            return dropped_at.month, dropped_at.year
        except ValueError:
            pass

    # Compatibility fallback for safely identifiable legacy Drop rows.
    cursor.execute(
        """
        SELECT effective_month, effective_year
        FROM student_class_history
        WHERE student_id = ?
          AND old_class_id IS NULL
          AND new_class_id = ?
          AND change_type = 'MANUAL'
        ORDER BY effective_year DESC, effective_month DESC, history_id DESC
        LIMIT 1
        """,
        (int(student_id), int(class_id)),
    )
    row = cursor.fetchone()
    if row:
        return int(row["effective_month"]), int(row["effective_year"])

    cursor.execute(
        """
        SELECT month_number, year
        FROM student_fee_records
        WHERE student_id = ?
          AND fee_type = 'Monthly'
        ORDER BY year DESC, month_number DESC, id DESC
        LIMIT 1
        """,
        (int(student_id),),
    )

    row = cursor.fetchone()

    if row:
        return (
            int(row["month_number"]),
            int(row["year"]),
        )

    cursor.execute(
        """
        SELECT updated_at
        FROM students
        WHERE student_id = ?
        """,
        (int(student_id),),
    )

    row = cursor.fetchone()
    updated_at = str(
        row["updated_at"]
        if row
        else ""
    ).strip()

    try:
        updated = datetime.fromisoformat(updated_at)
        return updated.month, updated.year
    except ValueError:
        return int(reference.month), int(reference.year)


def archive_eligible_dropped_students(
    as_of=None,
    student_ids=None,
    connection=None,
    tolerate_missing_fee_configuration: bool = False,
) -> dict:
    """
    Archive cleared Dropped students after a later session starts.

    Before the next April, a cleared student remains Dropped. If old dues
    remain after April, the student stays Dropped and remains payable. When
    the final due is paid in that later session, archiving occurs atomically.

    Archived records are not deleted: identity, fee, receipt, class and
    promotion history remain intact for audit and Student Management Search.
    """
    reference = as_of or datetime.now()
    current_session_start = _academic_session_start_year(
        reference.month,
        reference.year,
    )

    normalized_ids = {
        int(student_id)
        for student_id in (student_ids or [])
        if student_id is not None
    }

    owns_connection = connection is None

    if owns_connection:
        connection = get_connection()

    cursor = connection.cursor()
    now = reference.strftime("%Y-%m-%d %H:%M:%S")

    try:
        if owns_connection:
            connection.execute("BEGIN IMMEDIATE")

        query = """
            SELECT student_id, class_id, father_name, father_cnic
            FROM students
            WHERE student_status = ?
        """
        parameters = [STATUS_DROPPED]

        if normalized_ids:
            placeholders = ",".join(
                "?" for _ in normalized_ids
            )
            query += (
                f" AND student_id IN ({placeholders})"
            )
            parameters.extend(sorted(normalized_ids))

        query += " ORDER BY student_id ASC"

        cursor.execute(query, parameters)
        dropped_rows = cursor.fetchall()

        archived_ids = []
        blocked_ids = []
        configuration_blocked_ids = []
        configuration_errors = {}

        for row in dropped_rows:
            student_id = int(row["student_id"])
            cursor.execute(
                """
                SELECT student_id, class_id, student_status, father_name, father_cnic
                FROM students
                WHERE student_id = ?
                """,
                (student_id,),
            )
            current_student = cursor.fetchone()
            if (
                current_student is None
                or str(current_student["student_status"] or "").strip() != STATUS_DROPPED
            ):
                continue
            class_id = int(current_student["class_id"])

            drop_month, drop_year = _archive_drop_boundary(
                cursor=cursor,
                student_id=student_id,
                class_id=class_id,
                reference=reference,
            )

            drop_session_start = (
                _academic_session_start_year(
                    drop_month,
                    drop_year,
                )
            )

            if current_session_start <= drop_session_start:
                continue

            from app.services import fee_service

            if tolerate_missing_fee_configuration:
                savepoint_name = f"startup_archive_fee_{student_id}"
                cursor.execute(f"SAVEPOINT {savepoint_name}")

                try:
                    fee_service.ensure_fee_records_generated(
                        student_id=student_id,
                        connection=connection,
                        as_of=reference,
                    )

                except ValueError as error:
                    cursor.execute(
                        f"ROLLBACK TO SAVEPOINT {savepoint_name}"
                    )
                    cursor.execute(
                        f"RELEASE SAVEPOINT {savepoint_name}"
                    )

                    if str(error).startswith(
                        "Monthly Fee configuration is missing for "
                    ):
                        configuration_blocked_ids.append(student_id)
                        configuration_errors[student_id] = str(error)
                        continue

                    raise

                except Exception:
                    cursor.execute(
                        f"ROLLBACK TO SAVEPOINT {savepoint_name}"
                    )
                    cursor.execute(
                        f"RELEASE SAVEPOINT {savepoint_name}"
                    )
                    raise

                else:
                    cursor.execute(
                        f"RELEASE SAVEPOINT {savepoint_name}"
                    )

            else:
                fee_service.ensure_fee_records_generated(
                    student_id=student_id,
                    connection=connection,
                    as_of=reference,
                )

            cursor.execute(
                """
                SELECT 1
                FROM student_fee_records
                WHERE student_id = ?
                  AND fee_type = 'Monthly'
                  AND status = 'Unpaid'
                LIMIT 1
                """,
                (student_id,),
            )

            if cursor.fetchone() is not None:
                blocked_ids.append(student_id)
                if owns_connection and normalized_ids and len(normalized_ids) == 1:
                    raise ValueError(
                        "Student cannot be Archived because outstanding fee records exist."
                    )
                continue

            cursor.execute(
                """
                UPDATE students
                SET
                    student_status = ?,
                    updated_at = ?,
                    updated_by = ?
                WHERE student_id = ?
                  AND student_status = ?
                """,
                (
                    STATUS_ARCHIVED,
                    now,
                    "automatic_archive",
                    student_id,
                    STATUS_DROPPED,
                ),
            )

            if cursor.rowcount != 1:
                continue

            cursor.execute(
                """
                INSERT INTO student_status_history (
                    student_id, old_status, new_status, event_type, effective_datetime,
                    reason, changed_by, created_at, related_class_id
                ) VALUES (?, ?, ?, 'ARCHIVE', ?, ?, ?, ?, ?)
                """,
                (
                    student_id, STATUS_DROPPED, STATUS_ARCHIVED, now,
                    "All required dues were cleared after the inactive boundary.",
                    "automatic_archive", now, class_id,
                ),
            )

            from app.services import sibling_fee_service
            sibling_fee_service.recalculate_sibling_fees_after_student_change(
                student_id=student_id,
                old_father_name=current_student["father_name"],
                new_father_name=current_student["father_name"],
                old_father_cnic=current_student["father_cnic"],
                new_father_cnic=current_student["father_cnic"],
                effective_month=int(reference.month),
                effective_year=int(reference.year),
                connection=connection,
                changed_by="automatic_archive",
            )

            archived_ids.append(student_id)

        if owns_connection:
            connection.commit()

        return {
            "archived_count": len(archived_ids),
            "archived_student_ids": archived_ids,
            "blocked_student_ids": blocked_ids,
            "configuration_blocked_student_ids": configuration_blocked_ids,
            "configuration_errors": configuration_errors,
        }

    except Exception:
        if owns_connection:
            connection.rollback()
        raise

    finally:
        if owns_connection:
            connection.close()


# ---------------------------------------------------------------------------
# Read / Search
# ---------------------------------------------------------------------------

_STUDENT_CURRENT_STATUS_COLUMNS = """
    CASE
        WHEN s.student_status = 'Active' THEN COALESCE(
            (SELECT h.effective_datetime
             FROM student_status_history h
             WHERE h.student_id=s.student_id
               AND h.new_status='Active'
               AND h.event_type IN ('REACTIVATION', 'COMING_SESSION_ACTIVATION')
             ORDER BY h.effective_datetime DESC, h.id DESC LIMIT 1),
            s.admission_date
        )
        WHEN s.student_status = 'Dropped' THEN
            (SELECT h.effective_datetime FROM student_status_history h
             WHERE h.student_id=s.student_id AND h.event_type='DROP'
             ORDER BY h.effective_datetime DESC, h.id DESC LIMIT 1)
        WHEN s.student_status IN ('Graduated', 'Partially Graduated') THEN
            (SELECT g.graduation_date FROM graduation_records g
             WHERE g.student_id=s.student_id AND g.is_reversed=0
             ORDER BY g.graduation_date DESC, g.graduated_record_id DESC LIMIT 1)
        WHEN s.student_status = 'Archived' THEN
            (SELECT h.effective_datetime FROM student_status_history h
             WHERE h.student_id=s.student_id AND h.event_type='ARCHIVE'
             ORDER BY h.effective_datetime DESC, h.id DESC LIMIT 1)
        ELSE s.admission_date
    END AS current_status_date,
    CASE
        WHEN s.student_status = 'Active' THEN COALESCE(
            (SELECT h.event_type FROM student_status_history h
             WHERE h.student_id=s.student_id
               AND h.new_status='Active'
               AND h.event_type IN ('REACTIVATION', 'COMING_SESSION_ACTIVATION')
             ORDER BY h.effective_datetime DESC, h.id DESC LIMIT 1),
            'ADMISSION'
        )
        WHEN s.student_status = 'Dropped' THEN 'DROP'
        WHEN s.student_status IN ('Graduated', 'Partially Graduated') THEN 'GRADUATION'
        WHEN s.student_status = 'Archived' THEN 'ARCHIVE'
        ELSE 'ADMISSION'
    END AS current_status_event
"""


def _student_read_select() -> str:
    return f"s.*, c.class_name, {_STUDENT_CURRENT_STATUS_COLUMNS}"

def get_student_by_id(student_id: int):
    """Return the complete student record, or None if not found."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT {_student_read_select()}
            FROM students s
            LEFT JOIN classes c ON s.class_id = c.id
            WHERE s.student_id = ?
            """,
            (student_id,),
        )
        return cursor.fetchone()
    finally:
        connection.close()


def search_students(
    admission_number: str = None,
    student_name: str = None,
    father_name: str = None,
    father_cnic: str = None,
    class_id: int = None,
    roll_number: int = None,
    status: str = None,
    include_archived: bool = False,
):
    """
    Search students using any combination of admission number, roll number,
    student name, father name (all case-insensitive), class, and status.
    By default Archived records are excluded so other modules cannot surface
    them. Student Management Search passes include_archived=True and is the
    only normal screen allowed to display archived students.
    """
    query = f"""
        SELECT {_student_read_select()}
        FROM students s
        LEFT JOIN classes c ON s.class_id = c.id
        WHERE 1 = 1
    """
    params = []

    if not include_archived:
        query += " AND s.student_status <> ?"
        params.append(STATUS_ARCHIVED)

    if admission_number and str(admission_number).strip():
        normalized_admission_number = normalize_admission_number_search(
            admission_number
        )
        query += " AND TRIM(s.admission_number) = ? COLLATE NOCASE"
        params.append(normalized_admission_number)

    if roll_number is not None and str(roll_number).strip():
        displayed_roll, released_only = parse_roll_number_search(roll_number)
        if released_only:
            query += " AND s.released_roll_number = ?"
            params.append(displayed_roll)
        else:
            query += " AND (s.roll_number = ? OR s.released_roll_number = ?)"
            params.extend((displayed_roll, displayed_roll))

    if student_name and str(student_name).strip():
        query += " AND INSTR(NORMALIZE_PERSON_NAME(s.student_name), ?) > 0"
        params.append(normalize_person_name(student_name))

    if father_name and str(father_name).strip():
        query += " AND INSTR(NORMALIZE_PERSON_NAME(s.father_name), ?) > 0"
        params.append(normalize_person_name(father_name))

    if father_cnic:
        normalized_father_cnic = "".join(
            character
            for character in father_cnic
            if character.isdigit()
        )

        query += """
            AND REPLACE(
                REPLACE(
                    COALESCE(
                        s.father_cnic,
                        ''
                    ),
                    '-',
                    ''
                ),
                ' ',
                ''
            ) = ?
        """

        params.append(
            normalized_father_cnic
        )

    if class_id:
        query += " AND s.class_id = ?"
        params.append(class_id)

    if status and status != "All":
        query += " AND s.student_status = ?"
        params.append(status)

    query += " ORDER BY s.student_name ASC"

    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        connection.close()


def class_has_any_students(class_id: int) -> bool:
    """
    Check whether any student (of any status) is assigned to a class.
    Used by Class Management to block deletion of classes with students.
    """
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM students WHERE class_id = ?", (class_id,))
        return cursor.fetchone() is not None
    finally:
        connection.close()


def count_students_by_status(class_id: int, status: str) -> int:
    """Return the count of students in a class with a specific status."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS student_count FROM students WHERE class_id = ? AND student_status = ?",
            (class_id, status),
        )
        row = cursor.fetchone()
        return row["student_count"]
    finally:
        connection.close()


# ACTIVE_GENDER_AND_SCHOOL_STRENGTH_V10

def count_active_students_by_gender(
    class_id: int,
    gender: str,
) -> int:
    """
    Count only Active students of one gender inside one class section.

    Gender matching is case-insensitive so legacy values such as MALE,
    Male, and male are treated consistently.
    """
    normalized_gender = str(gender or "").strip().upper()

    if normalized_gender not in {"MALE", "FEMALE"}:
        raise ValueError(
            "Gender must be Male or Female."
        )

    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS student_count
            FROM students
            WHERE class_id = ?
              AND student_status = ?
              AND UPPER(TRIM(COALESCE(gender, ''))) = ?
            """,
            (
                int(class_id),
                STATUS_ACTIVE,
                normalized_gender,
            ),
        )

        row = cursor.fetchone()

        return int(
            row["student_count"]
            or 0
        )

    finally:
        connection.close()


def count_school_active_strength() -> int:
    """
    Return the complete school strength using Active students only.

    Dropped, Graduated, Partially Graduated, Archived, and every other
    non-Active status are excluded.
    """
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS student_count
            FROM students
            WHERE student_status = ?
            """,
            (
                STATUS_ACTIVE,
            ),
        )

        row = cursor.fetchone()

        return int(
            row["student_count"]
            or 0
        )

    finally:
        connection.close()


def available_admission_session_start_years() -> list[int]:
    """Return sessions that contain admissions, including the current session."""
    now = datetime.now()
    current_start_year = _academic_session_start_year(
        now.month,
        now.year,
    )
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT DISTINCT
                CASE
                    WHEN target_session_start_year IS NOT NULL
                        THEN target_session_start_year
                    WHEN admission_month >= 4 THEN admission_year
                    ELSE admission_year - 1
                END AS session_start_year
            FROM students
            WHERE admission_year IS NOT NULL
              AND admission_month IS NOT NULL
            """
        )
        session_years = {
            int(row["session_start_year"])
            for row in cursor.fetchall()
            if row["session_start_year"] is not None
        }
        session_years.add(current_start_year)
        return sorted(session_years, reverse=True)
    finally:
        connection.close()


def current_academic_session_start_year() -> int:
    """Return the start year of the current April-to-March session."""
    now = datetime.now()
    return _academic_session_start_year(now.month, now.year)


def count_admissions_for_session(session_start_year: int) -> int:
    """Count students admitted into one April-to-March academic session."""
    connection = get_connection()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT COUNT(*) AS admission_count
            FROM students
            WHERE CASE
                WHEN target_session_start_year IS NOT NULL
                    THEN target_session_start_year
                WHEN admission_month >= 4 THEN admission_year
                ELSE admission_year - 1
            END = ?
            """,
            (int(session_start_year),),
        )
        row = cursor.fetchone()
        return int(row["admission_count"] or 0)
    finally:
        connection.close()


def load_students_by_status(class_id: int, status: str):
    """
    Return every student in a given class with a given status
    (Active or Dropped), ordered by roll number. Used by the Class
    Management search result screen to populate its student table
    and PDF export.
    """
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT * FROM students
            WHERE class_id = ? AND student_status = ?
            ORDER BY
                COALESCE(released_roll_number, roll_number) ASC,
                student_id ASC
            """,
            (class_id, status),
        )
        return cursor.fetchall()
    finally:
        connection.close()
# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

def _validate_manual_class_change_chronology_with_cursor(
    cursor,
    student_id: int,
    current_class_id: int,
    admission_month: int,
    admission_year: int,
    effective_month: int,
    effective_year: int,
) -> dict:
    """Enforce chronological, continuous, append-only manual class history."""
    effective_period = (int(effective_year), int(effective_month))
    admission_period = (int(admission_year), int(admission_month))
    if effective_period < admission_period:
        raise ValueError(
            "Class-change effective month cannot be earlier than the Admission Month."
        )

    cursor.execute(
        """
        SELECT history_id, new_class_id, effective_month, effective_year
        FROM student_class_history
        WHERE student_id = ?
        ORDER BY effective_year DESC, effective_month DESC, history_id DESC
        LIMIT 1
        """,
        (int(student_id),),
    )
    latest_row = cursor.fetchone()
    if latest_row is None:
        return {
            "latest_period": admission_period,
            "latest_class_id": int(current_class_id),
        }

    latest_period = (
        int(latest_row["effective_year"]),
        int(latest_row["effective_month"]),
    )
    if effective_period < latest_period:
        raise ValueError(
            "Class-change effective month cannot be earlier than the latest "
            "existing lifecycle or class-change month."
        )

    latest_class_id = latest_row["new_class_id"]
    if latest_class_id is not None and int(latest_class_id) != int(current_class_id):
        raise ValueError(
            "Class-change history is not continuous: the latest recorded class "
            "does not match the student's current class."
        )

    return {
        "latest_period": latest_period,
        "latest_class_id": int(current_class_id),
    }


def validate_manual_class_change_period(
    student_id: int,
    current_class_id: int,
    effective_month: int,
    effective_year: int,
) -> bool:
    """Read-only UI preflight for the authoritative transaction validation."""
    connection = get_connection()
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT admission_month, admission_year FROM students WHERE student_id = ?",
            (int(student_id),),
        )
        student_row = cursor.fetchone()
        if student_row is None:
            raise ValueError("Student not found.")
        _validate_manual_class_change_chronology_with_cursor(
            cursor=cursor,
            student_id=int(student_id),
            current_class_id=int(current_class_id),
            admission_month=int(student_row["admission_month"]),
            admission_year=int(student_row["admission_year"]),
            effective_month=int(effective_month),
            effective_year=int(effective_year),
        )
        return True
    finally:
        connection.close()


def update_student(
    student_id: int,
    roll_number: int,
    class_id: int,
    student_name: str,
    father_name: str,
    father_cnic: str,
    father_contact_number: str,
    student_status: str,
    school_leaving_certificate_submitted: bool,
    admission_year: int,
    admission_month: int,
    admission_day: int,
    admission_time: str,
    effective_month: int = None,
    effective_year: int = None,
    updated_by: str = "admin",
    admission_number: str = None,
    gender: str = None,
    whatsapp_number: str = None,
    current_address: str = None,
    permanent_address: str = None,
    date_of_birth: str = None,
    form_b_submitted: bool = None,
    birth_certificate_submitted: bool = None,
) -> None:
    """
    Update an existing student.

    Manual class-change rules:

    1. Paid fee records and earlier sessions are protected.

    2. Every unpaid monthly fee in the selected academic
       session follows the student's newly selected class.

    3. Forward movement after an earlier failure:
       - reset failure_count;
       - create or correct one destination-class admission fee.

    4. Backward movement to a class previously studied:
       - increase failure_count by one;
       - apply repeat/failure rules;
       - remove any unpaid admission fee for that session.

    5. Backward correction to a class never studied before:
       - do not increase failure_count;
       - correct an existing unpaid admission fee;
       - preserve a paid admission fee.
    """
    if student_status not in ALL_STUDENT_STATUSES:
        raise ValueError(
            f"Invalid student status: {student_status}"
        )

    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # Admission-date arguments remain in the public signature for UI and
    # compatibility, but an existing student's original Admission Date is
    # immutable and is reloaded from the master record below.
    del admission_year, admission_month, admission_day, admission_time

    connection = get_connection()
    cursor = connection.cursor()

    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            SELECT
                class_id,
                roll_number,
                released_roll_number,
                student_status,
                failure_count,
                admission_number,
                tracking_id,
                student_name,
                father_name,
                gender,
                father_cnic,
                whatsapp_number,
                current_address,
                permanent_address,
                date_of_birth,
                form_b_submitted,
                birth_certificate_submitted,
                admission_date,
                admission_year,
                admission_month,
                admission_day,
                admission_time
            FROM students
            WHERE student_id = ?
            """,
            (student_id,),
        )

        existing_student = cursor.fetchone()

        if existing_student is None:
            raise ValueError(
                f"Student not found: {student_id}"
            )

        normalized_student_name = validate_person_name_length(student_name)
        normalized_father_name = validate_person_name_length(father_name)
        normalized_father_cnic = normalize_father_cnic(father_cnic)
        existing_father_cnic = normalize_father_cnic(
            existing_student["father_cnic"],
            required=False,
        )

        duplicate_student = _find_existing_student_identity_with_cursor(
            cursor,
            student_name=normalized_student_name,
            father_name=normalized_father_name,
            father_cnic=normalized_father_cnic,
            exclude_student_id=int(student_id),
        )
        if duplicate_student is not None:
            raise ValueError(
                "Another student record already uses this normalized identity. "
                "A student with the same Student Name, Father Name and "
                "Father CNIC already exists."
            )

        existing_status = str(
            existing_student["student_status"]
            or ""
        ).strip()

        if (
            student_status == STATUS_COMING_SESSION
            or existing_status == STATUS_COMING_SESSION
        ):
            raise ValueError(
                "Coming-session admissions must be edited through the "
                "controlled coming-session workflow."
            )

        completion_statuses = {
            STATUS_GRADUATED,
            STATUS_PARTIALLY_GRADUATED,
        }
        if (
            student_status in completion_statuses
            and student_status != existing_status
        ):
            raise ValueError(
                "Graduated and Partially Graduated are assigned only by "
                "the Class 10 completion workflow."
            )
        if (
            existing_status in completion_statuses
            and student_status != existing_status
        ):
            raise ValueError(
                "A completed Class 10 student cannot be manually moved to "
                "another status. Completion status is controlled by fees."
            )

        archiving_student = (
            student_status == STATUS_ARCHIVED
            and existing_status != STATUS_ARCHIVED
        )
        if archiving_student and existing_status not in {
            STATUS_ACTIVE,
            STATUS_DROPPED,
        }:
            raise ValueError(
                "Only an Active or Dropped student can be manually Archived."
            )

        if (
            existing_status == STATUS_ARCHIVED
            and student_status not in {
                STATUS_ARCHIVED,
                STATUS_ACTIVE,
            }
        ):
            raise ValueError(
                "An Archived student can remain Archived or be "
                "manually reactivated as Active."
            )

        reactivating_to_active = (
            existing_status != STATUS_ACTIVE
            and student_status == STATUS_ACTIVE
        )

        class10_completion_reactivation = (
            existing_status in {
                STATUS_GRADUATED,
                STATUS_PARTIALLY_GRADUATED,
            }
            and student_status == STATUS_ACTIVE
        )

        dropping_from_active = (
            existing_status == STATUS_ACTIVE
            and student_status == STATUS_DROPPED
        )

        # The original Admission Date is never replaced by an activation,
        # reactivation, class change, promotion, drop, archive, or edit.
        admission_date = str(
            existing_student["admission_date"]
        )
        admission_year = int(
            existing_student["admission_year"]
        )
        admission_month = int(
            existing_student["admission_month"]
        )
        admission_day = int(
            existing_student["admission_day"]
        )
        cleaned_admission_time = str(
            existing_student["admission_time"]
            or ""
        )

        if reactivating_to_active:
            reactivation_date = datetime.now()
            effective_month = reactivation_date.month
            effective_year = reactivation_date.year

        elif dropping_from_active:
            drop_date = datetime.now()
            effective_month = drop_date.month
            effective_year = drop_date.year

        elif archiving_student:
            archive_date = datetime.now()
            effective_month = archive_date.month
            effective_year = archive_date.year

        existing_admission_number = str(
            existing_student["admission_number"] or ""
        )
        supplied_admission_number = (
            existing_admission_number
            if admission_number is None
            else str(admission_number).strip()
        )
        if not supplied_admission_number:
            raise ValueError("Admission Number cannot be blank.")

        admission_number_changed = (
            normalize_admission_number_search(supplied_admission_number)
            != normalize_admission_number_search(existing_admission_number)
        )
        if admission_number_changed:
            raise ValueError(
                "Admission Number is permanent and cannot be edited after the student is saved."
            )
        updated_admission_number = existing_admission_number

        cursor.execute(
            """
            SELECT 1
            FROM students
            WHERE TRIM(admission_number) = ? COLLATE NOCASE
              AND student_id != ?
            """,
            (updated_admission_number, int(student_id)),
        )
        if cursor.fetchone() is not None:
            raise ValueError(
                "This Admission Number is already used by another student."
            )

        if gender is None:
            updated_gender = (
                existing_student["gender"]
                or ""
            )

        else:
            if not validate_gender(
                gender
            ):
                raise ValueError(
                    "Gender must be Male or Female."
                )

            updated_gender = (
                gender.strip().title()
            )

        if whatsapp_number is None:
            updated_whatsapp_number = (
                existing_student[
                    "whatsapp_number"
                ]
            )

        else:
            updated_whatsapp_number = (
                whatsapp_number.strip()
                if whatsapp_number
                else None
            )

        if current_address is None:
            updated_current_address = (
                existing_student[
                    "current_address"
                ]
            )

        else:
            updated_current_address = (
                current_address.strip()
                if current_address
                else None
            )

        if permanent_address is None:
            updated_permanent_address = (
                existing_student[
                    "permanent_address"
                ]
            )

        else:
            updated_permanent_address = (
                permanent_address.strip()
                if permanent_address
                else None
            )

        stored_date_of_birth = str(existing_student["date_of_birth"] or "").strip()
        if date_of_birth is None:
            updated_date_of_birth_input = stored_date_of_birth
            allow_stored_iso = True
        else:
            updated_date_of_birth_input = str(date_of_birth or "").strip()
            allow_stored_iso = (
                bool(stored_date_of_birth)
                and updated_date_of_birth_input == stored_date_of_birth
            )

        try:
            stored_admission_datetime = datetime.fromisoformat(admission_date)
        except ValueError as error:
            raise ValueError(
                "Stored Admission Date is invalid and must be corrected."
            ) from error
        updated_date_of_birth = validate_date_of_birth_before_admission(
            updated_date_of_birth_input,
            stored_admission_datetime,
            allow_unchanged_stored_iso=allow_stored_iso,
        )

        if form_b_submitted is None:
            updated_form_b_submitted = int(
                existing_student[
                    "form_b_submitted"
                ]
                or 0
            )

        else:
            updated_form_b_submitted = (
                1
                if form_b_submitted
                else 0
            )

        if birth_certificate_submitted is None:
            updated_birth_certificate_submitted = int(
                existing_student[
                    "birth_certificate_submitted"
                ]
                or 0
            )

        else:
            updated_birth_certificate_submitted = (
                1
                if birth_certificate_submitted
                else 0
            )

        old_class_id = int(existing_student["class_id"])
        requested_class_id = int(class_id)
        released_or_completed = (
            existing_status in completion_statuses
            or existing_student["released_roll_number"] is not None
            or int(existing_student["roll_number"]) <= 0
        )
        lifecycle_locked_profile_edit = (
            released_or_completed
            and not reactivating_to_active
            and not archiving_student
        )
        if lifecycle_locked_profile_edit:
            if requested_class_id != old_class_id:
                raise ValueError(
                    "Released or completed students cannot change class through "
                    "ordinary profile editing. Use the controlled Reactivate Student workflow."
                )
            if student_status != existing_status:
                raise ValueError(
                    "Released or completed student status is lifecycle-controlled."
                )
            # Ignore the positive display roll shown by the UI and preserve the
            # internal released-roll sentinel exactly.
            roll_number = int(existing_student["roll_number"])

        route_result = (
            _resolve_student_gender_section_route_with_cursor(
                cursor,
                student_id=int(
                    student_id
                ),
                selected_class_id=(
                    requested_class_id
                ),
                gender=updated_gender,
                student_status=student_status,
                existing_class_id=old_class_id,
                existing_gender=(
                    existing_student[
                        "gender"
                    ]
                ),
                existing_student_status=(
                    existing_student[
                        "student_status"
                    ]
                ),
                existing_roll_number=int(
                    existing_student[
                        "roll_number"
                    ]
                ),
                existing_released_roll_number=(
                    existing_student[
                        "released_roll_number"
                    ]
                ),
            )
        )

        new_class_id = int(
            route_result[
                "resolved_class_id"
            ]
        )

        roll_number = int(
            route_result[
                "resolved_roll_number"
            ]
        )

        failure_count_before = int(
            existing_student["failure_count"]
            or 0
        )

        class_changed = (
            old_class_id != new_class_id
        )

        if class_changed and (dropping_from_active or archiving_student):
            raise ValueError(
                "Transfer and Drop/Archive cannot be completed in the same "
                "save. Keep the current class when Dropping or Archiving "
                "the student."
            )

        if student_status == STATUS_ACTIVE and roll_number <= 0:
            raise ValueError(
                "Active students must have a positive Roll Number."
            )

        archive_released_roll_number = None
        if archiving_student:
            existing_released_roll = existing_student["released_roll_number"]
            if existing_released_roll is not None:
                archive_released_roll_number = int(existing_released_roll)
            elif int(existing_student["roll_number"]) > 0:
                archive_released_roll_number = int(existing_student["roll_number"])
            else:
                raise ValueError(
                    "The student's historical Roll Number cannot be determined "
                    "for archiving."
                )

            # Preserve the former positive number for display/search and free
            # it for immediate reuse in the class. The student-specific
            # negative sentinel cannot collide with a live positive roll.
            roll_number = -abs(int(student_id))

        manual_promotion_after_failure = False
        manual_backward_repeat = False

        resulting_failure_count = (
            failure_count_before
        )

        # Completion-status reactivation now follows the same failure-count
        # rule as Dropped reactivation. There is no automatic Class 10
        # repeater increment. A lower class increments failure_count only
        # when the student previously studied that destination in this school.

        if class_changed:
            if (
                effective_month is None
                or effective_year is None
            ):
                raise ValueError(
                    "Effective month and year are required "
                    "when changing a student's class."
                )

            effective_month = int(
                effective_month
            )

            effective_year = int(
                effective_year
            )

            if not 1 <= effective_month <= 12:
                raise ValueError(
                    "Effective month must be between 1 and 12."
                )

            if (
                effective_year < 2000
                or effective_year > 2100
            ):
                raise ValueError(
                    "Effective year must be between "
                    "2000 and 2100."
                )

            current_date = datetime.now()
            if (effective_year, effective_month) > (
                current_date.year,
                current_date.month,
            ):
                raise ValueError(
                    "Future-dated class changes are not allowed. Select "
                    "the current month or an earlier month."
                )

            _validate_manual_class_change_chronology_with_cursor(
                cursor=cursor,
                student_id=int(student_id),
                current_class_id=int(old_class_id),
                admission_month=int(admission_month),
                admission_year=int(admission_year),
                effective_month=int(effective_month),
                effective_year=int(effective_year),
            )

            cursor.execute(
                """
                SELECT
                    id,
                    class_level
                FROM classes
                WHERE id IN (?, ?)
                """,
                (
                    old_class_id,
                    new_class_id,
                ),
            )

            class_rows = cursor.fetchall()

            class_levels = {
                int(row["id"]): str(
                    row["class_level"]
                ).strip().upper()
                for row in class_rows
            }

            if (
                old_class_id not in class_levels
                or new_class_id not in class_levels
            ):
                raise ValueError(
                    "The old or new class could not be found."
                )

            level_sequence = [
                "PLAY GROUP",
                "NURSERY",
                "KG",
                "1",
                "2",
                "3",
                "4",
                "5",
                "6",
                "7",
                "8",
                "9",
                "10",
            ]

            old_level = class_levels[
                old_class_id
            ]

            new_level = class_levels[
                new_class_id
            ]

            old_rank = (
                level_sequence.index(old_level)
                if old_level in level_sequence
                else None
            )

            new_rank = (
                level_sequence.index(new_level)
                if new_level in level_sequence
                else None
            )

            session_start_year = (
                effective_year
                if effective_month >= 4
                else effective_year - 1
            )

            target_session_label = (
                f"{session_start_year}-"
                f"{session_start_year + 1}"
            )

            destination_previously_studied = False

            # Historical monthly-fee evidence from an earlier
            # academic session.
            cursor.execute(
                """
                SELECT 1
                FROM student_fee_records
                WHERE student_id = ?
                  AND class_id = ?
                  AND fee_type = 'Monthly'
                  AND session_year <> ?
                LIMIT 1
                """,
                (
                    student_id,
                    new_class_id,
                    target_session_label,
                ),
            )

            if cursor.fetchone() is not None:
                destination_previously_studied = True

            # Promotion history also proves the student was
            # previously assigned to the destination class.
            if not destination_previously_studied:
                cursor.execute(
                    """
                    SELECT 1
                    FROM promotion_history
                    WHERE student_id = ?
                      AND is_reversed = 0
                      AND (
                            old_class_id = ?
                            OR new_class_id = ?
                          )
                    LIMIT 1
                    """,
                    (
                        student_id,
                        new_class_id,
                        new_class_id,
                    ),
                )

                if cursor.fetchone() is not None:
                    destination_previously_studied = True

            # Previous manual class history is additional
            # evidence that the destination class was studied.
            if not destination_previously_studied:
                cursor.execute(
                    """
                    SELECT 1
                    FROM student_class_history
                    WHERE student_id = ?
                      AND (
                            old_class_id = ?
                            OR new_class_id = ?
                          )
                    LIMIT 1
                    """,
                    (
                        student_id,
                        new_class_id,
                        new_class_id,
                    ),
                )

                if cursor.fetchone() is not None:
                    destination_previously_studied = True

            if (
                old_rank is not None
                and new_rank is not None
            ):
                if (
                    new_rank > old_rank
                    and failure_count_before > 0
                ):
                    manual_promotion_after_failure = True
                    resulting_failure_count = 0

                elif (
                    new_rank < old_rank
                    and destination_previously_studied
                ):
                    manual_backward_repeat = True

                    resulting_failure_count = (
                        failure_count_before + 1
                    )

        cursor.execute(
            """
            UPDATE students
            SET
                admission_number = ?,
                roll_number = ?,
                released_roll_number = ?,
                class_id = ?,
                student_name = ?,
                gender = ?,
                father_name = ?,
                father_cnic = ?,
                father_contact_number = ?,
                whatsapp_number = ?,
                current_address = ?,
                permanent_address = ?,
                student_status = ?,
                school_leaving_certificate_submitted = ?,
                form_b_submitted = ?,
                birth_certificate_submitted = ?,
                date_of_birth = ?,
                admission_date = ?,
                admission_year = ?,
                admission_month = ?,
                admission_day = ?,
                admission_time = ?,
                failure_count = ?,
                updated_at = ?,
                updated_by = ?
            WHERE student_id = ?
            """,
            (
                updated_admission_number,
                roll_number,
                (
                    archive_released_roll_number
                    if archiving_student
                    else None
                    if student_status == STATUS_ACTIVE
                    or class_changed
                    or dropping_from_active
                    else existing_student["released_roll_number"]
                ),
                new_class_id,
                normalized_student_name,
                updated_gender,
                normalized_father_name,
                normalized_father_cnic,
                father_contact_number,
                updated_whatsapp_number,
                updated_current_address,
                updated_permanent_address,
                student_status,
                (
                    1
                    if school_leaving_certificate_submitted
                    else 0
                ),
                updated_form_b_submitted,
                updated_birth_certificate_submitted,
                updated_date_of_birth,
                admission_date,
                admission_year,
                admission_month,
                admission_day,
                cleaned_admission_time,
                resulting_failure_count,
                now,
                updated_by,
                student_id,
            ),
        )

        if admission_number_changed:
            cursor.execute(
                """
                INSERT INTO admission_number_history (
                    student_id, old_admission_number, new_admission_number,
                    changed_at, changed_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(student_id), existing_admission_number,
                    updated_admission_number, now, str(updated_by or "admin"),
                ),
            )

        if class_changed or reactivating_to_active:
            # Reactivation assigns the student to an operational destination
            # class even when that destination is the previously retained
            # class. Preserve that class-assignment audit in addition to the
            # explicit status-history event.
            history_actor = (
                f"REACTIVATION:{str(updated_by or 'admin')}"
                if reactivating_to_active
                else str(updated_by or "admin")
            )
            cursor.execute(
                """
                INSERT INTO student_class_history (
                    student_id,
                    old_class_id,
                    new_class_id,
                    effective_month,
                    effective_year,
                    change_type,
                    change_datetime,
                    changed_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    old_class_id,
                    new_class_id,
                    effective_month,
                    effective_year,
                    "MANUAL",
                    now,
                    history_actor,
                ),
            )

        if dropping_from_active or reactivating_to_active or archiving_student:
            cursor.execute(
                """
                INSERT INTO student_status_history(
                    student_id, old_status, new_status, event_type, effective_datetime,
                    reason, changed_by, created_at, related_class_id, related_event_id
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    int(student_id),
                    existing_status,
                    student_status,
                    (
                        "ARCHIVE"
                        if archiving_student
                        else "DROP"
                        if dropping_from_active
                        else "REACTIVATION"
                    ),
                    now,
                    (
                        "Student manually archived and removed from enrolment."
                        if archiving_student
                        else "Student dropped from active enrolment."
                        if dropping_from_active
                        else "Student reactivated through the controlled workflow."
                    ),
                    str(updated_by or "admin"),
                    now,
                    int(old_class_id if dropping_from_active else new_class_id),
                ),
            )

        if (
            class_changed
            or reactivating_to_active
            or dropping_from_active
            or archiving_student
        ):

            from app.services import fee_service

            if archiving_student:
                fee_service.remove_unpaid_fee_records_for_archive(
                    student_id=student_id,
                    connection=connection,
                )

            elif dropping_from_active:
                fee_service.pause_fee_records_after_drop(
                    student_id=student_id,
                    drop_month=effective_month,
                    drop_year=effective_year,
                    connection=connection,
                )

            elif reactivating_to_active:
                # Reactivation starts a new payable period. Historical unpaid
                # dues remain attached to their original months/classes.
                fee_service.generate_reactivation_fee_records(
                    student_id=student_id,
                    class_id=new_class_id,
                    reactivation_month=effective_month,
                    reactivation_year=effective_year,
                    connection=connection,
                )

                if class10_completion_reactivation:
                    cursor.execute(
                        """
                        UPDATE graduation_records
                        SET is_reversed = 1
                        WHERE student_id = ?
                          AND is_reversed = 0
                        """,
                        (student_id,),
                    )

            elif class_changed:
                fee_service.recalculate_fees_after_class_change(
                    student_id=student_id,
                    old_class_id=old_class_id,
                    new_class_id=new_class_id,
                    effective_month=effective_month,
                    effective_year=effective_year,
                    connection=connection,
                    manual_promotion_after_failure=(
                        manual_promotion_after_failure
                    ),
                    manual_backward_repeat=(
                        manual_backward_repeat
                    ),
                )

        from app.services import sibling_fee_service

        sibling_relevant_changed = any((
            normalize_person_name(existing_student["father_name"])
                != normalized_father_name,
            existing_father_cnic != normalized_father_cnic,
            int(old_class_id) != int(new_class_id),
            existing_status != student_status,
            str(existing_student["admission_date"] or "") != admission_date,
            str(existing_student["tracking_id"] or "").strip().upper()
                != str(existing_student["tracking_id"] or "").strip().upper(),
        ))

        if sibling_relevant_changed:
            sibling_effective_month = (
                int(effective_month)
                if effective_month is not None
                else datetime.now().month
            )
            sibling_effective_year = (
                int(effective_year)
                if effective_year is not None
                else datetime.now().year
            )
            sibling_fee_service.recalculate_sibling_fees_after_student_change(
                student_id=int(student_id),
                old_father_cnic=existing_student["father_cnic"],
                new_father_cnic=normalized_father_cnic,
                old_father_name=existing_student["father_name"],
                new_father_name=normalized_father_name,
                effective_month=sibling_effective_month,
                effective_year=sibling_effective_year,
                connection=connection,
                changed_by=str(updated_by or "admin"),
            )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()




# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def reactivate_student(
    student_id: int,
    class_id: int | None = None,
    updated_by: str = "admin",
) -> dict:
    """Reactivate only a Dropped or Archived student with a fresh roll."""
    student_row = get_student_by_id(int(student_id))
    if student_row is None:
        raise ValueError("Student not found.")

    current_status = str(student_row["student_status"] or "").strip()
    if current_status not in {STATUS_DROPPED, STATUS_ARCHIVED}:
        raise ValueError(
            "Only Dropped or Archived students can use Reactivate Student."
        )

    target_class_id = int(
        class_id if class_id is not None else student_row["class_id"]
    )
    # The service transaction generates the committed fresh roll. A UI or
    # preflight value is never trusted for reactivation.
    now = datetime.now()

    update_student(
        student_id=int(student_id),
        admission_number=student_row["admission_number"],
        roll_number=int(student_row["roll_number"]),
        class_id=target_class_id,
        student_name=student_row["student_name"],
        gender=student_row["gender"],
        father_name=student_row["father_name"],
        father_cnic=student_row["father_cnic"] or "",
        father_contact_number=student_row["father_contact_number"],
        whatsapp_number=student_row["whatsapp_number"] or "",
        current_address=student_row["current_address"] or "",
        permanent_address=student_row["permanent_address"] or "",
        student_status=STATUS_ACTIVE,
        school_leaving_certificate_submitted=bool(
            student_row["school_leaving_certificate_submitted"]
        ),
        form_b_submitted=bool(student_row["form_b_submitted"]),
        birth_certificate_submitted=bool(
            student_row["birth_certificate_submitted"]
        ),
        date_of_birth=student_row["date_of_birth"] or "",
        admission_year=int(student_row["admission_year"]),
        admission_month=int(student_row["admission_month"]),
        admission_day=int(student_row["admission_day"]),
        admission_time=student_row["admission_time"] or "",
        effective_month=now.month,
        effective_year=now.year,
        updated_by=updated_by,
    )

    updated_row = get_student_by_id(int(student_id))
    return dict(updated_row) if updated_row is not None else {}


def display_roll_number(student_row) -> str:
    """
    Return the operational roll for Active/reserved records and the former
    roll with * for a record whose positive roll has been released.
    """
    keys = set(student_row.keys()) if hasattr(student_row, "keys") else set()
    released = (
        student_row["released_roll_number"]
        if "released_roll_number" in keys
        else None
    )
    if str(student_row["student_status"] or "").strip() == STATUS_COMING_SESSION:
        return "Pending"
    if released is not None:
        return f"{int(released)}*"
    return str(int(student_row["roll_number"]))


def format_failure_stars(failure_count: int) -> str:
    """Return a string of asterisks representing the student's failure count."""
    if not failure_count or failure_count <= 0:
        return ""
    return " " + ("*" * failure_count)


def display_name_with_stars(student_name: str, failure_count: int) -> str:
    """Return the student's name with failure stars appended for display only."""
    return f"{student_name}{format_failure_stars(failure_count)}"


def display_name_with_status_marker(
    student_name: str,
    failure_count: int,
    student_status: str,
) -> str:
    """
    Return a display-only name. Partially Graduated Class-10
    students receive >>> without changing the stored student name.
    """
    display_name = display_name_with_stars(
        student_name,
        failure_count,
    )

    if (
        str(student_status or "").strip()
        == STATUS_PARTIALLY_GRADUATED
    ):
        return f"{display_name} >>>"

    return display_name
