"""
Real-Time Finance screen.

Shows financial activity for one exact date.

Default:
- Today's date
- Today's collected fee
- Today's expenses
- Today's final income

Final Income = Total Fee Collected - Total Expenses
"""

from datetime import datetime
from html import escape

from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import (
    QTextDocument,
    QPageLayout,
)
from PySide6.QtPrintSupport import (
    QPrinter,
    QPrintDialog,
    QPrintPreviewDialog,
)

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QDateEdit,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFileDialog,
    QDialog,
)

from app import navigation
from app.services import real_time_finance_service


REAL_TIME_FINANCE_CLEAN_LAYOUT_V1 = True
REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# KPI card
# ---------------------------------------------------------------------------

class RealTimeFinanceKpiCard(QFrame):
    """Reusable KPI card for Real-Time Finance."""

    def __init__(self, title):
        super().__init__()

        self.setFrameShape(
            QFrame.StyledPanel
        )
        self.setMinimumHeight(82)
        self.setMaximumHeight(95)

        self.setStyleSheet(
            """
            RealTimeFinanceKpiCard {
                background-color: #ffffff;
                border: 1px solid #d9dee8;
                border-radius: 8px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            12,
            8,
            12,
            8,
        )
        layout.setSpacing(4)

        title_label = QLabel(title)
        title_label.setAlignment(
            Qt.AlignCenter
        )
        title_label.setStyleSheet(
            "font-size: 12px; font-weight: bold;"
        )
        layout.addWidget(title_label)

        self.value_label = QLabel(
            "Rs. 0.00"
        )
        self.value_label.setAlignment(
            Qt.AlignCenter
        )
        self.value_label.setStyleSheet(
            "font-size: 18px; font-weight: bold;"
        )
        layout.addWidget(
            self.value_label
        )

    def set_amount(
        self,
        amount,
        is_final_income=False,
    ):
        """Display a formatted monetary amount."""
        numeric_amount = float(amount)

        self.value_label.setText(
            f"Rs. {numeric_amount:,.2f}"
        )

        if is_final_income:
            if numeric_amount < 0:
                text_color = "#b00020"
            else:
                text_color = "#166534"

            self.value_label.setStyleSheet(
                f"font-size: 18px; "
                f"font-weight: bold; "
                f"color: {text_color};"
            )
        else:
            self.value_label.setStyleSheet(
                "font-size: 18px; "
                "font-weight: bold;"
            )


# ---------------------------------------------------------------------------
# Real-Time Finance page
# ---------------------------------------------------------------------------

class RealTimeFinancePage(QWidget):
    """Display exact-date fee collection, expenses, and final income."""

    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window
        self.current_summary = None
        self.fee_current_page = 1
        self.expense_current_page = 1

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(
            18,
            14,
            18,
            16,
        )
        main_layout.setSpacing(7)

        # ---------------------------------------------------------------
        # Page heading
        # ---------------------------------------------------------------

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        title_label = QLabel(
            "Real-Time Finance"
        )
        title_label.setObjectName(
            "pageTitle"
        )
        title_row.addWidget(
            title_label
        )
        title_row.addStretch()

        self.back_to_main_dashboard_button = QPushButton(
            "Back to Main Dashboard"
        )
        self.back_to_main_dashboard_button.setObjectName(
            "secondaryButton"
        )
        self.back_to_main_dashboard_button.setMinimumWidth(
            190
        )
        self.back_to_main_dashboard_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_MAIN_DASHBOARD
            )
        )
        title_row.addWidget(
            self.back_to_main_dashboard_button
        )

        main_layout.addLayout(
            title_row
        )

        instruction_label = QLabel(
            "The screen shows today's financial activity by default. "
            "Select any exact date to view that day's collected fee, "
            "expenses, and final income."
        )
        instruction_label.setWordWrap(
            True
        )
        main_layout.addWidget(
            instruction_label
        )

        # ---------------------------------------------------------------
        # Date search
        # ---------------------------------------------------------------

        search_row = QHBoxLayout()
        search_row.setSpacing(12)

        search_row.addWidget(
            QLabel("Select Date:")
        )

        self.search_date_input = QDateEdit()
        self.search_date_input.setCalendarPopup(
            True
        )
        self.search_date_input.setDisplayFormat(
            "dd-MM-yyyy"
        )
        self.search_date_input.setMinimumWidth(
            180
        )
        search_row.addWidget(
            self.search_date_input
        )

        search_button = QPushButton(
            "Search Finance"
        )
        search_button.clicked.connect(
            self._load_selected_date
        )
        search_row.addWidget(
            search_button
        )

        today_button = QPushButton(
            "Show Today's Data"
        )
        today_button.setObjectName(
            "secondaryButton"
        )
        today_button.clicked.connect(
            self._load_today
        )
        search_row.addWidget(
            today_button
        )

        refresh_button = QPushButton(
            "Refresh"
        )
        refresh_button.setObjectName(
            "secondaryButton"
        )
        refresh_button.clicked.connect(
            self._refresh_current_date
        )
        search_row.addWidget(
            refresh_button
        )

        search_row.addStretch()

        main_layout.addLayout(
            search_row
        )

        self.date_information_label = QLabel(
            "No financial data loaded."
        )
        self.date_information_label.setStyleSheet(
            "font-weight: bold;"
        )
        main_layout.addWidget(
            self.date_information_label
        )

        # ---------------------------------------------------------------
        # KPI cards
        # ---------------------------------------------------------------

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(14)

        self.fee_collected_card = (
            RealTimeFinanceKpiCard(
                "Total Fee Collected"
            )
        )
        kpi_row.addWidget(
            self.fee_collected_card
        )

        self.expenses_card = (
            RealTimeFinanceKpiCard(
                "Total Expenses"
            )
        )
        kpi_row.addWidget(
            self.expenses_card
        )

        self.final_income_card = (
            RealTimeFinanceKpiCard(
                "Final Income"
            )
        )
        kpi_row.addWidget(
            self.final_income_card
        )

        main_layout.addLayout(
            kpi_row
        )

        formula_label = QLabel(
            "Final Income = Total Fee Collected − Total Expenses"
        )
        formula_label.setAlignment(
            Qt.AlignCenter
        )
        formula_label.setStyleSheet(
            "font-weight: bold;"
        )
        main_layout.addWidget(
            formula_label
        )

        # ---------------------------------------------------------------
        # Fee payments table
        # ---------------------------------------------------------------

        fee_heading_row = QHBoxLayout()

        fee_heading = QLabel(
            "Fee Collection Details"
        )
        fee_heading.setStyleSheet(
            "font-size: 15px; font-weight: bold;"
        )
        fee_heading_row.addWidget(
            fee_heading
        )

        self.fee_count_label = QLabel(
            "Payments: 0"
        )
        self.fee_count_label.setStyleSheet(
            "font-weight: bold;"
        )
        fee_heading_row.addWidget(
            self.fee_count_label
        )

        fee_heading_row.addStretch()

        main_layout.addLayout(
            fee_heading_row
        )

        self.fee_table = QTableWidget()
        self.fee_table.setColumnCount(8)
        self.fee_table.setHorizontalHeaderLabels(
            [
                "Receipt No.",
                "Time",
                "Admission No.",
                "Roll No.",
                "Student Name",
                "Father Name",
                "Class",
                "Amount",
            ]
        )

        self.fee_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.fee_table.verticalHeader().setVisible(
            False
        )
        self.fee_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.fee_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )
        self.fee_table.setAlternatingRowColors(
            True
        )
        self.fee_table.setMinimumHeight(
            100
        )

        main_layout.addWidget(
            self.fee_table,
            2,
        )

        fee_paging_row = QHBoxLayout()
        self.fee_previous_page_button = QPushButton("Previous")
        self.fee_previous_page_button.setObjectName("secondaryButton")
        self.fee_previous_page_button.clicked.connect(
            self._show_previous_fee_page
        )
        fee_paging_row.addWidget(self.fee_previous_page_button)

        self.fee_next_page_button = QPushButton("Next")
        self.fee_next_page_button.setObjectName("secondaryButton")
        self.fee_next_page_button.clicked.connect(self._show_next_fee_page)
        fee_paging_row.addWidget(self.fee_next_page_button)

        self.fee_page_label = QLabel("Showing 0 payments")
        self.fee_page_label.setStyleSheet("font-weight: bold;")
        fee_paging_row.addWidget(self.fee_page_label)
        fee_paging_row.addStretch()
        fee_paging_row.addWidget(
            QLabel(
                f"Display: {REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE} "
                "payments per page"
            )
        )
        main_layout.addLayout(fee_paging_row)

        # ---------------------------------------------------------------
        # Expenses table
        # ---------------------------------------------------------------

        expense_heading_row = QHBoxLayout()

        expense_heading = QLabel(
            "Expense Details"
        )
        expense_heading.setStyleSheet(
            "font-size: 15px; font-weight: bold;"
        )
        expense_heading_row.addWidget(
            expense_heading
        )

        self.expense_count_label = QLabel(
            "Expenses: 0"
        )
        self.expense_count_label.setStyleSheet(
            "font-weight: bold;"
        )
        expense_heading_row.addWidget(
            self.expense_count_label
        )

        expense_heading_row.addStretch()

        main_layout.addLayout(
            expense_heading_row
        )

        self.expense_table = QTableWidget()
        self.expense_table.setColumnCount(4)
        self.expense_table.setHorizontalHeaderLabels(
            [
                "Record No.",
                "Time Added",
                "Description",
                "Amount",
            ]
        )

        self.expense_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.expense_table.verticalHeader().setVisible(
            False
        )
        self.expense_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.expense_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )
        self.expense_table.setAlternatingRowColors(
            True
        )
        self.expense_table.setMinimumHeight(
            90
        )

        main_layout.addWidget(
            self.expense_table,
            1,
        )

        expense_paging_row = QHBoxLayout()
        self.expense_previous_page_button = QPushButton("Previous")
        self.expense_previous_page_button.setObjectName("secondaryButton")
        self.expense_previous_page_button.clicked.connect(
            self._show_previous_expense_page
        )
        expense_paging_row.addWidget(self.expense_previous_page_button)

        self.expense_next_page_button = QPushButton("Next")
        self.expense_next_page_button.setObjectName("secondaryButton")
        self.expense_next_page_button.clicked.connect(
            self._show_next_expense_page
        )
        expense_paging_row.addWidget(self.expense_next_page_button)

        self.expense_page_label = QLabel("Showing 0 expenses")
        self.expense_page_label.setStyleSheet("font-weight: bold;")
        expense_paging_row.addWidget(self.expense_page_label)
        expense_paging_row.addStretch()
        expense_paging_row.addWidget(
            QLabel(
                f"Display: {REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE} "
                "expenses per page"
            )
        )
        main_layout.addLayout(expense_paging_row)

        # ---------------------------------------------------------------
        # Report controls
        # ---------------------------------------------------------------

        report_row = QHBoxLayout()
        report_row.setSpacing(12)

        self.view_report_button = QPushButton(
            "View Daily Finance Report"
        )
        self.view_report_button.setEnabled(
            False
        )

        self.view_report_button.clicked.connect(
            self._view_daily_finance_report
        )
        report_row.addWidget(
            self.view_report_button
        )

        self.print_report_button = QPushButton(
            "Print Daily Finance Report"
        )
        self.print_report_button.setEnabled(
            False
        )

        self.print_report_button.clicked.connect(
            self._print_daily_finance_report
        )
        report_row.addWidget(
            self.print_report_button
        )

        self.pdf_report_button = QPushButton(
            "Download Daily Finance PDF"
        )
        self.pdf_report_button.setEnabled(
            False
        )

        self.pdf_report_button.clicked.connect(
            self._download_daily_finance_pdf
        )
        report_row.addWidget(
            self.pdf_report_button
        )

        report_row.addStretch()

        main_layout.addLayout(
            report_row
        )

        self._render_fee_page()
        self._render_expense_page()
        # Today's data is loaded when the user opens Real-Time Finance.

    # ------------------------------------------------------------------
    # Page loading
    # ------------------------------------------------------------------

    def reset_page(self):
        """Open the page with today's financial data."""
        self.search_date_input.setDate(
            QDate.currentDate()
        )

        self._load_selected_date()

    def _load_today(self):
        """Set the date to today and reload financial activity."""
        self.search_date_input.setDate(
            QDate.currentDate()
        )

        self._load_selected_date()

    def _refresh_current_date(self):
        """Reload data for the currently selected date."""
        self._load_selected_date()

    def _load_selected_date(self):
        """Load finance information for the selected exact date."""
        selected_qdate = (
            self.search_date_input.date()
        )

        selected_date_text = (
            selected_qdate.toString(
                "yyyy-MM-dd"
            )
        )

        try:
            summary = (
                real_time_finance_service
                .get_real_time_finance_summary(
                    selected_date_text
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Date",
                str(error),
            )
            return

        except Exception as error:
            QMessageBox.critical(
                self,
                "Finance Data Error",
                "The financial data could not be loaded."
                f"\n\n{error}",
            )
            return

        self.current_summary = summary
        self.fee_current_page = 1
        self.expense_current_page = 1

        self._update_kpis(
            summary
        )
        self._render_fee_page()
        self._render_expense_page()

        if summary["is_today"]:
            period_text = (
                f"Today — {summary['display_date']}"
            )
        else:
            period_text = (
                summary["display_date"]
            )

        self.date_information_label.setText(
            f"Showing financial activity for: "
            f"{period_text}"
        )

        self.fee_count_label.setText(
            f"Payments: "
            f"{summary['fee_payment_count']}"
        )

        self.expense_count_label.setText(
            f"Expenses: "
            f"{summary['expense_count']}"
        )

        self.view_report_button.setEnabled(
            True
        )
        self.print_report_button.setEnabled(
            True
        )
        self.pdf_report_button.setEnabled(
            True
        )

    # ------------------------------------------------------------------
    # KPI data
    # ------------------------------------------------------------------

    def _update_kpis(self, summary):
        """Update fee, expense, and final-income KPI cards."""
        self.fee_collected_card.set_amount(
            summary["total_fee_collected"]
        )

        self.expenses_card.set_amount(
            summary["total_expenses"]
        )

        self.final_income_card.set_amount(
            summary["final_income"],
            is_final_income=True,
        )

    # ------------------------------------------------------------------
    # Fee-payment table
    # ------------------------------------------------------------------

    def _populate_fee_table(
        self,
        payment_rows,
    ):
        """Display one bounded page of fee payments."""
        self.fee_table.setRowCount(
            len(payment_rows)
        )

        for row_index, payment_row in enumerate(
            payment_rows
        ):
            payment_time = self._extract_time(
                payment_row.get(
                    "payment_date"
                )
            )

            values = [
                payment_row.get(
                    "receipt_no"
                ) or "-",
                payment_time,
                payment_row.get(
                    "admission_number"
                ) or "-",
                payment_row.get(
                    "roll_number"
                ) or "-",
                payment_row.get(
                    "student_name"
                ) or "-",
                payment_row.get(
                    "father_name"
                ) or "-",
                payment_row.get(
                    "class_name"
                ) or "-",
                (
                    f"Rs. "
                    f"{float(payment_row['total_amount']):,.2f}"
                ),
            ]

            for column_index, value in enumerate(
                values
            ):
                item = QTableWidgetItem(
                    str(value)
                )

                if column_index != 4 and column_index != 5:
                    item.setTextAlignment(
                        Qt.AlignCenter
                    )

                self.fee_table.setItem(
                    row_index,
                    column_index,
                    item,
                )

    def _render_fee_page(self):
        rows = (self.current_summary or {}).get("fee_payment_rows", [])
        total = len(rows)
        pages = max(
            1,
            (total + REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE - 1)
            // REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE,
        )
        self.fee_current_page = min(max(1, self.fee_current_page), pages)
        start = (self.fee_current_page - 1) * REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE
        end = min(start + REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE, total)
        self._populate_fee_table(rows[start:end])
        if total:
            self.fee_page_label.setText(
                f"Showing {start + 1}-{end} of {total} payments"
            )
        else:
            self.fee_page_label.setText("Showing 0 payments")
        self.fee_previous_page_button.setEnabled(self.fee_current_page > 1)
        self.fee_next_page_button.setEnabled(self.fee_current_page < pages)

    def _show_previous_fee_page(self):
        if self.fee_current_page > 1:
            self.fee_current_page -= 1
            self._render_fee_page()

    def _show_next_fee_page(self):
        rows = (self.current_summary or {}).get("fee_payment_rows", [])
        pages = max(
            1,
            (len(rows) + REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE - 1)
            // REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE,
        )
        if self.fee_current_page < pages:
            self.fee_current_page += 1
            self._render_fee_page()

    # ------------------------------------------------------------------
    # Expense table
    # ------------------------------------------------------------------

    def _populate_expense_table(
        self,
        expense_rows,
    ):
        """Display every expense recorded on the selected date."""
        self.expense_table.setRowCount(
            len(expense_rows)
        )

        for row_index, expense_row in enumerate(
            expense_rows
        ):
            created_time = self._extract_time(
                expense_row.get(
                    "created_at"
                )
            )

            values = [
                expense_row.get(
                    "id"
                ) or "-",
                created_time,
                expense_row.get(
                    "description"
                ) or "-",
                (
                    f"Rs. "
                    f"{float(expense_row['amount']):,.2f}"
                ),
            ]

            for column_index, value in enumerate(
                values
            ):
                item = QTableWidgetItem(
                    str(value)
                )

                if column_index != 2:
                    item.setTextAlignment(
                        Qt.AlignCenter
                    )

                self.expense_table.setItem(
                    row_index,
                    column_index,
                    item,
                )

    def _render_expense_page(self):
        rows = (self.current_summary or {}).get("expense_rows", [])
        total = len(rows)
        pages = max(
            1,
            (total + REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE - 1)
            // REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE,
        )
        self.expense_current_page = min(max(1, self.expense_current_page), pages)
        start = (self.expense_current_page - 1) * REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE
        end = min(start + REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE, total)
        self._populate_expense_table(rows[start:end])
        if total:
            self.expense_page_label.setText(
                f"Showing {start + 1}-{end} of {total} expenses"
            )
        else:
            self.expense_page_label.setText("Showing 0 expenses")
        self.expense_previous_page_button.setEnabled(
            self.expense_current_page > 1
        )
        self.expense_next_page_button.setEnabled(
            self.expense_current_page < pages
        )

    def _show_previous_expense_page(self):
        if self.expense_current_page > 1:
            self.expense_current_page -= 1
            self._render_expense_page()

    def _show_next_expense_page(self):
        rows = (self.current_summary or {}).get("expense_rows", [])
        pages = max(
            1,
            (len(rows) + REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE - 1)
            // REAL_TIME_FINANCE_DISPLAY_PAGE_SIZE,
        )
        if self.expense_current_page < pages:
            self.expense_current_page += 1
            self._render_expense_page()

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _extract_time(
        self,
        stored_datetime,
    ):
        """Extract HH:MM:SS from a stored date-time string."""
        if not stored_datetime:
            return "-"

        datetime_text = str(
            stored_datetime
        ).strip()

        if " " not in datetime_text:
            return "-"

        time_text = datetime_text.split(
            " ",
            1,
        )[1]

        return time_text


            # ------------------------------------------------------------------
    # Daily finance report
    # ------------------------------------------------------------------

    def _build_daily_finance_report_html(self):
        """Build the printable daily finance report."""
        if self.current_summary is None:
            raise ValueError(
                "No financial data is available."
            )

        summary = self.current_summary

        generated_at = datetime.now().strftime(
            "%d-%m-%Y %I:%M %p"
        )

        fee_rows_html = ""

        for serial_number, payment_row in enumerate(
            summary["fee_payment_rows"],
            start=1,
        ):
            payment_time = self._extract_time(
                payment_row.get("payment_date")
            )

            fee_rows_html += f"""
                <tr>
                    <td class="center">
                        {serial_number}
                    </td>

                    <td class="center">
                        {escape(str(payment_row.get('receipt_no') or '-'))}
                    </td>

                    <td class="center">
                        {escape(str(payment_time))}
                    </td>

                    <td class="center">
                        {escape(str(payment_row.get('admission_number') or '-'))}
                    </td>

                    <td>
                        {escape(str(payment_row.get('student_name') or '-'))}
                    </td>

                    <td>
                        {escape(str(payment_row.get('father_name') or '-'))}
                    </td>

                    <td class="center">
                        {escape(str(payment_row.get('class_name') or '-'))}
                    </td>

                    <td class="amount">
                        Rs. {float(payment_row['total_amount']):,.2f}
                    </td>
                </tr>
            """

        if not fee_rows_html:
            fee_rows_html = """
                <tr>
                    <td colspan="8" class="center">
                        No fee payments recorded on this date.
                    </td>
                </tr>
            """

        expense_rows_html = ""

        for serial_number, expense_row in enumerate(
            summary["expense_rows"],
            start=1,
        ):
            created_time = self._extract_time(
                expense_row.get("created_at")
            )

            expense_rows_html += f"""
                <tr>
                    <td class="center">
                        {serial_number}
                    </td>

                    <td class="center">
                        {escape(str(expense_row.get('id') or '-'))}
                    </td>

                    <td class="center">
                        {escape(str(created_time))}
                    </td>

                    <td>
                        {escape(str(expense_row.get('description') or '-'))}
                    </td>

                    <td class="amount">
                        Rs. {float(expense_row['amount']):,.2f}
                    </td>
                </tr>
            """

        if not expense_rows_html:
            expense_rows_html = """
                <tr>
                    <td colspan="5" class="center">
                        No expenses recorded on this date.
                    </td>
                </tr>
            """

        final_income_color = "#000000"

        return f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: 8.5pt;
                    color: #000000;
                    margin: 0;
                    padding: 0;
                }}

                .school-name {{
                    text-align: center;
                    font-size: 19pt;
                    font-weight: bold;
                    margin-bottom: 2px;
                }}

                .report-title {{
                    text-align: center;
                    font-size: 14pt;
                    font-weight: bold;
                    margin-bottom: 2px;
                }}

                .report-date {{
                    text-align: center;
                    font-size: 11pt;
                    font-weight: bold;
                    margin-bottom: 3px;
                }}

                .generated-date {{
                    text-align: center;
                    font-size: 8pt;
                    margin-bottom: 12px;
                }}

                .kpi-table {{
                    width: 100%;
                    border-collapse: separate;
                    border-spacing: 8px;
                    margin-bottom: 5px;
                }}

                .kpi-table td {{
                    width: 33.33%;
                    border: 1px solid #000000;
                    background-color: #ffffff;
                    text-align: center;
                    padding: 10px;
                }}

                .kpi-title {{
                    font-size: 10pt;
                    font-weight: bold;
                    margin-bottom: 5px;
                }}

                .kpi-value {{
                    font-size: 15pt;
                    font-weight: bold;
                }}

                .formula {{
                    text-align: center;
                    font-weight: bold;
                    margin: 6px 0 12px 0;
                }}

                .section-heading {{
                    font-size: 11pt;
                    font-weight: bold;
                    background-color: #ffffff;
                    border: 1px solid #000000;
                    padding: 5px;
                    margin-top: 10px;
                }}

                .details-table {{
                    width: 100%;
                    border-collapse: collapse;
                }}

                .details-table th {{
                    border: 1px solid #000000;
                    background-color: #ffffff;
                    padding: 5px;
                    text-align: center;
                    font-weight: bold;
                }}

                .details-table td {{
                    border: 1px solid #000000;
                    padding: 5px;
                }}

                .center {{
                    text-align: center;
                }}

                .amount {{
                    text-align: right;
                    white-space: nowrap;
                }}

                .total-row td {{
                    background-color: #ffffff;
                    font-weight: bold;
                }}

                .signatures {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 25px;
                }}

                .signatures td {{
                    width: 50%;
                    border: none;
                    padding-top: 15px;
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
                Daily Real-Time Finance Report
            </div>

            <div class="report-date">
                {escape(str(summary['display_date']))}
            </div>

            <div class="generated-date">
                Generated: {generated_at}
            </div>

            <table class="kpi-table">
                <tr>
                    <td>
                        <div class="kpi-title">
                            Total Fee Collected
                        </div>

                        <div class="kpi-value">
                            Rs. {summary['total_fee_collected']:,.2f}
                        </div>
                    </td>

                    <td>
                        <div class="kpi-title">
                            Total Expenses
                        </div>

                        <div class="kpi-value">
                            Rs. {summary['total_expenses']:,.2f}
                        </div>
                    </td>

                    <td>
                        <div class="kpi-title">
                            Final Income
                        </div>

                        <div class="kpi-value"
                             style="color:{final_income_color};">
                            Rs. {summary['final_income']:,.2f}
                        </div>
                    </td>
                </tr>
            </table>

            <div class="formula">
                Final Income =
                Total Fee Collected − Total Expenses
            </div>

            <div class="section-heading">
                Fee Collection Details
                — Payments: {summary['fee_payment_count']}
            </div>

            <table class="details-table">
                <tr>
                    <th style="width:5%;">No.</th>
                    <th style="width:13%;">Receipt No.</th>
                    <th style="width:9%;">Time</th>
                    <th style="width:12%;">Admission No.</th>
                    <th style="width:17%;">Student Name</th>
                    <th style="width:17%;">Father Name</th>
                    <th style="width:10%;">Class</th>
                    <th style="width:17%;">Amount</th>
                </tr>

                {fee_rows_html}

                <tr class="total-row">
                    <td colspan="7">
                        Total Fee Collected
                    </td>

                    <td class="amount">
                        Rs. {summary['total_fee_collected']:,.2f}
                    </td>
                </tr>
            </table>

            <div class="section-heading">
                Expense Details
                — Records: {summary['expense_count']}
            </div>

            <table class="details-table">
                <tr>
                    <th style="width:7%;">No.</th>
                    <th style="width:12%;">Record No.</th>
                    <th style="width:12%;">Time</th>
                    <th style="width:49%;">Description</th>
                    <th style="width:20%;">Amount</th>
                </tr>

                {expense_rows_html}

                <tr class="total-row">
                    <td colspan="4">
                        Total Expenses
                    </td>

                    <td class="amount">
                        Rs. {summary['total_expenses']:,.2f}
                    </td>
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

    def _create_daily_finance_document(self):
        """Create the daily finance QTextDocument."""
        document = QTextDocument()

        document.setHtml(
            self._build_daily_finance_report_html()
        )

        return document

    def _configure_daily_finance_printer(
        self,
        printer,
    ):
        """Configure the daily report for landscape printing."""
        printer.setPageOrientation(
            QPageLayout.Orientation.Landscape
        )

    def _view_daily_finance_report(self):
        """Open the daily finance report preview."""
        if self.current_summary is None:
            QMessageBox.warning(
                self,
                "No Finance Data",
                "Load financial data before viewing the report.",
            )
            return

        try:
            printer = QPrinter(
                QPrinter.PrinterMode.HighResolution
            )

            self._configure_daily_finance_printer(
                printer
            )

            document = (
                self._create_daily_finance_document()
            )

            preview_dialog = QPrintPreviewDialog(
                printer,
                self,
            )

            preview_dialog.setWindowTitle(
                "View Daily Finance Report"
            )

            preview_dialog.paintRequested.connect(
                lambda preview_printer: document.print_(
                    preview_printer
                )
            )

            preview_dialog.exec()

        except Exception as error:
            QMessageBox.critical(
                self,
                "Report Preview Error",
                "The daily finance report could not be displayed."
                f"\n\n{error}",
            )

    def _print_daily_finance_report(self):
        """Open the Windows print dialog."""
        if self.current_summary is None:
            QMessageBox.warning(
                self,
                "No Finance Data",
                "Load financial data before printing.",
            )
            return

        printer = QPrinter(
            QPrinter.PrinterMode.HighResolution
        )

        self._configure_daily_finance_printer(
            printer
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )

        print_dialog.setWindowTitle(
            "Print Daily Finance Report"
        )

        if (
            print_dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        try:
            document = (
                self._create_daily_finance_document()
            )

            document.print_(printer)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Print Error",
                "The daily finance report could not be printed."
                f"\n\n{error}",
            )

    def _download_daily_finance_pdf(self):
        """Save the daily finance report as a PDF."""
        if self.current_summary is None:
            QMessageBox.warning(
                self,
                "No Finance Data",
                "Load financial data before downloading the PDF.",
            )
            return

        selected_date = self.current_summary[
            "selected_date"
        ].replace("-", "_")

        suggested_name = (
            "Hira_Model_School_Daily_Finance_"
            f"{selected_date}.pdf"
        )

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Daily Finance PDF",
            suggested_name,
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

            self._configure_daily_finance_printer(
                printer
            )

            document = (
                self._create_daily_finance_document()
            )

            document.print_(printer)

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Save Error",
                "The daily finance PDF could not be saved."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "PDF Saved",
            "The daily finance report PDF was saved successfully.",
        )
