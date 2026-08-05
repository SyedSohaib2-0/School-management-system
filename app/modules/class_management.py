"""
Class Management module.
Contains the Class Management Dashboard, Add Class form, Search Class
screen, and Class Search Result screen (KPIs, student table, PDF export,
delete class).

This module only reads student data - it never creates, edits, or
deletes students. All student data changes remain the responsibility
of the Student Management module, as required by the project rules.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QMessageBox, QFileDialog, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog,
    QSpinBox, QInputDialog, QToolButton,
)

# CLASS_CAPACITY_MANAGEMENT_UI_V1
# ACTIVE_GENDER_STRENGTH_KPI_UI_V10

from app import navigation
from app.config import ALL_CLASS_LEVELS, STATUS_ACTIVE, build_class_name
from app.services import class_service
from app.services import student_service


# ---------------------------------------------------------------------------
# Small reusable clickable KPI card
# ---------------------------------------------------------------------------

class KpiCard(QFrame):
    """A clickable KPI card showing a title and a count."""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        clickable: bool = False,
    ):
        super().__init__()
        self._clickable = bool(
            clickable
        )
        self.setObjectName("kpiCard")
        self.setCursor(
            Qt.PointingHandCursor
            if self._clickable
            else Qt.ArrowCursor
        )
        self.setProperty("selected", "false")

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self.title_label = QLabel(title)
        layout.addWidget(self.title_label)

        self.count_label = QLabel("0")
        self.count_label.setObjectName("kpiCount")
        layout.addWidget(self.count_label)

    def set_count(self, count: int) -> None:
        self.count_label.setText(str(count))

    def set_selected(self, selected: bool) -> None:
        """Toggle the visual 'selected' state so the active KPI stands out."""
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Class Capacity Settings Dialog
# ---------------------------------------------------------------------------

# CLASS_CAPACITY_SETTINGS_ICON_V1

class ClassCapacitySettingsDialog(QDialog):
    """Edit the capacity of one existing class section."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._class_rows = []
        self._selected_details = None

        self.setWindowTitle("Class Capacity Settings")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        heading = QLabel("Class Capacity Settings")
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)

        help_label = QLabel(
            "Select a class and section, then enter the maximum "
            "number of Active students allowed."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        form = QFormLayout()
        form.setSpacing(10)

        self.class_level_combo = QComboBox()
        self.class_level_combo.currentIndexChanged.connect(
            self._reload_sections
        )
        form.addRow("Class:", self.class_level_combo)

        self.section_combo = QComboBox()
        self.section_combo.currentIndexChanged.connect(
            self._load_selected_class
        )
        form.addRow("Section:", self.section_combo)

        self.capacity_input = QSpinBox()
        self.capacity_input.setRange(1, 1000)
        self.capacity_input.setSuffix(" students")
        form.addRow("Class Size:", self.capacity_input)

        self.status_combo = QComboBox()
        self.status_combo.addItem("Active", True)
        self.status_combo.addItem("Inactive", False)
        form.addRow("Status:", self.status_combo)

        layout.addLayout(form)

        self.occupancy_label = QLabel(
            "Active Students: - | Available Seats: -"
        )
        self.occupancy_label.setWordWrap(True)
        layout.addWidget(self.occupancy_label)

        button_row = QHBoxLayout()
        button_row.addStretch()

        cancel_button = QPushButton("Close")
        cancel_button.setObjectName("secondaryButton")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)

        self.save_button = QPushButton("Save Class Settings")
        self.save_button.clicked.connect(self._save_capacity)
        button_row.addWidget(self.save_button)

        layout.addLayout(button_row)

        self._reload_classes()

    def _reload_classes(self) -> None:
        self._class_rows = list(class_service.get_all_classes(include_inactive=True))

        selected_level = self.class_level_combo.currentData()

        levels_with_classes = []
        for level in ALL_CLASS_LEVELS:
            if any(
                row["class_level"] == level
                for row in self._class_rows
            ):
                levels_with_classes.append(level)

        self.class_level_combo.blockSignals(True)
        self.class_level_combo.clear()
        self.class_level_combo.addItem("Select Class", None)

        for level in levels_with_classes:
            self.class_level_combo.addItem(level, level)

        if selected_level in levels_with_classes:
            for index in range(self.class_level_combo.count()):
                if self.class_level_combo.itemData(index) == selected_level:
                    self.class_level_combo.setCurrentIndex(index)
                    break

        self.class_level_combo.blockSignals(False)
        self._reload_sections()

    def _reload_sections(self, *_args) -> None:
        selected_level = self.class_level_combo.currentData()

        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem("Select Section", None)

        if selected_level:
            matching_rows = [
                row
                for row in self._class_rows
                if row["class_level"] == selected_level
            ]

            for row in matching_rows:
                label = row["section"]
                if not class_service.validate_section(
                    row["section"], row["class_level"]
                ):
                    label += " (Invalid Section)"
                if not bool(row["is_active"]):
                    label += " (Inactive)"
                self.section_combo.addItem(label, int(row["id"]))

        self.section_combo.blockSignals(False)
        self._load_selected_class()

    def _load_selected_class(self, *_args) -> None:
        class_id = self.section_combo.currentData()
        self._selected_details = None

        if not class_id:
            self.capacity_input.setEnabled(False)
            self.status_combo.setEnabled(False)
            self.save_button.setEnabled(False)
            self.occupancy_label.setText(
                "Active Students: - | Available Seats: -"
            )
            return

        details = class_service.get_class_capacity_details(class_id)

        if not details:
            self.capacity_input.setEnabled(False)
            self.status_combo.setEnabled(False)
            self.save_button.setEnabled(False)
            self.occupancy_label.setText(
                "The selected class could not be found."
            )
            return

        self._selected_details = details

        active_students = int(details["active_students"])
        reserved_students = int(details.get("coming_session_reserved", 0) or 0)
        minimum_capacity = max(1, active_students + reserved_students)

        self.capacity_input.setRange(minimum_capacity, 1000)
        self.capacity_input.setValue(int(details["student_capacity"]))
        self.capacity_input.setEnabled(True)
        self.status_combo.setEnabled(True)
        self.status_combo.setCurrentIndex(0 if bool(details["is_active"]) else 1)
        self.save_button.setEnabled(True)

        self.occupancy_label.setText(
            f"Current Class: {details['class_name']} | "
            f"Active Students: {active_students} | "
            f"Coming-Session Reserved: {reserved_students} | "
            f"Available Seats: {details['available_seats']}"
        )

    def _save_capacity(self) -> None:
        if not self._selected_details:
            QMessageBox.warning(
                self,
                "No Class Selected",
                "Please select a class and section.",
            )
            return

        class_id = int(self._selected_details["id"])
        new_capacity = self.capacity_input.value()

        desired_active = bool(self.status_combo.currentData())
        try:
            saved = class_service.update_class_settings(
                class_id=class_id,
                student_capacity=new_capacity,
                active=desired_active,
            )
            saved_capacity = int(saved["student_capacity"])
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Class Settings",
                str(error),
            )
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Capacity Update Error",
                "The class capacity could not be updated."
                f"\n\n{error}",
            )
            return

        self._selected_details = (
            class_service.get_class_capacity_details(class_id)
        )
        self._load_selected_class()

        QMessageBox.information(
            self,
            "Class Settings Updated",
            f"{self._selected_details['class_name']} capacity was updated to "
            f"{saved_capacity} and status is "
            f"{'Active' if self._selected_details['is_active'] else 'Inactive'}.",
        )


# ---------------------------------------------------------------------------
# Class Management Dashboard
# ---------------------------------------------------------------------------

class ClassDashboardPage(QWidget):
    """The Class Management landing page, shown when the module opens."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title_row = QHBoxLayout()

        title_label = QLabel("Class Management")
        title_label.setObjectName("pageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch()

        self.capacity_settings_button = QToolButton()
        self.capacity_settings_button.setText("⚙")
        self.capacity_settings_button.setToolTip(
            "Class Capacity Settings"
        )
        self.capacity_settings_button.setAccessibleName(
            "Class Capacity Settings"
        )
        self.capacity_settings_button.setFixedSize(34, 34)
        self.capacity_settings_button.clicked.connect(
            self._open_capacity_settings
        )
        title_row.addWidget(self.capacity_settings_button)

        layout.addLayout(title_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(15)

        add_class_button = QPushButton("Add Class")
        add_class_button.clicked.connect(self._open_add_class)
        button_row.addWidget(add_class_button)

        search_class_button = QPushButton("Search Class")
        search_class_button.clicked.connect(self._open_search_class)
        button_row.addWidget(search_class_button)

        layout.addLayout(button_row)

        back_button = QPushButton("Back to Main Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_MAIN_DASHBOARD)
        )
        layout.addWidget(back_button)
        layout.addStretch()

    def _open_capacity_settings(self):
        dialog = ClassCapacitySettingsDialog(self)
        dialog.exec()

    def _open_add_class(self):
        self.main_window.pages[navigation.PAGE_ADD_CLASS].reset_form()
        self.main_window.go_to_page(navigation.PAGE_ADD_CLASS)

    def _open_search_class(self):
        self.main_window.pages[navigation.PAGE_SEARCH_CLASS].reload_class_dropdown()
        self.main_window.go_to_page(navigation.PAGE_SEARCH_CLASS)


# ---------------------------------------------------------------------------
# Add Class
# ---------------------------------------------------------------------------

class AddClassPage(QWidget):
    """The Add Class form. The class name is always generated, never typed."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(15)

        title_label = QLabel("Add Class")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        form = QFormLayout()
        form.setSpacing(10)

        self.class_level_combo = QComboBox()
        self.class_level_combo.addItem("Select Class Level", None)
        for level in ALL_CLASS_LEVELS:
            self.class_level_combo.addItem(level, level)
        self.class_level_combo.currentIndexChanged.connect(self._update_generated_name)
        form.addRow("Class Level:", self.class_level_combo)

        self.section_input = QLineEdit()
        self.section_input.setPlaceholderText("e.g. A, B, C...")
        self.section_input.textChanged.connect(self._update_generated_name)
        form.addRow("Section:", self.section_input)

        self.generated_name_label = QLabel("-")
        self.generated_name_label.setObjectName("generatedClassName")
        form.addRow("Generated Class Name:", self.generated_name_label)

        self.capacity_input = QSpinBox()
        self.capacity_input.setRange(
            1,
            1000,
        )
        self.capacity_input.setValue(
            class_service.DEFAULT_CLASS_CAPACITY
        )
        self.capacity_input.setSuffix(
            " students"
        )
        self.capacity_input.setToolTip(
            "Maximum number of Active students allowed in this class."
        )
        form.addRow(
            "Student Capacity:",
            self.capacity_input,
        )

        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setSpacing(15)

        save_button = QPushButton("Save Class")
        save_button.clicked.connect(self._save_class)
        button_row.addWidget(save_button)

        clear_button = QPushButton("Clear")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear_form)
        button_row.addWidget(clear_button)

        layout.addLayout(button_row)

        back_button = QPushButton("Back to Class Management Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_CLASS_DASHBOARD)
        )
        layout.addWidget(back_button)
        layout.addStretch()

    # -------------------------------------------------------------
    # Generated class name preview
    # -------------------------------------------------------------

    def _current_level(self):
        return self.class_level_combo.currentData()

    def _current_section(self):
        return self.section_input.text().strip().upper()

    def _update_generated_name(self, *_args):
        # Keep the typed section uppercase as the user types, without
        # fighting the cursor position on every keystroke.
        cursor_position = self.section_input.cursorPosition()
        upper_text = self.section_input.text().upper()
        if upper_text != self.section_input.text():
            self.section_input.blockSignals(True)
            self.section_input.setText(upper_text)
            self.section_input.setCursorPosition(cursor_position)
            self.section_input.blockSignals(False)

        level = self._current_level()
        section = self._current_section()

        if level and section and class_service.validate_section(section, level):
            self.generated_name_label.setText(build_class_name(level, section))
        else:
            self.generated_name_label.setText("-")

    # -------------------------------------------------------------
    # Save / Clear
    # -------------------------------------------------------------


    def _save_class(self):
        level = self._current_level()
        section = self._current_section()

        if not level:
            QMessageBox.warning(
                self,
                "Missing Class Level",
                "Please select a class level.",
            )
            return

        if not class_service.validate_section(
            section, level
        ):
            QMessageBox.warning(
                self,
                "Invalid Section",
                (
                    "Please enter a valid section. Classes 4 through 10 "
                    "must use exactly one letter from A to Z."
                ),
            )
            return

        class_name = build_class_name(
            level,
            section,
        )

        if not class_service.is_class_name_unique(
            class_name
        ):
            QMessageBox.warning(
                self,
                "Duplicate Class",
                "This class already exists.",
            )
            return

        student_capacity = (
            self.capacity_input.value()
        )

        try:
            new_class_id = (
                class_service.add_class(
                    level,
                    section,
                    student_capacity,
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Capacity",
                str(error),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Class Save Error",
                (
                    "The class could not be saved."
                    f"\n\n{error}"
                ),
            )
            return

        QMessageBox.information(
            self,
            "Class Added",
            (
                "Class added successfully."
                f"\n\nClass: {class_name}"
                f"\nStudent Capacity: {student_capacity}"
                "\n\nYou can now add the next class or section."
            ),
        )

        self._prepare_for_next_class()

    def _prepare_for_next_class(
        self,
    ) -> None:
        """
        Keep the Add Class page open after saving.

        The selected class level remains selected so another section
        of the same class can be entered immediately.
        """
        self.section_input.clear()

        self.generated_name_label.setText(
            "-"
        )

        self.section_input.setFocus()
    def _clear_form(self):
        if self._current_level() or self._current_section():
            confirm = QMessageBox.question(
                self, "Clear Form",
                "Are you sure you want to clear the entered class information?",
            )
            if confirm != QMessageBox.Yes:
                return
        self.reset_form()


    def reset_form(self):
        """Return the form to its default empty state."""
        self.class_level_combo.setCurrentIndex(
            0
        )

        self.section_input.clear()

        self.generated_name_label.setText(
            "-"
        )

        self.capacity_input.setValue(
            class_service.DEFAULT_CLASS_CAPACITY
        )



# ---------------------------------------------------------------------------
# Search Class
# ---------------------------------------------------------------------------

class SearchClassPage(QWidget):
    """The Search Class screen - select an existing class from a dropdown."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(15)

        title_label = QLabel("Search Class")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        form = QFormLayout()
        form.setSpacing(10)

        self.class_combo = QComboBox()
        form.addRow("Select Class:", self.class_combo)

        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setSpacing(15)

        search_button = QPushButton("Search")
        search_button.clicked.connect(self._perform_search)
        button_row.addWidget(search_button)

        clear_button = QPushButton("Clear Search")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear_search)
        button_row.addWidget(clear_button)

        layout.addLayout(button_row)

        back_button = QPushButton("Back to Class Management Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_CLASS_DASHBOARD)
        )
        layout.addWidget(back_button)
        layout.addStretch()

    def reload_class_dropdown(self):
        """Reload every stored class into the dropdown, including brand new ones."""
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem("Select a class...", None)
        for class_row in class_service.get_all_classes(include_inactive=True):
            label = class_row["class_name"]
            if not bool(class_row["is_active"]):
                label += " (Inactive)"
            self.class_combo.addItem(label, class_row["id"])
        self.class_combo.blockSignals(False)

    def _perform_search(self):
        class_id = self.class_combo.currentData()

        if not class_id:
            QMessageBox.warning(self, "No Class Selected", "Please select a class.")
            return

        class_row = class_service.get_class_by_id(class_id)
        if not class_row:
            QMessageBox.warning(self, "Class Not Found", "Class not found.")
            return

        result_page = self.main_window.pages[navigation.PAGE_CLASS_SEARCH_RESULT]
        result_page.load_class(class_row)
        self.main_window.go_to_page(navigation.PAGE_CLASS_SEARCH_RESULT)

    def _clear_search(self):
        self.class_combo.setCurrentIndex(0)


# ---------------------------------------------------------------------------
# Class Search Result (KPIs + student table + PDF + delete)
# ---------------------------------------------------------------------------

class ClassSearchResultPage(QWidget):
    """
    Shows the selected class, Active-only gender-strength KPI cards,
    the Active student table, and Save-as-PDF / Delete Class / Back actions.
    This page only reads student records - it never edits them.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_class_row = None
        self.current_students = []

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(15)

        self.class_title_label = QLabel("Class: -")
        self.class_title_label.setObjectName("pageTitle")
        layout.addWidget(self.class_title_label)

        capacity_row = QHBoxLayout()
        capacity_row.setSpacing(15)

        self.capacity_label = QLabel(
            "Capacity: - | Active: - | Available Seats: -"
        )
        self.capacity_label.setObjectName(
            "sectionHeading"
        )
        capacity_row.addWidget(
            self.capacity_label
        )

        capacity_row.addStretch()

        self.edit_capacity_button = QPushButton(
            "Edit Capacity"
        )
        self.edit_capacity_button.setObjectName(
            "secondaryButton"
        )
        self.edit_capacity_button.clicked.connect(
            self._edit_capacity
        )
        capacity_row.addWidget(
            self.edit_capacity_button
        )

        layout.addLayout(
            capacity_row
        )

        # --- Active-only gender-strength KPI cards ---
        # Play Group through Class 3 show both Male and Female strength.
        # Class 4 onward shows the section's permitted gender only:
        # A-F = Male, G-Z = Female.
        self.kpi_row = QHBoxLayout()
        self.kpi_row.setSpacing(15)

        self.male_strength_kpi = KpiCard(
            "Male Strength"
        )
        self.kpi_row.addWidget(
            self.male_strength_kpi
        )

        self.female_strength_kpi = KpiCard(
            "Female Strength"
        )
        self.kpi_row.addWidget(
            self.female_strength_kpi
        )

        layout.addLayout(
            self.kpi_row
        )

        # --- Table heading + table ---
        self.table_heading_label = QLabel("Active Students")
        self.table_heading_label.setObjectName("sectionHeading")
        layout.addWidget(self.table_heading_label)

        self.empty_label = QLabel("")
        layout.addWidget(self.empty_label)

        self.student_table = QTableWidget()
        self.student_table.setColumnCount(4)
        self.student_table.setHorizontalHeaderLabels(
            ["Admission Number", "Roll Number", "Student Name", "Father Name"]
        )
        self.student_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.student_table.verticalHeader().setVisible(False)
        self.student_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.student_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.student_table)

        # --- Action buttons ---
        action_row = QHBoxLayout()
        action_row.setSpacing(15)

        pdf_button = QPushButton("Save Current List as PDF")
        pdf_button.clicked.connect(self._save_pdf)
        action_row.addWidget(pdf_button)

        print_button = QPushButton("Print Current Class")
        print_button.clicked.connect(
            self._print_current_class
        )
        action_row.addWidget(print_button)

        delete_button = QPushButton("Delete Class")
        delete_button.setObjectName("secondaryButton")
        delete_button.clicked.connect(self._delete_class)
        action_row.addWidget(delete_button)

        layout.addLayout(action_row)

        back_button = QPushButton("Back to Class Management Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_CLASS_DASHBOARD)
        )
        layout.addWidget(back_button)

    # -------------------------------------------------------------
    # Loading a class
    # -------------------------------------------------------------


    def load_class(
        self,
        class_row,
    ) -> None:
        """
        Set up the page for a newly searched class,
        showing Active students first.
        """
        self.current_class_row = (
            class_row
        )

        self.class_title_label.setText(
            (
                "Class: "
                f"{class_row['class_name']}"
            )
        )

        self._refresh_capacity_display()
        self._refresh_strength_kpis()
        self._load_active_students()



    def _refresh_capacity_display(
        self,
    ) -> None:
        """
        Reload the selected class capacity together with
        Active-student and available-seat counts.
        """
        if not self.current_class_row:
            self.capacity_label.setText(
                (
                    "Capacity: - | Active: - | "
                    "Available Seats: -"
                )
            )

            self.edit_capacity_button.setEnabled(
                False
            )

            return

        details = (
            class_service
            .get_class_capacity_details(
                self.current_class_row[
                    "id"
                ]
            )
        )

        if not details:
            self.capacity_label.setText(
                (
                    "Capacity: unavailable | "
                    "Active: unavailable | "
                    "Available Seats: unavailable"
                )
            )

            self.edit_capacity_button.setEnabled(
                False
            )

            return

        self.current_class_row = (
            class_service.get_class_by_id(
                details["id"]
            )
        )

        self.capacity_label.setText(
            (
                f"Capacity: "
                f"{details['student_capacity']} | "
                f"Active: "
                f"{details['active_students']} | "
                f"Available Seats: "
                f"{details['available_seats']}"
            )
        )

        self.edit_capacity_button.setEnabled(
            True
        )

    def _edit_capacity(
        self,
    ) -> None:
        """
        Allow the stored capacity to be edited without
        changing any student record.
        """
        if not self.current_class_row:
            QMessageBox.warning(
                self,
                "No Class Selected",
                "Please load a class first.",
            )
            return

        details = (
            class_service
            .get_class_capacity_details(
                self.current_class_row[
                    "id"
                ]
            )
        )

        if not details:
            QMessageBox.warning(
                self,
                "Class Not Found",
                (
                    "The selected class could "
                    "not be found."
                ),
            )
            return

        current_capacity = int(
            details[
                "student_capacity"
            ]
        )

        active_students = int(
            details[
                "active_students"
            ]
        )

        minimum_capacity = max(
            1,
            active_students,
        )

        new_capacity, accepted = (
            QInputDialog.getInt(
                self,
                "Edit Class Capacity",
                (
                    f"Class: "
                    f"{details['class_name']}\n"
                    f"Current Active Students: "
                    f"{active_students}\n\n"
                    "Enter the maximum number of "
                    "Active students allowed:"
                ),
                current_capacity,
                minimum_capacity,
                1000,
                1,
            )
        )

        if not accepted:
            return

        if (
            new_capacity
            == current_capacity
        ):
            return

        try:
            saved_capacity = (
                class_service
                .update_class_capacity(
                    details["id"],
                    new_capacity,
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Capacity",
                str(error),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Capacity Update Error",
                (
                    "The class capacity could "
                    "not be updated."
                    f"\n\n{error}"
                ),
            )
            return

        self.current_class_row = (
            class_service.get_class_by_id(
                details["id"]
            )
        )

        self._refresh_capacity_display()

        QMessageBox.information(
            self,
            "Capacity Updated",
            (
                f"{details['class_name']} capacity "
                f"was updated to {saved_capacity}."
            ),
        )

    def _refresh_strength_kpis(self) -> None:
        """
        Refresh Active-only gender strength for the selected class.

        Play Group through Class 3:
            show Male Strength and Female Strength.

        Class 4 through Class 10:
            section A-F shows Male Strength only;
            section G-Z shows Female Strength only.
        """
        if not self.current_class_row:
            self.male_strength_kpi.hide()
            self.female_strength_kpi.hide()
            return

        class_id = int(
            self.current_class_row["id"]
        )

        class_level = str(
            self.current_class_row["class_level"]
            or ""
        ).strip().upper()

        section = str(
            self.current_class_row["section"]
            or ""
        ).strip().upper()

        male_count = (
            student_service
            .count_active_students_by_gender(
                class_id,
                "Male",
            )
        )

        female_count = (
            student_service
            .count_active_students_by_gender(
                class_id,
                "Female",
            )
        )

        self.male_strength_kpi.set_count(
            male_count
        )

        self.female_strength_kpi.set_count(
            female_count
        )

        try:
            numeric_level = int(
                class_level
            )
        except (
            TypeError,
            ValueError,
        ):
            numeric_level = None

        if (
            numeric_level is None
            or numeric_level <= 3
        ):
            self.male_strength_kpi.show()
            self.female_strength_kpi.show()
            return

        if (
            len(section) == 1
            and section in "ABCDEF"
        ):
            self.male_strength_kpi.show()
            self.female_strength_kpi.hide()
            return

        if (
            len(section) == 1
            and section
            in "GHIJKLMNOPQRSTUVWXYZ"
        ):
            self.male_strength_kpi.hide()
            self.female_strength_kpi.show()
            return

        # A malformed legacy Class 4+ section is not assigned a misleading
        # gender label. Capacity and Active table remain available.
        self.male_strength_kpi.hide()
        self.female_strength_kpi.hide()

    # -------------------------------------------------------------
    # Active student table population
    # -------------------------------------------------------------

    def _load_active_students(self) -> None:
        if not self.current_class_row:
            return

        class_id = int(
            self.current_class_row["id"]
        )

        self.current_students = (
            student_service.load_students_by_status(
                class_id,
                STATUS_ACTIVE,
            )
        )

        self.table_heading_label.setText(
            "Active Students"
        )

        self._populate_table(
            self.current_students,
            "No active students found in this class.",
        )

    def _populate_table(self, students, empty_message: str) -> None:
        self.student_table.setRowCount(0)

        if not students:
            self.empty_label.setText(empty_message)
            self.student_table.setVisible(False)
            return

        self.empty_label.setText("")
        self.student_table.setVisible(True)
        self.student_table.setRowCount(len(students))

        for row_index, student_row in enumerate(students):
            self.student_table.setItem(row_index, 0, QTableWidgetItem(student_row["admission_number"]))
            self.student_table.setItem(row_index, 1, QTableWidgetItem(student_service.display_roll_number(student_row)))
            self.student_table.setItem(
                row_index, 2,
                QTableWidgetItem(
                    student_service.display_name_with_stars(
                        student_row["student_name"], student_row["failure_count"]
                    )
                ),
            )
            self.student_table.setItem(row_index, 3, QTableWidgetItem(student_row["father_name"]))

    # -------------------------------------------------------------
    # Print current class
    # -------------------------------------------------------------

    def _print_current_class(self) -> None:
        """
        Print only Active students of the currently selected class.
        The displayed KPI selection does not affect printing.
        """
        if not self.current_class_row:
            QMessageBox.warning(
                self,
                "No Class Selected",
                "Please load a class first.",
            )
            return

        class_id = self.current_class_row["id"]
        class_name = self.current_class_row["class_name"]

        active_students = (
            student_service.load_students_by_status(
                class_id,
                STATUS_ACTIVE,
            )
        )

        if not active_students:
            QMessageBox.information(
                self,
                "No Active Students",
                (
                    "There are no Active students in "
                    f"{class_name} to print."
                ),
            )
            return

        printer = QPrinter(
            QPrinter.HighResolution
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )
        print_dialog.setWindowTitle(
            f"Print Active Students - {class_name}"
        )

        if print_dialog.exec() != QDialog.Accepted:
            return

        document = QTextDocument()
        document.setDocumentMargin(10)
        document.setHtml(
            class_service.build_student_list_print_html(
                class_name,
                active_students,
            )
        )
        document.print_(
            printer
        )

    # -------------------------------------------------------------
    # Save as PDF
    # -------------------------------------------------------------

    def _save_pdf(self) -> None:
        if not self.current_class_row:
            return

        list_type_label = "Active Students"
        class_name = self.current_class_row["class_name"]

        suggested_name = class_service.build_pdf_file_name(class_name, list_type_label)
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save Student List as PDF", suggested_name, "PDF Files (*.pdf)"
        )

        if not save_path:
            return  # User cancelled the save dialog.

        final_save_path = class_service.ensure_pdf_extension(save_path)

        try:
            actual_path = class_service.export_student_list_pdf(
                class_name, list_type_label, self.current_students, final_save_path
            )
            QMessageBox.information(
                self,
                "PDF Saved",
                f"Student list PDF saved successfully.\n\n{actual_path}",
            )
        except Exception:
            QMessageBox.critical(self, "PDF Save Error", "Unable to save the student list PDF.")

    # -------------------------------------------------------------
    # Delete class
    # -------------------------------------------------------------

    def _delete_class(self) -> None:
        if not self.current_class_row:
            return

        class_id = int(self.current_class_row["id"])
        confirm = QMessageBox.question(
            self,
            "Delete or Inactivate Class",
            "A never-used class will be deleted permanently. A class with "
            "current or historical references will be marked Inactive and "
            "preserved for reports and receipts. Continue?",
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            result = class_service.delete_class(class_id)
        except Exception as error:
            QMessageBox.critical(
                self, "Class Update Error",
                f"The class could not be updated safely.\n\n{error}",
            )
            return

        if result.get("action") == "inactivated":
            QMessageBox.information(
                self, "Class Inactivated",
                "This referenced class was marked Inactive. Historical data "
                "remains available and the class can be reactivated in Class Settings.",
            )
        else:
            QMessageBox.information(
                self, "Class Deleted", "The never-used class was deleted permanently."
            )
        self.current_class_row = None
        self.main_window.go_to_page(navigation.PAGE_CLASS_DASHBOARD)
