"""
First-run credential setup, administrator login, and password recovery UI.

Startup order is controlled by run.py:
1. Verify/activate the machine-bound software licence.
2. Initialize/migrate the school database.
3. Create the single administrator identity when none exists.
4. Require username/password login before MainWindow is created.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QRegularExpression,
    QTimer,
    Qt,
)
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.services import auth_service


LOGIN_SECURITY_UI_V10 = True


_DIALOG_STYLESHEET = """
QDialog {
    background-color: #F5F7FB;
}

QFrame#authCard {
    background-color: #FFFFFF;
    border: 1px solid #DCE3EF;
    border-radius: 10px;
}

QLabel#authTitle {
    color: #17233D;
    font-size: 22px;
    font-weight: 700;
}

QLabel#authSubtitle {
    color: #596780;
    font-size: 12px;
}

QLabel#authHint {
    color: #667085;
    font-size: 11px;
}

QLineEdit {
    min-height: 36px;
    border: 1px solid #C9D3E3;
    border-radius: 5px;
    background-color: #FFFFFF;
    padding: 3px 8px;
    color: #17233D;
}

QLineEdit:focus {
    border: 1px solid #3157D5;
}

QPushButton {
    min-height: 36px;
    border-radius: 5px;
    padding: 4px 16px;
    font-weight: 600;
}

QPushButton#primaryAuthButton {
    background-color: #3157D5;
    color: #FFFFFF;
    border: 1px solid #3157D5;
}

QPushButton#primaryAuthButton:hover {
    background-color: #2448BF;
}

QPushButton#secondaryAuthButton {
    background-color: #FFFFFF;
    color: #24324A;
    border: 1px solid #C9D3E3;
}

QPushButton#linkAuthButton {
    background-color: transparent;
    color: #3157D5;
    border: none;
    text-align: left;
    padding-left: 0px;
}
"""


def _configure_cnic_field(field: QLineEdit) -> None:
    field.setObjectName("recoveryCnicInput")
    field.setPlaceholderText(
        "13 digits without dashes"
    )
    field.setMaxLength(
        auth_service.CNIC_DIGITS_REQUIRED
    )
    field.setValidator(
        QRegularExpressionValidator(
            QRegularExpression(r"^[0-9]{0,13}$"),
            field,
        )
    )


def _configure_dob_field(field: QLineEdit) -> None:
    field.setObjectName("recoveryDateOfBirthInput")
    field.setPlaceholderText("DD-MM-YYYY")
    field.setInputMask("00-00-0000;_")
    field.setMaxLength(10)


def _set_password_visibility(
    visible: bool,
    *password_fields: QLineEdit,
) -> None:
    echo_mode = (
        QLineEdit.EchoMode.Normal
        if visible
        else QLineEdit.EchoMode.Password
    )

    for field in password_fields:
        field.setEchoMode(echo_mode)


def _build_dialog_card(
    dialog: QDialog,
    title: str,
    subtitle: str,
):
    outer_layout = QVBoxLayout(dialog)
    outer_layout.setContentsMargins(24, 24, 24, 24)

    card = QFrame()
    card.setObjectName("authCard")

    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(28, 26, 28, 26)
    card_layout.setSpacing(14)

    title_label = QLabel(title)
    title_label.setObjectName("authTitle")
    title_label.setAlignment(
        Qt.AlignmentFlag.AlignCenter
    )
    card_layout.addWidget(title_label)

    subtitle_label = QLabel(subtitle)
    subtitle_label.setObjectName("authSubtitle")
    subtitle_label.setAlignment(
        Qt.AlignmentFlag.AlignCenter
    )
    subtitle_label.setWordWrap(True)
    card_layout.addWidget(subtitle_label)

    outer_layout.addWidget(card)

    return card_layout


class CredentialSetupDialog(QDialog):
    """Create the one administrator identity after licence activation."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.setWindowTitle(
            "Hira Model School — Create Login Credentials"
        )
        self.setModal(True)
        self.setMinimumWidth(590)
        self.setStyleSheet(_DIALOG_STYLESHEET)

        card_layout = _build_dialog_card(
            self,
            "Create Administrator Login",
            (
                "The software licence is active. Create the single "
                "administrator username, password, and recovery identity."
            ),
        )

        form_layout = QFormLayout()
        form_layout.setSpacing(11)
        form_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )

        self.username_edit = QLineEdit()
        self.username_edit.setObjectName(
            "setupUsernameInput"
        )
        self.username_edit.setPlaceholderText(
            "e.g. admin"
        )
        self.username_edit.setMaxLength(
            auth_service.USERNAME_MAXIMUM_LENGTH
        )
        form_layout.addRow(
            "Username:",
            self.username_edit,
        )

        self.password_edit = QLineEdit()
        self.password_edit.setObjectName(
            "setupPasswordInput"
        )
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.password_edit.setMaxLength(
            auth_service.PASSWORD_MAXIMUM_LENGTH
        )
        self.password_edit.setPlaceholderText(
            f"Minimum {auth_service.PASSWORD_MINIMUM_LENGTH} characters"
        )
        form_layout.addRow(
            "Password:",
            self.password_edit,
        )

        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setObjectName(
            "setupConfirmPasswordInput"
        )
        self.confirm_password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.confirm_password_edit.setMaxLength(
            auth_service.PASSWORD_MAXIMUM_LENGTH
        )
        self.confirm_password_edit.setPlaceholderText(
            "Enter the same password again"
        )
        form_layout.addRow(
            "Confirm password:",
            self.confirm_password_edit,
        )

        self.cnic_edit = QLineEdit()
        _configure_cnic_field(self.cnic_edit)
        form_layout.addRow(
            "Recovery CNIC:",
            self.cnic_edit,
        )

        self.date_of_birth_edit = QLineEdit()
        _configure_dob_field(
            self.date_of_birth_edit
        )
        form_layout.addRow(
            "Date of birth:",
            self.date_of_birth_edit,
        )

        card_layout.addLayout(form_layout)

        recovery_hint = QLabel(
            "Forgot Password will require this exact 13-digit CNIC and "
            "date of birth. These recovery values are stored only as a "
            "secure verification digest."
        )
        recovery_hint.setObjectName("authHint")
        recovery_hint.setWordWrap(True)
        card_layout.addWidget(recovery_hint)

        self.show_password_check = QCheckBox(
            "Show password"
        )
        self.show_password_check.toggled.connect(
            lambda visible: _set_password_visibility(
                visible,
                self.password_edit,
                self.confirm_password_edit,
            )
        )
        card_layout.addWidget(
            self.show_password_check
        )

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.exit_button = QPushButton("Exit")
        self.exit_button.setObjectName(
            "secondaryAuthButton"
        )
        self.exit_button.clicked.connect(
            self.reject
        )
        button_layout.addWidget(self.exit_button)

        self.create_button = QPushButton(
            "Create Login Credentials"
        )
        self.create_button.setObjectName(
            "primaryAuthButton"
        )
        self.create_button.clicked.connect(
            self._create_credentials
        )
        button_layout.addWidget(self.create_button)

        card_layout.addLayout(button_layout)

        self.confirm_password_edit.returnPressed.connect(
            self._create_credentials
        )
        self.date_of_birth_edit.returnPressed.connect(
            self._create_credentials
        )
        self.username_edit.setFocus()

    def _create_credentials(self) -> None:
        password = self.password_edit.text()
        confirm_password = (
            self.confirm_password_edit.text()
        )

        if password != confirm_password:
            QMessageBox.warning(
                self,
                "Passwords Do Not Match",
                "Password and Confirm Password must be identical.",
            )
            self.confirm_password_edit.clear()
            self.confirm_password_edit.setFocus()
            return

        try:
            auth_service.create_admin_credentials(
                username=self.username_edit.text(),
                password=password,
                cnic=self.cnic_edit.text(),
                date_of_birth=(
                    self.date_of_birth_edit.text()
                ),
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Login Setup Could Not Be Completed",
                str(error),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Login Setup Error",
                (
                    "Administrator credentials could not be saved "
                    "safely. No second administrator was created.\n\n"
                    f"Technical details: {type(error).__name__}: {error}"
                ),
            )
            return

        self.password_edit.clear()
        self.confirm_password_edit.clear()

        QMessageBox.information(
            self,
            "Login Credentials Created",
            (
                "Administrator credentials were created successfully.\n\n"
                "The Login window will now open."
            ),
        )
        self.accept()


class ForgotPasswordDialog(QDialog):
    """Reset the single administrator password using CNIC and DOB."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.password_reset_successful = False

        self.setWindowTitle(
            "Hira Model School — Forgot Password"
        )
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(_DIALOG_STYLESHEET)

        card_layout = _build_dialog_card(
            self,
            "Reset Administrator Password",
            (
                "Enter the same CNIC and date of birth used when "
                "the login credentials were first created."
            ),
        )

        form_layout = QFormLayout()
        form_layout.setSpacing(11)
        form_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )

        self.cnic_edit = QLineEdit()
        _configure_cnic_field(self.cnic_edit)
        form_layout.addRow(
            "CNIC:",
            self.cnic_edit,
        )

        self.date_of_birth_edit = QLineEdit()
        _configure_dob_field(
            self.date_of_birth_edit
        )
        form_layout.addRow(
            "Date of birth:",
            self.date_of_birth_edit,
        )

        self.new_password_edit = QLineEdit()
        self.new_password_edit.setObjectName(
            "forgotNewPasswordInput"
        )
        self.new_password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.new_password_edit.setMaxLength(
            auth_service.PASSWORD_MAXIMUM_LENGTH
        )
        self.new_password_edit.setPlaceholderText(
            f"Minimum {auth_service.PASSWORD_MINIMUM_LENGTH} characters"
        )
        form_layout.addRow(
            "New password:",
            self.new_password_edit,
        )

        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setObjectName(
            "forgotConfirmPasswordInput"
        )
        self.confirm_password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.confirm_password_edit.setMaxLength(
            auth_service.PASSWORD_MAXIMUM_LENGTH
        )
        self.confirm_password_edit.setPlaceholderText(
            "Enter the new password again"
        )
        form_layout.addRow(
            "Confirm password:",
            self.confirm_password_edit,
        )

        card_layout.addLayout(form_layout)

        self.show_password_check = QCheckBox(
            "Show new password"
        )
        self.show_password_check.toggled.connect(
            lambda visible: _set_password_visibility(
                visible,
                self.new_password_edit,
                self.confirm_password_edit,
            )
        )
        card_layout.addWidget(
            self.show_password_check
        )

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.back_button = QPushButton("Back")
        self.back_button.setObjectName(
            "secondaryAuthButton"
        )
        self.back_button.clicked.connect(
            self.reject
        )
        button_layout.addWidget(self.back_button)

        self.reset_button = QPushButton(
            "Reset Password"
        )
        self.reset_button.setObjectName(
            "primaryAuthButton"
        )
        self.reset_button.clicked.connect(
            self._reset_password
        )
        button_layout.addWidget(self.reset_button)

        card_layout.addLayout(button_layout)

        self.confirm_password_edit.returnPressed.connect(
            self._reset_password
        )
        self.cnic_edit.setFocus()

    def _reset_password(self) -> None:
        new_password = self.new_password_edit.text()
        confirm_password = (
            self.confirm_password_edit.text()
        )

        if new_password != confirm_password:
            QMessageBox.warning(
                self,
                "Passwords Do Not Match",
                "New Password and Confirm Password must be identical.",
            )
            self.confirm_password_edit.clear()
            self.confirm_password_edit.setFocus()
            return

        try:
            reset_successful = (
                auth_service.reset_admin_password(
                    cnic=self.cnic_edit.text(),
                    date_of_birth=(
                        self.date_of_birth_edit.text()
                    ),
                    new_password=new_password,
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Password Could Not Be Reset",
                str(error),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Password Reset Error",
                (
                    "The password could not be reset safely.\n\n"
                    f"Technical details: {type(error).__name__}: {error}"
                ),
            )
            return

        if not reset_successful:
            QMessageBox.warning(
                self,
                "Recovery Details Do Not Match",
                (
                    "The CNIC or date of birth is incorrect.\n\n"
                    "Enter the exact CNIC without dashes and the exact "
                    "date of birth used during login setup."
                ),
            )
            self.new_password_edit.clear()
            self.confirm_password_edit.clear()
            return

        self.password_reset_successful = True
        self.new_password_edit.clear()
        self.confirm_password_edit.clear()

        QMessageBox.information(
            self,
            "Password Reset Successfully",
            "Use the new password in the Login window.",
        )
        self.accept()


class LoginDialog(QDialog):
    """Require administrator username/password before opening MainWindow."""

    MAX_LOGIN_ATTEMPTS = 5
    LOGIN_COOLDOWN_SECONDS = 10

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.authenticated_username = ""
        self.failed_login_attempts = 0
        self.cooldown_seconds_remaining = 0
        self.cooldown_timer = QTimer(self)
        self.cooldown_timer.setInterval(1000)
        self.cooldown_timer.timeout.connect(
            self._update_login_cooldown
        )

        self.setWindowTitle(
            "Hira Model School — Administrator Login"
        )
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setStyleSheet(_DIALOG_STYLESHEET)

        card_layout = _build_dialog_card(
            self,
            "Administrator Login",
            (
                "Enter the username and password created for this "
                "school-management installation."
            ),
        )

        form_layout = QFormLayout()
        form_layout.setSpacing(11)
        form_layout.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight
        )

        self.username_edit = QLineEdit()
        self.username_edit.setObjectName(
            "loginUsernameInput"
        )
        self.username_edit.setMaxLength(
            auth_service.USERNAME_MAXIMUM_LENGTH
        )
        self.username_edit.setPlaceholderText(
            "Username"
        )
        self.username_edit.setText(
            auth_service.get_admin_username()
        )
        form_layout.addRow(
            "Username:",
            self.username_edit,
        )

        self.password_edit = QLineEdit()
        self.password_edit.setObjectName(
            "loginPasswordInput"
        )
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        self.password_edit.setMaxLength(
            auth_service.PASSWORD_MAXIMUM_LENGTH
        )
        self.password_edit.setPlaceholderText(
            "Password"
        )
        form_layout.addRow(
            "Password:",
            self.password_edit,
        )

        card_layout.addLayout(form_layout)

        self.show_password_check = QCheckBox(
            "Show password"
        )
        self.show_password_check.toggled.connect(
            lambda visible: _set_password_visibility(
                visible,
                self.password_edit,
            )
        )
        card_layout.addWidget(
            self.show_password_check
        )

        self.forgot_password_button = QPushButton(
            "Forgot Password?"
        )
        self.forgot_password_button.setObjectName(
            "linkAuthButton"
        )
        self.forgot_password_button.clicked.connect(
            self._open_forgot_password
        )
        card_layout.addWidget(
            self.forgot_password_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        self.login_status_label = QLabel(
            f"Login attempts available: {self.MAX_LOGIN_ATTEMPTS}"
        )
        self.login_status_label.setObjectName("authHint")
        card_layout.addWidget(self.login_status_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.exit_button = QPushButton("Exit")
        self.exit_button.setObjectName(
            "secondaryAuthButton"
        )
        self.exit_button.clicked.connect(
            self.reject
        )
        button_layout.addWidget(self.exit_button)

        self.login_button = QPushButton("Login")
        self.login_button.setObjectName(
            "primaryAuthButton"
        )
        self.login_button.clicked.connect(
            self._login
        )
        button_layout.addWidget(self.login_button)

        card_layout.addLayout(button_layout)

        self.password_edit.returnPressed.connect(
            self._login
        )

        if self.username_edit.text():
            self.password_edit.setFocus()
        else:
            self.username_edit.setFocus()

    def _login(self) -> None:
        if self.cooldown_timer.isActive():
            return

        username = self.username_edit.text()
        password = self.password_edit.text()

        authenticated = auth_service.authenticate_admin(
            username,
            password,
        )

        self.password_edit.clear()

        if not authenticated:
            self.failed_login_attempts += 1
            remaining_attempts = (
                self.MAX_LOGIN_ATTEMPTS
                - self.failed_login_attempts
            )

            if remaining_attempts <= 0:
                QMessageBox.warning(
                    self,
                    "Login Temporarily Locked",
                    (
                        "Five incorrect login attempts were made. "
                        "Please wait 10 seconds before trying again "
                        "or using Forgot Password."
                    ),
                )
                self._start_login_cooldown()
                return

            self.login_status_label.setText(
                f"Login attempts remaining: {remaining_attempts}"
            )
            QMessageBox.warning(
                self,
                "Login Failed",
                (
                    "The username or password is incorrect.\n\n"
                    f"Remaining attempts: {remaining_attempts}"
                ),
            )
            self.password_edit.setFocus()
            return

        self.authenticated_username = (
            auth_service.get_admin_username()
        )
        self.accept()

    def _start_login_cooldown(self) -> None:
        """Disable authentication controls for the ten-second break."""
        self.cooldown_seconds_remaining = (
            self.LOGIN_COOLDOWN_SECONDS
        )
        self.username_edit.setEnabled(False)
        self.password_edit.setEnabled(False)
        self.login_button.setEnabled(False)
        self.forgot_password_button.setEnabled(False)
        self.login_status_label.setText(
            "Too many incorrect attempts. Try again in "
            f"{self.cooldown_seconds_remaining} seconds."
        )
        self.cooldown_timer.start()

    def _update_login_cooldown(self) -> None:
        """Update the countdown and restore a fresh set of attempts."""
        self.cooldown_seconds_remaining -= 1

        if self.cooldown_seconds_remaining > 0:
            self.login_status_label.setText(
                "Too many incorrect attempts. Try again in "
                f"{self.cooldown_seconds_remaining} seconds."
            )
            return

        self.cooldown_timer.stop()
        self.failed_login_attempts = 0
        self.username_edit.setEnabled(True)
        self.password_edit.setEnabled(True)
        self.login_button.setEnabled(True)
        self.forgot_password_button.setEnabled(True)
        self.login_status_label.setText(
            f"Login attempts available: {self.MAX_LOGIN_ATTEMPTS}. "
            "You can try again or use Forgot Password."
        )
        self.password_edit.setFocus()

    def _open_forgot_password(self) -> None:
        recovery_dialog = ForgotPasswordDialog(
            self
        )

        recovery_result = recovery_dialog.exec()

        if (
            recovery_result
            == QDialog.DialogCode.Accepted
            and recovery_dialog.password_reset_successful
        ):
            self.username_edit.setText(
                auth_service.get_admin_username()
            )
            self.password_edit.clear()
            self.password_edit.setFocus()
