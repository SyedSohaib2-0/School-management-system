"""
Integrated private licence-generator dialog.

This owner-only dialog generates a digitally signed, machine-bound licence
for a customer request code and saves it as a portable .lic file.
"""

import base64
import calendar
import json
import os
import uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
)

from app.licence_generator.licence_security import (
    unlock_signing_key,
)

from app.services.license_service import (
    PRODUCT_CODE,
    _canonical_license_payload,
    machine_fingerprint_from_request_code,
)


class LicenceGeneratorDialog(QDialog):
    """
    Generate an owner-signed licence file for a customer computer.
    """

    def __init__(
        self,
        authorised_private_key,
        parent=None,
    ) -> None:
        super().__init__(
            parent
        )

        self.authorised_private_key = (
            authorised_private_key
        )

        self.generation_successful = False
        self.activation_successful = False
        self.generated_payload = None

        self.current_datetime = datetime.now(
            timezone.utc
        )

        self.setWindowTitle(
            "Hira Model School — Licence Generator"
        )

        self.setMinimumWidth(
            650
        )

        self.setModal(
            True
        )

        self._build_interface()

    def _build_interface(
        self,
    ) -> None:
        """Build the complete licence-generator interface."""
        main_layout = QVBoxLayout(
            self
        )

        main_layout.setContentsMargins(
            28,
            24,
            28,
            24,
        )

        main_layout.setSpacing(
            15
        )

        title_label = QLabel(
            "Generate Customer Licence File"
        )

        title_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        title_label.setStyleSheet(
            "font-size: 22px; "
            "font-weight: 700;"
        )

        main_layout.addWidget(
            title_label
        )

        privacy_label = QLabel(
            "Owner-only tool. Paste the request code supplied by the "
            "customer. The generated licence will work only on that computer."
        )

        privacy_label.setWordWrap(
            True
        )

        privacy_label.setStyleSheet(
            "padding: 10px; "
            "background-color: #fff4ce; "
            "border: 1px solid #d6b656; "
            "border-radius: 5px;"
        )

        main_layout.addWidget(
            privacy_label
        )

        customer_group = QGroupBox(
            "Customer Information"
        )

        customer_layout = QFormLayout(
            customer_group
        )

        self.customer_name_edit = QLineEdit()

        self.customer_name_edit.setPlaceholderText(
            "Enter customer or school name"
        )

        customer_layout.addRow(
            "Customer/school name:",
            self.customer_name_edit,
        )

        self.request_code_edit = QLineEdit()
        self.request_code_edit.setPlaceholderText(
            "HMS-XXXX-XXXX-..."
        )
        customer_layout.addRow(
            "Activation request code:",
            self.request_code_edit,
        )

        main_layout.addWidget(
            customer_group
        )

        licence_group = QGroupBox(
            "Licence Validity"
        )

        licence_layout = QVBoxLayout(
            licence_group
        )

        type_layout = QFormLayout()

        self.licence_type_combo = QComboBox()

        self.licence_type_combo.addItems(
            [
                "Permanent",
                "Custom",
                "Trial",
            ]
        )

        self.licence_type_combo.currentIndexChanged.connect(
            self._change_validity_page
        )

        type_layout.addRow(
            "Licence type:",
            self.licence_type_combo,
        )

        licence_layout.addLayout(
            type_layout
        )

        self.validity_pages = QStackedWidget()

        self.validity_pages.addWidget(
            self._build_permanent_page()
        )

        self.validity_pages.addWidget(
            self._build_custom_page()
        )

        self.validity_pages.addWidget(
            self._build_trial_page()
        )

        licence_layout.addWidget(
            self.validity_pages
        )

        main_layout.addWidget(
            licence_group
        )

        confirmation_group = QGroupBox(
            "Final Authorisation"
        )

        confirmation_layout = QFormLayout(
            confirmation_group
        )

        self.confirmation_password_edit = QLineEdit()

        self.confirmation_password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )

        self.confirmation_password_edit.setPlaceholderText(
            "Re-enter the secret activation password"
        )

        self.confirmation_password_edit.returnPressed.connect(
            self._generate_and_save
        )

        confirmation_layout.addRow(
            "Secret password:",
            self.confirmation_password_edit,
        )

        main_layout.addWidget(
            confirmation_group
        )

        button_layout = QHBoxLayout()

        self.generate_button = QPushButton(
            "Generate Licence File"
        )

        self.generate_button.setMinimumHeight(
            42
        )

        self.generate_button.clicked.connect(
            self._generate_and_save
        )

        button_layout.addWidget(
            self.generate_button
        )

        button_layout.addStretch()

        cancel_button = QPushButton(
            "Cancel"
        )

        cancel_button.clicked.connect(
            self.reject
        )

        button_layout.addWidget(
            cancel_button
        )

        main_layout.addLayout(
            button_layout
        )

        self.customer_name_edit.setFocus()

    def _create_month_combo(
        self,
    ) -> QComboBox:
        """Create a month-selection combo box."""
        month_combo = QComboBox()

        for month_number in range(
            1,
            13,
        ):
            month_combo.addItem(
                calendar.month_name[
                    month_number
                ],
                month_number,
            )

        month_combo.setCurrentIndex(
            self.current_datetime.month - 1
        )

        return month_combo

    def _create_year_spinbox(
        self,
    ) -> QSpinBox:
        """Create a year-selection spin box."""
        year_spinbox = QSpinBox()

        year_spinbox.setRange(
            self.current_datetime.year - 5,
            self.current_datetime.year + 50,
        )

        year_spinbox.setValue(
            self.current_datetime.year
        )

        year_spinbox.setMinimumWidth(
            120
        )

        return year_spinbox

    def _build_permanent_page(
        self,
    ):
        """Build Permanent licence date controls."""
        page = QGroupBox(
            "Permanent Licence Period"
        )

        layout = QFormLayout(
            page
        )

        self.permanent_start_month = (
            self._create_month_combo()
        )

        self.permanent_start_year = (
            self._create_year_spinbox()
        )

        layout.addRow(
            "Starting month:",
            self.permanent_start_month,
        )

        layout.addRow(
            "Starting year:",
            self.permanent_start_year,
        )

        information_label = QLabel(
            "This licence will have no expiry date."
        )

        information_label.setWordWrap(
            True
        )

        layout.addRow(
            "",
            information_label,
        )

        return page

    def _build_custom_page(
        self,
    ):
        """Build Custom licence date controls."""
        page = QGroupBox(
            "Custom Licence Period"
        )

        layout = QFormLayout(
            page
        )

        self.custom_start_month = (
            self._create_month_combo()
        )

        self.custom_start_year = (
            self._create_year_spinbox()
        )

        self.custom_end_month = (
            self._create_month_combo()
        )

        self.custom_end_year = (
            self._create_year_spinbox()
        )

        self.custom_end_year.setValue(
            self.current_datetime.year + 1
        )

        layout.addRow(
            "Starting month:",
            self.custom_start_month,
        )

        layout.addRow(
            "Starting year:",
            self.custom_start_year,
        )

        layout.addRow(
            "Ending month:",
            self.custom_end_month,
        )

        layout.addRow(
            "Ending year:",
            self.custom_end_year,
        )

        return page

    def _build_trial_page(
        self,
    ):
        """Build the one-month Trial licence information page."""
        page = QGroupBox(
            "Trial Licence Period"
        )

        layout = QVBoxLayout(
            page
        )

        trial_label = QLabel(
            "The Trial licence starts immediately and expires "
            "automatically after one calendar month."
        )

        trial_label.setWordWrap(
            True
        )

        layout.addWidget(
            trial_label
        )

        return page

    def _change_validity_page(
        self,
        index: int,
    ) -> None:
        """Display controls for the selected licence type."""
        self.validity_pages.setCurrentIndex(
            index
        )

    @staticmethod
    def _month_start(
        year: int,
        month: int,
    ) -> datetime:
        """Return the first instant of the selected UTC month."""
        return datetime(
            year,
            month,
            1,
            0,
            0,
            0,
            tzinfo=timezone.utc,
        )

    @staticmethod
    def _month_end(
        year: int,
        month: int,
    ) -> datetime:
        """Return the final second of the selected UTC month."""
        final_day = calendar.monthrange(
            year,
            month,
        )[1]

        return datetime(
            year,
            month,
            final_day,
            23,
            59,
            59,
            tzinfo=timezone.utc,
        )

    @staticmethod
    def _add_one_calendar_month(
        value: datetime,
    ) -> datetime:
        """Add one calendar month while preserving a valid day."""
        if value.month == 12:
            target_year = value.year + 1
            target_month = 1

        else:
            target_year = value.year
            target_month = value.month + 1

        target_day = min(
            value.day,
            calendar.monthrange(
                target_year,
                target_month,
            )[1],
        )

        return value.replace(
            year=target_year,
            month=target_month,
            day=target_day,
        )

    @staticmethod
    def _format_utc_datetime(
        value: datetime,
    ) -> str:
        """Convert a UTC datetime into the signed licence format."""
        return (
            value.astimezone(
                timezone.utc
            )
            .replace(
                microsecond=0
            )
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    def _build_validity_period(
        self,
    ) -> dict:
        """Build and validate the selected licence period."""
        licence_type = (
            self.licence_type_combo.currentText()
        )

        now = datetime.now(
            timezone.utc
        )

        if licence_type == "Permanent":
            start_datetime = self._month_start(
                self.permanent_start_year.value(),
                self.permanent_start_month.currentData(),
            )

            expiry_datetime = None

        elif licence_type == "Custom":
            start_datetime = self._month_start(
                self.custom_start_year.value(),
                self.custom_start_month.currentData(),
            )

            expiry_datetime = self._month_end(
                self.custom_end_year.value(),
                self.custom_end_month.currentData(),
            )

            if expiry_datetime <= start_datetime:
                raise ValueError(
                    "The Custom licence ending month must be later "
                    "than its starting month."
                )

        else:
            start_datetime = now

            expiry_datetime = (
                self._add_one_calendar_month(
                    now
                )
            )

        if start_datetime > now:
            raise ValueError(
                "The selected starting month is in the future. "
                "Select the current month or an earlier month so the "
                "software can open immediately after activation."
            )

        return {
            "license_type": licence_type,
            "starts_at": self._format_utc_datetime(
                start_datetime
            ),
            "expires_at": (
                self._format_utc_datetime(
                    expiry_datetime
                )
                if expiry_datetime is not None
                else None
            ),
        }

    def _private_keys_match(
        self,
        confirmation_private_key,
    ) -> bool:
        """Confirm both password checks unlocked the same signing key."""
        if (
            self.authorised_private_key is None
            or confirmation_private_key is None
        ):
            return False

        original_public_key = (
            self.authorised_private_key
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )

        confirmation_public_key = (
            confirmation_private_key
            .public_key()
            .public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )

        return (
            original_public_key
            == confirmation_public_key
        )

    def _generate_and_save(
        self,
    ) -> None:
        """Sign and save a licence for the supplied customer request code."""
        customer_name = (
            self.customer_name_edit.text().strip()
        )

        if not customer_name:
            QMessageBox.warning(
                self,
                "Customer Name Required",
                (
                    "Enter the customer or school name."
                ),
            )

            self.customer_name_edit.setFocus()
            return

        request_code = self.request_code_edit.text().strip()
        try:
            customer_fingerprint = machine_fingerprint_from_request_code(
                request_code
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Valid Request Code Required",
                str(error),
            )
            self.request_code_edit.setFocus()
            return

        confirmation_password = (
            self.confirmation_password_edit.text()
        )

        if not confirmation_password:
            QMessageBox.warning(
                self,
                "Password Confirmation Required",
                (
                    "Re-enter the secret activation password "
                    "before generating the licence."
                ),
            )

            self.confirmation_password_edit.setFocus()
            return

        try:
            validity = self._build_validity_period()

        except ValueError as error:
            QMessageBox.critical(
                self,
                "Invalid Licence Period",
                str(
                    error
                ),
            )

            return

        unlock_result = unlock_signing_key(
            confirmation_password
        )

        self.confirmation_password_edit.clear()
        confirmation_password = ""

        if not unlock_result.get(
            "success",
            False,
        ):
            QMessageBox.critical(
                self,
                "Authorisation Failed",
                unlock_result.get(
                    "message",
                    (
                        "The secret activation password "
                        "is incorrect."
                    ),
                ),
            )

            self.confirmation_password_edit.setFocus()
            return

        confirmation_private_key = (
            unlock_result.get(
                "private_key"
            )
        )

        if not self._private_keys_match(
            confirmation_private_key
        ):
            confirmation_private_key = None

            QMessageBox.critical(
                self,
                "Signing-Key Verification Failed",
                (
                    "The licence signing key could not "
                    "be verified."
                ),
            )

            return

        issued_datetime = datetime.now(
            timezone.utc
        )

        payload = {
            "license_id": str(
                uuid.uuid4()
            ),
            "customer_name": customer_name,
            "product_code": PRODUCT_CODE,
            "machine_fingerprint": (
                customer_fingerprint
            ),
            "issued_at": self._format_utc_datetime(
                issued_datetime
            ),
            "license_type": validity[
                "license_type"
            ],
            "starts_at": validity[
                "starts_at"
            ],
            "expires_at": validity[
                "expires_at"
            ],
        }

        signature = confirmation_private_key.sign(
            _canonical_license_payload(
                payload
            )
        )

        confirmation_private_key = None

        licence_document = {
            "payload": payload,
            "signature": (
                base64.b64encode(
                    signature
                ).decode(
                    "ascii"
                )
            ),
        }

        safe_customer_name = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in customer_name
        ).strip("_") or "customer"
        suggested_name = f"{safe_customer_name}_{payload['license_id'][:8]}.lic"
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Customer Licence",
            suggested_name,
            "Hira Model School Licence (*.lic)",
        )
        if not selected_path:
            return
        if not selected_path.lower().endswith(".lic"):
            selected_path += ".lic"

        temporary_path = f"{selected_path}.tmp"
        try:
            with open(temporary_path, "w", encoding="utf-8") as licence_file:
                json.dump(
                    licence_document,
                    licence_file,
                    indent=4,
                    ensure_ascii=False,
                )
                licence_file.flush()
                os.fsync(licence_file.fileno())
            os.replace(temporary_path, selected_path)
        except (OSError, TypeError, ValueError) as error:
            try:
                if os.path.isfile(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            QMessageBox.critical(
                self,
                "Licence File Could Not Be Saved",
                f"The licence could not be saved.\n\nDetails: {error}",
            )
            return

        self.generated_payload = payload

        self.generation_successful = True
        self.activation_successful = True
        self.authorised_private_key = None

        expiry_display = (
            self.generated_payload.get(
                "expires_at"
            )
            or "No expiry"
        )

        QMessageBox.information(
            self,
            "Licence File Generated",
            (
                "The licence was generated and digitally signed.\n\n"
                f"Customer: {customer_name}\n"
                f"Licence type: "
                f"{self.generated_payload.get('license_type')}\n"
                f"Starts: "
                f"{self.generated_payload.get('starts_at')}\n"
                f"Expires: {expiry_display}\n"
                f"Saved as: {selected_path}\n\n"
                "Send only this .lic file to the customer."
            ),
        )

        self.accept()

    def take_generated_payload(
        self,
    ):
        """Return the generated licence details."""
        payload = self.generated_payload
        self.generated_payload = None

        return payload
