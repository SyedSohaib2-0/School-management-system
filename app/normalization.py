"""Shared input normalization helpers used by services and SQLite queries."""

import re
import unicodedata


def normalize_person_name(value) -> str:
    """Return the canonical Unicode NFC, whitespace-collapsed name value."""
    normalized = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(normalized.split()).upper()


def is_valid_person_name(value) -> bool:
    """Validate the approved Unicode-aware person-name character rules."""
    text = normalize_person_name(value)
    if not text:
        return False

    punctuation = {"'", "’", "-"}
    separators = punctuation | {" "}

    if text[0] in separators or text[-1] in separators:
        return False

    previous_was_separator = False
    previous_was_letter_or_mark = False
    has_letter = False

    for character in text:
        category = unicodedata.category(character)
        is_letter = category.startswith("L")
        is_mark = category.startswith("M")

        if is_letter:
            has_letter = True
            previous_was_separator = False
            previous_was_letter_or_mark = True
            continue

        if is_mark:
            if not previous_was_letter_or_mark:
                return False
            previous_was_separator = False
            previous_was_letter_or_mark = True
            continue

        if character in separators:
            if previous_was_separator or not previous_was_letter_or_mark:
                return False
            previous_was_separator = True
            previous_was_letter_or_mark = False
            continue

        return False

    return has_letter and not previous_was_separator


def normalize_father_cnic_digits(value) -> str:
    """Remove display separators without inventing or truncating CNIC digits."""
    return re.sub(r"[\s-]+", "", str(value or "").strip())


def canonicalize_tracking_id(value, *, fallback_number=None) -> str:
    """Return the canonical HMS-000001 representation for a valid numeric ID."""
    raw = str(value or "").strip().upper()
    if not raw and fallback_number is not None:
        raw = str(int(fallback_number))
    match = re.fullmatch(r"(?:HMS-)?(\d+)", raw)
    if match is None:
        raise ValueError("Tracking ID is malformed and cannot be canonicalized safely.")
    number = int(match.group(1))
    if number < 1:
        raise ValueError("Tracking ID must contain a positive numeric value.")
    return f"HMS-{number:06d}"
