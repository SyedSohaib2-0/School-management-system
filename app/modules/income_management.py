"""
Income Management dashboard.

Provides:
- Search by complete April-to-March session
- Search by session year and month
- Total collected fee KPI
- Total expenses KPI
- Final income KPI
- Monthly fee collection versus expenses bar graph

Final Income = Collected Fee - Expenses
"""

import calendar
import os
import tempfile

from datetime import datetime
from html import escape

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QPainter,
    QTextDocument,
    QPageLayout,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFrame,
    QMessageBox,
    QFileDialog,
    QDialog,
)

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QBarSeries,
    QBarSet,
    QBarCategoryAxis,
    QValueAxis,
)

from PySide6.QtPrintSupport import (
    QPrinter,
    QPrintDialog,
    QPrintPreviewDialog,
)

from app import navigation
from app.services import income_service


# ---------------------------------------------------------------------------
# KPI card
# ---------------------------------------------------------------------------

class IncomeKpiCard(QFrame):
    """Reusable KPI card for Income Management."""

    def __init__(self, title):
        super().__init__()

        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(82)
        self.setMaximumHeight(95)

        self.setStyleSheet(
            """
            IncomeKpiCard {
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

    def set_amount(self, amount):
        """Display a formatted monetary amount."""
        self.value_label.setText(
            f"Rs. {float(amount):,.2f}"
        )


# ---------------------------------------------------------------------------
# Income Management page
# ---------------------------------------------------------------------------

class IncomeManagementPage(QWidget):
    """Income dashboard with KPIs and a monthly comparison chart."""



    def __init__(self, main_window):
        super().__init__()

        self.main_window = main_window

        self.current_summary = None
        self.current_chart_rows = []
        self.current_report_title = ""

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(
            18,
            14,
            18,
            16,
        )
        main_layout.setSpacing(7)

        # ---------------------------------------------------------------
        # Heading
        # ---------------------------------------------------------------

        title_label = QLabel(
            "Income Management"
        )
        title_label.setObjectName(
            "pageTitle"
        )
        main_layout.addWidget(
            title_label
        )

        instruction_label = QLabel(
            "Select a school session. Leave Month as All Months "
            "to view the complete April-to-March session, or select "
            "a specific month to view that month's income data."
        )
        instruction_label.setWordWrap(True)
        main_layout.addWidget(
            instruction_label
        )

        # ---------------------------------------------------------------
        # Search controls
        # ---------------------------------------------------------------

        search_row = QHBoxLayout()
        search_row.setSpacing(12)

        search_row.addWidget(
            QLabel("Session Year:")
        )

        self.session_year_combo = QComboBox()
        self.session_year_combo.setMinimumWidth(
            170
        )
        search_row.addWidget(
            self.session_year_combo
        )

        search_row.addWidget(
            QLabel("Month:")
        )

        self.month_combo = QComboBox()
        self.month_combo.setMinimumWidth(
            210
        )

        self.month_combo.addItem(
            "All Months — Complete Session",
            None,
        )

        for month_number in income_service.SESSION_MONTHS:
            self.month_combo.addItem(
                calendar.month_name[
                    month_number
                ],
                month_number,
            )

        search_row.addWidget(
            self.month_combo
        )

        search_button = QPushButton(
            "Search Income"
        )
        search_button.clicked.connect(
            self._load_income_data
        )
        search_row.addWidget(
            search_button
        )

        refresh_button = QPushButton(
            "Refresh"
        )
        refresh_button.setObjectName(
            "secondaryButton"
        )
        refresh_button.clicked.connect(
            self.reset_page
        )
        search_row.addWidget(
            refresh_button
        )

        search_row.addStretch()

        main_layout.addLayout(
            search_row
        )
        self.search_information_label = QLabel(
            "No income search performed."
        )
        self.search_information_label.setStyleSheet(
            "font-weight: bold;"
        )
        main_layout.addWidget(
            self.search_information_label
        )

        # ---------------------------------------------------------------
        # KPI cards
        # ---------------------------------------------------------------

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(15)

        self.collected_fee_card = IncomeKpiCard(
            "Total Collected Fee"
        )
        kpi_row.addWidget(
            self.collected_fee_card
        )

        self.total_expenses_card = IncomeKpiCard(
            "Total Expenses"
        )
        kpi_row.addWidget(
            self.total_expenses_card
        )

        self.final_income_card = IncomeKpiCard(
            "Final Income"
        )
        kpi_row.addWidget(
            self.final_income_card
        )

        main_layout.addLayout(
            kpi_row
        )

        formula_label = QLabel(
            "Final Income = Total Collected Fee − Total Expenses"
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
        # Monthly bar graph
        # ---------------------------------------------------------------

        graph_heading = QLabel(
            "Monthly Fee Collection and Expenses Comparison"
        )
        graph_heading.setStyleSheet(
            "font-size: 14px; font-weight: bold;"
        )
        main_layout.addWidget(
            graph_heading
        )

        self.chart_view = QChartView()
        self.chart_view.setRenderHint(
            QPainter.Antialiasing
        )
        self.chart_view.setMinimumHeight(
            220
        )

        main_layout.addWidget(
            self.chart_view,
            1,
        )

        # ---------------------------------------------------------------
        # Report controls
        # ---------------------------------------------------------------

        report_row = QHBoxLayout()
        report_row.setSpacing(12)

        self.view_report_button = QPushButton(
            "View Income Report"
        )
        self.view_report_button.setEnabled(
            False
        )

        self.view_report_button.clicked.connect(
            self._view_income_report
        )

        report_row.addWidget(
            self.view_report_button
        )

        self.print_report_button = QPushButton(
            "Print Income Report"
        )
        self.print_report_button.setEnabled(
            False
        )

        self.print_report_button.clicked.connect(
        self._print_income_report
        )
        report_row.addWidget(
            self.print_report_button
        )

        self.pdf_report_button = QPushButton(
            "Download Income Report PDF"
        )
        self.pdf_report_button.setEnabled(
            False
        )

        self.pdf_report_button.clicked.connect(
        self._download_income_report_pdf
        )

        report_row.addWidget(
            self.pdf_report_button
        )

        report_row.addStretch()

        main_layout.addLayout(
            report_row
        )

        # ---------------------------------------------------------------
        # Back button
        # ---------------------------------------------------------------

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

        main_layout.addWidget(
            back_button
        )

        # Data is loaded when Income Management is opened. Avoid running
        # the complete financial aggregation while the page is still hidden
        # during application startup.

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------

    def reset_page(self):
        """
        Reload session years and show the complete current session.

        Month index 0 means All Months.
        """
        self._reload_session_years()

        self.month_combo.setCurrentIndex(
            0
        )

        self._load_income_data()

    def _reload_session_years(self):
        """Load all available April-to-March financial sessions."""
        selected_year = (
            self.session_year_combo.currentData()
        )

        self.session_year_combo.blockSignals(
            True
        )
        self.session_year_combo.clear()

        session_years = (
            income_service
            .get_available_income_session_years()
        )

        for session_start_year in session_years:
            self.session_year_combo.addItem(
                f"{session_start_year}-"
                f"{session_start_year + 1}",
                session_start_year,
            )

        if selected_year is not None:
            selected_index = (
                self.session_year_combo.findData(
                    selected_year
                )
            )

            if selected_index >= 0:
                self.session_year_combo.setCurrentIndex(
                    selected_index
                )

        self.session_year_combo.blockSignals(
            False
        )



    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------

    def _load_income_data(self):
        """Load KPI totals and chart data for the selected search."""
        session_start_year = (
            self.session_year_combo.currentData()
        )

        if session_start_year is None:
            QMessageBox.warning(
                self,
                "No Session Selected",
                "Please select a session year.",
            )
            return

        month_number = (
            self.month_combo.currentData()
        )

        try:
            report_snapshot = income_service.get_income_report_snapshot(
                session_start_year,
                month_number,
            )
            summary = report_snapshot["summary"]
            monthly_rows = report_snapshot["monthly_rows"]

        except Exception as error:
            QMessageBox.critical(
                self,
                "Income Data Error",
                "Income data could not be loaded."
                f"\n\n{error}",
            )
            return

        if month_number is not None:
            chart_rows = [
                row
                for row in monthly_rows
                if row["month_number"]
                == month_number
            ]
        else:
            chart_rows = monthly_rows

        self.current_summary = summary
        self.current_chart_rows = chart_rows

        self.current_report_title = (
            f"Income Report — "
            f"{summary['search_label']}"
        )

        self._update_kpis(
            summary
        )
        self._update_chart(
            chart_rows,
            summary,
        )

        self.search_information_label.setText(
            f"Showing: {summary['search_label']} | "
            f"Session {summary['session_year']}"
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
    # KPI values
    # ------------------------------------------------------------------

    def _update_kpis(self, summary):
        """Update all three Income Management KPI cards."""
        self.collected_fee_card.set_amount(
            summary["collected_fee"]
        )

        self.total_expenses_card.set_amount(
            summary["total_expenses"]
        )

        self.final_income_card.set_amount(
            summary["final_income"]
        )

    # ------------------------------------------------------------------
    # Chart
    # ------------------------------------------------------------------

    def _update_chart(
        self,
        chart_rows,
        summary,
    ):
        """Create the fee collection versus expenses bar graph."""
        collected_fee_set = QBarSet(
            "Collected Fee"
        )
        expenses_set = QBarSet(
            "Expenses"
        )

        categories = []
        chart_values = []

        for row in chart_rows:
            collected_fee = float(
                row["collected_fee"]
            )
            expenses = float(
                row["total_expenses"]
            )

            collected_fee_set.append(
                collected_fee
            )
            expenses_set.append(
                expenses
            )

            categories.append(
                row["month_label"]
            )

            chart_values.extend(
                [
                    collected_fee,
                    expenses,
                ]
            )

        series = QBarSeries()
        series.append(
            collected_fee_set
        )
        series.append(
            expenses_set
        )

        chart = QChart()
        chart.addSeries(
            series
        )

        chart.setTitle(
            f"Fee Collection vs Expenses — "
            f"{summary['search_label']}"
        )

        chart.legend().setVisible(
            True
        )
        chart.legend().setAlignment(
            Qt.AlignBottom
        )

        category_axis = QBarCategoryAxis()
        category_axis.append(
            categories
        )

        chart.addAxis(
            category_axis,
            Qt.AlignBottom,
        )
        series.attachAxis(
            category_axis
        )

        value_axis = QValueAxis()

        maximum_value = max(
            chart_values,
            default=0.0,
        )

        if maximum_value <= 0:
            maximum_value = 1000.0

        value_axis.setRange(
            0,
            maximum_value * 1.15,
        )
        value_axis.setLabelFormat(
            "%.0f"
        )
        value_axis.setTitleText(
            "Amount (Rs.)"
        )

        chart.addAxis(
            value_axis,
            Qt.AlignLeft,
        )
        series.attachAxis(
            value_axis
        )

        self.chart_view.setChart(
            chart
        )

            # ------------------------------------------------------------------
    # Income report
    # ------------------------------------------------------------------

    def _create_chart_image(self):
        """Save the currently displayed income chart as a temporary PNG."""
        temporary_file = tempfile.NamedTemporaryFile(
            suffix=".png",
            delete=False,
        )
        chart_path = temporary_file.name
        temporary_file.close()

        chart_pixmap = self.chart_view.grab()

        if not chart_pixmap.save(
            chart_path,
            "PNG",
        ):
            raise RuntimeError(
                "The income chart image could not be created."
            )

        return chart_path

    def _build_income_report_html(
        self,
        chart_path,
    ):
        """Build the printable income report HTML."""
        if self.current_summary is None:
            raise ValueError(
                "No income data is available."
            )

        generated_at = datetime.now().strftime(
            "%d-%m-%Y %I:%M %p"
        )

        summary = self.current_summary

        chart_url = QUrl.fromLocalFile(
            chart_path
        ).toString()

        monthly_rows_html = ""

        for serial_number, row in enumerate(
            self.current_chart_rows,
            start=1,
        ):
            collected_fee = float(
                row["collected_fee"]
            )
            total_expenses = float(
                row["total_expenses"]
            )
            final_income = float(
                row["final_income"]
            )

            if final_income < 0:
                income_style = (
                    "color:#b00020; font-weight:bold;"
                )
            else:
                income_style = (
                    "color:#166534; font-weight:bold;"
                )

            monthly_rows_html += f"""
                <tr>
                    <td class="center">
                        {serial_number}
                    </td>

                    <td class="center">
                        {escape(str(row['month_name']))}
                        {escape(str(row['calendar_year']))}
                    </td>

                    <td class="amount">
                        Rs. {collected_fee:,.2f}
                    </td>

                    <td class="amount">
                        Rs. {total_expenses:,.2f}
                    </td>

                    <td class="amount"
                        style="{income_style}">
                        Rs. {final_income:,.2f}
                    </td>
                </tr>
            """

        final_income = float(
            summary["final_income"]
        )

        if final_income < 0:
            final_income_color = "#b00020"
        else:
            final_income_color = "#166534"

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
                    margin-bottom: 2px;
                }}

                .report-title {{
                    text-align: center;
                    font-size: 14pt;
                    font-weight: bold;
                    margin-bottom: 2px;
                }}

                .generated-date {{
                    text-align: center;
                    font-size: 8.5pt;
                    margin-bottom: 12px;
                }}

                .kpi-table {{
                    width: 100%;
                    border-collapse: separate;
                    border-spacing: 8px;
                    margin-bottom: 8px;
                }}

                .kpi-table td {{
                    border: 1px solid #aeb6c4;
                    background-color: #f1f3f7;
                    text-align: center;
                    padding: 10px;
                    width: 33.33%;
                }}

                .kpi-title {{
                    font-size: 10pt;
                    font-weight: bold;
                    margin-bottom: 6px;
                }}

                .kpi-value {{
                    font-size: 15pt;
                    font-weight: bold;
                }}

                .formula {{
                    text-align: center;
                    font-weight: bold;
                    margin: 8px 0 12px 0;
                }}

                .chart-heading {{
                    font-size: 11pt;
                    font-weight: bold;
                    margin-top: 8px;
                    margin-bottom: 5px;
                }}

                .chart-image {{
                    width: 100%;
                    max-height: 360px;
                    margin-bottom: 10px;
                }}

                .monthly-table {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 8px;
                }}

                .monthly-table th {{
                    border: 1px solid #555555;
                    background-color: #e7e9ee;
                    padding: 5px;
                    text-align: center;
                    font-weight: bold;
                }}

                .monthly-table td {{
                    border: 1px solid #555555;
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
                    background-color: #eeeeee;
                    font-weight: bold;
                }}

                .signatures {{
                    width: 100%;
                    border-collapse: collapse;
                    margin-top: 25px;
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

            <table class="kpi-table">
                <tr>
                    <td>
                        <div class="kpi-title">
                            Total Collected Fee
                        </div>

                        <div class="kpi-value">
                            Rs. {summary['collected_fee']:,.2f}
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
                Total Collected Fee − Total Expenses
            </div>

            <div class="chart-heading">
                Monthly Fee Collection and Expenses Comparison
            </div>

            <img
                class="chart-image"
                src="{chart_url}"
            >

            <table class="monthly-table">
                <tr>
                    <th style="width:7%;">
                        No.
                    </th>

                    <th style="width:21%;">
                        Month
                    </th>

                    <th style="width:24%;">
                        Collected Fee
                    </th>

                    <th style="width:24%;">
                        Expenses
                    </th>

                    <th style="width:24%;">
                        Final Income
                    </th>
                </tr>

                {monthly_rows_html}

                <tr class="total-row">
                    <td colspan="2">
                        Total
                    </td>

                    <td class="amount">
                        Rs. {summary['collected_fee']:,.2f}
                    </td>

                    <td class="amount">
                        Rs. {summary['total_expenses']:,.2f}
                    </td>

                    <td class="amount"
                        style="color:{final_income_color};">
                        Rs. {summary['final_income']:,.2f}
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

    def _create_income_report_document(
        self,
        chart_path,
    ):
        """Create a QTextDocument containing the income report."""
        document = QTextDocument()

        document.setHtml(
            self._build_income_report_html(
                chart_path
            )
        )

        return document

    def _configure_income_printer(
        self,
        printer,
    ):
        """Configure the income report page."""
        printer.setPageOrientation(
            QPageLayout.Orientation.Landscape
        )

    def _remove_temporary_chart(
        self,
        chart_path,
    ):
        """Remove the temporary chart image safely."""
        try:
            if (
                chart_path
                and os.path.exists(chart_path)
            ):
                os.remove(chart_path)
        except OSError:
            pass

    def _view_income_report(self):
        """Open the income report preview."""
        if self.current_summary is None:
            QMessageBox.warning(
                self,
                "No Income Data",
                "Search income data before viewing the report.",
            )
            return

        chart_path = None

        try:
            chart_path = self._create_chart_image()

            printer = QPrinter(
                QPrinter.PrinterMode.HighResolution
            )

            self._configure_income_printer(
                printer
            )

            document = (
                self._create_income_report_document(
                    chart_path
                )
            )

            preview_dialog = QPrintPreviewDialog(
                printer,
                self,
            )

            preview_dialog.setWindowTitle(
                "View Income Report"
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
                "Income Report Error",
                "The income report could not be displayed."
                f"\n\n{error}",
            )

        finally:
            self._remove_temporary_chart(
                chart_path
            )

    def _print_income_report(self):
        """Open the Windows print dialog for the income report."""
        if self.current_summary is None:
            QMessageBox.warning(
                self,
                "No Income Data",
                "Search income data before printing.",
            )
            return

        chart_path = None

        try:
            chart_path = self._create_chart_image()

            printer = QPrinter(
                QPrinter.PrinterMode.HighResolution
            )

            self._configure_income_printer(
                printer
            )

            print_dialog = QPrintDialog(
                printer,
                self,
            )

            print_dialog.setWindowTitle(
                "Print Income Report"
            )

            if (
                print_dialog.exec()
                != QDialog.DialogCode.Accepted
            ):
                return

            document = (
                self._create_income_report_document(
                    chart_path
                )
            )

            document.print_(printer)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Income Print Error",
                "The income report could not be printed."
                f"\n\n{error}",
            )

        finally:
            self._remove_temporary_chart(
                chart_path
            )

    def _download_income_report_pdf(self):
        """Save the current income report as a PDF."""
        if self.current_summary is None:
            QMessageBox.warning(
                self,
                "No Income Data",
                "Search income data before downloading the PDF.",
            )
            return

        session_year = self.current_summary[
            "session_year"
        ].replace("-", "_")

        month_number = self.current_summary[
            "month_number"
        ]

        if month_number is None:
            report_period = (
                f"Session_{session_year}"
            )
        else:
            report_period = (
                f"{calendar.month_name[month_number]}_"
                f"{session_year}"
            )

        suggested_name = (
            f"Hira_Model_School_Income_Report_"
            f"{report_period}.pdf"
        )

        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Income Report PDF",
            suggested_name,
            "PDF Files (*.pdf)",
        )

        if not save_path:
            return

        if not save_path.lower().endswith(
            ".pdf"
        ):
            save_path += ".pdf"

        chart_path = None

        try:
            chart_path = self._create_chart_image()

            printer = QPrinter(
                QPrinter.PrinterMode.HighResolution
            )

            printer.setOutputFormat(
                QPrinter.OutputFormat.PdfFormat
            )

            printer.setOutputFileName(
                save_path
            )

            self._configure_income_printer(
                printer
            )

            document = (
                self._create_income_report_document(
                    chart_path
                )
            )

            document.print_(printer)

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Save Error",
                "The income report PDF could not be saved."
                f"\n\n{error}",
            )
            return

        finally:
            self._remove_temporary_chart(
                chart_path
            )

        QMessageBox.information(
            self,
            "PDF Saved",
            "The income report PDF was saved successfully.",
        )