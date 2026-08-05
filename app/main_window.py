"""
Main window module.
Contains the single application window, built around one QStackedWidget.
Every screen in the application is a page inside this one window rather
than a separate pop-up window, as required by the project rules.
"""

from PySide6.QtWidgets import (
    QMainWindow,
    QStackedWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QDialog,
    QMessageBox,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QMarginsF, QTimer, QSize
from PySide6.QtGui import QTextDocument, QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter, QPrintDialog

from app import navigation
from app.config import STATUS_ACTIVE
from app.services import class_service, student_service, fee_service
from app.modules.student_management import (
    StudentDashboardPage, AddStudentPage, SearchStudentPage,
    ViewStudentDetailsPage, EditStudentPage,
)
from app.modules.class_management import (
    ClassDashboardPage, AddClassPage, SearchClassPage, ClassSearchResultPage,
)
from app.modules.promotion_management import (
    PromotionDashboardPage, FullClassPromotionPage,
)
from app.modules.fee_management import (
    FeeManagementDashboardPage,
    SetFeeDashboardPage,
    SetClassFeePage,
    SetIndividualFeePage,
)
from app.modules.pay_fee import PayFeePage
from app.modules.fee_receipt import FeeReceiptPage
from app.modules.class_fee_status import ClassFeeStatusPage
from app.modules.full_school_fee_list import FullSchoolFeeListPage
from app.modules.month_range_fee_report import MonthRangeFeeReportPage

from app.modules.expense_management import (
    ExpenseManagementDashboardPage,
    AddExpensePage,
)

from app.modules.search_expense import SearchExpensePage
from app.modules.income_management import IncomeManagementPage
from app.modules.real_time_finance import RealTimeFinancePage
from app.modules.backup_management import BackupManagementPage
from app.modules.statistics_management import StatisticsManagementPage
from app.modules.coming_session_capacity_report import (
    ComingSessionCapacityReportPage,
)
from app.ui_shell import ApplicationShell

# ACTIVE_SCHOOL_STRENGTH_DASHBOARD_V10


class CurrentPageStack(QStackedWidget):
    """Stack whose hidden pages do not enlarge the top-level window.

    QStackedWidget normally reports a minimum size based on every registered
    page, including hidden pages. On Windows DPI scaling, one large hidden page
    can therefore force the main window beyond the usable screen geometry.
    This class keeps the existing page widgets and their visual layouts intact
    while exposing only the current page's normal size hint to the parent.
    """

    def minimumSizeHint(self) -> QSize:
        """Do not aggregate minimum sizes from hidden stack pages."""
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        """Use the visible page for layout guidance, not hidden pages."""
        current_page = self.currentWidget()
        if current_page is None:
            return super().sizeHint()
        return current_page.sizeHint()


class MainWindow(QMainWindow):
    """The single top-level window for the whole application."""

    def __init__(self):
        super().__init__()

        self.setWindowTitle(
            "Hira Model School Management System"
        )
        self.resize(
            1200,
            720,
        )

        # Detect a rolled-back Windows clock before any automatic startup
        # maintenance. Existing financial history remains authoritative.
        self.financial_clock_status = fee_service.get_financial_clock_status()

        # Apply the session-boundary archive rule only when chronology is safe.
        # A rolled-back clock must never trigger automatic record mutations.
        if self.financial_clock_status["clock_rollback_detected"]:
            self.startup_archive_result = {
                "skipped_due_to_clock_rollback": True,
                "archived_count": 0,
            }
        else:
            self.startup_archive_result = (
                student_service.archive_eligible_dropped_students(
                    tolerate_missing_fee_configuration=True,
                )
            )

        # -------------------------------------------------------------
        # Application page stack
        # -------------------------------------------------------------

        self.stacked_widget = CurrentPageStack()
        self.stacked_widget.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )

        # Maps navigation page names to their widget instances.
        self.pages = {}

        # -------------------------------------------------------------
        # Register every existing page
        # -------------------------------------------------------------

        self._register_main_dashboard()
        self._register_student_management_pages()
        self._register_class_management_pages()
        self._register_promotion_management_pages()
        self._register_fee_management_pages()
        self._register_expense_management_pages()
        self._register_income_management_page()
        self._register_statistics_page()
        self._register_real_time_finance_page()
        self._register_backup_management_page()
        self._register_placeholder_pages()

        # -------------------------------------------------------------
        # Central sidebar navigation
        # -------------------------------------------------------------

        navigation_items = [
            {
                "key": "dashboard",
                "text": "Dashboard",
                "callback": (
                    lambda: self.go_to_page(
                        navigation.PAGE_MAIN_DASHBOARD
                    )
                ),
            },
            {
                "key": "students",
                "text": "Student Management",
                "callback": self._open_student_management,
            },
            {
                "key": "classes",
                "text": "Class Management",
                "callback": self._open_class_management,
            },
            {
                "key": "promotions",
                "text": "Promotion Management",
                "callback": self._open_promotion_management,
            },
            {
                "key": "fees",
                "text": "Fee Management",
                "callback": self._open_fee_management,
            },
            {
                "key": "statistics",
                "text": "Statistics",
                "callback": self._open_statistics,
            },
            {
                "key": "expenses",
                "text": "Expenses Management",
                "callback": self._open_expense_management,
            },
            {
                "key": "income",
                "text": "Income Management",
                "callback": self._open_income_management,
            },
            {
                "key": "real_time",
                "text": "Real-Time Finance",
                "callback": self._open_real_time_finance,
            },
            {
                "key": "backup",
                "text": "Backup Management",
                "callback": self._open_backup_management,
            },
        ]

        self.application_shell = ApplicationShell(
            stacked_widget=self.stacked_widget,
            navigation_items=navigation_items,
            parent=self,
        )
        self.application_shell.admission_session_combo.currentIndexChanged.connect(
            self._refresh_session_admission_total
        )

        self.setCentralWidget(
            self.application_shell
        )

        # -------------------------------------------------------------
        # Initial page
        # -------------------------------------------------------------

        self.application_shell.set_active_module(
            "dashboard",
            "Dashboard",
        )

        self.go_to_page(
            navigation.PAGE_MAIN_DASHBOARD
        )

        # Show the warning after the main window has finished constructing.
        QTimer.singleShot(0, self._show_financial_clock_warning_if_needed)

    def _show_financial_clock_warning_if_needed(self) -> None:
        """Warn without blocking database startup when Windows time moved back."""
        status = fee_service.get_financial_clock_status()
        self.financial_clock_status = status
        if not status["clock_rollback_detected"]:
            return

        latest = status["latest_financial_timestamp"]
        latest_text = (
            latest.strftime("%Y-%m-%d %H:%M:%S")
            if latest is not None
            else "unknown"
        )
        receipt = status["latest_receipt_no"] or "-"
        QMessageBox.warning(
            self,
            "Computer Date/Time Warning",
            "The software opened without changing school records, but the "
            "computer date/time is earlier than saved fee history.\n\n"
            f"Latest saved receipt: {receipt}\n"
            f"Latest saved transaction time: {latest_text}\n\n"
            "Fee payment and payment cancellation are temporarily blocked, "
            "and automatic dropped-student archival was skipped for this "
            "startup. Correct Windows date/time, choose Sync now, and reopen "
            "the software.",
        )

    # -----------------------------------------------------------------
    # Page registration
    # -----------------------------------------------------------------

    def add_page(self, page_name: str, widget: QWidget) -> None:
        """Register a page widget under a fixed page name and add it to the stack."""
        self.pages[page_name] = widget
        self.stacked_widget.addWidget(widget)

    def go_to_page(self, page_name: str) -> None:
        """Switch the visible page to the one registered under page_name."""
        if page_name not in self.pages:
            raise ValueError(f"Unknown page: {page_name}")

        self.stacked_widget.setCurrentWidget(
            self.pages[page_name]
        )

        if hasattr(
            self,
            "application_shell",
        ):
            on_main_dashboard = (
                page_name
                == navigation.PAGE_MAIN_DASHBOARD
            )

            self.application_shell.set_school_strength_visible(
                on_main_dashboard
            )

            if on_main_dashboard:
                self._refresh_dashboard_statistics()

    def _refresh_dashboard_statistics(self) -> None:
        """Refresh school strength and the selected session's admissions."""
        self._refresh_school_strength()
        session_years = (
            student_service.available_admission_session_start_years()
        )
        selected_year = (
            self.application_shell
            .selected_admission_session_start_year()
        )
        if selected_year not in session_years:
            selected_year = (
                student_service.current_academic_session_start_year()
            )
        self.application_shell.set_admission_sessions(
            session_years,
            selected_year,
        )
        self._refresh_session_admission_total()

    def _refresh_session_admission_total(self, _index=None) -> None:
        """Refresh admission count when the dashboard session changes."""
        selected_year = (
            self.application_shell
            .selected_admission_session_start_year()
        )
        if selected_year is None:
            return
        total = student_service.count_admissions_for_session(
            selected_year
        )
        self.application_shell.set_session_admission_total(total)

    def _refresh_school_strength(self) -> None:
        """Refresh the Dashboard badge using Active students only."""
        if not hasattr(
            self,
            "application_shell",
        ):
            return

        active_count = (
            student_service
            .count_school_active_strength()
        )

        self.application_shell.set_school_strength(
            active_count
        )

    # -----------------------------------------------------------------
    # Main Dashboard
    # -----------------------------------------------------------------

    def _register_main_dashboard(self) -> None:
        """Build a compact and organised Main Dashboard."""
        dashboard_page = QWidget()

        layout = QVBoxLayout(dashboard_page)
        layout.setContentsMargins(
            35,
            28,
            35,
            30,
        )
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        title_label = QLabel(
            "Hira Model School Management System"
        )
        title_label.setObjectName("pageTitle")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)

        subtitle_label = QLabel(
            "Select a module to continue"
        )
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet(
            "font-size: 14px; "
            "color: #59657a; "
            "margin-bottom: 12px;"
        )
        layout.addWidget(subtitle_label)

        button_grid = QGridLayout()
        button_grid.setHorizontalSpacing(16)
        button_grid.setVerticalSpacing(14)
        button_grid.setContentsMargins(
            70,
            10,
            70,
            10,
        )

        module_buttons = [
            (
                "Student Management",
                self._open_student_management,
            ),
            (
                "Class Management",
                self._open_class_management,
            ),
            (
                "Promotion Management",
                self._open_promotion_management,
            ),
            (
                "Fee Management",
                self._open_fee_management,
            ),
            (
                "Statistics",
                self._open_statistics,
            ),
            (
                "Expenses Management",
                self._open_expense_management,
            ),
            (
                "Income Management",
                self._open_income_management,
            ),
            (
                "Real-Time Finance",
                self._open_real_time_finance,
            ),
            (
                "Backup Management",
                self._open_backup_management,
            ),
            (
                "Print Whole School",
                self._print_whole_school,
            ),
        ]

        number_of_columns = 3

        for index, (
            button_text,
            button_action,
        ) in enumerate(module_buttons):

            module_button = QPushButton(
                button_text
            )

            module_button.clicked.connect(
                button_action
            )

            module_button.setMinimumWidth(180)
            module_button.setMaximumWidth(240)
            module_button.setFixedHeight(46)

            row_number = (
                index // number_of_columns
            )

            column_number = (
                index % number_of_columns
            )

            button_grid.addWidget(
                module_button,
                row_number,
                column_number,
            )

        for column_number in range(
            number_of_columns
        ):
            button_grid.setColumnStretch(
                column_number,
                1,
            )

        layout.addLayout(button_grid)
        layout.addStretch()

        self.add_page(
            navigation.PAGE_MAIN_DASHBOARD,
            dashboard_page,
        )

    # -----------------------------------------------------------------
    # Print Whole School
    # -----------------------------------------------------------------

    def _print_whole_school(self) -> None:
        """
        Print all Active students, grouped class-wise.

        Every class starts on a new page. Dropped and Graduated
        students are excluded.
        """
        class_groups = []

        for class_row in class_service.get_all_classes():
            active_students = (
                student_service.load_students_by_status(
                    class_row["id"],
                    STATUS_ACTIVE,
                )
            )

            if active_students:
                class_groups.append(
                    (
                        class_row["class_name"],
                        active_students,
                    )
                )

        if not class_groups:
            QMessageBox.information(
                self,
                "No Active Students",
                "No Active student records are available to print.",
            )
            return

        printer = QPrinter(
            QPrinter.HighResolution
        )
        printer.setPageLayout(
            QPageLayout(
                QPageSize(QPageSize.A4),
                QPageLayout.Portrait,
                QMarginsF(4.0, 4.0, 4.0, 4.0),
                QPageLayout.Millimeter,
            )
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )
        print_dialog.setWindowTitle(
            "Print Whole School - Active Students"
        )

        if print_dialog.exec() != QDialog.Accepted:
            return

        try:
            document = QTextDocument()
            document.setDocumentMargin(0)
            document.setHtml(
                class_service.build_whole_school_print_html(
                    class_groups
                )
            )
            document.print_(
                printer
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Print Error",
                (
                    "Unable to print the whole-school student list.\n\n"
                    f"Details: {error}"
                ),
            )

    # -----------------------------------------------------------------
    # Student Management
    # -----------------------------------------------------------------

    def _register_student_management_pages(self) -> None:
        """Build and register the real Student Management pages."""
        student_dashboard_page = StudentDashboardPage(self)
        self.add_page(navigation.PAGE_STUDENT_DASHBOARD, student_dashboard_page)

        add_student_page = AddStudentPage(self)
        self.add_page(navigation.PAGE_ADD_STUDENT, add_student_page)

        search_student_page = SearchStudentPage(self)
        self.add_page(navigation.PAGE_SEARCH_STUDENT, search_student_page)

        view_student_details_page = ViewStudentDetailsPage(self)
        self.add_page(navigation.PAGE_VIEW_STUDENT_DETAILS, view_student_details_page)

        edit_student_page = EditStudentPage(self)
        self.add_page(navigation.PAGE_EDIT_STUDENT, edit_student_page)

    # -----------------------------------------------------------------
    # Class Management
    # -----------------------------------------------------------------

    def _register_class_management_pages(self) -> None:
        """Build and register the real Class Management pages."""
        class_dashboard_page = ClassDashboardPage(self)
        self.add_page(navigation.PAGE_CLASS_DASHBOARD, class_dashboard_page)

        add_class_page = AddClassPage(self)
        self.add_page(navigation.PAGE_ADD_CLASS, add_class_page)

        search_class_page = SearchClassPage(self)
        self.add_page(navigation.PAGE_SEARCH_CLASS, search_class_page)

        class_search_result_page = ClassSearchResultPage(self)
        self.add_page(navigation.PAGE_CLASS_SEARCH_RESULT, class_search_result_page)

    # -----------------------------------------------------------------
    # Temporary placeholder pages
    # (Replaced with real module pages in later steps)
    # -----------------------------------------------------------------

    def _register_promotion_management_pages(self) -> None:
        """Build and register the real Promotion Management pages built so far."""
        promotion_dashboard_page = PromotionDashboardPage(self)
        self.add_page(navigation.PAGE_PROMOTION_DASHBOARD, promotion_dashboard_page)

        full_class_promotion_page = FullClassPromotionPage(self)
        self.add_page(navigation.PAGE_FULL_CLASS_PROMOTION, full_class_promotion_page)


    def _register_backup_management_page(self) -> None:
        """Build and register the Backup Management page."""
        backup_management_page = BackupManagementPage(
            self
        )
        self.add_page(
            navigation.PAGE_BACKUP_MANAGEMENT,
            backup_management_page,
        )

    def _register_real_time_finance_page(self) -> None:
        """Build and register the Real-Time Finance page."""
        real_time_finance_page = RealTimeFinancePage(
            self
        )
        self.add_page(
            navigation.PAGE_REAL_TIME_FINANCE,
            real_time_finance_page,
        )

    def _register_income_management_page(self) -> None:
        """Build and register the Income Management page."""

        income_management_page = IncomeManagementPage(
            self
        )

        self.add_page(
            navigation.PAGE_INCOME_DASHBOARD,
            income_management_page,
        )

    def _register_statistics_page(self) -> None:
        """Build and register Statistics and Coming Session report pages."""
        statistics_page = StatisticsManagementPage(self)
        self.add_page(
            navigation.PAGE_STATISTICS_DASHBOARD,
            statistics_page,
        )

        coming_session_report_page = ComingSessionCapacityReportPage(self)
        self.add_page(
            navigation.PAGE_COMING_SESSION_CAPACITY_REPORT,
            coming_session_report_page,
        )

    def _register_expense_management_pages(self) -> None:
        """Build and register the Expenses Management pages."""

        expense_dashboard_page = ExpenseManagementDashboardPage(
            self
        )
        self.add_page(
            navigation.PAGE_EXPENSE_DASHBOARD,
            expense_dashboard_page,
        )

        add_expense_page = AddExpensePage(
            self
        )
        self.add_page(
            navigation.PAGE_ADD_EXPENSE,
            add_expense_page,
        )   

        search_expense_page = SearchExpensePage(
            self
        )
        self.add_page(
            navigation.PAGE_SEARCH_EXPENSE,
            search_expense_page,
        )

    def _register_fee_management_pages(self) -> None:
        """Build and register the Fee Management pages completed so far."""

        fee_dashboard_page = FeeManagementDashboardPage(self)
        self.add_page(
            navigation.PAGE_FEE_DASHBOARD,
            fee_dashboard_page,
        )

        pay_fee_page = PayFeePage(self)
        self.add_page(
            navigation.PAGE_PAY_FEE_SEARCH,
            pay_fee_page,
        )
        fee_receipt_page = FeeReceiptPage(self)
        self.add_page(
            navigation.PAGE_FEE_RECEIPT,
        fee_receipt_page,
        )

        class_fee_status_page = ClassFeeStatusPage(self)
        self.add_page(
            navigation.PAGE_CLASS_STATUS,
            class_fee_status_page,
        )

        full_school_fee_list_page = FullSchoolFeeListPage(self)
        self.add_page(
            navigation.PAGE_FULL_SCHOOL_LIST,
            full_school_fee_list_page,
        )

        month_range_fee_report_page = MonthRangeFeeReportPage(self)
        self.add_page(
            navigation.PAGE_MONTH_RANGE_FEE_REPORT,
            month_range_fee_report_page,
        )

        set_fee_dashboard_page = SetFeeDashboardPage(self)
        self.add_page(
            navigation.PAGE_SET_FEE_DASHBOARD,
            set_fee_dashboard_page,
        )

        set_class_fee_page = SetClassFeePage(self)
        self.add_page(
            navigation.PAGE_SET_CLASS_FEE,
            set_class_fee_page,
        )

        set_individual_fee_page = SetIndividualFeePage(self)
        self.add_page(
            navigation.PAGE_SET_INDIVIDUAL_FEE,
            set_individual_fee_page,
        )

    def _register_placeholder_pages(self) -> None:
        """
        No modules are on placeholders anymore. This method is kept in
        place (currently empty) so future modules can be added here the
        same way, without needing to restructure __init__ again.
        """
        pass

    def _build_placeholder_page(self, message: str) -> QWidget:
        """Build a simple page showing a message and a Back to Main Dashboard button."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(20)

        message_label = QLabel(message)
        layout.addWidget(message_label)

        back_button = QPushButton("Back to Main Dashboard")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.go_to_page(navigation.PAGE_MAIN_DASHBOARD)
        )
        layout.addWidget(back_button)
        layout.addStretch()

        return page


    # -----------------------------------------------------------------
    # Dashboard button handlers
    # -----------------------------------------------------------------

    def _open_student_management(self) -> None:
        self.go_to_page(navigation.PAGE_STUDENT_DASHBOARD)

    def _open_statistics(self) -> None:
        page = self.pages[
            navigation.PAGE_STATISTICS_DASHBOARD
        ]
        page.reset_page()
        self.application_shell.set_active_module(
            "statistics",
            "Statistics",
        )
        self.go_to_page(
            navigation.PAGE_STATISTICS_DASHBOARD
        )

    def _open_class_management(self) -> None:
        self.go_to_page(navigation.PAGE_CLASS_DASHBOARD)

    def _open_promotion_management(self) -> None:
        self.go_to_page(navigation.PAGE_PROMOTION_DASHBOARD)
  
    def _open_fee_management(self) -> None:
        self.go_to_page(navigation.PAGE_FEE_DASHBOARD)

    def _open_expense_management(self) -> None:
        self.go_to_page(
            navigation.PAGE_EXPENSE_DASHBOARD
        )
    def _open_real_time_finance(self) -> None:
        page = self.pages[
            navigation.PAGE_REAL_TIME_FINANCE
        ]

        page.reset_page()

        self.go_to_page(
            navigation.PAGE_REAL_TIME_FINANCE
        )


    def _open_income_management(self) -> None:
        page = self.pages[
            navigation.PAGE_INCOME_DASHBOARD
        ]

        page.reset_page()

        self.go_to_page(
            navigation.PAGE_INCOME_DASHBOARD
        )

    def _open_backup_management(self) -> None:
        """Open the Backup Management page."""

        self.application_shell.set_active_module(
            "backup",
            "Backup Management",
        )

        page = self.pages[
            navigation.PAGE_BACKUP_MANAGEMENT
        ]
        page.reset_page()

        self.go_to_page(
            navigation.PAGE_BACKUP_MANAGEMENT
        )
