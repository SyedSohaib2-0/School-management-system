"""
Expenses Management user interface.

Contains:
- Expenses Management Dashboard
- Add Expense screen

The Search Expense screen will be added in the next steps.
"""

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QDateEdit,
    QDoubleSpinBox,
    QTextEdit,
    QMessageBox,
)

from app import navigation
from app.services import expense_service


# ---------------------------------------------------------------------------
# Expenses Management Dashboard
# ---------------------------------------------------------------------------

class ExpenseManagementDashboardPage(QWidget):
    """Main dashboard for Expenses Management."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(18)

        title_label = QLabel("Expenses Management")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        instruction_label = QLabel(
            "Add a new school expense or search previous expenses."
        )
        instruction_label.setWordWrap(True)
        layout.addWidget(instruction_label)

        add_expense_button = QPushButton("Add Expense")
        add_expense_button.clicked.connect(
            self._open_add_expense
        )
        layout.addWidget(add_expense_button)

        search_expense_button = QPushButton(
            "Search Expense"
        )
        search_expense_button.clicked.connect(
            self._open_search_expense
        )
        layout.addWidget(search_expense_button)

        back_button = QPushButton(
            "Back to Main Dashboard"
        )
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_MAIN_DASHBOARD
            )
        )
        layout.addWidget(back_button)

        layout.addStretch()

    def _open_add_expense(self):
        """Reset and open the Add Expense screen."""
        page = self.main_window.pages[
            navigation.PAGE_ADD_EXPENSE
        ]

        page.reset_form()

        self.main_window.go_to_page(
            navigation.PAGE_ADD_EXPENSE
        )

    def _open_search_expense(self):
        """Reset and open the Search Expense screen."""
        page = self.main_window.pages[
            navigation.PAGE_SEARCH_EXPENSE
        ]

        page.reset_page()

        self.main_window.go_to_page(
            navigation.PAGE_SEARCH_EXPENSE
        )


# ---------------------------------------------------------------------------
# Add Expense
# ---------------------------------------------------------------------------

class AddExpensePage(QWidget):
    """Add one expense with date, amount, and description."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(40, 35, 40, 40)
        layout.setSpacing(16)

        title_label = QLabel("Add Expense")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        instruction_label = QLabel(
            "Enter the expense date, amount, and a clear description."
        )
        instruction_label.setWordWrap(True)
        layout.addWidget(instruction_label)

        form_layout = QFormLayout()
        form_layout.setHorizontalSpacing(20)
        form_layout.setVerticalSpacing(14)

        # ---------------------------------------------------------------
        # Expense date
        # ---------------------------------------------------------------

        self.expense_date_input = QDateEdit()
        self.expense_date_input.setCalendarPopup(True)
        self.expense_date_input.setDisplayFormat(
            "dd-MM-yyyy"
        )
        self.expense_date_input.setMaximumDate(
            QDate.currentDate()
        )
        self.expense_date_input.setMinimumHeight(36)
        self.expense_date_input.setMaximumWidth(220)

        form_layout.addRow(
            "Expense Date:",
            self.expense_date_input,
        )

        # ---------------------------------------------------------------
        # Expense amount
        # ---------------------------------------------------------------

        self.amount_input = QDoubleSpinBox()
        self.amount_input.setRange(
            0.00,
            999999999.00,
        )
        self.amount_input.setDecimals(2)
        self.amount_input.setSingleStep(100.00)
        self.amount_input.setPrefix("Rs. ")
        self.amount_input.setMinimumHeight(36)
        self.amount_input.setMaximumWidth(240)

        form_layout.addRow(
            "Expense Amount:",
            self.amount_input,
        )

        # ---------------------------------------------------------------
        # Expense description
        # ---------------------------------------------------------------

        self.description_input = QTextEdit()
        self.description_input.setPlaceholderText(
            "Write a clear description of the expense..."
        )
        self.description_input.setMinimumHeight(130)
        self.description_input.setMaximumHeight(180)

        form_layout.addRow(
            "Expense Description:",
            self.description_input,
        )

        layout.addLayout(form_layout)

        # ---------------------------------------------------------------
        # Session information
        # ---------------------------------------------------------------

        self.session_label = QLabel()
        self.session_label.setWordWrap(True)
        self.session_label.setStyleSheet(
            "font-weight: bold;"
        )
        layout.addWidget(self.session_label)

        self.expense_date_input.dateChanged.connect(
            self._update_session_label
        )

        # ---------------------------------------------------------------
        # Action buttons
        # ---------------------------------------------------------------

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        save_button = QPushButton("Save Expense")
        save_button.clicked.connect(
            self._save_expense
        )
        action_row.addWidget(save_button)

        clear_button = QPushButton("Clear")
        clear_button.setObjectName(
            "secondaryButton"
        )
        clear_button.clicked.connect(
            self.reset_form
        )
        action_row.addWidget(clear_button)

        layout.addLayout(action_row)

        back_button = QPushButton(
            "Back to Expenses Management"
        )
        back_button.setObjectName(
            "secondaryButton"
        )
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_EXPENSE_DASHBOARD
            )
        )
        layout.addWidget(back_button)

        layout.addStretch()

        self.reset_form()

    # ------------------------------------------------------------------
    # Form control
    # ------------------------------------------------------------------

    def reset_form(self):
        """Reset the form to today's date and empty values."""
        self.expense_date_input.setMaximumDate(
            QDate.currentDate()
        )
        self.expense_date_input.setDate(
            QDate.currentDate()
        )
        self.amount_input.setValue(0.00)
        self.description_input.clear()

        self._update_session_label()

    def _update_session_label(self):
        """Display the April-to-March session for the selected date."""
        selected_date = self.expense_date_input.date()

        month_number = selected_date.month()
        calendar_year = selected_date.year()

        session_label = (
            expense_service.get_expense_session_label(
                month_number,
                calendar_year,
            )
        )

        self.session_label.setText(
            f"School Session: {session_label} "
            f"(April {session_label[:4]} to "
            f"March {session_label[-4:]})"
        )

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_expense(self):
        """Validate and save the entered expense."""
        selected_date = self.expense_date_input.date()

        if selected_date > QDate.currentDate():
            QMessageBox.warning(
                self,
                "Invalid Expense Date",
                "Expense Date cannot be in the future.",
            )
            self.expense_date_input.setFocus()
            return

        expense_date = selected_date.toString(
            "yyyy-MM-dd"
        )
        amount = self.amount_input.value()
        description = (
            self.description_input
            .toPlainText()
            .strip()
        )

        if not expense_service.validate_expense_amount(
            amount
        ):
            QMessageBox.warning(
                self,
                "Invalid Expense Amount",
                "Expense amount must be greater than zero.",
            )
            return

        if not expense_service.validate_expense_description(
            description
        ):
            message = (
                "Expense description cannot exceed 1,000 characters."
                if len(expense_service.normalize_expense_description(description))
                > expense_service.MAX_EXPENSE_DESCRIPTION_LENGTH
                else "Please write a description for the expense."
            )
            QMessageBox.warning(
                self,
                "Invalid Expense Description",
                message,
            )
            return

        confirmation = QMessageBox.question(
            self,
            "Confirm Expense",
            f"Save this expense?\n\n"
            f"Date: {selected_date.toString('dd-MM-yyyy')}\n"
            f"Amount: Rs. {amount:,.2f}\n"
            f"Description: {description}",
        )

        if confirmation != QMessageBox.Yes:
            return

        try:
            expense_id = expense_service.add_expense(
                expense_date=expense_date,
                amount=amount,
                description=description,
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Expense Error",
                str(error),
            )
            return
        except Exception as error:
            QMessageBox.critical(
                self,
                "Expense Save Error",
                "The expense could not be saved."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "Expense Saved",
            f"Expense was saved successfully.\n\n"
            f"Expense Record No: {expense_id}\n"
            f"Amount: Rs. {amount:,.2f}",
        )

        self.reset_form()