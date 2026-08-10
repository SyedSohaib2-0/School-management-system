"""Customer-safe offline licence activation dialog."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.services.license_service import (
    get_activation_request_code,
    install_license_file,
)


class LicenseActivationDialog(QDialog):
    """Show this computer's request code and import an owner-signed licence."""

    def __init__(self, verification_message: str = "", parent=None) -> None:
        super().__init__(parent)
        self.activation_successful = False
        self.setWindowTitle("Hira Model School - Licence Activation")
        self.setMinimumWidth(680)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel("Activate Hira Model School")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(title)

        explanation = QLabel(
            "This installation requires an owner-signed licence. Copy the "
            "request code below and send it to the software owner. When you "
            "receive the .lic file, import it here."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        if verification_message:
            status = QLabel(verification_message)
            status.setWordWrap(True)
            status.setStyleSheet(
                "padding: 10px; background: #fff4ce; border: 1px solid #d6b656;"
            )
            layout.addWidget(status)

        self.request_code_edit = QLineEdit(get_activation_request_code())
        self.request_code_edit.setReadOnly(True)
        self.request_code_edit.setObjectName("activationRequestCode")
        layout.addWidget(QLabel("Activation request code:"))
        layout.addWidget(self.request_code_edit)

        actions = QHBoxLayout()
        copy_button = QPushButton("Copy Request Code")
        copy_button.clicked.connect(self._copy_request_code)
        actions.addWidget(copy_button)

        import_button = QPushButton("Import Licence File")
        import_button.clicked.connect(self._import_licence)
        actions.addWidget(import_button)
        actions.addStretch()

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def _copy_request_code(self) -> None:
        QGuiApplication.clipboard().setText(self.request_code_edit.text())
        QMessageBox.information(
            self,
            "Request Code Copied",
            "Send this request code to the authorised software owner.",
        )

    def _import_licence(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Licence File",
            "",
            "Hira Model School Licence (*.lic)",
        )
        if not selected_path:
            return

        result = install_license_file(selected_path)
        if not result.get("success", False):
            QMessageBox.critical(
                self,
                "Licence Could Not Be Installed",
                result.get("message", "The selected licence is invalid."),
            )
            return

        self.activation_successful = True
        QMessageBox.information(
            self,
            "Licence Activated",
            "The licence was imported and verified successfully.",
        )
        self.accept()
