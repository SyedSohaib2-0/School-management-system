"""
Security functions for the integrated licence generator.

The encrypted signing key is unlocked only in memory after the correct
secret activation password is entered. The password is never stored.
"""

import base64
import hashlib
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from app.services.license_service import (
    LICENSE_PUBLIC_KEY_PATH,
    PRODUCT_CODE,
)


KEY_PACKAGE_PATH = os.path.join(
    os.path.dirname(
        os.path.abspath(__file__)
    ),
    "encrypted_signing_key.json",
)

SUPPORTED_PACKAGE_VERSION = 1
_EXPECTED_SCRYPT_N = 32768
_EXPECTED_SCRYPT_R = 8
_EXPECTED_SCRYPT_P = 1
_EXPECTED_KEY_LENGTH = 32
_EXPECTED_SALT_BYTES = 16

ASSOCIATED_DATA = (
    f"{PRODUCT_CODE}|"
    "EMBEDDED-LICENCE-SIGNING-KEY|"
    f"VERSION-{SUPPORTED_PACKAGE_VERSION}"
).encode(
    "utf-8"
)


def _load_key_package() -> dict:
    """Load and validate the encrypted signing-key package."""
    if not os.path.isfile(
        KEY_PACKAGE_PATH
    ):
        raise FileNotFoundError(
            "The encrypted licence-signing key package is missing."
        )

    with open(
        KEY_PACKAGE_PATH,
        "r",
        encoding="utf-8",
    ) as package_file:
        package = json.load(
            package_file
        )

    if package.get(
        "version"
    ) != SUPPORTED_PACKAGE_VERSION:
        raise ValueError(
            "The encrypted signing-key package version is unsupported."
        )

    if package.get(
        "product_code"
    ) != PRODUCT_CODE:
        raise ValueError(
            "The signing-key package belongs to another product."
        )

    return package


def _derive_decryption_key(
    password: str,
    package: dict,
) -> bytes:
    """Derive the AES key from the secret activation password."""
    key_derivation = package.get(
        "key_derivation",
        {},
    )

    if key_derivation.get("algorithm") != "SCRYPT":
        raise ValueError(
            "The encrypted signing-key package uses an unsupported "
            "key-derivation algorithm."
        )

    try:
        salt = base64.b64decode(
            key_derivation["salt"],
            validate=True,
        )

        n_value = int(
            key_derivation["n"]
        )

        r_value = int(
            key_derivation["r"]
        )

        p_value = int(
            key_derivation["p"]
        )

        key_length = int(
            key_derivation["length"]
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(
            "The encrypted signing-key package is damaged."
        ) from error

    if (
        n_value != _EXPECTED_SCRYPT_N
        or r_value != _EXPECTED_SCRYPT_R
        or p_value != _EXPECTED_SCRYPT_P
        or key_length != _EXPECTED_KEY_LENGTH
        or len(salt) != _EXPECTED_SALT_BYTES
    ):
        raise ValueError(
            "The encrypted signing-key package contains unsafe or "
            "unsupported key-derivation parameters."
        )

    key_derivation_function = Scrypt(
        salt=salt,
        length=key_length,
        n=n_value,
        r=r_value,
        p=p_value,
    )

    return key_derivation_function.derive(
        password.encode(
            "utf-8"
        )
    )


def _verify_signing_key_matches_application(
    private_key: Ed25519PrivateKey,
    package: dict,
) -> None:
    """
    Confirm that the decrypted signing key matches both the package
    fingerprint and the public key used by the application.
    """
    public_key_raw = (
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )

    calculated_fingerprint = hashlib.sha256(
        public_key_raw
    ).hexdigest()

    package_fingerprint = str(
        package.get(
            "public_key_sha256",
            "",
        )
    ).lower()

    if calculated_fingerprint != package_fingerprint:
        raise ValueError(
            "The decrypted signing key failed its integrity check."
        )

    if not os.path.isfile(
        LICENSE_PUBLIC_KEY_PATH
    ):
        raise FileNotFoundError(
            "The application licence public key is missing."
        )

    with open(
        LICENSE_PUBLIC_KEY_PATH,
        "rb",
    ) as public_key_file:
        application_public_key = (
            serialization.load_pem_public_key(
                public_key_file.read()
            )
        )

    application_public_key_raw = (
        application_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )

    application_fingerprint = hashlib.sha256(
        application_public_key_raw
    ).hexdigest()

    if calculated_fingerprint != application_fingerprint:
        raise ValueError(
            "The signing key does not match the application's "
            "licence verification key."
        )


def unlock_signing_key(
    secret_password: str,
) -> dict:
    """
    Decrypt the embedded Ed25519 private key using the secret password.

    The returned private-key object exists only in application memory.
    """
    if not secret_password:
        return {
            "success": False,
            "status": "password_required",
            "message": (
                "Enter the secret activation password."
            ),
            "private_key": None,
        }

    try:
        package = _load_key_package()

        decryption_key = _derive_decryption_key(
            secret_password,
            package,
        )

        encryption_data = package.get(
            "encryption",
            {},
        )

        nonce = base64.b64decode(
            encryption_data["nonce"],
            validate=True,
        )

        encrypted_private_key = base64.b64decode(
            encryption_data["ciphertext"],
            validate=True,
        )

        raw_private_key = bytearray(
            AESGCM(
                decryption_key
            ).decrypt(
                nonce,
                encrypted_private_key,
                ASSOCIATED_DATA,
            )
        )

        try:
            private_key = (
                Ed25519PrivateKey.from_private_bytes(
                    bytes(
                        raw_private_key
                    )
                )
            )

        finally:
            for index in range(
                len(
                    raw_private_key
                )
            ):
                raw_private_key[index] = 0

        _verify_signing_key_matches_application(
            private_key,
            package,
        )

        return {
            "success": True,
            "status": "signing_key_unlocked",
            "message": (
                "The private licence generator was unlocked."
            ),
            "private_key": private_key,
        }

    except InvalidTag:
        return {
            "success": False,
            "status": "incorrect_password",
            "message": (
                "The secret activation password is incorrect."
            ),
            "private_key": None,
        }

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
    ) as error:
        return {
            "success": False,
            "status": "key_package_error",
            "message": str(
                error
            ),
            "private_key": None,
        }