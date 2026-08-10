"""
Single-administrator authentication service.

The school-management application is offline and permits one administrator
credential record. Password and recovery details are never stored as plain
text. The recovery identity is the administrator CNIC (13 digits without
separators) plus date of birth in DD-MM-YYYY format.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime

from app.database import get_connection


AUTHENTICATION_SCHEMA_V10 = True

ADMIN_USER_ID = 1
PASSWORD_MINIMUM_LENGTH = 10
PASSWORD_MAXIMUM_LENGTH = 128
USERNAME_MINIMUM_LENGTH = 3
USERNAME_MAXIMUM_LENGTH = 50
CNIC_DIGITS_REQUIRED = 13

_KDF_NAME = "pbkdf2_sha256"
_KDF_ITERATIONS = 390_000
_SALT_BYTES = 16
_HASH_BYTES = 32
_MIN_ACCEPTED_KDF_ITERATIONS = 100_000
_MAX_ACCEPTED_KDF_ITERATIONS = 2_000_000
_MIN_ACCEPTED_SALT_BYTES = 16
_MAX_ACCEPTED_SALT_BYTES = 64
_MIN_ACCEPTED_HASH_BYTES = 16
_MAX_ACCEPTED_HASH_BYTES = 64
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_CNIC_PATTERN = re.compile(r"^[0-9]{13}$")
_DOB_PATTERN = re.compile(r"^[0-9]{2}-[0-9]{2}-[0-9]{4}$")


def normalize_username(username: str) -> str:
    """Validate and normalize a login username."""
    cleaned = str(username or "").strip()

    if not cleaned:
        raise ValueError("Username is required.")

    if not (
        USERNAME_MINIMUM_LENGTH
        <= len(cleaned)
        <= USERNAME_MAXIMUM_LENGTH
    ):
        raise ValueError(
            "Username must contain between "
            f"{USERNAME_MINIMUM_LENGTH} and "
            f"{USERNAME_MAXIMUM_LENGTH} characters."
        )

    if not _USERNAME_PATTERN.fullmatch(cleaned):
        raise ValueError(
            "Username may contain letters, numbers, dots, "
            "underscores, and hyphens only."
        )

    return cleaned


def validate_password(password: str) -> str:
    """Validate a password without altering its characters."""
    value = str(password or "")

    if len(value) < PASSWORD_MINIMUM_LENGTH:
        raise ValueError(
            "Password must contain at least "
            f"{PASSWORD_MINIMUM_LENGTH} characters."
        )

    if len(value) > PASSWORD_MAXIMUM_LENGTH:
        raise ValueError(
            "Password cannot exceed "
            f"{PASSWORD_MAXIMUM_LENGTH} characters."
        )

    if not re.search(r"[A-Z]", value) or not re.search(r"[a-z]", value) or not re.search(r"[0-9]", value) or not re.search(r"[^A-Za-z0-9]", value):
        raise ValueError(
            "Password must include an uppercase letter, a lowercase letter, "
            "a number, and a special character."
        )

    return value


def _ensure_security_audit_tables(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS authentication_throttle (
            throttle_key TEXT PRIMARY KEY,
            failure_count INTEGER NOT NULL DEFAULT 0,
            locked_until_epoch INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS authentication_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            actor TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            details TEXT
        )
        """
    )


def _throttle_key(purpose: str, identity: str) -> str:
    digest = hashlib.sha256(str(identity or "").strip().lower().encode("utf-8")).hexdigest()
    return f"{purpose}:{digest}"


def _is_throttled(connection, key: str) -> bool:
    _ensure_security_audit_tables(connection)
    row = connection.execute(
        "SELECT locked_until_epoch FROM authentication_throttle WHERE throttle_key=?",
        (key,),
    ).fetchone()
    return bool(row and int(row["locked_until_epoch"] or 0) > int(time.time()))


def _record_auth_result(connection, key: str, *, succeeded: bool) -> None:
    _ensure_security_audit_tables(connection)
    now_epoch = int(time.time())
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if succeeded:
        connection.execute("DELETE FROM authentication_throttle WHERE throttle_key=?", (key,))
        return
    row = connection.execute(
        "SELECT failure_count FROM authentication_throttle WHERE throttle_key=?", (key,)
    ).fetchone()
    failures = int(row["failure_count"] or 0) + 1 if row else 1
    lock_seconds = 300 if failures >= 5 else 0
    connection.execute(
        """INSERT INTO authentication_throttle(throttle_key,failure_count,locked_until_epoch,updated_at)
           VALUES(?,?,?,?) ON CONFLICT(throttle_key) DO UPDATE SET
           failure_count=excluded.failure_count, locked_until_epoch=excluded.locked_until_epoch,
           updated_at=excluded.updated_at""",
        (key, failures, now_epoch + lock_seconds, now_text),
    )


def normalize_cnic(cnic: str) -> str:
    """Require exactly 13 CNIC digits with no dashes or spaces."""
    cleaned = str(cnic or "").strip()

    if not _CNIC_PATTERN.fullmatch(cleaned):
        raise ValueError(
            "CNIC must contain exactly 13 digits without dashes."
        )

    return cleaned


def normalize_date_of_birth(date_of_birth: str) -> str:
    """Require a genuine calendar date in DD-MM-YYYY format."""
    cleaned = str(date_of_birth or "").strip()

    if not _DOB_PATTERN.fullmatch(cleaned):
        raise ValueError(
            "Date of birth must use DD-MM-YYYY format."
        )

    try:
        parsed_date = datetime.strptime(
            cleaned,
            "%d-%m-%Y",
        ).date()

    except ValueError as error:
        raise ValueError(
            "Enter a valid date of birth in DD-MM-YYYY format."
        ) from error

    if parsed_date > datetime.now().date():
        raise ValueError(
            "Date of birth cannot be in the future."
        )

    return parsed_date.strftime("%d-%m-%Y")


def _base64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _base64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))


def _hash_secret(secret_value: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        secret_value.encode("utf-8"),
        salt,
        _KDF_ITERATIONS,
        dklen=_HASH_BYTES,
    )

    return "$".join(
        (
            _KDF_NAME,
            str(_KDF_ITERATIONS),
            _base64_encode(salt),
            _base64_encode(derived_key),
        )
    )


def _verify_secret(secret_value: str, stored_digest: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = (
            str(stored_digest).split("$", 3)
        )

        if algorithm != _KDF_NAME:
            return False

        iterations = int(iterations_text)
        salt = _base64_decode(salt_text)
        expected_digest = _base64_decode(digest_text)

        # A damaged or deliberately modified local database must not be able
        # to turn login into an unbounded CPU or memory operation. Valid
        # historical digests remain accepted inside conservative limits.
        if not (
            _MIN_ACCEPTED_KDF_ITERATIONS
            <= iterations
            <= _MAX_ACCEPTED_KDF_ITERATIONS
        ):
            return False
        if not (
            _MIN_ACCEPTED_SALT_BYTES
            <= len(salt)
            <= _MAX_ACCEPTED_SALT_BYTES
        ):
            return False
        if not (
            _MIN_ACCEPTED_HASH_BYTES
            <= len(expected_digest)
            <= _MAX_ACCEPTED_HASH_BYTES
        ):
            return False

        candidate_digest = hashlib.pbkdf2_hmac(
            "sha256",
            secret_value.encode("utf-8"),
            salt,
            iterations,
            dklen=len(expected_digest),
        )

        return hmac.compare_digest(
            candidate_digest,
            expected_digest,
        )

    except (
        TypeError,
        ValueError,
        base64.binascii.Error,
    ):
        return False


def _build_recovery_secret(
    cnic: str,
    date_of_birth: str,
) -> str:
    normalized_cnic = normalize_cnic(cnic)
    normalized_dob = normalize_date_of_birth(date_of_birth)

    return f"{normalized_cnic}|{normalized_dob}"


def has_admin_credentials() -> bool:
    """Return True only when the single administrator row exists."""
    connection = get_connection(
        automatic_backup=False
    )

    try:
        row = connection.execute(
            """
            SELECT 1
            FROM admin_credentials
            WHERE user_id = ?
            LIMIT 1
            """,
            (ADMIN_USER_ID,),
        ).fetchone()

        return row is not None

    finally:
        connection.close()


def get_admin_username() -> str:
    """Return the configured username, or an empty string before setup."""
    connection = get_connection(
        automatic_backup=False
    )

    try:
        row = connection.execute(
            """
            SELECT username
            FROM admin_credentials
            WHERE user_id = ?
            LIMIT 1
            """,
            (ADMIN_USER_ID,),
        ).fetchone()

        if row is None:
            return ""

        return str(row["username"])

    finally:
        connection.close()


def create_admin_credentials(
    username: str,
    password: str,
    cnic: str,
    date_of_birth: str,
    created_by: str = "first_run_setup",
) -> None:
    """
    Create the one permitted administrator record.

    The single-row preflight and insert occur in one immediate transaction so
    two setup attempts cannot create multiple administrator identities.
    """
    normalized_username = normalize_username(username)
    validated_password = validate_password(password)
    recovery_secret = _build_recovery_secret(
        cnic,
        date_of_birth,
    )

    password_digest = _hash_secret(
        validated_password
    )
    recovery_digest = _hash_secret(
        recovery_secret
    )
    now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE;")

        existing_row = connection.execute(
            """
            SELECT user_id
            FROM admin_credentials
            LIMIT 1
            """
        ).fetchone()

        if existing_row is not None:
            raise ValueError(
                "Administrator credentials have already been created."
            )

        connection.execute(
            """
            INSERT INTO admin_credentials (
                user_id,
                username,
                password_digest,
                recovery_digest,
                created_at,
                created_by,
                updated_at,
                updated_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ADMIN_USER_ID,
                normalized_username,
                password_digest,
                recovery_digest,
                now,
                str(created_by or "first_run_setup"),
                now,
                str(created_by or "first_run_setup"),
            ),
        )

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def authenticate_admin(
    username: str,
    password: str,
) -> bool:
    """Verify username and password without disclosing which field failed."""
    try:
        normalized_username = normalize_username(
            username
        )

    except ValueError:
        return False

    connection = get_connection(
        automatic_backup=False
    )

    try:
        throttle_key = _throttle_key("login", normalized_username)
        if _is_throttled(connection, throttle_key):
            connection.commit()
            return False
        row = connection.execute(
            """
            SELECT username, password_digest
            FROM admin_credentials
            WHERE user_id = ?
              AND username = ? COLLATE NOCASE
            LIMIT 1
            """,
            (
                ADMIN_USER_ID,
                normalized_username,
            ),
        ).fetchone()

        authenticated = bool(row) and _verify_secret(
            str(password or ""), str(row["password_digest"])
        )
        _record_auth_result(connection, throttle_key, succeeded=authenticated)
        connection.execute(
            "INSERT INTO authentication_events(event_type,outcome,actor,occurred_at,details) VALUES(?,?,?,?,?)",
            (
                "login",
                "success" if authenticated else "failure",
                normalized_username,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                None,
            ),
        )
        connection.commit()
        return authenticated

    finally:
        connection.close()


def reset_admin_password(
    cnic: str,
    date_of_birth: str,
    new_password: str,
    updated_by: str = "forgot_password",
) -> bool:
    """
    Reset the password only after CNIC and DOB match the setup identity.

    Returns False for an incorrect recovery identity. Validation problems are
    raised as ValueError so the user interface can display a precise format
    message without revealing stored values.
    """
    recovery_secret = _build_recovery_secret(
        cnic,
        date_of_birth,
    )
    validated_password = validate_password(
        new_password
    )

    connection = get_connection()

    try:
        connection.execute("BEGIN IMMEDIATE;")
        throttle_key = _throttle_key("password_recovery", cnic)
        if _is_throttled(connection, throttle_key):
            connection.commit()
            return False

        row = connection.execute(
            """
            SELECT recovery_digest
            FROM admin_credentials
            WHERE user_id = ?
            LIMIT 1
            """,
            (ADMIN_USER_ID,),
        ).fetchone()

        if row is None:
            _record_auth_result(connection, throttle_key, succeeded=False)
            connection.commit()
            return False

        recovery_matches = _verify_secret(
            recovery_secret,
            str(row["recovery_digest"]),
        )

        if not recovery_matches:
            _record_auth_result(connection, throttle_key, succeeded=False)
            connection.execute(
                "INSERT INTO authentication_events(event_type,outcome,actor,occurred_at,details) VALUES(?,?,?,?,?)",
                ("password_reset", "failure", str(updated_by or "forgot_password"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"), None),
            )
            connection.commit()
            return False

        now = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        connection.execute(
            """
            UPDATE admin_credentials
            SET password_digest = ?,
                updated_at = ?,
                updated_by = ?
            WHERE user_id = ?
            """,
            (
                _hash_secret(validated_password),
                now,
                str(updated_by or "forgot_password"),
                ADMIN_USER_ID,
            ),
        )
        _record_auth_result(connection, throttle_key, succeeded=True)
        connection.execute(
            "INSERT INTO authentication_events(event_type,outcome,actor,occurred_at,details) VALUES(?,?,?,?,?)",
            ("password_reset", "success", str(updated_by or "forgot_password"), now, None),
        )

        connection.commit()
        return True

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


def change_admin_password(
    username: str,
    current_password: str,
    new_password: str,
    updated_by: str = "change_password",
) -> bool:
    """Verify and change the admin password in one write transaction."""
    validated_password = validate_password(new_password)
    normalized_username = normalize_username(username)
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE;")
        row = connection.execute(
            """
            SELECT password_digest
            FROM admin_credentials
            WHERE user_id = ?
              AND username = ? COLLATE NOCASE
            LIMIT 1
            """,
            (ADMIN_USER_ID, normalized_username),
        ).fetchone()
        if row is None:
            _ensure_security_audit_tables(connection)
            connection.execute(
                "INSERT INTO authentication_events(event_type,outcome,actor,occurred_at,details) VALUES(?,?,?,?,?)",
                ("password_change", "failure", normalized_username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Credential not found"),
            )
            connection.commit()
            return False

        original_digest = str(row["password_digest"])
        if not _verify_secret(current_password, original_digest):
            _ensure_security_audit_tables(connection)
            connection.execute(
                "INSERT INTO authentication_events(event_type,outcome,actor,occurred_at,details) VALUES(?,?,?,?,?)",
                ("password_change", "failure", normalized_username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Current password rejected"),
            )
            connection.commit()
            return False

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor = connection.execute(
            """
            UPDATE admin_credentials
            SET password_digest = ?,
                updated_at = ?,
                updated_by = ?
            WHERE user_id = ?
              AND username = ? COLLATE NOCASE
              AND password_digest = ?
            """,
            (
                _hash_secret(validated_password),
                now,
                str(updated_by or "change_password"),
                ADMIN_USER_ID,
                normalized_username,
                original_digest,
            ),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError(
                "Authentication conflict: the password changed during this operation."
            )
        _ensure_security_audit_tables(connection)
        connection.execute(
            "INSERT INTO authentication_events(event_type,outcome,actor,occurred_at,details) VALUES(?,?,?,?,?)",
            ("password_change", "success", normalized_username, now, None),
        )
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
