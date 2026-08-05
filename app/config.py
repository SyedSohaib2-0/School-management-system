"""
Central configuration file.
All fixed settings (paths, allowed values, formats) live here so that
every module reads the same values instead of hardcoding them.
"""

import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Base folder paths
# ---------------------------------------------------------------------------

# The installed application folder containing run.py and program assets.
BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

# Writable application data must remain outside Program Files.
# On Windows this resolves to:
# C:\Users\<User>\AppData\Local\HiraModelSchool
LOCAL_APP_DATA_ROOT = os.environ.get(
    "LOCALAPPDATA",
    os.path.join(
        os.path.expanduser("~"),
        "AppData",
        "Local",
    ),
)

APP_DATA_DIR = os.path.join(
    LOCAL_APP_DATA_ROOT,
    "HiraModelSchool",
)

DATA_DIR = os.path.join(
    APP_DATA_DIR,
    "data",
)

# Single shared SQLite database file used by every module.
DATABASE_PATH = os.path.join(
    DATA_DIR,
    "school.db",
)

# Folder where generated PDFs are saved.
EXPORTS_DIR = os.path.join(
    APP_DATA_DIR,
    "exports",
)

# Folder containing internal backups, restore history, and backup logs.
BACKUPS_DIR = os.path.join(
    APP_DATA_DIR,
    "backups",
)

# Stylesheet remains with the installed program files because it is
# a read-only application asset.
STYLE_SHEET_PATH = os.path.join(
    BASE_DIR,
    "app",
    "styles.qss",
)

# Branded application icons used by the main window, taskbar,
# activation dialogs, and installer.
APPLICATION_ICON_PATH = os.path.join(
    BASE_DIR,
    "app",
    "assets",
    "hira_model_school_icon.ico",
)

APPLICATION_ICON_PNG_PATH = os.path.join(
    BASE_DIR,
    "app",
    "assets",
    "hira_model_school_icon.png",
)


# ---------------------------------------------------------------------------
# Approved student statuses (used by Student, Class, and Promotion modules)
# ---------------------------------------------------------------------------

STATUS_ACTIVE = "Active"
STATUS_DROPPED = "Dropped"
STATUS_PARTIALLY_GRADUATED = "Partially Graduated"
STATUS_GRADUATED = "Graduated"
STATUS_ARCHIVED = "Archived"
STATUS_COMING_SESSION = "Coming Session"

ALL_STUDENT_STATUSES = [
    STATUS_ACTIVE,
    STATUS_DROPPED,
    STATUS_PARTIALLY_GRADUATED,
    STATUS_GRADUATED,
    STATUS_ARCHIVED,
    STATUS_COMING_SESSION,
]


# ---------------------------------------------------------------------------
# Approved class levels and sections
# ---------------------------------------------------------------------------

# Special (non-numbered) class levels
SPECIAL_CLASS_LEVELS = ["PLAY GROUP", "NURSERY", "KG"]

# Numbered class levels, stored as text for consistency with special levels
NUMBERED_CLASS_LEVELS = [str(n) for n in range(1, 11)]  # "1".."10"

ALL_CLASS_LEVELS = SPECIAL_CLASS_LEVELS + NUMBERED_CLASS_LEVELS

# Sections are unlimited. Any section made only of uppercase letters is
# allowed (one or more letters), so a school can create as many sections
# as needed: A, B, C, ... Z, and beyond if ever required (e.g. AA, AB).
# This is validated using SECTION_NAME_PATTERN in class_service.py rather
# than a fixed list, since a fixed list would always have a ceiling.
SECTION_NAME_PATTERN = r"^[A-Z]+$"


def section_rank(section: str) -> int:
    """Return Excel-style natural rank: A=1, Z=26, AA=27, AB=28."""
    normalized = str(section or "").strip().upper()
    if not normalized or not normalized.isalpha():
        return 10**9
    result = 0
    for character in normalized:
        result = result * 26 + ord(character) - ord("A") + 1
    return result

# Classes for which the School Leaving Certificate checkbox is optional
SLC_OPTIONAL_CLASS_LEVELS = ["PLAY GROUP"]


def build_class_name(class_level: str, section: str) -> str:
    """
    Build the correctly formatted class name from a level and section.

    Numbered classes  -> no space,  e.g. "3" + "A" -> "3A"
    Special classes   -> one space, e.g. "NURSERY" + "A" -> "NURSERY A"

    Both inputs are uppercased before combining, matching the approved
    class-name rules used across the whole application.
    """
    level = class_level.strip().upper()
    sec = section.strip().upper()

    if level in SPECIAL_CLASS_LEVELS:
        return f"{level} {sec}"
    return f"{level}{sec}"


# ---------------------------------------------------------------------------
# Promotion sequence (used by Promotion Management)
# ---------------------------------------------------------------------------
# Maps each class level to the next class level in the promotion chain.
# Class "10" has no next level -> handled as graduation in promotion logic.

CLASS_LEVEL_SEQUENCE = ["PLAY GROUP", "NURSERY", "KG"] + [str(n) for n in range(1, 11)]


def get_next_class_level(current_level: str):
    """
    Return the next class level in the promotion sequence, or None if the
    current level is the last one (Class 10 -> graduation, handled elsewhere).
    """
    level = current_level.strip().upper()
    if level not in CLASS_LEVEL_SEQUENCE:
        return None
    index = CLASS_LEVEL_SEQUENCE.index(level)
    if index + 1 >= len(CLASS_LEVEL_SEQUENCE):
        return None  # Class 10 is the last level; graduation handled separately
    return CLASS_LEVEL_SEQUENCE[index + 1]


# ---------------------------------------------------------------------------
# Father contact number format
# ---------------------------------------------------------------------------

CONTACT_COUNTRY_CODE = "+92"
CONTACT_DIGITS_REQUIRED = 10

# ---------------------------------------------------------------------------
# Father CNIC format
# ---------------------------------------------------------------------------

CNIC_DIGITS_REQUIRED = 13


# ---------------------------------------------------------------------------
# Fee Management constants
# ---------------------------------------------------------------------------

SESSION_START_CALENDAR_MONTH = 4  # The school session starts every April

# (Month name, calendar month number) in session order: April -> March
SESSION_MONTH_ORDER = [
    ("April", 4), ("May", 5), ("June", 6), ("July", 7),
    ("August", 8), ("September", 9), ("October", 10),
    ("November", 11), ("December", 12), ("January", 1),
    ("February", 2), ("March", 3),
]

# MONTHLY_ONLY_FEE_CONSTANTS_V8
FEE_TYPE_MONTHLY = "Monthly"

FEE_STATUS_PAID = "Paid"
FEE_STATUS_UNPAID = "Unpaid"
FEE_STATUS_WAIVED = "Waived"


def get_current_session_start_year() -> int:
    """
    Return the calendar year the current school session started in.
    A session runs April to March, so January-March belongs to the
    session that started the previous calendar year.
    """
    today = datetime.now()
    if today.month >= SESSION_START_CALENDAR_MONTH:
        return today.year
    return today.year - 1


def get_session_year_label(session_start_year: int) -> str:
    """Build a display label like '2026-2027' from the session's start year."""
    return f"{session_start_year}-{session_start_year + 1}"


def build_session_months(session_start_year: int):
    """
    Return the 12 (month_name, month_number, calendar_year) tuples for one
    school session, in April-to-March order.
    Example for session_start_year=2026:
    [("April", 4, 2026), ("May", 5, 2026), ..., ("March", 3, 2027)]
    """
    months = []
    for month_name, month_number in SESSION_MONTH_ORDER:
        calendar_year = (
            session_start_year if month_number >= SESSION_START_CALENDAR_MONTH
            else session_start_year + 1
        )
        months.append((month_name, month_number, calendar_year))
    return months