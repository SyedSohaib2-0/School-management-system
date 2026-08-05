"""
Reusable application shell.

Provides:
- Dashboard-only Active school-strength badge
- Fixed left navigation sidebar
- Compact module buttons
- Top header showing the current module
- Existing QStackedWidget content area

This file does not change any module functionality.
"""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QGraphicsOpacityEffect,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QButtonGroup,
    QComboBox,
    QStackedWidget,
)



PROJECT_LOGO_RELATIVE_PATH = (
    Path("assets")
    / "images"
    / "hira_model_school_logo.png"
)


def _resource_path(relative_path):
    """
    Resolve resources in both normal Python execution
    and a future PyInstaller build.
    """
    frozen_root = getattr(
        sys,
        "_MEIPASS",
        None,
    )

    if frozen_root:
        base_path = Path(
            frozen_root
        )

    else:
        base_path = (
            Path(__file__)
            .resolve()
            .parent
            .parent
        )

    return (
        base_path
        / relative_path
    )


class ApplicationShell(QWidget):
    """
    Reusable visual shell for the complete application.

    Existing module pages remain inside the supplied QStackedWidget.
    """

    def __init__(
        self,
        stacked_widget: QStackedWidget,
        navigation_items,
        parent=None,
    ):
        super().__init__(parent)

        self.stacked_widget = stacked_widget
        self.navigation_buttons = {}

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -------------------------------------------------------------
        # Left sidebar
        # -------------------------------------------------------------

        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setObjectName("applicationSidebar")

        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(
            14,
            18,
            14,
            18,
        )
        sidebar_layout.setSpacing(7)

        logo_path = _resource_path(
            PROJECT_LOGO_RELATIVE_PATH
        )

        logo_pixmap = QPixmap(
            str(logo_path)
        )

        self.sidebar_logo_label = QLabel()
        self.sidebar_logo_label.setAlignment(
            Qt.AlignCenter
        )
        self.sidebar_logo_label.setFixedHeight(
            78
        )
        self.sidebar_logo_label.setStyleSheet(
            """
            QLabel {
                background-color: transparent;
                border: none;
                padding: 2px;
            }
            """
        )

        if not logo_pixmap.isNull():
            self.sidebar_logo_label.setPixmap(
                logo_pixmap.scaled(
                    68,
                    68,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

        else:
            self.sidebar_logo_label.hide()

        sidebar_layout.addWidget(
            self.sidebar_logo_label
        )

        school_name_label = QLabel(
            "Hira Model School"
        )
        school_name_label.setAlignment(
            Qt.AlignCenter
        )
        school_name_label.setWordWrap(True)
        school_name_label.setStyleSheet(
            """
            QLabel {
                background-color: transparent;
                color: #ffffff;
                border: none;
                font-size: 18px;
                font-weight: 700;
                padding: 10px 4px 14px 4px;
            }
            """
        )
        sidebar_layout.addWidget(
            school_name_label
        )

        divider = QFrame()
        divider.setFrameShape(
            QFrame.HLine
        )
        divider.setStyleSheet(
            "background-color: #435676;"
        )
        sidebar_layout.addWidget(divider)

        self.button_group = QButtonGroup(
            self
        )
        self.button_group.setExclusive(True)

        for item in navigation_items:
            item_key = item["key"]
            item_text = item["text"]
            item_callback = item["callback"]

            navigation_button = QPushButton(
                item_text
            )
            navigation_button.setCheckable(True)
            navigation_button.setFixedHeight(38)
            navigation_button.setCursor(
                Qt.PointingHandCursor
            )

            navigation_button.setStyleSheet(
                """
                QPushButton {
                    background-color: transparent;
                    color: #E4EAF4;
                    border: none;
                    border-radius: 5px;
                    padding: 7px 10px;
                    text-align: left;
                    font-size: 13px;
                    font-weight: 600;
                }

                QPushButton:hover {
                    background-color: #2B4168;
                    color: #ffffff;
                }

                QPushButton:checked {
                    background-color: #3157D5;
                    color: #ffffff;
                }
                """
            )

            navigation_button.clicked.connect(
                lambda checked=False,
                key=item_key,
                text=item_text,
                callback=item_callback:
                self._handle_navigation(
                    key,
                    text,
                    callback,
                )
            )

            self.button_group.addButton(
                navigation_button
            )

            self.navigation_buttons[
                item_key
            ] = navigation_button

            sidebar_layout.addWidget(
                navigation_button
            )

        sidebar_layout.addStretch()

        footer_label = QLabel(
            "Offline School Management System"
        )
        footer_label.setAlignment(
            Qt.AlignCenter
        )
        footer_label.setWordWrap(True)
        footer_label.setStyleSheet(
            """
            color: #A9B5C7;
            font-size: 10px;
            padding-top: 8px;
            """
        )
        sidebar_layout.addWidget(
            footer_label
        )

        main_layout.addWidget(
            self.sidebar
        )

        # -------------------------------------------------------------
        # Right content area
        # -------------------------------------------------------------

        content_frame = QFrame()
        content_frame.setObjectName(
            "applicationContent"
        )

        content_layout = QVBoxLayout(
            content_frame
        )
        content_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        content_layout.setSpacing(0)

        # -------------------------------------------------------------
        # Top header
        # -------------------------------------------------------------

        header_frame = QFrame()
        header_frame.setFixedHeight(56)
        header_frame.setStyleSheet(
            """
            QFrame {
                background-color: #ffffff;
                border-bottom: 1px solid #DCE2EC;
            }
            """
        )

        header_layout = QHBoxLayout(
            header_frame
        )
        header_layout.setContentsMargins(
            22,
            0,
            22,
            0,
        )

        self.module_title_label = QLabel(
            "Dashboard"
        )
        self.module_title_label.setStyleSheet(
            """
            color: #17233D;
            font-size: 18px;
            font-weight: 700;
            """
        )
        header_layout.addWidget(
            self.module_title_label
        )

        header_layout.addStretch()

        self.school_strength_badge = QLabel(
            "School Strength: 0"
        )
        self.school_strength_badge.setObjectName(
            "schoolStrengthBadge"
        )
        self.school_strength_badge.setAlignment(
            Qt.AlignCenter
        )
        self.school_strength_badge.setMinimumWidth(
            150
        )
        self.school_strength_badge.setFixedHeight(
            30
        )
        self.school_strength_badge.setStyleSheet(
            """
            QLabel#schoolStrengthBadge {
                background-color: #EEF2FF;
                color: #2949B8;
                border: 1px solid #CCD6FF;
                border-radius: 15px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 700;
            }
            """
        )
        header_layout.addWidget(
            self.school_strength_badge
        )

        header_layout.addSpacing(8)

        self.session_admissions_badge = QLabel(
            "Total Admissions: 0"
        )
        self.session_admissions_badge.setAlignment(Qt.AlignCenter)
        self.session_admissions_badge.setMinimumWidth(145)
        self.session_admissions_badge.setFixedHeight(30)
        self.session_admissions_badge.setStyleSheet(
            """
            QLabel {
                background-color: #ECFDF3;
                color: #137A43;
                border: 1px solid #B7E4C7;
                border-radius: 15px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 700;
            }
            """
        )
        header_layout.addWidget(self.session_admissions_badge)

        header_layout.addSpacing(6)

        self.admission_session_combo = QComboBox()
        self.admission_session_combo.setToolTip(
            "Select the academic session for the admission total"
        )
        self.admission_session_combo.setMinimumWidth(112)
        self.admission_session_combo.setFixedHeight(30)
        header_layout.addWidget(self.admission_session_combo)

        header_layout.addSpacing(
            16
        )

        system_label = QLabel(
            "Hira Model School"
        )
        system_label.setStyleSheet(
            """
            QLabel {
                background-color: transparent;
                color: #657187;
                border: none;
                font-size: 13px;
                font-weight: 600;
            }
            """
        )
        header_layout.addWidget(
            system_label
        )

        content_layout.addWidget(
            header_frame
        )

        # Existing application pages remain here.
        content_layout.addWidget(
            self.stacked_widget,
            1,
        )

        # -------------------------------------------------------------
        # Global bottom watermark
        # -------------------------------------------------------------

        bottom_watermark_frame = QFrame()
        bottom_watermark_frame.setObjectName(
            "bottomWatermarkFrame"
        )
        bottom_watermark_frame.setFixedHeight(
            54
        )

        watermark_layout = QHBoxLayout(
            bottom_watermark_frame
        )
        watermark_layout.setContentsMargins(
            18,
            4,
            18,
            4,
        )
        watermark_layout.setSpacing(
            8
        )

        watermark_layout.addStretch()

        self.bottom_watermark_logo_label = QLabel()
        self.bottom_watermark_logo_label.setAlignment(
            Qt.AlignCenter
        )
        self.bottom_watermark_logo_label.setFixedSize(
            42,
            42,
        )
        self.bottom_watermark_logo_label.setStyleSheet(
            """
            QLabel {
                background-color: transparent;
                border: none;
            }
            """
        )

        if not logo_pixmap.isNull():
            self.bottom_watermark_logo_label.setPixmap(
                logo_pixmap.scaled(
                    40,
                    40,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

            watermark_opacity = (
                QGraphicsOpacityEffect(
                    self.bottom_watermark_logo_label
                )
            )
            watermark_opacity.setOpacity(
                0.14
            )

            self.bottom_watermark_logo_label.setGraphicsEffect(
                watermark_opacity
            )

        else:
            self.bottom_watermark_logo_label.hide()

        watermark_layout.addWidget(
            self.bottom_watermark_logo_label
        )

        watermark_text_label = QLabel(
            "HIRA MODEL SCHOOL"
        )
        watermark_text_label.setStyleSheet(
            """
            QLabel {
                background-color: transparent;
                color: #B0B9C7;
                border: none;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            """
        )

        watermark_layout.addWidget(
            watermark_text_label
        )

        watermark_layout.addStretch()

        content_layout.addWidget(
            bottom_watermark_frame
        )

        main_layout.addWidget(
            content_frame,
            1,
        )

        # -------------------------------------------------------------
        # Shell background
        # -------------------------------------------------------------

        self.setStyleSheet(
            """
            QFrame#applicationSidebar {
                background-color: #1D2B46;
                border: none;
            }

            QFrame#applicationContent {
                background-color: #F5F7FB;
                border: none;
            }

            QFrame#bottomWatermarkFrame {
                background-color: #F5F7FB;
                border: none;
                border-top: 1px solid #E7EBF1;
            }
            """
        )

    def _handle_navigation(
        self,
        item_key,
        item_text,
        callback,
    ):
        """Run the existing navigation callback."""
        self.module_title_label.setText(
            item_text
        )

        self.school_strength_badge.setVisible(
            item_key == "dashboard"
        )
        self.session_admissions_badge.setVisible(
            item_key == "dashboard"
        )
        self.admission_session_combo.setVisible(
            item_key == "dashboard"
        )

        callback()

    def set_active_module(
        self,
        item_key,
        title=None,
    ):
        """
        Mark one sidebar module as active.

        This can also be called when navigation occurs from inside a module.
        """
        button = self.navigation_buttons.get(
            item_key
        )

        if button is not None:
            button.setChecked(True)

            if title:
                self.module_title_label.setText(
                    title
                )
            else:
                self.module_title_label.setText(
                    button.text()
                )

        self.school_strength_badge.setVisible(
            item_key == "dashboard"
        )
        self.session_admissions_badge.setVisible(
            item_key == "dashboard"
        )
        self.admission_session_combo.setVisible(
            item_key == "dashboard"
        )

    def set_school_strength(
        self,
        active_count,
    ) -> None:
        """Update the compact Dashboard-only Active school strength badge."""
        self.school_strength_badge.setText(
            f"School Strength: {int(active_count)}"
        )

    def set_school_strength_visible(
        self,
        visible,
    ) -> None:
        """Show the strength badge only on the Main Dashboard."""
        self.school_strength_badge.setVisible(
            bool(visible)
        )
        self.session_admissions_badge.setVisible(bool(visible))
        self.admission_session_combo.setVisible(bool(visible))

    def set_admission_sessions(
        self,
        session_start_years,
        selected_start_year,
    ) -> None:
        """Populate the admission-session selector without firing a refresh."""
        self.admission_session_combo.blockSignals(True)
        self.admission_session_combo.clear()
        for start_year in session_start_years:
            start_year = int(start_year)
            self.admission_session_combo.addItem(
                f"{start_year}-{start_year + 1}",
                start_year,
            )
        selected_index = self.admission_session_combo.findData(
            int(selected_start_year)
        )
        if selected_index >= 0:
            self.admission_session_combo.setCurrentIndex(selected_index)
        self.admission_session_combo.blockSignals(False)

    def selected_admission_session_start_year(self):
        """Return the start year selected in the dashboard session control."""
        value = self.admission_session_combo.currentData()
        return int(value) if value is not None else None

    def set_session_admission_total(self, total) -> None:
        """Update the dashboard admission total for the selected session."""
        self.session_admissions_badge.setText(
            f"Total Admissions: {int(total)}"
        )

    def set_module_title(
        self,
        title,
    ):
        """Change only the top-header title."""
        self.module_title_label.setText(
            str(title)
        )
