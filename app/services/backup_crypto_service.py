"""Authenticated encrypted-backup container and Windows key protection.

Only backup copies are encrypted.  The live SQLite database remains at the
existing runtime location.  Local automatic operation uses Windows DPAPI;
portable recovery uses the administrator's recovery password.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.config import APP_DATA_DIR, BACKUPS_DIR


ENCRYPTED_BACKUP_EXTENSION = ".hmsbackup"
LEGACY_BACKUP_EXTENSION = ".db"
BACKUP_MAGIC = b"HMSBKP01"
BACKUP_FORMAT_VERSION = 3
LEGACY_SUPPORTED_BACKUP_FORMAT_VERSIONS = {1, 2}
BACKUP_CHUNK_SIZE = 4 * 1024 * 1024
BACKUP_CHUNK_RECORD_DATA = 1
BACKUP_CHUNK_RECORD_MANIFEST = 2
BACKUP_CHUNK_RECORD_HEADER = struct.Struct(">BQI")
MAX_HEADER_SIZE = 1024 * 1024
KEY_STORE_FORMAT_VERSION = 2
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
DPAPI_ENTROPY = b"HiraModelSchool.BackupKey.v1"
SECURITY_DIRECTORY = os.path.join(APP_DATA_DIR, "security")
KEY_STORE_PATH = os.path.join(SECURITY_DIRECTORY, "backup_key_store.json")
TEMP_DIRECTORY = os.path.join(BACKUPS_DIR, "temporary_plaintext")


class BackupSecurityError(RuntimeError):
    """Controlled backup-encryption or key-management failure."""


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, field_name: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except Exception as error:
        raise BackupSecurityError(
            f"Encrypted backup field '{field_name}' is invalid."
        ) from error


def _atomic_write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary_path = f"{path}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as output_file:
            json.dump(data, output_file, indent=4, sort_keys=True)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            if os.path.isfile(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass
        raise


def _test_dpapi_key() -> bytes | None:
    """Return a deterministic test-only key on non-Windows test systems."""
    configured = os.environ.get("HMS_BACKUP_TEST_DPAPI_KEY", "")
    if not configured:
        return None
    return hashlib.sha256(configured.encode("utf-8")).digest()


def _protect_bytes(data: bytes) -> bytes:
    if os.name != "nt":
        test_key = _test_dpapi_key()
        if test_key is None:
            raise BackupSecurityError(
                "Windows key protection is unavailable on this operating system."
            )
        nonce = os.urandom(12)
        return b"TEST" + nonce + AESGCM(test_key).encrypt(
            nonce,
            data,
            DPAPI_ENTROPY,
        )

    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    data_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(DPAPI_ENTROPY)
    input_blob = DATA_BLOB(
        len(data),
        ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    entropy_blob = DATA_BLOB(
        len(DPAPI_ENTROPY),
        ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DATA_BLOB()

    success = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Hira Model School Backup Key",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    if not success:
        raise BackupSecurityError("Windows could not protect the backup key.")

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_bytes(protected_data: bytes) -> bytes:
    if os.name != "nt":
        test_key = _test_dpapi_key()
        if test_key is None:
            raise BackupSecurityError(
                "Windows key protection is unavailable on this operating system."
            )
        if not protected_data.startswith(b"TEST") or len(protected_data) < 16:
            raise BackupSecurityError("The protected backup key is invalid.")
        nonce = protected_data[4:16]
        try:
            return AESGCM(test_key).decrypt(
                nonce,
                protected_data[16:],
                DPAPI_ENTROPY,
            )
        except Exception as error:
            raise BackupSecurityError(
                "The protected backup key could not be opened on this computer."
            ) from error

    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL

    data_buffer = ctypes.create_string_buffer(protected_data)
    entropy_buffer = ctypes.create_string_buffer(DPAPI_ENTROPY)
    input_blob = DATA_BLOB(
        len(protected_data),
        ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    entropy_blob = DATA_BLOB(
        len(DPAPI_ENTROPY),
        ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = DATA_BLOB()
    description = wintypes.LPWSTR()

    success = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    )
    if not success:
        raise BackupSecurityError(
            "The protected backup key is not available to this Windows account."
        )

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(output_blob.pbData)


def validate_recovery_password_policy(
    password: str,
    confirmation: str | None = None,
) -> tuple[bool, str]:
    if not isinstance(password, str):
        return False, "Enter a backup recovery password."
    if password != password.strip():
        return False, "Leading or trailing spaces are not allowed."
    if len(password) < 12:
        return False, "The recovery password must contain at least 12 characters."
    if len(password) > 128:
        return False, "The recovery password cannot exceed 128 characters."
    if not any(character.isupper() for character in password):
        return False, "Include at least one uppercase letter."
    if not any(character.islower() for character in password):
        return False, "Include at least one lowercase letter."
    if not any(character.isdigit() for character in password):
        return False, "Include at least one number."
    if not any(not character.isalnum() for character in password):
        return False, "Include at least one symbol."
    weak_values = {
        "password123!",
        "admin123456!",
        "school123456!",
        "hiramodelschool1!",
    }
    if password.lower() in weak_values:
        return False, "Choose a less predictable recovery password."
    if confirmation is not None and password != confirmation:
        return False, "The recovery-password entries do not match."
    return True, "The recovery password meets the required policy."


def _derive_recovery_root(
    password: str,
    recovery_salt: bytes,
    *,
    n: int = SCRYPT_N,
    r: int = SCRYPT_R,
    p: int = SCRYPT_P,
) -> bytes:
    n, r, p = int(n), int(r), int(p)
    if n < 2 ** 14 or n > 2 ** 20 or n & (n - 1) or not 1 <= r <= 32 or not 1 <= p <= 16:
        raise BackupSecurityError("The backup contains unsupported password-security parameters.")
    return Scrypt(
        salt=recovery_salt,
        length=32,
        n=n,
        r=r,
        p=p,
    ).derive(password.encode("utf-8"))


def _recovery_verifier(recovery_root: bytes) -> bytes:
    return hmac.new(
        recovery_root,
        b"HMS backup recovery verifier v1",
        hashlib.sha256,
    ).digest()

def _build_key_store_v2(
    key_id: str,
    master_key: bytes,
    recovery_password: str,
    created_at: str | None = None,
) -> dict:
    """Build a Windows-protected key store for format version 2.

    The recovery password is never written in readable form. It is protected
    with Windows DPAPI so automatic backups can derive a fresh Scrypt key from
    a unique salt for every backup container.
    """
    if len(master_key) != 32:
        raise BackupSecurityError("The backup master key has an invalid length.")
    password_bytes = recovery_password.encode("utf-8")
    key_store_salt = os.urandom(16)
    verifier_root = _derive_recovery_root(recovery_password, key_store_salt)
    return {
        "format_version": KEY_STORE_FORMAT_VERSION,
        "key_id": str(key_id),
        "created_at": created_at
        or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "key_store_salt": _b64encode(key_store_salt),
        "scrypt_n": SCRYPT_N,
        "scrypt_r": SCRYPT_R,
        "scrypt_p": SCRYPT_P,
        "recovery_verifier": _b64encode(_recovery_verifier(verifier_root)),
        "protected_master_key": _b64encode(_protect_bytes(master_key)),
        "protected_recovery_password": _b64encode(
            _protect_bytes(password_bytes)
        ),
    }


def _validate_key_store_v2_password(
    key_store: dict,
    recovery_password: str,
) -> None:
    key_store_salt = _b64decode(
        key_store["key_store_salt"],
        "key_store_salt",
    )
    verifier_root = _derive_recovery_root(
        recovery_password,
        key_store_salt,
        n=key_store.get("scrypt_n", SCRYPT_N),
        r=key_store.get("scrypt_r", SCRYPT_R),
        p=key_store.get("scrypt_p", SCRYPT_P),
    )
    expected_verifier = _b64decode(
        key_store["recovery_verifier"],
        "recovery_verifier",
    )
    if not hmac.compare_digest(
        _recovery_verifier(verifier_root),
        expected_verifier,
    ):
        raise BackupSecurityError(
            "The protected Backup Recovery Password could not be authenticated."
        )



def configure_backup_security(password: str, confirmation: str) -> dict:
    valid, message = validate_recovery_password_policy(password, confirmation)
    if not valid:
        return {"success": False, "message": message}
    if os.path.isfile(KEY_STORE_PATH):
        return {
            "success": False,
            "message": (
                "Backup encryption is already configured. Existing key material "
                "was not replaced."
            ),
        }

    master_key = os.urandom(32)
    key_id = str(uuid4())
    try:
        key_store = _build_key_store_v2(
            key_id,
            master_key,
            password,
        )
        _atomic_write_json(KEY_STORE_PATH, key_store)
    except Exception as error:
        return {
            "success": False,
            "message": f"Backup encryption could not be configured: {error}",
        }
    return {
        "success": True,
        "message": (
            "Backup encryption was configured successfully. Keep the recovery "
            "password in a secure location."
        ),
        "key_id": key_id,
    }

def upgrade_backup_security(recovery_password: str) -> dict:
    """Upgrade a version-1 key store to per-backup Scrypt salts.

    Existing encrypted backups remain readable. The master key and key ID are
    retained, while future backups use container format version 2.
    """
    try:
        key_store = _load_key_store()
        version = int(key_store.get("format_version", 0))
        if version == KEY_STORE_FORMAT_VERSION:
            return {
                "success": True,
                "upgraded": False,
                "message": "Backup encryption already uses the current format.",
            }
        if version != 1:
            raise BackupSecurityError(
                "The existing backup key store cannot be upgraded."
            )
        recovery_salt = _b64decode(
            key_store["recovery_salt"],
            "recovery_salt",
        )
        recovery_root = _derive_recovery_root(
            recovery_password,
            recovery_salt,
        )
        expected_verifier = _b64decode(
            key_store["recovery_verifier"],
            "recovery_verifier",
        )
        if not hmac.compare_digest(
            _recovery_verifier(recovery_root),
            expected_verifier,
        ):
            raise BackupSecurityError(
                "The Backup Recovery Password is incorrect."
            )
        master_key = _unprotect_bytes(
            _b64decode(
                key_store["protected_master_key"],
                "protected_master_key",
            )
        )
        upgraded_store = _build_key_store_v2(
            str(key_store["key_id"]),
            master_key,
            recovery_password,
            created_at=str(
                key_store.get("created_at")
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ),
        )
        upgraded_store["upgraded_at"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        _atomic_write_json(KEY_STORE_PATH, upgraded_store)
        return {
            "success": True,
            "upgraded": True,
            "message": (
                "Backup encryption was upgraded successfully. Future backups "
                "will use a unique recovery salt."
            ),
        }
    except Exception as error:
        return {
            "success": False,
            "upgraded": False,
            "message": f"Backup encryption could not be upgraded: {error}",
        }




def _load_key_store() -> dict:
    if not os.path.isfile(KEY_STORE_PATH):
        raise BackupSecurityError("Backup encryption is not configured.")
    try:
        with open(KEY_STORE_PATH, "r", encoding="utf-8") as key_file:
            data = json.load(key_file)
    except Exception as error:
        raise BackupSecurityError("The backup key store could not be read.") from error
    if not isinstance(data, dict):
        raise BackupSecurityError("The backup key store has an invalid format.")
    version = int(data.get("format_version", 0))
    if version == 1:
        required = {
            "key_id",
            "recovery_salt",
            "recovery_verifier",
            "protected_master_key",
            "protected_recovery_root",
        }
    elif version == KEY_STORE_FORMAT_VERSION:
        required = {
            "key_id",
            "key_store_salt",
            "recovery_verifier",
            "protected_master_key",
            "protected_recovery_password",
        }
    else:
        raise BackupSecurityError(
            "The backup key store has an unsupported format."
        )
    if not required.issubset(data):
        raise BackupSecurityError("The backup key store is incomplete.")
    return data



def get_backup_security_status() -> dict:
    if not os.path.isfile(KEY_STORE_PATH):
        return {
            "configured": False,
            "usable": False,
            "upgrade_required": False,
            "status_text": "Not Configured",
            "message": "Create a Backup Recovery Password before making new backups.",
            "key_id": None,
        }
    try:
        key_id, master_key, recovery_material, material_type = (
            _load_local_key_material()
        )
        if len(master_key) != 32:
            raise BackupSecurityError(
                "The protected backup key has an invalid length."
            )
        upgrade_required = material_type == "legacy_root"
        return {
            "configured": True,
            "usable": True,
            "upgrade_required": upgrade_required,
            "status_text": (
                "Configured — Upgrade Required"
                if upgrade_required
                else "Configured"
            ),
            "message": (
                "Re-enter the Backup Recovery Password once to enable a unique "
                "Scrypt recovery salt for every new backup."
                if upgrade_required
                else "AES-256-GCM with password recovery and Windows protection."
            ),
            "key_id": key_id,
        }
    except Exception as error:
        return {
            "configured": True,
            "usable": False,
            "upgrade_required": False,
            "status_text": "Configured — Key Unavailable",
            "message": str(error),
            "key_id": None,
        }



def is_backup_security_configured() -> bool:
    status = get_backup_security_status()
    return bool(status.get("configured") and status.get("usable"))


def _load_local_key_material() -> tuple[str, bytes, object, str]:
    """Return local key material and its recovery-material type."""
    key_store = _load_key_store()
    master_key = _unprotect_bytes(
        _b64decode(key_store["protected_master_key"], "protected_master_key")
    )
    if len(master_key) != 32:
        raise BackupSecurityError("The protected backup key has an invalid length.")

    version = int(key_store.get("format_version", 0))
    if version == 1:
        recovery_root = _unprotect_bytes(
            _b64decode(
                key_store["protected_recovery_root"],
                "protected_recovery_root",
            )
        )
        if len(recovery_root) != 32:
            raise BackupSecurityError(
                "The protected backup recovery key has an invalid length."
            )
        return key_store["key_id"], master_key, recovery_root, "legacy_root"

    protected_password = _unprotect_bytes(
        _b64decode(
            key_store["protected_recovery_password"],
            "protected_recovery_password",
        )
    )
    try:
        recovery_password = protected_password.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BackupSecurityError(
            "The protected Backup Recovery Password is invalid."
        ) from error
    _validate_key_store_v2_password(key_store, recovery_password)
    return key_store["key_id"], master_key, recovery_password, "password"



def is_encrypted_backup(backup_path: str) -> bool:
    if not backup_path or not os.path.isfile(backup_path):
        return False
    try:
        with open(backup_path, "rb") as backup_file:
            return backup_file.read(len(BACKUP_MAGIC)) == BACKUP_MAGIC
    except OSError:
        return False


def _read_container_header(backup_path: str) -> tuple[dict, bytes, int]:
    """Read only the encrypted header and payload length for cheap inspection."""
    try:
        with open(backup_path, "rb") as backup_file:
            magic = backup_file.read(len(BACKUP_MAGIC))
            if magic != BACKUP_MAGIC:
                raise BackupSecurityError("The selected file is not an encrypted HMS backup.")
            header_size_bytes = backup_file.read(4)
            if len(header_size_bytes) != 4:
                raise BackupSecurityError("The encrypted backup header is incomplete.")
            header_size = struct.unpack(">I", header_size_bytes)[0]
            if header_size < 2 or header_size > MAX_HEADER_SIZE:
                raise BackupSecurityError("The encrypted backup header size is invalid.")
            header_bytes = backup_file.read(header_size)
            if len(header_bytes) != header_size:
                raise BackupSecurityError("The encrypted backup header is incomplete.")
            payload_start = backup_file.tell()
            backup_file.seek(0, os.SEEK_END)
            payload_length = backup_file.tell() - payload_start
    except OSError as error:
        raise BackupSecurityError(f"The encrypted backup could not be read: {error}") from error
    if payload_length <= 0:
        raise BackupSecurityError("The encrypted backup payload is missing.")
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as error:
        raise BackupSecurityError("The encrypted backup header is invalid.") from error
    if not isinstance(header, dict):
        raise BackupSecurityError("The encrypted backup header is invalid.")
    try:
        format_version = int(header.get("format_version", 0))
    except (TypeError, ValueError) as error:
        raise BackupSecurityError("The encrypted backup version is not supported.") from error
    if format_version not in LEGACY_SUPPORTED_BACKUP_FORMAT_VERSIONS | {BACKUP_FORMAT_VERSION}:
        raise BackupSecurityError("The encrypted backup version is not supported.")
    return header, header_bytes, payload_length


def _read_container(backup_path: str) -> tuple[dict, bytes, bytes]:
    header, header_bytes, payload_length = _read_container_header(backup_path)
    if int(header.get("format_version", 0)) == BACKUP_FORMAT_VERSION:
        raise BackupSecurityError(
            "Chunked encrypted backups must be processed using the streaming reader."
        )
    try:
        with open(backup_path, "rb") as backup_file:
            backup_file.seek(len(BACKUP_MAGIC) + 4 + len(header_bytes))
            ciphertext = backup_file.read()
    except OSError as error:
        raise BackupSecurityError(f"The encrypted backup could not be read: {error}") from error
    if len(ciphertext) != payload_length:
        raise BackupSecurityError("The encrypted backup payload is truncated.")
    return header, header_bytes, ciphertext


def read_encrypted_backup_header(backup_path: str) -> dict:
    header, _, _ = _read_container_header(backup_path)
    return header


BACKUP_INSPECTION_VALID_LOCAL_KEY = "VALID_LOCAL_KEY"
BACKUP_INSPECTION_PASSWORD_REQUIRED = "PASSWORD_REQUIRED"
BACKUP_INSPECTION_CORRUPTED_HEADER = "CORRUPTED_HEADER"
BACKUP_INSPECTION_AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
BACKUP_INSPECTION_UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
BACKUP_INSPECTION_NOT_ENCRYPTED = "NOT_ENCRYPTED"

_REQUIRED_ENCRYPTED_HEADER_FIELDS = {
    "format_version", "algorithm", "key_id", "recovery_salt",
    "recovery_verifier", "backup_salt", "recovery_nonce",
    "wrapped_master_key", "data_key_nonce", "wrapped_data_key",
    "payload_nonce",
}

_REQUIRED_CHUNKED_HEADER_FIELDS = {
    "format_version", "algorithm", "key_id", "recovery_salt",
    "recovery_verifier", "backup_salt", "recovery_nonce",
    "wrapped_master_key", "data_key_nonce", "wrapped_data_key",
    "chunk_size", "chunk_count", "database_size", "nonce_prefix",
}


def inspect_encrypted_backup(backup_path: str) -> dict:
    """Classify encrypted backup metadata without broad exception guessing."""
    if not is_encrypted_backup(backup_path):
        return {
            "classification": BACKUP_INSPECTION_NOT_ENCRYPTED,
            "message": "The file is not an encrypted HMS backup.",
            "header": None,
        }
    try:
        header, _header_bytes, payload_length = _read_container_header(backup_path)
    except BackupSecurityError as error:
        message = str(error)
        classification = (
            BACKUP_INSPECTION_UNSUPPORTED_FORMAT
            if "version is not supported" in message.lower()
            else BACKUP_INSPECTION_CORRUPTED_HEADER
        )
        return {"classification": classification, "message": message, "header": None}
    except Exception as error:
        return {
            "classification": BACKUP_INSPECTION_CORRUPTED_HEADER,
            "message": f"Encrypted backup inspection failed: {error}",
            "header": None,
        }

    required_fields = (
        _REQUIRED_CHUNKED_HEADER_FIELDS
        if int(header.get("format_version", 0)) == BACKUP_FORMAT_VERSION
        else _REQUIRED_ENCRYPTED_HEADER_FIELDS
    )
    missing = sorted(required_fields - set(header))
    if missing:
        return {
            "classification": BACKUP_INSPECTION_CORRUPTED_HEADER,
            "message": "Encrypted backup header is incomplete. Missing: " + ", ".join(missing),
            "header": header,
        }
    minimum_payload = (
        BACKUP_CHUNK_RECORD_HEADER.size + 16
        if int(header.get("format_version", 0)) == BACKUP_FORMAT_VERSION
        else 16
    )
    if payload_length < minimum_payload:
        return {
            "classification": BACKUP_INSPECTION_CORRUPTED_HEADER,
            "message": "Encrypted backup ciphertext is truncated.",
            "header": header,
        }
    try:
        local_key_id, _, _, _ = _load_local_key_material()
    except BackupSecurityError:
        local_key_id = None
    if str(header.get("key_id")) != str(local_key_id):
        return {
            "classification": BACKUP_INSPECTION_PASSWORD_REQUIRED,
            "message": "Backup Recovery Password is required for this valid foreign-key backup.",
            "header": header,
        }
    return {
        "classification": BACKUP_INSPECTION_VALID_LOCAL_KEY,
        "message": "Encrypted backup header is valid for the local key.",
        "header": header,
    }


def backup_requires_recovery_password(backup_path: str) -> bool:
    return inspect_encrypted_backup(backup_path)["classification"] == BACKUP_INSPECTION_PASSWORD_REQUIRED



def _wrap_master_key(
    master_key: bytes,
    recovery_root: bytes,
    backup_salt: bytes,
    key_id: str,
) -> tuple[bytes, bytes]:
    wrapping_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=backup_salt,
        info=b"HMS backup master wrapper v1",
    ).derive(recovery_root)
    nonce = os.urandom(12)
    wrapped = AESGCM(wrapping_key).encrypt(
        nonce,
        master_key,
        key_id.encode("utf-8"),
    )
    return nonce, wrapped


def _unwrap_master_key_from_password(
    header: dict,
    recovery_password: str,
) -> bytes:
    recovery_salt = _b64decode(header["recovery_salt"], "recovery_salt")
    recovery_root = _derive_recovery_root(
        recovery_password,
        recovery_salt,
        n=header.get("scrypt_n", SCRYPT_N),
        r=header.get("scrypt_r", SCRYPT_R),
        p=header.get("scrypt_p", SCRYPT_P),
    )
    expected_verifier = _b64decode(
        header["recovery_verifier"],
        "recovery_verifier",
    )
    if not hmac.compare_digest(_recovery_verifier(recovery_root), expected_verifier):
        raise BackupSecurityError(
            "The backup could not be authenticated. The recovery password may be incorrect, or the backup may be damaged."
        )
    backup_salt = _b64decode(header["backup_salt"], "backup_salt")
    wrapping_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=backup_salt,
        info=b"HMS backup master wrapper v1",
    ).derive(recovery_root)
    try:
        return AESGCM(wrapping_key).decrypt(
            _b64decode(header["recovery_nonce"], "recovery_nonce"),
            _b64decode(header["wrapped_master_key"], "wrapped_master_key"),
            str(header["key_id"]).encode("utf-8"),
        )
    except Exception as error:
        raise BackupSecurityError(
            "The backup could not be authenticated. The recovery password may be incorrect, or the backup may be damaged."
        ) from error


def resolve_master_key_for_backup(
    backup_path: str,
    recovery_password: str | None = None,
    protected_master_key: str | None = None,
) -> bytes:
    header = read_encrypted_backup_header(backup_path)
    if protected_master_key:
        master_key = _unprotect_bytes(
            _b64decode(protected_master_key, "pending_protected_master_key")
        )
        if len(master_key) != 32:
            raise BackupSecurityError("The pending restore key is invalid.")
        return master_key
    try:
        local_key_id, local_master_key, _, _ = _load_local_key_material()
        if header.get("key_id") == local_key_id:
            return local_master_key
    except BackupSecurityError:
        pass
    if recovery_password is None:
        raise BackupSecurityError(
            "This backup was created with a different Windows key. Enter the Backup Recovery Password."
        )
    return _unwrap_master_key_from_password(header, recovery_password)



def protect_master_key_for_pending_restore(master_key: bytes) -> str:
    if len(master_key) != 32:
        raise BackupSecurityError("The pending restore key has an invalid length.")
    return _b64encode(_protect_bytes(master_key))


def unprotect_pending_restore_key(protected_key: str) -> bytes:
    """Authenticate and recover a DPAPI-protected pending-restore key."""
    key = _unprotect_bytes(_b64decode(protected_key, "pending_restore_key"))
    if len(key) != 32:
        raise BackupSecurityError("The pending restore key has an invalid length.")
    return key


def protect_local_security_state(value: bytes) -> str:
    """Protect small local security metadata with the current Windows account."""
    return _b64encode(_protect_bytes(bytes(value)))


def unprotect_local_security_state(value: str) -> bytes:
    """Recover small DPAPI-protected local security metadata."""
    return _unprotect_bytes(_b64decode(value, "protected_security_state"))


def _collect_database_manifest(
    database_path: str,
    destination_type: str,
    *,
    database_size: int | None = None,
    database_sha256: str | None = None,
) -> dict:
    if database_size is None:
        database_size = os.path.getsize(database_path)
    if database_sha256 is None:
        digest = hashlib.sha256()
        with open(database_path, "rb") as source:
            for chunk in iter(lambda: source.read(BACKUP_CHUNK_SIZE), b""):
                digest.update(chunk)
        database_sha256 = digest.hexdigest()
    table_names: list[str] = []
    schema_version = 0
    connection = None
    try:
        connection = sqlite3.connect(database_path)
        schema_row = connection.execute("PRAGMA user_version;").fetchone()
        if schema_row:
            schema_version = int(schema_row[0])
        table_names = sorted(
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        )
    finally:
        if connection is not None:
            connection.close()
    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "schema_version": schema_version,
        "original_database_filename": os.path.basename(database_path),
        "database_size": int(database_size),
        "database_sha256": str(database_sha256),
        "destination_type": destination_type,
        "required_school_tables": ["classes", "students"],
        "present_tables": table_names,
    }
    return manifest


def _available_memory_bytes() -> int | None:
    """Return an approximate available-memory value without optional packages."""
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
            return None
        page_size = os.sysconf("SC_PAGE_SIZE")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
        return int(page_size) * int(available_pages)
    except (AttributeError, OSError, ValueError):
        return None


def _validate_streaming_resources(database_path: str, output_path: str) -> None:
    """Fail safely before writing when disk or working memory is insufficient."""
    database_size = os.path.getsize(database_path)
    output_directory = os.path.dirname(os.path.abspath(output_path)) or os.getcwd()
    os.makedirs(output_directory, exist_ok=True)
    free_disk = shutil.disk_usage(output_directory).free
    required_disk = database_size + max(32 * 1024 * 1024, BACKUP_CHUNK_SIZE * 2)
    if free_disk < required_disk:
        raise BackupSecurityError(
            "Insufficient free disk space for a safe encrypted backup operation."
        )
    available_memory = _available_memory_bytes()
    if available_memory is not None and available_memory < BACKUP_CHUNK_SIZE * 4:
        raise BackupSecurityError(
            "Insufficient available memory for the bounded backup encryption buffer."
        )


def _chunk_nonce(nonce_prefix: bytes, index: int) -> bytes:
    if len(nonce_prefix) != 4:
        raise BackupSecurityError("The encrypted backup nonce prefix is invalid.")
    return nonce_prefix + struct.pack(">Q", int(index))


def _write_encrypted_chunk(
    output_file,
    *,
    aesgcm: AESGCM,
    header_bytes: bytes,
    nonce_prefix: bytes,
    record_type: int,
    index: int,
    plaintext: bytes,
) -> None:
    record_header = BACKUP_CHUNK_RECORD_HEADER.pack(
        int(record_type), int(index), len(plaintext)
    )
    ciphertext = aesgcm.encrypt(
        _chunk_nonce(nonce_prefix, index),
        plaintext,
        header_bytes + record_header,
    )
    output_file.write(record_header)
    output_file.write(ciphertext)


def create_encrypted_backup_from_database(
    database_path: str,
    output_path: str,
    destination_type: str,
) -> dict:
    if not os.path.isfile(database_path):
        return {"success": False, "message": "The database snapshot does not exist."}
    try:
        key_id, master_key, recovery_material, material_type = (
            _load_local_key_material()
        )
        if material_type != "password":
            raise BackupSecurityError(
                "Backup encryption must be upgraded before creating new backups."
            )
        recovery_password = str(recovery_material)
        _validate_streaming_resources(database_path, output_path)
        database_size = os.path.getsize(database_path)
        chunk_count = (database_size + BACKUP_CHUNK_SIZE - 1) // BACKUP_CHUNK_SIZE
        data_key = os.urandom(32)

        # A fresh Scrypt salt is generated for every backup. This ensures that
        # identical recovery passwords do not produce the same recovery root
        # across backup containers.
        recovery_salt = os.urandom(16)
        recovery_root = _derive_recovery_root(
            recovery_password,
            recovery_salt,
        )
        backup_salt = os.urandom(16)
        recovery_nonce, wrapped_master_key = _wrap_master_key(
            master_key,
            recovery_root,
            backup_salt,
            key_id,
        )
        data_key_nonce = os.urandom(12)
        wrapped_data_key = AESGCM(master_key).encrypt(
            data_key_nonce,
            data_key,
            key_id.encode("utf-8"),
        )
        nonce_prefix = os.urandom(4)
        header = {
            "format_version": BACKUP_FORMAT_VERSION,
            "algorithm": "AES-256-GCM-CHUNKED",
            "key_id": key_id,
            "recovery_salt": _b64encode(recovery_salt),
            "scrypt_n": SCRYPT_N,
            "scrypt_r": SCRYPT_R,
            "scrypt_p": SCRYPT_P,
            "recovery_verifier": _b64encode(
                _recovery_verifier(recovery_root)
            ),
            "backup_salt": _b64encode(backup_salt),
            "recovery_nonce": _b64encode(recovery_nonce),
            "wrapped_master_key": _b64encode(wrapped_master_key),
            "data_key_nonce": _b64encode(data_key_nonce),
            "wrapped_data_key": _b64encode(wrapped_data_key),
            "chunk_size": BACKUP_CHUNK_SIZE,
            "chunk_count": chunk_count,
            "database_size": database_size,
            "nonce_prefix": _b64encode(nonce_prefix),
        }
        header_bytes = json.dumps(
            header,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        output_directory = os.path.dirname(os.path.abspath(output_path))
        os.makedirs(output_directory, exist_ok=True)
        temporary_output = f"{output_path}.tmp"
        if os.path.exists(temporary_output):
            os.remove(temporary_output)
        with open(temporary_output, "wb") as backup_file:
            backup_file.write(BACKUP_MAGIC)
            backup_file.write(struct.pack(">I", len(header_bytes)))
            backup_file.write(header_bytes)
            digest = hashlib.sha256()
            encrypted = AESGCM(data_key)
            actual_size = 0
            actual_chunks = 0
            with open(database_path, "rb") as database_file:
                while True:
                    chunk = database_file.read(BACKUP_CHUNK_SIZE)
                    if not chunk:
                        break
                    digest.update(chunk)
                    actual_size += len(chunk)
                    _write_encrypted_chunk(
                        backup_file,
                        aesgcm=encrypted,
                        header_bytes=header_bytes,
                        nonce_prefix=nonce_prefix,
                        record_type=BACKUP_CHUNK_RECORD_DATA,
                        index=actual_chunks,
                        plaintext=chunk,
                    )
                    actual_chunks += 1
            if actual_size != database_size or actual_chunks != chunk_count:
                raise BackupSecurityError(
                    "The database changed while its encrypted snapshot was being written."
                )
            manifest = _collect_database_manifest(
                database_path,
                destination_type,
                database_size=actual_size,
                database_sha256=digest.hexdigest(),
            )
            manifest["chunk_count"] = actual_chunks
            manifest_bytes = json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            _write_encrypted_chunk(
                backup_file,
                aesgcm=encrypted,
                header_bytes=header_bytes,
                nonce_prefix=nonce_prefix,
                record_type=BACKUP_CHUNK_RECORD_MANIFEST,
                index=actual_chunks,
                plaintext=manifest_bytes,
            )
            backup_file.flush()
            os.fsync(backup_file.fileno())
        os.replace(temporary_output, output_path)
        return {
            "success": True,
            "message": "Encrypted backup created successfully.",
            "backup_path": output_path,
            "manifest": manifest,
        }
    except Exception as error:
        try:
            for candidate in (output_path, f"{output_path}.tmp"):
                if os.path.isfile(candidate):
                    os.remove(candidate)
        except OSError:
            pass
        return {
            "success": False,
            "message": f"Encrypted backup creation failed: {error}",
            "backup_path": None,
        }



def decrypt_encrypted_backup_to_path(
    backup_path: str,
    output_database_path: str,
    recovery_password: str | None = None,
    protected_master_key: str | None = None,
) -> dict:
    temporary_output = f"{output_database_path}.tmp"
    try:
        header, header_bytes, _payload_length = _read_container_header(backup_path)
        master_key = resolve_master_key_for_backup(
            backup_path,
            recovery_password=recovery_password,
            protected_master_key=protected_master_key,
        )
        try:
            data_key = AESGCM(master_key).decrypt(
                _b64decode(header["data_key_nonce"], "data_key_nonce"),
                _b64decode(header["wrapped_data_key"], "wrapped_data_key"),
                str(header["key_id"]).encode("utf-8"),
            )
        except Exception as error:
            raise BackupSecurityError(
                "The backup could not be authenticated. The recovery password may be incorrect, or the backup may be damaged."
            ) from error
        output_directory = os.path.dirname(os.path.abspath(output_database_path))
        os.makedirs(output_directory, exist_ok=True)
        if os.path.isfile(temporary_output):
            os.remove(temporary_output)

        if int(header.get("format_version", 0)) == BACKUP_FORMAT_VERSION:
            expected_chunk_size = int(header.get("chunk_size", 0))
            expected_chunk_count = int(header.get("chunk_count", -1))
            expected_database_size = int(header.get("database_size", -1))
            if expected_chunk_size < 1 or expected_chunk_size > 64 * 1024 * 1024:
                raise BackupSecurityError("The encrypted backup chunk size is invalid.")
            nonce_prefix = _b64decode(header["nonce_prefix"], "nonce_prefix")
            encrypted = AESGCM(data_key)
            digest = hashlib.sha256()
            written = 0
            expected_index = 0
            manifest = None
            payload_offset = len(BACKUP_MAGIC) + 4 + len(header_bytes)
            with open(backup_path, "rb") as backup_file, open(temporary_output, "wb") as database_file:
                backup_file.seek(payload_offset)
                while True:
                    record_header = backup_file.read(BACKUP_CHUNK_RECORD_HEADER.size)
                    if len(record_header) != BACKUP_CHUNK_RECORD_HEADER.size:
                        raise BackupSecurityError("The encrypted backup chunk stream is truncated.")
                    record_type, record_index, plaintext_length = BACKUP_CHUNK_RECORD_HEADER.unpack(record_header)
                    if record_index != expected_index:
                        raise BackupSecurityError("The encrypted backup chunk order is invalid.")
                    if plaintext_length < 0 or plaintext_length > max(expected_chunk_size, MAX_HEADER_SIZE):
                        raise BackupSecurityError("The encrypted backup chunk length is invalid.")
                    ciphertext = backup_file.read(plaintext_length + 16)
                    if len(ciphertext) != plaintext_length + 16:
                        raise BackupSecurityError("The encrypted backup chunk is truncated.")
                    try:
                        plaintext = encrypted.decrypt(
                            _chunk_nonce(nonce_prefix, record_index),
                            ciphertext,
                            header_bytes + record_header,
                        )
                    except Exception as error:
                        raise BackupSecurityError(
                            "The backup could not be authenticated. The recovery password may be incorrect, or the backup may be damaged."
                        ) from error
                    if record_type == BACKUP_CHUNK_RECORD_DATA:
                        if expected_index >= expected_chunk_count or plaintext_length > expected_chunk_size:
                            raise BackupSecurityError("The encrypted backup contains an invalid data chunk.")
                        database_file.write(plaintext)
                        digest.update(plaintext)
                        written += len(plaintext)
                        expected_index += 1
                        continue
                    if record_type != BACKUP_CHUNK_RECORD_MANIFEST or expected_index != expected_chunk_count:
                        raise BackupSecurityError("The encrypted backup manifest record is misplaced.")
                    try:
                        manifest = json.loads(plaintext.decode("utf-8"))
                    except Exception as error:
                        raise BackupSecurityError("The encrypted backup manifest is invalid.") from error
                    if backup_file.read(1):
                        raise BackupSecurityError("The encrypted backup contains unexpected trailing data.")
                    break
                database_file.flush()
                os.fsync(database_file.fileno())
            if not isinstance(manifest, dict):
                raise BackupSecurityError("The encrypted backup manifest is missing.")
            expected_size = int(manifest.get("database_size", -1))
            expected_hash = str(manifest.get("database_sha256", ""))
            if (
                written != expected_database_size
                or expected_size != written
                or int(manifest.get("chunk_count", -1)) != expected_chunk_count
                or not hmac.compare_digest(expected_hash, digest.hexdigest())
            ):
                raise BackupSecurityError("The encrypted database payload failed verification.")
        else:
            _header, _header_bytes, ciphertext = _read_container(backup_path)
            try:
                plaintext = AESGCM(data_key).decrypt(
                    _b64decode(header["payload_nonce"], "payload_nonce"),
                    ciphertext,
                    header_bytes,
                )
            except Exception as error:
                raise BackupSecurityError(
                    "The backup could not be authenticated. The recovery password may be incorrect, or the backup may be damaged."
                ) from error
            if len(plaintext) < 4:
                raise BackupSecurityError("The decrypted backup payload is incomplete.")
            manifest_length = struct.unpack(">I", plaintext[:4])[0]
            if manifest_length < 2 or 4 + manifest_length > len(plaintext):
                raise BackupSecurityError("The encrypted backup manifest is invalid.")
            manifest_bytes = plaintext[4 : 4 + manifest_length]
            database_bytes = plaintext[4 + manifest_length :]
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except Exception as error:
                raise BackupSecurityError("The encrypted backup manifest is invalid.") from error
            expected_size = int(manifest.get("database_size", -1))
            expected_hash = str(manifest.get("database_sha256", ""))
            actual_hash = hashlib.sha256(database_bytes).hexdigest()
            if expected_size != len(database_bytes) or not hmac.compare_digest(expected_hash, actual_hash):
                raise BackupSecurityError("The encrypted database payload failed verification.")
            with open(temporary_output, "wb") as database_file:
                database_file.write(database_bytes)
                database_file.flush()
                os.fsync(database_file.fileno())
        os.replace(temporary_output, output_database_path)
        return {
            "success": True,
            "message": "Encrypted backup authenticated and decrypted successfully.",
            "database_path": output_database_path,
            "manifest": manifest,
            "_master_key": master_key,
        }
    except Exception as error:
        try:
            if os.path.isfile(temporary_output):
                os.remove(temporary_output)
            if os.path.isfile(output_database_path):
                os.remove(output_database_path)
        except OSError:
            pass
        return {
            "success": False,
            "message": str(error),
            "database_path": None,
        }


def validate_encrypted_backup_detailed(
    backup_path: str,
    recovery_password: str | None = None,
) -> dict:
    """Return a structured encrypted-backup classification and validation result."""
    inspection = inspect_encrypted_backup(backup_path)
    classification = inspection["classification"]
    if classification in {
        BACKUP_INSPECTION_NOT_ENCRYPTED,
        BACKUP_INSPECTION_CORRUPTED_HEADER,
        BACKUP_INSPECTION_UNSUPPORTED_FORMAT,
    }:
        return {
            "success": False,
            "classification": classification,
            "message": inspection["message"],
        }
    if classification == BACKUP_INSPECTION_PASSWORD_REQUIRED and not recovery_password:
        return {
            "success": False,
            "classification": BACKUP_INSPECTION_PASSWORD_REQUIRED,
            "message": inspection["message"],
        }

    os.makedirs(TEMP_DIRECTORY, exist_ok=True)
    temporary_path = os.path.join(TEMP_DIRECTORY, f"validate_{uuid4().hex}.db")
    connection = None
    try:
        result = decrypt_encrypted_backup_to_path(
            backup_path,
            temporary_path,
            recovery_password=recovery_password,
        )
        if not result.get("success"):
            message = str(result.get("message") or "Encrypted backup validation failed.")
            lowered = message.lower()
            if recovery_password and ("password" in lowered or "authenticated" in lowered):
                result_class = BACKUP_INSPECTION_AUTHENTICATION_FAILED
            elif classification == BACKUP_INSPECTION_VALID_LOCAL_KEY:
                result_class = BACKUP_INSPECTION_CORRUPTED_HEADER
            else:
                result_class = BACKUP_INSPECTION_AUTHENTICATION_FAILED
            return {
                "success": False,
                "classification": result_class,
                "message": message,
            }
        connection = sqlite3.connect(temporary_path)
        integrity = connection.execute("PRAGMA integrity_check;").fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
        }
        if not integrity or str(integrity[0]).lower() != "ok":
            return {
                "success": False,
                "classification": BACKUP_INSPECTION_CORRUPTED_HEADER,
                "message": "Decrypted database integrity check failed.",
            }
        missing = {"classes", "students"} - tables
        if missing:
            return {
                "success": False,
                "classification": BACKUP_INSPECTION_CORRUPTED_HEADER,
                "message": "Missing required school tables: " + ", ".join(sorted(missing)),
            }
        return {
            "success": True,
            "classification": BACKUP_INSPECTION_VALID_LOCAL_KEY,
            "message": "Encrypted backup authenticated and school database validated.",
        }
    except sqlite3.DatabaseError as error:
        return {
            "success": False,
            "classification": BACKUP_INSPECTION_CORRUPTED_HEADER,
            "message": f"Decrypted SQLite validation failed: {error}",
        }
    finally:
        if connection is not None:
            connection.close()
        try:
            if os.path.isfile(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass


def validate_encrypted_backup(
    backup_path: str,
    recovery_password: str | None = None,
) -> tuple[bool, str]:
    result = validate_encrypted_backup_detailed(
        backup_path,
        recovery_password=recovery_password,
    )
    return bool(result.get("success")), str(result.get("message") or "")


def adopt_backup_security_from_recovery(
    backup_path: str,
    recovery_password: str,
    master_key: bytes,
) -> dict:
    """Adopt a recovered key when the local key store is absent or unusable."""
    if os.path.isfile(KEY_STORE_PATH):
        current_status = get_backup_security_status()
        if current_status.get("usable", False):
            return {
                "success": True,
                "adopted": False,
                "message": "Existing usable backup encryption configuration was retained.",
            }
    try:
        header = read_encrypted_backup_header(backup_path)
        recovery_salt = _b64decode(header["recovery_salt"], "recovery_salt")
        recovery_root = _derive_recovery_root(
            recovery_password,
            recovery_salt,
            n=header.get("scrypt_n", SCRYPT_N),
            r=header.get("scrypt_r", SCRYPT_R),
            p=header.get("scrypt_p", SCRYPT_P),
        )
        expected_verifier = _b64decode(
            header["recovery_verifier"],
            "recovery_verifier",
        )
        if not hmac.compare_digest(
            _recovery_verifier(recovery_root),
            expected_verifier,
        ):
            raise BackupSecurityError(
                "The backup recovery password could not be authenticated."
            )
        if len(master_key) != 32:
            raise BackupSecurityError("The recovered master key is invalid.")
        key_store = _build_key_store_v2(
            str(header["key_id"]),
            master_key,
            recovery_password,
        )
        key_store["adopted_at"] = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if os.path.isfile(KEY_STORE_PATH):
            damaged_path = KEY_STORE_PATH + datetime.now().strftime(".damaged_%Y%m%d_%H%M%S")
            os.replace(KEY_STORE_PATH, damaged_path)
        _atomic_write_json(KEY_STORE_PATH, key_store)
        return {
            "success": True,
            "adopted": True,
            "message": (
                "The recovered backup key is now protected for this Windows account."
            ),
        }
    except Exception as error:
        return {
            "success": False,
            "adopted": False,
            "message": f"Recovered backup security could not be adopted: {error}",
        }


def recover_backup_security_from_backup(backup_path: str, recovery_password: str) -> dict:
    """Repair a missing/damaged local key store using a valid encrypted backup."""
    try:
        if not is_encrypted_backup(backup_path):
            raise BackupSecurityError("Select an encrypted .hmsbackup file.")
        master_key = resolve_master_key_for_backup(
            backup_path, recovery_password=recovery_password
        )
        return adopt_backup_security_from_recovery(
            backup_path, recovery_password, master_key
        )
    except (OSError, TypeError, ValueError, BackupSecurityError) as error:
        return {
            "success": False,
            "adopted": False,
            "message": f"Backup security could not be recovered: {error}",
        }
