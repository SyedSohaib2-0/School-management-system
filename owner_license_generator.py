"""Owner-only entry point for generating customer licence files."""

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from app.config import APPLICATION_ICON_PATH, STYLE_SHEET_PATH
from app.licence_generator.activation_password_dialog import (
    ActivationPasswordDialog,
)
from app.licence_generator.licence_generator_dialog import (
    LicenceGeneratorDialog,
)


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("Hira Model School Licence Generator")
    application.setOrganizationName("Hira Model School")
    application.setWindowIcon(QIcon(APPLICATION_ICON_PATH))
    try:
        with open(STYLE_SHEET_PATH, "r", encoding="utf-8") as style_file:
            application.setStyleSheet(style_file.read())
    except (OSError, UnicodeError):
        pass

    password_dialog = ActivationPasswordDialog()
    if password_dialog.exec() != QDialog.DialogCode.Accepted:
        return 0

    private_key = password_dialog.take_private_key()
    if private_key is None:
        QMessageBox.critical(
            None,
            "Generator Could Not Be Opened",
            "The private signing key could not be unlocked.",
        )
        return 1

    generator_dialog = LicenceGeneratorDialog(
        authorised_private_key=private_key,
    )
    generator_dialog.exec()
    generator_dialog.authorised_private_key = None
    private_key = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
