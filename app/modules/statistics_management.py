"""Simple, read-only school Statistics dashboard."""

from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
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
from app.services import statistics_service, student_service


class StatisticsKpiCard(QFrame):
    """Compact KPI card with a headline value and scope label."""

    def __init__(
        self,
        title: str,
        scope: str,
        *,
        background: str,
        foreground: str,
    ) -> None:
        super().__init__()
        self.setMinimumHeight(86)
        self.setStyleSheet(
            f"""
            StatisticsKpiCard {{
                background-color: {background};
                border: 1px solid #DCE3EF;
                border-radius: 9px;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(
            "font-size: 11px; font-weight: 700; color: #44516A;"
        )
        layout.addWidget(title_label)

        self.value_label = QLabel("0")
        self.value_label.setAlignment(Qt.AlignCenter)
        self.value_label.setStyleSheet(
            f"font-size: 18px; font-weight: 800; color: {foreground};"
        )
        layout.addWidget(self.value_label)

        scope_label = QLabel(scope)
        scope_label.setAlignment(Qt.AlignCenter)
        scope_label.setStyleSheet(
            "font-size: 10px; color: #667085;"
        )
        layout.addWidget(scope_label)

    def set_count(self, value) -> None:
        self.value_label.setText(f"{int(value):,}")

    def set_amount(self, value) -> None:
        self.value_label.setText(f"Rs. {float(value):,.2f}")


class StatisticsManagementPage(QWidget):
    """Session-filtered statistics using read-only service snapshots."""

    def __init__(self, main_window) -> None:
        super().__init__()
        self.main_window = main_window
        self.current_snapshot = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 14, 20, 16)
        main_layout.setSpacing(8)

        heading_row = QHBoxLayout()
        heading_text = QVBoxLayout()
        title_label = QLabel("School Statistics")
        title_label.setObjectName("pageTitle")
        heading_text.addWidget(title_label)

        subtitle_label = QLabel(
            "A read-only overview of admissions and financial activity "
            "for the selected April-to-March academic session."
        )
        subtitle_label.setStyleSheet("color: #667085;")
        heading_text.addWidget(subtitle_label)
        heading_row.addLayout(heading_text)
        heading_row.addStretch()

        heading_row.addWidget(QLabel("Academic Session:"))
        self.session_combo = QComboBox()
        self.session_combo.setMinimumWidth(140)
        self.session_combo.currentIndexChanged.connect(
            self._load_statistics
        )
        heading_row.addWidget(self.session_combo)

        refresh_button = QPushButton("Refresh")
        refresh_button.setObjectName("secondaryButton")
        refresh_button.clicked.connect(self.reset_page)
        heading_row.addWidget(refresh_button)
        main_layout.addLayout(heading_row)

        self.freshness_label = QLabel("Not refreshed yet")
        self.freshness_label.setAlignment(Qt.AlignRight)
        self.freshness_label.setStyleSheet(
            "font-size: 10px; color: #667085;"
        )
        main_layout.addWidget(self.freshness_label)

        card_row = QHBoxLayout()
        card_row.setSpacing(9)
        self.school_strength_card = StatisticsKpiCard(
            "School Strength",
            "Current active students",
            background="#EEF2FF",
            foreground="#3157D5",
        )
        self.admissions_card = StatisticsKpiCard(
            "Session Admissions",
            "Selected admission cohort",
            background="#ECFDF3",
            foreground="#137A43",
        )
        self.collected_card = StatisticsKpiCard(
            "Fee Collected",
            "Received during session",
            background="#EFF8FF",
            foreground="#1769AA",
        )
        self.expenses_card = StatisticsKpiCard(
            "Total Expenses",
            "Recorded during session",
            background="#FFF7ED",
            foreground="#B45309",
        )
        self.net_balance_card = StatisticsKpiCard(
            "Net Balance",
            "Collected fee minus expenses",
            background="#F5F3FF",
            foreground="#6D28D9",
        )

        for card in (
            self.school_strength_card,
            self.admissions_card,
            self.collected_card,
            self.expenses_card,
            self.net_balance_card,
        ):
            card_row.addWidget(card, 1)
        main_layout.addLayout(card_row)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(12)

        cohort_frame = QFrame()
        cohort_frame.setStyleSheet(
            "QFrame { background: #FFFFFF; border: 1px solid #DCE3EF; "
            "border-radius: 8px; }"
        )
        cohort_layout = QVBoxLayout(cohort_frame)
        cohort_layout.setContentsMargins(10, 8, 10, 10)
        cohort_title = QLabel(
            "Selected Admission Cohort - Current Status"
        )
        cohort_title.setStyleSheet(
            "font-size: 13px; font-weight: 700; color: #17233D;"
        )
        cohort_layout.addWidget(cohort_title)

        self.status_table = QTableWidget()
        self.status_table.setColumnCount(2)
        self.status_table.setHorizontalHeaderLabels(
            ["Current Status", "Students"]
        )
        self.status_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.status_table.setSelectionMode(QTableWidget.SingleSelection)
        self.status_table.setMinimumWidth(320)
        self.status_table.cellClicked.connect(
            self._open_coming_session_report_from_row
        )
        cohort_layout.addWidget(self.status_table)
        detail_row.addWidget(cohort_frame, 2)

        chart_frame = QFrame()
        chart_frame.setStyleSheet(
            "QFrame { background: #FFFFFF; border: 1px solid #DCE3EF; "
            "border-radius: 8px; }"
        )
        chart_layout = QVBoxLayout(chart_frame)
        chart_layout.setContentsMargins(8, 8, 8, 8)
        chart_title = QLabel(
            "Monthly Fee Collection and Expenses"
        )
        chart_title.setStyleSheet(
            "font-size: 13px; font-weight: 700; color: #17233D;"
        )
        chart_layout.addWidget(chart_title)

        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_view.setMinimumHeight(260)
        chart_layout.addWidget(self.chart_view, 1)
        detail_row.addWidget(chart_frame, 5)
        main_layout.addLayout(detail_row, 1)

        note_label = QLabel(
            "Fee Collected follows payment date and excludes cancelled "
            "payments. School Strength is a current snapshot and does not "
            "change with the session selector."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet(
            "font-size: 10px; color: #667085;"
        )
        main_layout.addWidget(note_label)

        button_row = QHBoxLayout()
        back_button = QPushButton("Back to Main Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            self._return_to_dashboard
        )
        button_row.addWidget(back_button)
        button_row.addStretch()
        main_layout.addLayout(button_row)

        # Data is loaded when Statistics is opened. Avoid hidden-page
        # database queries and chart work during application startup.

    def reset_page(self) -> None:
        """Reload session choices and show a fresh statistics snapshot."""
        selected_year = self.session_combo.currentData()
        try:
            session_years = (
                statistics_service.get_available_statistics_session_years()
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Statistics Sessions Could Not Be Loaded",
                "The available academic sessions could not be loaded."
                f"\n\n{error}",
            )
            return
        if selected_year not in session_years:
            selected_year = (
                student_service.current_academic_session_start_year()
            )

        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        for session_start_year in session_years:
            self.session_combo.addItem(
                f"{session_start_year}-{session_start_year + 1}",
                session_start_year,
            )
        selected_index = self.session_combo.findData(selected_year)
        if selected_index >= 0:
            self.session_combo.setCurrentIndex(selected_index)
        self.session_combo.blockSignals(False)
        self._load_statistics()

    def _return_to_dashboard(self) -> None:
        self.main_window.application_shell.set_active_module(
            "dashboard",
            "Dashboard",
        )
        self.main_window.go_to_page(
            navigation.PAGE_MAIN_DASHBOARD
        )

    def _load_statistics(self, _index=None) -> None:
        """Refresh every card, cohort row, and chart from one service result."""
        session_start_year = self.session_combo.currentData()
        if session_start_year is None:
            return

        try:
            snapshot = statistics_service.get_statistics_snapshot(
                session_start_year
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Statistics Could Not Be Loaded",
                "The read-only statistics snapshot could not be loaded."
                f"\n\n{error}",
            )
            return

        self.current_snapshot = snapshot
        self.school_strength_card.set_count(
            snapshot["active_strength"]
        )
        self.admissions_card.set_count(
            snapshot["session_admissions"]
        )
        self.collected_card.set_amount(
            snapshot["collected_fee"]
        )
        self.expenses_card.set_amount(
            snapshot["total_expenses"]
        )
        self.net_balance_card.set_amount(
            snapshot["net_balance"]
        )
        self._update_status_table(snapshot["status_counts"])
        self._update_chart(
            snapshot["monthly_rows"],
            snapshot["session_year"],
        )
        self.freshness_label.setText(
            f"Session {snapshot['session_year']} | Refreshed "
            f"{snapshot['refreshed_at']}"
        )

    def _update_status_table(self, status_counts: dict) -> None:
        self.status_table.setRowCount(
            len(statistics_service.STATISTICS_STATUS_ORDER)
        )
        for row_index, status in enumerate(
            statistics_service.STATISTICS_STATUS_ORDER
        ):
            status_item = QTableWidgetItem(status)
            count_item = QTableWidgetItem(
                f"{int(status_counts.get(status, 0)):,}"
            )
            count_item.setTextAlignment(Qt.AlignCenter)
            if status == "Coming Session":
                for item in (status_item, count_item):
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                    item.setForeground(QColor("#3157D5"))
                    item.setToolTip(
                        "Click to open the Coming Session class-capacity report."
                    )
            self.status_table.setItem(row_index, 0, status_item)
            self.status_table.setItem(row_index, 1, count_item)

    def _open_coming_session_report_from_row(
        self, row_index: int, _column_index: int
    ) -> None:
        status_item = self.status_table.item(row_index, 0)
        if status_item is None or status_item.text() != "Coming Session":
            return
        base_year = self.session_combo.currentData()
        if base_year is None:
            return
        page = self.main_window.pages[
            navigation.PAGE_COMING_SESSION_CAPACITY_REPORT
        ]
        page.reset_page(int(base_year))
        self.main_window.application_shell.set_active_module(
            "statistics",
            "Statistics / Coming Session",
        )
        self.main_window.go_to_page(
            navigation.PAGE_COMING_SESSION_CAPACITY_REPORT
        )

    def _update_chart(
        self,
        monthly_rows: list,
        session_year: str,
    ) -> None:
        collected_set = QBarSet("Fee Collected")
        collected_set.setColor(QColor("#3157D5"))
        expenses_set = QBarSet("Expenses")
        expenses_set.setColor(QColor("#E07A2D"))

        categories = []
        values = []
        for row in monthly_rows:
            collected = float(row["collected_fee"])
            expenses = float(row["total_expenses"])
            collected_set.append(collected)
            expenses_set.append(expenses)
            categories.append(str(row["month_label"]))
            values.extend((collected, expenses))

        series = QBarSeries()
        series.append(collected_set)
        series.append(expenses_set)

        chart = QChart()
        chart.addSeries(series)
        chart.setTitle(f"Financial Activity - Session {session_year}")
        chart.legend().setVisible(True)
        chart.legend().setAlignment(Qt.AlignBottom)
        chart.setAnimationOptions(
            QChart.AnimationOption.NoAnimation
        )

        category_axis = QBarCategoryAxis()
        category_axis.append(categories)
        chart.addAxis(category_axis, Qt.AlignBottom)
        series.attachAxis(category_axis)

        value_axis = QValueAxis()
        maximum = max(values, default=0.0)
        value_axis.setRange(0, maximum * 1.15 if maximum > 0 else 1000)
        value_axis.setLabelFormat("%.0f")
        value_axis.setTitleText("Amount (Rs.)")
        chart.addAxis(value_axis, Qt.AlignLeft)
        series.attachAxis(value_axis)

        self.chart_view.setChart(chart)
