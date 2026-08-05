"""
Private activation-password dialog.

This dialog unlocks the encrypted licence-signing key in memory.
The entered password is never saved to disk.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.licence_generator.licence_security import (
    unlock_signing_key,
)


class ActivationPasswordDialog(QDialog):
    """Ask the authorised provider for the secret activation password."""

    MAX_ATTEMPTS = 5

    def __init__(
        self,
        parent=None,
    ) -> None:
        super().__init__(
            parent
        )

        self.private_key = None
        self.failed_attempts = 0

        self.setWindowTitle(
            "Hira Model School — Private Activation"
        )

        self.setMinimumWidth(
            520
        )

        self.setModal(
            True
        )

        self._build_interface()

    def _build_interface(
        self,
    ) -> None:
        """Build the secret-password interface."""
        main_layout = QVBoxLayout(
            self
        )

        main_layout.setContentsMargins(
            26,
            24,
            26,
            24,
        )

        main_layout.setSpacing(
            14
        )

        title_label = QLabel(
            "Private Licence Activation"
        )

        title_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        title_label.setStyleSheet(
            "font-size: 21px; "
            "font-weight: 700;"
        )

        main_layout.addWidget(
            title_label
        )

        description_label = QLabel(
            "This section is restricted to the authorised software "
            "provider. Enter the private activation password to open "
            "the licence generator."
        )

        description_label.setWordWrap(
            True
        )

        main_layout.addWidget(
            description_label
        )

        password_label = QLabel(
            "Secret activation password:"
        )

        password_label.setStyleSheet(
            "font-weight: 600;"
        )

        main_layout.addWidget(
            password_label
        )

        self.password_edit = QLineEdit()

        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )

        self.password_edit.setPlaceholderText(
            "Enter private activation password"
        )

        self.password_edit.returnPressed.connect(
            self._unlock_generator
        )

        main_layout.addWidget(
            self.password_edit
        )

        self.attempts_label = QLabel(
            f"Maximum attempts: {self.MAX_ATTEMPTS}"
        )

        self.attempts_label.setStyleSheet(
            "font-size: 11px; "
            "color: #666666;"
        )

        main_layout.addWidget(
            self.attempts_label
        )

        button_layout = QHBoxLayout()

        unlock_button = QPushButton(
            "Unlock Licence Generator"
        )

        unlock_button.setMinimumHeight(
            38
        )

        unlock_button.clicked.connect(
            self._unlock_generator
        )

        button_layout.addWidget(
            unlock_button
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

        self.password_edit.setFocus()

    def _unlock_generator(
        self,
    ) -> None:
        """Validate the secret password and unlock the signing key."""
        password = self.password_edit.text()

        if not password:
            QMessageBox.warning(
                self,
                "Password Required",
                (
                    "Enter the secret activation password."
                ),
            )

            self.password_edit.setFocus()
            return

        result = unlock_signing_key(
            password
        )

        self.password_edit.clear()
        password = ""

        if result.get(
            "success",
            False,
        ):
            self.private_key = result.get(
                "private_key"
            )

            if self.private_key is None:
                QMessageBox.critical(
                    self,
                    "Activation Error",
                    (
                        "The signing key could not be loaded."
                    ),
                )

                return

            self.accept()
            return

        self.failed_attempts += 1

        remaining_attempts = (
            self.MAX_ATTEMPTS
            - self.failed_attempts
        )

        if remaining_attempts <= 0:
            QMessageBox.critical(
                self,
                "Access Denied",
                (
                    "The maximum number of password attempts "
                    "has been reached.\n\n"
                    "The software will now close."
                ),
            )

            self.reject()
            return

        self.attempts_label.setText(
            f"Remaining attempts: {remaining_attempts}"
        )

        QMessageBox.critical(
            self,
            "Incorrect Password",
            (
                f"{result.get('message', 'Access denied.')}\n\n"
                f"Remaining attempts: {remaining_attempts}"
            ),
        )

        self.password_edit.setFocus()

    def take_private_key(
        self,
    ):
        """
        Return the unlocked private key and remove the dialog's
        reference to it.
        """
        private_key = self.private_key
        self.private_key = None

        return private_key