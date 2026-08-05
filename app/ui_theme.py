"""
Central user-interface theme for the School Management System.

This file controls only visual appearance and reusable table styling.

It does not change:
- database operations
- navigation
- calculations
- searches
- payments
- printing
- PDF generation
- promotion rules
"""

from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
)


# ---------------------------------------------------------------------------
# Global application stylesheet
# ---------------------------------------------------------------------------

APP_STYLESHEET = """
/* ---------------------------------------------------------
   Complete application
   --------------------------------------------------------- */

QWidget {
    background-color: #f4f6fb;
    color: #1f2a44;
    font-family: "Segoe UI";
    font-size: 13px;
}

QMainWindow {
    background-color: #f4f6fb;
}


/* ---------------------------------------------------------
   Page headings
   --------------------------------------------------------- */

QLabel#pageTitle {
    background-color: transparent;
    color: #17233d;
    font-size: 24px;
    font-weight: 700;
    padding: 0px 0px 5px 0px;
}

QLabel#sectionHeading {
    background-color: transparent;
    color: #233354;
    font-size: 16px;
    font-weight: 700;
    padding: 4px 0px;
}

QLabel#generatedClassName {
    background-color: #eef2ff;
    border: 1px solid #c5d0f5;
    border-radius: 5px;
    padding: 7px;
    font-weight: 700;
}


/* ---------------------------------------------------------
   Primary buttons
   --------------------------------------------------------- */

QPushButton {
    background-color: #456cf2;
    color: #ffffff;

    border: none;
    border-radius: 6px;

    padding: 5px 12px;

    min-width: 110px;
    max-width: 240px;

    min-height: 24px;
    max-height: 34px;

    font-weight: 600;
}

QPushButton:hover {
    background-color: #355bd7;
}

QPushButton:pressed {
    background-color: #2949b5;
}

QPushButton:disabled {
    background-color: #b7bfce;
    color: #eef0f5;
}


/* ---------------------------------------------------------
   Secondary buttons
   --------------------------------------------------------- */

QPushButton#secondaryButton {
    background-color: #aeb7c7;
    color: #17233d;
}

QPushButton#secondaryButton:hover {
    background-color: #9da8ba;
}

QPushButton#secondaryButton:pressed {
    background-color: #8c98ab;
}


/* ---------------------------------------------------------
   Destructive buttons
   --------------------------------------------------------- */

QPushButton#dangerButton {
    background-color: #c93c3c;
    color: #ffffff;
}

QPushButton#dangerButton:hover {
    background-color: #ad2f2f;
}

QPushButton#dangerButton:pressed {
    background-color: #8f2525;
}


/* ---------------------------------------------------------
   Text and number inputs
   --------------------------------------------------------- */

QLineEdit,
QTextEdit,
QPlainTextEdit,
QDateEdit,
QTimeEdit,
QDateTimeEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    background-color: #ffffff;
    color: #1f2a44;

    border: 1px solid #cbd2df;
    border-radius: 5px;

    padding: 6px 8px;

    min-height: 22px;

    selection-background-color: #456cf2;
    selection-color: #ffffff;
}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QDateEdit:focus,
QTimeEdit:focus,
QDateTimeEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {
    border: 1px solid #456cf2;
}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QDateEdit:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QComboBox:disabled {
    background-color: #e7eaf0;
    color: #7b8495;
}


/* ---------------------------------------------------------
   Combo-box dropdown
   --------------------------------------------------------- */

QComboBox::drop-down {
    width: 28px;
    border: none;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #1f2a44;

    border: 1px solid #cbd2df;

    selection-background-color: #456cf2;
    selection-color: #ffffff;

    outline: none;
}


/* ---------------------------------------------------------
   Tables
   --------------------------------------------------------- */

QTableWidget,
QTableView {
    background-color: #ffffff;
    alternate-background-color: #f6f8fc;

    border: 1px solid #d5dae4;
    border-radius: 4px;

    gridline-color: #dce1e9;

    selection-background-color: #dce5ff;
    selection-color: #17233d;
}

QTableWidget::item,
QTableView::item {
    padding: 5px;
    border: none;
}

QTableWidget::item:selected,
QTableView::item:selected {
    background-color: #dce5ff;
    color: #17233d;
}


/* ---------------------------------------------------------
   Table headings
   --------------------------------------------------------- */

QHeaderView::section {
    background-color: #e9ecf3;
    color: #24314d;

    border: none;
    border-right: 1px solid #d5dae4;
    border-bottom: 1px solid #d5dae4;

    padding: 7px 5px;

    font-weight: 700;
}

QTableCornerButton::section {
    background-color: #e9ecf3;
    border: none;
}


/* ---------------------------------------------------------
   Frames and cards
   --------------------------------------------------------- */

QFrame {
    border-color: #d9dee8;
}

QFrame#cardFrame {
    background-color: #ffffff;

    border: 1px solid #d9dee8;
    border-radius: 8px;
}


/* ---------------------------------------------------------
   Tabs
   --------------------------------------------------------- */

QTabWidget::pane {
    background-color: #ffffff;

    border: 1px solid #d5dae4;
    border-radius: 5px;
}

QTabBar::tab {
    background-color: #e5e9f2;
    color: #273552;

    border: 1px solid #d1d7e2;
    border-bottom: none;

    padding: 7px 14px;

    min-width: 100px;
}

QTabBar::tab:selected {
    background-color: #456cf2;
    color: #ffffff;
}


/* ---------------------------------------------------------
   Group boxes
   --------------------------------------------------------- */

QGroupBox {
    background-color: #ffffff;

    border: 1px solid #d9dee8;
    border-radius: 6px;

    margin-top: 12px;
    padding-top: 10px;

    font-weight: 700;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;

    left: 10px;
    padding: 0px 5px;
}


/* ---------------------------------------------------------
   Scrollbars
   --------------------------------------------------------- */

QScrollBar:vertical {
    background-color: #edf0f5;
    width: 11px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #aab4c5;
    border-radius: 5px;
    min-height: 28px;
}

QScrollBar::handle:vertical:hover {
    background-color: #8996aa;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background-color: #edf0f5;
    height: 11px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background-color: #aab4c5;
    border-radius: 5px;
    min-width: 28px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #8996aa;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0px;
}


/* ---------------------------------------------------------
   Calendar
   --------------------------------------------------------- */

QCalendarWidget QWidget {
    background-color: #ffffff;
}

QCalendarWidget QToolButton {
    background-color: #456cf2;
    color: #ffffff;

    border-radius: 4px;
    padding: 5px;
}


/* ---------------------------------------------------------
   Message boxes
   --------------------------------------------------------- */

QMessageBox {
    background-color: #f4f6fb;
}

QMessageBox QPushButton {
    min-width: 90px;
}


/* ---------------------------------------------------------
   Tooltips
   --------------------------------------------------------- */

QToolTip {
    background-color: #273552;
    color: #ffffff;

    border: 1px solid #17233d;
    border-radius: 3px;

    padding: 5px;
}


/* ---------------------------------------------------------
   Clearly visible checkboxes
   --------------------------------------------------------- */

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 20px;
    height: 20px;

    background-color: #ffffff;
    border: 2px solid #526079;
    border-radius: 3px;
}

QCheckBox::indicator:hover {
    border-color: #456cf2;
}

QCheckBox::indicator:checked {
    background-color: #456cf2;
    border: 3px solid #173ea5;
}

QCheckBox::indicator:disabled {
    background-color: #d9dee8;
    border-color: #98a2b3;
}
"""


PROFESSIONAL_THEME_OVERRIDES = """
/* Hira Model School professional contrast theme */

QWidget {
    background-color: #F5F7FB;
    color: #17233D;
    font-family: "Segoe UI";
    font-size: 13px;
}

QMainWindow,
QDialog {
    background-color: #F5F7FB;
}

QLabel {
    background-color: transparent;
}

QLabel#pageTitle {
    color: #15213A;
    font-size: 25px;
    font-weight: 750;
    padding: 0px 0px 4px 0px;
}

QLabel#sectionHeading {
    color: #21304F;
    font-size: 16px;
    font-weight: 700;
    padding: 3px 0px;
}

QLabel#studentSummary {
    background-color: #EEF3FF;
    color: #17233D;
    border: 1px solid #C9D6F6;
    border-radius: 7px;
    padding: 9px 12px;
    font-weight: 600;
}

QPushButton {
    background-color: #3157D5;
    color: #FFFFFF;
    border: 1px solid #3157D5;
    border-radius: 7px;
    padding: 7px 15px;
    min-height: 25px;
    font-weight: 650;
}

QPushButton:hover {
    background-color: #284BC1;
    border-color: #284BC1;
}

QPushButton:pressed {
    background-color: #203EA3;
    border-color: #203EA3;
}

QPushButton:focus {
    border: 2px solid #173A9C;
}

QPushButton:disabled {
    background-color: #C9D0DC;
    color: #F7F8FA;
    border-color: #C9D0DC;
}

QPushButton#secondaryButton {
    background-color: #E8EDF5;
    color: #1D2B46;
    border: 1px solid #CDD6E3;
}

QPushButton#secondaryButton:hover {
    background-color: #DCE4EF;
    border-color: #BCC8D8;
}

QPushButton#dangerButton {
    background-color: #C23A3A;
    color: #FFFFFF;
    border-color: #C23A3A;
}

QPushButton#dangerButton:hover {
    background-color: #A93030;
    border-color: #A93030;
}

QLineEdit,
QTextEdit,
QPlainTextEdit,
QDateEdit,
QTimeEdit,
QDateTimeEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    background-color: #FFFFFF;
    color: #17233D;
    border: 1px solid #C7D0DE;
    border-radius: 7px;
    padding: 7px 9px;
    min-height: 22px;
    selection-background-color: #3157D5;
    selection-color: #FFFFFF;
}

QLineEdit:hover,
QTextEdit:hover,
QPlainTextEdit:hover,
QDateEdit:hover,
QTimeEdit:hover,
QDateTimeEdit:hover,
QSpinBox:hover,
QDoubleSpinBox:hover,
QComboBox:hover {
    border-color: #9EABBE;
}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QDateEdit:focus,
QTimeEdit:focus,
QDateTimeEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {
    border: 2px solid #3157D5;
}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QDateEdit:disabled,
QTimeEdit:disabled,
QDateTimeEdit:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QComboBox:disabled {
    background-color: #E9EDF3;
    color: #78849A;
    border-color: #D3DAE5;
}

QComboBox::drop-down {
    width: 30px;
    border: none;
}

QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #17233D;
    border: 1px solid #C7D0DE;
    selection-background-color: #3157D5;
    selection-color: #FFFFFF;
    outline: none;
}

QTableWidget,
QTableView {
    background-color: #FFFFFF;
    alternate-background-color: #F7F9FC;
    color: #17233D;
    border: 1px solid #D8DFE9;
    border-radius: 6px;
    gridline-color: #E2E7EF;
    selection-background-color: #DCE6FF;
    selection-color: #17233D;
}

QTableWidget::item,
QTableView::item {
    padding: 6px;
    border: none;
}

QHeaderView::section {
    background-color: #E9EEF7;
    color: #1C2943;
    border: none;
    border-right: 1px solid #D6DEEA;
    border-bottom: 1px solid #CCD5E2;
    padding: 8px 6px;
    font-weight: 700;
}

QFrame#cardFrame,
QFrame#pageCard,
QFrame#searchCard,
QFrame#summaryCard,
QFrame#actionCard {
    background-color: #FFFFFF;
    border: 1px solid #DCE2EC;
    border-radius: 9px;
}

QGroupBox {
    background-color: #FFFFFF;
    color: #1D2B46;
    border: 1px solid #DCE2EC;
    border-radius: 8px;
    margin-top: 13px;
    padding-top: 11px;
    font-weight: 700;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 11px;
    padding: 0px 5px;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

QScrollBar:vertical {
    background-color: #EDF1F6;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background-color: #A9B5C7;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #7E8EA6;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    background-color: #FFFFFF;
    border: 2px solid #526079;
    border-radius: 4px;
}

QCheckBox::indicator:hover {
    border-color: #3157D5;
}

QCheckBox::indicator:checked {
    background-color: #3157D5;
    border: 2px solid #173A9C;
}

QCheckBox::indicator:disabled {
    background-color: #DCE2EB;
    border-color: #9CA8B9;
}

QMessageBox {
    background-color: #F5F7FB;
}

QToolTip {
    background-color: #1D2B46;
    color: #FFFFFF;
    border: 1px solid #15213A;
    border-radius: 4px;
    padding: 6px;
}
"""


# ---------------------------------------------------------------------------
# Apply theme
# ---------------------------------------------------------------------------

def apply_global_theme(
    application: QApplication,
) -> None:
    """
    Apply the central stylesheet to the complete application.

    Every existing and future page will inherit this theme.
    """
    application.setStyle("Fusion")
    application.setStyleSheet(
        APP_STYLESHEET
        + PROFESSIONAL_THEME_OVERRIDES
    )


# ---------------------------------------------------------------------------
# Reusable table configuration
# ---------------------------------------------------------------------------

def configure_standard_table(
    table: QTableWidget,
    stretch_columns: bool = True,
) -> None:
    """
    Apply consistent behaviour to a table.

    Tables remain independently scrollable, so the complete page does
    not need to scroll merely because many records are displayed.
    """
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.NoEditTriggers
    )

    table.setSelectionBehavior(
        QAbstractItemView.SelectionBehavior.SelectRows
    )

    table.setSelectionMode(
        QAbstractItemView.SelectionMode.SingleSelection
    )

    table.setAlternatingRowColors(
        True
    )

    table.verticalHeader().setVisible(
        False
    )

    table.horizontalHeader().setHighlightSections(
        False
    )

    table.setWordWrap(
        False
    )

    if stretch_columns:
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )