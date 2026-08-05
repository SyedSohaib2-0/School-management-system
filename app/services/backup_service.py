"""
Backup Management service.

This module creates safe SQLite database backups without changing the
original database. It uses SQLite's built-in backup mechanism so a
consistent snapshot can be created while the application is running.
"""

import json
import os
import re
import shutil
import stat
import tempfile
import socket
import sqlite3
from datetime import datetime
from uuid import uuid4

from app.config import (
    DATABASE_PATH,
    BACKUPS_DIR,
)
from app import database as database_module
from app import schema_integrity
from app.services.backup_settings_service import (
    load_backup_settings,
)
from app.services.backup_crypto_service import (
    ENCRYPTED_BACKUP_EXTENSION,
    LEGACY_BACKUP_EXTENSION,
    TEMP_DIRECTORY as ENCRYPTED_BACKUP_TEMP_DIRECTORY,
    adopt_backup_security_from_recovery,
    backup_requires_recovery_password,
    create_encrypted_backup_from_database,
    decrypt_encrypted_backup_to_path,
    get_backup_security_status,
    is_encrypted_backup,
    inspect_encrypted_backup,
    BACKUP_INSPECTION_VALID_LOCAL_KEY,
    BACKUP_INSPECTION_PASSWORD_REQUIRED,
    BACKUP_INSPECTION_CORRUPTED_HEADER,
    BACKUP_INSPECTION_UNSUPPORTED_FORMAT,
    protect_master_key_for_pending_restore,
    validate_encrypted_backup,
    validate_encrypted_backup_detailed,
    BACKUP_INSPECTION_AUTHENTICATION_FAILED,
)


BACKUP_FILE_PREFIX = "school_backup"
BACKUP_FILE_EXTENSION = ENCRYPTED_BACKUP_EXTENSION
LEGACY_BACKUP_FILE_EXTENSION = LEGACY_BACKUP_EXTENSION

INTERNAL_BACKUP_DIRECTORY = os.path.join(
    BACKUPS_DIR,
    "internal",
)

PRE_RESTORE_BACKUP_DIRECTORY = os.path.join(
    BACKUPS_DIR,
    "pre_restore",
)

INTERNAL_BACKUP_PREFIX = (
    "school_internal_backup"
)

SELECTED_FOLDER_BACKUP_PREFIX = (
    "school_folder_backup"
)


GOOGLE_DRIVE_BACKUP_PREFIX = (
    "school_google_backup"
)

PRE_RESTORE_BACKUP_PREFIX = (
    "school_pre_restore_backup"
)
RESTORE_STATE_FILE_PATH = os.path.join(
    BACKUPS_DIR,
    "restore_state.json",
)

# Restore requests are staged here while the application is open.
# The live database will be replaced safely during the next startup,
# before normal database connections are created.
PENDING_RESTORE_DIRECTORY = os.path.join(
    BACKUPS_DIR,
    "pending_restore",
)

PENDING_RESTORE_REQUEST_PATH = os.path.join(
    PENDING_RESTORE_DIRECTORY,
    "pending_restore.json",
)

PENDING_RESTORE_DATABASE_PATH = os.path.join(
    PENDING_RESTORE_DIRECTORY,
    "pending_restore.db",
)

PENDING_RESTORE_ENCRYPTED_PATH = os.path.join(
    PENDING_RESTORE_DIRECTORY,
    "pending_restore.hmsbackup",
)

RESTORE_QUARANTINE_DIRECTORY = os.path.join(
    BACKUPS_DIR,
    "Restore_Quarantine",
)

def ensure_backup_directory() -> None:
    """Create the backup directory when it does not already exist."""
    os.makedirs(
        BACKUPS_DIR,
        exist_ok=True,
    )


def build_backup_file_name() -> str:
    """Return a unique timestamped encrypted-backup filename."""
    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S-%f"
    )

    return (
        f"{BACKUP_FILE_PREFIX}_"
        f"{timestamp}"
        f"{BACKUP_FILE_EXTENSION}"
    )


def validate_sqlite_database(
    database_file_path: str,
) -> tuple[bool, str]:
    """Validate SQLite integrity and always release the file handle."""
    if not database_file_path:
        return False, "No database file was provided."
    if not os.path.isfile(database_file_path):
        return False, "The database file does not exist."

    connection = None
    try:
        connection = sqlite3.connect(database_file_path)
        result = connection.execute("PRAGMA integrity_check;").fetchone()
        if not result:
            return False, "The database integrity check returned no result."
        if str(result[0]).lower() != "ok":
            return False, f"Database integrity check failed: {result[0]}"
        return True, "Database integrity check completed successfully."
    except sqlite3.DatabaseError as error:
        return False, f"The selected file is not a valid SQLite database: {error}"
    except OSError as error:
        return False, f"The database file could not be accessed: {error}"
    finally:
        if connection is not None:
            connection.close()


def create_database_backup() -> dict:
    """Create one encrypted backup in the protected backup directory."""
    return create_backup_in_destination(
        destination_directory=BACKUPS_DIR,
        backup_prefix=BACKUP_FILE_PREFIX,
        retention_limit=1000,
        destination_name="Protected database backup",
    )

def format_file_size(
    size_in_bytes: int,
) -> str:
    """Return a readable file-size value."""
    if size_in_bytes < 1024:
        return f"{size_in_bytes} bytes"

    size_in_kb = size_in_bytes / 1024

    if size_in_kb < 1024:
        return f"{size_in_kb:.2f} KB"

    size_in_mb = size_in_kb / 1024

    return f"{size_in_mb:.2f} MB"


def list_database_backups() -> list[dict]:
    """Return protected root-level backups, newest first."""
    ensure_backup_directory()
    return list_destination_backups(
        BACKUPS_DIR,
        BACKUP_FILE_PREFIX,
    )

def delete_database_backup(
    backup_file_path: str,
) -> dict:
    """Delete one recognized root-level encrypted or legacy backup."""
    if not backup_file_path:
        return {
            "success": False,
            "message": "No backup file was selected.",
        }

    ensure_backup_directory()
    absolute_backup_directory = os.path.abspath(BACKUPS_DIR)
    absolute_file_path = os.path.abspath(backup_file_path)
    file_directory = os.path.dirname(absolute_file_path)
    file_name = os.path.basename(absolute_file_path)

    if file_directory != absolute_backup_directory:
        return {
            "success": False,
            "message": "The selected file is outside the approved backup directory.",
        }

    if not file_name.startswith(f"{BACKUP_FILE_PREFIX}_"):
        return {
            "success": False,
            "message": "The selected file is not a recognized school backup.",
        }

    if not file_name.lower().endswith(
        (BACKUP_FILE_EXTENSION, LEGACY_BACKUP_FILE_EXTENSION)
    ):
        return {
            "success": False,
            "message": "The selected file does not have a supported backup extension.",
        }

    if not os.path.isfile(absolute_file_path):
        return {
            "success": False,
            "message": "The selected backup file does not exist.",
        }

    try:
        os.remove(absolute_file_path)
        return {
            "success": True,
            "message": f"Backup '{file_name}' was deleted successfully.",
        }
    except OSError as error:
        return {
            "success": False,
            "message": f"The backup could not be deleted: {error}",
        }

def ensure_multi_backup_directories() -> None:
    """Create protected internal backup directories."""
    os.makedirs(
        INTERNAL_BACKUP_DIRECTORY,
        exist_ok=True,
    )

    os.makedirs(
        PRE_RESTORE_BACKUP_DIRECTORY,
        exist_ok=True,
    )


def _build_destination_backup_name(
    backup_prefix: str,
) -> str:
    """
    Create a unique timestamped backup filename.

    Microseconds are included so two rapid database changes cannot
    create the same filename.
    """
    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S-%f"
    )

    return (
        f"{backup_prefix}_"
        f"{timestamp}"
        f"{BACKUP_FILE_EXTENSION}"
    )


def _parse_backup_timestamp(file_path: str, backup_prefix: str):
    """Parse the application timestamp embedded in a recognized backup name."""
    file_name = os.path.basename(file_path)
    lower_name = file_name.lower()
    extension = None
    for candidate in (BACKUP_FILE_EXTENSION, LEGACY_BACKUP_FILE_EXTENSION):
        if lower_name.endswith(candidate.lower()):
            extension = candidate
            break
    if extension is None:
        return None

    stem = file_name[:-len(extension)]
    expected_prefix = f"{backup_prefix}_"
    if not stem.startswith(expected_prefix):
        return None
    timestamp_text = stem[len(expected_prefix):]
    formats = (
        "%Y-%m-%d_%H-%M-%S-%f",
        "%Y-%m-%d_%H-%M-%S",
        "%Y-%m-%d_%H%M%S",
        "%Y-%m-%d_%H_%M_%S",
    )
    for timestamp_format in formats:
        try:
            return datetime.strptime(timestamp_text, timestamp_format)
        except ValueError:
            continue

    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2})_(\d{2})[-_:]?(\d{2})[-_:]?(\d{2})",
        timestamp_text,
    )
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)}_{match.group(2)}{match.group(3)}{match.group(4)}",
                "%Y-%m-%d_%H%M%S",
            )
        except ValueError:
            return None
    return None


def _backup_sort_key(file_path: str, backup_prefix: str):
    embedded = _parse_backup_timestamp(file_path, backup_prefix)
    if embedded is not None:
        return (embedded.timestamp(), 1, os.path.basename(file_path).upper())
    try:
        fallback = os.path.getmtime(file_path)
    except OSError:
        fallback = 0.0
    return (fallback, 0, os.path.basename(file_path).upper())


def _get_destination_backup_files(
    destination_directory: str,
    backup_prefix: str,
) -> list[str]:
    """Return recognized backups ordered by embedded creation timestamp."""
    if not os.path.isdir(destination_directory):
        return []

    expected_prefix = f"{backup_prefix}_"
    recognized_files = []
    try:
        destination_items = os.listdir(destination_directory)
    except OSError:
        return []

    for file_name in destination_items:
        if not file_name.startswith(expected_prefix):
            continue
        if not file_name.lower().endswith(
            (BACKUP_FILE_EXTENSION, LEGACY_BACKUP_FILE_EXTENSION)
        ):
            continue
        file_path = os.path.join(destination_directory, file_name)
        try:
            stat_result = os.stat(file_path)
        except OSError:
            # A synchronized/cloud file may disappear during enumeration.
            # One missing file must not hide every other available backup.
            continue
        if not stat.S_ISREG(stat_result.st_mode):
            continue
        embedded = _parse_backup_timestamp(file_path, backup_prefix)
        sort_timestamp = (
            embedded.timestamp() if embedded is not None else stat_result.st_mtime
        )
        recognized_files.append(
            (file_path, (sort_timestamp, 1 if embedded is not None else 0, file_name.upper()))
        )

    recognized_files.sort(key=lambda item: item[1], reverse=True)
    return [path for path, _sort_key in recognized_files]


def apply_backup_retention(
    destination_directory: str,
    backup_prefix: str,
    retention_limit: int,
    protected_backup_path: str | None = None,
) -> dict:
    """Keep the true newest timestamped backups and never delete the active file."""
    try:
        safe_retention_limit = int(retention_limit)
    except (TypeError, ValueError):
        safe_retention_limit = 1
    if safe_retention_limit < 1:
        safe_retention_limit = 1

    backup_files = _get_destination_backup_files(
        destination_directory,
        backup_prefix,
    )
    protected_realpath = (
        os.path.normcase(os.path.realpath(protected_backup_path))
        if protected_backup_path
        else None
    )
    # The just-created backup must count inside, not in addition to, the
    # configured retention limit. When the Windows clock moves backwards its
    # embedded timestamp can sort behind older files. The previous slice-based
    # logic protected that file from deletion but also retained every newer
    # file, allowing the directory to grow beyond its configured limit on every
    # backup. Reserve one retention slot for the protected file and fill the
    # remaining slots with the newest other backups.
    retained_realpaths = set()
    if protected_realpath is not None:
        for file_path in backup_files:
            candidate_realpath = os.path.normcase(os.path.realpath(file_path))
            if candidate_realpath == protected_realpath:
                retained_realpaths.add(candidate_realpath)
                break

    remaining_slots = safe_retention_limit - len(retained_realpaths)
    for file_path in backup_files:
        candidate_realpath = os.path.normcase(os.path.realpath(file_path))
        if candidate_realpath in retained_realpaths:
            continue
        if remaining_slots > 0:
            retained_realpaths.add(candidate_realpath)
            remaining_slots -= 1

    files_to_delete = [
        file_path
        for file_path in backup_files
        if os.path.normcase(os.path.realpath(file_path)) not in retained_realpaths
        and (
            protected_realpath is None
            or os.path.normcase(os.path.realpath(file_path)) != protected_realpath
        )
    ]

    deleted_files = []
    deletion_errors = []
    for file_path in files_to_delete:
        try:
            os.remove(file_path)
            deleted_files.append(os.path.basename(file_path))
        except OSError as error:
            deletion_errors.append({
                "path": file_path,
                "file_name": os.path.basename(file_path),
                "error": str(error),
            })

    return {
        "success": not deletion_errors,
        "deleted_files": deleted_files,
        "deletion_errors": deletion_errors,
        "retained_count": len(backup_files) - len(deleted_files),
        "ordered_files": [os.path.basename(path) for path in backup_files],
    }


def create_backup_in_destination(
    destination_directory: str,
    backup_prefix: str,
    retention_limit: int,
    destination_name: str,
) -> dict:
    """Create an authenticated encrypted backup without changing live data."""
    if not destination_directory:
        return {
            "success": False,
            "skipped": True,
            "destination": destination_name,
            "message": f"{destination_name} is not configured.",
            "backup_path": None,
        }

    security_status = get_backup_security_status()
    if not security_status.get("usable", False):
        return {
            "success": False,
            "skipped": False,
            "destination": destination_name,
            "message": (
                "Backup encryption is not configured or its Windows key is "
                "unavailable. Open Backup Management and configure the "
                "Backup Recovery Password first."
            ),
            "backup_path": None,
        }

    if not os.path.isfile(DATABASE_PATH):
        return {
            "success": False,
            "skipped": False,
            "destination": destination_name,
            "message": "The main school database could not be found.",
            "backup_path": None,
        }

    try:
        os.makedirs(destination_directory, exist_ok=True)
        os.makedirs(ENCRYPTED_BACKUP_TEMP_DIRECTORY, exist_ok=True)
    except OSError as error:
        return {
            "success": False,
            "skipped": False,
            "destination": destination_name,
            "message": f"{destination_name} is unavailable: {error}",
            "backup_path": None,
        }

    backup_name = _build_destination_backup_name(backup_prefix)
    backup_path = os.path.join(destination_directory, backup_name)
    snapshot_path = os.path.join(
        ENCRYPTED_BACKUP_TEMP_DIRECTORY,
        f"snapshot_{uuid4().hex}.db",
    )
    source_connection = None
    snapshot_connection = None

    try:
        source_connection = sqlite3.connect(DATABASE_PATH)
        snapshot_connection = sqlite3.connect(snapshot_path)
        source_connection.backup(snapshot_connection)
        snapshot_connection.commit()
    except (sqlite3.DatabaseError, OSError) as error:
        return {
            "success": False,
            "skipped": False,
            "destination": destination_name,
            "message": f"{destination_name} snapshot failed: {error}",
            "backup_path": None,
        }
    finally:
        if snapshot_connection is not None:
            snapshot_connection.close()
        if source_connection is not None:
            source_connection.close()

    try:
        snapshot_valid, snapshot_message = validate_school_database_schema(
            snapshot_path,
            require_current_schema=True,
        )
        if not snapshot_valid:
            return {
                "success": False,
                "skipped": False,
                "destination": destination_name,
                "message": snapshot_message,
                "backup_path": None,
            }

        encryption_result = create_encrypted_backup_from_database(
            snapshot_path,
            backup_path,
            destination_name,
        )
        if not encryption_result.get("success", False):
            return {
                "success": False,
                "skipped": False,
                "destination": destination_name,
                "message": encryption_result.get(
                    "message",
                    "Encrypted backup creation failed.",
                ),
                "backup_path": None,
            }

        encrypted_valid, encrypted_message = validate_encrypted_backup(
            backup_path
        )
        if not encrypted_valid:
            try:
                os.remove(backup_path)
            except OSError:
                pass
            return {
                "success": False,
                "skipped": False,
                "destination": destination_name,
                "message": encrypted_message,
                "backup_path": None,
            }

        verification_directory = tempfile.mkdtemp(prefix="hms_created_backup_verify_")
        verification_database = os.path.join(verification_directory, "verified.db")
        try:
            decrypt_result = decrypt_encrypted_backup_to_path(
                backup_path,
                verification_database,
            )
            if not decrypt_result.get("success", False):
                raise RuntimeError(
                    decrypt_result.get("message") or "Encrypted backup verification failed."
                )
            verified, verify_message = validate_school_database_schema(
                verification_database,
                require_current_schema=True,
            )
            if not verified:
                raise RuntimeError(verify_message)
        except Exception as error:
            try:
                os.remove(backup_path)
            except OSError:
                pass
            return {
                "success": False,
                "skipped": False,
                "destination": destination_name,
                "message": f"Created backup failed full current-schema verification: {error}",
                "backup_path": None,
            }
        finally:
            shutil.rmtree(verification_directory, ignore_errors=True)

        retention_result = apply_backup_retention(
            destination_directory,
            backup_prefix,
            retention_limit,
            protected_backup_path=backup_path,
        )
        retention_success = bool(retention_result.get("success", False))
        deletion_errors = list(retention_result.get("deletion_errors", []) or [])
        failed_paths = []
        for error_item in deletion_errors:
            if isinstance(error_item, dict):
                failed_paths.append(
                    str(error_item.get("path") or error_item.get("file_name") or "")
                )
            else:
                failed_paths.append(str(error_item).split(":", 1)[0])
        return {
            "status": "success" if retention_success else "partial_success",
            "success": retention_success,
            "backup_created": True,
            "retention_success": retention_success,
            "retention_error": None if retention_success else "; ".join(
                f"{item.get('path') or item.get('file_name')}: {item.get('error')}"
                if isinstance(item, dict) else str(item)
                for item in deletion_errors
            ),
            "failed_retention_paths": [path for path in failed_paths if path],
            "skipped": False,
            "destination": destination_name,
            "message": (
                f"{destination_name} was encrypted and verified successfully."
                if retention_success
                else f"{destination_name} was created, but backup retention failed: "
                     f"{retention_result.get('deletion_errors', [])}"
            ),
            "backup_name": backup_name,
            "backup_path": backup_path,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "file_size": os.path.getsize(backup_path),
            "retention_result": retention_result,
            "encrypted": True,
        }
    finally:
        try:
            if os.path.isfile(snapshot_path):
                os.remove(snapshot_path)
        except OSError:
            pass


def run_automatic_backups(destination_names=None) -> dict:
    """Run enabled automatic destinations and report exact outcomes."""
    requested_destinations = (
        {str(name) for name in destination_names}
        if destination_names is not None
        else None
    )
    settings = load_backup_settings()
    ensure_multi_backup_directories()
    results: list[dict] = []

    def skipped(destination: str, message: str) -> None:
        results.append({
            "success": False,
            "skipped": True,
            "destination": destination,
            "message": message,
            "backup_path": None,
        })

    def failed(destination: str, message: str) -> None:
        results.append({
            "success": False,
            "skipped": False,
            "destination": destination,
            "message": message,
            "backup_path": None,
        })

    destinations = (
        (
            "Internal automatic backup",
            bool(settings.get("internal_enabled", True)),
            INTERNAL_BACKUP_DIRECTORY,
            INTERNAL_BACKUP_PREFIX,
            settings.get("internal_retention", 10),
        ),
        (
            "Selected-folder automatic backup",
            bool(settings.get("selected_folder_enabled", False)),
            str(settings.get("selected_folder_path", "") or "").strip(),
            SELECTED_FOLDER_BACKUP_PREFIX,
            settings.get("selected_folder_retention", 10),
        ),
        (
            "Google Drive automatic backup",
            bool(settings.get("google_drive_enabled", False)),
            str(settings.get("google_drive_path", "") or "").strip(),
            GOOGLE_DRIVE_BACKUP_PREFIX,
            settings.get("google_drive_retention", 20),
        ),
    )

    for destination_name, enabled, directory, prefix, retention in destinations:
        if (
            requested_destinations is not None
            and destination_name not in requested_destinations
        ):
            continue
        if not enabled:
            skipped(destination_name, f"{destination_name} is disabled.")
            continue
        if not directory:
            skipped(destination_name, f"{destination_name} is not configured.")
            continue
        if destination_name.startswith("Google Drive") and not os.path.isdir(directory):
            failed(
                destination_name,
                f"{destination_name} failed because the configured folder is unavailable: {directory}",
            )
            continue
        try:
            results.append(
                create_backup_in_destination(
                    destination_directory=directory,
                    backup_prefix=prefix,
                    retention_limit=retention,
                    destination_name=destination_name,
                )
            )
        except Exception as error:
            failed(destination_name, f"Unexpected {destination_name} error: {error}")

    successful_count = sum(bool(item.get("success")) for item in results)
    failed_count = sum(
        not item.get("success", False) and not item.get("skipped", False)
        for item in results
    )
    skipped_count = sum(bool(item.get("skipped")) for item in results)
    return {
        "success": failed_count == 0 and successful_count >= 1,
        "successful_count": successful_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "results": results,
    }

def internet_connection_available(
    timeout_seconds: float = 2.0,
) -> bool:
    """
    Check whether an internet connection is available.

    More than one public DNS service is checked because one service
    may occasionally be blocked by a network provider.
    """
    connection_targets = (
        ("1.1.1.1", 53),
        ("8.8.8.8", 53),
    )

    for host_name, port_number in connection_targets:
        try:
            with socket.create_connection(
                (
                    host_name,
                    port_number,
                ),
                timeout=timeout_seconds,
            ):
                return True

        except OSError:
            continue

    return False

def create_manual_google_drive_backup() -> dict:
    """
    Create a manual Google Drive backup.

    Google Drive is never included in automatic backups.

    A missing folder, unavailable internet connection, disconnected
    Google Drive application, or file-system error returns a controlled
    result and never crashes the software.
    """
    settings = load_backup_settings()

    google_drive_path = settings.get(
        "google_drive_path",
        "",
    )

    if not google_drive_path:
        return {
            "success": False,
            "skipped": True,
            "destination": "Google Drive",
            "message": (
                "Google Drive backup folder is not configured.\n\n"
                "Select a Google Drive folder first."
            ),
            "backup_path": None,
        }

    if not os.path.isdir(
        google_drive_path
    ):
        return {
            "success": False,
            "skipped": True,
            "destination": "Google Drive",
            "message": (
                "The configured Google Drive folder is unavailable.\n\n"
                "Open Google Drive for Desktop and check that the "
                "selected folder still exists."
            ),
            "backup_path": None,
        }

    if not internet_connection_available():
        return {
            "success": False,
            "skipped": True,
            "destination": "Google Drive",
            "message": (
                "Internet connection is unavailable.\n\n"
                "Connect to the internet and click New Backup again."
            ),
            "backup_path": None,
        }

    try:
        result = create_backup_in_destination(
            destination_directory=(
                google_drive_path
            ),
            backup_prefix=(
                GOOGLE_DRIVE_BACKUP_PREFIX
            ),
            retention_limit=settings.get(
                "google_drive_retention",
                20,
            ),
            destination_name=(
                "Google Drive manual backup"
            ),
        )

        return result

    except (
        OSError,
        sqlite3.DatabaseError,
    ) as error:
        return {
            "success": False,
            "skipped": True,
            "destination": "Google Drive",
            "message": (
                "Google Drive backup could not be created.\n\n"
                "Check the internet connection, Google Drive "
                "application, and selected folder permissions.\n\n"
                f"Technical details: {error}"
            ),
            "backup_path": None,
        }

    except Exception as error:
        return {
            "success": False,
            "skipped": True,
            "destination": "Google Drive",
            "message": (
                "An unexpected Google Drive backup error occurred.\n\n"
                "The school database was not affected.\n\n"
                f"Technical details: {error}"
            ),
            "backup_path": None,
        }

def create_manual_selected_folder_backup() -> dict:
    """
    Create a manual backup in the user-selected computer folder.

    The folder must first be configured in Backup Management.
    The latest 10 recognized backups are retained.
    """
    settings = load_backup_settings()

    selected_folder_path = settings.get(
        "selected_folder_path",
        "",
    )

    if not selected_folder_path:
        return {
            "success": False,
            "skipped": True,
            "destination": "Selected folder",
            "message": (
                "No computer backup folder has been selected."
            ),
            "backup_path": None,
        }

    try:
        return create_backup_in_destination(
            destination_directory=(
                selected_folder_path
            ),
            backup_prefix=(
                SELECTED_FOLDER_BACKUP_PREFIX
            ),
            retention_limit=settings.get(
                "selected_folder_retention",
                10,
            ),
            destination_name=(
                "Selected-folder manual backup"
            ),
        )

    except Exception as error:
        return {
            "success": False,
            "skipped": True,
            "destination": "Selected folder",
            "message": (
                "The selected-folder backup could not "
                f"be created: {error}"
            ),
            "backup_path": None,
        }


def list_destination_backup_metadata(
    destination_directory: str,
    backup_prefix: str,
) -> list[dict]:
    """Return inexpensive backup metadata; heavy validation remains pending."""
    records = []
    for backup_path in _get_destination_backup_files(destination_directory, backup_prefix):
        try:
            file_size = os.path.getsize(backup_path)
            parsed_timestamp = _parse_backup_timestamp(backup_path, backup_prefix)
            if parsed_timestamp is None:
                parsed_timestamp = datetime.fromtimestamp(os.path.getmtime(backup_path))
            encrypted = is_encrypted_backup(backup_path)
            inspection = inspect_encrypted_backup(backup_path) if encrypted else None
            classification = inspection["classification"] if inspection else "NOT_ENCRYPTED"
            immediate_status = "Pending"
            message = "Full validation is pending."
            if not encrypted:
                immediate_status = "Recognized Legacy Backup"
                message = (
                    "Migration candidate recognized. A temporary copy must be "
                    "migrated and fully validated before it is restore-ready."
                )
            if classification == BACKUP_INSPECTION_PASSWORD_REQUIRED:
                immediate_status = "Password Required"
                message = inspection["message"]
            elif classification in {BACKUP_INSPECTION_CORRUPTED_HEADER, BACKUP_INSPECTION_UNSUPPORTED_FORMAT}:
                immediate_status = "Corrupted"
                message = inspection["message"]
            records.append({
                "backup_name": os.path.basename(backup_path),
                "backup_path": backup_path,
                "created_at": parsed_timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "file_size": file_size,
                "file_size_display": format_file_size(file_size),
                "is_valid": None,
                "validation_message": message,
                "status_text": immediate_status,
                "backup_format": "encrypted" if encrypted else "legacy",
                "inspection_classification": classification,
            })
        except OSError:
            continue
    return records


def validate_destination_backup_record(backup_path: str) -> dict:
    """Perform one full backup validation outside the UI thread."""
    if is_encrypted_backup(backup_path):
        detailed = validate_encrypted_backup_detailed(backup_path)
        classification = detailed.get("classification")
        message = str(detailed.get("message") or "")
        if classification == BACKUP_INSPECTION_PASSWORD_REQUIRED:
            status_text = "Password Required"
            is_valid = None
        elif classification in {
            BACKUP_INSPECTION_CORRUPTED_HEADER,
            BACKUP_INSPECTION_UNSUPPORTED_FORMAT,
        }:
            status_text = "Corrupted"
            is_valid = False
        elif classification == BACKUP_INSPECTION_AUTHENTICATION_FAILED:
            status_text = "Authentication Failed"
            is_valid = False
        else:
            is_valid = bool(detailed.get("success"))
            status_text = "Valid" if is_valid else "Validation Failed"
        return {
            "is_valid": is_valid,
            "status_text": status_text,
            "validation_message": message,
            "classification": classification,
        }
    temporary_directory = tempfile.mkdtemp(prefix="hms_legacy_validation_")
    temporary_copy = os.path.join(temporary_directory, "legacy_candidate.db")
    try:
        shutil.copy2(backup_path, temporary_copy)
        valid, message = migrate_and_validate_school_database_copy(temporary_copy)
        return {
            "is_valid": valid,
            "status_text": (
                "Validated and Restore-Ready"
                if valid
                else "Invalid or Corrupted"
            ),
            "validation_message": message,
            "classification": "LEGACY_MIGRATION_CANDIDATE",
        }
    except OSError as error:
        return {
            "is_valid": False,
            "status_text": "Invalid or Corrupted",
            "validation_message": f"The legacy backup could not be copied for validation: {error}",
            "classification": "LEGACY_MIGRATION_CANDIDATE",
        }
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def list_destination_backups(
    destination_directory: str,
    backup_prefix: str,
) -> list[dict]:
    """Backward-compatible synchronous listing built from staged operations."""
    records = list_destination_backup_metadata(destination_directory, backup_prefix)
    for record in records:
        if record["status_text"] in {"Pending", "Recognized Legacy Backup"}:
            record.update(validate_destination_backup_record(record["backup_path"]))
    return records

def validate_school_database_schema(
    database_file_path: str,
    require_current_schema: bool = False,
) -> tuple[bool, str]:
    """Validate school identity and, when requested, full current integrity."""
    is_valid, validation_message = validate_sqlite_database(database_file_path)
    if not is_valid:
        return False, validation_message

    connection = None
    try:
        connection = sqlite3.connect(database_file_path)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "NORMALIZE_PERSON_NAME", 1, database_module.normalize_person_name,
            deterministic=True,
        )
        connection.create_function(
            "HMS_TEST_FIXTURE_MODE", 0, lambda: 0, deterministic=True
        )
        existing_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        baseline_columns = {
            "classes": {"id", "class_level", "section", "class_name"},
            "students": {"student_id", "admission_number", "roll_number", "class_id", "student_name", "father_name", "student_status", "admission_year", "admission_month", "admission_day"},
            "student_class_history": {"history_id", "student_id", "new_class_id", "effective_month", "effective_year", "change_type"},
            "fee_settings": {"id", "class_id", "monthly_fee"},
            "fee_history": {"id", "class_id", "monthly_fee"},
            "student_fee_override": {"id", "student_id"},
            "student_fee_records": {"id", "student_id", "class_id", "fee_type", "amount", "status"},
            "fee_payments": {"id", "receipt_no", "student_id", "payment_date", "total_amount"},
            "fee_receipts": {"id", "payment_id", "student_fee_record_id", "item_name", "amount", "status"},
            "promotion_batches": {"batch_id", "source_class_id"},
            "promotion_history": {"promotion_id", "student_id", "old_class_id"},
            "graduation_records": {"graduated_record_id", "student_id"},
            "expenses": {"id", "expense_date", "amount", "description"},
        }
        missing_tables = set(baseline_columns) - existing_tables
        if missing_tables:
            return False, (
                "The selected database is not a recognized school backup.\n\n"
                "Missing required tables: " + ", ".join(sorted(missing_tables))
            )
        for table, required in baseline_columns.items():
            columns = {
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            missing = required - columns
            if missing:
                return False, (
                    "The selected database is incomplete or incompatible.\n\n"
                    f"Table {table} is missing required columns: "
                    + ", ".join(sorted(missing))
                )

        installed_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if installed_version < 0:
            return False, "The database schema version is invalid."
        if installed_version > database_module.CURRENT_SCHEMA_VERSION:
            return False, (
                "The selected database was created by a newer unsupported "
                f"version ({installed_version}). This application supports "
                f"up to version {database_module.CURRENT_SCHEMA_VERSION}."
            )
        if not require_current_schema:
            return True, (
                "The school database is genuine and its schema version is "
                "supported for temporary-copy migration."
            )
        if installed_version != database_module.CURRENT_SCHEMA_VERSION:
            return False, (
                "The temporary restore database was not migrated to the "
                f"required schema version {database_module.CURRENT_SCHEMA_VERSION}."
            )

        valid, message = schema_integrity.validate_current_schema(
            connection,
            repair_missing_indexes=False,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            return False, "Restore candidate failed current-schema integrity: " + message
        return True, "The migrated school database passed full validation."
    except (sqlite3.DatabaseError, schema_integrity.SchemaIntegrityError) as error:
        return False, f"The school database structure could not be verified: {error}"
    finally:
        if connection is not None:
            connection.close()


def migrate_and_validate_school_database_copy(
    database_file_path: str,
) -> tuple[bool, str]:
    """Migrate only a temporary restore copy, then fully validate it."""
    preflight_valid, preflight_message = validate_school_database_schema(
        database_file_path,
        require_current_schema=False,
    )
    if not preflight_valid:
        return False, preflight_message

    connection = None
    try:
        connection = sqlite3.connect(database_file_path)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "NORMALIZE_PERSON_NAME", 1, database_module.normalize_person_name,
            deterministic=True,
        )
        connection.create_function("HMS_TEST_FIXTURE_MODE", 0, lambda: 0, deterministic=True)
        connection.execute("BEGIN IMMEDIATE")
        installed_version = database_module.get_database_schema_version(connection)
        migrated_version = database_module.run_schema_migrations(
            connection,
            installed_version,
        )
        if migrated_version != database_module.CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                "The temporary restore copy did not reach the current schema version."
            )

        # Same-schema application hotfixes can strengthen trigger/index
        # definitions without incrementing PRAGMA user_version. Live startup
        # always reinstalls those code-owned definitions; a restore copy must do
        # the same or an otherwise valid V23 backup created before a hotfix is
        # incorrectly rejected as incompatible.
        schema_integrity.install_integrity_triggers(connection)
        valid, message = schema_integrity.validate_critical_indexes(
            connection,
            repair_missing=True,
            report_directory=os.path.join(BACKUPS_DIR, "Reconciliation"),
        )
        if not valid:
            raise RuntimeError(message)
        valid, message = schema_integrity.validate_tracking_id_trigger(
            connection,
            repair_missing=True,
        )
        if not valid:
            raise RuntimeError(message)

        database_module.set_database_schema_version(connection, migrated_version)
        connection.commit()
    except Exception as error:
        if connection is not None:
            connection.rollback()
        return False, (
            "The temporary restore copy could not be migrated safely.\n\n"
            f"Technical details: {error}"
        )
    finally:
        if connection is not None:
            connection.close()

    return validate_school_database_schema(
        database_file_path,
        require_current_schema=True,
    )


def _has_reparse_attribute(path: str) -> bool:
    """Return True for a symbolic link, junction, or Windows reparse point."""
    try:
        if os.path.islink(path):
            return True
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_flag)
    except OSError:
        return False


def _path_is_inside_directory(file_path: str, directory_path: str) -> bool:
    """Verify lexical and resolved containment, including link/junction chains."""
    if not file_path or not directory_path:
        return False
    try:
        absolute_file = os.path.abspath(file_path)
        absolute_root = os.path.abspath(directory_path)
        if os.path.commonpath([absolute_file, absolute_root]) != absolute_root:
            return False
        resolved_file = os.path.realpath(absolute_file)
        resolved_root = os.path.realpath(absolute_root)
        if os.path.commonpath([resolved_file, resolved_root]) != resolved_root:
            return False

        relative = os.path.relpath(absolute_file, absolute_root)
        current = absolute_root
        for component in relative.split(os.sep):
            if component in {"", "."}:
                continue
            current = os.path.join(current, component)
            if _has_reparse_attribute(current):
                resolved_component = os.path.realpath(current)
                if os.path.commonpath([resolved_component, resolved_root]) != resolved_root:
                    return False
        return True
    except (OSError, ValueError):
        return False


def validate_restore_source_path(file_path: str) -> tuple[bool, str, str]:
    """Validate an existing restore source against resolved approved roots."""
    absolute_path = os.path.abspath(str(file_path or ""))
    if not os.path.isfile(absolute_path):
        return False, "The selected backup file does not exist.", absolute_path
    for root in get_approved_restore_directories():
        if _path_is_inside_directory(absolute_path, root):
            return True, "Restore source path is approved.", os.path.realpath(absolute_path)
    return (
        False,
        "The selected file is outside the approved backup locations or uses an unsafe symbolic-link/junction path.",
        absolute_path,
    )

def get_approved_restore_directories() -> list[str]:
    """
    Return all directories from which restoration is permitted.

    This includes:
    - Hidden internal backups
    - Protected pre-restore backups
    - Configured selected folder
    - Configured Google Drive folder
    """
    settings = load_backup_settings()

    approved_directories = [
        INTERNAL_BACKUP_DIRECTORY,
        PRE_RESTORE_BACKUP_DIRECTORY,
    ]

    configured_paths = [
        settings.get(
            "selected_folder_path",
            "",
        ),
        settings.get(
            "google_drive_path",
            "",
        ),
    ]

    for configured_path in configured_paths:
        configured_path = str(
            configured_path
            or ""
        ).strip()

        if configured_path:
            approved_directories.append(
                configured_path
            )

    unique_directories = []

    for directory_path in approved_directories:
        absolute_directory_path = os.path.abspath(
            directory_path
        )

        if absolute_directory_path not in (
            unique_directories
        ):
            unique_directories.append(
                absolute_directory_path
            )

    return unique_directories


def create_pre_restore_backup() -> dict:
    """
    Create a protected backup of the current live database before
    any restoration is attempted.

    The newest 10 pre-restore backups are retained.
    """
    ensure_multi_backup_directories()

    return create_backup_in_destination(
        destination_directory=(
            PRE_RESTORE_BACKUP_DIRECTORY
        ),
        backup_prefix=(
            PRE_RESTORE_BACKUP_PREFIX
        ),
        retention_limit=10,
        destination_name=(
            "Pre-restore emergency backup"
        ),
    )


def restore_database_backup(
    selected_backup_path: str,
    record_history: bool = True,
    recovery_password: str | None = None,
) -> dict:
    """Prepare a safe restart-time restore instead of replacing live data."""
    return stage_database_restore(
        selected_backup_path=selected_backup_path,
        record_history=record_history,
        recovery_password=recovery_password,
    )

def get_latest_valid_pre_restore_backup() -> dict:
    """Find the newest authenticated pre-restore emergency backup."""
    ensure_multi_backup_directories()
    backup_paths = _get_destination_backup_files(
        PRE_RESTORE_BACKUP_DIRECTORY,
        PRE_RESTORE_BACKUP_PREFIX,
    )
    if not backup_paths:
        return {
            "success": False,
            "message": "No pre-restore emergency backup is available.",
            "backup_path": None,
            "backup_name": None,
            "created_at": None,
        }

    validation_errors = []
    for backup_path in backup_paths:
        if is_encrypted_backup(backup_path):
            is_valid, validation_message = validate_encrypted_backup(backup_path)
        else:
            is_valid, validation_message = validate_school_database_schema(
                backup_path
            )
        if not is_valid:
            validation_errors.append(
                {"backup_path": backup_path, "message": validation_message}
            )
            continue
        try:
            created_at = datetime.fromtimestamp(
                os.path.getmtime(backup_path)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            created_at = "Unknown"
        return {
            "success": True,
            "message": "The latest valid pre-restore backup was found.",
            "backup_path": backup_path,
            "backup_name": os.path.basename(backup_path),
            "created_at": created_at,
        }

    return {
        "success": False,
        "message": (
            "Pre-restore backup files were found, but none passed "
            "authentication and database validation."
        ),
        "backup_path": None,
        "backup_name": None,
        "created_at": None,
        "validation_errors": validation_errors,
    }


def reverse_last_restore(
    recovery_password: str | None = None,
) -> dict:
    """Stage the latest reversible restore for next-startup processing."""
    reversible_result = get_reversible_restore_operation()
    if not reversible_result.get("success", False):
        return {
            "success": False,
            "message": reversible_result.get(
                "message",
                "No reversible restore operation is available.",
            ),
            "restart_required": False,
        }
    restore_operation = reversible_result["restore_operation"]
    return stage_database_restore(
        selected_backup_path=restore_operation["pre_restore_backup_path"],
        record_history=False,
        request_type="reverse_restore",
        target_restore_id=restore_operation.get("restore_id"),
        recovery_password=recovery_password,
    )
    

def _default_restore_state() -> dict:
    """Return an empty restore-history structure."""
    return {
        "last_restore": None,
    }


def load_restore_state() -> dict:
    """
    Load the most recent restore operation safely.

    A missing or damaged state file returns an empty state instead
    of crashing the application.
    """
    if not os.path.isfile(
        RESTORE_STATE_FILE_PATH
    ):
        return _default_restore_state()

    try:
        with open(
            RESTORE_STATE_FILE_PATH,
            "r",
            encoding="utf-8",
        ) as state_file:
            state = json.load(
                state_file
            )

        if not isinstance(
            state,
            dict,
        ):
            return _default_restore_state()

        last_restore = state.get(
            "last_restore"
        )

        if (
            last_restore is not None
            and not isinstance(
                last_restore,
                dict,
            )
        ):
            return _default_restore_state()

        return {
            "last_restore": last_restore,
        }

    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):
        return _default_restore_state()


def save_restore_state(
    state: dict,
) -> dict:
    """
    Save restore history atomically.

    The temporary-file approach prevents a partially written state
    file if Windows or the software closes unexpectedly.
    """
    os.makedirs(
        BACKUPS_DIR,
        exist_ok=True,
    )

    temporary_path = (
        f"{RESTORE_STATE_FILE_PATH}.tmp"
    )

    safe_state = {
        "last_restore": state.get(
            "last_restore"
        ),
    }

    try:
        with open(
            temporary_path,
            "w",
            encoding="utf-8",
        ) as state_file:
            json.dump(
                safe_state,
                state_file,
                indent=4,
            )

        os.replace(
            temporary_path,
            RESTORE_STATE_FILE_PATH,
        )

        return safe_state

    except (
        OSError,
        TypeError,
        ValueError,
    ):
        try:
            if os.path.isfile(
                temporary_path
            ):
                os.remove(
                    temporary_path
                )
        except OSError:
            pass

        raise


def record_restore_operation(
    selected_backup_path: str,
    pre_restore_backup_path: str,
) -> dict:
    """
    Persist the normal-restore history before live database replacement.

    The caller must restore the previous state if the subsequent atomic live
    database replacement fails. Each completed normal restore can later be
    reversed only once.
    """
    restore_operation = {
        "restore_id": str(
            uuid4()
        ),
        "selected_backup_path": os.path.abspath(
            selected_backup_path
        ),
        "pre_restore_backup_path": os.path.abspath(
            pre_restore_backup_path
        ),
        "restored_at": datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "is_reversed": False,
        "reversed_at": None,
        "reverse_safety_backup_path": None,
    }

    save_restore_state(
        {
            "last_restore": restore_operation,
        }
    )

    return restore_operation


def get_reversible_restore_operation() -> dict:
    """Return the latest restore only when its emergency backup is usable."""
    state = load_restore_state()
    restore_operation = state.get("last_restore")
    if not restore_operation:
        return {
            "success": False,
            "message": "No recorded restore operation is available to reverse.",
            "restore_operation": None,
        }
    if restore_operation.get("is_reversed", False):
        return {
            "success": False,
            "message": "The most recent restore has already been reversed.",
            "restore_operation": restore_operation,
        }
    pre_restore_backup_path = restore_operation.get(
        "pre_restore_backup_path",
        "",
    )
    if not pre_restore_backup_path:
        return {
            "success": False,
            "message": (
                "The recorded restore operation does not contain an emergency "
                "backup path."
            ),
            "restore_operation": restore_operation,
        }
    if not os.path.isfile(pre_restore_backup_path):
        return {
            "success": False,
            "message": (
                "The emergency backup required to reverse the last restore no "
                "longer exists."
            ),
            "restore_operation": restore_operation,
        }
    if is_encrypted_backup(pre_restore_backup_path):
        is_valid, validation_message = validate_encrypted_backup(
            pre_restore_backup_path
        )
    else:
        is_valid, validation_message = validate_school_database_schema(
            pre_restore_backup_path
        )
    if not is_valid:
        return {
            "success": False,
            "message": (
                "The emergency backup required to reverse the last restore is "
                f"invalid.\n\n{validation_message}"
            ),
            "restore_operation": restore_operation,
        }
    return {
        "success": True,
        "message": "The most recent restore operation can be reversed.",
        "restore_operation": restore_operation,
    }


def mark_last_restore_reversed(
    reverse_safety_backup_path: str,
    target_restore_id: str | None = None,
) -> dict:
    """
    Mark the latest restore as reversed.

    When a target restore ID is supplied, the state is updated only
    when it matches the recorded restore operation.
    """
    state = load_restore_state()

    restore_operation = state.get(
        "last_restore"
    )

    if not restore_operation:
        return {
            "success": False,
            "message": (
                "No restore operation was available to update."
            ),
        }

    recorded_restore_id = restore_operation.get(
        "restore_id"
    )

    if (
        target_restore_id is not None
        and recorded_restore_id != target_restore_id
    ):
        return {
            "success": False,
            "message": (
                "The recorded restore operation does not match "
                "the reverse-restore request."
            ),
        }

    if restore_operation.get(
        "is_reversed",
        False,
    ):
        return {
            "success": False,
            "message": (
                "The most recent restore was already marked "
                "as reversed."
            ),
        }

    restore_operation[
        "is_reversed"
    ] = True

    restore_operation[
        "reversed_at"
    ] = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    restore_operation[
        "reverse_safety_backup_path"
    ] = os.path.abspath(
        reverse_safety_backup_path
    )

    save_restore_state(
        {
            "last_restore": restore_operation,
        }
    )

    return {
        "success": True,
        "message": (
            "The restore operation was marked as reversed."
        ),
        "restore_operation": restore_operation,
    }



def stage_database_restore(
    selected_backup_path: str,
    record_history: bool = True,
    request_type: str = "normal_restore",
    target_restore_id: str | None = None,
    recovery_password: str | None = None,
) -> dict:
    """Authenticate and stage an encrypted or legacy restore for startup."""
    if request_type not in {"normal_restore", "reverse_restore"}:
        return {
            "success": False,
            "message": "The restore request type is invalid.",
            "restart_required": False,
        }
    if not selected_backup_path:
        return {
            "success": False,
            "message": "No backup file was selected.",
            "restart_required": False,
        }

    path_valid, path_message, absolute_backup_path = validate_restore_source_path(
        selected_backup_path
    )
    if not path_valid:
        return {
            "success": False,
            "message": path_message,
            "restart_required": False,
        }

    os.makedirs(PENDING_RESTORE_DIRECTORY, exist_ok=True)
    os.makedirs(ENCRYPTED_BACKUP_TEMP_DIRECTORY, exist_ok=True)
    encrypted_format = is_encrypted_backup(absolute_backup_path)
    staged_path = (
        PENDING_RESTORE_ENCRYPTED_PATH
        if encrypted_format
        else PENDING_RESTORE_DATABASE_PATH
    )
    other_staged_path = (
        PENDING_RESTORE_DATABASE_PATH
        if encrypted_format
        else PENDING_RESTORE_ENCRYPTED_PATH
    )
    temporary_staged_path = f"{staged_path}.tmp"
    temporary_request_path = f"{PENDING_RESTORE_REQUEST_PATH}.tmp"
    validation_database_path = os.path.join(
        ENCRYPTED_BACKUP_TEMP_DIRECTORY,
        f"restore_validation_{uuid4().hex}.db",
    )
    protected_pending_master_key = None

    try:
        if encrypted_format:
            decrypt_result = decrypt_encrypted_backup_to_path(
                absolute_backup_path,
                validation_database_path,
                recovery_password=recovery_password,
            )
            if not decrypt_result.get("success", False):
                return {
                    "success": False,
                    "message": decrypt_result.get(
                        "message",
                        "The encrypted backup could not be authenticated.",
                    ),
                    "restart_required": False,
                    "recovery_password_required": (
                        backup_requires_recovery_password(absolute_backup_path)
                    ),
                }
            database_valid, database_message = (
                migrate_and_validate_school_database_copy(
                    validation_database_path
                )
            )
            if not database_valid:
                return {
                    "success": False,
                    "message": database_message,
                    "restart_required": False,
                }
            master_key = decrypt_result.get("_master_key")
            protected_pending_master_key = protect_master_key_for_pending_restore(
                master_key
            )
            if recovery_password:
                adopt_result = adopt_backup_security_from_recovery(
                    absolute_backup_path,
                    recovery_password,
                    master_key,
                )
                if not adopt_result.get("success", False):
                    return {
                        "success": False,
                        "message": adopt_result.get(
                            "message",
                            "Recovered backup security could not be protected locally.",
                        ),
                        "restart_required": False,
                    }
        else:
            shutil.copy2(absolute_backup_path, validation_database_path)
            database_valid, database_message = (
                migrate_and_validate_school_database_copy(
                    validation_database_path
                )
            )
            if not database_valid:
                return {
                    "success": False,
                    "message": database_message,
                    "restart_required": False,
                }

        for temporary_path in (temporary_staged_path, temporary_request_path):
            if os.path.isfile(temporary_path):
                os.remove(temporary_path)

        shutil.copy2(absolute_backup_path, temporary_staged_path)
        os.replace(temporary_staged_path, staged_path)
        if os.path.isfile(other_staged_path):
            os.remove(other_staged_path)

        restore_request = {
            "request_id": str(uuid4()),
            "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "original_backup_path": absolute_backup_path,
            "staged_backup_path": staged_path,
            "backup_format": "encrypted" if encrypted_format else "legacy",
            "record_history": bool(record_history),
            "request_type": request_type,
            "target_restore_id": target_restore_id,
            "pending_protected_master_key": protected_pending_master_key,
        }
        with open(temporary_request_path, "w", encoding="utf-8") as request_file:
            json.dump(restore_request, request_file, indent=4)
            request_file.flush()
            os.fsync(request_file.fileno())
        os.replace(temporary_request_path, PENDING_RESTORE_REQUEST_PATH)

        format_text = "encrypted" if encrypted_format else "legacy unencrypted"
        return {
            "success": True,
            "message": (
                f"The {format_text} backup was verified and prepared for "
                "restoration.\n\nThe software must close and start again to "
                "complete the restore safely."
            ),
            "restart_required": True,
            "pending_request": restore_request,
        }
    except (OSError, shutil.Error, TypeError, ValueError, RuntimeError) as error:
        for temporary_path in (temporary_staged_path, temporary_request_path):
            try:
                if os.path.isfile(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
        return {
            "success": False,
            "message": (
                "The restore could not be prepared safely.\n\n"
                f"Technical details: {error}"
            ),
            "restart_required": False,
        }
    finally:
        try:
            if os.path.isfile(validation_database_path):
                os.remove(validation_database_path)
        except OSError:
            pass

def _quarantine_invalid_pending_restore(
    failure_reason: str,
    validation_stage: str,
    source_paths=None,
) -> dict:
    """Quarantine a damaged pending request and permit normal startup."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    quarantine_path = os.path.join(
        RESTORE_QUARANTINE_DIRECTORY,
        timestamp,
    )
    os.makedirs(quarantine_path, exist_ok=True)

    moved_paths = []
    move_failures = []
    pending_root = os.path.realpath(PENDING_RESTORE_DIRECTORY)
    if os.path.isdir(PENDING_RESTORE_DIRECTORY):
        for entry_name in sorted(os.listdir(PENDING_RESTORE_DIRECTORY)):
            source_path = os.path.join(PENDING_RESTORE_DIRECTORY, entry_name)
            try:
                resolved_source = os.path.realpath(source_path)
                if os.path.commonpath([pending_root, resolved_source]) != pending_root:
                    move_failures.append(
                        f"Rejected unsafe pending artifact path: {source_path}"
                    )
                    continue
                destination_path = os.path.join(quarantine_path, entry_name)
                shutil.move(source_path, destination_path)
                moved_paths.append(destination_path)
            except (OSError, shutil.Error, ValueError) as error:
                move_failures.append(f"{source_path}: {error}")

    # The active marker must not survive even when moving another artifact fails.
    try:
        if os.path.isfile(PENDING_RESTORE_REQUEST_PATH):
            os.remove(PENDING_RESTORE_REQUEST_PATH)
    except OSError as error:
        move_failures.append(
            f"Could not remove active pending-restore marker: {error}"
        )

    report_path = os.path.join(quarantine_path, "restore_error_report.txt")
    report_lines = [
        "HIRA MODEL SCHOOL - PENDING RESTORE QUARANTINE REPORT",
        f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Validation stage: {validation_stage}",
        f"Failure reason: {failure_reason}",
        "Live database changed: NO",
        "The existing school database was not modified.",
        "Source paths:",
    ]
    for source_path in source_paths or ():
        report_lines.append(f"- {source_path}")
    report_lines.append("Quarantined artifacts:")
    report_lines.extend(f"- {path}" for path in moved_paths)
    if move_failures:
        report_lines.append("Quarantine warnings:")
        report_lines.extend(f"- {message}" for message in move_failures)
    with open(report_path, "w", encoding="utf-8") as report_file:
        report_file.write("\n".join(report_lines) + "\n")

    return {
        "success": True,
        "restore_processed": False,
        "restore_quarantined": True,
        "startup_warning": True,
        "message": (
            "A pending restore request was invalid and has been quarantined.\n\n"
            "The existing school database was not changed.\n\n"
            f"Quarantine: {quarantine_path}"
        ),
        "restart_required": False,
        "quarantine_path": quarantine_path,
        "quarantine_report": report_path,
    }


def _checkpoint_and_clear_live_database_sidecars() -> None:
    """Make the live main file self-contained before atomic replacement.

    A previous crash can leave committed pages in ``school.db-wal``. Replacing
    only ``school.db`` while those sidecars survive may apply stale pages to the
    restored database on its next open. Startup has no normal database
    connections yet, so checkpoint the live database, require a non-busy WAL
    result, close the handle, and then remove both sidecars before replacement.
    If any step fails, the restore stops while the original main database still
    contains all committed pages.
    """
    if not os.path.isfile(DATABASE_PATH):
        return

    connection = None
    try:
        connection = sqlite3.connect(DATABASE_PATH, timeout=10.0)
        checkpoint_row = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE);"
        ).fetchone()
        if checkpoint_row and int(checkpoint_row[0] or 0) != 0:
            raise RuntimeError(
                "The live database WAL is busy and could not be checkpointed."
            )
        connection.commit()
    except (sqlite3.DatabaseError, OSError) as error:
        raise RuntimeError(
            "The live database could not be checkpointed before restore: "
            f"{error}"
        ) from error
    finally:
        if connection is not None:
            connection.close()

    for suffix in ("-wal", "-shm"):
        sidecar_path = f"{DATABASE_PATH}{suffix}"
        try:
            if os.path.exists(sidecar_path):
                os.remove(sidecar_path)
        except OSError as error:
            raise RuntimeError(
                "A stale SQLite sidecar could not be removed before restore: "
                f"{sidecar_path}: {error}"
            ) from error


def process_pending_restore_before_startup() -> dict:
    """Complete a staged encrypted or legacy restore before database startup."""
    request_exists = os.path.isfile(PENDING_RESTORE_REQUEST_PATH)
    staged_candidates_exist = any(
        os.path.isfile(candidate)
        for candidate in (
            PENDING_RESTORE_ENCRYPTED_PATH,
            PENDING_RESTORE_DATABASE_PATH,
        )
    )
    if not request_exists and not staged_candidates_exist:
        return {
            "success": True,
            "restore_processed": False,
            "message": "No pending database restore was found.",
            "restart_required": False,
        }
    if not request_exists:
        return _quarantine_invalid_pending_restore(
            "The pending restore files are incomplete.",
            "request_presence",
            source_paths=(
                PENDING_RESTORE_REQUEST_PATH,
                PENDING_RESTORE_ENCRYPTED_PATH,
                PENDING_RESTORE_DATABASE_PATH,
            ),
        )

    try:
        with open(PENDING_RESTORE_REQUEST_PATH, "r", encoding="utf-8") as request_file:
            restore_request = json.load(request_file)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        return _quarantine_invalid_pending_restore(
            f"The pending restore request could not be read: {error}",
            "request_parse",
            source_paths=(PENDING_RESTORE_REQUEST_PATH,),
        )

    if not isinstance(restore_request, dict):
        return _quarantine_invalid_pending_restore(
            "The pending restore request has an invalid format.",
            "request_structure",
            source_paths=(PENDING_RESTORE_REQUEST_PATH,),
        )

    backup_format = str(
        restore_request.get("backup_format", "legacy") or ""
    ).strip().lower()
    if backup_format not in {"encrypted", "legacy"}:
        return _quarantine_invalid_pending_restore(
            f"Unsupported pending restore backup format: {backup_format or '-'}.",
            "backup_format_validation",
            source_paths=(PENDING_RESTORE_REQUEST_PATH,),
        )
    expected_staged_path = (
        PENDING_RESTORE_ENCRYPTED_PATH
        if backup_format == "encrypted"
        else PENDING_RESTORE_DATABASE_PATH
    )
    recorded_staged_path = os.path.abspath(
        str(restore_request.get("staged_backup_path", ""))
    )
    if recorded_staged_path != os.path.abspath(expected_staged_path):
        return _quarantine_invalid_pending_restore(
            "The pending restore request refers to an unexpected backup location.",
            "staged_path_validation",
            source_paths=(recorded_staged_path, expected_staged_path),
        )
    if not os.path.isfile(expected_staged_path):
        return _quarantine_invalid_pending_restore(
            "The staged backup file is missing.",
            "staged_file_presence",
            source_paths=(expected_staged_path,),
        )

    request_type = str(
        restore_request.get("request_type", "normal_restore")
    ).strip()
    if request_type not in {"normal_restore", "reverse_restore"}:
        return _quarantine_invalid_pending_restore(
            f"Unsupported pending restore request type: {request_type or '-'}.",
            "request_type_validation",
            source_paths=(PENDING_RESTORE_REQUEST_PATH, expected_staged_path),
        )

    # The original source was path-validated, authenticated, and copied into
    # the protected pending directory during staging. It may legitimately be a
    # removable drive or synchronized folder that is unavailable after the
    # required restart. Startup therefore treats the fixed staged artifact as
    # authoritative and retains the original path only as audit metadata.
    original_path = str(restore_request.get("original_backup_path", ""))

    database_directory = os.path.dirname(DATABASE_PATH)
    os.makedirs(database_directory, exist_ok=True)
    os.makedirs(ENCRYPTED_BACKUP_TEMP_DIRECTORY, exist_ok=True)
    temporary_live_database_path = os.path.join(
        database_directory,
        f"school_startup_restore_{uuid4().hex}.db",
    )

    try:
        if backup_format == "encrypted":
            decrypt_result = decrypt_encrypted_backup_to_path(
                expected_staged_path,
                temporary_live_database_path,
                protected_master_key=restore_request.get(
                    "pending_protected_master_key"
                ),
            )
            if not decrypt_result.get("success", False):
                return _quarantine_invalid_pending_restore(
                    "The pending encrypted backup failed authentication. "
                    f"{decrypt_result.get('message', '')}",
                    "encrypted_backup_authentication",
                    source_paths=(expected_staged_path, original_path),
                )
        else:
            shutil.copy2(expected_staged_path, temporary_live_database_path)

        temporary_valid, temporary_message = (
            migrate_and_validate_school_database_copy(
                temporary_live_database_path
            )
        )
        if not temporary_valid:
            return _quarantine_invalid_pending_restore(
                "The temporary startup restore database failed verification. "
                f"{temporary_message}",
                "temporary_database_validation",
                source_paths=(expected_staged_path, original_path),
            )

        pre_restore_result = create_pre_restore_backup()
        if not pre_restore_result.get("success", False):
            return {
                "success": False,
                "restore_processed": False,
                "message": (
                    "The pending restore was cancelled because the current live "
                    "database could not be backed up safely.\n\n"
                    f"{pre_restore_result.get('message', '')}"
                ),
                "restart_required": False,
            }

        pre_restore_backup_path = pre_restore_result.get("backup_path")

        # The pre-restore backup already contains every committed WAL page.
        # Make the live main file self-contained and remove stale sidecars
        # before replacing it with the fully validated temporary database.
        _checkpoint_and_clear_live_database_sidecars()

        restore_operation = None
        previous_restore_state = load_restore_state()
        restore_state_changed = False

        # Restore history is committed before the live database replacement.
        # Therefore, a state-write failure cancels the restore while the live
        # database is still untouched. If the database replacement itself
        # fails, the previous state is restored atomically.
        if request_type == "normal_restore":
            if restore_request.get("record_history", True):
                restore_operation = record_restore_operation(
                    selected_backup_path=restore_request.get(
                        "original_backup_path",
                        expected_staged_path,
                    ),
                    pre_restore_backup_path=pre_restore_backup_path,
                )
                restore_state_changed = True
        elif request_type == "reverse_restore":
            reverse_mark_result = mark_last_restore_reversed(
                reverse_safety_backup_path=pre_restore_backup_path,
                target_restore_id=restore_request.get("target_restore_id"),
            )
            if not reverse_mark_result.get("success", False):
                raise RuntimeError(
                    reverse_mark_result.get(
                        "message",
                        "Reverse-restore history could not be updated.",
                    )
                )
            restore_operation = reverse_mark_result.get("restore_operation")
            restore_state_changed = True
        else:
            raise ValueError("Unsupported pending restore request type.")

        if not _path_is_inside_directory(expected_staged_path, PENDING_RESTORE_DIRECTORY):
            raise RuntimeError("The staged restore path failed final containment validation.")

        try:
            os.replace(temporary_live_database_path, DATABASE_PATH)
        except Exception:
            if restore_state_changed:
                save_restore_state(previous_restore_state)
            raise

        for completed_path in (
            PENDING_RESTORE_REQUEST_PATH,
            PENDING_RESTORE_ENCRYPTED_PATH,
            PENDING_RESTORE_DATABASE_PATH,
        ):
            try:
                if os.path.isfile(completed_path):
                    os.remove(completed_path)
            except OSError:
                pass

        return {
            "success": True,
            "restore_processed": True,
            "message": (
                "The pending database restore was completed successfully before "
                "startup."
            ),
            "restart_required": False,
            "pre_restore_backup_path": pre_restore_backup_path,
            "restore_operation": restore_operation,
        }
    except (OSError, shutil.Error, TypeError, ValueError, RuntimeError) as error:
        return {
            "success": False,
            "restore_processed": False,
            "message": (
                "The pending startup restore could not be completed.\n\n"
                "The protected pre-restore backup remains available.\n\n"
                f"Technical details: {error}"
            ),
            "restart_required": False,
        }
    finally:
        try:
            if os.path.isfile(temporary_live_database_path):
                os.remove(temporary_live_database_path)
        except OSError:
            pass

