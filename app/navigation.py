"""
Navigation constants.
Every page in the application has one fixed name here. The main window
uses these names to switch pages with QStackedWidget, and modules use
them to navigate to and from their own screens without typing raw
strings throughout the codebase.
"""

# Main Dashboard
PAGE_MAIN_DASHBOARD = "main_dashboard"

# Statistics
PAGE_STATISTICS_DASHBOARD = "statistics_dashboard"
PAGE_COMING_SESSION_CAPACITY_REPORT = "coming_session_capacity_report"

# Student Management pages (built in later steps)
PAGE_STUDENT_DASHBOARD = "student_dashboard"
PAGE_ADD_STUDENT = "add_student"
PAGE_SEARCH_STUDENT = "search_student"
PAGE_EDIT_STUDENT = "edit_student"
PAGE_VIEW_STUDENT_DETAILS = "view_student_details"

# Class Management pages (built in later steps)
PAGE_CLASS_DASHBOARD = "class_dashboard"
PAGE_ADD_CLASS = "add_class"
PAGE_SEARCH_CLASS = "search_class"
PAGE_CLASS_SEARCH_RESULT = "class_search_result"

# Promotion Management pages (built in later steps)
PAGE_PROMOTION_DASHBOARD = "promotion_dashboard"
PAGE_FULL_CLASS_PROMOTION = "full_class_promotion"
# --- Fee Management ---
PAGE_FEE_DASHBOARD = "fee_dashboard"
PAGE_PAY_FEE_SEARCH = "pay_fee_search"
PAGE_PAY_FEE_RESULT = "pay_fee_result"
PAGE_FEE_RECEIPT = "fee_receipt"
PAGE_CLASS_STATUS = "class_status"
PAGE_SET_FEE_DASHBOARD = "set_fee_dashboard"
PAGE_SET_CLASS_FEE = "set_class_fee"
PAGE_SET_INDIVIDUAL_FEE = "set_individual_fee"
PAGE_FULL_SCHOOL_LIST = "full_school_list"
PAGE_MONTH_RANGE_FEE_REPORT = "month_range_fee_report"
# ---------------------------------------------------------------------------
# Expenses Management
# ---------------------------------------------------------------------------

PAGE_EXPENSE_DASHBOARD = "expense_dashboard"
PAGE_ADD_EXPENSE = "add_expense"
PAGE_SEARCH_EXPENSE = "search_expense"
# ---------------------------------------------------------------------------
# Income Management
# ---------------------------------------------------------------------------

PAGE_INCOME_DASHBOARD = "income_dashboard"
# ---------------------------------------------------------------------------
# Real-Time Finance
# ---------------------------------------------------------------------------

PAGE_REAL_TIME_FINANCE = "real_time_finance"

# ---------------------------------------------------------------------------
# Backup Management
# ---------------------------------------------------------------------------

PAGE_BACKUP_MANAGEMENT = "backup_management"
