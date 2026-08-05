"""Pure filename helpers for paid receipt PDF downloads."""

from datetime import datetime
import os
import re


def build_paid_receipt_default_filename(payment_data: dict) -> str:
    """Include receipt number, Tracking ID and payment date in the filename."""
    receipt_no = str(payment_data.get("receipt_no") or "-").strip()
    tracking_id = str(payment_data.get("tracking_id") or "-").strip()
    raw_date = str(payment_data.get("payment_date") or "").strip()
    safe_date = "unknown-date"
    for date_format in ("%d-%m-%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            safe_date = datetime.strptime(raw_date, date_format).strftime("%Y-%m-%d")
            break
        except ValueError:
            continue

    def safe_component(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
        return cleaned or "-"

    return (
        f"Fee_Receipt_{safe_component(receipt_no)}_"
        f"{safe_component(tracking_id)}_{safe_date}.pdf"
    )


def make_duplicate_safe_pdf_path(file_path: str) -> str:
    """Return a non-existing PDF path without overwriting an older receipt."""
    final_path = file_path if file_path.lower().endswith(".pdf") else file_path + ".pdf"
    if not os.path.exists(final_path):
        return final_path
    base_path, extension = os.path.splitext(final_path)
    suffix = 2
    while True:
        candidate = f"{base_path}_{suffix}{extension}"
        if not os.path.exists(candidate):
            return candidate
        suffix += 1
