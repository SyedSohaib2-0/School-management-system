"""
Offline machine-bound licence support.

This module creates a stable machine fingerprint without storing
personal school data. The fingerprint will later be matched against
a digitally signed licence file.
"""

import base64
import ctypes
import hashlib
import json
import os
import platform
import shutil
import uuid
import winreg
from datetime import datetime, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)

from app.config import APP_DATA_DIR
from app.services.backup_crypto_service import (
    BackupSecurityError,
    protect_local_security_state,
    unprotect_local_security_state,
)


PRODUCT_CODE = (
    "HIRA-MODEL-SCHOOL-MANAGEMENT-SYSTEM"
)

LICENSE_DIRECTORY = os.path.join(
    APP_DATA_DIR,
    "license",
)

LICENSE_FILE_PATH = os.path.join(
    LICENSE_DIRECTORY,
    "hira_model_school.lic",
)
APPLICATION_PACKAGE_DIRECTORY = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

LICENSE_PUBLIC_KEY_PATH = os.path.join(
    APPLICATION_PACKAGE_DIRECTORY,
    "license_public_key.pem",
)

MAX_LICENSE_FILE_BYTES = 64 * 1024
TRUSTED_TIME_PATH = os.path.join(LICENSE_DIRECTORY, "last_trusted_time.json")
TRUSTED_TIME_ROLLBACK_TOLERANCE_SECONDS = 300


def _check_and_record_trusted_time(now_utc: datetime) -> str | None:
    """Persist the latest observed UTC time and warn when the clock moves back."""
    previous = None
    state_warning = None
    try:
        with open(TRUSTED_TIME_PATH, "r", encoding="utf-8") as stream:
            stored = json.load(stream)
        protected = stored.get("protected_last_trusted_utc")
        raw_time = (
            unprotect_local_security_state(protected).decode("utf-8")
            if protected
            else stored.get("last_trusted_utc")
        )
        previous = _parse_utc_datetime(raw_time)
    except (
        FileNotFoundError,
        OSError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        BackupSecurityError,
    ):
        previous = None
        if os.path.isfile(TRUSTED_TIME_PATH):
            state_warning = "Warning: the saved trusted licence time is damaged or cannot be authenticated."

    warning = state_warning
    if previous is not None and (previous - now_utc).total_seconds() > TRUSTED_TIME_ROLLBACK_TOLERANCE_SECONDS:
        warning = (
            "Warning: the computer clock is earlier than the last trusted licence time "
            f"({previous.isoformat().replace('+00:00', 'Z')}). Please correct the clock."
        )

    latest = max(now_utc, previous) if previous is not None else now_utc
    try:
        os.makedirs(LICENSE_DIRECTORY, exist_ok=True)
        temporary_path = TRUSTED_TIME_PATH + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as stream:
            trusted_text = latest.isoformat().replace("+00:00", "Z")
            json.dump(
                {"protected_last_trusted_utc": protect_local_security_state(trusted_text.encode("utf-8"))},
                stream,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, TRUSTED_TIME_PATH)
    except OSError:
        warning = warning or "Warning: the last trusted licence time could not be saved."
    return warning


def _read_windows_machine_guid() -> str:
    """Read the Windows MachineGuid without requiring administrator access."""
    registry_path = (
        r"SOFTWARE\Microsoft\Cryptography"
    )

    access_flags = winreg.KEY_READ

    if hasattr(
        winreg,
        "KEY_WOW64_64KEY",
    ):
        access_flags |= (
            winreg.KEY_WOW64_64KEY
        )

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            registry_path,
            0,
            access_flags,
        ) as registry_key:
            machine_guid, _ = (
                winreg.QueryValueEx(
                    registry_key,
                    "MachineGuid",
                )
            )

        return str(
            machine_guid
        ).strip().lower()

    except (
        OSError,
        TypeError,
        ValueError,
    ):
        return ""


def _read_system_volume_serial() -> str:
    """Return the Windows system-drive volume serial number."""
    system_drive = os.environ.get(
        "SystemDrive",
        "C:",
    )

    root_path = (
        system_drive.rstrip(
            "\\/"
        )
        + "\\"
    )

    serial_number = ctypes.c_uint32(
        0
    )

    maximum_component_length = (
        ctypes.c_uint32(
            0
        )
    )

    file_system_flags = ctypes.c_uint32(
        0
    )

    try:
        success = (
            ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(
                    root_path
                ),
                None,
                0,
                ctypes.byref(
                    serial_number
                ),
                ctypes.byref(
                    maximum_component_length
                ),
                ctypes.byref(
                    file_system_flags
                ),
                None,
                0,
            )
        )

        if not success:
            return ""

        return format(
            serial_number.value,
            "08X",
        )

    except (
        AttributeError,
        OSError,
        TypeError,
        ValueError,
    ):
        return ""


def get_machine_identity_components() -> dict:
    """
    Return the stable, non-secret identifiers used for fingerprinting.

    Computer name and network-adapter values are deliberately excluded
    because they may change during ordinary maintenance.
    """
    return {
        "product_code": PRODUCT_CODE,
        "windows_machine_guid": (
            _read_windows_machine_guid()
        ),
        "system_volume_serial": (
            _read_system_volume_serial()
        ),
    }


def get_machine_fingerprint() -> str:
    """
    Create a deterministic SHA-256 machine fingerprint.

    The same computer should produce the same fingerprint on every
    application launch.
    """
    components = (
        get_machine_identity_components()
    )

    fingerprint_source = "|".join(
        (
            f"{key}="
            f"{components.get(key, '')}"
        )
        for key in sorted(
            components
        )
    )

    return hashlib.sha256(
        fingerprint_source.encode(
            "utf-8"
        )
    ).hexdigest()


def get_activation_request_code() -> str:
    """
    Return a readable activation request code derived from the full
    machine fingerprint.
    """
    fingerprint_bytes = bytes.fromhex(
        get_machine_fingerprint()
    )

    encoded_fingerprint = (
        base64.b32encode(
            fingerprint_bytes
        )
        .decode(
            "ascii"
        )
        .rstrip(
            "="
        )
    )

    grouped_code = "-".join(
        encoded_fingerprint[
            index:index + 4
        ]
        for index in range(
            0,
            len(
                encoded_fingerprint
            ),
            4,
        )
    )

    return (
        f"HMS-{grouped_code}"
    )


def machine_fingerprint_from_request_code(
    request_code: str,
) -> str:
    """Validate an activation request code and recover its fingerprint."""
    normalised_code = "".join(
        str(request_code or "").strip().upper().split()
    )
    if not normalised_code.startswith("HMS-"):
        raise ValueError("The activation request code must start with HMS-.")

    encoded_fingerprint = normalised_code[4:].replace("-", "")
    if len(encoded_fingerprint) != 52 or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        for character in encoded_fingerprint
    ):
        raise ValueError("The activation request code is incomplete or invalid.")

    padded_code = encoded_fingerprint + (
        "=" * ((8 - len(encoded_fingerprint) % 8) % 8)
    )
    try:
        fingerprint_bytes = base64.b32decode(
            padded_code,
            casefold=False,
        )
    except (ValueError, TypeError) as error:
        raise ValueError("The activation request code is invalid.") from error

    if len(fingerprint_bytes) != 32:
        raise ValueError("The activation request code has an invalid length.")
    return fingerprint_bytes.hex()

def _canonical_license_payload(
    payload: dict,
) -> bytes:
    """
    Convert licence data into the exact byte format used for signing
    and signature verification.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        ensure_ascii=False,
    ).encode(
        "utf-8"
    )


def _parse_utc_datetime(
    value: str,
) -> datetime:
    """Parse an ISO date-time value and return a UTC-aware datetime."""
    normalised_value = str(
        value
    ).strip()

    if normalised_value.endswith(
        "Z"
    ):
        normalised_value = (
            f"{normalised_value[:-1]}+00:00"
        )

    parsed_value = datetime.fromisoformat(
        normalised_value
    )

    if parsed_value.tzinfo is None:
        parsed_value = parsed_value.replace(
            tzinfo=timezone.utc
        )

    return parsed_value.astimezone(
        timezone.utc
    )


def verify_installed_license() -> dict:
    """
    Verify the installed offline licence.

    Validation includes:
    - Licence file existence and JSON format
    - Ed25519 digital signature
    - Product code
    - Current machine fingerprint
    - Required licence fields
    - Optional expiry date
    """
    activation_request_code = (
        get_activation_request_code()
    )

    if not os.path.isfile(
        LICENSE_PUBLIC_KEY_PATH
    ):
        return {
            "success": False,
            "status": "public_key_missing",
            "message": (
                "The licence verification key is missing from "
                "the application installation."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": None,
        }

    if not os.path.isfile(
        LICENSE_FILE_PATH
    ):
        return {
            "success": False,
            "status": "license_missing",
            "message": (
                "This computer has not been activated."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": None,
        }

    try:
        installed_size = os.path.getsize(
            LICENSE_FILE_PATH
        )
    except OSError as error:
        return {
            "success": False,
            "status": "license_unreadable",
            "message": (
                "The installed licence file could not be inspected.\n\n"
                f"Technical details: {error}"
            ),
            "activation_request_code": activation_request_code,
            "license_payload": None,
        }

    if installed_size > MAX_LICENSE_FILE_BYTES:
        return {
            "success": False,
            "status": "license_too_large",
            "message": (
                "The installed licence file is larger than the safe limit."
            ),
            "activation_request_code": activation_request_code,
            "license_payload": None,
        }

    try:
        with open(
            LICENSE_FILE_PATH,
            "r",
            encoding="utf-8",
        ) as licence_file:
            licence_document = json.load(
                licence_file
            )

    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        return {
            "success": False,
            "status": "license_unreadable",
            "message": (
                "The installed licence file could not be read "
                "or has an invalid format.\n\n"
                f"Technical details: {error}"
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": None,
        }

    if not isinstance(
        licence_document,
        dict,
    ):
        return {
            "success": False,
            "status": "license_invalid",
            "message": (
                "The installed licence file has an invalid structure."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": None,
        }

    payload = licence_document.get(
        "payload"
    )

    encoded_signature = licence_document.get(
        "signature"
    )

    if (
        not isinstance(
            payload,
            dict,
        )
        or not isinstance(
            encoded_signature,
            str,
        )
        or not encoded_signature.strip()
    ):
        return {
            "success": False,
            "status": "license_invalid",
            "message": (
                "The installed licence does not contain the "
                "required signed licence data."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": None,
        }

    required_fields = (
        "license_id",
        "customer_name",
        "product_code",
        "machine_fingerprint",
        "issued_at",
        "license_type",
    )

    missing_fields = [
        field_name
        for field_name in required_fields
        if not str(
            payload.get(
                field_name,
                "",
            )
        ).strip()
    ]

    if missing_fields:
        return {
            "success": False,
            "status": "license_invalid",
            "message": (
                "The installed licence is missing required fields:\n"
                + ", ".join(
                    missing_fields
                )
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": payload,
        }

    try:
        with open(
            LICENSE_PUBLIC_KEY_PATH,
            "rb",
        ) as public_key_file:
            loaded_public_key = (
                serialization.load_pem_public_key(
                    public_key_file.read()
                )
            )

        if not isinstance(
            loaded_public_key,
            Ed25519PublicKey,
        ):
            raise TypeError(
                "The bundled public key is not an Ed25519 key."
            )

        signature_bytes = base64.b64decode(
            encoded_signature,
            validate=True,
        )

        loaded_public_key.verify(
            signature_bytes,
            _canonical_license_payload(
                payload
            ),
        )

    except InvalidSignature:
        return {
            "success": False,
            "status": "signature_invalid",
            "message": (
                "The installed licence signature is invalid. "
                "The licence may have been changed or corrupted."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": payload,
        }

    except (
        OSError,
        TypeError,
        ValueError,
    ) as error:
        return {
            "success": False,
            "status": "verification_error",
            "message": (
                "The licence could not be verified.\n\n"
                f"Technical details: {error}"
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": payload,
        }

    if payload.get(
        "product_code"
    ) != PRODUCT_CODE:
        return {
            "success": False,
            "status": "wrong_product",
            "message": (
                "This licence was issued for a different product."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": payload,
        }

    current_machine_fingerprint = (
        get_machine_fingerprint()
    )

    if payload.get(
        "machine_fingerprint"
    ) != current_machine_fingerprint:
        return {
            "success": False,
            "status": "wrong_machine",
            "message": (
                "This licence belongs to a different computer."
            ),
            "activation_request_code": (
                activation_request_code
            ),
            "license_payload": payload,
        }

       # New licences may contain a starting date. Older licences without
    # this field remain compatible and become valid immediately.
    start_datetime = None

    starts_at = payload.get(
        "starts_at"
    )

    if starts_at:
        try:
            start_datetime = (
                _parse_utc_datetime(
                    starts_at
                )
            )

        except (
            TypeError,
            ValueError,
        ) as error:
            return {
                "success": False,
                "status": "start_date_invalid",
                "message": (
                    "The licence starting date is invalid.\n\n"
                    f"Technical details: {error}"
                ),
                "activation_request_code": (
                    activation_request_code
                ),
                "license_payload": payload,
            }

        if datetime.now(
            timezone.utc
        ) < start_datetime:
            return {
                "success": False,
                "status": "license_not_started",
                "message": (
                    "The software licence is not active yet.\n\n"
                    f"Activation begins: {starts_at}"
                ),
                "activation_request_code": (
                    activation_request_code
                ),
                "license_payload": payload,
            }     

    expires_at = payload.get(
        "expires_at"
    )

    if expires_at:
        try:
            expiry_datetime = (
                _parse_utc_datetime(
                    expires_at
                )
            )

        except (
            TypeError,
            ValueError,
        ) as error:
            return {
                "success": False,
                "status": "expiry_invalid",
                "message": (
                    "The licence expiry date is invalid.\n\n"
                    f"Technical details: {error}"
                ),
                "activation_request_code": (
                    activation_request_code
                ),
                "license_payload": payload,
            }
        if (
            start_datetime is not None
            and expiry_datetime <= start_datetime
        ):
            return {
                "success": False,
                "status": "license_period_invalid",
                "message": (
                    "The licence ending date must be later "
                    "than its starting date."
                ),
                "activation_request_code": (
                    activation_request_code
                ),
                "license_payload": payload,
            }
        if datetime.now(
            timezone.utc
        ) > expiry_datetime:
            return {
                "success": False,
                "status": "license_expired",
                "message": (
                    "The software licence has expired."
                ),
                "activation_request_code": (
                    activation_request_code
                ),
                "license_payload": payload,
            }

    trusted_time_warning = _check_and_record_trusted_time(datetime.now(timezone.utc))
    return {
        "success": True,
        "status": "licensed",
        "message": (
            "The software licence is valid."
            + (f"\n\n{trusted_time_warning}" if trusted_time_warning else "")
        ),
        "warning": trusted_time_warning,
        "activation_request_code": (
            activation_request_code
        ),
        "license_payload": payload,
    }

def install_license_file(
    selected_license_path: str,
) -> dict:
    """
    Install and verify a signed licence file safely.

    An existing valid licence is preserved and restored automatically
    when the selected replacement licence is invalid.
    """
    if not selected_license_path:
        return {
            "success": False,
            "status": "no_file_selected",
            "message": (
                "No licence file was selected."
            ),
        }

    source_path = os.path.abspath(
        selected_license_path
    )

    if not os.path.isfile(
        source_path
    ):
        return {
            "success": False,
            "status": "file_missing",
            "message": (
                "The selected licence file does not exist."
            ),
        }

    if not source_path.lower().endswith(
        ".lic"
    ):
        return {
            "success": False,
            "status": "wrong_file_type",
            "message": (
                "Select a valid .lic licence file."
            ),
        }

    try:
        source_size = os.path.getsize(
            source_path
        )
    except OSError as error:
        return {
            "success": False,
            "status": "file_unreadable",
            "message": (
                "The selected licence file could not be inspected.\n\n"
                f"Technical details: {error}"
            ),
        }

    if source_size > MAX_LICENSE_FILE_BYTES:
        return {
            "success": False,
            "status": "license_too_large",
            "message": (
                "The selected licence file is larger than the safe limit."
            ),
        }

    temporary_path = (
        f"{LICENSE_FILE_PATH}.tmp"
    )

    previous_license_path = (
        f"{LICENSE_FILE_PATH}.previous"
    )

    had_previous_license = os.path.isfile(
        LICENSE_FILE_PATH
    )
    replacement_installed = False

    try:
        os.makedirs(
            LICENSE_DIRECTORY,
            exist_ok=True,
        )
        for cleanup_path in (
            temporary_path,
            previous_license_path,
        ):
            if os.path.isfile(
                cleanup_path
            ):
                os.remove(
                    cleanup_path
                )

        shutil.copy2(
            source_path,
            temporary_path,
        )

        if had_previous_license:
            shutil.copy2(
                LICENSE_FILE_PATH,
                previous_license_path,
            )

        os.replace(
            temporary_path,
            LICENSE_FILE_PATH,
        )
        replacement_installed = True

        verification_result = (
            verify_installed_license()
        )

        if not verification_result.get(
            "success",
            False,
        ):
            try:
                if os.path.isfile(
                    LICENSE_FILE_PATH
                ):
                    os.remove(
                        LICENSE_FILE_PATH
                    )

                if (
                    had_previous_license
                    and os.path.isfile(
                        previous_license_path
                    )
                ):
                    os.replace(
                        previous_license_path,
                        LICENSE_FILE_PATH,
                    )

            except OSError:
                pass

            return {
                "success": False,
                "status": verification_result.get(
                    "status",
                    "license_invalid",
                ),
                "message": verification_result.get(
                    "message",
                    (
                        "The selected licence file is invalid."
                    ),
                ),
            }

        if os.path.isfile(
            previous_license_path
        ):
            os.remove(
                previous_license_path
            )

        return {
            "success": True,
            "status": "license_installed",
            "message": (
                "The licence was installed and verified successfully."
            ),
            "license_payload": verification_result.get(
                "license_payload"
            ),
        }

    except Exception as error:
        try:
            if os.path.isfile(
                temporary_path
            ):
                os.remove(
                    temporary_path
                )
            if replacement_installed:
                if os.path.isfile(
                    LICENSE_FILE_PATH
                ):
                    os.remove(
                        LICENSE_FILE_PATH
                    )
                if (
                    had_previous_license
                    and os.path.isfile(
                        previous_license_path
                    )
                ):
                    os.replace(
                        previous_license_path,
                        LICENSE_FILE_PATH,
                    )
        except OSError:
            pass

        return {
            "success": False,
            "status": "installation_error",
            "message": (
                "The licence file could not be installed.\n\n"
                f"Technical details: {error}"
            ),
        }


def install_generated_license_document(
    license_document: dict,
) -> dict:
    """
    Safely install a licence document generated inside the application.

    The previous licence is restored automatically if the generated
    licence fails verification.
    """
    if not isinstance(
        license_document,
        dict,
    ):
        return {
            "success": False,
            "status": "invalid_license_document",
            "message": (
                "The generated licence document is invalid."
            ),
        }

    payload = license_document.get(
        "payload"
    )

    signature = license_document.get(
        "signature"
    )

    if not isinstance(
        payload,
        dict,
    ) or not isinstance(
        signature,
        str,
    ):
        return {
            "success": False,
            "status": "invalid_license_document",
            "message": (
                "The generated licence document is incomplete."
            ),
        }

    temporary_path = (
        f"{LICENSE_FILE_PATH}.generated.tmp"
    )

    previous_license_path = (
        f"{LICENSE_FILE_PATH}.generated.previous"
    )

    had_previous_license = os.path.isfile(
        LICENSE_FILE_PATH
    )

    try:
        os.makedirs(
            LICENSE_DIRECTORY,
            exist_ok=True,
        )
        for cleanup_path in (
            temporary_path,
            previous_license_path,
        ):
            if os.path.isfile(
                cleanup_path
            ):
                os.remove(
                    cleanup_path
                )

        if had_previous_license:
            shutil.copy2(
                LICENSE_FILE_PATH,
                previous_license_path,
            )

        with open(
            temporary_path,
            "w",
            encoding="utf-8",
        ) as license_file:
            json.dump(
                license_document,
                license_file,
                indent=4,
                ensure_ascii=False,
            )

            license_file.flush()

            os.fsync(
                license_file.fileno()
            )

        os.replace(
            temporary_path,
            LICENSE_FILE_PATH,
        )

        verification_result = (
            verify_installed_license()
        )

        if not verification_result.get(
            "success",
            False,
        ):
            if os.path.isfile(
                LICENSE_FILE_PATH
            ):
                os.remove(
                    LICENSE_FILE_PATH
                )

            if (
                had_previous_license
                and os.path.isfile(
                    previous_license_path
                )
            ):
                os.replace(
                    previous_license_path,
                    LICENSE_FILE_PATH,
                )

            return {
                "success": False,
                "status": verification_result.get(
                    "status",
                    "generated_license_invalid",
                ),
                "message": verification_result.get(
                    "message",
                    (
                        "The generated licence failed verification."
                    ),
                ),
            }

        if os.path.isfile(
            previous_license_path
        ):
            os.remove(
                previous_license_path
            )

        return {
            "success": True,
            "status": "generated_license_installed",
            "message": (
                "The licence was generated, installed, "
                "and verified successfully."
            ),
            "license_payload": verification_result.get(
                "license_payload",
                {},
            ),
        }

    except (
        OSError,
        TypeError,
        ValueError,
    ) as error:
        try:
            if os.path.isfile(
                temporary_path
            ):
                os.remove(
                    temporary_path
                )

            if (
                not os.path.isfile(
                    LICENSE_FILE_PATH
                )
                and os.path.isfile(
                    previous_license_path
                )
            ):
                os.replace(
                    previous_license_path,
                    LICENSE_FILE_PATH,
                )

        except OSError:
            pass

        return {
            "success": False,
            "status": "generated_license_installation_error",
            "message": (
                "The generated licence could not be installed.\n\n"
                f"Technical details: {error}"
            ),
        }
