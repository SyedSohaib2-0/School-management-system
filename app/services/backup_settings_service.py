"""
Persistent settings for multi-destination database backups.

Backup destinations:
1. Hidden internal automatic backups
2. User-selected folder automatic backups
3. Google Drive manual and automatic backups
"""

import json
import logging
import os
import shutil
from copy import deepcopy
from datetime import datetime

from app.config import DATABASE_PATH

logger = logging.getLogger(__name__)


SETTINGS_FILE_PATH = os.path.join(
    os.path.dirname(
        os.path.abspath(DATABASE_PATH)
    ),
    "backup_settings.json",
)
SETTINGS_PREVIOUS_FILE_PATH = f"{SETTINGS_FILE_PATH}.previous"


DEFAULT_BACKUP_SETTINGS = {
    # Hidden internal backups
    "internal_enabled": True,
    "internal_retention": 10,

    # User-selected computer folder
    "selected_folder_enabled": False,
    "selected_folder_path": "",
    "selected_folder_retention": 10,


    # Google Drive automatic and manual backups
    "google_drive_enabled": False,
    "google_drive_path": "",
    "google_drive_retention": 20,
    "google_drive_manual_only": False,
}


def parse_bool(value, default: bool = False) -> bool:
    """Parse supported persisted boolean representations fail-closed."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    logger.warning(
        "Unknown backup boolean value %r; using safe default %s.",
        value,
        bool(default),
    )
    return bool(default)


def _normalise_retention(
    value,
    default_value: int,
) -> int:
    """Return a safe positive backup-retention value."""
    try:
        retention = int(value)
    except (
        TypeError,
        ValueError,
    ):
        return default_value

    if retention < 1:
        return default_value

    return retention


def normalise_backup_settings(
    settings: dict | None,
) -> dict:
    """
    Merge saved settings with required defaults.

    Unknown keys are ignored so damaged or outdated settings do not
    break the application.
    """
    normalised = deepcopy(
        DEFAULT_BACKUP_SETTINGS
    )

    if not isinstance(
        settings,
        dict,
    ):
        return normalised

    normalised["internal_enabled"] = parse_bool(
        settings.get("internal_enabled", True),
        default=True,
    )

    normalised["internal_retention"] = (
        _normalise_retention(
            settings.get(
                "internal_retention"
            ),
            10,
        )
    )

    normalised["selected_folder_enabled"] = parse_bool(
        settings.get("selected_folder_enabled", False),
        default=False,
    )

    normalised[
        "selected_folder_path"
    ] = str(
        settings.get(
            "selected_folder_path",
            "",
        )
        or ""
    ).strip()

    normalised[
        "selected_folder_retention"
    ] = _normalise_retention(
        settings.get(
            "selected_folder_retention"
        ),
        10,
    )


    normalised["google_drive_enabled"] = parse_bool(
        settings.get("google_drive_enabled", False),
        default=False,
    )

    normalised["google_drive_path"] = str(
        settings.get(
            "google_drive_path",
            "",
        )
        or ""
    ).strip()

    normalised[
        "google_drive_retention"
    ] = _normalise_retention(
        settings.get(
            "google_drive_retention"
        ),
        20,
    )

    normalised[
        "google_drive_manual_only"
    ] = False

    return normalised


def _read_valid_settings_file(path: str) -> dict:
    """Read one settings file and reject non-object JSON documents."""
    with open(path, "r", encoding="utf-8") as settings_file:
        saved_settings = json.load(settings_file)
    if not isinstance(saved_settings, dict):
        raise ValueError("Backup settings must be a JSON object.")
    return normalise_backup_settings(saved_settings)


def _flush_file(file_object) -> None:
    """Best-effort durable flush for small recovery metadata files."""
    file_object.flush()
    os.fsync(file_object.fileno())


def _write_settings_file(path: str, settings: dict) -> None:
    """Write a settings document atomically without touching recovery copy."""
    temporary_path = f"{path}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as settings_file:
            json.dump(settings, settings_file, indent=4)
            _flush_file(settings_file)
        os.replace(temporary_path, path)
    except (OSError, TypeError, ValueError):
        try:
            if os.path.isfile(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        raise


def _preserve_current_settings() -> None:
    """Keep the last readable primary document before replacing it."""
    if not os.path.isfile(SETTINGS_FILE_PATH):
        return
    try:
        _read_valid_settings_file(SETTINGS_FILE_PATH)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return

    temporary_previous = f"{SETTINGS_PREVIOUS_FILE_PATH}.tmp"
    try:
        shutil.copyfile(SETTINGS_FILE_PATH, temporary_previous)
        with open(temporary_previous, "rb+") as previous_file:
            os.fsync(previous_file.fileno())
        os.replace(temporary_previous, SETTINGS_PREVIOUS_FILE_PATH)
    except OSError:
        try:
            if os.path.isfile(temporary_previous):
                os.remove(temporary_previous)
        except OSError:
            pass
        raise


def _quarantine_damaged_file(path: str) -> str | None:
    """Move damaged metadata aside so evidence is not silently destroyed."""
    if not os.path.isfile(path):
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    quarantine_path = f"{path}.corrupt_{timestamp}"
    try:
        os.replace(path, quarantine_path)
        return quarantine_path
    except OSError as error:
        logger.warning("Could not quarantine damaged backup settings %s: %s", path, error)
        return None


def save_backup_settings(settings: dict) -> dict:
    """Save settings atomically while preserving the last readable version."""
    normalised = normalise_backup_settings(settings)
    settings_directory = os.path.dirname(SETTINGS_FILE_PATH)
    os.makedirs(settings_directory, exist_ok=True)
    _preserve_current_settings()
    _write_settings_file(SETTINGS_FILE_PATH, normalised)
    return normalised


def load_backup_settings() -> dict:
    """Load settings, recovering the previous valid document when possible."""
    try:
        os.makedirs(os.path.dirname(SETTINGS_FILE_PATH), exist_ok=True)
    except OSError as error:
        logger.error("Backup settings directory is unavailable: %s", error)
        return normalise_backup_settings(DEFAULT_BACKUP_SETTINGS)

    if os.path.isfile(SETTINGS_FILE_PATH):
        try:
            with open(SETTINGS_FILE_PATH, "r", encoding="utf-8") as settings_file:
                saved_settings = json.load(settings_file)
            if not isinstance(saved_settings, dict):
                raise ValueError("Backup settings must be a JSON object.")
            normalised = normalise_backup_settings(saved_settings)
            if normalised != saved_settings:
                save_backup_settings(normalised)
            return normalised
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            logger.warning("Primary backup settings are damaged: %s", error)
            _quarantine_damaged_file(SETTINGS_FILE_PATH)

    if os.path.isfile(SETTINGS_PREVIOUS_FILE_PATH):
        try:
            recovered = _read_valid_settings_file(SETTINGS_PREVIOUS_FILE_PATH)
            _write_settings_file(SETTINGS_FILE_PATH, recovered)
            logger.warning("Recovered backup settings from the previous valid copy.")
            return recovered
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            logger.warning("Previous backup settings are also damaged: %s", error)
            _quarantine_damaged_file(SETTINGS_PREVIOUS_FILE_PATH)

    defaults = normalise_backup_settings(DEFAULT_BACKUP_SETTINGS)
    try:
        _write_settings_file(SETTINGS_FILE_PATH, defaults)
    except OSError as error:
        logger.error("Default backup settings could not be persisted: %s", error)
    return defaults


def update_backup_setting(
    setting_name: str,
    setting_value,
) -> dict:
    """Update one approved backup setting."""
    if setting_name not in (
        DEFAULT_BACKUP_SETTINGS
    ):
        raise KeyError(
            f"Unknown backup setting: "
            f"{setting_name}"
        )

    settings = load_backup_settings()

    settings[
        setting_name
    ] = setting_value

    return save_backup_settings(
        settings
    )


def reset_backup_settings() -> dict:
    """Reset all backup settings to their safe defaults."""
    return save_backup_settings(
        DEFAULT_BACKUP_SETTINGS
    )
