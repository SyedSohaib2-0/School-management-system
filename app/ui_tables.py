"""
Central reusable table configuration.

This module controls structural table behaviour such as:
- column resize modes
- fixed column widths
- horizontal scroll locking
- action-button sizing and focus behaviour

Colours and general widget appearance remain in ui_theme.py.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHeaderView,
    QPushButton,
    QTableWidget,
)


# FEE_TRACKING_ID_UI_V1
def keep_table_left_aligned(
    table: QTableWidget,
) -> None:
    """
    Keep the table at horizontal offset zero.

    Some cell widgets, particularly buttons in the last column,
    can cause QTableWidget to move horizontally when they receive
    focus. This function prevents that hidden offset.
    """
    scroll_bar = table.horizontalScrollBar()

    def reset_to_left(*_arguments):
        if scroll_bar.value() != 0:
            scroll_bar.setValue(0)

    # Install signal handlers only once.
    if not getattr(
        table,
        "_global_left_lock_installed",
        False,
    ):
        scroll_bar.valueChanged.connect(
            reset_to_left
        )

        scroll_bar.rangeChanged.connect(
            lambda _minimum, _maximum:
            QTimer.singleShot(
                0,
                reset_to_left,
            )
        )

        table._global_left_lock_installed = True
        table._global_left_reset_callback = (
            reset_to_left
        )

    QTimer.singleShot(
        0,
        reset_to_left,
    )


def configure_pay_fee_search_table(
    table: QTableWidget,
) -> None:
    """
    Apply the global Pay Fee search-results table structure.

    Columns:
    0 Tracking ID
    1 Admission Number
    2 Roll Number
    3 Student Name
    4 Father Name
    5 Father CNIC
    6 Class
    7 Action
    """
    table.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )

    header = table.horizontalHeader()

    header.setStretchLastSection(
        False
    )

    header.setMinimumSectionSize(
        52
    )

    header.setSectionResizeMode(
        0,
        QHeaderView.ResizeMode.Fixed,
    )
    table.setColumnWidth(
        0,
        108,
    )

    header.setSectionResizeMode(
        1,
        QHeaderView.ResizeMode.Fixed,
    )
    table.setColumnWidth(
        1,
        112,
    )

    header.setSectionResizeMode(
        2,
        QHeaderView.ResizeMode.Fixed,
    )
    table.setColumnWidth(
        2,
        64,
    )

    header.setSectionResizeMode(
        3,
        QHeaderView.ResizeMode.Stretch,
    )

    header.setSectionResizeMode(
        4,
        QHeaderView.ResizeMode.Stretch,
    )

    header.setSectionResizeMode(
        5,
        QHeaderView.ResizeMode.Stretch,
    )

    header.setSectionResizeMode(
        6,
        QHeaderView.ResizeMode.Fixed,
    )
    table.setColumnWidth(
        6,
        92,
    )

    header.setSectionResizeMode(
        7,
        QHeaderView.ResizeMode.Fixed,
    )
    table.setColumnWidth(
        7,
        128,
    )

    keep_table_left_aligned(
        table
    )


def configure_table_action_button(
    button: QPushButton,
) -> None:
    """
    Apply global action-button dimensions and prevent the button
    from horizontally scrolling its parent table.
    """
    button.setFixedWidth(
        108
    )

    button.setFocusPolicy(
        Qt.FocusPolicy.NoFocus
    )

    button.setAutoDefault(
        False
    )

    button.setDefault(
        False
    )
