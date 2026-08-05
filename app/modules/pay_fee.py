"""
Pay Fee screen.

This page:
- Searches students using Fee Management search rules.
- Shows duplicate search results for manual selection.
- Keeps the selected student visible.
- Loads Monthly Fee records.
- Enforces continuous payment order.
- Calculates the selected total.

The receipt preview and permanent payment saving will be connected
in the next step.
"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QPushButton,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QHeaderView,
    QCheckBox,
    QScrollArea,
    QStackedWidget,
    QFrame,
    QSizePolicy,
)

from app import navigation
from app.ui_tables import (
    configure_pay_fee_search_table,
    configure_table_action_button,
    keep_table_left_aligned,
)
from app.services import class_service
from app.services import student_service
from app.services import fee_service


# FEE_TRACKING_ID_UI_V1
# CURRENT_SESSION_FULL_FEE_HISTORY_UI_V1
# TRACKING_ID_ONLY_FULL_FEE_HISTORY_V1
# MONTHLY_ONLY_PAY_FEE_UI_V8
# Fee table item names now use Monthly Fee month and year only.
PAY_FEE_CLEAN_ACTION_CELL_V1 = True
RECEIPT_NUMBER_REOPEN_UI_V16 = True
class PayFeePage(QWidget):
    """Search a student and select payable fee items in order."""

    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window

        self.selected_student_id = None
        self.selected_student = None

        # The complete Paid and Unpaid history is shown only when
        # the selected student came from a successful Tracking ID search.
        self._tracking_id_history_requested = False
        self.full_fee_history_mode = False

        self.fee_rows = []
        self.fee_checkboxes = []

        # ---------------------------------------------------------------
        # Two full-page views
        # ---------------------------------------------------------------

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        outer_layout.setSpacing(0)

        self.page_stack = QStackedWidget()
        outer_layout.addWidget(
            self.page_stack
        )

        # ===============================================================
        # VIEW 1: STUDENT SEARCH
        # ===============================================================

        self.search_view = QWidget()

        search_page_layout = QVBoxLayout(
            self.search_view
        )
        search_page_layout.setContentsMargins(
            24,
            18,
            24,
            18,
        )
        search_page_layout.setSpacing(12)

        search_title_row = QHBoxLayout()
        search_title_row.setSpacing(12)

        search_title_label = QLabel(
            "Pay Fee"
        )
        search_title_label.setObjectName(
            "pageTitle"
        )
        search_title_row.addWidget(
            search_title_label
        )

        search_title_row.addStretch()

        back_dashboard_button = QPushButton(
            "Back to Fee Management Dashboard"
        )
        back_dashboard_button.setObjectName(
            "secondaryButton"
        )
        back_dashboard_button.setMinimumWidth(
            225
        )
        back_dashboard_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_FEE_DASHBOARD
            )
        )
        search_title_row.addWidget(
            back_dashboard_button
        )

        search_page_layout.addLayout(
            search_title_row
        )

        instruction_label = QLabel(
            "Search students by Tracking ID or the existing fields. "
            "To reopen a completed paid receipt, enter its Receipt Number "
            "and press Enter or click Open Receipt. Example text shown "
            "inside an empty field is only a placeholder."
        )
        instruction_label.setWordWrap(
            True
        )
        instruction_label.setStyleSheet(
            """
            QLabel {
                color: #5F6C82;
                font-size: 13px;
                padding-bottom: 2px;
            }
            """
        )
        search_page_layout.addWidget(
            instruction_label
        )

        # ---------------------------------------------------------------
        # Search form card
        # ---------------------------------------------------------------

        search_card = QFrame()
        search_card.setObjectName(
            "searchCard"
        )

        search_card_layout = QVBoxLayout(
            search_card
        )
        search_card_layout.setContentsMargins(
            16,
            14,
            16,
            14,
        )
        search_card_layout.setSpacing(
            12
        )

        search_grid = QGridLayout()
        search_grid.setHorizontalSpacing(
            12
        )
        search_grid.setVerticalSpacing(
            9
        )
        search_grid.setColumnStretch(
            1,
            1,
        )
        search_grid.setColumnStretch(
            3,
            1,
        )
        search_grid.setColumnStretch(
            5,
            1,
        )

        self.tracking_id_input = QLineEdit()
        self.tracking_id_input.setPlaceholderText(
            "Enter Tracking ID, e.g. HMS-000001"
        )
        self.tracking_id_input.setClearButtonEnabled(
            True
        )
        self.tracking_id_input.setToolTip(
            "Type the student's Tracking ID. The example text is not an entered value."
        )

        self.receipt_number_input = QLineEdit()
        self.receipt_number_input.setPlaceholderText(
            "000001"
        )
        self.receipt_number_input.setMaxLength(
            20
        )
        self.receipt_number_input.setClearButtonEnabled(
            True
        )
        self.receipt_number_input.setToolTip(
            "Enter a completed internal Receipt Number and press Enter."
        )

        self.admission_number_input = QLineEdit()
        self.admission_number_input.setPlaceholderText(
            "Admission Number"
        )
        self.admission_number_input.setClearButtonEnabled(
            True
        )

        self.student_name_input = QLineEdit()
        self.student_name_input.setPlaceholderText(
            "Student Name"
        )
        self.student_name_input.setClearButtonEnabled(
            True
        )

        self.father_name_input = QLineEdit()
        self.father_name_input.setPlaceholderText(
            "Father Name"
        )
        self.father_name_input.setClearButtonEnabled(
            True
        )

        self.roll_number_input = QLineEdit()
        self.roll_number_input.setPlaceholderText(
            "Roll Number"
        )
        self.roll_number_input.setClearButtonEnabled(
            True
        )

        self.class_combo = QComboBox()
        self.class_combo.setMinimumWidth(
            175
        )

        self.father_cnic_input = QLineEdit()
        self.father_cnic_input.setPlaceholderText(
            "13-digit Father CNIC"
        )
        self.father_cnic_input.setMaxLength(
            13
        )
        self.father_cnic_input.setClearButtonEnabled(
            True
        )

        search_grid.addWidget(
            QLabel("Admission Number"),
            0,
            0,
        )
        search_grid.addWidget(
            self.admission_number_input,
            0,
            1,
        )

        search_grid.addWidget(
            QLabel("Student Name"),
            0,
            2,
        )
        search_grid.addWidget(
            self.student_name_input,
            0,
            3,
        )

        search_grid.addWidget(
            QLabel("Father Name"),
            0,
            4,
        )
        search_grid.addWidget(
            self.father_name_input,
            0,
            5,
        )

        search_grid.addWidget(
            QLabel("Roll Number"),
            1,
            0,
        )
        search_grid.addWidget(
            self.roll_number_input,
            1,
            1,
        )

        search_grid.addWidget(
            QLabel("Select Class"),
            1,
            2,
        )
        search_grid.addWidget(
            self.class_combo,
            1,
            3,
        )

        search_grid.addWidget(
            QLabel("Father CNIC"),
            1,
            4,
        )
        search_grid.addWidget(
            self.father_cnic_input,
            1,
            5,
        )

        tracking_row = QHBoxLayout()
        tracking_row.setSpacing(
            12
        )

        tracking_label = QLabel(
            "Tracking ID"
        )
        tracking_label.setMinimumWidth(
            100
        )

        tracking_row.addWidget(
            tracking_label
        )
        tracking_row.addWidget(
            self.tracking_id_input,
            1,
        )

        receipt_label = QLabel(
            "Receipt Number"
        )
        receipt_label.setMinimumWidth(
            115
        )
        tracking_row.addWidget(
            receipt_label
        )
        tracking_row.addWidget(
            self.receipt_number_input,
            1,
        )

        open_receipt_button = QPushButton(
            "Open Receipt"
        )
        open_receipt_button.setObjectName(
            "secondaryButton"
        )
        open_receipt_button.setMinimumWidth(
            135
        )
        open_receipt_button.clicked.connect(
            self._open_paid_receipt_by_number
        )
        tracking_row.addWidget(
            open_receipt_button
        )

        search_card_layout.addLayout(
            tracking_row
        )

        search_card_layout.addLayout(
            search_grid
        )

        search_button_row = QHBoxLayout()
        search_button_row.setSpacing(
            12
        )
        search_button_row.addStretch()

        search_button = QPushButton(
            "Search Student"
        )
        search_button.setMinimumWidth(
            190
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
        clear_search_button.setObjectName(
            "secondaryButton"
        )
        clear_search_button.setMinimumWidth(
            160
        )
        clear_search_button.clicked.connect(
            self.reset_page
        )
        search_button_row.addWidget(
            clear_search_button
        )

        search_button_row.addStretch()

        search_card_layout.addLayout(
            search_button_row
        )

        search_page_layout.addWidget(
            search_card
        )

        # ---------------------------------------------------------------
        # Enter-key navigation
        # ---------------------------------------------------------------

        self.tracking_id_input.returnPressed.connect(
            self._search_students
        )
        self.receipt_number_input.returnPressed.connect(
            self._open_paid_receipt_by_number
        )

        self.admission_number_input.returnPressed.connect(
            self.student_name_input.setFocus
        )
        self.student_name_input.returnPressed.connect(
            self.father_name_input.setFocus
        )
        self.father_name_input.returnPressed.connect(
            self.roll_number_input.setFocus
        )
        self.roll_number_input.returnPressed.connect(
            self.class_combo.setFocus
        )

        self.class_combo.installEventFilter(
            self
        )

        self.father_cnic_input.returnPressed.connect(
            self._search_students
        )

        QWidget.setTabOrder(
            self.tracking_id_input,
            self.receipt_number_input,
        )
        QWidget.setTabOrder(
            self.receipt_number_input,
            self.admission_number_input,
        )
        QWidget.setTabOrder(
            self.admission_number_input,
            self.student_name_input,
        )
        QWidget.setTabOrder(
            self.student_name_input,
            self.father_name_input,
        )
        QWidget.setTabOrder(
            self.father_name_input,
            self.roll_number_input,
        )
        QWidget.setTabOrder(
            self.roll_number_input,
            self.class_combo,
        )
        QWidget.setTabOrder(
            self.class_combo,
            self.father_cnic_input,
        )

        # ---------------------------------------------------------------
        # Search-results heading
        # ---------------------------------------------------------------

        results_heading_row = QHBoxLayout()

        results_heading = QLabel(
            "Search Results"
        )
        results_heading.setObjectName(
            "sectionHeading"
        )
        results_heading_row.addWidget(
            results_heading
        )

        results_heading_row.addStretch()

        self.search_results_count_label = QLabel(
            "0 students"
        )
        self.search_results_count_label.setStyleSheet(
            """
            QLabel {
                color: #657187;
                font-size: 12px;
                font-weight: 600;
            }
            """
        )
        results_heading_row.addWidget(
            self.search_results_count_label
        )

        search_page_layout.addLayout(
            results_heading_row
        )

        # ---------------------------------------------------------------
        # Search-results table: only this area scrolls
        # ---------------------------------------------------------------

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(
            8
        )
        self.results_table.setHorizontalHeaderLabels(
            [
                "Tracking ID",
                "Admission No",
                "Roll No",
                "Student Name",
                "Father Name",
                "Father CNIC",
                "Class",
                "Action",
            ]
        )
        self.results_table.setAlternatingRowColors(
            True
        )
        self.results_table.setWordWrap(
            False
        )
        self.results_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.results_table.verticalHeader().setVisible(
            False
        )
        self.results_table.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
        self.results_table.setMinimumHeight(
            190
        )

        configure_pay_fee_search_table(
            self.results_table
        )
        self.results_table.setColumnWidth(
            7,
            140,
        )

        self.results_table.setStyleSheet(
            """
            QWidget#tableCellContainer {
                background-color: transparent;
            }
            """
        )

        search_page_layout.addWidget(
            self.results_table,
            1,
        )

        # ===============================================================
        # VIEW 2: FEE RECORD
        # ===============================================================

        self.fee_record_view = QWidget()

        fee_page_layout = QVBoxLayout(
            self.fee_record_view
        )
        fee_page_layout.setContentsMargins(
            24,
            18,
            24,
            18,
        )
        fee_page_layout.setSpacing(
            11
        )

        fee_title_row = QHBoxLayout()
        fee_title_row.setSpacing(
            12
        )

        fee_title_label = QLabel(
            "Fee Record"
        )
        fee_title_label.setObjectName(
            "pageTitle"
        )
        fee_title_row.addWidget(
            fee_title_label
        )

        fee_title_row.addStretch()

        back_search_button = QPushButton(
            "Back to Student Search"
        )
        back_search_button.setObjectName(
            "secondaryButton"
        )
        back_search_button.setMinimumWidth(
            190
        )
        back_search_button.clicked.connect(
            self._back_to_student_search
        )
        fee_title_row.addWidget(
            back_search_button
        )

        fee_page_layout.addLayout(
            fee_title_row
        )

        self.selected_student_label = QLabel(
            "Selected Student: None"
        )
        self.selected_student_label.setObjectName(
            "studentSummary"
        )
        self.selected_student_label.setWordWrap(
            True
        )
        fee_page_layout.addWidget(
            self.selected_student_label
        )

        fee_instruction_label = QLabel(
            "Select unpaid fee items in sequence. Earlier unpaid "
            "items cannot be skipped."
        )
        fee_instruction_label.setWordWrap(
            True
        )
        fee_instruction_label.setStyleSheet(
            """
            QLabel {
                color: #5F6C82;
                font-size: 13px;
            }
            """
        )
        fee_page_layout.addWidget(
            fee_instruction_label
        )

        # ---------------------------------------------------------------
        # Fee table: only this area scrolls
        # ---------------------------------------------------------------

        self.fee_table = QTableWidget()
        self.fee_table.setObjectName(
            "feeRecordTable"
        )
        self.fee_table.setColumnCount(
            8
        )
        self.fee_table.setHorizontalHeaderLabels(
            [
                "Month / Fee Item",
                "Class",
                "Session",
                "Amount",
                "Status",
                "Payment Date",
                "Receipt No.",
                "Select",
            ]
        )
        self.fee_table.setAlternatingRowColors(
            True
        )
        self.fee_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.fee_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.fee_table.verticalHeader().setVisible(
            False
        )
        self.fee_table.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
        self.fee_table.setMinimumHeight(
            250
        )

        fee_header = (
            self.fee_table.horizontalHeader()
        )
        fee_header.setStretchLastSection(
            False
        )

        # FEE_TABLE_PROPORTIONS_FIX

        # Month / fee item gets a controlled width.
        fee_header.setSectionResizeMode(
            0,
            QHeaderView.Interactive,
        )
        self.fee_table.setColumnWidth(
            0,
            205,
        )

        # Historical information, payment date and receipt columns
        # share the remaining width.
        for column_index in range(
            1,
            7,
        ):
            fee_header.setSectionResizeMode(
                column_index,
                QHeaderView.Stretch,
            )

        # Checkbox column remains compact.
        fee_header.setSectionResizeMode(
            7,
            QHeaderView.Fixed,
        )
        self.fee_table.setColumnWidth(
            7,
            76,
        )

        self.fee_table.setStyleSheet(
            """
            QTableWidget#feeRecordTable::item {
                padding: 0px;
                margin: 0px;
            }

            QWidget#tableCellContainer {
                background-color: transparent;
                margin: 0px;
                padding: 0px;
            }
            """
        )

        fee_page_layout.addWidget(
            self.fee_table,
            1,
        )

        # ---------------------------------------------------------------
        # Selected total
        # ---------------------------------------------------------------

        total_frame = QFrame()
        total_frame.setObjectName(
            "summaryCard"
        )

        total_layout = QHBoxLayout(
            total_frame
        )
        total_layout.setContentsMargins(
            14,
            8,
            14,
            8,
        )

        total_layout.addStretch()

        total_title = QLabel(
            "Selected Total"
        )
        total_title.setStyleSheet(
            """
            QLabel {
                color: #46546C;
                font-size: 13px;
                font-weight: 650;
            }
            """
        )
        total_layout.addWidget(
            total_title
        )

        self.selected_total_label = QLabel(
            "Rs. 0.00"
        )
        self.selected_total_label.setStyleSheet(
            """
            QLabel {
                color: #173A9C;
                font-size: 18px;
                font-weight: 750;
                padding-left: 8px;
            }
            """
        )
        total_layout.addWidget(
            self.selected_total_label
        )

        fee_page_layout.addWidget(
            total_frame
        )

        # ---------------------------------------------------------------
        # Fee-record actions
        # ---------------------------------------------------------------

        action_row = QHBoxLayout()
        action_row.setSpacing(
            12
        )

        back_search_action_button = QPushButton(
            "Back to Student Search"
        )
        back_search_action_button.setObjectName(
            "secondaryButton"
        )
        back_search_action_button.clicked.connect(
            self._back_to_student_search
        )
        action_row.addWidget(
            back_search_action_button
        )

        clear_selection_button = QPushButton(
            "Clear Fee Selection"
        )
        clear_selection_button.setObjectName(
            "secondaryButton"
        )
        clear_selection_button.clicked.connect(
            self._clear_fee_selection
        )
        action_row.addWidget(
            clear_selection_button
        )

        action_row.addStretch()

        self.continue_button = QPushButton(
            "Continue to Receipt"
        )
        self.continue_button.setMinimumWidth(
            210
        )
        self.continue_button.clicked.connect(
            self._continue_to_receipt
        )
        action_row.addWidget(
            self.continue_button
        )

        fee_page_layout.addLayout(
            action_row
        )

        # ---------------------------------------------------------------
        # Register both views
        # ---------------------------------------------------------------

        self.page_stack.addWidget(
            self.search_view
        )
        self.page_stack.addWidget(
            self.fee_record_view
        )

        self.reload_class_dropdown()
        self.reset_page()

    # ------------------------------------------------------------------
    # Page setup
    # ------------------------------------------------------------------

    def eventFilter(self, watched, event):
        """Move from the class field to Father CNIC on Enter."""
        if (
            watched is self.class_combo
            and event.type()
            == QEvent.Type.KeyPress
            and event.key()
            in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            )
        ):
            self.father_cnic_input.setFocus()
            return True

        return super().eventFilter(
            watched,
            event,
        )

    def _show_search_view(self):
        """Display the dedicated student-search page."""
        self.page_stack.setCurrentWidget(
            self.search_view
        )

        keep_table_left_aligned(
            self.results_table
        )

    def _show_fee_record_view(self):
        """Display the dedicated selected-student fee page."""
        self.page_stack.setCurrentWidget(
            self.fee_record_view
        )

    def _back_to_student_search(self):
        """
        Return without clearing the search fields or search results.
        Fee checkboxes are cleared for safety.
        """
        self._clear_fee_selection()
        self._show_search_view()

    def reload_class_dropdown(self):
        """Reload all existing classes."""
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
        """Clear the complete Pay Fee page."""
        self.selected_student_id = None
        self.selected_student = None

        self._tracking_id_history_requested = False
        self.full_fee_history_mode = False

        self.tracking_id_input.clear()
        self.receipt_number_input.clear()
        self.admission_number_input.clear()
        self.student_name_input.clear()
        self.father_name_input.clear()
        self.roll_number_input.clear()
        self.father_cnic_input.clear()

        if self.class_combo.count() > 0:
            self.class_combo.setCurrentIndex(0)

        self.results_table.setRowCount(0)
        self.fee_table.setRowCount(0)

        self.fee_rows = []
        self.fee_checkboxes = []

        self.selected_student_label.setText(
            "Selected Student: None"
        )
        self.selected_total_label.setText(
            "Rs. 0.00"
        )

        self.search_results_count_label.setText(
            "0 students"
        )

        self._show_search_view()
        self.tracking_id_input.setFocus()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_students(self):
        receipt_number = (
            self.receipt_number_input.text().strip()
            or None
        )

        tracking_id = (
            self.tracking_id_input.text().strip()
            or None
        )

        admission_number = (
            self.admission_number_input.text().strip().upper()
            or None
        )
        student_name = (
            self.student_name_input.text().strip()
            or None
        )
        father_name = (
            self.father_name_input.text().strip()
            or None
        )
        roll_number = (
            self.roll_number_input.text().strip()
            or None
        )
        if roll_number:
            try:
                student_service.parse_roll_number_search(roll_number)
            except ValueError as error:
                QMessageBox.warning(
                    self,
                    "Invalid Roll Number",
                    str(error),
                )
                self.roll_number_input.setFocus()
                return

        father_cnic = (
            self.father_cnic_input.text().strip()
            or None
        )

        if (
            father_cnic
            and not student_service.validate_cnic(
                father_cnic
            )
        ):
            QMessageBox.warning(
                self,
                "Invalid Father CNIC",
                (
                    "Father CNIC must contain exactly "
                    "13 numeric digits."
                ),
            )
            self.father_cnic_input.setFocus()
            return

        class_id = self.class_combo.currentData()

        has_student_search_criteria = any(
            (
                tracking_id,
                admission_number,
                student_name,
                father_cnic,
                roll_number,
            )
        )

        # The Receipt Number field has its own workflow. Previously the main
        # Search Student button ignored it and displayed the generic
        # "Enter a Tracking ID" warning when Receipt Number was the only
        # entered value. Route that unambiguous receipt-only action to the
        # existing protected receipt lookup instead.
        if receipt_number and not has_student_search_criteria:
            self._open_paid_receipt_by_number()
            return

        try:
            results = fee_service.search_students_for_fee(
                tracking_id=tracking_id,
                admission_number=admission_number,
                student_name=student_name,
                father_name=father_name,
                father_cnic=father_cnic,
                roll_number=roll_number,
                class_id=class_id,
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Search Error",
                str(error),
            )
            return

        # Set the display mode only after the search completes
        # successfully. Tracking ID has priority in Fee Service.
        self._tracking_id_history_requested = bool(
            tracking_id
        )

        self._populate_search_results(
            results
        )

    def _open_paid_receipt_by_number(self):
        """Open one completed paid receipt by its internal Receipt Number."""
        receipt_number = (
            self.receipt_number_input.text().strip()
        )

        if not receipt_number:
            QMessageBox.warning(
                self,
                "Receipt Number Required",
                "Please enter a Receipt Number.",
            )
            self.receipt_number_input.setFocus()
            return False

        receipt_page = self.main_window.pages.get(
            navigation.PAGE_FEE_RECEIPT
        )

        if receipt_page is None or not hasattr(
            receipt_page,
            "load_paid_receipt_by_number",
        ):
            QMessageBox.critical(
                self,
                "Receipt Page Error",
                "The paid receipt page is not available.",
            )
            return False

        self.main_window.pending_fee_payment = None

        if not receipt_page.load_paid_receipt_by_number(
            receipt_number
        ):
            self.receipt_number_input.setFocus()
            return False

        self.main_window.go_to_page(
            navigation.PAGE_FEE_RECEIPT
        )
        return True

    def _populate_search_results(self, results):
        self.results_table.setRowCount(0)
        self._clear_selected_student()

        if not results:
            self.search_results_count_label.setText(
                "0 students"
            )

            QMessageBox.information(
                self,
                "Student Not Found",
                "Student not found.",
            )
            return

        self.results_table.setRowCount(len(results))

        result_word = (
            "student"
            if len(results) == 1
            else "students"
        )

        self.search_results_count_label.setText(
            f"{len(results)} {result_word}"
        )

        for row_index, student_row in enumerate(results):
            display_name = (
                student_service.display_name_with_stars(
                    student_row["student_name"],
                    student_row["failure_count"],
                )
            )

            values = [
                student_row["tracking_id"] or "-",
                student_row["admission_number"],
                student_service.display_roll_number(student_row),
                display_name,
                student_row["father_name"],
                student_row["father_cnic"] or "-",
                student_row["class_name"] or "-",
            ]

            for column_index, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                self.results_table.setItem(
                    row_index,
                    column_index,
                    item,
                )

            select_button = QPushButton("Select")
            configure_table_action_button(
                select_button
            )
            select_button.setObjectName(
                "payFeeSelectButton"
            )
            select_button.setFixedSize(
                112,
                34,
            )
            select_button.clicked.connect(
                lambda _checked=False,
                student_id=student_row["student_id"]:
                self._select_student(student_id)
            )

            action_container = QWidget()
            action_container.setObjectName(
                "tableCellContainer"
            )

            action_layout = QHBoxLayout(
                action_container
            )
            action_layout.setContentsMargins(
                6,
                4,
                6,
                4,
            )
            action_layout.setAlignment(
                Qt.AlignCenter
            )
            action_layout.addWidget(
                select_button
            )

            self.results_table.setCellWidget(
                row_index,
                7,
                action_container,
            )
            self.results_table.setRowHeight(
                row_index,
                46,
            )

        # SEARCH_RESULTS_LEFT_ALIGNMENT
        keep_table_left_aligned(
            self.results_table
        )

    # ------------------------------------------------------------------
    # Selected student and fee loading
    # ------------------------------------------------------------------

    def _select_student(self, student_id):
        student_row = student_service.get_student_by_id(
            student_id
        )

        if not student_row:
            QMessageBox.warning(
                self,
                "Student Not Found",
                "The selected student could not be found.",
            )
            return

        self.selected_student_id = student_id
        self.selected_student = student_row

        # Freeze the last successful search mode for this selected
        # student. refresh_after_payment() then retains the same mode.
        self.full_fee_history_mode = bool(
            self._tracking_id_history_requested
        )

        display_name = (
            student_service.display_name_with_stars(
                student_row["student_name"],
                student_row["failure_count"],
            )
        )

        self.selected_student_label.setText(
            f"Selected Student: "
            f"Tracking ID: {student_row['tracking_id'] or '-'} | "
            f"Admission No: {student_row['admission_number']} | "
            f"Roll No: {student_service.display_roll_number(student_row)} | "
            f"{display_name} | "
            f"Father: {student_row['father_name']} | "
            f"CNIC: {student_row['father_cnic'] or '-'} | "
            f"Class: {student_row['class_name'] or '-'} | "
            f"Status: {student_row['student_status']}"
        )

        self._load_fee_records()

        self._show_fee_record_view()

    def _clear_selected_student(self):
        self.selected_student_id = None
        self.selected_student = None

        self.selected_student_label.setText(
            "Selected Student: None"
        )

        self.fee_table.setRowCount(0)
        self.fee_rows = []
        self.fee_checkboxes = []

        self.selected_total_label.setText(
            "Rs. 0.00"
        )

    def _load_fee_records(self):
        if not self.selected_student_id:
            return

        try:
            records = fee_service.get_fee_table_for_student(
                self.selected_student_id
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Fee Setup Required",
                str(error),
            )

            self.fee_table.setRowCount(
                0
            )

            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Fee Load Error",
                f"Fee records could not be loaded.\n\n{error}",
            )

            self.fee_table.setRowCount(
                0
            )

            return

        def row_value(
            fee_row,
            key,
            default=None,
        ):
            try:
                value = fee_row[key]

            except (
                KeyError,
                IndexError,
                TypeError,
            ):
                return default

            return (
                value
                if value is not None
                else default
            )

        def display_payment_date(
            raw_value,
        ):
            if raw_value is None:
                return "-"

            date_text = str(
                raw_value
            ).strip()

            if not date_text:
                return "-"

            # Database payment timestamps normally begin with YYYY-MM-DD.
            # The table displays dates in DD-MM-YYYY format.
            if (
                len(date_text) >= 10
                and date_text[4] == "-"
                and date_text[7] == "-"
                and date_text[:4].isdigit()
                and date_text[5:7].isdigit()
                and date_text[8:10].isdigit()
            ):
                return (
                    f"{date_text[8:10]}-"
                    f"{date_text[5:7]}-"
                    f"{date_text[:4]}"
                )

            return date_text

        all_fee_rows = list(
            records
        )

        if self.full_fee_history_mode:
            # Tracking ID search:
            # show every Paid and Unpaid record through the
            # end of the current academic session.
            self.fee_rows = all_fee_rows

        else:
            # Every other search:
            # preserve the original compact display containing
            # all Unpaid records and only the latest Paid record.
            last_paid_index = None

            for index, fee_row in enumerate(
                all_fee_rows
            ):
                if fee_row["status"] == "Paid":
                    last_paid_index = index

            self.fee_rows = [
                fee_row
                for index, fee_row in enumerate(
                    all_fee_rows
                )
                if (
                    fee_row["status"] == "Unpaid"
                    or index == last_paid_index
                )
            ]

        self.fee_checkboxes = []

        self.fee_table.clearContents()

        self.fee_table.setRowCount(
            len(self.fee_rows)
        )

        first_unpaid_found = False

        for row_index, fee_row in enumerate(
            self.fee_rows
        ):
            month_name = row_value(
                fee_row,
                "month_name",
                "-",
            )

            fee_year = row_value(
                fee_row,
                "year",
                "",
            )

            item_name = (
                f"{month_name} {fee_year}"
            ).strip()

            class_name = row_value(
                fee_row,
                "historical_class_name",
            )

            if not class_name:
                class_row = (
                    class_service.get_class_by_id(
                        row_value(
                            fee_row,
                            "class_id",
                        )
                    )
                )

                class_name = (
                    class_row["class_name"]
                    if class_row
                    else "-"
                )

            fee_status = str(
                row_value(
                    fee_row,
                    "status",
                    "-",
                )
            )

            if fee_status == "Paid":
                payment_date = (
                    display_payment_date(
                        row_value(
                            fee_row,
                            "payment_date",
                        )
                    )
                )

                receipt_number = str(
                    row_value(
                        fee_row,
                        "receipt_no",
                        "-",
                    )
                    or "-"
                )

            else:
                payment_date = "-"
                receipt_number = "-"

            values = [
                item_name,
                class_name,
                row_value(
                    fee_row,
                    "session_year",
                    "-",
                ),
                (
                    f"Rs. "
                    f"{float(row_value(fee_row, 'amount', 0.0)):,.2f}"
                ),
                fee_status,
                payment_date,
                receipt_number,
            ]

            for column_index, value in enumerate(
                values
            ):
                table_item = QTableWidgetItem(
                    str(value)
                )

                table_item.setTextAlignment(
                    Qt.AlignCenter
                )

                self.fee_table.setItem(
                    row_index,
                    column_index,
                    table_item,
                )

            checkbox = QCheckBox()

            checkbox.setProperty(
                "record_id",
                row_value(
                    fee_row,
                    "id",
                ),
            )

            checkbox.setProperty(
                "fee_amount",
                float(
                    row_value(
                        fee_row,
                        "amount",
                        0.0,
                    )
                ),
            )

            if fee_status != "Unpaid":
                checkbox.setEnabled(
                    False
                )

            elif not first_unpaid_found:
                checkbox.setEnabled(
                    True
                )

                first_unpaid_found = True

            else:
                checkbox.setEnabled(
                    False
                )

            checkbox.stateChanged.connect(
                lambda state,
                index=row_index:
                self._fee_checkbox_changed(
                    index,
                    state,
                )
            )

            select_background_item = (
                QTableWidgetItem("")
            )

            select_background_item.setFlags(
                Qt.ItemFlag.NoItemFlags
            )

            self.fee_table.setItem(
                row_index,
                7,
                select_background_item,
            )

            checkbox_container = QWidget()

            checkbox_container.setObjectName(
                "tableCellContainer"
            )

            checkbox_layout = QHBoxLayout(
                checkbox_container
            )

            checkbox_layout.setContentsMargins(
                0,
                0,
                0,
                0,
            )

            checkbox_layout.setAlignment(
                Qt.AlignCenter
            )

            checkbox_layout.addWidget(
                checkbox
            )

            self.fee_table.setCellWidget(
                row_index,
                7,
                checkbox_container,
            )

            self.fee_checkboxes.append(
                checkbox
            )

        self._update_selected_total()

    def _set_fee_row_selected_style(
        self,
        row_index,
        selected,
    ):
        """
        Paint the complete visible fee row with one continuous colour.
        """
        if (
            row_index < 0
            or row_index >= self.fee_table.rowCount()
        ):
            return

        if selected:
            row_colour = "#E2F4E6"
            text_colour = "#17351F"

        elif row_index % 2 == 0:
            row_colour = "#FFFFFF"
            text_colour = "#17233D"

        else:
            row_colour = "#F7F9FC"
            text_colour = "#17233D"

        # Columns 0 through 6 contain visible fee-history text.
        for column_index in range(
            0,
            7,
        ):
            cell_widget = self.fee_table.cellWidget(
                row_index,
                column_index,
            )

            table_item = self.fee_table.item(
                row_index,
                column_index,
            )

            if cell_widget is None:
                cell_widget = QLabel(
                    table_item.text()
                    if table_item is not None
                    else ""
                )

                cell_widget.setAlignment(
                    Qt.AlignmentFlag.AlignCenter
                )

                cell_widget.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                    True,
                )

                self.fee_table.setCellWidget(
                    row_index,
                    column_index,
                    cell_widget,
                )

            cell_widget.setContentsMargins(
                0,
                0,
                0,
                0,
            )

            cell_widget.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {row_colour};
                    color: {text_colour};
                    border: none;
                    margin: 0px;
                    padding: 0px;
                }}
                """
            )

        checkbox_container = self.fee_table.cellWidget(
            row_index,
            7,
        )

        if checkbox_container is not None:
            checkbox_container.setContentsMargins(
                0,
                0,
                0,
                0,
            )

            checkbox_container.setStyleSheet(
                f"""
                QWidget#tableCellContainer {{
                    background-color: {row_colour};
                    border: none;
                    margin: 0px;
                    padding: 0px;
                }}
                """
            )

        self.fee_table.viewport().update()

    # ------------------------------------------------------------------
    # Sequential checkbox control
    # ------------------------------------------------------------------

    def _fee_checkbox_changed(self, changed_index, state):
        checkbox = self.fee_checkboxes[changed_index]

        if not checkbox.isEnabled():
            return

        if state == Qt.Checked.value:
            self._enable_next_unpaid_checkbox(
                changed_index
            )
        else:
            self._clear_later_checkboxes(
                changed_index
            )

        self._set_fee_row_selected_style(
            changed_index,
            state == Qt.Checked.value,
        )

        self._update_selected_total()

    def _enable_next_unpaid_checkbox(self, current_index):
        for next_index in range(
            current_index + 1,
            len(self.fee_rows),
        ):
            fee_row = self.fee_rows[next_index]
            next_checkbox = self.fee_checkboxes[
                next_index
            ]

            if fee_row["status"] != "Unpaid":
                continue

            next_checkbox.setEnabled(True)
            break

    def _clear_later_checkboxes(self, current_index):
        for later_index in range(
            current_index + 1,
            len(self.fee_checkboxes),
        ):
            later_checkbox = self.fee_checkboxes[
                later_index
            ]

            later_checkbox.blockSignals(True)
            later_checkbox.setChecked(False)
            later_checkbox.blockSignals(False)

            self._set_fee_row_selected_style(
                later_index,
                False,
            )

            if (
                self.fee_rows[later_index]["status"]
                == "Unpaid"
            ):
                later_checkbox.setEnabled(False)

    def _clear_fee_selection(self):
        first_unpaid_found = False

        for row_index, fee_row in enumerate(self.fee_rows):
            checkbox = self.fee_checkboxes[row_index]

            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)

            self._set_fee_row_selected_style(
                row_index,
                False,
            )

            if fee_row["status"] != "Unpaid":
                checkbox.setEnabled(False)
            elif not first_unpaid_found:
                checkbox.setEnabled(True)
                first_unpaid_found = True
            else:
                checkbox.setEnabled(False)

        self._update_selected_total()

    def _selected_record_ids(self):
        selected_ids = []

        for checkbox in self.fee_checkboxes:
            if checkbox.isChecked():
                selected_ids.append(
                    checkbox.property("record_id")
                )

        return selected_ids

    def _update_selected_total(self):
        total = 0.0

        for checkbox in self.fee_checkboxes:
            if checkbox.isChecked():
                total += float(
                    checkbox.property("fee_amount")
                )

        self.selected_total_label.setText(
            f"Rs. {total:,.2f}"
        )

    # ------------------------------------------------------------------
    # Continue to receipt
    # ------------------------------------------------------------------

    def refresh_after_payment(
        self,
        student_id,
    ):
        """
        Reload the selected student's fee records immediately after
        a payment is confirmed.

        The selected student remains open on the Fee Record view.
        """
        if student_id is None:
            return False

        try:
            student_id = int(
                student_id
            )

        except (
            TypeError,
            ValueError,
        ):
            return False

        student_row = (
            student_service.get_student_by_id(
                student_id
            )
        )

        if not student_row:
            self._clear_selected_student()
            self._show_search_view()
            return False

        self.selected_student_id = (
            student_id
        )
        self.selected_student = (
            student_row
        )

        display_name = (
            student_service.display_name_with_stars(
                student_row["student_name"],
                student_row["failure_count"],
            )
        )

        self.selected_student_label.setText(
            f"Selected Student: "
            f"Tracking ID: {student_row['tracking_id'] or '-'} | "
            f"Admission No: {student_row['admission_number']} | "
            f"Roll No: {student_service.display_roll_number(student_row)} | "
            f"{display_name} | "
            f"Father: {student_row['father_name']} | "
            f"CNIC: {student_row['father_cnic'] or '-'} | "
            f"Class: {student_row['class_name'] or '-'} | "
            f"Status: {student_row['student_status']}"
        )

        # Clear all stale widgets and cached checkbox references
        # before loading authoritative records from the database.
        self.fee_table.clearContents()
        self.fee_table.setRowCount(
            0
        )

        self.fee_rows = []
        self.fee_checkboxes = []

        self.selected_total_label.setText(
            "Rs. 0.00"
        )

        self._load_fee_records()
        self._show_fee_record_view()

        return True

    def _continue_to_receipt(self):
        if not self.selected_student_id:
            QMessageBox.warning(
                self,
                "No Student Selected",
                "Please search and select a student first.",
            )
            return

        selected_ids = self._selected_record_ids()

        if not selected_ids:
            QMessageBox.warning(
                self,
                "No Fee Selected",
                "Please select at least one unpaid fee item.",
            )
            return

        if not fee_service.validate_payment_selection(
            self.selected_student_id,
            selected_ids,
        ):
            QMessageBox.warning(
                self,
                "Invalid Fee Selection",
                "Previous fee must be paid first.",
            )
            return

        self.main_window.pending_fee_payment = {
            "student_id": self.selected_student_id,
            "selected_record_ids": selected_ids,
        }

        receipt_page = self.main_window.pages[
            navigation.PAGE_FEE_RECEIPT
        ]

        if receipt_page.load_pending_payment():
            self.main_window.go_to_page(
                navigation.PAGE_FEE_RECEIPT
            )