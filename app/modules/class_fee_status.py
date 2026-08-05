"""
Class Fee Status screen.

Shows every active student in a selected class together with:
- Last paid fee item
- Pending fee items
- Number of pending items
- Yellow or red fee status
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from app import navigation
from app.services import class_service
from app.services import student_service
from app.services import fee_service


# MONTHLY_ONLY_CLASS_FEE_STATUS_UI_V8
# Pending fee status now counts Monthly Fee items only.
CLASS_FEE_STATUS_CLEAN_LAYOUT_V1 = True

class ClassFeeStatusPage(QWidget):
    """Display fee status for all active students in one class."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        title_label = QLabel("Class Fee Status")
        title_label.setObjectName("pageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch()

        self.back_to_fee_management_button = QPushButton(
            "Back to Fee Management"
        )
        self.back_to_fee_management_button.setObjectName(
            "secondaryButton"
        )
        self.back_to_fee_management_button.setMinimumWidth(
            190
        )
        self.back_to_fee_management_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_FEE_DASHBOARD
            )
        )
        title_row.addWidget(
            self.back_to_fee_management_button
        )

        layout.addLayout(title_row)

        instruction_label = QLabel(
            "Select a class to view each active student's last paid fee, "
            "pending fee items, and payment status."
        )
        instruction_label.setWordWrap(True)
        layout.addWidget(instruction_label)

        # ---------------------------------------------------------------
        # Class selection
        # ---------------------------------------------------------------

        selection_row = QHBoxLayout()
        selection_row.setSpacing(12)

        selection_row.addWidget(
            QLabel("Select Class:")
        )

        self.class_combo = QComboBox()
        self.class_combo.setMinimumWidth(220)
        self.class_combo.setMaximumWidth(300)
        selection_row.addWidget(
            self.class_combo
        )

        load_button = QPushButton("Load Class Status")
        load_button.clicked.connect(
            self._load_class_status
        )
        selection_row.addWidget(
            load_button
        )

        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName(
            "secondaryButton"
        )
        refresh_button.clicked.connect(
            self._load_class_status
        )
        selection_row.addWidget(
            refresh_button
        )

        selection_row.addStretch()
        layout.addLayout(selection_row)

        # ---------------------------------------------------------------
        # Class summary
        # ---------------------------------------------------------------

        summary_row = QHBoxLayout()
        summary_row.setSpacing(15)

        self.total_students_label = QLabel(
            "Total Students: 0"
        )
        self.total_students_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.total_students_label
        )

        self.fully_paid_label = QLabel(
            "Fully Paid: 0"
        )
        self.fully_paid_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.fully_paid_label
        )

        self.yellow_status_label = QLabel(
            "Yellow Status: 0"
        )
        self.yellow_status_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.yellow_status_label
        )

        self.red_status_label = QLabel(
            "Red Status: 0"
        )
        self.red_status_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.red_status_label
        )

        summary_row.addStretch()
        layout.addLayout(summary_row)

        status_note = QLabel(
            "Yellow: latest monthly fee is unpaid. "
            "Red: two or more latest monthly fees are unpaid."
        )
        status_note.setWordWrap(True)
        layout.addWidget(status_note)

        # ---------------------------------------------------------------
        # Status table
        # ---------------------------------------------------------------

        self.status_table = QTableWidget()
        self.status_table.setColumnCount(8)
        self.status_table.setHorizontalHeaderLabels(
            [
                "Admission No",
                "Roll No",
                "Student Name",
                "Father Name",
                "Father CNIC",
                "Last Paid",
                "Pending Fees",
                "Fee Status",
            ]
        )

        self.status_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setWordWrap(False)
        self.status_table.setMinimumHeight(250)

        layout.addWidget(
            self.status_table,
            1,
        )

        self.reload_class_dropdown()
        self.reset_page()

    # ------------------------------------------------------------------
    # Page setup
    # ------------------------------------------------------------------

    def reload_class_dropdown(self):
        """Load every existing class into the dropdown."""
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem(
            "Select Class",
            None,
        )

        for class_row in class_service.get_all_classes():
            self.class_combo.addItem(
                class_row["class_name"],
                class_row["id"],
            )

        self.class_combo.setCurrentIndex(0)
        self.class_combo.blockSignals(False)

    def reset_page(self):
        """Clear the class status table and summary."""
        self.status_table.setRowCount(0)

        self.total_students_label.setText(
            "Total Students: 0"
        )
        self.fully_paid_label.setText(
            "Fully Paid: 0"
        )
        self.yellow_status_label.setText(
            "Yellow Status: 0"
        )
        self.red_status_label.setText(
            "Red Status: 0"
        )

    # ------------------------------------------------------------------
    # Load class status
    # ------------------------------------------------------------------

    def _load_class_status(self):
        class_id = self.class_combo.currentData()

        if class_id is None:
            QMessageBox.warning(
                self,
                "No Class Selected",
                "Please select a class first.",
            )
            return

        try:
            status_rows = (
                fee_service.get_class_fee_status_rows(
                    class_id
                )
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Fee Setup Required",
                str(error),
            )
            self.reset_page()
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Class Status Error",
                "The class fee status could not be loaded."
                f"\n\n{error}",
            )
            self.reset_page()
            return

        self._populate_status_table(
            status_rows
        )

    def _populate_status_table(self, status_rows):
        self.status_table.setRowCount(
            len(status_rows)
        )

        fully_paid_count = 0
        yellow_count = 0
        red_count = 0

        for row_index, status_row in enumerate(
            status_rows
        ):
            display_name = (
                student_service.display_name_with_stars(
                    status_row["student_name"],
                    status_row["failure_count"],
                )
            )

            unpaid_months = status_row[
                "unpaid_months"
            ]

            pending_text = (
                ", ".join(unpaid_months)
                if unpaid_months
                else "None"
            )

            color_status = status_row[
                "fee_color_status"
            ]

            monthly_unpaid_count = len(
                unpaid_months
            )

            fee_status_text = str(
                monthly_unpaid_count
            )

            if color_status == "yellow":
                row_color = QColor(
                    255,
                    245,
                    170,
                )
                yellow_count += 1

            elif color_status == "red":
                row_color = QColor(
                    255,
                    190,
                    190,
                )
                red_count += 1

            else:
                row_color = None
                fully_paid_count += 1

            values = [
                status_row["admission_number"],
                status_row["roll_number"],
                display_name,
                status_row["father_name"],
                status_row["father_cnic"],
                status_row["last_paid"],
                pending_text,
                fee_status_text,
            ]

            for column_index, value in enumerate(
                values
            ):
                item = QTableWidgetItem(
                    str(value)
                )

                item.setTextAlignment(
                    Qt.AlignCenter
                )

                if row_color is not None:
                    item.setBackground(
                        row_color
                    )

                self.status_table.setItem(
                    row_index,
                    column_index,
                    item,
                )

        total_students = len(status_rows)

        self.total_students_label.setText(
            f"Total Students: {total_students}"
        )
        self.fully_paid_label.setText(
            f"Fully Paid: {fully_paid_count}"
        )
        self.yellow_status_label.setText(
            f"Yellow Status: {yellow_count}"
        )
        self.red_status_label.setText(
            f"Red Status: {red_count}"
        )

        if not status_rows:
            QMessageBox.information(
                self,
                "No Active Students",
                "The selected class has no active students.",
            )