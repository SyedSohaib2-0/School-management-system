"""
Multi-destination Backup Management interface.

Visible backup destinations:
1. User-selected computer folder
2. Google Drive folder

Hidden internal backups are managed automatically by the backup
service and are deliberately not displayed on this page.
"""

import os

from PySide6.QtCore import (
    Qt,
    QUrl,
    QObject,
    Signal,
    QRunnable,
    QThreadPool,
)
from PySide6.QtGui import (
    QDesktopServices,
)
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
    QHeaderView,
    QFileDialog,
    QLineEdit,
    QCheckBox,
    QTabWidget,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
)

from app import navigation
from app.services.backup_service import (
    create_manual_selected_folder_backup,
    create_manual_google_drive_backup,
    stage_database_restore,
    get_reversible_restore_operation,
    list_destination_backup_metadata,
    validate_destination_backup_record,
    SELECTED_FOLDER_BACKUP_PREFIX,
    GOOGLE_DRIVE_BACKUP_PREFIX,
)
from app.services.backup_settings_service import (
    load_backup_settings,
    save_backup_settings,
)
from app.services.backup_crypto_service import (
    backup_requires_recovery_password,
    configure_backup_security,
    upgrade_backup_security,
    get_backup_security_status,
    is_encrypted_backup,
    recover_backup_security_from_backup,
)


class _BackupValidationSignals(QObject):
    started = Signal(int, str)
    result = Signal(int, str, dict)
    finished = Signal(int)


class _BackupValidationWorker(QRunnable):
    """Sequential, cancellable backup validation outside the UI thread."""

    def __init__(self, token: int, records: list[dict]):
        super().__init__()
        self.token = int(token)
        self.records = list(records)
        self.signals = _BackupValidationSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            for record in self.records:
                if self._cancelled:
                    break
                path = str(record.get("backup_path") or "")
                self.signals.started.emit(self.token, path)
                try:
                    result = validate_destination_backup_record(path)
                except Exception as error:
                    result = {
                        "is_valid": False,
                        "status_text": "Validation Failed",
                        "validation_message": str(error),
                    }
                if self._cancelled:
                    break
                self.signals.result.emit(self.token, path, result)
        finally:
            self.signals.finished.emit(self.token)


class BackupManagementPage(QWidget):
    """Manage external and cloud backup destinations."""

    def __init__(
        self,
        main_window,
    ):
        super().__init__()

        self.main_window = main_window
        self.settings = {}
        self._validation_tokens = {}
        self._validation_workers = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)

        self._build_user_interface()

    def reset_page(self) -> None:
        """Load settings and backup inventories when the page is opened."""
        self._load_saved_settings()
        self.refresh_all_backup_tables()

    # -----------------------------------------------------------------
    # User interface
    # -----------------------------------------------------------------

    def _build_user_interface(
        self,
    ) -> None:
        """Build the complete Backup Management screen."""
        main_layout = QVBoxLayout(self)

        main_layout.setContentsMargins(
            26,
            22,
            26,
            24,
        )

        main_layout.setSpacing(
            12
        )

        title_label = QLabel(
            "Backup Management"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        main_layout.addWidget(
            title_label
        )

        information_label = QLabel(
            "Internal backups are created automatically and remain "
            "hidden. The selected folder and local Google Drive for "
            "Desktop folder can also receive automatic backups. "
            "Online cloud synchronization is managed by Google Drive "
            "for Desktop and is not verified by this software."
        )

        information_label.setWordWrap(
            True
        )

        main_layout.addWidget(
            information_label
        )

        self._build_backup_security_section(
            main_layout
        )

        self.backup_tabs = QTabWidget()

        self.selected_folder_tab = QWidget()
        self.google_drive_tab = QWidget()

        self.backup_tabs.addTab(
            self.selected_folder_tab,
            "Selected Folder",
        )


        self.backup_tabs.addTab(
            self.google_drive_tab,
            "Google Drive",
        )

        self._build_selected_folder_tab()
        self._build_google_drive_tab()

        main_layout.addWidget(
            self.backup_tabs,
            1,
        )

        bottom_layout = QHBoxLayout()

        refresh_all_button = QPushButton(
            "Refresh All"
        )

        refresh_all_button.setObjectName(
            "secondaryButton"
        )

        refresh_all_button.clicked.connect(
            self.refresh_all_backup_tables
        )

        bottom_layout.addWidget(
            refresh_all_button
        )

        restore_button = QPushButton(
            "Restore Selected Backup"
        )

        restore_button.setObjectName(
            "dangerButton"
        )

        restore_button.clicked.connect(
            self._restore_selected_backup
        )

        bottom_layout.addWidget(
            restore_button
        )

        reverse_restore_button = QPushButton(
            "Reverse Last Restore"
        )

        reverse_restore_button.setObjectName(
            "secondaryButton"
        )

        reverse_restore_button.clicked.connect(
            self._reverse_last_restore
        )

        bottom_layout.addWidget(
            reverse_restore_button
        )

        bottom_layout.addStretch()

        back_button = QPushButton(
            "Back to Main Dashboard"
        )

        back_button.setObjectName(
            "secondaryButton"
        )

        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_MAIN_DASHBOARD
            )
        )

        bottom_layout.addWidget(
            back_button
        )

        main_layout.addLayout(
            bottom_layout
        )

    def _build_backup_security_section(
        self,
        main_layout: QVBoxLayout,
    ) -> None:
        """Display backup-encryption status and first-time setup."""
        security_group = QGroupBox(
            "Backup Encryption"
        )
        security_layout = QHBoxLayout(
            security_group
        )

        self.backup_encryption_status_label = QLabel()
        self.backup_encryption_status_label.setWordWrap(
            True
        )
        security_layout.addWidget(
            self.backup_encryption_status_label,
            1,
        )

        self.configure_backup_encryption_button = QPushButton(
            "Configure Encryption"
        )
        self.configure_backup_encryption_button.clicked.connect(
            self._configure_backup_encryption
        )
        security_layout.addWidget(
            self.configure_backup_encryption_button
        )

        main_layout.addWidget(
            security_group
        )
        self._refresh_backup_security_status()

    def _refresh_backup_security_status(
        self,
    ) -> None:
        """Refresh the visible encryption status without exposing secrets."""
        status = get_backup_security_status()
        if status.get(
            "usable",
            False,
        ):
            if status.get("upgrade_required", False):
                self.backup_encryption_status_label.setText(
                    "Backup Encryption: Upgrade Required\n"
                    "Re-enter the recovery password once to enable a unique "
                    "Scrypt salt for every new backup."
                )
                self.configure_backup_encryption_button.setText(
                    "Upgrade Encryption"
                )
                self.configure_backup_encryption_button.setEnabled(True)
                return
            self.backup_encryption_status_label.setText(
                "Backup Encryption: Configured\n"
                "Recovery Method: Password + Windows Protection\n"
                "Encryption: AES-256-GCM"
            )
            self.configure_backup_encryption_button.setText(
                "Configured"
            )
            self.configure_backup_encryption_button.setEnabled(
                False
            )
            return

        self.backup_encryption_status_label.setText(
            "Backup Encryption: "
            f"{status.get('status_text', 'Not Configured')}\n"
            f"{status.get('message', '')}"
        )
        self.configure_backup_encryption_button.setText(
            "Recover from Backup" if status.get("configured", False) else "Configure Encryption"
        )
        self.configure_backup_encryption_button.setEnabled(
            True
        )

    def _configure_backup_encryption(
        self,
    ) -> bool:
        """Create the recovery password and Windows-protected backup key."""
        status = get_backup_security_status()
        if status.get(
            "usable",
            False,
        ):
            if status.get("upgrade_required", False):
                password, accepted = QInputDialog.getText(
                    self,
                    "Upgrade Backup Encryption",
                    "Enter the existing Backup Recovery Password:",
                    QLineEdit.EchoMode.Password,
                )
                if not accepted:
                    return False
                result = upgrade_backup_security(password)
                if not result.get("success", False):
                    QMessageBox.warning(
                        self,
                        "Encryption Upgrade Failed",
                        result.get("message", "Encryption could not be upgraded."),
                    )
                    return False
                QMessageBox.information(
                    self,
                    "Backup Encryption Upgraded",
                    result.get("message", "Encryption was upgraded successfully."),
                )
                self._refresh_backup_security_status()
                return True
            QMessageBox.information(
                self,
                "Backup Encryption",
                "Backup encryption is already configured.",
            )
            return True

        if status.get(
            "configured",
            False,
        ):
            backup_path, _ = QFileDialog.getOpenFileName(
                self, "Select an Existing Encrypted Backup", "", "Hira Backup (*.hmsbackup)"
            )
            if not backup_path:
                return False
            password, accepted = QInputDialog.getText(
                self, "Recover Backup Encryption", "Enter the Backup Recovery Password:",
                QLineEdit.EchoMode.Password,
            )
            if not accepted:
                return False
            result = recover_backup_security_from_backup(backup_path, password)
            password = None
            if not result.get("success", False):
                QMessageBox.critical(self, "Recovery Failed", result.get("message", "Backup security could not be recovered."))
                return False
            QMessageBox.information(self, "Backup Encryption Recovered", result.get("message", "Backup encryption was recovered."))
            self._refresh_backup_security_status()
            return True

        dialog = QDialog(
            self
        )
        dialog.setWindowTitle(
            "Configure Backup Encryption"
        )
        dialog.setMinimumWidth(
            520
        )
        dialog_layout = QVBoxLayout(
            dialog
        )

        explanation = QLabel(
            "Create one Backup Recovery Password. New internal, selected-folder, "
            "Google Drive, and pre-restore backups will be encrypted automatically."
        )
        explanation.setWordWrap(
            True
        )
        dialog_layout.addWidget(
            explanation
        )

        form_layout = QFormLayout()
        password_edit = QLineEdit()
        password_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        confirm_edit = QLineEdit()
        confirm_edit.setEchoMode(
            QLineEdit.EchoMode.Password
        )
        form_layout.addRow(
            "Backup Recovery Password:",
            password_edit,
        )
        form_layout.addRow(
            "Confirm Recovery Password:",
            confirm_edit,
        )
        dialog_layout.addLayout(
            form_layout
        )

        show_password_check = QCheckBox(
            "Show password"
        )

        show_password_check.toggled.connect(
            lambda visible: (
                password_edit.setEchoMode(
                    QLineEdit.EchoMode.Normal
                    if visible
                    else QLineEdit.EchoMode.Password
                ),
                confirm_edit.setEchoMode(
                    QLineEdit.EchoMode.Normal
                    if visible
                    else QLineEdit.EchoMode.Password
                ),
            )
        )

        dialog_layout.addWidget(
            show_password_check
        )

        policy_label = QLabel(
            "Minimum 12 characters with uppercase, lowercase, number, and symbol."
        )
        policy_label.setWordWrap(
            True
        )
        dialog_layout.addWidget(
            policy_label
        )

        acknowledgement = QCheckBox(
            "I understand that the recovery password is never stored in readable "
            "plain-text form. Losing both the password and authorised Windows key may "
            "make external backups permanently unrecoverable."
        )
        dialog_layout.addWidget(
            acknowledgement
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        dialog_layout.addWidget(
            buttons
        )
        buttons.rejected.connect(
            dialog.reject
        )

        def attempt_configuration() -> None:
            if not acknowledgement.isChecked():
                QMessageBox.warning(
                    dialog,
                    "Acknowledgement Required",
                    "Confirm that you understand the recovery-password warning.",
                )
                return
            result = configure_backup_security(
                password_edit.text(),
                confirm_edit.text(),
            )
            if not result.get(
                "success",
                False,
            ):
                QMessageBox.warning(
                    dialog,
                    "Encryption Could Not Be Configured",
                    result.get(
                        "message",
                        "Backup encryption could not be configured.",
                    ),
                )
                return
            QMessageBox.information(
                dialog,
                "Backup Encryption Configured",
                result.get(
                    "message",
                    "Backup encryption was configured successfully.",
                ),
            )
            dialog.accept()

        buttons.accepted.connect(
            attempt_configuration
        )
        configured = (
            dialog.exec()
            == QDialog.DialogCode.Accepted
        )
        self._refresh_backup_security_status()
        return configured

    def _ensure_backup_security_configured(
        self,
    ) -> bool:
        """Require encryption setup before creating a new backup."""
        status = get_backup_security_status()
        if status.get(
            "usable",
            False,
        ) and not status.get("upgrade_required", False):
            return True
        return self._configure_backup_encryption()

    def _request_backup_recovery_password(
        self,
        backup_path: str,
    ) -> str | None:
        """Request a password only for a foreign encrypted backup key."""
        if not is_encrypted_backup(
            backup_path
        ):
            return None
        if not backup_requires_recovery_password(
            backup_path
        ):
            return None

        password, accepted = QInputDialog.getText(
            self,
            "Backup Recovery Password",
            (
                "This encrypted backup was created with a different Windows "
                "key. Enter its Backup Recovery Password:"
            ),
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return None
        return password

    def _build_selected_folder_tab(
        self,
    ) -> None:
        """Build controls for the selected computer folder."""
        layout = QVBoxLayout(
            self.selected_folder_tab
        )

        layout.setContentsMargins(
            18,
            18,
            18,
            18,
        )

        layout.setSpacing(
            12
        )

        description_label = QLabel(
            "Select a folder on this computer or another connected "
            "storage location. Automatic backup will run after a "
            "successful database-changing operation."
        )

        description_label.setWordWrap(
            True
        )

        layout.addWidget(
            description_label
        )

        self.selected_folder_enabled_checkbox = QCheckBox(
            "Enable automatic backup to selected folder"
        )

        layout.addWidget(
            self.selected_folder_enabled_checkbox
        )

        path_layout = QHBoxLayout()

        self.selected_folder_path_edit = QLineEdit()

        self.selected_folder_path_edit.setReadOnly(
            True
        )

        self.selected_folder_path_edit.setPlaceholderText(
            "No folder selected"
        )

        path_layout.addWidget(
            self.selected_folder_path_edit,
            1,
        )

        select_folder_button = QPushButton(
            "Select Folder"
        )

        select_folder_button.clicked.connect(
            self._select_computer_folder
        )

        path_layout.addWidget(
            select_folder_button
        )

        layout.addLayout(
            path_layout
        )

        action_layout = QHBoxLayout()

        selected_new_backup_button = QPushButton(
            "New Backup"
        )

        selected_new_backup_button.clicked.connect(
            self._create_selected_folder_backup
        )

        action_layout.addWidget(
            selected_new_backup_button
        )

        selected_open_folder_button = QPushButton(
            "Open Folder"
        )

        selected_open_folder_button.setObjectName(
            "secondaryButton"
        )

        selected_open_folder_button.clicked.connect(
            lambda: self._open_configured_folder(
                self.selected_folder_path_edit.text(),
                "Selected backup folder",
            )
        )

        action_layout.addWidget(
            selected_open_folder_button
        )

        action_layout.addStretch()

        retention_label = QLabel(
            "Retention: latest 10 backups"
        )

        retention_label.setStyleSheet(
            "font-weight: 600;"
        )

        action_layout.addWidget(
            retention_label
        )

        layout.addLayout(
            action_layout
        )

        self.selected_folder_table = (
            self._create_backup_table()
        )

        layout.addWidget(
            self.selected_folder_table,
            1,
        )

    def _build_google_drive_tab(
        self,
    ) -> None:
        """Build controls for the local Google Drive backup folder."""
        layout = QVBoxLayout(
            self.google_drive_tab
        )

        layout.setContentsMargins(
            18,
            18,
            18,
            18,
        )

        layout.setSpacing(
            12
        )

        description_label = QLabel(
            "Select a local folder managed by Google Drive for "
            "Desktop. Automatic and manual backups are copied and "
            "verified in this local folder. Online cloud upload is "
            "performed separately by Google Drive for Desktop and "
            "is not verified by this software."
        )

        description_label.setWordWrap(
            True
        )

        layout.addWidget(
            description_label
        )

        self.google_drive_enabled_checkbox = QCheckBox(
            "Enable automatic backup to the local Google Drive folder"
        )

        layout.addWidget(
            self.google_drive_enabled_checkbox
        )

        path_layout = QHBoxLayout()

        self.google_drive_path_edit = QLineEdit()

        self.google_drive_path_edit.setReadOnly(
            True
        )

        self.google_drive_path_edit.setPlaceholderText(
            "No Google Drive folder selected"
        )

        path_layout.addWidget(
            self.google_drive_path_edit,
            1,
        )

        select_google_drive_button = QPushButton(
            "Select Google Drive Folder"
        )

        select_google_drive_button.clicked.connect(
            self._select_google_drive_folder
        )

        path_layout.addWidget(
            select_google_drive_button
        )

        layout.addLayout(
            path_layout
        )

        action_layout = QHBoxLayout()

        google_new_backup_button = QPushButton(
            "New Backup"
        )

        google_new_backup_button.clicked.connect(
            self._create_google_drive_backup
        )

        action_layout.addWidget(
            google_new_backup_button
        )

        google_open_folder_button = QPushButton(
            "Open Google Drive Folder"
        )

        google_open_folder_button.setObjectName(
            "secondaryButton"
        )

        google_open_folder_button.clicked.connect(
            lambda: self._open_configured_folder(
                self.google_drive_path_edit.text(),
                "Google Drive backup folder",
            )
        )

        action_layout.addWidget(
            google_open_folder_button
        )

        action_layout.addStretch()

        retention_label = QLabel(
            "Retention: latest 20 backups"
        )

        retention_label.setStyleSheet(
            "font-weight: 600;"
        )

        action_layout.addWidget(
            retention_label
        )

        layout.addLayout(
            action_layout
        )

        self.google_drive_table = (
            self._create_backup_table()
        )

        layout.addWidget(
            self.google_drive_table,
            1,
        )

    def _create_backup_table(
        self,
    ) -> QTableWidget:
        """Create a standard read-only backup table."""
        table = QTableWidget()

        table.setColumnCount(
            4
        )

        table.setHorizontalHeaderLabels(
            [
                "Backup File",
                "Created At",
                "File Size",
                "Status",
            ]
        )

        table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )

        table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )

        table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )

        table.setAlternatingRowColors(
            True
        )

        table.verticalHeader().setVisible(
            False
        )

        header = table.horizontalHeader()

        header.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )

        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )

        header.setSectionResizeMode(
            2,
            QHeaderView.ResizeMode.ResizeToContents,
        )

        header.setSectionResizeMode(
            3,
            QHeaderView.ResizeMode.ResizeToContents,
        )

        return table

    # -----------------------------------------------------------------
    # Settings
    # -----------------------------------------------------------------

    def _load_saved_settings(
        self,
    ) -> None:
        """Load saved destination paths and automatic settings."""
        try:
            self.settings = (
                load_backup_settings()
            )

        except Exception as error:
            QMessageBox.warning(
                self,
                "Backup Settings",
                (
                    "Backup settings could not be loaded. "
                    "Safe default settings will be used.\n\n"
                    f"Technical details: {error}"
                ),
            )

            self.settings = {}

        self.selected_folder_path_edit.setText(
            self.settings.get(
                "selected_folder_path",
                "",
            )
        )


        self.google_drive_path_edit.setText(
            self.settings.get(
                "google_drive_path",
                "",
            )
        )

        self.selected_folder_enabled_checkbox.setChecked(
            self.settings.get(
                "selected_folder_enabled",
                False,
            )
        )


        self.google_drive_enabled_checkbox.setChecked(
            self.settings.get(
                "google_drive_enabled",
                False,
            )
        )

        self.selected_folder_enabled_checkbox.toggled.connect(
            self._save_settings
        )


        self.google_drive_enabled_checkbox.toggled.connect(
            self._save_settings
        )

    def _save_settings(
        self,
    ) -> bool:
        """Save all visible destination settings safely."""
        try:
            settings = load_backup_settings()

            settings[
                "selected_folder_enabled"
            ] = (
                self.selected_folder_enabled_checkbox.isChecked()
            )

            settings[
                "selected_folder_path"
            ] = (
                self.selected_folder_path_edit.text().strip()
            )

            settings[
                "selected_folder_retention"
            ] = 10


            settings[
                "google_drive_enabled"
            ] = (
                self.google_drive_enabled_checkbox.isChecked()
            )

            settings[
                "google_drive_path"
            ] = (
                self.google_drive_path_edit.text().strip()
            )

            settings[
                "google_drive_retention"
            ] = 20

            settings[
                "google_drive_manual_only"
            ] = False

            self.settings = save_backup_settings(
                settings
            )

            return True

        except Exception as error:
            QMessageBox.critical(
                self,
                "Settings Could Not Be Saved",
                (
                    "The backup settings could not be saved.\n\n"
                    f"Technical details: {error}"
                ),
            )

            return False

    # -----------------------------------------------------------------
    # Folder selection
    # -----------------------------------------------------------------

    def _select_computer_folder(
        self,
    ) -> None:
        """Select the automatic computer backup folder."""
        selected_path = QFileDialog.getExistingDirectory(
            self,
            "Select Computer Backup Folder",
            self.selected_folder_path_edit.text()
            or os.path.expanduser("~"),
        )

        if not selected_path:
            return

        self.selected_folder_path_edit.setText(
            selected_path
        )

        self.selected_folder_enabled_checkbox.setChecked(
            True
        )

        self._save_settings()
        self.refresh_selected_folder_table()

    def _select_google_drive_folder(
        self,
    ) -> None:
        """Select a folder inside Google Drive for Desktop."""

        selected_path = QFileDialog.getExistingDirectory(
            self,
            "Select Google Drive Backup Folder",
            self.google_drive_path_edit.text()
            or os.path.expanduser("~"),
        )

        if not selected_path:
            return

        self.google_drive_path_edit.setText(
            selected_path
        )

        self._save_settings()
        self.refresh_google_drive_table()

    # -----------------------------------------------------------------
    # Manual backup actions
    # -----------------------------------------------------------------

    def _create_selected_folder_backup(
        self,
    ) -> None:
        """Create a manual encrypted selected-folder backup."""
        if not self._ensure_backup_security_configured():
            return
        self._save_settings()
        result = create_manual_selected_folder_backup()
        self._show_backup_result(result)
        self.refresh_selected_folder_table()

    def _create_google_drive_backup(
        self,
    ) -> None:
        """Create a manual encrypted Google Drive-folder backup."""
        if not self._ensure_backup_security_configured():
            return
        self._save_settings()
        result = create_manual_google_drive_backup()
        self._show_backup_result(result)
        self.refresh_google_drive_table()

    def _show_backup_result(
        self,
        result: dict,
    ) -> None:
        """Show a controlled backup success or failure message."""
        if result.get("status") == "partial_success" or (
            result.get("backup_created") and not result.get("retention_success", True)
        ):
            failed_paths = "\n".join(result.get("failed_retention_paths", []) or ["-"])
            QMessageBox.warning(
                self,
                "Backup Created — Retention Warning",
                "Backup was created successfully, but retention cleanup failed.\n\n"
                f"Backup:\n{result.get('backup_path', '-')}\n\n"
                f"Could not remove:\n{failed_paths}\n\n"
                f"Reason:\n{result.get('retention_error') or result.get('message', '-')}",
            )
            return

        if result.get(
            "success",
            False,
        ):
            QMessageBox.information(
                self,
                "Backup Created",
                (
                    f"{result.get('message', 'Backup created.')}\n\n"
                    f"Backup File:\n"
                    f"{result.get('backup_name', '')}"
                ),
            )

            return

        QMessageBox.warning(
            self,
            "Backup Not Created",
            result.get(
                "message",
                "The backup could not be created.",
            ),
        )

    # -----------------------------------------------------------------
    # Table refresh
    # -----------------------------------------------------------------

    def refresh_all_backup_tables(
        self,
    ) -> None:
        """Refresh all visible backup destinations."""
        self.refresh_selected_folder_table()
        self.refresh_google_drive_table()

    def refresh_selected_folder_table(self) -> None:
        self._refresh_destination_table(
            self.selected_folder_table,
            self.selected_folder_path_edit.text().strip(),
            SELECTED_FOLDER_BACKUP_PREFIX,
        )

    def refresh_google_drive_table(self) -> None:
        self._refresh_destination_table(
            self.google_drive_table,
            self.google_drive_path_edit.text().strip(),
            GOOGLE_DRIVE_BACKUP_PREFIX,
        )

    def _refresh_destination_table(
        self,
        table: QTableWidget,
        path: str,
        prefix: str,
    ) -> None:
        key = id(table)
        previous = self._validation_workers.get(key)
        if previous is not None:
            previous.cancel()
        token = int(self._validation_tokens.get(key, 0)) + 1
        self._validation_tokens[key] = token
        records = list_destination_backup_metadata(path, prefix)
        self._populate_backup_table(table, records)
        if not records:
            self._validation_workers.pop(key, None)
            return
        worker = _BackupValidationWorker(token, records)
        self._validation_workers[key] = worker
        worker.signals.started.connect(
            lambda emitted_token, backup_path, target=table:
                self._apply_backup_validation_started(
                    target, emitted_token, backup_path
                )
        )
        worker.signals.result.connect(
            lambda emitted_token, backup_path, result, target=table:
                self._apply_backup_validation_result(
                    target, emitted_token, backup_path, result
                )
        )
        worker.signals.finished.connect(
            lambda emitted_token, target=table:
                self._finish_backup_validation(target, emitted_token)
        )
        self._thread_pool.start(worker)

    def _apply_backup_validation_started(
        self,
        table: QTableWidget,
        token: int,
        backup_path: str,
    ) -> None:
        if int(self._validation_tokens.get(id(table), 0)) != int(token):
            return
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == backup_path:
                table.setItem(row, 3, QTableWidgetItem("Validating"))
                break

    def _apply_backup_validation_result(
        self,
        table: QTableWidget,
        token: int,
        backup_path: str,
        result: dict,
    ) -> None:
        if int(self._validation_tokens.get(id(table), 0)) != int(token):
            return
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and str(item.data(Qt.ItemDataRole.UserRole) or "") == backup_path:
                table.setItem(row, 3, QTableWidgetItem(result.get("status_text", "Validation Failed")))
                status_item = table.item(row, 3)
                status_item.setToolTip(result.get("validation_message", ""))
                break

    def _finish_backup_validation(self, table: QTableWidget, token: int) -> None:
        key = id(table)
        if int(self._validation_tokens.get(key, 0)) == int(token):
            self._validation_workers.pop(key, None)

    def closeEvent(self, event) -> None:
        for worker in list(self._validation_workers.values()):
            worker.cancel()
        self._thread_pool.clear()
        self._validation_workers.clear()
        super().closeEvent(event)

    def _populate_backup_table(
        self,
        table: QTableWidget,
        records: list[dict],
    ) -> None:
        """Display encrypted and legacy backup records."""
        table.setRowCount(len(records))
        for row_number, record in enumerate(records):
            name_item = QTableWidgetItem(record["backup_name"])
            name_item.setData(
                Qt.ItemDataRole.UserRole,
                record["backup_path"],
            )
            created_item = QTableWidgetItem(record["created_at"])
            size_item = QTableWidgetItem(record["file_size_display"])
            status_item = QTableWidgetItem(
                record.get(
                    "status_text",
                    "Valid" if record.get("is_valid") else "Invalid",
                )
            )
            status_item.setToolTip(record["validation_message"])
            table.setItem(row_number, 0, name_item)
            table.setItem(row_number, 1, created_item)
            table.setItem(row_number, 2, size_item)
            table.setItem(row_number, 3, status_item)
        if records:
            table.selectRow(0)
    # -----------------------------------------------------------------
    # Backup restoration
    # -----------------------------------------------------------------

    def _get_current_backup_table(
        self,
    ) -> QTableWidget:
        """
        Return the backup table belonging to the currently selected tab.
        """
        current_tab = (
            self.backup_tabs.currentWidget()
        )


        if current_tab is self.google_drive_tab:
            return self.google_drive_table

        return self.selected_folder_table

    def _get_selected_backup_path(
        self,
    ) -> str | None:
        """
        Return the selected backup path from the currently active tab.
        """
        current_table = (
            self._get_current_backup_table()
        )

        selected_row = (
            current_table.currentRow()
        )

        if selected_row < 0:
            return None

        backup_name_item = current_table.item(
            selected_row,
            0,
        )

        if backup_name_item is None:
            return None

        backup_path = backup_name_item.data(
            Qt.ItemDataRole.UserRole
        )

        if not backup_path:
            return None

        return str(
            backup_path
        )

    def _restore_selected_backup(
        self,
    ) -> None:
        """Authenticate and stage the selected backup for next startup."""
        selected_backup_path = self._get_selected_backup_path()
        if not selected_backup_path:
            QMessageBox.warning(
                self,
                "No Backup Selected",
                "Select one backup from the current backup table before restoring.",
            )
            return

        backup_name = os.path.basename(selected_backup_path)
        encrypted = is_encrypted_backup(selected_backup_path)
        if not encrypted and not self._ensure_backup_security_configured():
            return
        legacy_warning = (
            ""
            if encrypted
            else (
                "\n\nWARNING: This is an older unencrypted backup. It may "
                "contain readable confidential school data. Restore it only "
                "when its source is trusted."
            )
        )
        confirmation = QMessageBox.warning(
            self,
            "Confirm Database Restoration",
            (
                "Restoring this backup will replace the current school database "
                "during the next startup.\n\n"
                f"Selected backup:\n{backup_name}\n\n"
                "Any data entered after this backup was created will no longer "
                "appear in the active database.\n\n"
                "Before replacement, the current database will be saved as an "
                "encrypted emergency backup.\n\n"
                "The software will close after preparing the restore. Open it "
                "again to complete the restoration safely."
                f"{legacy_warning}\n\nDo you want to continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        recovery_password = self._request_backup_recovery_password(
            selected_backup_path
        )
        if (
            encrypted
            and backup_requires_recovery_password(selected_backup_path)
            and recovery_password is None
        ):
            return

        result = stage_database_restore(
            selected_backup_path=selected_backup_path,
            record_history=True,
            recovery_password=recovery_password,
        )
        recovery_password = None
        if not result.get("success", False):
            QMessageBox.critical(
                self,
                "Restore Could Not Be Prepared",
                result.get(
                    "message",
                    "The selected backup could not be prepared for restoration.",
                ),
            )
            return

        pending_request = result.get("pending_request", {})
        QMessageBox.information(
            self,
            "Restore Prepared",
            (
                f"{result.get('message', '')}\n\n"
                f"Selected backup:\n{backup_name}\n\n"
                "Restore request created at:\n"
                f"{pending_request.get('requested_at', 'Unknown')}\n\n"
                "The software will now close. Open it again to complete the "
                "database restoration."
            ),
        )
        QApplication.quit()


    def _reverse_last_restore(
        self,
    ) -> None:
        """Stage the latest reversible encrypted emergency backup."""
        reversible_result = get_reversible_restore_operation()
        if not reversible_result.get("success", False):
            QMessageBox.warning(
                self,
                "Reverse Restore Unavailable",
                reversible_result.get(
                    "message",
                    "No reversible restore operation is available.",
                ),
            )
            return

        restore_operation = reversible_result["restore_operation"]
        emergency_backup_path = restore_operation.get(
            "pre_restore_backup_path",
            "",
        )
        emergency_backup_name = os.path.basename(emergency_backup_path)
        restored_at = restore_operation.get("restored_at", "Unknown")
        restore_id = restore_operation.get("restore_id")
        confirmation = QMessageBox.warning(
            self,
            "Confirm Reverse Restore",
            (
                "This will reverse the most recent recorded database restore "
                "during the next startup.\n\n"
                f"Emergency backup to restore:\n{emergency_backup_name}\n\n"
                f"Original restore performed at: {restored_at}\n\n"
                "Before replacement, the current database will be saved as "
                "another encrypted emergency backup.\n\n"
                "This restore operation can be reversed only once.\n\n"
                "Do you want to continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        recovery_password = self._request_backup_recovery_password(
            emergency_backup_path
        )
        if (
            is_encrypted_backup(emergency_backup_path)
            and backup_requires_recovery_password(emergency_backup_path)
            and recovery_password is None
        ):
            return

        result = stage_database_restore(
            selected_backup_path=emergency_backup_path,
            record_history=False,
            request_type="reverse_restore",
            target_restore_id=restore_id,
            recovery_password=recovery_password,
        )
        recovery_password = None
        if not result.get("success", False):
            QMessageBox.critical(
                self,
                "Reverse Restore Could Not Be Prepared",
                result.get(
                    "message",
                    "The reverse restore could not be prepared safely.",
                ),
            )
            return

        pending_request = result.get("pending_request", {})
        QMessageBox.information(
            self,
            "Reverse Restore Prepared",
            (
                f"{result.get('message', '')}\n\n"
                "Emergency backup prepared for restoration:\n"
                f"{emergency_backup_name}\n\n"
                "Reverse request created at:\n"
                f"{pending_request.get('requested_at', 'Unknown')}\n\n"
                "The software will now close. Open it again to complete the "
                "reverse restore."
            ),
        )
        QApplication.quit()




    # -----------------------------------------------------------------
    # Folder opening
    # -----------------------------------------------------------------

    def _open_configured_folder(
        self,
        folder_path: str,
        folder_name: str,
    ) -> None:
        """Open a configured folder safely."""
        folder_path = folder_path.strip()

        if not folder_path:
            QMessageBox.warning(
                self,
                "Folder Not Selected",
                f"{folder_name} has not been selected.",
            )

            return

        if not os.path.isdir(
            folder_path
        ):
            QMessageBox.warning(
                self,
                "Folder Unavailable",
                (
                    f"{folder_name} is unavailable.\n\n"
                    "Check that the drive or application "
                    "is connected."
                ),
            )

            return

        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(
                folder_path
            )
        )

        if not opened:
            QMessageBox.warning(
                self,
                "Folder Could Not Open",
                (
                    "Windows could not open the folder "
                    "automatically."
                ),
            )

    def showEvent(
        self,
        event,
    ) -> None:
        """Refresh backup records when this page becomes visible."""
        self.refresh_all_backup_tables()
        super().showEvent(event)
