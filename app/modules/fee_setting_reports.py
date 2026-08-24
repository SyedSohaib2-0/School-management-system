"""Printable sibling-family and individual-fee records for Fee Setting."""

from __future__ import annotations

from html import escape

from PySide6.QtCore import QMarginsF, QRectF
from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import navigation
from app.services import fee_setting_report_service


def _money_text(value) -> str:
    if value is None:
        return "Fee Not Set"
    return f"Rs. {float(value):,.2f}"


def _percentage_text(value) -> str:
    if value is None:
        return "Fixed"
    return f"{float(value):g}%"


class _PrintableFeeSettingReportPage(QWidget):
    """Shared A4 printing and measured page-packing behavior."""

    report_title = "Fee Setting Report"

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.report: dict | None = None
        self.report_items: list = []

    @staticmethod
    def _prepare_printer() -> QPrinter:
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        printer.setPageMargins(
            QMarginsF(8, 8, 8, 8),
            QPageLayout.Unit.Millimeter,
        )
        return printer

    def _build_page_html(self, items, page_number: int, total_pages: int) -> str:
        raise NotImplementedError

    def _page_document(self, items, page_size, page_number=999, total_pages=999):
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setTextWidth(page_size.width())
        document.setHtml(
            self._build_page_html(items, page_number, total_pages)
        )
        document.setTextWidth(page_size.width())
        return document

    def _items_fit(self, items, page_size) -> bool:
        document = self._page_document(items, page_size)
        return document.documentLayout().documentSize().height() <= page_size.height()

    def _plan_pages(self, page_size):
        """Keep each report item whole; sibling pages pass one family per item."""
        pages = []
        current_page = []
        for item in self.report_items:
            candidate = [*current_page, item]
            if self._items_fit(candidate, page_size):
                current_page = candidate
                continue
            if current_page:
                pages.append(current_page)
            # An unusually large single family is still kept on one page. The
            # renderer scales that page down instead of splitting the family.
            current_page = [item]
        if current_page:
            pages.append(current_page)
        return pages

    def _render_report_to_printer(self, printer: QPrinter) -> None:
        if not self.report_items:
            raise ValueError("No report records are available.")
        page_points = printer.pageRect(QPrinter.Unit.Point)
        page_pixels = printer.pageRect(QPrinter.Unit.DevicePixel)
        if page_points.width() <= 0 or page_points.height() <= 0:
            raise OSError("The selected printer returned an invalid A4 page size.")
        pages = self._plan_pages(page_points.size())
        if not pages:
            raise ValueError("No report pages could be prepared.")

        painter = QPainter()
        if not painter.begin(printer):
            raise OSError("The selected printer could not be opened.")
        try:
            for page_index, items in enumerate(pages):
                if page_index and not printer.newPage():
                    raise OSError("The printer could not create the next report page.")
                document = self._page_document(
                    items,
                    page_points.size(),
                    page_index + 1,
                    len(pages),
                )
                document_height = max(
                    document.documentLayout().documentSize().height(), 1.0
                )
                fit_scale = min(1.0, page_points.height() / document_height)
                x_scale = page_pixels.width() / page_points.width()
                y_scale = page_pixels.height() / page_points.height()
                horizontal_offset = page_pixels.width() * (1.0 - fit_scale) / 2.0

                # Some printer drivers retain a device transform after
                # newPage(). Reset it so later pages start at the same origin
                # as page one instead of drifting upward.
                painter.resetTransform()
                painter.save()
                painter.translate(
                    page_pixels.left() + horizontal_offset, page_pixels.top()
                )
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

    def _print_report(self) -> None:
        if not self.report_items:
            QMessageBox.warning(self, "No Records", "No records are available to print.")
            return
        printer = self._prepare_printer()
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle(f"Print {self.report_title}")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._render_report_to_printer(printer)
        except Exception as error:  # noqa: BLE001 - show printer failures in the UI
            QMessageBox.critical(
                self,
                "Printing Error",
                f"The {self.report_title.lower()} could not be printed.\n\n{error}",
            )
            return
        QMessageBox.information(
            self,
            "Sent to Printer",
            f"The {self.report_title} was sent to the selected printer.",
        )

    def _back_to_set_fee(self) -> None:
        self.main_window.go_to_page(navigation.PAGE_SET_FEE_DASHBOARD)


class SiblingFeeRecordPage(_PrintableFeeSettingReportPage):
    """Current sibling policy records grouped into indivisible families."""

    report_title = "Sibling Fee Record"

    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(self.report_title)
        title.setObjectName("pageTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        back = QPushButton("Back to Set Fee")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self._back_to_set_fee)
        title_row.addWidget(back)
        layout.addLayout(title_row)

        info = QLabel(
            "All current active sibling families are shown together. During printing, "
            "a family is moved to the next page when the whole family cannot fit."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        actions = QHBoxLayout()
        self.summary_label = QLabel("Families: 0 | Sibling Students: 0")
        self.summary_label.setStyleSheet("font-weight: 700;")
        actions.addWidget(self.summary_label)
        actions.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setObjectName("secondaryButton")
        refresh.clicked.connect(self.reload_report)
        actions.addWidget(refresh)
        self.print_button = QPushButton("Print Sibling Record")
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self._print_report)
        actions.addWidget(self.print_button)
        layout.addLayout(actions)

        self.table = QTableWidget()
        self.table.setColumnCount(11)
        self.table.setHorizontalHeaderLabels(
            [
                "Family", "Father Name", "Father CNIC", "Father Contact",
                "Student Name", "Admission No.", "Class", "Roll No.",
                "Sibling Role", "Sibling %", "Current Fee",
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.table)

    def reset_page(self) -> None:
        self.reload_report()

    def reload_report(self) -> None:
        try:
            self.report = fee_setting_report_service.get_sibling_fee_report_snapshot()
        except Exception as error:  # noqa: BLE001 - show report failures in the UI
            self.report = None
            self.report_items = []
            self.table.setRowCount(0)
            self.print_button.setEnabled(False)
            QMessageBox.critical(
                self, "Sibling Record Error", f"Sibling records could not be loaded.\n\n{error}"
            )
            return

        self.report_items = list(self.report["families"])
        self.summary_label.setText(
            f"Families: {self.report['family_count']} | "
            f"Sibling Students: {self.report['student_count']} | "
            f"As of: {self.report['as_of_label']}"
        )
        flattened = [
            (family, student)
            for family in self.report_items
            for student in family["students"]
        ]
        self.table.setRowCount(len(flattened))
        for row_index, (family, student) in enumerate(flattened):
            values = [
                family["family_number"], family["father_name"], family["father_cnic"],
                family["father_contact_number"], student["student_name"],
                student["admission_number"], student["class_name"],
                student["roll_number"], student["assignment_role"],
                _percentage_text(student["sibling_percentage"]),
                _money_text(student["current_fee"]),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.print_button.setEnabled(bool(self.report_items))

    def _build_page_html(self, families, page_number: int, total_pages: int) -> str:
        report = self.report or {}
        blocks = []
        for family in families:
            student_rows = []
            for index, student in enumerate(family["students"], start=1):
                student_rows.append(
                    "<tr>"
                    f"<td class='center'>{index}</td>"
                    f"<td>{escape(student['student_name'])}</td>"
                    f"<td class='center'>{escape(student['admission_number'])}</td>"
                    f"<td class='center'>{escape(student['class_name'])}</td>"
                    f"<td class='center'>{escape(student['roll_number'])}</td>"
                    f"<td class='center'>{escape(student['assignment_role'].title())}</td>"
                    f"<td class='center'>{escape(_percentage_text(student['sibling_percentage']))}</td>"
                    f"<td class='right'>{escape(_money_text(student['current_fee']))}</td>"
                    "</tr>"
                )
            blocks.append(
                f"""
                <div class="family-block">
                    <div class="family-title">Family {int(family['family_number'])}</div>
                    <table class="family-meta"><tr>
                        <td><b>Father Name:</b> {escape(family['father_name'])}</td>
                        <td><b>Father CNIC:</b> {escape(family['father_cnic'])}</td>
                        <td><b>Father Contact:</b> {escape(family['father_contact_number'])}</td>
                    </tr></table>
                    <table class="students">
                        <thead><tr>
                            <th style="width:5%;">No.</th><th style="width:24%;">Student Name</th>
                            <th style="width:14%;">Admission No.</th><th style="width:9%;">Class</th>
                            <th style="width:8%;">Roll</th><th style="width:12%;">Role</th>
                            <th style="width:10%;">Sibling %</th><th style="width:18%;">Current Fee</th>
                        </tr></thead><tbody>{''.join(student_rows)}</tbody>
                    </table>
                </div>
                """
            )
        return f"""
        <html><head><style>
            body {{ font-family:Arial,Helvetica,sans-serif; font-size:8.3pt; color:#000; margin:0; background:#fff; }}
            h1 {{ text-align:center; font-size:15pt; margin:0; }}
            h2 {{ text-align:center; font-size:11pt; margin:2px 0 5px 0; }}
            .report-meta {{ width:100%; font-size:8pt; margin-bottom:6px; }}
            .report-meta td {{ border:none; padding:1px; }}
            .family-block {{ page-break-inside:avoid; margin:0 0 8px 0; }}
            .family-title {{ background:#fff; color:#000; border:1pt solid #000; font-weight:bold; padding:4px; }}
            .family-meta {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
            .family-meta td {{ border:0.6pt solid #000; padding:4px; width:33.33%; }}
            .students {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
            .students th {{ background:#fff; border:0.6pt solid #000; padding:3px 2px; }}
            .students td {{ background:#fff; border:0.6pt solid #000; padding:3px 2px; vertical-align:middle; }}
            .center {{ text-align:center; }} .right {{ text-align:right; white-space:nowrap; }}
            tr {{ page-break-inside:avoid; }}
        </style></head><body>
            <h1>Hira Model School</h1><h2>Sibling Fee Record</h2>
            <table class="report-meta"><tr>
                <td>As of: <b>{escape(str(report.get('as_of_label', '-')))}</b></td>
                <td style="text-align:center;">Families: {int(report.get('family_count', 0))} | Students: {int(report.get('student_count', 0))}</td>
                <td style="text-align:right;">Page {page_number} of {total_pages}</td>
            </tr><tr><td colspan="3">Generated: {escape(str(report.get('generated_at', '-')))}</td></tr></table>
            {''.join(blocks)}
        </body></html>
        """


class IndividualFeeRecordPage(_PrintableFeeSettingReportPage):
    """Current individual-fee rules with class and effective fee amounts."""

    report_title = "Individual Fee Set Record"

    @staticmethod
    def _prepare_printer() -> QPrinter:
        """Use landscape so names and both fee amounts remain easy to read."""
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        printer.setPageMargins(
            QMarginsF(8, 8, 8, 8),
            QPageLayout.Unit.Millimeter,
        )
        return printer

    def __init__(self, main_window) -> None:
        super().__init__(main_window)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(self.report_title)
        title.setObjectName("pageTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        back = QPushButton("Back to Set Fee")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self._back_to_set_fee)
        title_row.addWidget(back)
        layout.addLayout(title_row)

        info = QLabel(
            "Students with a currently applicable individual fee are listed with "
            "their normal class fee and current individual fee."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        actions = QHBoxLayout()
        self.summary_label = QLabel("Students with Individual Fee: 0")
        self.summary_label.setStyleSheet("font-weight: 700;")
        actions.addWidget(self.summary_label)
        actions.addStretch()
        refresh = QPushButton("Refresh")
        refresh.setObjectName("secondaryButton")
        refresh.clicked.connect(self.reload_report)
        actions.addWidget(refresh)
        self.print_button = QPushButton("Print Individual Fee Record")
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self._print_report)
        actions.addWidget(self.print_button)
        layout.addLayout(actions)

        self.table = QTableWidget()
        self.table.setColumnCount(12)
        self.table.setHorizontalHeaderLabels(
            [
                "Student Name", "Tracking ID", "Admission No.", "Class", "Roll No.",
                "Status", "Individual %", "Class Fee", "Current Individual Fee",
                "Effective From", "Effective To", "Difference",
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table)

    def reset_page(self) -> None:
        self.reload_report()

    def reload_report(self) -> None:
        try:
            self.report = fee_setting_report_service.get_individual_fee_report_snapshot()
        except Exception as error:  # noqa: BLE001 - show report failures in the UI
            self.report = None
            self.report_items = []
            self.table.setRowCount(0)
            self.print_button.setEnabled(False)
            QMessageBox.critical(
                self,
                "Individual Fee Record Error",
                f"Individual fee records could not be loaded.\n\n{error}",
            )
            return

        self.report_items = list(self.report["students"])
        self.summary_label.setText(
            f"Students with Individual Fee: {self.report['student_count']} | "
            f"As of: {self.report['as_of_label']}"
        )
        self.table.setRowCount(len(self.report_items))
        for row_index, student in enumerate(self.report_items):
            difference = (
                None
                if student["class_fee"] is None or student["individual_fee"] is None
                else student["class_fee"] - student["individual_fee"]
            )
            values = [
                student["student_name"], student["tracking_id"],
                student["admission_number"], student["class_name"],
                student["roll_number"], student["student_status"],
                _percentage_text(student["monthly_fee_percentage"]),
                _money_text(student["class_fee"]), _money_text(student["individual_fee"]),
                student["effective_from"], student["effective_to"],
                _money_text(difference),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row_index, column, QTableWidgetItem(str(value)))
        self.print_button.setEnabled(bool(self.report_items))

    def _build_page_html(self, students, page_number: int, total_pages: int) -> str:
        report = self.report or {}
        rows = []
        for index, student in enumerate(students, start=1):
            rows.append(
                "<tr>"
                f"<td class='center'>{index}</td>"
                f"<td>{escape(student['student_name'])}</td>"
                f"<td class='center'>{escape(student['admission_number'])}</td>"
                f"<td class='center'>{escape(student['class_name'])}</td>"
                f"<td class='center'>{escape(student['roll_number'])}</td>"
                f"<td class='center'>{escape(_percentage_text(student['monthly_fee_percentage']))}</td>"
                f"<td class='right'>{escape(_money_text(student['class_fee']))}</td>"
                f"<td class='right'>{escape(_money_text(student['individual_fee']))}</td>"
                f"<td class='center'>{escape(student['effective_from'])}</td>"
                f"<td class='center'>{escape(student['effective_to'])}</td>"
                "</tr>"
            )
        return f"""
        <html><head><style>
            body {{ font-family:Arial,Helvetica,sans-serif; font-size:7.7pt; color:#000; margin:0; background:#fff; }}
            h1 {{ text-align:center; font-size:15pt; margin:0; }}
            h2 {{ text-align:center; font-size:11pt; margin:2px 0 5px 0; }}
            .meta {{ width:100%; font-size:8pt; margin-bottom:6px; }}
            .meta td {{ border:none; padding:1px; }}
            .records {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
            .records th {{ background:#fff; border:0.6pt solid #000; padding:3px 1px; text-align:center; }}
            .records td {{ background:#fff; border:0.6pt solid #000; padding:3px 2px; vertical-align:middle; }}
            .center {{ text-align:center; }} .right {{ text-align:right; white-space:nowrap; }}
            tr {{ page-break-inside:avoid; }}
        </style></head><body>
            <h1>Hira Model School</h1><h2>Individual Fee Set Record</h2>
            <table class="meta"><tr>
                <td>As of: <b>{escape(str(report.get('as_of_label', '-')))}</b></td>
                <td style="text-align:center;">Students: {int(report.get('student_count', 0))}</td>
                <td style="text-align:right;">Page {page_number} of {total_pages}</td>
            </tr><tr><td colspan="3">Generated: {escape(str(report.get('generated_at', '-')))}</td></tr></table>
            <table class="records"><thead><tr>
                <th style="width:4%;">No.</th><th style="width:19%;">Student Name</th>
                <th style="width:11%;">Admission</th><th style="width:7%;">Class</th>
                <th style="width:6%;">Roll</th><th style="width:9%;">Individual %</th>
                <th style="width:11%;">Class Fee</th><th style="width:12%;">Individual Fee</th>
                <th style="width:11%;">From</th><th style="width:10%;">To</th>
            </tr></thead><tbody>{''.join(rows)}</tbody></table>
        </body></html>
        """
