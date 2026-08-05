"""
Full School Fee List screen.

Shows fee status for every active student in the school and provides:

- Direct printing through the Windows printer dialog
- PDF download
- Yellow and red overdue-status highlighting
- Numeric count of unpaid monthly fees
"""

from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QMarginsF
from PySide6.QtGui import (
    QColor,
    QTextDocument,
    QPageLayout,
    QPageSize,
    QPainter,
)
from PySide6.QtPrintSupport import (
    QPrinter,
    QPrintDialog,
)
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QDialog,
)

from app import navigation
from app.services import student_service
from app.services import fee_service


FULL_SCHOOL_DISPLAY_PAGE_SIZE = 100

# MONTHLY_ONLY_FULL_SCHOOL_FEE_LIST_UI_V8
# Fee Status legends now describe Monthly Fee counts only.

class FullSchoolFeeListPage(QWidget):
    """Display and print fee status for every active student."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.status_rows = []
        self.current_page = 1

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(25, 20, 25, 25)
        layout.setSpacing(12)

        title_label = QLabel("Full School Fee List")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        instruction_label = QLabel(
            "This list contains every active student in the school, "
            "together with the last paid fee, pending fees, and fee status."
        )
        instruction_label.setWordWrap(True)
        layout.addWidget(instruction_label)

        # ---------------------------------------------------------------
        # Summary
        # ---------------------------------------------------------------

        summary_row = QHBoxLayout()
        summary_row.setSpacing(18)

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
            "No Overdue Fee: 0"
        )
        self.fully_paid_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.fully_paid_label
        )

        self.yellow_label = QLabel(
            "Yellow Status: 0"
        )
        self.yellow_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.yellow_label
        )

        self.red_label = QLabel(
            "Red Status: 0"
        )
        self.red_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.red_label
        )

        self.fee_not_set_label = QLabel(
            "Fee Not Set: 0"
        )
        self.fee_not_set_label.setStyleSheet(
            "font-weight: bold;"
        )
        summary_row.addWidget(
            self.fee_not_set_label
        )

        summary_row.addStretch()
        layout.addLayout(summary_row)

        legend_label = QLabel(
            "Fee Status is the number of unpaid monthly fees; "
            "for example, 2 means two monthly fees are unpaid."
        )
        legend_label.setWordWrap(True)
        layout.addWidget(legend_label)

        # ---------------------------------------------------------------
        # Action buttons
        # ---------------------------------------------------------------

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        load_button = QPushButton(
            "Load Full School List"
        )
        load_button.clicked.connect(
            self.load_full_school_list
        )
        action_row.addWidget(
            load_button
        )

        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName(
            "secondaryButton"
        )
        refresh_button.clicked.connect(
            self.load_full_school_list
        )
        action_row.addWidget(
            refresh_button
        )

        self.print_button = QPushButton(
            "Print Full School List"
        )
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(
            self._print_full_school_list
        )
        action_row.addWidget(
            self.print_button
        )

        self.download_button = QPushButton(
            "Download Full School List PDF"
        )
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(
            self._download_full_school_list_pdf
        )
        action_row.addWidget(
            self.download_button
        )

        layout.addLayout(action_row)

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
            QLabel(f"Display: {FULL_SCHOOL_DISPLAY_PAGE_SIZE} students per page")
        )
        layout.addLayout(paging_row)

        # ---------------------------------------------------------------
        # Full school table
        # ---------------------------------------------------------------

        self.status_table = QTableWidget()
        self.status_table.setColumnCount(8)
        self.status_table.setHorizontalHeaderLabels(
            [
                "Class",
                "Admission No",
                "Roll No",
                "Student Name",
                "Father Name",
                "Last Paid",
                "Fee Status",
                "Pending Fees",
            ]
        )

        self.status_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )
        self.status_table.setSelectionBehavior(
            QTableWidget.SelectRows
        )
        self.status_table.setMinimumHeight(470)

        layout.addWidget(self.status_table)

        back_button = QPushButton(
            "Back to Fee Management"
        )
        back_button.setObjectName(
            "secondaryButton"
        )
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_FEE_DASHBOARD
            )
        )
        layout.addWidget(back_button)

        self.reset_page()

    # ------------------------------------------------------------------
    # Page setup
    # ------------------------------------------------------------------

    def reset_page(self):
        """Clear the list and disable printing options."""
        self.status_rows = []
        self.current_page = 1
        self.status_table.setRowCount(0)
        self.previous_page_button.setEnabled(False)
        self.next_page_button.setEnabled(False)
        self.page_label.setText("Showing 0 records")

        self.total_students_label.setText(
            "Total Students: 0"
        )
        self.fully_paid_label.setText(
            "No Overdue Fee: 0"
        )
        self.yellow_label.setText(
            "Yellow Status: 0"
        )
        self.red_label.setText(
            "Red Status: 0"
        )
        self.fee_not_set_label.setText(
            "Fee Not Set: 0"
        )

        self.print_button.setEnabled(False)
        self.download_button.setEnabled(False)
    

    def _get_display_rows(self):
        """
        Show only rows that need attention.

        Hidden:
        - fee status 0

        Shown:
        - 1, 2, 2*, *
        - Fee Not Set
        """
        display_rows = []

        for row in self.status_rows:
            fee_status_text = str(
                row.get("fee_status_text", "")
            ).strip()

            if fee_status_text == "0":
                continue

            display_rows.append(row)

        return display_rows
    # ------------------------------------------------------------------
    # Load records
    # ------------------------------------------------------------------

    def load_full_school_list(self):
        """Load fee records for every active student."""
        try:
            self.status_rows = (
                fee_service.get_full_school_fee_status_rows()
            )
        except Exception as error:
            self.reset_page()

            QMessageBox.critical(
                self,
                "Full School List Error",
                "The full school fee list could not be loaded."
                f"\n\n{error}",
            )
            return

        self.current_page = 1
        self._populate_table()

        has_records = bool(self.status_rows)
        has_display_rows = bool(
            self._get_display_rows()
        )

        self.print_button.setEnabled(
            has_display_rows
        )
        self.download_button.setEnabled(
            has_display_rows
        )

        if not has_records:
            QMessageBox.information(
                self,
                "No Active Students",
                "There are no active students in the school.",
            )

    def _populate_table(self):
        """Display only non-zero fee-status rows in the table."""
        display_rows = self._get_display_rows()

        total_rows = len(display_rows)
        total_pages = max(
            1,
            (total_rows + FULL_SCHOOL_DISPLAY_PAGE_SIZE - 1)
            // FULL_SCHOOL_DISPLAY_PAGE_SIZE,
        )
        self.current_page = max(1, min(self.current_page, total_pages))
        start_index = (self.current_page - 1) * FULL_SCHOOL_DISPLAY_PAGE_SIZE
        end_index = min(start_index + FULL_SCHOOL_DISPLAY_PAGE_SIZE, total_rows)
        page_rows = display_rows[start_index:end_index]

        self.status_table.setRowCount(len(page_rows))
        self.previous_page_button.setEnabled(self.current_page > 1)
        self.next_page_button.setEnabled(self.current_page < total_pages)
        if total_rows:
            self.page_label.setText(
                f"Showing {start_index + 1}-{end_index} of {total_rows} records "
                f"(Page {self.current_page} of {total_pages})"
            )
        else:
            self.page_label.setText("Showing 0 records")

        hidden_zero_count = 0
        yellow_count = 0
        red_count = 0
        fee_not_set_count = 0

        for original_row in self.status_rows:
            if str(original_row.get("fee_status_text", "")).strip() == "0":
                hidden_zero_count += 1

        for row_index, status_row in enumerate(
            page_rows
        ):
            display_name = (
                student_service.display_name_with_stars(
                    status_row["student_name"],
                    status_row["failure_count"],
                )
            )

            pending_items = status_row[
                "unpaid_items"
            ]

            pending_text = (
                ", ".join(pending_items)
                if pending_items
                else "None"
            )

            color_status = status_row[
                "fee_color_status"
            ]

            if status_row["fee_setup_missing"]:
                row_color = QColor(
                    220,
                    220,
                    220,
                )
                fee_not_set_count += 1

            elif color_status == "yellow":
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

            values = [
                status_row["class_name"],
                status_row["admission_number"],
                status_row["roll_number"],
                display_name,
                status_row["father_name"],
                status_row["last_paid"],
                status_row["fee_status_text"],
                pending_text,
            ]

            for column_index, value in enumerate(values):
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

        self.total_students_label.setText(
            f"Students Shown: {len(display_rows)}"
        )
        self.fully_paid_label.setText(
            f"Hidden 0 Status: {hidden_zero_count}"
        )
        self.yellow_label.setText(
            f"Yellow Status: {yellow_count}"
        )
        self.red_label.setText(
            f"Red Status: {red_count}"
        )
        self.fee_not_set_label.setText(
            f"Fee Not Set: {fee_not_set_count}"
        )
    def _show_previous_page(self):
        if self.current_page <= 1:
            return
        self.current_page -= 1
        self._populate_table()

    def _show_next_page(self):
        display_count = len(self._get_display_rows())
        total_pages = max(
            1,
            (display_count + FULL_SCHOOL_DISPLAY_PAGE_SIZE - 1)
            // FULL_SCHOOL_DISPLAY_PAGE_SIZE,
        )
        if self.current_page >= total_pages:
            return
        self.current_page += 1
        self._populate_table()

    # ------------------------------------------------------------------
    # Printable report
    # ------------------------------------------------------------------

    @staticmethod
    def _compact_pending_items(pending_items):
        """Return a compact but lossless month-range summary for reports."""
        cleaned = [
            str(item or "").strip()
            for item in (pending_items or [])
            if str(item or "").strip()
        ]
        if not cleaned:
            return "None"
        if cleaned == ["Fee Not Set"]:
            return "Fee Not Set"

        parsed = []
        for item in cleaned:
            try:
                parsed_date = datetime.strptime(item, "%B %Y")
            except ValueError:
                return ", ".join(cleaned)
            parsed.append((parsed_date.year, parsed_date.month))

        def month_index(value):
            return value[0] * 12 + value[1]

        def label(value):
            return datetime(value[0], value[1], 1).strftime("%b-%y")

        ranges = []
        range_start = parsed[0]
        previous = parsed[0]
        for current in parsed[1:]:
            if month_index(current) == month_index(previous) + 1:
                previous = current
                continue
            ranges.append((range_start, previous))
            range_start = current
            previous = current
        ranges.append((range_start, previous))

        return ", ".join(
            label(start_value)
            if start_value == end_value
            else f"{label(start_value)}–{label(end_value)}"
            for start_value, end_value in ranges
        )

    def _group_report_rows(self):
        """Group the already sorted display rows without changing their order."""
        groups = []
        current_name = None
        current_rows = None
        for status_row in self._get_display_rows():
            class_name = str(status_row.get("class_name") or "-")
            if class_name != current_name:
                current_name = class_name
                current_rows = []
                groups.append((class_name, current_rows))
            current_rows.append(status_row)
        return groups

    def _build_class_page_html(
        self,
        class_name,
        class_rows,
        page_number,
        total_pages,
        generated_date,
        font_size_pt,
        cell_padding_px,
    ):
        """Build one compact class page; it is never joined to another class."""
        row_html = []
        fee_not_set_count = 0
        yellow_count = 0
        red_count = 0

        for serial_no, status_row in enumerate(class_rows, start=1):
            display_name = student_service.display_name_with_stars(
                status_row["student_name"],
                status_row["failure_count"],
            )
            pending_text = self._compact_pending_items(
                status_row.get("unpaid_items")
            )

            if status_row["fee_setup_missing"]:
                row_background = "#dddddd"
                fee_not_set_count += 1
            elif status_row["fee_color_status"] == "yellow":
                row_background = "#fff5aa"
                yellow_count += 1
            elif status_row["fee_color_status"] == "red":
                row_background = "#ffbebe"
                red_count += 1
            else:
                row_background = "#ffffff"

            centered = (
                "border:0.55pt solid #555555;"
                f"padding:{cell_padding_px}px;"
                f"background-color:{row_background};"
                "text-align:center;white-space:nowrap;"
            )
            left = centered.replace("text-align:center", "text-align:left")

            row_html.append(
                f"""
                <tr>
                    <td style="{centered}">{serial_no}</td>
                    <td style="{centered}">{escape(str(status_row['admission_number']))}</td>
                    <td style="{centered}">{escape(student_service.display_roll_number(status_row))}</td>
                    <td style="{left}">{escape(str(display_name))}</td>
                    <td style="{left}">{escape(str(status_row['father_name']))}</td>
                    <td style="{centered}">{escape(str(status_row['last_paid']))}</td>
                    <td style="{centered}"><b>{escape(str(status_row['fee_status_text']))}</b></td>
                    <td style="{left}">{escape(pending_text)}</td>
                </tr>
                """
            )

        return f"""
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: {font_size_pt}pt;
                    color: #000000;
                    margin: 0;
                    padding: 0;
                }}
                .school-name {{
                    text-align:center;
                    font-size:{max(font_size_pt + 5.0, 10.0)}pt;
                    font-weight:bold;
                    margin:0;
                    padding:0;
                }}
                .report-title {{
                    text-align:center;
                    font-size:{max(font_size_pt + 2.5, 8.0)}pt;
                    font-weight:bold;
                    margin:1px 0 0 0;
                }}
                .meta {{
                    width:100%;
                    margin:1px 0 2px 0;
                    font-size:{max(font_size_pt - 0.2, 4.0)}pt;
                }}
                .meta td {{ border:none; padding:0; }}
                .class-heading {{
                    font-size:{max(font_size_pt + 1.0, 6.0)}pt;
                    font-weight:bold;
                    background:#d9d9d9;
                    border:0.55pt solid #555555;
                    padding:{max(cell_padding_px, 1)}px;
                    text-align:center;
                }}
                table.class-table {{
                    width:100%;
                    border-collapse:collapse;
                    table-layout:fixed;
                    margin:0;
                }}
                th {{
                    border:0.55pt solid #555555;
                    padding:{cell_padding_px}px;
                    background:#e7e9ee;
                    font-weight:bold;
                    text-align:center;
                    white-space:nowrap;
                }}
                tr {{ page-break-inside:avoid; }}
                .footer {{
                    width:100%;
                    margin-top:2px;
                    font-size:{max(font_size_pt - 0.35, 4.0)}pt;
                    font-weight:bold;
                }}
                .footer td {{ border:none; padding:0; }}
            </style>
        </head>
        <body>
            <div class="school-name">Hira Model School</div>
            <div class="report-title">Full School Fee List</div>
            <table class="meta">
                <tr>
                    <td style="text-align:left;">Generated: {escape(generated_date)}</td>
                    <td style="text-align:center;">Class: <b>{escape(class_name)}</b></td>
                    <td style="text-align:right;">Page {page_number} of {total_pages}</td>
                </tr>
            </table>
            <div class="class-heading">{escape(class_name)}</div>
            <table class="class-table">
                <tr>
                    <th style="width:4%;">No.</th>
                    <th style="width:11%;">Admission No.</th>
                    <th style="width:7%;">Roll No.</th>
                    <th style="width:18%;">Student Name</th>
                    <th style="width:18%;">Father Name</th>
                    <th style="width:11%;">Last Paid</th>
                    <th style="width:9%;">Fee Status</th>
                    <th style="width:22%;">Pending Fees</th>
                </tr>
                {''.join(row_html)}
            </table>
            <table class="footer">
                <tr>
                    <td style="text-align:left;">Total Students Shown: {len(class_rows)}</td>
                    <td style="text-align:center;">Yellow: {yellow_count} &nbsp; Red: {red_count} &nbsp; Fee Not Set: {fee_not_set_count}</td>
                    <td style="text-align:right;">0-status students are hidden</td>
                </tr>
            </table>
        </body>
        </html>
        """

    def _create_fitted_class_document(
        self,
        class_name,
        class_rows,
        page_number,
        total_pages,
        generated_date,
        page_size_points,
    ):
        """Choose the largest readable report style that fits one printer page."""
        style_candidates = (
            (8.2, 3.0),
            (7.5, 2.4),
            (6.9, 1.8),
            (6.3, 1.25),
            (5.7, 0.8),
            (5.1, 0.4),
            (4.5, 0.0),
            (3.9, 0.0),
        )
        selected_document = None
        for font_size_pt, cell_padding_px in style_candidates:
            document = QTextDocument()
            document.setDocumentMargin(0)
            document.setTextWidth(page_size_points.width())
            document.setHtml(
                self._build_class_page_html(
                    class_name,
                    class_rows,
                    page_number,
                    total_pages,
                    generated_date,
                    font_size_pt,
                    cell_padding_px,
                )
            )
            document.setTextWidth(page_size_points.width())
            selected_document = document
            if document.documentLayout().documentSize().height() <= page_size_points.height():
                return document, 1.0

        document_height = max(
            selected_document.documentLayout().documentSize().height(),
            1.0,
        )
        final_scale = min(1.0, page_size_points.height() / document_height)
        return selected_document, final_scale

    def _render_report_to_printer(self, printer):
        """Render exactly one complete class per physical/PDF page."""
        class_groups = self._group_report_rows()
        if not class_groups:
            raise ValueError("No non-zero fee-status records are available.")

        page_points = printer.pageRect(QPrinter.Unit.Point)
        page_pixels = printer.pageRect(QPrinter.Unit.DevicePixel)
        if page_points.width() <= 0 or page_points.height() <= 0:
            raise OSError("The selected printer returned an invalid printable page size.")

        painter = QPainter()
        if not painter.begin(printer):
            raise OSError("The selected printer could not be opened for rendering.")

        generated_date = datetime.now().strftime("%d-%m-%Y %I:%M %p")
        try:
            for page_index, (class_name, class_rows) in enumerate(class_groups):
                if page_index and not printer.newPage():
                    raise OSError("The printer could not create the next report page.")

                document, fit_scale = self._create_fitted_class_document(
                    class_name,
                    class_rows,
                    page_index + 1,
                    len(class_groups),
                    generated_date,
                    page_points.size(),
                )

                pixels_per_point_x = page_pixels.width() / page_points.width()
                pixels_per_point_y = page_pixels.height() / page_points.height()
                rendered_width = page_pixels.width() * fit_scale
                horizontal_offset = (page_pixels.width() - rendered_width) / 2.0

                painter.save()
                painter.translate(
                    page_pixels.left() + horizontal_offset,
                    page_pixels.top(),
                )
                painter.scale(
                    pixels_per_point_x * fit_scale,
                    pixels_per_point_y * fit_scale,
                )
                document.drawContents(
                    painter,
                    QRectF(
                        0,
                        0,
                        page_points.width(),
                        page_points.height() / max(fit_scale, 0.0001),
                    ),
                )
                painter.restore()
        finally:
            painter.end()

    def _build_report_html(self):
        """Compatibility helper returning the compact grouped report HTML."""
        groups = self._group_report_rows()
        generated_date = datetime.now().strftime("%d-%m-%Y %I:%M %p")
        pages = []
        for page_index, (class_name, class_rows) in enumerate(groups):
            page_html = self._build_class_page_html(
                class_name,
                class_rows,
                page_index + 1,
                len(groups),
                generated_date,
                6.3,
                1.25,
            )
            if page_index:
                page_html = '<div style="page-break-before:always;"></div>' + page_html
            pages.append(page_html)
        return "".join(pages)

    def _prepare_printer(self):
        """Create an A4 landscape printer with compact report margins."""
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        printer.setPageMargins(
            QMarginsF(6, 6, 6, 6),
            QPageLayout.Unit.Millimeter,
        )
        return printer

    # ------------------------------------------------------------------
    # Direct print
    # ------------------------------------------------------------------

    def _print_full_school_list(self):
        """Open the Windows print dialog without saving a PDF."""
        if not self._get_display_rows():
            QMessageBox.warning(
                self,
                "No Fee Status Records",
                (
                    "There are no non-zero fee-status records "
                    "available to print."
                ),
            )
            return

        printer = self._prepare_printer()

        print_dialog = QPrintDialog(
            printer,
            self,
        )
        print_dialog.setWindowTitle(
            "Print Full School Fee List"
        )

        if (
            print_dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        try:
            self._render_report_to_printer(printer)

        except Exception as error:
            QMessageBox.critical(
                self,
                "Printing Error",
                "The full school fee list could not be printed."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "List Sent to Printer",
            "The Full School Fee List was sent to the selected printer.",
        )

    # ------------------------------------------------------------------
    # PDF download
    # ------------------------------------------------------------------

    def _download_full_school_list_pdf(self):
        """Save the Full School Fee List as a PDF."""
        if not self._get_display_rows():
            QMessageBox.warning(
                self,
                "No Fee Status Records",
                (
                    "There are no non-zero fee-status records "
                    "available to download."
                ),
            )
            return

        current_date = datetime.now().strftime(
            "%Y-%m-%d"
        )

        default_name = (
            f"Full_School_Fee_List_{current_date}.pdf"
        )

        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Full School Fee List",
            default_name,
            "PDF Files (*.pdf)",
        )

        if not file_path:
            return

        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        try:
            output_path = Path(file_path)
            previous_file_state = None

            if output_path.is_file():
                previous_stat = output_path.stat()
                previous_file_state = (
                    previous_stat.st_size,
                    previous_stat.st_mtime_ns,
                )

            printer = self._prepare_printer()
            printer.setOutputFormat(
                QPrinter.PdfFormat
            )
            printer.setOutputFileName(
                file_path
            )

            self._render_report_to_printer(printer)

            if (
                printer.printerState()
                == QPrinter.PrinterState.Error
            ):
                raise OSError(
                    "The PDF printer reported an output error."
                )

            if not output_path.is_file():
                raise OSError(
                    "The PDF output file was not created."
                )

            output_stat = output_path.stat()

            if output_stat.st_size <= 0:
                raise OSError(
                    "The created PDF file is empty."
                )

            current_file_state = (
                output_stat.st_size,
                output_stat.st_mtime_ns,
            )

            if (
                previous_file_state is not None
                and current_file_state == previous_file_state
            ):
                raise OSError(
                    "The existing PDF file was not updated."
                )

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                "The Full School Fee List PDF could not be created."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "PDF Downloaded",
            "The Full School Fee List was saved successfully."
            f"\n\n{file_path}",
        )
