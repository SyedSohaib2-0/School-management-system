"""Fee Management report for unpaid fees within an inclusive month range."""

from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import (
    Qt,
    QRectF,
    QMarginsF,
    QObject,
    Signal,
    QRunnable,
    QThreadPool,
)
from PySide6.QtGui import QTextDocument, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
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
    QFileDialog,
    QDialog,
    QAbstractItemView,
)

from app import navigation
from app.config import (
    SESSION_MONTH_ORDER,
    get_current_session_start_year,
    get_session_year_label,
)
from app.services import fee_service, student_service


FEE_SESSION_FIRST_START_YEAR = 2024
FEE_SESSION_LAST_START_YEAR = 2099

REPORT_PAGE_SIZE = 100


class _MonthRangeSearchSignals(QObject):
    """Thread-safe signals for the read-only month-range search."""

    result = Signal(int, object)
    error = Signal(int, str)
    finished = Signal(int)


class _MonthRangeSearchWorker(QRunnable):
    """Load the report off the GUI thread so the page remains responsive."""

    def __init__(self, token, session_start_year, from_month, to_month):
        super().__init__()
        self.token = int(token)
        self.session_start_year = int(session_start_year)
        self.from_month = int(from_month)
        self.to_month = int(to_month)
        self.signals = _MonthRangeSearchSignals()

    def run(self):
        try:
            report = fee_service.get_month_range_unpaid_fee_report(
                self.session_start_year,
                self.from_month,
                self.to_month,
            )
        except Exception as error:
            self.signals.error.emit(self.token, str(error))
        else:
            self.signals.result.emit(self.token, report)
        finally:
            self.signals.finished.emit(self.token)


class MonthRangeFeeReportPage(QWidget):
    """Show active students with one or more unpaid months in a period."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.report = None
        self.report_rows = []
        self.current_page = 1
        self.page_size = REPORT_PAGE_SIZE
        self._search_token = 0
        self._active_search_token = None
        self._search_workers = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_label = QLabel("Sort by Month")
        title_label.setObjectName("pageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch()

        back_button = QPushButton("Back to Fee Management")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_FEE_DASHBOARD)
        )
        title_row.addWidget(back_button)
        layout.addLayout(title_row)

        instruction = QLabel(
            "Select a session and an inclusive From/To month range. "
            "Every active student with at least one unpaid due month in the "
            "selected period will appear. The direction follows the selection: "
            "for example, June to April means June, May, and April."
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        filter_row.addWidget(QLabel("Season:"))
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(145)
        self._populate_session_combo()
        filter_row.addWidget(self.session_combo)

        filter_row.addWidget(QLabel("From Month:"))
        self.from_month_combo = QComboBox()
        self._populate_month_combo(self.from_month_combo)
        filter_row.addWidget(self.from_month_combo)

        filter_row.addWidget(QLabel("To Month:"))
        self.to_month_combo = QComboBox()
        self._populate_month_combo(self.to_month_combo)
        filter_row.addWidget(self.to_month_combo)

        self.session_combo.currentIndexChanged.connect(self._mark_filters_changed)
        self.from_month_combo.currentIndexChanged.connect(self._mark_filters_changed)
        self.to_month_combo.currentIndexChanged.connect(self._mark_filters_changed)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.search)
        filter_row.addWidget(self.search_button)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self.refresh)
        filter_row.addWidget(self.refresh_button)

        filter_row.addStretch()
        layout.addLayout(filter_row)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(18)
        self.period_label = QLabel("Selected Period: -")
        self.students_label = QLabel("Students: 0")
        self.classes_label = QLabel("Classes: 0")
        self.unpaid_entries_label = QLabel("Unpaid Month Entries: 0")
        for label in (
            self.period_label,
            self.students_label,
            self.classes_label,
            self.unpaid_entries_label,
        ):
            label.setStyleSheet("font-weight: bold;")
            summary_row.addWidget(label)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        self.note_label = QLabel(
            "From and To months are included, and the range follows the "
            "selected direction. Future selected months are not treated as "
            "unpaid until their calendar month begins."
        )
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)

        self.print_button = QPushButton("Print Unpaid Fee List")
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self._print_report)
        action_row.addWidget(self.print_button)

        self.download_button = QPushButton("Download Unpaid Fee List PDF")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._download_pdf)
        action_row.addWidget(self.download_button)
        action_row.addStretch()
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
        paging_row.addWidget(QLabel(f"Display: {REPORT_PAGE_SIZE} students per page"))
        layout.addLayout(paging_row)

        self.status_table = QTableWidget()
        self.status_table.setColumnCount(6)
        self.status_table.setHorizontalHeaderLabels(
            [
                "Class",
                "Admission No",
                "Student Name",
                "Roll No",
                "Unpaid Months",
                "No. of Unpaid Months",
            ]
        )
        header = self.status_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.status_table.setAlternatingRowColors(True)
        self.status_table.setWordWrap(False)
        self.status_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.status_table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.status_table.verticalHeader().setDefaultSectionSize(32)
        self.status_table.setMinimumHeight(430)
        layout.addWidget(self.status_table, 1)

        self.reset_page(reset_filters=True)

    def _populate_session_combo(self):
        self.session_combo.clear()
        for start_year in range(
            FEE_SESSION_FIRST_START_YEAR,
            FEE_SESSION_LAST_START_YEAR + 1,
        ):
            self.session_combo.addItem(get_session_year_label(start_year), start_year)
        current_index = self.session_combo.findData(get_current_session_start_year())
        if current_index >= 0:
            self.session_combo.setCurrentIndex(current_index)

    @staticmethod
    def _populate_month_combo(combo):
        combo.clear()
        for month_name, month_number in SESSION_MONTH_ORDER:
            combo.addItem(month_name, int(month_number))

    def reset_page(self, *, reset_filters=False):
        self.report = None
        self.report_rows = []
        self.current_page = 1
        self.status_table.setRowCount(0)
        self.period_label.setText("Selected Period: -")
        self.students_label.setText("Students: 0")
        self.classes_label.setText("Classes: 0")
        self.unpaid_entries_label.setText("Unpaid Month Entries: 0")
        self.print_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.previous_page_button.setEnabled(False)
        self.next_page_button.setEnabled(False)
        self.page_label.setText("Showing 0 records")

        if reset_filters:
            current_start = get_current_session_start_year()
            session_index = self.session_combo.findData(current_start)
            if session_index >= 0:
                self.session_combo.setCurrentIndex(session_index)
            self.from_month_combo.setCurrentIndex(0)
            current_month = datetime.now().month
            to_index = self.to_month_combo.findData(current_month)
            self.to_month_combo.setCurrentIndex(to_index if to_index >= 0 else 0)

    def refresh(self):
        """Re-run the current filters against the database."""
        self.search()

    def _mark_filters_changed(self, _index=None):
        """Never leave an old report visible under newly selected filters."""
        if self._active_search_token is not None:
            return
        if self.report is None and not self.report_rows:
            return
        self.reset_page(reset_filters=False)
        self.note_label.setText(
            "Selection changed. Press Search to load the report for the newly "
            "selected session and month direction."
        )

    def _set_search_busy(self, busy):
        busy = bool(busy)
        self.search_button.setEnabled(not busy)
        self.refresh_button.setEnabled(not busy)
        self.session_combo.setEnabled(not busy)
        self.from_month_combo.setEnabled(not busy)
        self.to_month_combo.setEnabled(not busy)
        self.search_button.setText("Searching..." if busy else "Search")
        if busy:
            self.note_label.setText(
                "Loading the full report in the background. The page remains responsive."
            )

    def search(self):
        session_start_year = self.session_combo.currentData()
        from_month = self.from_month_combo.currentData()
        to_month = self.to_month_combo.currentData()
        if session_start_year is None or from_month is None or to_month is None:
            QMessageBox.warning(
                self,
                "Incomplete Selection",
                "Please select the season, From month, and To month.",
            )
            return

        self._search_token += 1
        token = self._search_token
        self._active_search_token = token
        self._set_search_busy(True)

        worker = _MonthRangeSearchWorker(
            token,
            int(session_start_year),
            int(from_month),
            int(to_month),
        )
        worker.signals.result.connect(self._accept_search_result)
        worker.signals.error.connect(self._accept_search_error)
        worker.signals.finished.connect(self._finish_search_worker)
        self._search_workers[token] = worker
        self._thread_pool.start(worker)

    def _accept_search_result(self, token, report):
        if int(token) != self._active_search_token:
            return

        self.report = report
        self.report_rows = list(report["rows"])
        self.current_page = 1
        self._populate_table()
        has_rows = bool(self.report_rows)
        self.print_button.setEnabled(has_rows)
        self.download_button.setEnabled(has_rows)

        future_count = len(report["future_periods"])
        if future_count:
            self.note_label.setText(
                "From and To months are included in the selected direction. "
                f"{future_count} selected future month(s) were not classified "
                "as unpaid because they are not due yet."
            )
        else:
            self.note_label.setText(
                "From and To months are included in the selected direction. "
                "Only active students with "
                "at least one unpaid month in this period are shown."
            )

        warning_count = len(report["fee_setup_warnings"])
        if warning_count:
            QMessageBox.warning(
                self,
                "Fee Setup Warning",
                f"{warning_count} active student(s) could not be fully simulated "
                "because a fee configuration is missing. Existing persisted "
                "unpaid months were still included where available.",
            )

        if not has_rows:
            QMessageBox.information(
                self,
                "No Unpaid Fees",
                "No active student has an unpaid due month in the selected period.",
            )

    def _accept_search_error(self, token, error_text):
        if int(token) != self._active_search_token:
            return
        self.reset_page(reset_filters=False)
        QMessageBox.critical(
            self,
            "Month Range Search Error",
            "The unpaid fee report could not be loaded."
            f"\n\n{error_text}",
        )

    def _finish_search_worker(self, token):
        self._search_workers.pop(int(token), None)
        if int(token) == self._active_search_token:
            self._active_search_token = None
            self._set_search_busy(False)

    def _page_count(self):
        if not self.report_rows:
            return 0
        return (len(self.report_rows) + self.page_size - 1) // self.page_size

    def _show_previous_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self._populate_table()

    def _show_next_page(self):
        if self.current_page < self._page_count():
            self.current_page += 1
            self._populate_table()

    def _populate_table(self):
        report = self.report or {}
        total_rows = len(self.report_rows)
        page_count = self._page_count()
        if page_count:
            self.current_page = min(max(1, self.current_page), page_count)
            start_index = (self.current_page - 1) * self.page_size
            end_index = min(start_index + self.page_size, total_rows)
            rows = self.report_rows[start_index:end_index]
        else:
            self.current_page = 1
            start_index = 0
            end_index = 0
            rows = []

        self.status_table.setUpdatesEnabled(False)
        try:
            self.status_table.clearContents()
            self.status_table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                display_name = student_service.display_name_with_stars(
                    row["student_name"],
                    row["failure_count"],
                )
                unpaid_months_text = ", ".join(row["unpaid_months"])
                values = [
                    row["class_name"],
                    row["admission_number"],
                    display_name,
                    student_service.display_roll_number(row),
                    unpaid_months_text,
                    row["unpaid_count"],
                ]
                for column_index, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(
                        Qt.AlignLeft | Qt.AlignVCenter
                        if column_index in (2, 4)
                        else Qt.AlignCenter
                    )
                    if column_index == 4:
                        item.setToolTip(unpaid_months_text)
                    self.status_table.setItem(row_index, column_index, item)
        finally:
            self.status_table.setUpdatesEnabled(True)
            self.status_table.viewport().update()

        self.previous_page_button.setEnabled(page_count > 1 and self.current_page > 1)
        self.next_page_button.setEnabled(page_count > 1 and self.current_page < page_count)
        if total_rows:
            self.page_label.setText(
                f"Showing {start_index + 1}-{end_index} of {total_rows} "
                f"(Page {self.current_page} of {page_count})"
            )
        else:
            self.page_label.setText("Showing 0 records")

        self.period_label.setText(f"Selected Period: {report.get('period_label', '-')}")
        self.students_label.setText(f"Students: {report.get('student_count', 0)}")
        self.classes_label.setText(f"Classes: {report.get('class_count', 0)}")
        self.unpaid_entries_label.setText(
            f"Unpaid Month Entries: {report.get('unpaid_month_entry_count', 0)}"
        )

    def _group_rows(self):
        groups = []
        current_name = None
        current_rows = None
        for row in self.report_rows:
            class_name = str(row.get("class_name") or "-")
            if class_name != current_name:
                current_name = class_name
                current_rows = []
                groups.append((class_name, current_rows))
            current_rows.append(row)
        return groups

    @staticmethod
    def _prepare_printer():
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        printer.setPageMargins(
            QMarginsF(8, 8, 8, 8),
            QPageLayout.Unit.Millimeter,
        )
        return printer

    def _build_page_html(
        self,
        class_chunks,
        *,
        page_number,
        total_pages,
        generated_date,
    ):
        report = self.report or {}
        sections = []
        for chunk in class_chunks:
            class_name = str(chunk["class_name"])
            continued = bool(chunk.get("continued"))
            heading = f"{class_name} (continued)" if continued else class_name
            row_html = []
            for serial_no, row in enumerate(chunk["rows"], start=chunk["serial_start"]):
                display_name = student_service.display_name_with_stars(
                    row["student_name"], row["failure_count"]
                )
                row_html.append(
                    "<tr>"
                    f"<td class='center'>{serial_no}</td>"
                    f"<td class='center'>{escape(str(row['admission_number']))}</td>"
                    f"<td>{escape(str(display_name))}</td>"
                    f"<td class='center'>{escape(student_service.display_roll_number(row))}</td>"
                    f"<td>{escape(', '.join(row['unpaid_months']))}</td>"
                    f"<td class='center'><b>{int(row['unpaid_count'])}</b></td>"
                    "</tr>"
                )
            sections.append(
                f"""
                <div class="class-block">
                    <div class="class-heading">Class: {escape(heading)}</div>
                    <table class="student-table">
                        <thead><tr>
                            <th style="width:6%;">No.</th>
                            <th style="width:18%;">Admission No.</th>
                            <th style="width:25%;">Student Name</th>
                            <th style="width:10%;">Roll No.</th>
                            <th style="width:31%;">Unpaid Months</th>
                            <th style="width:10%;">No. of Months</th>
                        </tr></thead>
                        <tbody>{''.join(row_html)}</tbody>
                    </table>
                </div>
                """
            )

        return f"""
        <html><head><style>
            body {{ font-family: Arial, Helvetica, sans-serif; font-size: 8.2pt; margin:0; color:#000; }}
            .school {{ text-align:center; font-size:14pt; font-weight:bold; margin:0; }}
            .title {{ text-align:center; font-size:11pt; font-weight:bold; margin:1px 0; }}
            .meta {{ width:100%; font-size:7.5pt; margin:1px 0 4px 0; }}
            .meta td {{ border:none; padding:0; }}
            .class-block {{ page-break-inside:avoid; margin:0 0 5px 0; }}
            .class-heading {{ background:#d9d9d9; border:0.6pt solid #555; padding:2px; font-weight:bold; }}
            table.student-table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
            th {{ background:#e7e9ee; border:0.6pt solid #555; padding:2px; text-align:center; }}
            td {{ border:0.6pt solid #555; padding:2px; vertical-align:middle; }}
            td.center {{ text-align:center; white-space:nowrap; }}
            tr {{ page-break-inside:avoid; }}
        </style></head><body>
            <div class="school">Hira Model School</div>
            <div class="title">Unpaid Fee List — Sort by Month</div>
            <table class="meta"><tr>
                <td style="text-align:left;">Season: <b>{escape(str(report.get('session_label', '-')))}</b></td>
                <td style="text-align:center;">Period: <b>{escape(str(report.get('period_label', '-')))}</b></td>
                <td style="text-align:right;">Page {page_number} of {total_pages}</td>
            </tr><tr>
                <td style="text-align:left;">Generated: {escape(generated_date)}</td>
                <td style="text-align:center;">Students: {int(report.get('student_count', 0))}</td>
                <td style="text-align:right;">Unpaid Entries: {int(report.get('unpaid_month_entry_count', 0))}</td>
            </tr></table>
            {''.join(sections)}
        </body></html>
        """

    def _document_for_chunks(self, chunks, page_size_points, *, page_number=999, total_pages=999):
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setTextWidth(page_size_points.width())
        document.setHtml(
            self._build_page_html(
                chunks,
                page_number=page_number,
                total_pages=total_pages,
                generated_date=datetime.now().strftime("%d-%m-%Y %I:%M %p"),
            )
        )
        document.setTextWidth(page_size_points.width())
        return document

    def _chunks_fit(self, chunks, page_size_points):
        document = self._document_for_chunks(chunks, page_size_points)
        return document.documentLayout().documentSize().height() <= page_size_points.height()

    def _largest_fitting_chunk(self, class_name, rows, page_size_points, serial_start):
        low = 1
        high = len(rows)
        best = 1
        while low <= high:
            middle = (low + high) // 2
            chunk = [{
                "class_name": class_name,
                "rows": rows[:middle],
                "serial_start": serial_start,
                "continued": serial_start > 1,
            }]
            if self._chunks_fit(chunk, page_size_points):
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    def _plan_report_pages(self, page_size_points):
        """Pack complete classes together and split only oversized classes."""
        pages = []
        current_page = []
        for class_name, class_rows in self._group_rows():
            full_chunk = {
                "class_name": class_name,
                "rows": class_rows,
                "serial_start": 1,
                "continued": False,
            }
            candidate = [*current_page, full_chunk]
            if self._chunks_fit(candidate, page_size_points):
                current_page = candidate
                continue

            if current_page:
                pages.append(current_page)
                current_page = []

            if self._chunks_fit([full_chunk], page_size_points):
                current_page = [full_chunk]
                continue

            remaining = list(class_rows)
            serial_start = 1
            while remaining:
                count = self._largest_fitting_chunk(
                    class_name,
                    remaining,
                    page_size_points,
                    serial_start,
                )
                pages.append([
                    {
                        "class_name": class_name,
                        "rows": remaining[:count],
                        "serial_start": serial_start,
                        "continued": serial_start > 1,
                    }
                ])
                remaining = remaining[count:]
                serial_start += count

        if current_page:
            pages.append(current_page)
        return pages

    def _render_report_to_printer(self, printer):
        if not self.report_rows:
            raise ValueError("No unpaid fee records are available.")

        page_points = printer.pageRect(QPrinter.Unit.Point)
        page_pixels = printer.pageRect(QPrinter.Unit.DevicePixel)
        if page_points.width() <= 0 or page_points.height() <= 0:
            raise OSError("The selected printer returned an invalid A4 page size.")

        pages = self._plan_report_pages(page_points.size())
        if not pages:
            raise ValueError("No report pages could be prepared.")

        painter = QPainter()
        if not painter.begin(printer):
            raise OSError("The selected printer could not be opened.")

        generated = datetime.now().strftime("%d-%m-%Y %I:%M %p")
        try:
            for page_index, chunks in enumerate(pages):
                if page_index and not printer.newPage():
                    raise OSError("The printer could not create the next page.")
                document = QTextDocument()
                document.setDocumentMargin(0)
                document.setTextWidth(page_points.width())
                document.setHtml(
                    self._build_page_html(
                        chunks,
                        page_number=page_index + 1,
                        total_pages=len(pages),
                        generated_date=generated,
                    )
                )
                document.setTextWidth(page_points.width())
                document_height = max(
                    document.documentLayout().documentSize().height(),
                    1.0,
                )
                fit_scale = min(1.0, page_points.height() / document_height)
                x_scale = page_pixels.width() / page_points.width()
                y_scale = page_pixels.height() / page_points.height()
                horizontal_offset = page_pixels.width() * (1.0 - fit_scale) / 2.0

                painter.save()
                painter.translate(page_pixels.left() + horizontal_offset, page_pixels.top())
                painter.scale(x_scale * fit_scale, y_scale * fit_scale)
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

    def _print_report(self):
        if not self.report_rows:
            QMessageBox.warning(self, "No Records", "Search for unpaid fees first.")
            return
        printer = self._prepare_printer()
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Print Unpaid Fee List")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._render_report_to_printer(printer)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Printing Error",
                "The unpaid fee list could not be printed."
                f"\n\n{error}",
            )
            return
        QMessageBox.information(
            self,
            "Sent to Printer",
            "The unpaid fee list was sent to the selected printer.",
        )

    def _download_pdf(self):
        if not self.report_rows:
            QMessageBox.warning(self, "No Records", "Search for unpaid fees first.")
            return
        report = self.report or {}
        safe_period = str(report.get("period_label", "period")).replace(" ", "_")
        safe_period = safe_period.replace("-", "_")
        default_name = f"Unpaid_Fee_List_{safe_period}_{datetime.now():%Y-%m-%d}.pdf"
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Unpaid Fee List",
            default_name,
            "PDF Files (*.pdf)",
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".pdf"):
            file_path += ".pdf"

        try:
            output_path = Path(file_path)
            previous_state = None
            if output_path.is_file():
                previous_stat = output_path.stat()
                previous_state = (previous_stat.st_size, previous_stat.st_mtime_ns)

            printer = self._prepare_printer()
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(file_path)
            self._render_report_to_printer(printer)

            if printer.printerState() == QPrinter.PrinterState.Error:
                raise OSError("The PDF printer reported an output error.")
            if not output_path.is_file() or output_path.stat().st_size <= 0:
                raise OSError("The PDF output file was not created correctly.")
            current_stat = output_path.stat()
            current_state = (current_stat.st_size, current_stat.st_mtime_ns)
            if previous_state is not None and current_state == previous_state:
                raise OSError("The existing PDF file was not updated.")
        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                "The unpaid fee list PDF could not be created."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "PDF Downloaded",
            "The unpaid fee list was saved successfully."
            f"\n\n{file_path}",
        )
