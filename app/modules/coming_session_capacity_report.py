"""Statistics subpage for the class-level Coming Session capacity report."""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QMarginsF
from PySide6.QtGui import QColor, QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app import navigation
from app.services import coming_session_capacity_report_service as report_service


class ComingSessionCapacityReportPage(QWidget):
    """Editable class-capacity summary without individual student details."""

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.report: dict | None = None
        self.report_rows: list[dict] = []
        self.capacity_inputs: dict[int, QSpinBox] = {}
        self._dirty_class_ids: set[int] = set()
        self._loading = False
        self._loaded_base_year: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 14, 20, 18)
        layout.setSpacing(9)

        title_row = QHBoxLayout()
        title_label = QLabel("Coming Session Capacity Report")
        title_label.setObjectName("pageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch()

        back_button = QPushButton("Back to Statistics")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(self._return_to_statistics)
        title_row.addWidget(back_button)
        layout.addLayout(title_row)

        instruction = QLabel(
            "Select the base academic session. The report shows the immediately "
            "following Coming Session, class by class. Class Capacity is editable "
            "here and is saved for future use."
        )
        instruction.setWordWrap(True)
        instruction.setStyleSheet("color: #667085;")
        layout.addWidget(instruction)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)
        filter_row.addWidget(QLabel("Base Academic Session:"))
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(150)
        self.session_combo.currentIndexChanged.connect(self._on_session_changed)
        filter_row.addWidget(self.session_combo)

        self.coming_session_label = QLabel("Coming Session: -")
        self.coming_session_label.setStyleSheet("font-weight: 700; color: #3157D5;")
        filter_row.addWidget(self.coming_session_label)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.clicked.connect(self._refresh_clicked)
        filter_row.addWidget(self.refresh_button)

        self.save_button = QPushButton("Save Capacities")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._save_capacities)
        filter_row.addWidget(self.save_button)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(18)
        self.classes_label = QLabel("Classes: 0")
        self.coming_total_label = QLabel("New Coming Students: 0")
        self.active_total_label = QLabel("Existing Active Students: 0")
        self.expected_total_label = QLabel("Expected Total: 0")
        self.over_capacity_label = QLabel("Over Capacity Classes: 0")
        for label in (
            self.classes_label,
            self.coming_total_label,
            self.active_total_label,
            self.expected_total_label,
            self.over_capacity_label,
        ):
            label.setStyleSheet("font-weight: 700;")
            summary_row.addWidget(label)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        action_row = QHBoxLayout()
        self.print_button = QPushButton("Print")
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(self._print_report)
        action_row.addWidget(self.print_button)

        self.download_button = QPushButton("Download PDF")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._download_pdf)
        action_row.addWidget(self.download_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        self.status_label = QLabel("Select a base session to load the report.")
        self.status_label.setStyleSheet("color: #667085; font-size: 10px;")
        layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                "Target Class",
                "New Coming Students",
                "Existing Active Students",
                "Class Capacity",
                "Expected Total",
                "Available Seats",
                "Status",
            ]
        )
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setMinimumHeight(420)
        layout.addWidget(self.table, 1)

    def reset_page(self, base_session_start_year: int | None = None) -> None:
        """Reload session choices and the requested base-session report."""
        self._loading = True
        try:
            years = report_service.get_available_base_session_years()
            if not years:
                years = [base_session_start_year] if base_session_start_year else []
            desired = (
                int(base_session_start_year)
                if base_session_start_year is not None
                else self._loaded_base_year
            )
            if desired not in years:
                desired = years[0] if years else None

            self.session_combo.clear()
            for year in years:
                self.session_combo.addItem(f"{year}-{year + 1}", int(year))
            if desired is not None:
                index = self.session_combo.findData(int(desired))
                if index >= 0:
                    self.session_combo.setCurrentIndex(index)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Coming Session Report Could Not Be Loaded",
                "The available academic sessions could not be loaded."
                f"\n\n{error}",
            )
            return
        finally:
            self._loading = False

        if self.session_combo.currentData() is not None:
            self._load_report(int(self.session_combo.currentData()))
        else:
            self._clear_report()

    def _clear_report(self) -> None:
        self.report = None
        self.report_rows = []
        self.capacity_inputs = {}
        self._dirty_class_ids.clear()
        self.table.setRowCount(0)
        self.coming_session_label.setText("Coming Session: -")
        self.classes_label.setText("Classes: 0")
        self.coming_total_label.setText("New Coming Students: 0")
        self.active_total_label.setText("Existing Active Students: 0")
        self.expected_total_label.setText("Expected Total: 0")
        self.over_capacity_label.setText("Over Capacity Classes: 0")
        self.save_button.setEnabled(False)
        self.print_button.setEnabled(False)
        self.download_button.setEnabled(False)

    def _on_session_changed(self, _index=None) -> None:
        if self._loading:
            return
        selected = self.session_combo.currentData()
        if selected is None:
            return
        if self._dirty_class_ids and not self._save_capacities(show_success=False):
            self._loading = True
            try:
                old_index = self.session_combo.findData(self._loaded_base_year)
                if old_index >= 0:
                    self.session_combo.setCurrentIndex(old_index)
            finally:
                self._loading = False
            return
        self._load_report(int(selected))

    def _refresh_clicked(self) -> None:
        if self._dirty_class_ids and not self._save_capacities(show_success=False):
            return
        selected = self.session_combo.currentData()
        if selected is not None:
            self._load_report(int(selected))

    def _load_report(self, base_year: int) -> None:
        try:
            report = report_service.get_coming_session_capacity_report(base_year)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Coming Session Report Could Not Be Loaded",
                "The class-capacity report could not be prepared."
                f"\n\n{error}",
            )
            return

        self.report = report
        self.report_rows = list(report["rows"])
        self._loaded_base_year = int(report["base_session_start_year"])
        self._dirty_class_ids.clear()
        self.save_button.setEnabled(False)
        self.coming_session_label.setText(
            f"Coming Session: {report['coming_session_label']}"
        )
        self.status_label.setText(
            "Existing Active Students are reconstructed at the end of "
            f"{report['base_session_label']}. Refreshed {report['generated_at']}."
        )
        self._populate_table()
        self._update_summary()
        has_rows = bool(self.report_rows)
        self.print_button.setEnabled(has_rows)
        self.download_button.setEnabled(has_rows)

    def _populate_table(self) -> None:
        self._loading = True
        try:
            self.capacity_inputs = {}
            self.table.setRowCount(len(self.report_rows))
            for row_index, row in enumerate(self.report_rows):
                class_id = int(row["class_id"])
                class_item = QTableWidgetItem(str(row["class_name"]))
                if not row.get("is_active", True):
                    class_item.setToolTip("This class is currently Inactive but is included for historical reporting.")
                self.table.setItem(row_index, 0, class_item)
                self._set_center_number(row_index, 1, row["new_coming_students"])
                self._set_center_number(row_index, 2, row["existing_active_students"])

                capacity_input = QSpinBox()
                capacity_input.setRange(int(row.get("minimum_capacity", 1)), 1000)
                capacity_input.setValue(int(row["class_capacity"]))
                capacity_input.setAlignment(Qt.AlignCenter)
                capacity_input.setButtonSymbols(
                    QAbstractSpinBox.ButtonSymbols.NoButtons
                )
                capacity_input.setFrame(False)
                capacity_input.setMinimumWidth(76)
                capacity_input.setFixedHeight(30)
                capacity_input.setStyleSheet(
                    "QSpinBox {"
                    " background: transparent;"
                    " border: none;"
                    " padding: 2px 8px;"
                    "}"
                    "QSpinBox:focus {"
                    " background: #FFFFFF;"
                    " border: 1px solid #3157D5;"
                    " border-radius: 4px;"
                    "}"
                )
                capacity_input.setToolTip(
                    "Enter the class capacity. The value is saved for future reports."
                )
                capacity_input.valueChanged.connect(
                    lambda value, cid=class_id: self._capacity_changed(cid, value)
                )
                self.table.setCellWidget(row_index, 3, capacity_input)
                self.capacity_inputs[class_id] = capacity_input

                self._set_center_number(row_index, 4, row["expected_total"])
                self._set_center_number(row_index, 5, row["available_seats"])
                self._set_status_item(row_index, str(row["status"]))
        finally:
            self._loading = False

    def _set_center_number(self, row_index: int, column: int, value: int) -> None:
        item = QTableWidgetItem(f"{int(value):,}")
        item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row_index, column, item)

    def _set_status_item(self, row_index: int, status: str) -> None:
        item = QTableWidgetItem(status)
        item.setTextAlignment(Qt.AlignCenter)
        if status == "Over Capacity":
            item.setForeground(QColor("#B42318"))
            item.setBackground(QColor("#FEE4E2"))
        else:
            item.setForeground(QColor("#137A43"))
            item.setBackground(QColor("#ECFDF3"))
        self.table.setItem(row_index, 6, item)

    def _capacity_changed(self, class_id: int, value: int) -> None:
        if self._loading:
            return
        row_index = next(
            (
                index
                for index, row in enumerate(self.report_rows)
                if int(row["class_id"]) == int(class_id)
            ),
            None,
        )
        if row_index is None:
            return
        row = self.report_rows[row_index]
        row["class_capacity"] = int(value)
        row["available_seats"] = int(value) - int(row["expected_total"])
        row["status"] = (
            "Over Capacity" if int(row["available_seats"]) < 0 else "Available"
        )
        self._set_center_number(row_index, 5, row["available_seats"])
        self._set_status_item(row_index, row["status"])
        self._dirty_class_ids.add(int(class_id))
        self.save_button.setEnabled(True)
        self._update_summary()

    def _update_summary(self) -> None:
        self.classes_label.setText(f"Classes: {len(self.report_rows):,}")
        self.coming_total_label.setText(
            "New Coming Students: "
            f"{sum(int(row['new_coming_students']) for row in self.report_rows):,}"
        )
        self.active_total_label.setText(
            "Existing Active Students: "
            f"{sum(int(row['existing_active_students']) for row in self.report_rows):,}"
        )
        self.expected_total_label.setText(
            "Expected Total: "
            f"{sum(int(row['expected_total']) for row in self.report_rows):,}"
        )
        over_count = sum(
            1 for row in self.report_rows if row["status"] == "Over Capacity"
        )
        self.over_capacity_label.setText(f"Over Capacity Classes: {over_count:,}")
        self.over_capacity_label.setStyleSheet(
            "font-weight: 700; color: #B42318;" if over_count else "font-weight: 700;"
        )

    def _save_capacities(self, _checked=False, *, show_success=True) -> bool:
        if not self._dirty_class_ids:
            return True
        payload = {
            class_id: int(self.capacity_inputs[class_id].value())
            for class_id in sorted(self._dirty_class_ids)
        }
        try:
            saved = report_service.save_class_capacities(payload)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Class Capacities Could Not Be Saved",
                "No capacity change was saved."
                f"\n\n{error}",
            )
            return False

        for row in self.report_rows:
            class_id = int(row["class_id"])
            if class_id in saved:
                row["class_capacity"] = int(saved[class_id])
        self._dirty_class_ids.clear()
        self.save_button.setEnabled(False)
        if show_success:
            QMessageBox.information(
                self,
                "Capacities Saved",
                "The class capacities were saved successfully for future use.",
            )
        return True

    def _return_to_statistics(self) -> None:
        if self._dirty_class_ids and not self._save_capacities(show_success=False):
            return
        self.main_window.application_shell.set_active_module(
            "statistics", "Statistics"
        )
        self.main_window.go_to_page(navigation.PAGE_STATISTICS_DASHBOARD)

    @staticmethod
    def _prepare_printer() -> QPrinter:
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        printer.setPageMargins(
            QMarginsF(8, 8, 8, 8),
            QPageLayout.Unit.Millimeter,
        )
        return printer

    def _build_report_html(self) -> str:
        report = self.report or {}
        rows_html = []
        for row in self.report_rows:
            status_style = "color:#000000;font-weight:bold;"
            rows_html.append(
                "<tr>"
                f"<td>{escape(str(row['class_name']))}</td>"
                f"<td class='num'>{int(row['new_coming_students'])}</td>"
                f"<td class='num'>{int(row['existing_active_students'])}</td>"
                f"<td class='num'>{int(row['class_capacity'])}</td>"
                f"<td class='num'>{int(row['expected_total'])}</td>"
                f"<td class='num'>{int(row['available_seats'])}</td>"
                f"<td style='{status_style}'>{escape(str(row['status']))}</td>"
                "</tr>"
            )
        return f"""
        <html><head><style>
            body {{ font-family: Arial, Helvetica, sans-serif; font-size: 8.5pt; color:#000; }}
            h1 {{ text-align:center; font-size:15pt; margin:0; }}
            h2 {{ text-align:center; font-size:11pt; margin:2px 0 7px 0; }}
            .meta {{ width:100%; margin-bottom:6px; font-size:8pt; }}
            .meta td {{ border:none; padding:1px; }}
            table.report {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
            table.report th {{ background:#FFFFFF; border:0.7pt solid #000; padding:4px 2px; text-align:center; }}
            table.report td {{ background:#FFFFFF; border:0.7pt solid #000; padding:4px 2px; text-align:center; }}
            table.report td:first-child {{ text-align:left; font-weight:bold; }}
            .num {{ white-space:nowrap; }}
            tr {{ page-break-inside:avoid; }}
        </style></head><body>
            <h1>Hira Model School</h1>
            <h2>Coming Session Class Capacity Report</h2>
            <table class="meta"><tr>
                <td>Base Session: <b>{escape(str(report.get('base_session_label', '-')))}</b></td>
                <td style="text-align:center;">Coming Session: <b>{escape(str(report.get('coming_session_label', '-')))}</b></td>
                <td style="text-align:right;">Generated: {escape(datetime.now().strftime('%d-%m-%Y %I:%M %p'))}</td>
            </tr><tr>
                <td>Classes: {len(self.report_rows)}</td>
                <td style="text-align:center;">New Coming Students: {sum(int(row['new_coming_students']) for row in self.report_rows)}</td>
                <td style="text-align:right;">Over Capacity Classes: {sum(1 for row in self.report_rows if row['status'] == 'Over Capacity')}</td>
            </tr></table>
            <table class="report">
                <thead><tr>
                    <th style="width:15%;">Target Class</th>
                    <th style="width:14%;">New Coming Students</th>
                    <th style="width:17%;">Existing Active Students</th>
                    <th style="width:13%;">Class Capacity</th>
                    <th style="width:13%;">Expected Total</th>
                    <th style="width:13%;">Available Seats</th>
                    <th style="width:15%;">Status</th>
                </tr></thead>
                <tbody>{''.join(rows_html)}</tbody>
            </table>
        </body></html>
        """

    def _document(self) -> QTextDocument:
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setHtml(self._build_report_html())
        return document

    def _ensure_ready_for_output(self) -> bool:
        if not self.report_rows:
            QMessageBox.warning(self, "No Records", "No class rows are available to print.")
            return False
        if self._dirty_class_ids and not self._save_capacities(show_success=False):
            return False
        return True

    def _print_report(self) -> None:
        if not self._ensure_ready_for_output():
            return
        printer = self._prepare_printer()
        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Print Coming Session Capacity Report")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._document().print_(printer)
        except Exception as error:
            QMessageBox.critical(
                self,
                "Printing Error",
                "The Coming Session capacity report could not be printed."
                f"\n\n{error}",
            )
            return
        QMessageBox.information(
            self,
            "Sent to Printer",
            "The Coming Session capacity report was sent to the selected printer.",
        )

    def _download_pdf(self) -> None:
        if not self._ensure_ready_for_output():
            return
        report = self.report or {}
        default_name = (
            "Coming_Session_Capacity_"
            f"{str(report.get('coming_session_label', 'Report')).replace('-', '_')}_"
            f"{datetime.now():%Y-%m-%d}.pdf"
        )
        file_path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Coming Session Capacity Report",
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
                stat = output_path.stat()
                previous_state = (stat.st_size, stat.st_mtime_ns)
            printer = self._prepare_printer()
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(file_path)
            self._document().print_(printer)
            if printer.printerState() == QPrinter.PrinterState.Error:
                raise OSError("The PDF printer reported an output error.")
            if not output_path.is_file() or output_path.stat().st_size <= 0:
                raise OSError("The PDF output file was not created correctly.")
            stat = output_path.stat()
            current_state = (stat.st_size, stat.st_mtime_ns)
            if previous_state is not None and current_state == previous_state:
                raise OSError("The existing PDF file was not updated.")
        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                "The Coming Session capacity report PDF could not be created."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "PDF Downloaded",
            "The Coming Session capacity report was saved successfully."
            f"\n\n{file_path}",
        )
