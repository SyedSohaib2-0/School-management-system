"""
Search Expense screen.

Search options:
1. Exact calendar date
2. One month within an April-to-March school session
3. Complete April-to-March school session

Includes paginated display, print preview, printing, and PDF export.
"""

import calendar

from datetime import datetime
from html import escape

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QTextDocument, QPageLayout
from PySide6.QtPrintSupport import (
    QPrinter,
    QPrintDialog,
    QPrintPreviewDialog,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFrame,
    QLabel,
    QPushButton,
    QComboBox,
    QDateEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFileDialog,
    QDialog,
    QStackedWidget,
    QSizePolicy,
)

from app import navigation
from app.services import expense_service


EXPENSE_DISPLAY_PAGE_SIZE = 100


class SearchExpensePage(QWidget):
    """Search and display saved school expenses."""

    SEARCH_EXACT_DATE = "exact_date"
    SEARCH_MONTH = "month"
    SEARCH_SESSION = "session"

    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window
        self.current_rows = []
        self.current_report_title = ""
        self.current_page = 1

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 22, 28, 24)
        main_layout.setSpacing(12)

        # ---------------------------------------------------------------
        # Page heading shared by search and results views
        # ---------------------------------------------------------------

        title_label = QLabel("Search Expense")
        title_label.setObjectName("pageTitle")
        main_layout.addWidget(title_label)

        instruction_label = QLabel(
            "Search expenses by exact date, one month, "
            "or a complete April-to-March school session."
        )
        instruction_label.setWordWrap(True)
        main_layout.addWidget(instruction_label)

        # Keep search credentials and search results on two independent
        # internal pages. This prevents the results table from compressing
        # the search controls and keeps the first view focused on criteria.
        self.content_stack = QStackedWidget()
        self.content_stack.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
        main_layout.addWidget(self.content_stack, 1)

        # ---------------------------------------------------------------
        # Search credentials page
        # ---------------------------------------------------------------

        self.search_view = QWidget()
        search_view_layout = QVBoxLayout(self.search_view)
        search_view_layout.setContentsMargins(0, 0, 0, 0)
        search_view_layout.setSpacing(14)

        search_card = QFrame()
        search_card.setObjectName("expenseSearchCard")
        search_card.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Maximum,
        )
        search_card.setStyleSheet(
            """
            QFrame#expenseSearchCard {
                background-color: white;
                border: 1px solid #DCE2EC;
                border-radius: 10px;
            }
            QFrame#expenseSearchCard QLabel#expenseFilterLabel {
                color: #4A5870;
                font-size: 12px;
                font-weight: 600;
                padding: 0px;
                border: none;
                background: transparent;
            }
            QFrame#expenseSearchCard QLabel#expenseSessionInfo {
                color: #29446F;
                font-weight: 600;
                padding: 7px 10px;
                background-color: #F1F5FC;
                border: 1px solid #DCE5F4;
                border-radius: 6px;
            }
            QFrame#expenseSearchCard QComboBox,
            QFrame#expenseSearchCard QDateEdit,
            QFrame#expenseSearchCard QSpinBox {
                min-height: 42px;
                max-height: 42px;
                background-color: white;
                border: 1px solid #C7D0DF;
                border-radius: 6px;
                padding: 0px 12px;
                selection-background-color: #DCE6FF;
            }
            QFrame#expenseSearchCard QComboBox:focus,
            QFrame#expenseSearchCard QDateEdit:focus,
            QFrame#expenseSearchCard QSpinBox:focus {
                border: 2px solid #3F5BE0;
            }
            """
        )

        search_card_layout = QVBoxLayout(search_card)
        search_card_layout.setContentsMargins(18, 16, 18, 16)
        search_card_layout.setSpacing(14)

        self.filters_row = QHBoxLayout()
        self.filters_row.setContentsMargins(0, 0, 0, 0)
        self.filters_row.setSpacing(14)
        self.filters_row.setAlignment(Qt.AlignTop)

        def build_filter_field(label_text, editor, minimum_width):
            field = QWidget()
            field.setMinimumWidth(minimum_width)
            field.setSizePolicy(
                QSizePolicy.Expanding,
                QSizePolicy.Fixed,
            )
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(6)

            label = QLabel(label_text)
            label.setObjectName("expenseFilterLabel")
            label.setMinimumHeight(20)

            editor.setMinimumHeight(42)
            editor.setMaximumHeight(42)

            field_layout.addWidget(label)
            field_layout.addWidget(editor)
            return field, label

        self.search_type_combo = QComboBox()
        self.search_type_combo.addItem("Exact Date", self.SEARCH_EXACT_DATE)
        self.search_type_combo.addItem("Month", self.SEARCH_MONTH)
        self.search_type_combo.addItem(
            "Complete Session",
            self.SEARCH_SESSION,
        )
        self.search_type_combo.currentIndexChanged.connect(
            self._update_search_controls
        )
        (
            self.search_type_field,
            self.search_type_label,
        ) = build_filter_field(
            "Search Type",
            self.search_type_combo,
            280,
        )

        self.exact_date_input = QDateEdit()
        self.exact_date_input.setCalendarPopup(True)
        self.exact_date_input.setDisplayFormat("dd-MM-yyyy")
        self.exact_date_input.setDate(QDate.currentDate())
        (
            self.exact_date_field,
            self.exact_date_label,
        ) = build_filter_field(
            "Expense Date",
            self.exact_date_input,
            220,
        )

        self.session_year_input = QSpinBox()
        self.session_year_input.setRange(2000, 2200)
        self.session_year_input.setButtonSymbols(QSpinBox.NoButtons)
        self.session_year_input.setAlignment(Qt.AlignCenter)
        (
            self.session_year_field,
            self.session_year_label,
        ) = build_filter_field(
            "Session Starting Year",
            self.session_year_input,
            220,
        )

        self.month_input = QComboBox()
        session_months = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
        for month_number in session_months:
            self.month_input.addItem(
                calendar.month_name[month_number],
                month_number,
            )
        (
            self.month_field,
            self.month_label,
        ) = build_filter_field(
            "Month",
            self.month_input,
            200,
        )

        self.filters_row.addWidget(self.search_type_field, 2)
        self.filters_row.addWidget(self.exact_date_field, 1)
        self.filters_row.addWidget(self.session_year_field, 1)
        self.filters_row.addWidget(self.month_field, 1)
        search_card_layout.addLayout(self.filters_row)

        self.session_information_label = QLabel()
        self.session_information_label.setObjectName("expenseSessionInfo")
        self.session_information_label.setWordWrap(True)
        self.session_information_label.setMinimumHeight(38)
        self.session_information_label.setAlignment(
            Qt.AlignLeft | Qt.AlignVCenter
        )
        search_card_layout.addWidget(self.session_information_label)

        self.session_year_input.valueChanged.connect(
            self._update_session_information
        )

        search_button_row = QHBoxLayout()
        search_button_row.setSpacing(10)

        search_button = QPushButton("Search Expenses")
        search_button.setMinimumWidth(160)
        search_button.clicked.connect(self._search_expenses)
        search_button_row.addWidget(search_button)

        clear_button = QPushButton("Clear Search")
        clear_button.setObjectName("secondaryButton")
        clear_button.setMinimumWidth(135)
        clear_button.clicked.connect(self._clear_search_fields)
        search_button_row.addWidget(clear_button)
        search_button_row.addStretch()
        search_card_layout.addLayout(search_button_row)

        search_view_layout.addWidget(search_card)

        search_back_button = QPushButton("Back to Expenses Management")
        search_back_button.setObjectName("secondaryButton")
        search_back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_EXPENSE_DASHBOARD
            )
        )
        search_view_layout.addWidget(search_back_button)
        search_view_layout.addStretch(1)

        self.content_stack.addWidget(self.search_view)

        # ---------------------------------------------------------------
        # Search results page
        # ---------------------------------------------------------------

        self.results_view = QWidget()
        results_layout = QVBoxLayout(self.results_view)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(12)

        self.result_heading_label = QLabel("No search performed.")
        self.result_heading_label.setStyleSheet(
            "font-size: 15px; font-weight: bold;"
        )
        results_layout.addWidget(self.result_heading_label)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(25)

        self.record_count_label = QLabel("Expense Records: 0")
        self.record_count_label.setStyleSheet("font-weight: bold;")
        summary_row.addWidget(self.record_count_label)

        self.total_expense_label = QLabel("Total Expense: Rs. 0.00")
        self.total_expense_label.setStyleSheet("font-weight: bold;")
        summary_row.addWidget(self.total_expense_label)
        summary_row.addStretch()
        results_layout.addLayout(summary_row)

        paging_row = QHBoxLayout()
        paging_row.setSpacing(8)

        self.previous_page_button = QPushButton("Previous")
        self.previous_page_button.setObjectName("secondaryButton")
        self.previous_page_button.clicked.connect(self._show_previous_page)
        self.previous_page_button.setEnabled(False)
        paging_row.addWidget(self.previous_page_button)

        self.next_page_button = QPushButton("Next")
        self.next_page_button.setObjectName("secondaryButton")
        self.next_page_button.clicked.connect(self._show_next_page)
        self.next_page_button.setEnabled(False)
        paging_row.addWidget(self.next_page_button)

        self.page_label = QLabel("Showing 0 records")
        self.page_label.setStyleSheet("font-weight: bold;")
        paging_row.addWidget(self.page_label)
        paging_row.addStretch()
        paging_row.addWidget(
            QLabel(
                f"Display: {EXPENSE_DISPLAY_PAGE_SIZE} records per page"
            )
        )
        results_layout.addLayout(paging_row)

        self.expense_table = QTableWidget()
        self.expense_table.setColumnCount(5)
        self.expense_table.setHorizontalHeaderLabels(
            [
                "Record No.",
                "Expense Date",
                "Description",
                "Amount",
                "Session",
            ]
        )
        self.expense_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.expense_table.verticalHeader().setVisible(False)
        self.expense_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.expense_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.expense_table.setAlternatingRowColors(True)
        self.expense_table.setMinimumHeight(250)
        results_layout.addWidget(self.expense_table, 1)

        print_row = QHBoxLayout()
        print_row.setSpacing(12)

        self.view_button = QPushButton("View Expense Report")
        self.view_button.setEnabled(False)
        self.view_button.clicked.connect(self._view_expense_report)
        print_row.addWidget(self.view_button)

        self.print_button = QPushButton("Print Expense Report")
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self._print_expense_report)
        print_row.addWidget(self.print_button)

        self.pdf_button = QPushButton("Download Expense Report PDF")
        self.pdf_button.setEnabled(False)
        self.pdf_button.clicked.connect(self._download_expense_report_pdf)
        print_row.addWidget(self.pdf_button)
        print_row.addStretch()
        results_layout.addLayout(print_row)

        results_navigation_row = QHBoxLayout()
        results_navigation_row.setSpacing(10)

        back_to_search_button = QPushButton("Back to Search Expense")
        back_to_search_button.setObjectName("secondaryButton")
        back_to_search_button.clicked.connect(self._show_search_page)
        results_navigation_row.addWidget(back_to_search_button)

        results_dashboard_button = QPushButton(
            "Back to Expenses Management"
        )
        results_dashboard_button.setObjectName("secondaryButton")
        results_dashboard_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_EXPENSE_DASHBOARD
            )
        )
        results_navigation_row.addWidget(results_dashboard_button)
        results_navigation_row.addStretch()
        results_layout.addLayout(results_navigation_row)

        self.content_stack.addWidget(self.results_view)
        self.reset_page()

    # ------------------------------------------------------------------
    # Page reset
    # ------------------------------------------------------------------

    def reset_page(self):
        """Reset search fields, results, and return to credentials view."""
        today = QDate.currentDate()

        session_start_year = (
            expense_service.get_expense_session_start_year(
                today.month(),
                today.year(),
            )
        )

        self.search_type_combo.setCurrentIndex(0)
        self.exact_date_input.setDate(today)
        self.session_year_input.setValue(session_start_year)

        current_month_index = self.month_input.findData(today.month())
        if current_month_index >= 0:
            self.month_input.setCurrentIndex(current_month_index)

        self._update_session_information()
        self._update_search_controls()
        self.clear_results()
        self._show_search_page()

    def _clear_search_fields(self):
        """Restore current-date search defaults without leaving search view."""
        self.reset_page()

    def _show_search_page(self):
        """Show only the expense search credentials."""
        self.content_stack.setCurrentWidget(self.search_view)

    def _show_results_page(self):
        """Show the result table for the completed valid search."""
        self.content_stack.setCurrentWidget(self.results_view)

    def clear_results(self):
        """Clear the expense table and report summary."""
        self.current_rows = []
        self.current_report_title = ""
        self.current_page = 1

        self.expense_table.setRowCount(0)
        self.previous_page_button.setEnabled(False)
        self.next_page_button.setEnabled(False)
        self.page_label.setText("Showing 0 records")

        self.result_heading_label.setText("No search performed.")
        self.record_count_label.setText("Expense Records: 0")
        self.total_expense_label.setText("Total Expense: Rs. 0.00")

        self.view_button.setEnabled(False)
        self.print_button.setEnabled(False)
        self.pdf_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Search control visibility
    # ------------------------------------------------------------------

    def _update_search_controls(self):
        """Show only fields required by the selected search type."""
        search_type = (
            self.search_type_combo.currentData()
        )

        exact_date_required = (
            search_type
            == self.SEARCH_EXACT_DATE
        )

        month_required = (
            search_type
            == self.SEARCH_MONTH
        )

        session_required = (
            search_type
            in (
                self.SEARCH_MONTH,
                self.SEARCH_SESSION,
            )
        )

        self.exact_date_field.setVisible(exact_date_required)
        self.session_year_field.setVisible(session_required)
        self.month_field.setVisible(month_required)
        self.session_information_label.setVisible(session_required)

        # Force a fresh geometry calculation after changing modes. This is
        # important on Windows when display scaling changes at runtime.
        self.search_type_field.updateGeometry()
        self.exact_date_field.updateGeometry()
        self.session_year_field.updateGeometry()
        self.month_field.updateGeometry()
        self.session_information_label.updateGeometry()

    def _update_session_information(self):
        """Show the complete April-to-March session range."""
        start_year = (
            self.session_year_input.value()
        )
        end_year = start_year + 1

        self.session_information_label.setText(
            f"Selected session: {start_year}-{end_year} "
            f"(April {start_year} to March {end_year})"
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _search_expenses(self):
        """Run the selected expense search."""
        search_type = (
            self.search_type_combo.currentData()
        )

        try:
            if search_type == self.SEARCH_EXACT_DATE:
                selected_date = (
                    self.exact_date_input.date()
                )

                result = (
                    expense_service
                    .search_expenses_by_exact_date(
                        calendar_year=selected_date.year(),
                        month_number=selected_date.month(),
                        day_number=selected_date.day(),
                    )
                )

                report_title = (
                    "Expenses for "
                    f"{selected_date.toString('dd-MM-yyyy')}"
                )

            elif search_type == self.SEARCH_MONTH:
                session_start_year = (
                    self.session_year_input.value()
                )
                month_number = (
                    self.month_input.currentData()
                )
                month_name = calendar.month_name[
                    month_number
                ]

                result = (
                    expense_service
                    .search_expenses_by_month(
                        month_number=month_number,
                        session_start_year=session_start_year,
                    )
                )

                report_title = (
                    f"{month_name} Expenses — "
                    f"Session {session_start_year}-"
                    f"{session_start_year + 1}"
                )

            else:
                session_start_year = (
                    self.session_year_input.value()
                )

                result = (
                    expense_service
                    .search_expenses_by_session_year(
                        session_start_year
                    )
                )

                report_title = (
                    "Complete Expense Report — "
                    f"Session {session_start_year}-"
                    f"{session_start_year + 1}"
                )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Search",
                str(error),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Expense Search Error",
                "Expenses could not be searched."
                f"\n\n{error}",
            )
            return

        self.current_rows = result["rows"]
        self.current_report_title = report_title
        self.current_page = 1

        self._show_results_page()
        self._populate_results(result)

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    def _populate_results(self, result):
        """Display expense rows and totals."""
        rows = result["rows"]
        total_amount = result["total_amount"]

        total_rows = len(rows)
        total_pages = max(
            1,
            (total_rows + EXPENSE_DISPLAY_PAGE_SIZE - 1)
            // EXPENSE_DISPLAY_PAGE_SIZE,
        )
        self.current_page = max(1, min(self.current_page, total_pages))
        start_index = (self.current_page - 1) * EXPENSE_DISPLAY_PAGE_SIZE
        end_index = min(start_index + EXPENSE_DISPLAY_PAGE_SIZE, total_rows)
        page_rows = rows[start_index:end_index]

        self.expense_table.setRowCount(len(page_rows))
        self.previous_page_button.setEnabled(self.current_page > 1)
        self.next_page_button.setEnabled(self.current_page < total_pages)
        if total_rows:
            self.page_label.setText(
                f"Showing {start_index + 1}-{end_index} of {total_rows} records "
                f"(Page {self.current_page} of {total_pages})"
            )
        else:
            self.page_label.setText("Showing 0 records")

        for row_index, expense_row in enumerate(
            page_rows
        ):
            stored_date = str(
                expense_row["expense_date"]
            )

            date_parts = stored_date.split("-")

            if len(date_parts) == 3:
                formatted_date = (
                    f"{date_parts[2]}-"
                    f"{date_parts[1]}-"
                    f"{date_parts[0]}"
                )
            else:
                formatted_date = stored_date

            values = [
                expense_row["id"],
                formatted_date,
                expense_row["description"],
                f"Rs. {float(expense_row['amount']):,.2f}",
                expense_row["session_year"],
            ]

            for column_index, value in enumerate(
                values
            ):
                item = QTableWidgetItem(
                    str(value)
                )

                if column_index in (
                    0,
                    1,
                    3,
                    4,
                ):
                    item.setTextAlignment(
                        Qt.AlignCenter
                    )

                self.expense_table.setItem(
                    row_index,
                    column_index,
                    item,
                )

        self.result_heading_label.setText(
            self.current_report_title
        )
        self.record_count_label.setText(
            f"Expense Records: {len(rows)}"
        )
        self.total_expense_label.setText(
            f"Total Expense: "
            f"Rs. {total_amount:,.2f}"
        )

        has_records = bool(rows)

        self.view_button.setEnabled(
            has_records
        )
        self.print_button.setEnabled(
            has_records
        )
        self.pdf_button.setEnabled(
            has_records
        )

        if not rows:
            QMessageBox.information(
                self,
                "No Expenses Found",
                "No expense records were found "
                "for the selected search.",
            )
    def _show_previous_page(self):
        if self.current_page <= 1:
            return
        self.current_page -= 1
        self._populate_results(
            {
                "rows": self.current_rows,
                "total_amount": sum(
                    float(row["amount"]) for row in self.current_rows
                ),
            }
        )

    def _show_next_page(self):
        total_pages = max(
            1,
            (len(self.current_rows) + EXPENSE_DISPLAY_PAGE_SIZE - 1)
            // EXPENSE_DISPLAY_PAGE_SIZE,
        )
        if self.current_page >= total_pages:
            return
        self.current_page += 1
        self._populate_results(
            {
                "rows": self.current_rows,
                "total_amount": sum(
                    float(row["amount"]) for row in self.current_rows
                ),
            }
        )

    # ------------------------------------------------------------------
    # Expense report
    # ------------------------------------------------------------------

    def _build_expense_report_html(self):
        """Build the printable expense report."""
        generated_at = datetime.now().strftime(
            "%d-%m-%Y %I:%M %p"
        )

        total_amount = sum(
            float(row["amount"])
            for row in self.current_rows
        )

        report_rows_html = ""

        for serial_number, expense_row in enumerate(
            self.current_rows,
            start=1,
        ):
            stored_date = str(
                expense_row["expense_date"]
            )

            date_parts = stored_date.split("-")

            if len(date_parts) == 3:
                formatted_date = (
                    f"{date_parts[2]}-"
                    f"{date_parts[1]}-"
                    f"{date_parts[0]}"
                )
            else:
                formatted_date = stored_date

            description = escape(
                str(expense_row["description"])
            )

            amount = float(
                expense_row["amount"]
            )

            session_year = escape(
                str(expense_row["session_year"])
            )

            report_rows_html += f"""
                <tr>
                    <td class="center">
                        {serial_number}
                    </td>

                    <td class="center">
                        {escape(formatted_date)}
                    </td>

                    <td class="description">
                        {description}
                    </td>

                    <td class="amount">
                        Rs. {amount:,.2f}
                    </td>

                    <td class="center">
                        {session_year}
                    </td>
                </tr>
            """

        return f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: 9pt;
                    color: #000000;
                    margin: 0;
                    padding: 0;
                }}

                .school-name {{
                    text-align: center;
                    font-size: 19pt;
                    font-weight: bold;
                    margin-bottom: 3px;
                }}

                .report-title {{
                    text-align: center;
                    font-size: 14pt;
                    font-weight: bold;
                    margin-bottom: 3px;
                }}

                .generated-date {{
                    text-align: center;
                    font-size: 8.5pt;
                    margin-bottom: 12px;
                }}

                .summary-table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 10px;
                }}

                .summary-table td {{
                    border: none;
                    font-size: 10pt;
                    font-weight: bold;
                    padding: 3px;
                }}

                .expense-table {{
                    width: 100%;
                    border-collapse: collapse;
                }}

                .expense-table th {{
                    border: 1px solid #555555;
                    background-color: #e7e9ee;
                    padding: 6px;
                    text-align: center;
                    font-weight: bold;
                }}

                .expense-table td {{
                    border: 1px solid #555555;
                    padding: 6px;
                }}

                .center {{
                    text-align: center;
                }}

                .description {{
                    text-align: left;
                }}

                .amount {{
                    text-align: right;
                    white-space: nowrap;
                }}

                .total-row td {{
                    font-weight: bold;
                    background-color: #eeeeee;
                }}

                .signatures {{
                    width: 100%;
                    margin-top: 35px;
                    border-collapse: collapse;
                }}

                .signatures td {{
                    border: none;
                    padding-top: 15px;
                    width: 50%;
                }}

                .right-signature {{
                    text-align: right;
                }}
            </style>
        </head>

        <body>
            <div class="school-name">
                Hira Model School
            </div>

            <div class="report-title">
                {escape(self.current_report_title)}
            </div>

            <div class="generated-date">
                Generated: {generated_at}
            </div>

            <table class="summary-table">
                <tr>
                    <td>
                        Expense Records:
                        {len(self.current_rows)}
                    </td>

                    <td style="text-align: right;">
                        Total Expense:
                        Rs. {total_amount:,.2f}
                    </td>
                </tr>
            </table>

            <table class="expense-table">
                <tr>
                    <th style="width: 7%;">
                        No.
                    </th>

                    <th style="width: 15%;">
                        Expense Date
                    </th>

                    <th style="width: 48%;">
                        Description
                    </th>

                    <th style="width: 17%;">
                        Amount
                    </th>

                    <th style="width: 13%;">
                        Session
                    </th>
                </tr>

                {report_rows_html}

                <tr class="total-row">
                    <td colspan="3">
                        Total
                    </td>

                    <td class="amount">
                        Rs. {total_amount:,.2f}
                    </td>

                    <td></td>
                </tr>
            </table>

            <table class="signatures">
                <tr>
                    <td>
                        ___________________________
                        <br>
                        Prepared By
                    </td>

                    <td class="right-signature">
                        ___________________________
                        <br>
                        Principal/Administrator
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """

    def _create_expense_report_document(self):
        """Create a QTextDocument containing the expense report."""
        document = QTextDocument()

        document.setHtml(
            self._build_expense_report_html()
        )

        return document

    def _configure_report_printer(self, printer):
        """Configure the expense report for landscape printing."""
        printer.setPageOrientation(
            QPageLayout.Orientation.Landscape
        )

    def _view_expense_report(self):
        """Open the expense report in print-preview mode."""
        if not self.current_rows:
            QMessageBox.warning(
                self,
                "No Expense Records",
                "Search for expense records before viewing the report.",
            )
            return

        printer = QPrinter(
            QPrinter.PrinterMode.HighResolution
        )

        self._configure_report_printer(
            printer
        )

        document = (
            self._create_expense_report_document()
        )

        preview_dialog = QPrintPreviewDialog(
            printer,
            self,
        )

        preview_dialog.setWindowTitle(
            "View Expense Report"
        )

        preview_dialog.paintRequested.connect(
            lambda preview_printer: document.print_(
                preview_printer
            )
        )

        preview_dialog.exec()

    def _print_expense_report(self):
        """Open the Windows print dialog and print the report."""
        if not self.current_rows:
            QMessageBox.warning(
                self,
                "No Expense Records",
                "Search for expense records before printing.",
            )
            return

        printer = QPrinter(
            QPrinter.PrinterMode.HighResolution
        )

        self._configure_report_printer(
            printer
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )

        print_dialog.setWindowTitle(
            "Print Expense Report"
        )

        if (
            print_dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        document = (
            self._create_expense_report_document()
        )

        document.print_(printer)

    def _download_expense_report_pdf(self):
        """Save the current expense report as a PDF file."""
        if not self.current_rows:
            QMessageBox.warning(
                self,
                "No Expense Records",
                "Search for expense records before downloading the PDF.",
            )
            return

        suggested_file_name = (
            "Hira_Model_School_Expense_Report.pdf"
        )

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Expense Report PDF",
            suggested_file_name,
            "PDF Files (*.pdf)",
        )

        if not save_path:
            return

        if not save_path.lower().endswith(
            ".pdf"
        ):
            save_path += ".pdf"

        try:
            printer = QPrinter(
                QPrinter.PrinterMode.HighResolution
            )

            printer.setOutputFormat(
                QPrinter.OutputFormat.PdfFormat
            )

            printer.setOutputFileName(
                save_path
            )

            self._configure_report_printer(
                printer
            )

            document = (
                self._create_expense_report_document()
            )

            document.print_(printer)

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Save Error",
                "The expense report PDF could not be saved."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "PDF Saved",
            "The expense report PDF was saved successfully.",
        )