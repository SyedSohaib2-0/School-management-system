"""
Fee Management module.

This step contains:
1. Fee Management Dashboard
2. Set Fee Dashboard
3. Set Class Fee screen

Pay Fee, Class Status, Set Individual Student Fee, and Full School List
will be added in later steps.

This file handles screen logic only. All fee calculations and database
operations are delegated to app.services.fee_service.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QMessageBox,
    QAbstractSpinBox,
    QDoubleSpinBox,
    QSpinBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QScrollArea,
    QSizePolicy,
    QDialog,
)
from app import navigation
from app.config import (
    SESSION_START_CALENDAR_MONTH,
    SESSION_MONTH_ORDER,
)
from app.services import class_service
from app.services import student_service
from app.services import fee_service
from app.services import sibling_fee_service


FEE_SESSION_FIRST_START_YEAR = 2024
FEE_SESSION_LAST_START_YEAR = 2099

FEE_UI_CLEAN_LAYOUT_V1 = True


def _session_label(
    session_start_year: int,
) -> str:
    """Return an April-March academic-session label."""
    start_year = int(session_start_year)

    return (
        f"{start_year}-"
        f"{start_year + 1}"
    )


def _current_session_start_year() -> int:
    """Return the session-start year for today's date."""
    today = datetime.now()

    if (
        today.month
        >= SESSION_START_CALENDAR_MONTH
    ):
        return today.year

    return today.year - 1


def _calendar_year_for_session_month(
    session_start_year: int,
    month_number: int,
) -> int:
    """
    Convert a selected session and month to calendar year.

    April 2025-2026 -> April 2025
    January 2025-2026 -> January 2026
    """
    start_year = int(session_start_year)
    month_number = int(month_number)

    if (
        month_number
        >= SESSION_START_CALENDAR_MONTH
    ):
        return start_year

    return start_year + 1


def _populate_fee_session_combo(
    combo: QComboBox,
) -> None:
    """Load sessions from 2024-2025 onward."""
    combo.blockSignals(True)
    combo.clear()

    for session_start_year in range(
        FEE_SESSION_FIRST_START_YEAR,
        FEE_SESSION_LAST_START_YEAR + 1,
    ):
        combo.addItem(
            _session_label(
                session_start_year
            ),
            session_start_year,
        )

    current_start_year = (
        _current_session_start_year()
    )

    current_index = combo.findData(
        current_start_year
    )

    if current_index >= 0:
        combo.setCurrentIndex(
            current_index
        )

    combo.blockSignals(False)


# ---------------------------------------------------------------------------
# Fee Management Dashboard
# ---------------------------------------------------------------------------

class FeeManagementDashboardPage(QWidget):
    """Main landing page for the Fee Management module."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title_label = QLabel("Fee Management")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        subtitle_label = QLabel("Select a fee management option:")
        layout.addWidget(subtitle_label)

        button_column = QVBoxLayout()
        button_column.setSpacing(12)

        pay_fee_button = QPushButton("Pay Fee")
        pay_fee_button.clicked.connect(
            self._open_pay_fee
        )
        button_column.addWidget(pay_fee_button)

        class_status_button = QPushButton("Class Status")
        class_status_button.clicked.connect(
            self._open_class_status
        )
        button_column.addWidget(class_status_button)

        set_fee_button = QPushButton("Set Fee")
        set_fee_button.clicked.connect(self._open_set_fee_dashboard)
        button_column.addWidget(set_fee_button)

        full_school_list_button = QPushButton("Full School List")
        full_school_list_button.clicked.connect(
            self._open_full_school_list
        )
        button_column.addWidget(full_school_list_button)

        month_range_button = QPushButton("Sort by Month")
        month_range_button.clicked.connect(
            self._open_month_range_fee_report
        )
        button_column.addWidget(month_range_button)

        layout.addLayout(button_column)

        back_button = QPushButton("Back to Main Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_MAIN_DASHBOARD
            )
        )
        layout.addWidget(back_button)

        layout.addStretch()

    def _open_month_range_fee_report(self):
        """Reset and open the inclusive unpaid-fee month-range report."""
        page = self.main_window.pages[
            navigation.PAGE_MONTH_RANGE_FEE_REPORT
        ]
        page.reset_page(reset_filters=True)
        self.main_window.go_to_page(
            navigation.PAGE_MONTH_RANGE_FEE_REPORT
        )

    def _open_full_school_list(self):
        """Refresh and open the Full School Fee List screen."""
        page = self.main_window.pages[
            navigation.PAGE_FULL_SCHOOL_LIST
        ]

        page.reset_page()

        self.main_window.go_to_page(
            navigation.PAGE_FULL_SCHOOL_LIST
        )

    def _open_class_status(self):
        """Refresh and open the Class Fee Status screen."""
        page = self.main_window.pages[
            navigation.PAGE_CLASS_STATUS
        ]

        page.reload_class_dropdown()
        page.reset_page()

        self.main_window.go_to_page(
            navigation.PAGE_CLASS_STATUS
        )

    def _open_pay_fee(self):
        """Refresh and open the Pay Fee screen."""

        page = self.main_window.pages[
            navigation.PAGE_PAY_FEE_SEARCH
        ]

        page.reload_class_dropdown()
        page.reset_page()

        self.main_window.go_to_page(
            navigation.PAGE_PAY_FEE_SEARCH
        )
    def _open_set_fee_dashboard(self):
        """Open the Set Fee sub-dashboard."""
        self.main_window.go_to_page(
            navigation.PAGE_SET_FEE_DASHBOARD
        )

    def _show_coming_soon(self, screen_name: str):
        """Temporary message for screens built in later steps."""
        QMessageBox.information(
            self,
            "Coming Soon",
            f"{screen_name} will be added in a later step.",
        )


# ---------------------------------------------------------------------------
# Set Fee Dashboard
# ---------------------------------------------------------------------------

class SetFeeDashboardPage(QWidget):
    """Dashboard for class-wise and individual student fee settings."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title_label = QLabel("Set Fee")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        subtitle_label = QLabel("Choose how the fee should be set:")
        layout.addWidget(subtitle_label)

        button_column = QVBoxLayout()
        button_column.setSpacing(12)

        set_class_fee_button = QPushButton("Set Class Fee")
        set_class_fee_button.clicked.connect(
            self._open_set_class_fee
        )
        button_column.addWidget(set_class_fee_button)

        set_individual_fee_button = QPushButton(
            "Set Individual Student Fee"
        )
        set_individual_fee_button.clicked.connect(
            self._open_set_individual_fee
        )
        button_column.addWidget(set_individual_fee_button)

        sibling_fee_button = QPushButton(
            "Sibling Set"
        )
        sibling_fee_button.clicked.connect(
            self._open_sibling_fee
        )
        button_column.addWidget(
            sibling_fee_button
        )

        layout.addLayout(button_column)

        back_button = QPushButton("Back to Fee Management")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_FEE_DASHBOARD
            )
        )
        layout.addWidget(back_button)

        layout.addStretch()

    def _open_set_class_fee(self):
        """Refresh and open the Set Class Fee screen."""

        page = self.main_window.pages[
            navigation.PAGE_SET_CLASS_FEE
        ]

        page.reload_class_dropdown()
        page.reset_page()

        self.main_window.go_to_page(
            navigation.PAGE_SET_CLASS_FEE
        )

    def _open_set_individual_fee(self):
        """Refresh and open the Set Individual Student Fee screen."""

        page = self.main_window.pages[
            navigation.PAGE_SET_INDIVIDUAL_FEE
        ]

        page.reload_class_dropdown()
        page.reset_page()

        self.main_window.go_to_page(
            navigation.PAGE_SET_INDIVIDUAL_FEE
        )

    def _open_sibling_fee(self):
        """Open the Sibling Monthly Fee setting dialog."""
        dialog = SiblingFeeDialog(self)
        dialog.exec()


class SiblingFeeDialog(QDialog):
    """Configure the automatic sibling Monthly Fee percentage."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sibling Set")
        self.setModal(True)
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("Sibling Monthly Fee")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        explanation = QLabel(
            "Active students are treated as siblings only when they "
            "share the same valid 13-digit Father CNIC. The highest-"
            "class sibling pays 100% of the class Monthly Fee; lower-"
            "class siblings pay the percentage entered below. If the "
            "highest class is tied, the earliest Admission Date pays "
            "100%; an identical date is resolved by the lowest "
            "Tracking ID."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.current_setting_label = QLabel(
            "Current Setting: Not configured"
        )
        self.current_setting_label.setWordWrap(True)
        layout.addWidget(self.current_setting_label)

        form = QFormLayout()
        self.percentage_input = QDoubleSpinBox()
        self.percentage_input.setRange(0.00, 100.00)
        self.percentage_input.setDecimals(2)
        self.percentage_input.setSingleStep(1.00)
        self.percentage_input.setSuffix(" %")
        self.percentage_input.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        form.addRow(
            "Lower-sibling Monthly Fee:",
            self.percentage_input,
        )
        layout.addLayout(form)

        effective_note = QLabel(
            "Saving applies from the current calendar month. Only "
            "current/future Unpaid Monthly Fee records are recalculated; "
            "Paid records and earlier months remain unchanged."
        )
        effective_note.setWordWrap(True)
        layout.addWidget(effective_note)

        button_row = QHBoxLayout()
        save_button = QPushButton("Save Sibling Percentage")
        save_button.clicked.connect(self._save_setting)
        button_row.addWidget(save_button)

        close_button = QPushButton("Close")
        close_button.setObjectName("secondaryButton")
        close_button.clicked.connect(self.reject)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

        self._load_setting()

    def _load_setting(self):
        setting = (
            sibling_fee_service.get_current_sibling_fee_setting()
        )
        if not setting:
            self.current_setting_label.setText(
                "Current Setting: Not configured"
            )
            self.percentage_input.setValue(0.00)
            return

        percentage = float(
            setting["monthly_fee_percentage"]
        )
        self.percentage_input.setValue(percentage)
        self.current_setting_label.setText(
            "Current Setting: "
            f"{percentage:.2f}% from "
            f"{int(setting['effective_from_month']):02d}/"
            f"{int(setting['effective_from_year'])}"
        )

    def _save_setting(self):
        percentage = self.percentage_input.value()
        confirmation = QMessageBox.question(
            self,
            "Confirm Sibling Fee",
            (
                "Save the lower-sibling Monthly Fee as "
                f"{percentage:.2f}%?\n\n"
                "The highest eligible sibling will remain at 100%. "
                "Current/future Unpaid Monthly Fee records will be "
                "recalculated automatically."
            ),
        )
        if confirmation != QMessageBox.Yes:
            return

        try:
            result = (
                sibling_fee_service.save_sibling_fee_percentage(
                    monthly_fee_percentage=percentage,
                    created_by="admin",
                )
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Sibling Fee Error",
                f"Sibling fee could not be saved.\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "Sibling Fee Saved",
            (
                f"Sibling Monthly Fee saved at {percentage:.2f}%.\n\n"
                f"Full-fee siblings: {result['full_fee_students']}\n"
                f"Discounted siblings: {result['discounted_students']}\n"
                f"Updated unpaid fee records: "
                f"{result['updated_fee_records']}"
            ),
        )
        self._load_setting()


# ---------------------------------------------------------------------------
# Set Class Fee Screen
# ---------------------------------------------------------------------------

# MONTHLY_ONLY_SET_CLASS_FEE_UI_V8
# The class-fee screen now manages Monthly Fee only.

class SetClassFeePage(QWidget):
    """Set or change Monthly Fee for one class."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(
            40,
            40,
            40,
            40,
        )
        layout.setSpacing(15)

        title_label = QLabel(
            "Set Class Fee"
        )
        title_label.setObjectName(
            "pageTitle"
        )
        layout.addWidget(
            title_label
        )

        instruction_label = QLabel(
            "Select a class and enter its Monthly Fee. "
            "The saved fee will apply from the selected "
            "month and academic session onward. Previous "
            "fee records will remain preserved."
        )
        instruction_label.setWordWrap(
            True
        )
        layout.addWidget(
            instruction_label
        )

        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        self.class_combo = QComboBox()
        self.class_combo.currentIndexChanged.connect(
            self._load_selected_class_fee
        )

        form_layout.addRow(
            "Select Class:",
            self.class_combo,
        )

        self.current_fee_label = QLabel(
            "No class selected."
        )
        self.current_fee_label.setWordWrap(
            True
        )

        form_layout.addRow(
            "Currently Effective Fee:",
            self.current_fee_label,
        )

        self.next_scheduled_fee_label = QLabel("No future fee is scheduled.")
        self.next_scheduled_fee_label.setWordWrap(True)
        form_layout.addRow(
            "Next Scheduled Fee:",
            self.next_scheduled_fee_label,
        )

        self.selected_period_fee_label = QLabel(
            "Select a class and effective period."
        )
        self.selected_period_fee_label.setWordWrap(True)
        form_layout.addRow(
            "Fee at Selected Period:",
            self.selected_period_fee_label,
        )

        self.monthly_fee_input = QDoubleSpinBox()
        self.monthly_fee_input.setRange(
            0.00,
            999999999.00,
        )
        self.monthly_fee_input.setDecimals(
            2
        )
        self.monthly_fee_input.setSingleStep(
            100.00
        )
        self.monthly_fee_input.setPrefix(
            "Rs. "
        )
        self.monthly_fee_input.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        form_layout.addRow(
            "Monthly Fee:",
            self.monthly_fee_input,
        )

        self.effective_month_combo = QComboBox()

        for (
            month_name,
            month_number,
        ) in SESSION_MONTH_ORDER:
            self.effective_month_combo.addItem(
                month_name,
                month_number,
            )

        self.effective_month_combo.currentIndexChanged.connect(
            self._load_selected_class_fee
        )

        form_layout.addRow(
            "Effective From Month:",
            self.effective_month_combo,
        )

        self.effective_session_combo = QComboBox()
        self.effective_session_combo.setMinimumWidth(
            180
        )

        _populate_fee_session_combo(
            self.effective_session_combo
        )

        self.effective_session_combo.currentIndexChanged.connect(
            self._load_selected_class_fee
        )

        form_layout.addRow(
            "Effective Session:",
            self.effective_session_combo,
        )

        layout.addLayout(
            form_layout
        )

        button_row = QHBoxLayout()
        button_row.setSpacing(15)

        save_button = QPushButton(
            "Save Class Fee"
        )
        save_button.clicked.connect(
            self._save_class_fee
        )
        button_row.addWidget(
            save_button
        )

        clear_button = QPushButton(
            "Clear Form"
        )
        clear_button.setObjectName(
            "secondaryButton"
        )
        clear_button.clicked.connect(
            self.reset_page
        )
        button_row.addWidget(
            clear_button
        )

        layout.addLayout(
            button_row
        )

        back_button = QPushButton(
            "Back to Set Fee"
        )
        back_button.setObjectName(
            "secondaryButton"
        )
        back_button.clicked.connect(
            lambda:
            self.main_window.go_to_page(
                navigation.PAGE_SET_FEE_DASHBOARD
            )
        )

        layout.addWidget(
            back_button
        )
        layout.addStretch()

        self.reload_class_dropdown()
        self.reset_page()


    # ------------------------------------------------------------------
    # Form setup
    # ------------------------------------------------------------------

    def reload_class_dropdown(self):
        """Reload all existing classes from Class Management."""
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem("Select Class", None)

        for class_row in class_service.get_all_classes():
            self.class_combo.addItem(
                class_row["class_name"],
                class_row["id"],
            )

        self.class_combo.setCurrentIndex(0)
        self.class_combo.blockSignals(False)

    def reset_page(self):
        """Clear the form and restore the current effective date."""
        self.class_combo.setCurrentIndex(
            0
        )

        self.monthly_fee_input.setValue(
            0.00
        )

        self.current_fee_label.setText(
            "No class selected."
        )
        self.next_scheduled_fee_label.setText(
            "No future fee is scheduled."
        )
        self.selected_period_fee_label.setText(
            "Select a class and effective period."
        )

        self._set_current_effective_date()


    def _set_current_effective_date(self):
        """Set effective month/year to the computer's current date."""
        today = datetime.now()

        month_index = self.effective_month_combo.findData(
            today.month
        )

        if month_index >= 0:
            self.effective_month_combo.setCurrentIndex(
                month_index
            )

        current_session_start = (
            _current_session_start_year()
        )

        session_index = (
            self.effective_session_combo.findData(
                current_session_start
            )
        )

        if session_index >= 0:
            self.effective_session_combo.setCurrentIndex(
                session_index
            )

    def _month_name(self, month_number: int) -> str:
        """Convert a calendar month number into its display name."""
        for month_name, number in SESSION_MONTH_ORDER:
            if number == month_number:
                return month_name

        return str(month_number)

    # ------------------------------------------------------------------
    # Existing class fee
    # ------------------------------------------------------------------

    def _load_selected_class_fee(self):
        """Show actual current, next scheduled, and selected-period fees."""
        class_id = self.class_combo.currentData()
        session_start_year = self.effective_session_combo.currentData()
        selected_month = self.effective_month_combo.currentData()

        self.monthly_fee_input.setValue(0.00)
        if class_id is None:
            self.current_fee_label.setText("No class selected.")
            self.next_scheduled_fee_label.setText("No class selected.")
            self.selected_period_fee_label.setText("No class selected.")
            return
        if session_start_year is None or selected_month is None:
            self.current_fee_label.setText("No academic period selected.")
            self.next_scheduled_fee_label.setText("No academic period selected.")
            self.selected_period_fee_label.setText("No effective period selected.")
            return

        session_start_year = int(session_start_year)
        selected_month = int(selected_month)
        selected_year = (
            session_start_year
            if selected_month >= SESSION_START_CALENDAR_MONTH
            else session_start_year + 1
        )

        actual_schedule = fee_service.get_class_fee_display_schedule(
            class_id=int(class_id),
            as_of=datetime.now(),
        )
        selected_schedule = fee_service.get_class_fee_display_schedule(
            class_id=int(class_id),
            selected_month=selected_month,
            selected_year=selected_year,
        )
        current = actual_schedule.get("currently_effective")
        upcoming = actual_schedule.get("next_scheduled")
        selected_fee = selected_schedule.get("currently_effective")

        if current:
            current_month_name = self._month_name(int(current["effective_from_month"]))
            self.current_fee_label.setText(
                f"Rs. {float(current['monthly_fee']):.2f} | "
                f"effective from {current_month_name} {int(current['effective_from_year'])}."
            )
        else:
            self.current_fee_label.setText("No class fee is currently effective.")

        if upcoming:
            upcoming_month_name = self._month_name(int(upcoming["effective_from_month"]))
            self.next_scheduled_fee_label.setText(
                f"Rs. {float(upcoming['monthly_fee']):.2f} from "
                f"{upcoming_month_name} {int(upcoming['effective_from_year'])}."
            )
        else:
            self.next_scheduled_fee_label.setText("No future fee is scheduled.")

        selected_month_name = self._month_name(selected_month)
        if selected_fee:
            selected_amount = float(selected_fee["monthly_fee"])
            self.monthly_fee_input.setValue(selected_amount)
            self.selected_period_fee_label.setText(
                f"{selected_month_name} {selected_year}: Rs. {selected_amount:.2f}. "
                "Saving creates or corrects the rule for this selected period."
            )
        else:
            self.selected_period_fee_label.setText(
                f"{selected_month_name} {selected_year}: no applicable fee exists. "
                "Saving will create a fee event for this period."
            )


    # ------------------------------------------------------------------
    # Save class fee
    # ------------------------------------------------------------------

    def _save_class_fee(self):
        """Validate and save the selected class's Monthly Fee."""
        class_id = (
            self.class_combo.currentData()
        )

        if class_id is None:
            QMessageBox.warning(
                self,
                "No Class Selected",
                (
                    "Please select a class before "
                    "saving the fee."
                ),
            )
            return

        monthly_fee = (
            self.monthly_fee_input.value()
        )

        effective_month = (
            self.effective_month_combo.currentData()
        )

        session_start_year = (
            self.effective_session_combo.currentData()
        )

        if session_start_year is None:
            QMessageBox.warning(
                self,
                "No Session Selected",
                (
                    "Please select an effective "
                    "academic session."
                ),
            )
            return

        current_session = (
            _current_session_start_year()
        )

        if session_start_year < current_session:
            QMessageBox.warning(
                self,
                "Past Session Locked",
                (
                    "Fees of previous academic "
                    "sessions cannot be modified."
                ),
            )
            return

        effective_year = (
            _calendar_year_for_session_month(
                session_start_year,
                effective_month,
            )
        )

        if not fee_service.validate_class_fee_amount(
            monthly_fee
        ):
            QMessageBox.warning(
                self,
                "Invalid Full Class Fee",
                (
                    "Full Class Monthly Fee must be "
                    "greater than zero. A zero fee may "
                    "only be assigned through an "
                    "individual student fee rule."
                ),
            )
            return

        selected_class = (
            class_service.get_class_by_id(
                class_id
            )
        )

        if selected_class is None:
            QMessageBox.warning(
                self,
                "Class Not Found",
                (
                    "The selected class could "
                    "not be found."
                ),
            )
            return

        class_level = selected_class[
            "class_level"
        ]

        level_classes = (
            class_service.get_classes_by_level(
                class_level
            )
        )

        section_names = ", ".join(
            class_row[
                "class_name"
            ]
            for class_row in level_classes
        )

        sections_with_existing_fee = [
            class_row[
                "class_name"
            ]
            for class_row in level_classes
            if fee_service.get_class_fee_for_session(
                class_id=class_row[
                    "id"
                ],
                session_start_year=(
                    session_start_year
                ),
            )
        ]

        effective_label = (
            f"{self.effective_month_combo.currentText()} | "
            "Session "
            f"{self.effective_session_combo.currentText()}"
        )

        if sections_with_existing_fee:
            existing_names = ", ".join(
                sections_with_existing_fee
            )

            confirmation_message = (
                "A fee is already set for one or more "
                f"sections of class level {class_level}.\n\n"
                f"Existing fee sections: {existing_names}\n\n"
                "The previous fees will remain preserved "
                "in fee history. The new fee will apply "
                "to all these sections:\n"
                f"{section_names}\n\n"
                f"Monthly Fee: Rs. {monthly_fee:.2f}\n"
                f"Effective From: {effective_label}\n\n"
                "Do you want to continue?"
            )

        else:
            confirmation_message = (
                "Set the following fee for every section "
                f"of class level {class_level}?\n\n"
                f"Sections: {section_names}\n\n"
                f"Monthly Fee: Rs. {monthly_fee:.2f}\n"
                f"Effective From: {effective_label}"
            )

        confirmation = QMessageBox.question(
            self,
            "Confirm Class-Level Fee",
            confirmation_message,
        )

        if confirmation != QMessageBox.Yes:
            return

        try:
            fee_service.set_class_fees_for_level(
                class_ids=[
                    class_row["id"]
                    for class_row in level_classes
                ],
                monthly_fee=monthly_fee,
                effective_from_month=(
                    effective_month
                ),
                effective_from_year=(
                    effective_year
                ),
            )

        except (
            ValueError,
            TypeError,
        ) as error:
            QMessageBox.warning(
                self,
                "Fee Save Error",
                str(
                    error
                ),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Fee Save Error",
                (
                    "Class fee could not be "
                    f"saved.\n\n{error}"
                ),
            )
            return

        QMessageBox.information(
            self,
            "Class Fee Saved",
            (
                "Fee was saved successfully for "
                "all sections of class level "
                f"{class_level}:\n\n"
                f"{section_names}"
            ),
        )

        self._load_selected_class_fee()




# ---------------------------------------------------------------------------
# Set Individual Student Fee Screen
# ---------------------------------------------------------------------------

# MONTHLY_ONLY_SET_INDIVIDUAL_FEE_UI_V8
# The individual-fee screen now manages Monthly Fee percentage overrides only.

class SetIndividualFeePage(QWidget):
    """
    Search a student and set or remove an individual Monthly Fee percentage override.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.selected_student_id = None
        self.selected_student = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(
            18,
            14,
            18,
            16,
        )
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        title_label = QLabel(
            "Set Individual Student Fee"
        )
        title_label.setObjectName(
            "pageTitle"
        )
        title_row.addWidget(
            title_label
        )
        title_row.addStretch()

        self.back_to_set_fee_button = QPushButton(
            "Back to Set Fee"
        )
        self.back_to_set_fee_button.setObjectName(
            "secondaryButton"
        )
        self.back_to_set_fee_button.setMinimumWidth(
            170
        )
        self.back_to_set_fee_button.clicked.connect(
            lambda:
            self.main_window.go_to_page(
                navigation.PAGE_SET_FEE_DASHBOARD
            )
        )
        title_row.addWidget(
            self.back_to_set_fee_button
        )

        layout.addLayout(
            title_row
        )

        instruction_label = QLabel(
            "Search and select a student. Enter the "
            "Monthly Fee as a percentage of the "
            "applicable class Monthly Fee."
        )
        instruction_label.setWordWrap(
            True
        )
        layout.addWidget(
            instruction_label
        )

        search_grid = QGridLayout()
        search_grid.setHorizontalSpacing(10)
        search_grid.setVerticalSpacing(8)

        self.admission_number_input = QLineEdit()
        self.admission_number_input.setPlaceholderText(
            "Admission Number"
        )

        self.student_name_input = QLineEdit()
        self.student_name_input.setPlaceholderText(
            "Student Name"
        )

        self.father_name_input = QLineEdit()
        self.father_name_input.setPlaceholderText(
            "Father Name"
        )

        self.roll_number_input = QLineEdit()
        self.roll_number_input.setPlaceholderText(
            "Roll Number"
        )
        self.roll_number_input.setMaximumWidth(
            140
        )

        self.search_class_combo = QComboBox()
        self.search_class_combo.setMinimumWidth(
            180
        )
        self.search_class_combo.setMaximumWidth(
            220
        )

        search_grid.addWidget(
            QLabel("Admission Number:"),
            0,
            0,
        )
        search_grid.addWidget(
            self.admission_number_input,
            0,
            1,
        )

        search_grid.addWidget(
            QLabel("Student Name:"),
            0,
            2,
        )
        search_grid.addWidget(
            self.student_name_input,
            0,
            3,
        )

        search_grid.addWidget(
            QLabel("Father Name:"),
            0,
            4,
        )
        search_grid.addWidget(
            self.father_name_input,
            0,
            5,
        )

        search_grid.addWidget(
            QLabel("Roll Number:"),
            1,
            0,
        )
        search_grid.addWidget(
            self.roll_number_input,
            1,
            1,
        )

        search_grid.addWidget(
            QLabel("Select Class:"),
            1,
            2,
        )
        search_grid.addWidget(
            self.search_class_combo,
            1,
            3,
        )

        layout.addLayout(
            search_grid
        )

        search_button_row = QHBoxLayout()
        search_button_row.setSpacing(12)

        search_button_row.addStretch()

        search_button = QPushButton(
            "Search Student"
        )
        search_button.setMinimumWidth(
            210
        )
        search_button.clicked.connect(
            self._search_students
        )
        search_button_row.addWidget(
            search_button
        )

        clear_search_button = QPushButton(
            "Clear Search"
        )
        clear_search_button.setMinimumWidth(
            180
        )
        clear_search_button.setObjectName(
            "secondaryButton"
        )
        clear_search_button.clicked.connect(
            self.reset_page
        )
        search_button_row.addWidget(
            clear_search_button
        )
        search_button_row.addStretch()

        layout.addLayout(
            search_button_row
        )

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(
            7
        )
        self.results_table.setHorizontalHeaderLabels(
            [
                "Admission No",
                "Roll No",
                "Student Name",
                "Father Name",
                "Father CNIC",
                "Class",
                "Action",
            ]
        )

        results_header = (
            self.results_table.horizontalHeader()
        )

        for column_index in range(6):
            results_header.setSectionResizeMode(
                column_index,
                QHeaderView.Stretch,
            )

        results_header.setSectionResizeMode(
            6,
            QHeaderView.Fixed,
        )
        self.results_table.setColumnWidth(
            6,
            136,
        )
        self.results_table.verticalHeader().setVisible(
            False
        )
        self.results_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.results_table.setAlternatingRowColors(
            True
        )
        self.results_table.setWordWrap(
            False
        )
        self.results_table.setMinimumHeight(
            108
        )
        self.results_table.setMaximumHeight(
            138
        )

        layout.addWidget(
            self.results_table
        )

        self.selected_student_summary_label = QLabel(
            "Selected Student: None"
        )
        self.selected_student_summary_label.setWordWrap(
            True
        )
        layout.addWidget(
            self.selected_student_summary_label
        )

        self.class_fee_summary_label = QLabel(
            "Current Class Fee: -"
        )
        self.class_fee_summary_label.setWordWrap(
            True
        )
        layout.addWidget(
            self.class_fee_summary_label
        )

        self.override_fee_summary_label = QLabel(
            "Current Individual Fee: -"
        )
        self.override_fee_summary_label.setWordWrap(
            True
        )
        layout.addWidget(
            self.override_fee_summary_label
        )

        fee_heading = QLabel(
            "Individual Fee Details"
        )
        fee_heading.setObjectName(
            "sectionHeading"
        )
        layout.addWidget(
            fee_heading
        )

        fee_grid = QGridLayout()
        fee_grid.setHorizontalSpacing(12)
        fee_grid.setVerticalSpacing(8)

        self.individual_monthly_fee_input = (
            QDoubleSpinBox()
        )
        self.individual_monthly_fee_input.setRange(
            0.00,
            100.00,
        )
        self.individual_monthly_fee_input.setDecimals(
            2
        )
        self.individual_monthly_fee_input.setSingleStep(
            1.00
        )
        self.individual_monthly_fee_input.setSuffix(
            " %"
        )
        self.individual_monthly_fee_input.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.individual_monthly_fee_input.setMaximumWidth(
            180
        )
        self.individual_monthly_fee_input.valueChanged.connect(
            self._update_calculated_monthly_fee
        )

        self.calculated_monthly_fee_label = QLabel(
            "Rs. 0.00"
        )
        self.calculated_monthly_fee_label.setMinimumWidth(
            120
        )

        self.individual_effective_month_combo = (
            QComboBox()
        )
        self.individual_effective_month_combo.setMaximumWidth(
            180
        )

        for (
            month_name,
            month_number,
        ) in SESSION_MONTH_ORDER:
            self.individual_effective_month_combo.addItem(
                month_name,
                month_number,
            )

        self.individual_effective_session_combo = (
            QComboBox()
        )
        self.individual_effective_session_combo.setMinimumWidth(
            180
        )

        _populate_fee_session_combo(
            self.individual_effective_session_combo
        )

        self.individual_effective_month_combo.currentIndexChanged.connect(
            self._update_calculated_monthly_fee
        )
        self.individual_effective_session_combo.currentIndexChanged.connect(
            self._on_individual_fee_session_changed
        )

        self.individual_fee_scope_combo = QComboBox()
        self.individual_fee_scope_combo.setMinimumWidth(
            220
        )
        self.individual_fee_scope_combo.addItem(
            "Selected Session Only",
            "selected_session",
        )
        self.individual_fee_scope_combo.addItem(
            "Number of Sessions",
            "session_count",
        )
        self.individual_fee_scope_combo.addItem(
            "All Future Sessions",
            "all_future",
        )

        self.individual_fee_session_count_input = (
            QSpinBox()
        )
        self.individual_fee_session_count_input.setRange(
            1,
            50,
        )
        self.individual_fee_session_count_input.setValue(
            1
        )
        self.individual_fee_session_count_input.setMaximumWidth(
            100
        )
        self.individual_fee_session_count_input.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        self.individual_fee_scope_combo.currentIndexChanged.connect(
            self._update_individual_fee_scope_controls
        )

        fee_grid.addWidget(
            QLabel("Monthly Fee Percentage:"),
            0,
            0,
        )
        fee_grid.addWidget(
            self.individual_monthly_fee_input,
            0,
            1,
        )

        fee_grid.addWidget(
            QLabel("Calculated Monthly Fee:"),
            0,
            2,
        )
        fee_grid.addWidget(
            self.calculated_monthly_fee_label,
            0,
            3,
        )

        fee_grid.addWidget(
            QLabel("Effective From Month:"),
            1,
            0,
        )
        fee_grid.addWidget(
            self.individual_effective_month_combo,
            1,
            1,
        )

        fee_grid.addWidget(
            QLabel("Effective Session:"),
            1,
            2,
        )
        fee_grid.addWidget(
            self.individual_effective_session_combo,
            1,
            3,
        )

        fee_grid.addWidget(
            QLabel("Apply For:"),
            2,
            0,
        )
        fee_grid.addWidget(
            self.individual_fee_scope_combo,
            2,
            1,
        )

        fee_grid.addWidget(
            QLabel("Number of Sessions:"),
            2,
            2,
        )
        fee_grid.addWidget(
            self.individual_fee_session_count_input,
            2,
            3,
        )

        layout.addLayout(
            fee_grid
        )

        action_row = QHBoxLayout()
        action_row.setSpacing(12)
        action_row.addStretch()

        save_override_button = QPushButton(
            "Save Individual Fee"
        )
        save_override_button.setMinimumWidth(
            210
        )
        save_override_button.clicked.connect(
            self._save_individual_fee
        )
        action_row.addWidget(
            save_override_button
        )

        remove_override_button = QPushButton(
            "Remove Individual Fee"
        )
        remove_override_button.setMinimumWidth(
            210
        )
        remove_override_button.clicked.connect(
            self._remove_individual_fee
        )
        action_row.addWidget(
            remove_override_button
        )

        clear_form_button = QPushButton(
            "Clear"
        )
        clear_form_button.setObjectName(
            "secondaryButton"
        )
        clear_form_button.setMinimumWidth(
            180
        )
        clear_form_button.clicked.connect(
            self.reset_page
        )
        action_row.addWidget(
            clear_form_button
        )
        action_row.addStretch()

        layout.addLayout(
            action_row
        )

        self.reload_class_dropdown()
        self.reset_page()


    # ------------------------------------------------------------------
    # Page setup
    # ------------------------------------------------------------------

    def reload_class_dropdown(self):
        """Load all existing classes into the search dropdown."""
        self.search_class_combo.blockSignals(True)
        self.search_class_combo.clear()
        self.search_class_combo.addItem("Select Class", None)

        for class_row in class_service.get_all_classes():
            self.search_class_combo.addItem(
                class_row["class_name"],
                class_row["id"],
            )

        self.search_class_combo.setCurrentIndex(0)
        self.search_class_combo.blockSignals(False)

    def reset_page(self):
        """Clear search results, student details, and fee fields."""
        self.selected_student_id = None
        self.selected_student = None

        self.admission_number_input.clear()
        self.student_name_input.clear()
        self.father_name_input.clear()
        self.roll_number_input.clear()

        if self.search_class_combo.count() > 0:
            self.search_class_combo.setCurrentIndex(
                0
            )

        self.results_table.setRowCount(
            0
        )

        self.selected_student_summary_label.setText(
            "Selected Student: None"
        )
        self.class_fee_summary_label.setText(
            "Current Class Fee: -"
        )
        self.override_fee_summary_label.setText(
            "Current Individual Fee: -"
        )

        self.individual_monthly_fee_input.setValue(
            0.00
        )

        self.individual_fee_scope_combo.setCurrentIndex(
            0
        )
        self.individual_fee_session_count_input.setValue(
            1
        )
        self._update_individual_fee_scope_controls()

        self._set_current_effective_date()


    def _set_current_effective_date(self):
        """Use the computer's current month and year."""
        today = datetime.now()

        month_index = self.individual_effective_month_combo.findData(today.month)

        if month_index >= 0:
            self.individual_effective_month_combo.setCurrentIndex(month_index)

        current_session_start = (
            _current_session_start_year()
        )

        session_index = (
            self.individual_effective_session_combo.findData(
                current_session_start
            )
        )

        if session_index >= 0:
            self.individual_effective_session_combo.setCurrentIndex(
                session_index
            )

    def _update_individual_fee_scope_controls(self):
        """Enable session count only for the numbered-session option."""
        selected_scope = (
            self.individual_fee_scope_combo.currentData()
        )

        uses_session_count = (
            selected_scope == "session_count"
        )

        self.individual_fee_session_count_input.setEnabled(
            uses_session_count
        )

        if selected_scope == "selected_session":
            self.individual_fee_session_count_input.setValue(1)

    def _month_name(self, month_number):
        """Return the display name of a calendar month."""
        for month_name, number in SESSION_MONTH_ORDER:
            if number == month_number:
                return month_name
        return str(month_number)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_students(self):
        admission_number = (
            self.admission_number_input.text().strip().upper() or None
        )
        student_name = self.student_name_input.text().strip() or None
        father_name = self.father_name_input.text().strip() or None
        roll_number = self.roll_number_input.text().strip() or None
        if roll_number:
            try:
                student_service.parse_roll_number_search(roll_number)
            except ValueError as error:
                QMessageBox.warning(self, "Invalid Roll Number", str(error))
                self.roll_number_input.setFocus()
                return
        class_id = self.search_class_combo.currentData()

        try:
            results = fee_service.search_students_for_fee(
                admission_number=admission_number,
                student_name=student_name,
                father_name=father_name,
                roll_number=roll_number,
                class_id=class_id,
            )
        except ValueError as error:
            QMessageBox.warning(self, "Search Error", str(error))
            return

        self._populate_results(results)

    def _populate_results(self, results):
        self.results_table.setRowCount(0)
        self._clear_selected_student()

        if not results:
            QMessageBox.information(
                self,
                "Student Not Found",
                "Student not found.",
            )
            return

        self.results_table.setRowCount(len(results))

        for row_index, student_row in enumerate(results):
            display_name = student_service.display_name_with_stars(
                student_row["student_name"],
                student_row["failure_count"],
            )

            values = [
                student_row["admission_number"],
                student_service.display_roll_number(student_row),
                display_name,
                student_row["father_name"],
                student_row["father_cnic"] or "-",
                student_row["class_name"] or "-",
            ]

            for column_index, value in enumerate(values):
                self.results_table.setItem(
                    row_index,
                    column_index,
                    QTableWidgetItem(str(value)),
                )

            select_button = QPushButton("Select")
            select_button.setObjectName(
                "individualFeeSelectButton"
            )
            select_button.setFixedSize(
                112,
                34,
            )
            select_button.setFocusPolicy(
                Qt.NoFocus
            )
            select_button.clicked.connect(
                lambda _checked=False, student_id=student_row["student_id"]:
                self._select_student(student_id)
            )

            button_container = QWidget()
            button_layout = QHBoxLayout(
                button_container
            )
            button_layout.setContentsMargins(
                6,
                4,
                6,
                4,
            )
            button_layout.setAlignment(
                Qt.AlignCenter
            )
            button_layout.addWidget(
                select_button
            )

            self.results_table.setRowHeight(
                row_index,
                46,
            )
            self.results_table.setCellWidget(
                row_index,
                6,
                button_container,
            )

    # ------------------------------------------------------------------
    # Selected student
    # ------------------------------------------------------------------

    def _select_student(self, student_id):
        student_row = student_service.get_student_by_id(student_id)

        if not student_row:
            QMessageBox.warning(
                self,
                "Student Not Found",
                "The selected student record could not be found.",
            )
            return

        self.selected_student_id = student_id
        self.selected_student = student_row

        display_name = student_service.display_name_with_stars(
            student_row["student_name"],
            student_row["failure_count"],
        )

        self.selected_student_summary_label.setText(
            f"Selected Student: "
            f"{student_row['admission_number']} | "
            f"Roll No: {student_row['roll_number']} | "
            f"{display_name} | "
            f"Father: {student_row['father_name']} | "
            f"CNIC: {student_row['father_cnic'] or '-'} | "
            f"Class: {student_row['class_name'] or '-'} | "
            f"Status: {student_row['student_status']}"
        )

        self._load_class_fee()
        self._load_current_override()

    def _clear_selected_student(self):
        self.selected_student_id = None
        self.selected_student = None

        self.selected_student_summary_label.setText(
            "Selected Student: None"
        )
        self.class_fee_summary_label.setText(
            "Current Class Fee: -"
        )
        self.override_fee_summary_label.setText(
            "Current Individual Fee: -"
        )

        self.individual_monthly_fee_input.setValue(
            0.00
        )

        self._set_current_effective_date()


    def _on_individual_fee_session_changed(self):
        """Load the selected student's rule for the chosen session."""
        if self.selected_student_id:
            self._load_current_override()
            return

        self._update_calculated_monthly_fee()


    def _update_calculated_monthly_fee(self):
        """
        Show the rupee amount produced by the entered percentage.
        """
        if (
            not self.selected_student
            or not self.selected_student["class_id"]
        ):
            self.calculated_monthly_fee_label.setText(
                "Rs. 0.00"
            )
            return

        month_number = (
            self.individual_effective_month_combo.currentData()
        )

        session_start_year = (
            self.individual_effective_session_combo.currentData()
        )

        percentage = (
            self.individual_monthly_fee_input.value()
        )

        if (
            month_number is None
            or session_start_year is None
        ):
            self.calculated_monthly_fee_label.setText(
                "Rs. 0.00"
            )
            return

        year = (
            _calendar_year_for_session_month(
                session_start_year,
                month_number,
            )
        )

        try:
            calculated_amount = (
                fee_service.calculate_individual_monthly_fee_preview(
                    class_id=self.selected_student["class_id"],
                    monthly_fee_percentage=percentage,
                    month_number=month_number,
                    year=year,
                )
            )
        except (ValueError, TypeError):
            self.calculated_monthly_fee_label.setText(
                "Rs. -"
            )
            return

        self.calculated_monthly_fee_label.setText(
            f"Rs. {calculated_amount:.2f}"
        )

    def _load_class_fee(self):
        """Display the selected student's normal class Monthly Fee."""
        if not self.selected_student:
            self.class_fee_summary_label.setText(
                "Current Class Fee: -"
            )
            self._update_calculated_monthly_fee()
            return

        class_id = self.selected_student[
            "class_id"
        ]

        class_fee = (
            fee_service.get_class_fee(
                class_id
            )
        )

        if not class_fee:
            self.class_fee_summary_label.setText(
                (
                    "Current Class Fee: "
                    "No class fee has been set."
                )
            )
            self._update_calculated_monthly_fee()
            return

        month_name = self._month_name(
            class_fee[
                "effective_from_month"
            ]
        )

        self.class_fee_summary_label.setText(
            (
                "Current Class Fee: "
                f"Monthly Rs. {class_fee['monthly_fee']:.2f} | "
                f"Effective from {month_name} "
                f"{class_fee['effective_from_year']}"
            )
        )

        self._update_calculated_monthly_fee()


    def _load_current_override(self):
        """Load the student's individual fee for the selected session."""
        if not self.selected_student_id:
            return

        selected_session_start = (
            self.individual_effective_session_combo.currentData()
        )

        if selected_session_start is None:
            return

        override = (
            fee_service.get_student_override_for_session(
                self.selected_student_id,
                selected_session_start,
            )
        )

        if not override:
            self.override_fee_summary_label.setText(
                (
                    "Individual Fee for Session "
                    f"{_session_label(selected_session_start)}: "
                    "No individual fee set for this season."
                )
            )

            self.individual_monthly_fee_input.setValue(
                0.00
            )
            self.individual_fee_scope_combo.setCurrentIndex(
                0
            )
            self.individual_fee_session_count_input.setValue(
                1
            )
            self._update_individual_fee_scope_controls()
            self._update_calculated_monthly_fee()
            return

        percentage = None

        if (
            "monthly_fee_percentage"
            in override.keys()
        ):
            percentage = override[
                "monthly_fee_percentage"
            ]

        if percentage is None:
            class_fee = (
                fee_service.get_class_fee_for_session(
                    self.selected_student[
                        "class_id"
                    ],
                    selected_session_start,
                )
            )

            if (
                class_fee
                and float(
                    class_fee[
                        "monthly_fee"
                    ]
                ) > 0
            ):
                percentage = round(
                    (
                        float(
                            override[
                                "monthly_fee"
                            ]
                        )
                        / float(
                            class_fee[
                                "monthly_fee"
                            ]
                        )
                        * 100.0
                    ),
                    2,
                )
            else:
                percentage = 0.00

        selected_session_start_period = (
            int(selected_session_start),
            SESSION_START_CALENDAR_MONTH,
        )
        selected_session_end_period = (
            int(selected_session_start) + 1,
            SESSION_START_CALENDAR_MONTH - 1,
        )
        override_start_period = (
            int(
                override[
                    "effective_from_year"
                ]
            ),
            int(
                override[
                    "effective_from_month"
                ]
            ),
        )

        if (
            override[
                "effective_to_year"
            ]
            is None
        ):
            override_end_period = None
        else:
            override_end_period = (
                int(
                    override[
                        "effective_to_year"
                    ]
                ),
                int(
                    override[
                        "effective_to_month"
                    ]
                ),
            )

        display_start_period = max(
            override_start_period,
            selected_session_start_period,
        )
        display_end_period = (
            selected_session_end_period
            if override_end_period is None
            else min(
                override_end_period,
                selected_session_end_period,
            )
        )

        self.individual_monthly_fee_input.setValue(
            float(percentage)
        )

        month_index = (
            self.individual_effective_month_combo.findData(
                display_start_period[1]
            )
        )

        if month_index >= 0:
            self.individual_effective_month_combo.setCurrentIndex(
                month_index
            )

        if override_end_period is None:
            scope_index = (
                self.individual_fee_scope_combo.findData(
                    "all_future"
                )
            )
            session_count = 1

        elif (
            override_end_period
            <= selected_session_end_period
        ):
            scope_index = (
                self.individual_fee_scope_combo.findData(
                    "selected_session"
                )
            )
            session_count = 1

        else:
            remaining_sessions = max(
                1,
                int(
                    override_end_period[0]
                    - int(selected_session_start)
                ),
            )
            scope_index = (
                self.individual_fee_scope_combo.findData(
                    "session_count"
                )
            )
            session_count = remaining_sessions

        if scope_index >= 0:
            self.individual_fee_scope_combo.setCurrentIndex(
                scope_index
            )

        self.individual_fee_session_count_input.setValue(
            session_count
        )
        self._update_individual_fee_scope_controls()
        self._update_calculated_monthly_fee()

        start_month_name = self._month_name(
            display_start_period[1]
        )
        end_month_name = self._month_name(
            display_end_period[1]
        )

        if display_start_period == display_end_period:
            period_label = start_month_name
        else:
            period_label = (
                f"{start_month_name} to "
                f"{end_month_name}"
            )

        self.override_fee_summary_label.setText(
            (
                "Individual Fee for Session "
                f"{_session_label(selected_session_start)}: "
                f"Monthly {float(percentage):.2f}% "
                f"({self.calculated_monthly_fee_label.text()}) | "
                f"Applies {period_label}"
            )
        )


    # ------------------------------------------------------------------
    # Save individual fee
    # ------------------------------------------------------------------

    def _save_individual_fee(self):
        if not self.selected_student_id:
            QMessageBox.warning(
                self,
                "No Student Selected",
                (
                    "Please search and select "
                    "a student first."
                ),
            )
            return

        monthly_percentage = (
            self.individual_monthly_fee_input.value()
        )

        effective_month = (
            self.individual_effective_month_combo.currentData()
        )

        session_start_year = (
            self.individual_effective_session_combo.currentData()
        )

        if session_start_year is None:
            QMessageBox.warning(
                self,
                "No Session Selected",
                (
                    "Please select an effective "
                    "academic session."
                ),
            )
            return

        current_session = (
            _current_session_start_year()
        )

        if session_start_year < current_session:
            QMessageBox.warning(
                self,
                "Past Session Locked",
                (
                    "Individual fees of previous "
                    "academic sessions cannot be modified."
                ),
            )
            return

        effective_year = (
            _calendar_year_for_session_month(
                session_start_year,
                effective_month,
            )
        )

        selected_scope = (
            self.individual_fee_scope_combo.currentData()
        )

        if selected_scope == "all_future":
            number_of_sessions = None
            scope_label = (
                "All Future Sessions"
            )

        elif selected_scope == "session_count":
            number_of_sessions = int(
                self.individual_fee_session_count_input.value()
            )

            scope_label = (
                f"{number_of_sessions} Session"
                if number_of_sessions == 1
                else f"{number_of_sessions} Sessions"
            )

        else:
            number_of_sessions = 1
            scope_label = (
                "Selected Session Only"
            )

        if not 0 <= monthly_percentage <= 100:
            QMessageBox.warning(
                self,
                "Invalid Monthly Fee Percentage",
                (
                    "Monthly Fee Percentage must "
                    "be between 0 and 100."
                ),
            )
            return

        try:
            calculated_monthly_fee = (
                fee_service.calculate_individual_monthly_fee_preview(
                    class_id=self.selected_student[
                        "class_id"
                    ],
                    monthly_fee_percentage=(
                        monthly_percentage
                    ),
                    month_number=effective_month,
                    year=effective_year,
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Class Fee Required",
                str(
                    error
                ),
            )
            return

        effective_label = (
            f"{self.individual_effective_month_combo.currentText()} | "
            "Session "
            f"{self.individual_effective_session_combo.currentText()}"
        )

        confirmation = QMessageBox.question(
            self,
            "Confirm Individual Fee",
            (
                "Set the following individual fee for "
                f"{self.selected_student['student_name']}?\n\n"
                "Monthly Percentage: "
                f"{monthly_percentage:.2f}%\n"
                "Calculated Monthly Fee: "
                f"Rs. {calculated_monthly_fee:.2f}\n"
                f"Effective From: {effective_label}\n"
                f"Apply For: {scope_label}\n\n"
                "The percentage will remain saved during "
                "the selected period. After a limited "
                "period ends, the normal class fee will "
                "apply automatically. If the class Monthly "
                "Fee changes, this student's monthly amount "
                "will recalculate automatically."
            ),
        )

        if confirmation != QMessageBox.Yes:
            return

        try:
            fee_service.set_student_fee_override(
                student_id=(
                    self.selected_student_id
                ),
                monthly_fee_percentage=(
                    monthly_percentage
                ),
                effective_from_month=(
                    effective_month
                ),
                effective_from_year=(
                    effective_year
                ),
                number_of_sessions=(
                    number_of_sessions
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Fee Save Error",
                (
                    "Individual fee could not "
                    f"be saved.\n\n{error}"
                ),
            )
            return

        QMessageBox.information(
            self,
            "Individual Fee Saved",
            (
                "Individual student fee percentage "
                "was saved successfully."
            ),
        )

        self._load_current_override()


    # ------------------------------------------------------------------
    # Remove individual fee
    # ------------------------------------------------------------------

    def _remove_individual_fee(self):
        if not self.selected_student_id:
            QMessageBox.warning(
                self, "No Student Selected",
                "Please search and select a student first.",
            )
            return

        removal_month = self.individual_effective_month_combo.currentData()
        removal_session = self.individual_effective_session_combo.currentData()
        if removal_month is None or removal_session is None:
            QMessageBox.warning(
                self, "Removal Period Required",
                "Select the removal effective month and academic session.",
            )
            return

        current_session = _current_session_start_year()
        if int(removal_session) < int(current_session):
            QMessageBox.warning(
                self, "Past Session Locked",
                "An individual fee cannot be removed retrospectively from a closed session.",
            )
            return

        removal_label = (
            f"{self.individual_effective_month_combo.currentText()} | "
            f"Session {self.individual_effective_session_combo.currentText()}"
        )
        confirmation = QMessageBox.question(
            self, "Remove Individual Fee",
            (
                "Remove the individual fee for "
                f"{self.selected_student['student_name']}?\n\n"
                f"Removal Effective From: {removal_label}\n"
                "Months before this boundary and all Paid or receipt-backed rows "
                "will remain unchanged. Adjustable rows from the boundary onward "
                "will use the applicable class fee."
            ),
        )
        if confirmation != QMessageBox.Yes:
            return

        try:
            removed = fee_service.remove_student_fee_override(
                student_id=self.selected_student_id,
                effective_from_month=int(removal_month),
                session_start_year=int(removal_session),
            )
        except Exception as error:
            QMessageBox.critical(
                self, "Remove Fee Error",
                f"Individual fee could not be removed.\n\n{error}",
            )
            return

        if not removed:
            QMessageBox.information(
                self, "No Individual Fee",
                "No individual-fee rule overlaps the selected removal period.",
            )
            self._load_current_override()
            return

        QMessageBox.information(
            self, "Individual Fee Removed",
            (
                f"Individual fee removed from {removal_label}. "
                "Historical Paid and receipt-backed rows were preserved."
            ),
        )
        self._load_current_override()

