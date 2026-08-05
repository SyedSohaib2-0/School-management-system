"""
Application entry point.
Run this file to start the School Management System.
"""

import sys

from app.services.crash_reporter import (
    attach_qt_application,
    finalize_crash_catcher,
    handle_unhandled_exception,
    install_global_crash_catcher,
)

# Install the process-wide safety net before Qt and the rest of the
# application are imported. Import/startup failures therefore also produce
# a support-ready crash report instead of disappearing without a record.
install_global_crash_catcher()

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMessageBox,
)

from app.database import initialize_database
from app.main_window import MainWindow
from app.auth_dialogs import (
    CredentialSetupDialog,
    LoginDialog,
)
from app.licence_generator.activation_password_dialog import (
    ActivationPasswordDialog,
)
from app.licence_generator.licence_generator_dialog import (
    LicenceGeneratorDialog,
)
from app.services.backup_scheduler import (
    complete_pending_backups_before_exit,
    start_automatic_backup_scheduler,
)

from app.services.backup_service import (
    process_pending_restore_before_startup,
)
from app.services.license_service import (
    verify_installed_license,
)
from app.services.auth_service import (
    has_admin_credentials,
)
from app.services.coming_session_service import (
    activate_due_coming_session_admissions,
)
from app.services.single_instance_service import (
    acquire_application_lock,
)
from app.config import (
    APPLICATION_ICON_PATH,
    STYLE_SHEET_PATH,
)
from app.ui_theme import apply_global_theme


def load_global_stylesheet(application: QApplication) -> None:
    """Load the single global stylesheet and apply it to the whole application."""
    try:
        with open(STYLE_SHEET_PATH, "r", encoding="utf-8") as style_file:
            application.setStyleSheet(style_file.read())
    except (OSError, UnicodeError) as error:
        # Appearance assets must never prevent access to school records.
        print(
            "Warning: styles.qss could not be loaded. "
            f"Running with default styling. Details: {error}"
        )

def run_authentication_gate() -> bool:
    """
    Require first-run setup and then administrator login.

    Licence validation is deliberately completed before this function is
    called. The MainWindow must never be created unless this gate returns
    True.
    """
    if not has_admin_credentials():
        setup_dialog = CredentialSetupDialog()

        setup_result = setup_dialog.exec()

        if (
            setup_result
            != QDialog.DialogCode.Accepted
        ):
            return False

    login_dialog = LoginDialog()

    login_result = login_dialog.exec()

    return (
        login_result
        == QDialog.DialogCode.Accepted
    )


def main():
    application = QApplication(sys.argv)

    try:
        application_lock = acquire_application_lock()
    except OSError as error:
        QMessageBox.critical(
            None,
            "Application Could Not Start",
            "The application lock could not be created safely.\n\n"
            f"Details: {error}",
        )
        return

    if application_lock is None:
        QMessageBox.information(
            None,
            "Hira Model School Is Already Open",
            "Another copy of the application is already running for this "
            "Windows user. Close the existing window before opening a new one.",
        )
        return

    # Keep the QLockFile alive for the full QApplication lifetime.
    application._hms_single_instance_lock = application_lock

    # Attach the GUI-thread notification bridge immediately after Qt exists.
    # Background-thread failures can now display the same polite crash dialog
    # without touching widgets from the wrong thread.
    attach_qt_application(
        application
    )

    application.setApplicationName(
        "Hira Model School Management System"
    )

    application.setOrganizationName(
        "Hira Model School"
    )

    application.setWindowIcon(
        QIcon(
            APPLICATION_ICON_PATH
        )
    )

    # Verify the digitally signed, machine-bound licence before
    # accessing the school database or processing a restore.
    try:
        license_result = verify_installed_license()
    except Exception as error:
        QMessageBox.critical(
            None,
            "Licence Verification Failed",
            "The software licence could not be checked safely. "
            "No school database was opened.\n\n"
            f"Technical details: {type(error).__name__}: {error}",
        )
        return

    if not license_result.get(
        "success",
        False,
    ):
        # First restrict access to the private licence generator.
        password_dialog = ActivationPasswordDialog()

        password_dialog_result = (
            password_dialog.exec()
        )

        if (
            password_dialog_result
            != QDialog.DialogCode.Accepted
        ):
            return

        authorised_private_key = (
            password_dialog.take_private_key()
        )

        if authorised_private_key is None:
            QMessageBox.critical(
                None,
                "Private Activation Failed",
                (
                    "The private licence-signing key "
                    "could not be unlocked."
                ),
            )

            return

        # Allow the authorised provider to choose the validity period,
        # generate the signed licence, and activate this computer.
        generator_dialog = LicenceGeneratorDialog(
            authorised_private_key=(
                authorised_private_key
            ),
        )

        generator_dialog_result = (
            generator_dialog.exec()
        )

        # Remove unnecessary references to the private signing key.
        authorised_private_key = None
        generator_dialog.authorised_private_key = None

        if (
            generator_dialog_result
            != QDialog.DialogCode.Accepted
            or not generator_dialog.activation_successful
        ):
            return

        # Verify the newly generated licence independently before
        # accessing the school database. Treat verification dependencies and
        # damaged licence files exactly like the initial pre-database check.
        try:
            license_result = verify_installed_license()
        except Exception as error:
            QMessageBox.critical(
                None,
                "Software Activation Could Not Be Verified",
                "The newly generated licence could not be checked safely. "
                "No school database was opened.\n\n"
                f"Technical details: {type(error).__name__}: {error}",
            )
            return

        if not license_result.get(
            "success",
            False,
        ):
            QMessageBox.critical(
                None,
                "Software Activation Failed",
                license_result.get(
                    "message",
                    (
                        "The generated licence could not "
                        "be verified."
                    ),
                ),
            )

            return

    # Apply a staged database restore before database initialization
    # and before normal application database connections are opened.
    try:
        restore_result = process_pending_restore_before_startup()
    except Exception as error:
        QMessageBox.critical(
            None,
            "Database Restore Could Not Be Checked",
            "The pending-restore state could not be inspected safely. "
            "The existing school database was not opened.\n\n"
            f"Technical details: {type(error).__name__}: {error}",
        )
        return

    if not restore_result.get(
        "success",
        False,
    ):
        QMessageBox.critical(
            None,
            "Database Restore Could Not Be Completed",
            restore_result.get(
                "message",
                (
                    "The pending database restore could not "
                    "be completed safely."
                ),
            ),
        )

        return

    # Create or update the required database tables only after any
    # pending startup restore has been processed successfully.
    try:
        initialize_database()

    except Exception as error:
        QMessageBox.critical(
            None,
            "Database Could Not Be Opened",
            (
                "The school database could not be initialized or "
                "upgraded safely.\n\n"
                "The software will close without changing school "
                "records.\n\n"
                f"Technical details:\n"
                f"{type(error).__name__}: {error}"
            ),
        )

        return

    # Activate due coming-session admissions after schema initialization.
    # Failures are isolated per class and reported after login; they do not
    # weaken or bypass authentication.
    try:
        coming_session_activation_result = (
            activate_due_coming_session_admissions()
        )
    except Exception as error:
        # Activation is class-isolated by design, but an unexpected service
        # failure must still leave authentication and the rest of the school
        # application available for diagnosis.
        coming_session_activation_result = {
            "success": False,
            "errors": [
                "Automatic Coming Session activation could not be completed: "
                f"{type(error).__name__}: {error}"
            ],
        }

    # Resume automatic-backup retries only after pending restore processing,
    # schema initialization, and due session activation have finished.
    try:
        backup_scheduler_start_result = start_automatic_backup_scheduler()
    except Exception as error:
        backup_scheduler_start_result = {
            "success": False,
            "message": (
                "Automatic backup scheduling could not be started: "
                f"{type(error).__name__}: {error}"
            ),
        }

    # Before the application fully closes, cancel the normal debounce
    # delay and wait until every pending/running backup has completed.
    application.aboutToQuit.connect(
        complete_pending_backups_before_exit
    )

    # Keep the existing stylesheet for page-specific compatibility.
    load_global_stylesheet(application)

    # Apply the new central visual theme to the entire application.
    apply_global_theme(application)

    # A valid licence allows the authentication screen to open, but the
    # school dashboard remains protected until username/password login
    # succeeds. On first use, create the one administrator identity first.
    if not run_authentication_gate():
        # Startup may have performed a due activation or recovered pending
        # backup work before the administrator cancelled login. QApplication
        # never enters its event loop in this path, so aboutToQuit is not a
        # reliable place to flush that work.
        complete_pending_backups_before_exit()
        return

    main_window = MainWindow()
    main_window.show()

    startup_archive_result = getattr(
        main_window,
        "startup_archive_result",
        {},
    )
    startup_configuration_errors = startup_archive_result.get(
        "configuration_errors",
        {},
    )

    if startup_configuration_errors:
        error_lines = list(startup_configuration_errors.values())
        QMessageBox.warning(
            main_window,
            "Historical Fee Configuration Required",
            (
                "The application opened safely, but one or more Dropped "
                "students could not be archived because a historical class "
                "fee is missing. No partial fee rows were saved.\n\n"
                + "\n\n".join(error_lines[:10])
                + (
                    f"\n\nAnd {len(error_lines) - 10} more record(s)."
                    if len(error_lines) > 10
                    else ""
                )
            ),
        )

    if coming_session_activation_result.get("errors"):
        QMessageBox.warning(
            main_window,
            "Coming-Session Activation Requires Attention",
            "Some coming-session admissions could not be activated safely.\n\n"
            + "\n".join(coming_session_activation_result["errors"][:20]),
        )

    if not backup_scheduler_start_result.get("success", False):
        QMessageBox.warning(
            main_window,
            "Automatic Backup Requires Attention",
            backup_scheduler_start_result.get(
                "message",
                "Automatic backup scheduling could not be started. "
                "Use Backup Management to create a manual verified backup "
                "before continuing important work.",
            ),
        )

    if restore_result.get("startup_warning", False):
        QMessageBox.warning(
            main_window,
            "Pending Restore Quarantined",
            restore_result.get(
                "message",
                "A damaged pending restore request was quarantined. "
                "The existing school database was not changed.",
            ),
        )

    return application.exec()


if __name__ == "__main__":
    exit_code = 0

    try:
        result = main()
        exit_code = int(
            result or 0
        )

    except (
        KeyboardInterrupt,
        SystemExit,
    ):
        raise

    except BaseException:
        # Startup/shutdown exceptions occur outside Qt's event-loop callback
        # handling, so report them explicitly before the process exits.
        handle_unhandled_exception(
            *sys.exc_info(),
            origin=(
                "Top-level application startup or shutdown exception"
            ),
        )
        exit_code = 1

    finally:
        finalize_crash_catcher()

    raise SystemExit(
        exit_code
    )