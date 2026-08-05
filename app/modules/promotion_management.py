"""
Promotion Management module.
Contains the Promotion Dashboard and the supported Full Class Promotion
screen. Individual student movement is handled only through the manual
class/section change workflow in Student Management. Promotion reversal
and single-record promotion are intentionally unavailable.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView,
)
from app import navigation
from app.config import (
    STATUS_ACTIVE,
    STATUS_PARTIALLY_GRADUATED,
)
from app.services import class_service
from app.services import student_service
from app.services import promotion_service


PROMOTION_PF_CELL_ALIGNMENT_V1 = True


# ---------------------------------------------------------------------------
# Promotion Dashboard
# ---------------------------------------------------------------------------

class PromotionDashboardPage(QWidget):
    """The Promotion Management landing page, shown when the module opens."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        title_label = QLabel("Promotion")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        button_column = QVBoxLayout()
        button_column.setSpacing(12)

        full_class_button = QPushButton("Full Class Promotion")
        full_class_button.clicked.connect(self._open_full_class_promotion)
        button_column.addWidget(full_class_button)

        layout.addLayout(button_column)

        back_button = QPushButton("Back to Main Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_MAIN_DASHBOARD)
        )
        layout.addWidget(back_button)
        layout.addStretch()

    def _open_full_class_promotion(self):
        page = self.main_window.pages[navigation.PAGE_FULL_CLASS_PROMOTION]
        page.reload_class_dropdown()
        page.reset_page()
        self.main_window.go_to_page(navigation.PAGE_FULL_CLASS_PROMOTION)

# ---------------------------------------------------------------------------
# Full Class Promotion
# ---------------------------------------------------------------------------

class FullClassPromotionPage(QWidget):
    """
    Select an Active class, enter P or F for every Active student in it,
    then process the whole class as one promotion batch.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.current_class_row = None
        self.active_students = []
        self.partially_graduated_students = []
        self.promotion_context = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(15)

        title_label = QLabel("Full Class Promotion")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        select_row = QHBoxLayout()
        select_row.setSpacing(15)

        self.class_combo = QComboBox()
        self.class_combo.currentIndexChanged.connect(
            self._on_class_selection_changed
        )
        select_row.addWidget(QLabel("Select Class:"))
        select_row.addWidget(self.class_combo, 1)

        load_button = QPushButton("Load Class")
        load_button.clicked.connect(self._load_class)
        select_row.addWidget(load_button)

        layout.addLayout(select_row)

        self.warning_banner = QLabel("")
        self.warning_banner.setObjectName("warningBanner")
        self.warning_banner.setWordWrap(True)
        self.warning_banner.setVisible(False)
        layout.addWidget(self.warning_banner)

        self.empty_label = QLabel("")
        layout.addWidget(self.empty_label)

        self.students_table = QTableWidget()
        self.students_table.setColumnCount(6)
        self.students_table.setHorizontalHeaderLabels(
            [
                "Admission No",
                "Roll Number",
                "Student Name",
                "Father Name",
                "Status",
                "P/F",
            ]
        )

        promotion_header = self.students_table.horizontalHeader()

        for column_index in range(5):
            promotion_header.setSectionResizeMode(
                column_index,
                QHeaderView.Stretch,
            )

        promotion_header.setSectionResizeMode(
            5,
            QHeaderView.Fixed,
        )
        self.students_table.setColumnWidth(
            5,
            100,
        )
        promotion_header.setDefaultAlignment(
            Qt.AlignCenter
        )

        self.students_table.verticalHeader().setVisible(False)
        self.students_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.students_table)

        self.promote_button = QPushButton("Promote Entire Class")
        self.promote_button.setEnabled(False)
        self.promote_button.clicked.connect(self._promote_class)
        layout.addWidget(self.promote_button)

        back_row = QHBoxLayout()
        back_row.setSpacing(15)

        back_to_promotion_button = QPushButton("Back to Promotion Dashboard")
        back_to_promotion_button.setObjectName("secondaryButton")
        back_to_promotion_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_PROMOTION_DASHBOARD)
        )
        back_row.addWidget(back_to_promotion_button)

        back_to_main_button = QPushButton("Back to Main Dashboard")
        back_to_main_button.setObjectName("secondaryButton")
        back_to_main_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_MAIN_DASHBOARD)
        )
        back_row.addWidget(back_to_main_button)

        layout.addLayout(back_row)

    # -------------------------------------------------------------
    # Setup / reset
    # -------------------------------------------------------------

    def reload_class_dropdown(self):
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem("Select a class...", None)
        for class_row in (
            promotion_service.get_classes_in_descending_promotion_order()
        ):
            self.class_combo.addItem(
                class_row["class_name"],
                class_row["id"],
            )
        self.class_combo.blockSignals(False)

    def _clear_loaded_class(self, message: str = ""):
        self.current_class_row = None
        self.active_students = []
        self.partially_graduated_students = []
        self.promotion_context = None
        self.students_table.setRowCount(0)
        self.students_table.setVisible(True)
        self.empty_label.setText(message)
        self.warning_banner.setVisible(False)
        self.warning_banner.setText("")
        self.promote_button.setEnabled(False)

    def _on_class_selection_changed(self, *_args):
        message = (
            "Class selection changed. Click Load Class before promotion."
            if self.class_combo.currentData() is not None
            else ""
        )
        self._clear_loaded_class(message)

    def reset_page(self):
        self.class_combo.blockSignals(True)
        self.class_combo.setCurrentIndex(0)
        self.class_combo.blockSignals(False)
        self._clear_loaded_class("")

    # -------------------------------------------------------------
    # Load class
    # -------------------------------------------------------------

    def _load_class(self):
        class_id = self.class_combo.currentData()
        self._clear_loaded_class("")
        if not class_id:
            QMessageBox.warning(self, "No Class Selected", "Please select a class.")
            return

        class_row = class_service.get_class_by_id(class_id)
        if not class_row:
            QMessageBox.warning(self, "Class Not Found", "Class not found.")
            return

        try:
            self.promotion_context = promotion_service.get_promotion_context(class_id)
        except ValueError as error:
            QMessageBox.warning(self, "Promotion Session", str(error))
            return

        self.current_class_row = class_row
        self.active_students = promotion_service.get_promotion_cohort_students(
            class_id
        )
        self.partially_graduated_students = []

        if str(class_row["class_level"] or "").strip() == "10":
            self.partially_graduated_students = (
                student_service.load_students_by_status(
                    class_id,
                    STATUS_PARTIALLY_GRADUATED,
                )
            )

        self._populate_table()
        self.promote_button.setEnabled(bool(self.active_students))

        mode = str(self.promotion_context.get("promotion_mode") or "NORMAL")
        context_message = (
            f"{'Overdue Promotion Recovery' if mode == 'OVERDUE' else 'Normal Full-Class Promotion'} | "
            f"Source Session: {self.promotion_context['source_session_label']} | "
            f"Target Session: {self.promotion_context['target_session_label']} | "
            f"Effective Date: {self.promotion_context['effective_date']}"
        )
        warning_messages = [context_message]
        self.promote_button.setText(
            "Complete Overdue Promotion" if mode == "OVERDUE"
            else "Promote Entire Class"
        )

        if promotion_service.was_class_promoted_within_months(class_id):
            warning_messages.append(
                "This class has already been promoted within the last 8 months."
            )

        if promotion_service.was_class_promoted_into_within_months(class_id):
            warning_messages.append(
                "This class recently received newly promoted students within the "
                "last 8 months. Promoting it again may affect students who just arrived."
            )

        integrity_warning = promotion_service.consume_promotion_integrity_warning()
        if integrity_warning:
            warning_messages.append(
                "Promotion history contains an invalid timestamp. "
                "Please review the integrity log before promoting this class."
            )

        if warning_messages:
            self.warning_banner.setText(" ".join(warning_messages))
            self.warning_banner.setVisible(True)
        else:
            self.warning_banner.setVisible(False)
            self.warning_banner.setText("")

    @staticmethod
    def _create_pf_cell():
        """Return a centered P/F selector contained inside a table cell."""
        cell_container = QWidget()
        cell_layout = QHBoxLayout(cell_container)
        cell_layout.setContentsMargins(8, 4, 8, 4)
        cell_layout.setSpacing(0)
        cell_layout.setAlignment(Qt.AlignCenter)

        pf_selector = QComboBox(cell_container)
        pf_selector.setObjectName(
            "promotionValueSelector"
        )
        pf_selector.addItems(["P", "F"])
        pf_selector.setCurrentText("P")
        pf_selector.setToolTip(
            "P = Pass, F = Fail"
        )
        pf_selector.setFixedSize(64, 32)

        cell_layout.addWidget(
            pf_selector,
            0,
            Qt.AlignCenter,
        )

        return cell_container

    @staticmethod
    def _get_pf_selector(cell_widget):
        """Return the P/F combo from the centered table-cell container."""
        if isinstance(cell_widget, QComboBox):
            return cell_widget

        if cell_widget is None:
            return None

        return cell_widget.findChild(
            QComboBox,
            "promotionValueSelector",
        )

    def _populate_table(self):
        self.students_table.setRowCount(0)
        visible_students = (
            list(self.active_students)
            + list(self.partially_graduated_students)
        )

        if not visible_students:
            self.empty_label.setText(
                "No active or Partially Graduated students found in this class."
            )
            self.students_table.setVisible(False)
            return

        self.empty_label.setText("")
        self.students_table.setVisible(True)
        self.students_table.setRowCount(len(visible_students))

        for row_index, student_row in enumerate(self.active_students):
            self.students_table.setItem(row_index, 0, QTableWidgetItem(student_row["admission_number"]))
            self.students_table.setItem(row_index, 1, QTableWidgetItem(student_service.display_roll_number(student_row)))
            self.students_table.setItem(
                row_index,
                2,
                QTableWidgetItem(
                    student_service.display_name_with_status_marker(
                        student_row["student_name"],
                        student_row["failure_count"],
                        student_row["student_status"],
                    )
                ),
            )
            self.students_table.setItem(row_index, 3, QTableWidgetItem(student_row["father_name"]))
            self.students_table.setItem(row_index, 4, QTableWidgetItem(student_row["student_status"]))

            pf_cell = self._create_pf_cell()
            self.students_table.setCellWidget(
                row_index,
                5,
                pf_cell,
            )
            self.students_table.setRowHeight(
                row_index,
                42,
            )

        partial_start = len(self.active_students)

        for offset, student_row in enumerate(self.partially_graduated_students):
            row_index = partial_start + offset
            self.students_table.setItem(row_index, 0, QTableWidgetItem(student_row["admission_number"]))
            self.students_table.setItem(row_index, 1, QTableWidgetItem(student_service.display_roll_number(student_row)))
            self.students_table.setItem(
                row_index,
                2,
                QTableWidgetItem(
                    student_service.display_name_with_status_marker(
                        student_row["student_name"],
                        student_row["failure_count"],
                        student_row["student_status"],
                    )
                ),
            )
            self.students_table.setItem(row_index, 3, QTableWidgetItem(student_row["father_name"]))
            self.students_table.setItem(row_index, 4, QTableWidgetItem(student_row["student_status"]))

            pending_label = QLabel("Awaiting Fees")
            pending_label.setAlignment(Qt.AlignCenter)
            pending_label.setToolTip(
                "Status changes automatically to Graduated after all Monthly Fees are paid."
            )
            self.students_table.setCellWidget(row_index, 5, pending_label)
            self.students_table.setRowHeight(row_index, 42)

    # -------------------------------------------------------------
    # Promote
    # -------------------------------------------------------------

    def _promote_class(self):
        selected_class_id = self.class_combo.currentData()
        if (
            not self.current_class_row
            or not self.active_students
            or selected_class_id is None
            or int(selected_class_id) != int(self.current_class_row["id"])
        ):
            self._clear_loaded_class(
                "Class selection is not loaded. Click Load Class before promotion."
            )
            QMessageBox.warning(
                self,
                "No Class Loaded",
                "Please load the currently selected class with active students first.",
            )
            return

        try:
            current_context = promotion_service.get_promotion_context(
                int(selected_class_id)
            )
        except ValueError as error:
            self._clear_loaded_class(
                "Promotion session changed. Reload the class before promotion."
            )
            QMessageBox.warning(self, "Promotion Session", str(error))
            return
        if (
            not self.promotion_context
            or int(current_context["source_session_start_year"])
               != int(self.promotion_context["source_session_start_year"])
            or str(current_context["promotion_mode"])
               != str(self.promotion_context["promotion_mode"])
        ):
            self._clear_loaded_class(
                "Promotion session changed. Reload the class before promotion."
            )
            QMessageBox.warning(
                self, "Promotion Session Changed",
                "The academic promotion context changed. Reload the class and try again."
            )
            return

        promotion_values = {}
        for row_index, student_row in enumerate(self.active_students):
            pf_cell = self.students_table.cellWidget(
                row_index,
                5,
            )
            pf_input = self._get_pf_selector(
                pf_cell
            )

            if pf_input is None:
                QMessageBox.warning(
                    self,
                    "Promotion Input Error",
                    (
                        "The P/F selector could not be read. "
                        "Reload the class and try again."
                    ),
                )
                return

            value = (
                pf_input.currentText()
                .strip()
                .upper()
            )
            if not promotion_service.validate_promotion_value(value):
                QMessageBox.warning(
                    self, "Missing Promotion Value",
                    f"Please enter P or F for {student_row['student_name']} (Roll {student_row['roll_number']}).",
                )
                return
            promotion_values[student_row["student_id"]] = value

        class_id = self.current_class_row["id"]

        try:
            preview = promotion_service.preview_full_class_promotion_plan(
                class_id, promotion_values
            )
        except ValueError as error:
            QMessageBox.warning(self, "Promotion Preview", str(error))
            return

        simulated_rows = int(preview.get("source_fee_rows_would_be_generated", 0))
        preview_lines = [
            "This preview is read-only and has made no database changes.",
            f"Mode: {preview.get('promotion_mode')}",
            f"Source Session: {preview.get('source_session_label')}",
            f"Target Session: {preview.get('target_session_label')}",
            f"Academic Effective Date: {preview.get('effective_date')}",
            f"Pass: {int(preview.get('passed_students', 0))}",
            f"Fail / Retain: {int(preview.get('failed_students', 0))}",
            f"Monthly fee rows that would be generated: {simulated_rows}",
            "Paid and receipt-backed fee records will remain unchanged.",
            "Only unpaid or receipt-free waived rows will follow the final class.",
        ]
        if preview.get("routing_type") == "GRADUATION":
            graduated = sum(
                1 for item in preview.get("students", [])
                if item.get("new_status") == "Graduated"
            )
            partial = sum(
                1 for item in preview.get("students", [])
                if item.get("new_status") == "Partially Graduated"
            )
            preview_lines.extend([
                f"Would become Graduated: {graduated}",
                f"Would become Partially Graduated: {partial}",
            ])

        confirm = QMessageBox.question(
            self,
            "Read-Only Promotion Preview",
            "\n".join(preview_lines)
            + "\n\nConfirm the actual full-class promotion?",
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            promotion_service.promote_full_class(class_id, promotion_values)
        except (ValueError, RuntimeError) as error:
            QMessageBox.warning(self, "Promotion Error", str(error))
            return

        completion_mode = str(preview.get("promotion_mode") or "NORMAL")
        QMessageBox.information(
            self,
            "Promotion Completed",
            "Overdue promotion recovery completed successfully."
            if completion_mode == "OVERDUE"
            else "Full class promotion completed successfully.",
        )
        self.reset_page()
