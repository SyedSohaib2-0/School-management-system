"""
Student Management module.
Contains the Student Management Dashboard, Add Student, Search Student,
View Student Details, and Edit Student screens.
All database work is delegated to app.services.student_service and
app.services.class_service - this file only handles the screen logic.
"""

from datetime import datetime
from html import escape
import base64
import json
import mimetypes
import re
import shutil
from pathlib import Path

from PySide6.QtCore import (
    QMarginsF,
    QEvent,
    Qt,
    QTime,
    QRegularExpression,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QSpinBox, QTimeEdit, QMessageBox,
    QGridLayout, QScrollArea, QFrame, QStackedWidget, QFileDialog, QDialog,
)

from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPixmap,
    QPageLayout,
    QPageSize,
    QTextDocument,
    QRegularExpressionValidator,
)
from PySide6.QtPrintSupport import (
    QPrinter,
    QPrintDialog,
)

from app import navigation
from app.config import APP_DATA_DIR
from app.services import student_service
from app.services import class_service
from app.services import coming_session_service


MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def format_admission_date(row) -> str:
    """Build a readable admission date string like '19 July 2026' from a student row."""
    month_name = MONTH_NAMES[row["admission_month"] - 1]
    return f"{row['admission_day']} {month_name} {row['admission_year']}"


# BEGIN PROFESSIONAL STUDENT PRINT HELPERS

SCHOOL_PRINT_ADDRESS = (
    "Warsak Road, Mathra, Peshawar"
)

SCHOOL_PRINT_BICEP = (
    "BICEP No. 1443"
)

SCHOOL_PRINT_MOBILE = (
    "Mobile: 0346-0951718"
)


# BEGIN SCHOOL PRINT PROFILE SETTINGS

DEFAULT_SCHOOL_PRINT_PROFILE = {
    "school_name": "HIRA MODEL SCHOOL",
    "school_address": "Warsak Road, Mathra, Peshawar",
    "bicep_number": "BICEP No. 1443",
    "mobile_number": "Mobile: 0346-0951718",
    "logo_filename": "hira_model_school_print_logo.png",
}


def _student_print_project_root() -> Path:
    return (
        Path(__file__)
        .resolve()
        .parents[2]
    )


def _student_print_images_directory() -> Path:
    # Student-print profile data and user-selected/generated logos are
    # writable runtime data. Keep them outside the installed program
    # directory so normal customer use works in a read-only installation.
    images_directory = (
        Path(APP_DATA_DIR)
        / "student_print"
        / "images"
    )

    images_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return images_directory


def _student_print_profile_path() -> Path:
    return (
        _student_print_images_directory()
        / "student_print_profile.json"
    )


def _normalize_logo_filename(
    filename: str,
    school_name: str = "",
) -> str:
    cleaned_filename = Path(
        str(
            filename
            or ""
        ).strip()
    ).name

    if not cleaned_filename:
        generated_name = re.sub(
            r"[^A-Za-z0-9]+",
            "_",
            str(
                school_name
                or "school"
            ).strip().lower(),
        ).strip("_")

        cleaned_filename = (
            f"{generated_name or 'school'}"
            f"_logo.png"
        )

    filename_path = Path(
        cleaned_filename
    )

    if (
        not filename_path.suffix
        or filename_path.suffix.lower()
        != ".png"
    ):
        cleaned_filename = (
            filename_path.stem
            + ".png"
        )

    if not re.fullmatch(
        r"[A-Za-z0-9._-]+\.png",
        cleaned_filename,
        flags=re.IGNORECASE,
    ):
        raise ValueError(
            "Logo Filename may contain only letters, "
            "numbers, dots, underscores and hyphens."
        )

    return cleaned_filename


def load_school_print_profile() -> dict:
    profile = dict(
        DEFAULT_SCHOOL_PRINT_PROFILE
    )

    config_path = (
        _student_print_profile_path()
    )

    if config_path.is_file():
        try:
            loaded_data = json.loads(
                config_path.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(
                loaded_data,
                dict,
            ):
                for key in profile:
                    value = loaded_data.get(
                        key
                    )

                    if (
                        value is not None
                        and str(value).strip()
                    ):
                        profile[key] = str(
                            value
                        ).strip()

        except (
            OSError,
            ValueError,
            TypeError,
        ):
            pass

    try:
        profile["logo_filename"] = (
            _normalize_logo_filename(
                profile["logo_filename"],
                profile["school_name"],
            )
        )

    except ValueError:
        profile["logo_filename"] = (
            DEFAULT_SCHOOL_PRINT_PROFILE[
                "logo_filename"
            ]
        )

    return profile


def save_school_print_profile(
    profile: dict,
) -> None:
    config_path = (
        _student_print_profile_path()
    )

    normalized_profile = {
        "school_name": str(
            profile["school_name"]
        ).strip(),

        "school_address": str(
            profile["school_address"]
        ).strip(),

        "bicep_number": str(
            profile["bicep_number"]
        ).strip(),

        "mobile_number": str(
            profile["mobile_number"]
        ).strip(),

        "logo_filename": (
            _normalize_logo_filename(
                profile["logo_filename"],
                profile["school_name"],
            )
        ),
    }

    temporary_path = (
        config_path.with_suffix(
            ".json.tmp"
        )
    )

    temporary_path.write_text(
        json.dumps(
            normalized_profile,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(
        config_path
    )


def _school_profile_logo_path(
    profile: dict = None,
) -> Path:
    active_profile = (
        profile
        or load_school_print_profile()
    )

    return (
        _student_print_images_directory()
        / active_profile["logo_filename"]
    )


def create_800x800_school_logo(
    source_path: Path,
    destination_path: Path,
) -> None:
    source_image = QImage(
        str(source_path)
    )

    if source_image.isNull():
        raise ValueError(
            "The selected logo image could not be opened."
        )

    canvas_size = 800
    safe_content_size = 720

    scaled_image = source_image.scaled(
        safe_content_size,
        safe_content_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    canvas = QImage(
        canvas_size,
        canvas_size,
        QImage.Format.Format_ARGB32,
    )

    canvas.fill(
        QColor(
            255,
            255,
            255,
            0,
        )
    )

    x_position = (
        canvas_size
        - scaled_image.width()
    ) // 2

    y_position = (
        canvas_size
        - scaled_image.height()
    ) // 2

    painter = QPainter(
        canvas
    )

    painter.setRenderHint(
        QPainter.RenderHint.SmoothPixmapTransform,
        True,
    )

    painter.drawImage(
        x_position,
        y_position,
        scaled_image,
    )

    painter.end()

    destination_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = (
        destination_path.parent
        / (
            destination_path.stem
            + "_temporary.png"
        )
    )

    if not canvas.save(
        str(temporary_path),
        "PNG",
    ):
        raise ValueError(
            "The resized logo could not be saved."
        )

    if destination_path.exists():
        backup_directory = (
            destination_path.parent
            / "_logo_backups"
        )

        backup_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        timestamp = datetime.now().strftime(
            "%Y%m%d_%H%M%S"
        )

        backup_path = (
            backup_directory
            / (
                destination_path.stem
                + "_"
                + timestamp
                + destination_path.suffix
            )
        )

        shutil.copy2(
            destination_path,
            backup_path,
        )

    temporary_path.replace(
        destination_path
    )


# END SCHOOL PRINT PROFILE SETTINGS


def _student_print_row_value(
    student_row,
    key: str,
    default=None,
):
    try:
        value = student_row[key]

    except (
        KeyError,
        IndexError,
        TypeError,
    ):
        value = default

    return value


def _student_print_safe_text(
    value,
    empty_value: str = "-",
) -> str:
    if value is None:
        return empty_value

    cleaned_value = str(
        value
    ).strip()

    return (
        cleaned_value
        if cleaned_value
        else empty_value
    )


def _student_print_admission_date(
    student_row,
) -> str:
    try:
        day = int(
            _student_print_row_value(
                student_row,
                "admission_day",
            )
        )

        month = int(
            _student_print_row_value(
                student_row,
                "admission_month",
            )
        )

        year = int(
            _student_print_row_value(
                student_row,
                "admission_year",
            )
        )

        month_name = MONTH_NAMES[
            month - 1
        ]

        return (
            f"{day:02d} "
            f"{month_name} "
            f"{year:04d}"
        )

    except (
        TypeError,
        ValueError,
        IndexError,
    ):
        return "-"


def _student_print_date_of_birth(
    student_row,
) -> str:
    stored_date = (
        _student_print_row_value(
            student_row,
            "date_of_birth",
        )
        or ""
    )

    return (
        student_service
        .format_date_of_birth_for_display(
            stored_date
        )
    )


def _school_print_logo_data_uri() -> str:
    """
    Load the currently configured school logo and embed it in
    Student Management print and PDF output.
    """
    project_root = (
        _student_print_project_root()
    )

    profile = (
        load_school_print_profile()
    )

    configured_logo_path = (
        _school_profile_logo_path(
            profile
        )
    )

    candidate_paths = [
        configured_logo_path,
        (
            project_root
            / "assets"
            / "images"
            / "hira_model_school_print_logo.png"
        ),
        (
            project_root
            / "assets"
            / "images"
            / "hira_model_school_receipt_watermark.png"
        ),
        (
            project_root
            / "assets"
            / "hira_school_logo.png"
        ),
        (
            project_root
            / "assets"
            / "hira_model_school_logo.png"
        ),
        (
            project_root
            / "assets"
            / "school_logo.png"
        ),
        (
            project_root
            / "assets"
            / "logo.png"
        ),
    ]

    checked_paths = set()

    for candidate_path in candidate_paths:
        resolved_path = (
            candidate_path.resolve()
        )

        if resolved_path in checked_paths:
            continue

        checked_paths.add(
            resolved_path
        )

        if not candidate_path.is_file():
            continue

        try:
            mime_type = (
                mimetypes.guess_type(
                    candidate_path.name
                )[0]
                or "image/png"
            )

            encoded_image = (
                base64.b64encode(
                    candidate_path.read_bytes()
                )
                .decode(
                    "ascii"
                )
            )

            return (
                f"data:{mime_type};"
                f"base64,{encoded_image}"
            )

        except OSError:
            continue

    images_directory = (
        _student_print_images_directory()
    )

    fallback_patterns = [
        "hira_model_school_receipt_watermark.*",
        "*school*logo*.png",
        "*hira*logo*.png",
        "*logo*.png",
        "*school*logo*.jpg",
        "*hira*logo*.jpg",
        "*logo*.jpg",
        "*logo*.jpeg",
    ]

    for pattern in fallback_patterns:
        for candidate_path in (
            images_directory.glob(
                pattern
            )
        ):
            resolved_path = (
                candidate_path.resolve()
            )

            if resolved_path in checked_paths:
                continue

            checked_paths.add(
                resolved_path
            )

            try:
                mime_type = (
                    mimetypes.guess_type(
                        candidate_path.name
                    )[0]
                    or "image/png"
                )

                encoded_image = (
                    base64.b64encode(
                        candidate_path.read_bytes()
                    )
                    .decode(
                        "ascii"
                    )
                )

                return (
                    f"data:{mime_type};"
                    f"base64,{encoded_image}"
                )

            except OSError:
                continue

    return ""



def build_student_print_fields(
    student_row,
):
    """
    Final clean student information used by Add, Edit and Search
    print/PDF output. Document submission statuses are intentionally
    excluded.
    """
    return [
        (
            "Admission Number",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "admission_number",
                )
            ),
        ),
        (
            "Roll Number",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "roll_number",
                )
            ),
        ),
        (
            "Class / Section",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "class_name",
                )
            ),
        ),
        (
            "Student Name",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "student_name",
                )
            ),
        ),
        (
            "Gender",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "gender",
                )
            ),
        ),
        (
            "Father Name",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "father_name",
                )
            ),
        ),
        (
            "Father CNIC",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "father_cnic",
                )
            ),
        ),
        (
            "Phone Number",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "father_contact_number",
                )
            ),
        ),
        (
            "WhatsApp Number",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "whatsapp_number",
                )
            ),
        ),
        (
            "Current Address",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "current_address",
                )
            ),
        ),
        (
            "Permanent Address",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "permanent_address",
                )
            ),
        ),
        (
            "Admission Date",
            _student_print_admission_date(
                student_row
            ),
        ),
        (
            "Date of Birth",
            _student_print_date_of_birth(
                student_row
            ),
        ),
        (
            "Student Status",
            _student_print_safe_text(
                _student_print_row_value(
                    student_row,
                    "student_status",
                )
            ),
        ),
    ]


def build_student_detail_fields(
    student_row,
):
    """
    Use the same complete and clean information in Search result
    cards and the View Student Details page.
    """
    return build_student_print_fields(
        student_row
    )


def build_professional_student_print_html(
    student_row,
    document_title: str,
    date_caption: str,
    date_value: str,
) -> str:
    """
    Build a compact one-page A4 student record.

    Short identification fields are grouped together. Address
    fields use full-width rows. Font size and spacing are reduced
    automatically when the record contains long text.
    """
    profile = load_school_print_profile()

    school_name = profile[
        "school_name"
    ]

    school_address = profile[
        "school_address"
    ]

    school_bicep = profile[
        "bicep_number"
    ]

    school_mobile = profile[
        "mobile_number"
    ]

    fields = dict(
        build_student_print_fields(
            student_row
        )
    )

    def field_value(
        label: str,
    ) -> str:
        return _student_print_safe_text(
            fields.get(
                label,
                "-",
            )
        )

    admission_number = field_value(
        "Admission Number"
    )

    roll_number = field_value(
        "Roll Number"
    )

    class_section = field_value(
        "Class / Section"
    )

    student_name = field_value(
        "Student Name"
    )

    gender = field_value(
        "Gender"
    )

    father_name = field_value(
        "Father Name"
    )

    father_cnic = field_value(
        "Father CNIC"
    )

    phone_number = field_value(
        "Phone Number"
    )

    whatsapp_number = field_value(
        "WhatsApp Number"
    )

    current_address = field_value(
        "Current Address"
    )

    permanent_address = field_value(
        "Permanent Address"
    )

    admission_date = field_value(
        "Admission Date"
    )

    date_of_birth = field_value(
        "Date of Birth"
    )

    student_status = field_value(
        "Student Status"
    )

    all_values = [
        admission_number,
        roll_number,
        class_section,
        student_name,
        gender,
        father_name,
        father_cnic,
        phone_number,
        whatsapp_number,
        current_address,
        permanent_address,
        admission_date,
        date_of_birth,
        student_status,
    ]

    total_character_count = sum(
        len(str(value))
        for value in all_values
    )

    longest_address = max(
        len(current_address),
        len(permanent_address),
    )

    if (
        total_character_count > 430
        or longest_address > 150
    ):
        label_font = 6.8
        value_font = 7.1
        cell_padding = 3
        line_height = 1.08
        school_font = 17
        title_font = 10.5
        logo_size = 54
        signature_margin = 12
        footer_spacer_height = 72
        footer_font = 6.7

    elif (
        total_character_count > 280
        or longest_address > 80
    ):
        label_font = 7.4
        value_font = 7.8
        cell_padding = 4
        line_height = 1.12
        school_font = 18
        title_font = 11
        logo_size = 60
        signature_margin = 18
        footer_spacer_height = 135
        footer_font = 7

    else:
        label_font = 8
        value_font = 8.4
        cell_padding = 5
        line_height = 1.18
        school_font = 19
        title_font = 11.5
        logo_size = 66
        signature_margin = 24
        footer_spacer_height = 190
        footer_font = 7.3

    logo_data_uri = (
        _school_print_logo_data_uri()
    )

    if logo_data_uri:
        logo_html = (
            "<img "
            f"src='{logo_data_uri}' "
            f"width='{logo_size}' "
            f"height='{logo_size}'>"
        )

    else:
        logo_html = (
            f"<table width='{logo_size}' "
            f"height='{logo_size}' "
            "cellspacing='0' cellpadding='0' "
            "style='border: 2px solid #173f77;'>"
            "<tr>"
            "<td align='center' valign='middle' "
            "style='font-size: 15pt; "
            "font-weight: bold; color: #173f77;'>"
            "HMS"
            "</td>"
            "</tr>"
            "</table>"
        )

    def label_cell(
        label_text: str,
        width: str,
        colspan: int = 1,
    ) -> str:
        colspan_html = (
            f" colspan='{colspan}'"
            if colspan > 1
            else ""
        )

        return (
            f"<td width='{width}'"
            f"{colspan_html} "
            "class='field-label'>"
            f"{escape(str(label_text))}"
            "</td>"
        )

    def value_cell(
        value_text: str,
        width: str,
        colspan: int = 1,
    ) -> str:
        colspan_html = (
            f" colspan='{colspan}'"
            if colspan > 1
            else ""
        )

        return (
            f"<td width='{width}'"
            f"{colspan_html} "
            "class='field-value'>"
            f"{escape(str(value_text))}"
            "</td>"
        )

    details_rows_html = "".join(
        [
            # Row 1: three compact identification groups
            (
                "<tr>"
                + label_cell(
                    "Admission Number",
                    "13%",
                )
                + value_cell(
                    admission_number,
                    "20%",
                )
                + label_cell(
                    "Roll Number",
                    "13%",
                )
                + value_cell(
                    roll_number,
                    "20%",
                )
                + label_cell(
                    "Class / Section",
                    "14%",
                )
                + value_cell(
                    class_section,
                    "20%",
                )
                + "</tr>"
            ),

            # Row 2
            (
                "<tr>"
                + label_cell(
                    "Student Name",
                    "15%",
                )
                + value_cell(
                    student_name,
                    "35%",
                    colspan=2,
                )
                + label_cell(
                    "Gender",
                    "15%",
                )
                + value_cell(
                    gender,
                    "35%",
                    colspan=2,
                )
                + "</tr>"
            ),

            # Row 3
            (
                "<tr>"
                + label_cell(
                    "Father Name",
                    "15%",
                )
                + value_cell(
                    father_name,
                    "35%",
                    colspan=2,
                )
                + label_cell(
                    "Father CNIC",
                    "15%",
                )
                + value_cell(
                    father_cnic,
                    "35%",
                    colspan=2,
                )
                + "</tr>"
            ),

            # Row 4
            (
                "<tr>"
                + label_cell(
                    "Phone Number",
                    "15%",
                )
                + value_cell(
                    phone_number,
                    "35%",
                    colspan=2,
                )
                + label_cell(
                    "WhatsApp Number",
                    "15%",
                )
                + value_cell(
                    whatsapp_number,
                    "35%",
                    colspan=2,
                )
                + "</tr>"
            ),

            # Row 5: full-width Current Address
            (
                "<tr>"
                + label_cell(
                    "Current Address",
                    "18%",
                )
                + value_cell(
                    current_address,
                    "82%",
                    colspan=5,
                )
                + "</tr>"
            ),

            # Row 6: full-width Permanent Address
            (
                "<tr>"
                + label_cell(
                    "Permanent Address",
                    "18%",
                )
                + value_cell(
                    permanent_address,
                    "82%",
                    colspan=5,
                )
                + "</tr>"
            ),

            # Row 7: three compact date/status groups
            (
                "<tr>"
                + label_cell(
                    "Admission Date",
                    "13%",
                )
                + value_cell(
                    admission_date,
                    "20%",
                )
                + label_cell(
                    "Date of Birth",
                    "13%",
                )
                + value_cell(
                    date_of_birth,
                    "20%",
                )
                + label_cell(
                    "Student Status",
                    "14%",
                )
                + value_cell(
                    student_status,
                    "20%",
                )
                + "</tr>"
            ),
        ]
    )

    return f"""
    <html>
    <head>
        <meta charset="utf-8">

        <style>
            body {{
                font-family: Arial, sans-serif;
                color: #172033;
                margin: 0;
                padding: 0;
                background: #ffffff;
            }}

            .school-name {{
                font-size: {school_font}pt;
                font-weight: bold;
                color: #173f77;
                line-height: 1.05;
            }}

            .school-subtitle {{
                font-size: 7pt;
                font-weight: bold;
                color: #9a7229;
                padding-top: 2px;
            }}

            .document-title {{
                font-size: {title_font}pt;
                font-weight: bold;
                color: #173f77;
                text-align: center;
                padding-top: 7px;
                padding-bottom: 2px;
                line-height: 1.1;
            }}

            .document-date {{
                font-size: 7.4pt;
                font-weight: bold;
                color: #7a5a21;
                text-align: center;
                padding-top: 1px;
                padding-bottom: 7px;
                line-height: 1.1;
            }}

            .details-table {{
                border-collapse: collapse;
                border: 1.5px solid #173f77;
                page-break-inside: avoid;
            }}

            .details-table td {{
                border: 1px solid #aeb9c8;
                padding: {cell_padding}px;
                vertical-align: middle;
                line-height: {line_height};
                word-wrap: break-word;
            }}

            .field-label {{
                background: #edf3fa;
                color: #173f77;
                font-size: {label_font}pt;
                font-weight: bold;
            }}

            .field-value {{
                background: #ffffff;
                color: #172033;
                font-size: {value_font}pt;
            }}

            .signature-text {{
                border-top: 1px solid #172033;
                padding-top: 3px;
                text-align: center;
                font-size: 7.4pt;
                font-weight: bold;
            }}

            .footer {{
                border-top: 1.5px solid #c69a3d;
                padding-top: 4px;
                text-align: center;
                color: #374151;
                font-size: {footer_font}pt;
                line-height: 1.2;
            }}

            .footer-school {{
                color: #173f77;
                font-weight: bold;
            }}

            .print-date {{
                padding-top: 3px;
                color: #7a5a21;
                font-size: {footer_font}pt;
                font-weight: bold;
                text-align: center;
            }}
        </style>
    </head>

    <body>
        <table
            width="100%"
            cellspacing="0"
            cellpadding="0"
        >
            <tr>
                <td width="78%" valign="middle">
                    <div class="school-name">
                        {escape(str(school_name))}
                    </div>

                    <div class="school-subtitle">
                        KNOWLEDGE · CHARACTER · EXCELLENCE
                    </div>
                </td>

                <td
                    width="22%"
                    align="right"
                    valign="middle"
                >
                    {logo_html}
                </td>
            </tr>

            <tr>
                <td
                    colspan="2"
                    style="
                        border-bottom: 3px solid #c69a3d;
                        height: 5px;
                    "
                >
                </td>
            </tr>
        </table>

        <div class="document-title">
            {escape(str(document_title))}
        </div>

        <table
            width="100%"
            cellspacing="0"
            cellpadding="0"
        >
            <tr>
                <td height="12">
                </td>
            </tr>
        </table>

        <table
            width="100%"
            class="details-table"
            cellspacing="0"
            cellpadding="0"
        >
            {details_rows_html}
        </table>

        <table
            width="100%"
            cellspacing="0"
            cellpadding="0"
            style="
                margin-top: {signature_margin}px;
                page-break-inside: avoid;
            "
        >
            <tr>
                <td width="67%">
                </td>

                <td
                    width="33%"
                    valign="bottom"
                    style="padding-left: 16px;"
                >
                    <div class="signature-text">
                        School Signature
                    </div>
                </td>
            </tr>
        </table>

        <table
            width="100%"
            cellspacing="0"
            cellpadding="0"
        >
            <tr>
                <td height="{footer_spacer_height}">
                </td>
            </tr>
        </table>

        <table
            width="100%"
            cellspacing="0"
            cellpadding="0"
            style="
                page-break-inside: avoid;
            "
        >
            <tr>
                <td class="footer">
                    <div>
                        <span class="footer-school">
                            {escape(str(school_name))}
                        </span>

                        &nbsp; | &nbsp;
                        {escape(str(school_address))}
                    </div>

                    <div>
                        {escape(str(school_bicep))}
                        &nbsp; | &nbsp;
                        {escape(str(school_mobile))}
                    </div>

                    <div class="print-date">
                        Printed on:
                        {escape(str(date_value))}
                    </div>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """






# END PROFESSIONAL STUDENT PRINT HELPERS



# =====================================================================
# Student Management Dashboard
# =====================================================================

class SchoolPrintSettingsDialog(QDialog):
    """
    Edit the school information and logo used by Student
    Management print and PDF documents.
    """

    def __init__(
        self,
        parent=None,
    ):
        super().__init__(
            parent
        )

        self.setWindowTitle(
            "School Print Settings"
        )

        self.setModal(
            True
        )

        self.setMinimumWidth(
            560
        )

        self.selected_logo_path = None
        self.profile = (
            load_school_print_profile()
        )

        main_layout = QVBoxLayout(
            self
        )

        main_layout.setContentsMargins(
            24,
            20,
            24,
            20,
        )

        main_layout.setSpacing(
            14
        )

        title_label = QLabel(
            "School Print Settings"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        main_layout.addWidget(
            title_label
        )

        description_label = QLabel(
            (
                "These details are used in Add Student, "
                "Edit Student and Search Student print/PDF "
                "documents."
            )
        )

        description_label.setWordWrap(
            True
        )

        main_layout.addWidget(
            description_label
        )

        content_layout = QHBoxLayout()

        content_layout.setSpacing(
            20
        )

        form_layout = QFormLayout()

        form_layout.setSpacing(
            12
        )

        self.school_name_input = QLineEdit(
            self.profile[
                "school_name"
            ]
        )

        self.address_input = QLineEdit(
            self.profile[
                "school_address"
            ]
        )

        self.bicep_input = QLineEdit(
            self.profile[
                "bicep_number"
            ]
        )

        self.mobile_input = QLineEdit(
            self.profile[
                "mobile_number"
            ]
        )

        self.logo_filename_input = QLineEdit(
            self.profile[
                "logo_filename"
            ]
        )

        self.logo_filename_input.setPlaceholderText(
            "school_logo.png"
        )

        form_layout.addRow(
            "School Name:",
            self.school_name_input,
        )

        form_layout.addRow(
            "Address:",
            self.address_input,
        )

        form_layout.addRow(
            "BICEP Number:",
            self.bicep_input,
        )

        form_layout.addRow(
            "Mobile Number:",
            self.mobile_input,
        )

        filename_widget = QWidget()

        filename_layout = QHBoxLayout(
            filename_widget
        )

        filename_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        filename_layout.setSpacing(
            8
        )

        filename_layout.addWidget(
            self.logo_filename_input,
            1,
        )

        use_name_button = QPushButton(
            "Use School Name"
        )

        use_name_button.setObjectName(
            "secondaryButton"
        )

        use_name_button.clicked.connect(
            self._use_school_name_filename
        )

        filename_layout.addWidget(
            use_name_button
        )

        form_layout.addRow(
            "Logo Filename:",
            filename_widget,
        )

        content_layout.addLayout(
            form_layout,
            1,
        )

        preview_layout = QVBoxLayout()

        preview_layout.setSpacing(
            8
        )

        preview_title = QLabel(
            "Logo Preview"
        )

        preview_title.setStyleSheet(
            "font-weight: bold;"
        )

        preview_title.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        preview_layout.addWidget(
            preview_title
        )

        self.logo_preview = QLabel()

        self.logo_preview.setFixedSize(
            150,
            150,
        )

        self.logo_preview.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self.logo_preview.setStyleSheet(
            (
                "border: 1px solid #B9C2CF;"
                "background: white;"
            )
        )

        preview_layout.addWidget(
            self.logo_preview,
            alignment=(
                Qt.AlignmentFlag.AlignCenter
            ),
        )

        self.logo_path_label = QLabel()

        self.logo_path_label.setWordWrap(
            True
        )

        self.logo_path_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        preview_layout.addWidget(
            self.logo_path_label
        )

        select_logo_button = QPushButton(
            "Select Logo"
        )

        select_logo_button.clicked.connect(
            self._select_logo
        )

        preview_layout.addWidget(
            select_logo_button
        )

        preview_layout.addStretch()

        content_layout.addLayout(
            preview_layout
        )

        main_layout.addLayout(
            content_layout
        )

        size_note = QLabel(
            (
                "Selected images are automatically converted "
                "to an exact 800 × 800 pixel PNG. The original "
                "aspect ratio is preserved."
            )
        )

        size_note.setWordWrap(
            True
        )

        main_layout.addWidget(
            size_note
        )

        button_row = QHBoxLayout()

        self.save_button = QPushButton(
            "Save Settings"
        )

        self.save_button.setDefault(
            True
        )

        self.save_button.setAutoDefault(
            True
        )

        self.save_button.clicked.connect(
            self._save_settings
        )

        cancel_button = QPushButton(
            "Cancel"
        )

        cancel_button.setObjectName(
            "secondaryButton"
        )

        cancel_button.clicked.connect(
            self.reject
        )

        button_row.addWidget(
            self.save_button
        )

        button_row.addWidget(
            cancel_button
        )

        button_row.addStretch()

        main_layout.addLayout(
            button_row
        )

        for input_widget in [
            self.school_name_input,
            self.address_input,
            self.bicep_input,
            self.mobile_input,
            self.logo_filename_input,
        ]:
            input_widget.returnPressed.connect(
                self._save_settings
            )

        self._show_current_logo()

    def _use_school_name_filename(
        self,
    ) -> None:
        school_name = (
            self.school_name_input
            .text()
            .strip()
        )

        generated_name = re.sub(
            r"[^A-Za-z0-9]+",
            "_",
            school_name.lower(),
        ).strip("_")

        self.logo_filename_input.setText(
            (
                f"{generated_name or 'school'}"
                f"_logo.png"
            )
        )

    def _display_preview(
        self,
        image_path: Path,
    ) -> None:
        pixmap = QPixmap(
            str(image_path)
        )

        if pixmap.isNull():
            self.logo_preview.setText(
                "Logo not available"
            )
            return

        preview_pixmap = pixmap.scaled(
            self.logo_preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.logo_preview.setPixmap(
            preview_pixmap
        )

    def _show_current_logo(
        self,
    ) -> None:
        logo_path = (
            _school_profile_logo_path(
                self.profile
            )
        )

        if logo_path.is_file():
            self._display_preview(
                logo_path
            )

            self.logo_path_label.setText(
                logo_path.name
            )

        else:
            self.logo_preview.setText(
                "No configured logo"
            )

            self.logo_path_label.setText(
                self.profile[
                    "logo_filename"
                ]
            )

    def _select_logo(
        self,
    ) -> None:
        selected_path, _selected_filter = (
            QFileDialog.getOpenFileName(
                self,
                "Select School Logo",
                str(
                    _student_print_images_directory()
                ),
                (
                    "Image Files "
                    "(*.png *.jpg *.jpeg *.bmp *.webp)"
                ),
            )
        )

        if not selected_path:
            return

        self.selected_logo_path = Path(
            selected_path
        )

        self._display_preview(
            self.selected_logo_path
        )

        self.logo_path_label.setText(
            self.selected_logo_path.name
        )

    def _save_settings(
        self,
    ) -> None:
        school_name = (
            self.school_name_input
            .text()
            .strip()
        )

        address = (
            self.address_input
            .text()
            .strip()
        )

        bicep_number = (
            self.bicep_input
            .text()
            .strip()
        )

        mobile_number = (
            self.mobile_input
            .text()
            .strip()
        )

        if not school_name:
            QMessageBox.warning(
                self,
                "Missing School Name",
                "Please enter the School Name.",
            )

            self.school_name_input.setFocus()
            return

        if not address:
            QMessageBox.warning(
                self,
                "Missing Address",
                "Please enter the School Address.",
            )

            self.address_input.setFocus()
            return

        if not bicep_number:
            QMessageBox.warning(
                self,
                "Missing BICEP Number",
                "Please enter the BICEP Number.",
            )

            self.bicep_input.setFocus()
            return

        if not mobile_number:
            QMessageBox.warning(
                self,
                "Missing Mobile Number",
                "Please enter the Mobile Number.",
            )

            self.mobile_input.setFocus()
            return

        try:
            logo_filename = (
                _normalize_logo_filename(
                    self.logo_filename_input
                    .text(),
                    school_name,
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Logo Filename",
                str(error),
            )

            self.logo_filename_input.setFocus()
            return

        updated_profile = {
            "school_name": school_name,
            "school_address": address,
            "bicep_number": bicep_number,
            "mobile_number": mobile_number,
            "logo_filename": logo_filename,
        }

        old_logo_path = (
            _school_profile_logo_path(
                self.profile
            )
        )

        new_logo_path = (
            _school_profile_logo_path(
                updated_profile
            )
        )

        try:
            if self.selected_logo_path is not None:
                create_800x800_school_logo(
                    self.selected_logo_path,
                    new_logo_path,
                )

            elif (
                old_logo_path.resolve()
                != new_logo_path.resolve()
            ):
                if not old_logo_path.is_file():
                    raise ValueError(
                        (
                            "The current logo could not be found. "
                            "Please select a logo before changing "
                            "the Logo Filename."
                        )
                    )

                create_800x800_school_logo(
                    old_logo_path,
                    new_logo_path,
                )

            save_school_print_profile(
                updated_profile
            )

        except (
            OSError,
            ValueError,
        ) as error:
            QMessageBox.critical(
                self,
                "Settings Save Failed",
                str(error),
            )
            return

        self.profile = updated_profile

        QMessageBox.information(
            self,
            "Settings Saved",
            (
                "School print settings were saved successfully.\n\n"
                f"Active Logo: {logo_filename}\n"
                "Logo Resolution: 800 × 800 pixels"
            ),
        )

        self.accept()


class StudentDashboardPage(QWidget):
    """
    Student Management landing page with School Print Settings.
    """

    def __init__(
        self,
        main_window,
    ):
        super().__init__()

        self.main_window = main_window

        layout = QVBoxLayout(
            self
        )

        layout.setAlignment(
            Qt.AlignTop
        )

        layout.setContentsMargins(
            40,
            30,
            40,
            40,
        )

        layout.setSpacing(
            20
        )

        title_row = QHBoxLayout()

        title_label = QLabel(
            "Student Management"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        settings_button = QPushButton(
            "⚙"
        )

        settings_button.setObjectName(
            "secondaryButton"
        )

        settings_button.setFixedSize(
            42,
            42,
        )

        settings_button.setToolTip(
            "School Print Settings"
        )

        settings_button.setAccessibleName(
            "School Print Settings"
        )

        settings_button.clicked.connect(
            self._open_print_settings
        )

        title_row.addWidget(
            title_label
        )

        title_row.addStretch()

        title_row.addWidget(
            settings_button
        )

        layout.addLayout(
            title_row
        )

        button_row = QHBoxLayout()

        button_row.setSpacing(
            15
        )

        add_student_button = QPushButton(
            "Add Student"
        )

        add_student_button.clicked.connect(
            self._open_add_student
        )

        button_row.addWidget(
            add_student_button
        )

        search_student_button = QPushButton(
            "Search Student"
        )

        search_student_button.clicked.connect(
            self._open_search_student
        )

        button_row.addWidget(
            search_student_button
        )

        layout.addLayout(
            button_row
        )

        back_button = QPushButton(
            "Back to Main Dashboard"
        )

        back_button.setObjectName(
            "secondaryButton"
        )

        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(
                navigation.PAGE_MAIN_DASHBOARD
            )
        )

        layout.addWidget(
            back_button
        )

        layout.addStretch()

    def _open_print_settings(
        self,
    ) -> None:
        dialog = SchoolPrintSettingsDialog(
            self
        )

        dialog.exec()

    def _open_add_student(
        self,
    ):
        self.main_window.pages[
            navigation.PAGE_ADD_STUDENT
        ].reset_form()

        self.main_window.go_to_page(
            navigation.PAGE_ADD_STUDENT
        )

    def _open_search_student(
        self,
    ):
        self.main_window.pages[
            navigation.PAGE_SEARCH_STUDENT
        ].reload_class_dropdown()

        self.main_window.go_to_page(
            navigation.PAGE_SEARCH_STUDENT
        )



# =====================================================================
# Add Student
# =====================================================================

class AddStudentPage(QWidget):
    """
    Three-stage Add Student workflow:

    1. Student Information
    2. Documents Inquiry
    3. Saved Student Details
    """

    def __init__(
        self,
        main_window,
    ):
        super().__init__()

        self.main_window = main_window
        self.selected_class_id = None
        self.pending_student_data = None
        self.saved_student_id = None
        self.saved_student_row = None
        self.saved_detail_html = ""

        self.outer_layout = QVBoxLayout(
            self
        )

        self.outer_layout.setContentsMargins(
            30,
            25,
            30,
            25,
        )

        self.outer_layout.setSpacing(
            12
        )

        self.workflow_stack = QStackedWidget()

        self.outer_layout.addWidget(
            self.workflow_stack
        )

        self._build_student_information_page()
        self._build_documents_page()
        self._build_saved_details_page()
        self._configure_keyboard_navigation()

        self.reset_form()

    # ========================================================
    # Shared helpers
    # ========================================================

    @staticmethod
    def _digits_only_validator(
        maximum_digits: int,
    ):
        return QRegularExpressionValidator(
            QRegularExpression(
                rf"\d{{0,{maximum_digits}}}"
            )
        )

    @staticmethod
    def _set_checkbox_size(
        checkbox: QCheckBox,
    ) -> None:
        checkbox.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 22px;
                height: 22px;
            }
            """
        )

    @staticmethod
    def _clear_layout(
        layout,
    ) -> None:
        while layout.count():
            item = layout.takeAt(0)

            if item.widget():
                item.widget().deleteLater()

            elif item.layout():
                AddStudentPage._clear_layout(
                    item.layout()
                )

    @staticmethod
    def _safe_text(
        value,
        empty_value: str = "-",
    ) -> str:
        if value is None:
            return empty_value

        cleaned = str(
            value
        ).strip()

        return cleaned or empty_value

    def _focus_widget(
        self,
        widget,
    ):
        def action():
            widget.setFocus()

        return action

    # ========================================================
    # Page 1 — Student Information
    # ========================================================

    def _build_student_information_page(
        self,
    ) -> None:
        """
        Build Page 1 in a compact two-column layout.

        All fields and action buttons remain visible on one
        normal desktop screen without a vertical scroll bar.
        """
        self.student_information_page = QWidget()

        page_layout = QVBoxLayout(
            self.student_information_page
        )

        page_layout.setContentsMargins(
            5,
            2,
            5,
            2,
        )

        page_layout.setSpacing(
            8
        )

        # ----------------------------------------------------
        # Compact heading row
        # ----------------------------------------------------

        heading_row = QHBoxLayout()

        heading_row.setSpacing(
            18
        )

        title_label = QLabel(
            "Add Student"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        subtitle_label = QLabel(
            "Student Information"
        )

        subtitle_label.setObjectName(
            "sectionHeading"
        )

        heading_row.addWidget(
            title_label
        )

        heading_row.addWidget(
            subtitle_label
        )

        heading_row.addStretch()

        page_layout.addLayout(
            heading_row
        )

        # ----------------------------------------------------
        # Main compact form card
        # ----------------------------------------------------

        form_container = QFrame()

        form_container.setObjectName(
            "card"
        )

        form_grid = QGridLayout(
            form_container
        )

        form_grid.setContentsMargins(
            18,
            12,
            18,
            12,
        )

        form_grid.setHorizontalSpacing(
            12
        )

        form_grid.setVerticalSpacing(
            8
        )

        form_grid.setColumnStretch(
            1,
            1,
        )

        form_grid.setColumnStretch(
            3,
            1,
        )

        def add_label(
            label_text: str,
            row: int,
            column: int,
        ) -> QLabel:
            label = QLabel(
                label_text
            )

            label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter
            )

            form_grid.addWidget(
                label,
                row,
                column,
            )

            return label

        # ====================================================
        # Left column
        # ====================================================

        # Select Class
        self.class_combo = QComboBox()

        self.class_combo.currentIndexChanged.connect(
            self._on_class_changed
        )

        add_label(
            "Select Class:",
            0,
            0,
        )

        form_grid.addWidget(
            self.class_combo,
            0,
            1,
        )

        # Admission for Coming Session
        self.coming_session_checkbox = QCheckBox(
            "Admission for Coming Session"
        )
        self._set_checkbox_size(self.coming_session_checkbox)
        self.coming_session_checkbox.toggled.connect(
            self._on_coming_session_toggled
        )
        form_grid.addWidget(
            self.coming_session_checkbox,
            1,
            0,
            1,
            2,
        )

        # Student Name
        self.student_name_input = QLineEdit()
        self.student_name_input.setMaxLength(200)

        self.student_name_input.setPlaceholderText(
            "Enter student name"
        )

        add_label(
            "Student Name:",
            2,
            0,
        )

        form_grid.addWidget(
            self.student_name_input,
            2,
            1,
        )

        # Gender
        self.gender_combo = QComboBox()

        self.gender_combo.addItem(
            "-- Select Gender --",
            "",
        )

        self.gender_combo.addItem(
            "Male",
            "Male",
        )

        self.gender_combo.addItem(
            "Female",
            "Female",
        )

        add_label(
            "Gender:",
            3,
            0,
        )

        form_grid.addWidget(
            self.gender_combo,
            3,
            1,
        )

        # Father Name
        self.father_name_input = QLineEdit()
        self.father_name_input.setMaxLength(200)

        self.father_name_input.setPlaceholderText(
            "Enter father name"
        )

        add_label(
            "Father Name:",
            4,
            0,
        )

        form_grid.addWidget(
            self.father_name_input,
            4,
            1,
        )

        # Father CNIC
        self.father_cnic_input = QLineEdit()

        self.father_cnic_input.setPlaceholderText(
            "13 digits, required"
        )

        self.father_cnic_input.setMaxLength(
            13
        )

        self.father_cnic_input.setValidator(
            self._digits_only_validator(
                13
            )
        )

        add_label(
            "Father CNIC:",
            5,
            0,
        )

        form_grid.addWidget(
            self.father_cnic_input,
            5,
            1,
        )

        # Phone Number
        phone_widget = QWidget()

        phone_row = QHBoxLayout(
            phone_widget
        )

        phone_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        phone_row.setSpacing(
            8
        )

        phone_prefix = QLabel(
            "+92"
        )

        phone_prefix.setStyleSheet(
            "font-weight: bold;"
        )

        self.phone_number_input = QLineEdit()

        self.phone_number_input.setPlaceholderText(
            "10 digits"
        )

        self.phone_number_input.setMaxLength(
            10
        )

        self.phone_number_input.setValidator(
            self._digits_only_validator(
                10
            )
        )

        phone_row.addWidget(
            phone_prefix
        )

        phone_row.addWidget(
            self.phone_number_input,
            1,
        )

        add_label(
            "Phone Number:",
            6,
            0,
        )

        form_grid.addWidget(
            phone_widget,
            6,
            1,
        )

        # ====================================================
        # Right column
        # ====================================================

        # WhatsApp Number
        whatsapp_widget = QWidget()

        whatsapp_row = QHBoxLayout(
            whatsapp_widget
        )

        whatsapp_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        whatsapp_row.setSpacing(
            8
        )

        whatsapp_prefix = QLabel(
            "+92"
        )

        whatsapp_prefix.setStyleSheet(
            "font-weight: bold;"
        )

        self.whatsapp_number_input = QLineEdit()

        self.whatsapp_number_input.setPlaceholderText(
            "10 digits, optional"
        )

        self.whatsapp_number_input.setMaxLength(
            10
        )

        self.whatsapp_number_input.setValidator(
            self._digits_only_validator(
                10
            )
        )

        whatsapp_row.addWidget(
            whatsapp_prefix
        )

        whatsapp_row.addWidget(
            self.whatsapp_number_input,
            1,
        )

        add_label(
            "WhatsApp Number:",
            0,
            2,
        )

        form_grid.addWidget(
            whatsapp_widget,
            0,
            3,
        )

        # Current Address
        self.current_address_input = QLineEdit()

        self.current_address_input.setPlaceholderText(
            "Enter current address"
        )

        add_label(
            "Current Address:",
            1,
            2,
        )

        form_grid.addWidget(
            self.current_address_input,
            1,
            3,
        )

        # Permanent Address
        self.permanent_address_input = QLineEdit()

        self.permanent_address_input.setPlaceholderText(
            "Enter permanent address"
        )

        add_label(
            "Permanent Address:",
            2,
            2,
        )

        form_grid.addWidget(
            self.permanent_address_input,
            2,
            3,
        )

        # Admission Date — always taken from the computer clock.
        admission_date_widget = QWidget()

        admission_date_row = QHBoxLayout(
            admission_date_widget
        )

        admission_date_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        admission_date_row.setSpacing(
            6
        )

        self.admission_day_input = QSpinBox()

        self.admission_day_input.setRange(
            1,
            31,
        )

        self.admission_month_input = QSpinBox()

        self.admission_month_input.setRange(
            1,
            12,
        )

        self.admission_year_input = QSpinBox()

        self.admission_year_input.setRange(
            2000,
            max(2000, datetime.now().year),
        )

        for system_admission_input in (
            self.admission_day_input,
            self.admission_month_input,
            self.admission_year_input,
        ):
            system_admission_input.setReadOnly(True)
            system_admission_input.setEnabled(False)
            system_admission_input.setFocusPolicy(
                Qt.FocusPolicy.NoFocus
            )
            system_admission_input.setToolTip(
                "Admission Date is set automatically from the computer date "
                "and cannot be edited."
            )

        self.admission_day_input.setFixedWidth(
            70
        )

        self.admission_month_input.setFixedWidth(
            70
        )

        self.admission_year_input.setFixedWidth(
            95
        )

        admission_date_row.addWidget(
            self.admission_day_input
        )

        admission_date_row.addWidget(
            QLabel("/")
        )

        admission_date_row.addWidget(
            self.admission_month_input
        )

        admission_date_row.addWidget(
            QLabel("/")
        )

        admission_date_row.addWidget(
            self.admission_year_input
        )

        admission_date_row.addStretch()

        add_label(
            "Admission Date (Computer Date):",
            3,
            2,
        )

        form_grid.addWidget(
            admission_date_widget,
            3,
            3,
        )

        # Roll Number
        roll_number_widget = QWidget()

        roll_number_row = QHBoxLayout(
            roll_number_widget
        )

        roll_number_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        roll_number_row.setSpacing(
            8
        )

        self.roll_number_input = QLineEdit()

        self.roll_number_input.setReadOnly(
            True
        )

        self.roll_number_input.setPlaceholderText(
            "Select class and generate"
        )

        self.generate_roll_button = QPushButton(
            "Generate"
        )

        self.generate_roll_button.clicked.connect(
            self._generate_roll_number
        )

        roll_number_row.addWidget(
            self.roll_number_input,
            1,
        )

        roll_number_row.addWidget(
            self.generate_roll_button
        )

        add_label(
            "Roll Number:",
            4,
            2,
        )

        form_grid.addWidget(
            roll_number_widget,
            4,
            3,
        )

        # Admission Number
        admission_number_widget = QWidget()

        admission_number_row = QHBoxLayout(
            admission_number_widget
        )

        admission_number_row.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        admission_number_row.setSpacing(
            8
        )

        self.admission_prefix_combo = QComboBox()

        self.admission_prefix_combo.addItems(
            [
                "H",
                "P",
            ]
        )

        self.admission_prefix_combo.setFixedWidth(
            65
        )

        self.admission_number_digits_input = QLineEdit()

        self.admission_number_digits_input.setPlaceholderText(
            "Click Generate"
        )

        self.admission_number_digits_input.setMaxLength(
            5
        )
        self.admission_number_digits_input.setReadOnly(True)

        self.generate_admission_button = QPushButton("Generate")
        self.generate_admission_button.clicked.connect(
            self._generate_admission_number
        )
        self.admission_prefix_combo.currentTextChanged.connect(
            self._on_admission_prefix_changed
        )

        admission_number_row.addWidget(
            self.admission_prefix_combo
        )

        admission_number_row.addWidget(
            self.admission_number_digits_input,
            1,
        )
        admission_number_row.addWidget(self.generate_admission_button)

        add_label(
            "Admission Number:",
            5,
            2,
        )

        form_grid.addWidget(
            admission_number_widget,
            5,
            3,
        )

        page_layout.addWidget(
            form_container,
            1,
        )

        # ----------------------------------------------------
        # Buttons
        # ----------------------------------------------------

        button_row = QHBoxLayout()

        button_row.setSpacing(
            12
        )

        self.student_next_button = QPushButton(
            "Next"
        )

        self.student_next_button.clicked.connect(
            self._go_to_documents
        )

        clear_button = QPushButton(
            "Clear Form"
        )

        clear_button.setObjectName(
            "secondaryButton"
        )

        clear_button.clicked.connect(
            self._clear_form_with_confirmation
        )

        back_button = QPushButton(
            "Back to Student Management"
        )

        back_button.setObjectName(
            "secondaryButton"
        )

        back_button.clicked.connect(
            self._go_back_with_confirmation
        )

        button_row.addWidget(
            self.student_next_button
        )

        button_row.addWidget(
            clear_button
        )

        button_row.addWidget(
            back_button
        )

        button_row.addStretch()

        page_layout.addLayout(
            button_row
        )

        self.workflow_stack.addWidget(
            self.student_information_page
        )

    # ========================================================
    # Page 2 — Documents Inquiry
    # ========================================================

    def _build_documents_page(
        self,
    ) -> None:
        self.documents_page = QWidget()

        page_layout = QVBoxLayout(
            self.documents_page
        )

        page_layout.setContentsMargins(
            25,
            20,
            25,
            20,
        )

        page_layout.setSpacing(
            18
        )

        title_label = QLabel(
            "Documents Inquiry"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        page_layout.addWidget(
            title_label
        )

        instruction_label = QLabel(
            "Enter the student's date of birth and select "
            "the documents submitted with the admission."
        )

        instruction_label.setWordWrap(
            True
        )

        page_layout.addWidget(
            instruction_label
        )

        documents_frame = QFrame()

        documents_frame.setObjectName(
            "card"
        )

        form_layout = QFormLayout(
            documents_frame
        )

        form_layout.setContentsMargins(
            25,
            25,
            25,
            25,
        )

        form_layout.setSpacing(
            20
        )

        self.date_of_birth_input = QLineEdit()

        self.date_of_birth_input.setPlaceholderText(
            "00-00-0000"
        )

        self.date_of_birth_input.setInputMask(
            "00-00-0000;_"
        )

        self.date_of_birth_input.setMaximumWidth(
            220
        )

        form_layout.addRow(
            "Date of Birth:",
            self.date_of_birth_input,
        )

        self.slc_checkbox = QCheckBox()

        self._set_checkbox_size(
            self.slc_checkbox
        )

        form_layout.addRow(
            "School Leaving Certificate:",
            self.slc_checkbox,
        )

        self.form_b_checkbox = QCheckBox()

        self._set_checkbox_size(
            self.form_b_checkbox
        )

        form_layout.addRow(
            "Form B:",
            self.form_b_checkbox,
        )

        self.birth_certificate_checkbox = QCheckBox()

        self._set_checkbox_size(
            self.birth_certificate_checkbox
        )

        form_layout.addRow(
            "Birth Certificate:",
            self.birth_certificate_checkbox,
        )

        page_layout.addWidget(
            documents_frame
        )

        requirement_label = QLabel(
            "School Leaving Certificate is optional for "
            "Play Group and required for Nursery, KG, "
            "and numbered classes. Form B and Birth "
            "Certificate are optional."
        )

        requirement_label.setWordWrap(
            True
        )

        page_layout.addWidget(
            requirement_label
        )

        page_layout.addStretch()

        button_row = QHBoxLayout()

        documents_back_button = QPushButton(
            "Back"
        )

        documents_back_button.setObjectName(
            "secondaryButton"
        )

        documents_back_button.clicked.connect(
            self._return_to_student_information
        )

        self.save_student_button = QPushButton(
            "Save"
        )

        self.save_student_button.clicked.connect(
            self._save_student
        )

        button_row.addWidget(
            documents_back_button
        )

        button_row.addWidget(
            self.save_student_button
        )

        button_row.addStretch()

        page_layout.addLayout(
            button_row
        )

        self.workflow_stack.addWidget(
            self.documents_page
        )

    # ========================================================
    # Page 3 — Saved Student Details
    # ========================================================

    def _build_saved_details_page(
        self,
    ) -> None:
        self.saved_details_page = QWidget()

        page_layout = QVBoxLayout(
            self.saved_details_page
        )

        page_layout.setContentsMargins(
            20,
            15,
            20,
            15,
        )

        page_layout.setSpacing(
            12
        )

        title_label = QLabel(
            "Student Details"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        page_layout.addWidget(
            title_label
        )

        self.saved_details_scroll = QScrollArea()

        self.saved_details_scroll.setWidgetResizable(
            True
        )

        details_container = QWidget()

        self.saved_details_grid = QGridLayout(
            details_container
        )

        self.saved_details_grid.setContentsMargins(
            20,
            20,
            20,
            20,
        )

        self.saved_details_grid.setHorizontalSpacing(
            25
        )

        self.saved_details_grid.setVerticalSpacing(
            12
        )

        self.saved_details_scroll.setWidget(
            details_container
        )

        page_layout.addWidget(
            self.saved_details_scroll,
            1,
        )

        button_row = QHBoxLayout()

        print_button = QPushButton(
            "Print"
        )

        print_button.clicked.connect(
            self._print_saved_student
        )

        download_button = QPushButton(
            "Download PDF"
        )

        download_button.clicked.connect(
            self._download_saved_student_pdf
        )

        back_button = QPushButton(
            "Back"
        )

        back_button.setObjectName(
            "secondaryButton"
        )

        back_button.clicked.connect(
            self._back_after_saved_details
        )

        button_row.addWidget(
            print_button
        )

        button_row.addWidget(
            download_button
        )

        button_row.addWidget(
            back_button
        )

        button_row.addStretch()

        page_layout.addLayout(
            button_row
        )

        self.workflow_stack.addWidget(
            self.saved_details_page
        )

    # ========================================================
    # Keyboard navigation
    # ========================================================

    def _configure_keyboard_navigation(
        self,
    ) -> None:
        self._enter_action_map = {
            self.class_combo:
                self._focus_widget(
                    self.coming_session_checkbox
                ),

            self.coming_session_checkbox:
                self._focus_widget(
                    self.student_name_input
                ),

            self.student_name_input:
                self._focus_widget(
                    self.gender_combo
                ),

            self.gender_combo:
                self._focus_widget(
                    self.father_name_input
                ),

            self.father_name_input:
                self._focus_widget(
                    self.father_cnic_input
                ),

            self.father_cnic_input:
                self._focus_widget(
                    self.phone_number_input
                ),

            self.phone_number_input:
                self._focus_widget(
                    self.whatsapp_number_input
                ),

            self.whatsapp_number_input:
                self._focus_widget(
                    self.current_address_input
                ),

            self.current_address_input:
                self._focus_widget(
                    self.permanent_address_input
                ),

            self.permanent_address_input:
                self._focus_widget(
                    self.admission_day_input
                ),

            self.admission_day_input:
                self._focus_widget(
                    self.admission_month_input
                ),

            self.admission_month_input:
                self._focus_widget(
                    self.admission_year_input
                ),

            self.admission_year_input:
                self._focus_widget(
                    self.generate_roll_button
                ),

            self.generate_roll_button:
                self._generate_roll_and_continue,

            self.admission_prefix_combo:
                self._focus_widget(
                    self.admission_number_digits_input
                ),

            self.admission_number_digits_input:
                self._focus_widget(
                    self.student_next_button
                ),

            self.student_next_button:
                self._go_to_documents,

            self.date_of_birth_input:
                self._focus_widget(
                    self.slc_checkbox
                ),

            self.slc_checkbox:
                self._focus_widget(
                    self.form_b_checkbox
                ),

            self.form_b_checkbox:
                self._focus_widget(
                    self.birth_certificate_checkbox
                ),

            self.birth_certificate_checkbox:
                self._focus_widget(
                    self.save_student_button
                ),

            self.save_student_button:
                self._save_student,
        }

        for widget in self._enter_action_map:
            widget.installEventFilter(
                self
            )

    def eventFilter(
        self,
        watched,
        event,
    ):
        if (
            event.type()
            == QEvent.Type.KeyPress
            and event.key()
            in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            )
        ):
            action = self._enter_action_map.get(
                watched
            )

            if action is not None:
                action()
                return True

        return super().eventFilter(
            watched,
            event,
        )

    def _sync_admission_date_to_system_clock(self) -> datetime:
        """Refresh the locked Add Student admission date from Windows."""
        current_datetime = datetime.now()

        self.admission_year_input.setMaximum(
            max(2000, current_datetime.year)
        )

        for admission_input, value in (
            (self.admission_day_input, current_datetime.day),
            (self.admission_month_input, current_datetime.month),
            (self.admission_year_input, current_datetime.year),
        ):
            admission_input.blockSignals(True)
            admission_input.setValue(value)
            admission_input.blockSignals(False)

        self._refresh_coming_session_checkbox_availability()
        return current_datetime

    def _refresh_coming_session_checkbox_availability(self, *_args) -> None:
        available = coming_session_service.is_coming_session_option_available(
            self.admission_year_input.value(),
            self.admission_month_input.value(),
            self.admission_day_input.value(),
        )
        if not available and self.coming_session_checkbox.isChecked():
            self.coming_session_checkbox.blockSignals(True)
            self.coming_session_checkbox.setChecked(False)
            self.coming_session_checkbox.blockSignals(False)
        self.coming_session_checkbox.setEnabled(available)
        self.coming_session_checkbox.setToolTip(
            "Available only for January-March admissions before the coming session starts."
            if available else
            "Coming-session admission is unavailable for the selected admission date."
        )
        self._apply_coming_session_roll_state()

    def _on_coming_session_toggled(self, checked: bool) -> None:
        if checked:
            confirmation = QMessageBox.question(
                self,
                "Admission for Coming Session",
                "This student will join the coming academic session.\n\n"
                "No roll will be assigned before 1 April, no monthly fee will "
                "be generated before April, and the student will be excluded "
                "from the ending session's promotion.\n\nContinue?",
            )
            if confirmation != QMessageBox.Yes:
                self.coming_session_checkbox.blockSignals(True)
                self.coming_session_checkbox.setChecked(False)
                self.coming_session_checkbox.blockSignals(False)
        self._apply_coming_session_roll_state()

    def _apply_coming_session_roll_state(self) -> None:
        pending = self.coming_session_checkbox.isChecked()
        self.generate_roll_button.setEnabled(not pending)
        if pending:
            self.roll_number_input.setText("Pending")
            self.roll_number_input.setPlaceholderText("Assigned when the coming session starts")
        else:
            if self.roll_number_input.text().strip() == "Pending":
                self.roll_number_input.clear()
            self.roll_number_input.setPlaceholderText("Select class and generate")

    # ========================================================
    # Class and number handling
    # ========================================================

    def _reload_class_dropdown(
        self,
    ) -> None:
        self.class_combo.blockSignals(
            True
        )

        self.class_combo.clear()

        self.class_combo.addItem(
            "-- Select Class --",
            None,
        )

        for class_row in (
            class_service.get_all_classes()
        ):
            self.class_combo.addItem(
                class_row["class_name"],
                class_row["id"],
            )

        self.class_combo.blockSignals(
            False
        )

        self.selected_class_id = None

    def _on_class_changed(
        self,
    ) -> None:
        self.selected_class_id = (
            self.class_combo.currentData()
        )

        if not self.coming_session_checkbox.isChecked():
            self.roll_number_input.clear()

    def _generate_roll_number(
        self,
    ) -> bool:
        if self.coming_session_checkbox.isChecked():
            self.roll_number_input.setText("Pending")
            return True
        if not self.selected_class_id:
            QMessageBox.warning(
                self,
                "Select Class",
                (
                    "Please select a class before "
                    "generating the roll number."
                ),
            )

            self.class_combo.setFocus()
            return False

        roll_number = (
            student_service.generate_roll_number(
                self.selected_class_id
            )
        )

        self.roll_number_input.setText(
            str(roll_number)
        )

        return True

    def _generate_roll_and_continue(
        self,
    ) -> None:
        if self._generate_roll_number():
            self.admission_prefix_combo.setFocus()

    def _on_admission_prefix_changed(self, _prefix: str) -> None:
        # A generated number belongs to the prefix selected at generation
        # time. Changing H/P invalidates the displayed candidate.
        self.admission_number_digits_input.clear()

    def _generate_admission_number(self) -> bool:
        prefix = self.admission_prefix_combo.currentText().strip()
        try:
            admission_number = student_service.generate_admission_number(prefix)
        except ValueError as error:
            QMessageBox.warning(self, "Admission Number", str(error))
            return False
        self.admission_number_digits_input.setText(admission_number[1:])
        return True

    # ========================================================
    # Page 1 validation
    # ========================================================

    def _collect_student_information(
        self,
    ):
        if not self.selected_class_id:
            QMessageBox.warning(
                self,
                "Missing Class",
                "Please select a class.",
            )

            self.class_combo.setFocus()
            return None

        class_row = class_service.get_class_by_id(
            self.selected_class_id
        )

        if not class_row:
            QMessageBox.warning(
                self,
                "Class Not Found",
                "The selected class could not be found.",
            )
            return None

        student_name = (
            self.student_name_input.text().strip()
        )

        if not student_service.validate_person_name(
            student_name
        ):
            QMessageBox.warning(
                self,
                "Invalid Student Name",
                (
                    "Please enter a valid student "
                    "name using letters only."
                ),
            )

            self.student_name_input.setFocus()
            return None

        gender = (
            self.gender_combo.currentData()
            or ""
        )

        if not student_service.validate_gender(
            gender
        ):
            QMessageBox.warning(
                self,
                "Missing Gender",
                "Please select the student's gender.",
            )

            self.gender_combo.setFocus()
            return None

        father_name = (
            self.father_name_input.text().strip()
        )

        if not student_service.validate_person_name(
            father_name
        ):
            QMessageBox.warning(
                self,
                "Invalid Father Name",
                (
                    "Please enter a valid father "
                    "name using letters only."
                ),
            )

            self.father_name_input.setFocus()
            return None

        father_cnic = (
            self.father_cnic_input.text().strip()
        )

        if not student_service.validate_cnic(
            father_cnic
        ):
            QMessageBox.warning(
                self,
                "Invalid Father CNIC",
                (
                    "Father CNIC is required and must contain exactly "
                    "13 numeric digits."
                ),
            )

            self.father_cnic_input.setFocus()
            return None

        existing_student = (
            student_service.find_existing_student_identity(
                student_name=student_name,
                father_name=father_name,
                father_cnic=father_cnic,
            )
        )

        if existing_student is not None:
            QMessageBox.warning(
                self,
                "Student Record Already Exists",
                (
                    "A student with the same name, "
                    "father name, and Father CNIC "
                    "already exists.\n\n"
                    "Please search and update the "
                    "existing record instead."
                ),
            )

            return None

        phone_digits = (
            self.phone_number_input.text().strip()
        )

        if not student_service.validate_contact_number(
            phone_digits
        ):
            QMessageBox.warning(
                self,
                "Invalid Phone Number",
                (
                    "Please enter exactly 10 digits "
                    "after +92."
                ),
            )

            self.phone_number_input.setFocus()
            return None

        whatsapp_digits = (
            self.whatsapp_number_input.text().strip()
        )

        if (
            not student_service
            .validate_optional_contact_number(
                whatsapp_digits
            )
        ):
            QMessageBox.warning(
                self,
                "Invalid WhatsApp Number",
                (
                    "WhatsApp Number must contain "
                    "exactly 10 digits after +92 "
                    "or be left empty."
                ),
            )

            self.whatsapp_number_input.setFocus()
            return None

        current_address = (
            self.current_address_input.text().strip()
        )

        if not current_address:
            QMessageBox.warning(
                self,
                "Missing Current Address",
                "Please enter the current address.",
            )

            self.current_address_input.setFocus()
            return None

        permanent_address = (
            self.permanent_address_input.text().strip()
        )

        if not permanent_address:
            QMessageBox.warning(
                self,
                "Missing Permanent Address",
                "Please enter the permanent address.",
            )

            self.permanent_address_input.setFocus()
            return None

        self._sync_admission_date_to_system_clock()

        admission_day = (
            self.admission_day_input.value()
        )

        admission_month = (
            self.admission_month_input.value()
        )

        admission_year = (
            self.admission_year_input.value()
        )

        if not student_service.validate_admission_date(
            admission_year,
            admission_month,
            admission_day,
        ):
            QMessageBox.warning(
                self,
                "Invalid Admission Date",
                (
                    "The selected admission date is "
                    "not a valid calendar date."
                ),
            )

            self.admission_day_input.setFocus()
            return None

        selected_admission_date = datetime(
            admission_year, admission_month, admission_day
        ).date()
        if selected_admission_date > datetime.now().date():
            QMessageBox.warning(
                self,
                "Invalid Admission Date",
                "Admission Date cannot be in the future.",
            )
            self.admission_day_input.setFocus()
            return None

        coming_session_admission = self.coming_session_checkbox.isChecked()
        if coming_session_admission:
            roll_number = -1
        else:
            roll_number_text = self.roll_number_input.text().strip()

            if not roll_number_text.isdigit():
                QMessageBox.warning(
                    self,
                    "Missing Roll Number",
                    "Please generate the student's roll number.",
                )
                self.generate_roll_button.setFocus()
                return None

            roll_number = int(roll_number_text)
            if not student_service.is_roll_number_unique_in_class(
                self.selected_class_id, roll_number
            ):
                QMessageBox.warning(
                    self,
                    "Duplicate Roll Number",
                    "This roll number is already used in the selected class. "
                    "Please generate another roll number.",
                )
                self.generate_roll_button.setFocus()
                return None

        admission_prefix = (
            self.admission_prefix_combo
            .currentText()
            .strip()
        )

        admission_digits = (
            self.admission_number_digits_input
            .text()
            .strip()
        )

        try:
            admission_number = (
                student_service
                .build_manual_admission_number(
                    admission_prefix,
                    admission_digits,
                )
            )

        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Admission Number",
                str(error),
            )

            self.admission_number_digits_input.setFocus()
            return None

        if not student_service.is_admission_number_unique(
            admission_number
        ):
            QMessageBox.warning(
                self,
                "Duplicate Admission Number",
                (
                    f"Admission Number {admission_number} "
                    "is already in use."
                ),
            )

            self.admission_number_digits_input.setFocus()
            return None

        return {
            "class_id": self.selected_class_id,
            "class_level": class_row["class_level"],
            "class_name": class_row["class_name"],
            "student_name": student_name,
            "gender": gender,
            "father_name": father_name,
            "father_cnic": father_cnic,
            "father_contact_number": (
                student_service.format_contact_number(
                    phone_digits
                )
            ),
            "whatsapp_number": (
                student_service
                .format_optional_contact_number(
                    whatsapp_digits
                )
            ),
            "current_address": current_address,
            "permanent_address": permanent_address,
            "admission_year": admission_year,
            "admission_month": admission_month,
            "admission_day": admission_day,
            "roll_number": roll_number,
            "admission_number": admission_number,
            "coming_session_admission": coming_session_admission,
        }

    def _go_to_documents(
        self,
    ) -> None:
        information = (
            self._collect_student_information()
        )

        if information is None:
            return

        self.pending_student_data = (
            information
        )

        self.workflow_stack.setCurrentWidget(
            self.documents_page
        )

        self.date_of_birth_input.setFocus()

    def _return_to_student_information(
        self,
    ) -> None:
        self.workflow_stack.setCurrentWidget(
            self.student_information_page
        )

        self.student_name_input.setFocus()

    # ========================================================
    # Save
    # ========================================================

    def _extract_date_of_birth(
        self,
    ):
        raw_text = (
            self.date_of_birth_input.text()
        )

        digits = "".join(
            character
            for character in raw_text
            if character.isdigit()
        )

        if len(digits) != 8:
            QMessageBox.warning(
                self,
                "Invalid Date of Birth",
                (
                    "Please enter Date of Birth in "
                    "DD-MM-YYYY format."
                ),
            )

            self.date_of_birth_input.setFocus()
            return None

        display_date = (
            f"{digits[0:2]}-"
            f"{digits[2:4]}-"
            f"{digits[4:8]}"
        )

        try:
            student_service.normalize_date_of_birth(display_date)

        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Date of Birth",
                (
                    "The entered Date of Birth is not "
                    "a valid calendar date."
                ),
            )

            self.date_of_birth_input.setFocus()
            return None

        return display_date

    def _save_student(
        self,
    ) -> None:
        if self.pending_student_data is None:
            information = (
                self._collect_student_information()
            )

            if information is None:
                self.workflow_stack.setCurrentWidget(
                    self.student_information_page
                )
                return

            self.pending_student_data = (
                information
            )

        date_of_birth = (
            self._extract_date_of_birth()
        )

        if date_of_birth is None:
            return

        admission_datetime = datetime(
            self.pending_student_data["admission_year"],
            self.pending_student_data["admission_month"],
            self.pending_student_data["admission_day"],
        )
        try:
            student_service.validate_date_of_birth_before_admission(
                date_of_birth,
                admission_datetime,
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Date of Birth",
                str(error),
            )
            self.date_of_birth_input.setFocus()
            return

        slc_submitted = (
            self.slc_checkbox.isChecked()
        )

        if (
            student_service.is_slc_required(
                self.pending_student_data[
                    "class_level"
                ]
            )
            and not slc_submitted
        ):
            QMessageBox.warning(
                self,
                "School Leaving Certificate Required",
                (
                    "School Leaving Certificate is "
                    "required for the selected class."
                ),
            )

            self.slc_checkbox.setFocus()
            return

        try:
            new_student_id = (
                student_service.add_student(
                    admission_number=(
                        self.pending_student_data[
                            "admission_number"
                        ]
                    ),
                    roll_number=(
                        self.pending_student_data[
                            "roll_number"
                        ]
                    ),
                    class_id=(
                        self.pending_student_data[
                            "class_id"
                        ]
                    ),
                    student_name=(
                        self.pending_student_data[
                            "student_name"
                        ]
                    ),
                    gender=(
                        self.pending_student_data[
                            "gender"
                        ]
                    ),
                    father_name=(
                        self.pending_student_data[
                            "father_name"
                        ]
                    ),
                    father_cnic=(
                        self.pending_student_data[
                            "father_cnic"
                        ]
                    ),
                    father_contact_number=(
                        self.pending_student_data[
                            "father_contact_number"
                        ]
                    ),
                    whatsapp_number=(
                        self.pending_student_data[
                            "whatsapp_number"
                        ]
                    ),
                    current_address=(
                        self.pending_student_data[
                            "current_address"
                        ]
                    ),
                    permanent_address=(
                        self.pending_student_data[
                            "permanent_address"
                        ]
                    ),
                    school_leaving_certificate_submitted=(
                        slc_submitted
                    ),
                    form_b_submitted=(
                        self.form_b_checkbox.isChecked()
                    ),
                    birth_certificate_submitted=(
                        self.birth_certificate_checkbox
                        .isChecked()
                    ),
                    date_of_birth=date_of_birth,
                    admission_year=(
                        self.pending_student_data[
                            "admission_year"
                        ]
                    ),
                    admission_month=(
                        self.pending_student_data[
                            "admission_month"
                        ]
                    ),
                    admission_day=(
                        self.pending_student_data[
                            "admission_day"
                        ]
                    ),
                    admission_time="",
                    coming_session_admission=(
                        self.pending_student_data["coming_session_admission"]
                    ),
                    use_system_admission_datetime=True,
                )
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Student Save Error",
                (
                    "The student record could not be "
                    "saved.\n\n"
                    f"Details: {error}"
                ),
            )
            return

        self.saved_student_id = (
            new_student_id
        )

        QMessageBox.information(
            self,
            "Student Saved",
            (
                "Student admission has been saved "
                "successfully."
            ),
        )

        self._load_saved_student_details(
            new_student_id
        )

    # ========================================================
    # Saved details
    # ========================================================

    def _build_saved_detail_fields(
        self,
        student_row,
    ):
        return [
            (
                "Admission Number",
                self._safe_text(
                    student_row[
                        "admission_number"
                    ]
                ),
            ),
            (
                "Roll Number",
                self._safe_text(
                    student_row[
                        "roll_number"
                    ]
                ),
            ),
            (
                "Class",
                self._safe_text(
                    student_row[
                        "class_name"
                    ]
                ),
            ),
            (
                "Student Name",
                self._safe_text(
                    student_row[
                        "student_name"
                    ]
                ),
            ),
            (
                "Gender",
                self._safe_text(
                    student_row[
                        "gender"
                    ]
                ),
            ),
            (
                "Father Name",
                self._safe_text(
                    student_row[
                        "father_name"
                    ]
                ),
            ),
            (
                "Father CNIC",
                self._safe_text(
                    student_row[
                        "father_cnic"
                    ]
                ),
            ),
            (
                "Phone Number",
                self._safe_text(
                    student_row[
                        "father_contact_number"
                    ]
                ),
            ),
            (
                "WhatsApp Number",
                self._safe_text(
                    student_row[
                        "whatsapp_number"
                    ]
                ),
            ),
            (
                "Current Address",
                self._safe_text(
                    student_row[
                        "current_address"
                    ]
                ),
            ),
            (
                "Permanent Address",
                self._safe_text(
                    student_row[
                        "permanent_address"
                    ]
                ),
            ),
            (
                "Admission Date",
                format_admission_date(
                    student_row
                ),
            ),
            (
                "Date of Birth",
                (
                    student_service
                    .format_date_of_birth_for_display(
                        student_row[
                            "date_of_birth"
                        ]
                    )
                ),
            ),
        ]

    def _build_saved_detail_html(
        self,
        fields,
    ) -> str:
        """
        Build the professional Add Student print and PDF.
        """
        if not self.saved_student_row:
            return ""

        generated_display = (
            datetime.now().strftime(
                "%d %B %Y, %I:%M %p"
            )
        )

        return (
            build_professional_student_print_html(
                student_row=(
                    self.saved_student_row
                ),
                document_title=(
                    "STUDENT ADMISSION RECORD"
                ),
                date_caption=(
                    "Generated on"
                ),
                date_value=(
                    generated_display
                ),
            )
        )


    def _load_saved_student_details(
        self,
        student_id: int,
    ) -> None:
        student_row = (
            student_service.get_student_by_id(
                student_id
            )
        )

        if not student_row:
            QMessageBox.warning(
                self,
                "Student Not Found",
                (
                    "The saved student record could "
                    "not be loaded."
                ),
            )
            return

        self.saved_student_row = (
            student_row
        )

        self._clear_layout(
            self.saved_details_grid
        )

        fields = (
            self._build_saved_detail_fields(
                student_row
            )
        )

        for row_index, (
            label_text,
            value_text,
        ) in enumerate(fields):
            label = QLabel(
                f"{label_text}:"
            )

            label.setStyleSheet(
                "font-weight: bold;"
            )

            value = QLabel(
                str(value_text)
            )

            value.setWordWrap(
                True
            )

            value.setTextInteractionFlags(
                Qt.TextInteractionFlag
                .TextSelectableByMouse
            )

            self.saved_details_grid.addWidget(
                label,
                row_index,
                0,
                Qt.AlignmentFlag.AlignTop,
            )

            self.saved_details_grid.addWidget(
                value,
                row_index,
                1,
            )

        self.saved_details_grid.setColumnStretch(
            1,
            1,
        )

        self.saved_detail_html = (
            self._build_saved_detail_html(
                fields
            )
        )

        self.workflow_stack.setCurrentWidget(
            self.saved_details_page
        )

    def _print_saved_student(
        self,
    ) -> None:
        if not self.saved_detail_html:
            QMessageBox.warning(
                self,
                "No Student Details",
                "No saved student details are available.",
            )
            return

        printer = QPrinter(
            QPrinter.HighResolution
        )

        printer.setPageSize(
            QPageSize(
                QPageSize.PageSizeId.A4
            )
        )

        printer.setPageOrientation(
            QPageLayout.Orientation.Portrait
        )

        printer.setPageMargins(
            QMarginsF(
                10,
                10,
                10,
                10,
            ),
            QPageLayout.Unit.Millimeter,
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )

        print_dialog.setWindowTitle(
            "Print Student Details"
        )

        if (
            print_dialog.exec()
            != QDialog.Accepted
        ):
            return

        try:
            document = QTextDocument()

            document.setPageSize(
                printer.pageRect(
                    QPrinter.Unit.Point
                ).size()
            )

            document.setDocumentMargin(
                12
            )

            document.setHtml(
                self.saved_detail_html
            )

            document.print_(
                printer
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Print Error",
                (
                    "Student details could not be "
                    "printed.\n\n"
                    f"Details: {error}"
                ),
            )

    def _download_saved_student_pdf(
        self,
    ) -> None:
        if (
            not self.saved_detail_html
            or not self.saved_student_row
        ):
            QMessageBox.warning(
                self,
                "No Student Details",
                "No saved student details are available.",
            )
            return

        admission_number = (
            self.saved_student_row[
                "admission_number"
            ]
        )

        suggested_name = (
            f"Student_{admission_number}_Details.pdf"
        )

        save_path, _selected_filter = (
            QFileDialog.getSaveFileName(
                self,
                "Download Student Details",
                suggested_name,
                "PDF Files (*.pdf)",
            )
        )

        if not save_path:
            return

        if not save_path.lower().endswith(
            ".pdf"
        ):
            save_path += ".pdf"

        try:
            printer = QPrinter(
                QPrinter.HighResolution
            )

            printer.setOutputFormat(
                QPrinter.OutputFormat.PdfFormat
            )

            printer.setOutputFileName(
                save_path
            )

            document = QTextDocument()

            document.setPageSize(
                printer.pageRect(
                    QPrinter.Unit.Point
                ).size()
            )

            document.setDocumentMargin(
                12
            )

            document.setHtml(
                self.saved_detail_html
            )

            document.print_(
                printer
            )

            QMessageBox.information(
                self,
                "PDF Downloaded",
                (
                    "Student details were downloaded "
                    "successfully."
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                (
                    "Student details could not be "
                    "downloaded.\n\n"
                    f"Details: {error}"
                ),
            )

    def _back_after_saved_details(
        self,
    ) -> None:
        self.reset_form()

        self.workflow_stack.setCurrentWidget(
            self.student_information_page
        )

        self.class_combo.setFocus()

    # ========================================================
    # Reset and navigation
    # ========================================================

    def _has_unsaved_data(
        self,
    ) -> bool:
        return bool(
            self.selected_class_id
            or self.coming_session_checkbox.isChecked()
            or self.student_name_input.text().strip()
            or self.gender_combo.currentData()
            or self.father_name_input.text().strip()
            or self.father_cnic_input.text().strip()
            or self.phone_number_input.text().strip()
            or self.whatsapp_number_input.text().strip()
            or self.current_address_input.text().strip()
            or self.permanent_address_input.text().strip()
            or self.roll_number_input.text().strip()
            or self.admission_number_digits_input
            .text()
            .strip()
            or "".join(
                character
                for character in (
                    self.date_of_birth_input.text()
                )
                if character.isdigit()
            )
            or self.slc_checkbox.isChecked()
            or self.form_b_checkbox.isChecked()
            or self.birth_certificate_checkbox
            .isChecked()
        )

    def _clear_form_with_confirmation(
        self,
    ) -> None:
        if self._has_unsaved_data():
            confirmation = QMessageBox.question(
                self,
                "Confirm Clear",
                (
                    "Are you sure you want to clear "
                    "the complete admission form?"
                ),
            )

            if confirmation != QMessageBox.Yes:
                return

        self.reset_form()

    def reset_form(
        self,
    ) -> None:
        self.pending_student_data = None
        self.saved_student_id = None
        self.saved_student_row = None
        self.saved_detail_html = ""

        self._reload_class_dropdown()

        self.coming_session_checkbox.blockSignals(True)
        self.coming_session_checkbox.setChecked(False)
        self.coming_session_checkbox.blockSignals(False)
        self.student_name_input.clear()
        self.gender_combo.setCurrentIndex(
            0
        )
        self.father_name_input.clear()
        self.father_cnic_input.clear()
        self.phone_number_input.clear()
        self.whatsapp_number_input.clear()
        self.current_address_input.clear()
        self.permanent_address_input.clear()
        self.roll_number_input.clear()

        self.admission_prefix_combo.setCurrentText(
            "H"
        )

        self.admission_number_digits_input.clear()

        self._sync_admission_date_to_system_clock()

        self.date_of_birth_input.clear()
        self.slc_checkbox.setChecked(
            False
        )
        self.form_b_checkbox.setChecked(
            False
        )
        self.birth_certificate_checkbox.setChecked(
            False
        )

        self.workflow_stack.setCurrentWidget(
            self.student_information_page
        )

        self.class_combo.setFocus()

    def _go_back_with_confirmation(
        self,
    ) -> None:
        if self._has_unsaved_data():
            confirmation = QMessageBox.question(
                self,
                "Unsaved Information",
                (
                    "The admission form contains "
                    "unsaved information.\n\n"
                    "Do you want to leave this form?"
                ),
            )

            if confirmation != QMessageBox.Yes:
                return

        self.reset_form()

        self.main_window.go_to_page(
            navigation.PAGE_STUDENT_DASHBOARD
        )


class StudentResultCard(QWidget):
    """
    Display a student record with Edit, Print and Download PDF
    actions.
    """

    def __init__(
        self,
        student_row,
        on_edit_clicked,
    ):
        super().__init__()

        self.student_row = dict(
            student_row
        )

        outer_layout = QVBoxLayout(
            self
        )

        outer_layout.setContentsMargins(
            0,
            0,
            0,
            20,
        )

        outer_layout.setSpacing(
            10
        )

        details_frame = QFrame()

        details_frame.setObjectName(
            "card"
        )

        details_grid = QGridLayout(
            details_frame
        )

        details_grid.setContentsMargins(
            16,
            12,
            16,
            12,
        )

        details_grid.setHorizontalSpacing(
            16
        )

        details_grid.setVerticalSpacing(
            8
        )

        details_grid.setColumnStretch(
            1,
            1,
        )

        details_grid.setColumnStretch(
            3,
            1,
        )

        fields = build_student_detail_fields(
            self.student_row
        )

        for field_index, (
            label_text,
            value_text,
        ) in enumerate(fields):
            row_index = (
                field_index // 2
            )

            start_column = (
                0
                if field_index % 2 == 0
                else 2
            )

            label = QLabel(
                f"{label_text}:"
            )

            label.setStyleSheet(
                "font-weight: bold;"
            )

            label.setAlignment(
                Qt.AlignmentFlag.AlignTop
                | Qt.AlignmentFlag.AlignRight
            )

            value = QLabel(
                str(value_text)
            )

            value.setWordWrap(
                True
            )

            value.setTextInteractionFlags(
                Qt.TextInteractionFlag
                .TextSelectableByMouse
            )

            details_grid.addWidget(
                label,
                row_index,
                start_column,
            )

            details_grid.addWidget(
                value,
                row_index,
                start_column + 1,
            )

        outer_layout.addWidget(
            details_frame
        )

        button_row = QHBoxLayout()

        edit_button = QPushButton(
            "Edit"
        )

        print_button = QPushButton(
            "Print"
        )

        download_button = QPushButton(
            "Download PDF"
        )

        print_button.setObjectName(
            "secondaryButton"
        )

        download_button.setObjectName(
            "secondaryButton"
        )

        edit_button.setMinimumWidth(
            110
        )

        print_button.setMinimumWidth(
            110
        )

        download_button.setMinimumWidth(
            135
        )

        student_id = self.student_row[
            "student_id"
        ]

        edit_button.clicked.connect(
            lambda checked=False: (
                on_edit_clicked(
                    student_id
                )
            )
        )

        print_button.clicked.connect(
            self._print_student
        )

        download_button.clicked.connect(
            self._download_student_pdf
        )

        button_row.addWidget(
            edit_button
        )

        button_row.addWidget(
            print_button
        )

        button_row.addWidget(
            download_button
        )

        button_row.addStretch()

        outer_layout.addLayout(
            button_row
        )

        separator = QFrame()

        separator.setFrameShape(
            QFrame.HLine
        )

        separator.setStyleSheet(
            "color: #D5D9E0;"
        )

        outer_layout.addWidget(
            separator
        )

    def _build_search_print_html(
        self,
    ) -> str:
        printed_display = (
            datetime.now().strftime(
                "%d %B %Y, %I:%M %p"
            )
        )

        return (
            build_professional_student_print_html(
                student_row=(
                    self.student_row
                ),
                document_title=(
                    "STUDENT INFORMATION RECORD"
                ),
                date_caption=(
                    "Printed on"
                ),
                date_value=(
                    printed_display
                ),
            )
        )

    def _print_student(
        self,
    ) -> None:
        print_html = (
            self._build_search_print_html()
        )

        printer = QPrinter(
            QPrinter.HighResolution
        )

        printer.setPageSize(
            QPageSize(
                QPageSize.PageSizeId.A4
            )
        )

        printer.setPageOrientation(
            QPageLayout.Orientation.Portrait
        )

        printer.setPageMargins(
            QMarginsF(
                10,
                10,
                10,
                10,
            ),
            QPageLayout.Unit.Millimeter,
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )

        print_dialog.setWindowTitle(
            "Print Student Record"
        )

        if (
            print_dialog.exec()
            != QDialog.Accepted
        ):
            return

        try:
            document = QTextDocument()

            document.setPageSize(
                printer.pageRect(
                    QPrinter.Unit.Point
                ).size()
            )

            document.setDocumentMargin(
                8
            )

            document.setHtml(
                print_html
            )

            document.print_(
                printer
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Print Error",
                (
                    "Student record could not be "
                    "printed.\n\n"
                    f"Details: {error}"
                ),
            )

    def _download_student_pdf(
        self,
    ) -> None:
        admission_number = (
            self.student_row[
                "admission_number"
            ]
        )

        suggested_name = (
            f"Student_"
            f"{admission_number}_"
            f"Record.pdf"
        )

        save_path, _selected_filter = (
            QFileDialog.getSaveFileName(
                self,
                "Download Student Record",
                suggested_name,
                "PDF Files (*.pdf)",
            )
        )

        if not save_path:
            return

        if not save_path.lower().endswith(
            ".pdf"
        ):
            save_path += ".pdf"

        try:
            printer = QPrinter(
                QPrinter.HighResolution
            )

            printer.setOutputFormat(
                QPrinter.OutputFormat.PdfFormat
            )

            printer.setOutputFileName(
                save_path
            )

            document = QTextDocument()

            document.setPageSize(
                printer.pageRect(
                    QPrinter.Unit.Point
                ).size()
            )

            document.setDocumentMargin(
                8
            )

            document.setHtml(
                self._build_search_print_html()
            )

            document.print_(
                printer
            )

            QMessageBox.information(
                self,
                "PDF Downloaded",
                (
                    "Student record was downloaded "
                    "successfully."
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                (
                    "Student record could not be "
                    "downloaded.\n\n"
                    f"Details: {error}"
                ),
            )



class SearchStudentPage(QWidget):
    """
    The Student Search screen. Results are shown directly as full detail
    cards (no separate View Details step needed), each with an Edit button.
    The entire page (search form + results) scrolls together naturally,
    rather than squeezing results into a small separate scroll box.
    """

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        # --- Outer scroll area wraps the whole page content ---
        page_scroll_area = QScrollArea()
        page_scroll_area.setWidgetResizable(True)
        page_scroll_area.setFrameShape(QFrame.NoFrame)

        content_widget = QWidget()
        outer_layout = QVBoxLayout(content_widget)
        outer_layout.setContentsMargins(40, 40, 40, 40)
        outer_layout.setSpacing(15)
        outer_layout.setAlignment(Qt.AlignTop)

        page_scroll_area.setWidget(content_widget)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(page_scroll_area)

        title_label = QLabel("Search Student")
        title_label.setObjectName("pageTitle")
        outer_layout.addWidget(title_label)

        # --- Search fields ---
        search_form = QFormLayout()
        search_form.setSpacing(10)

        self.admission_number_input = QLineEdit()
        self.admission_number_input.setPlaceholderText("e.g. 261174")
        search_form.addRow("Admission Number:", self.admission_number_input)

        self.roll_number_input = QLineEdit()
        self.roll_number_input.setPlaceholderText("e.g. 11")
        search_form.addRow("Roll Number:", self.roll_number_input)

        self.student_name_input = QLineEdit()
        self.student_name_input.setMaxLength(200)
        self.student_name_input.setPlaceholderText("e.g. Syed Sohaib")
        search_form.addRow("Student Name:", self.student_name_input)

        self.father_name_search_input = QLineEdit()
        self.father_name_search_input.setPlaceholderText("e.g. Ahmad Khan")
        search_form.addRow("Father Name:", self.father_name_search_input)

        self.father_cnic_search_input = QLineEdit()
        self.father_cnic_search_input.setPlaceholderText(
            "13 digits, e.g. 1730112345671"
        )
        self.father_cnic_search_input.setMaxLength(13)
        self.father_cnic_search_input.returnPressed.connect(
            self._perform_search
        )
        search_form.addRow(
            "Father CNIC:",
            self.father_cnic_search_input,
        )

        self.class_combo = QComboBox()
        search_form.addRow("Select Class:", self.class_combo)

        outer_layout.addLayout(search_form)

        # --- Search / Clear buttons ---
        button_row = QHBoxLayout()

        search_button = QPushButton("Search")
        search_button.clicked.connect(self._perform_search)
        button_row.addWidget(search_button)

        clear_button = QPushButton("Clear Search")
        clear_button.setObjectName("secondaryButton")
        clear_button.clicked.connect(self._clear_search)
        button_row.addWidget(clear_button)

        outer_layout.addLayout(button_row)

        # --- Results area (plain vertical list of detail cards) ---
        self.results_layout = QVBoxLayout()
        self.results_layout.setSpacing(15)
        self.results_layout.setAlignment(Qt.AlignTop)

        self.no_results_label = QLabel("Enter search details above and click Search.")
        self.results_layout.addWidget(self.no_results_label)

        outer_layout.addLayout(self.results_layout)

        # --- Back button ---
        back_button = QPushButton("Back to Student Management")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_STUDENT_DASHBOARD)
        )
        outer_layout.addWidget(back_button)

    # -------------------------------------------------------------
    # Class dropdown
    # -------------------------------------------------------------

    def reload_class_dropdown(self):
        """Reload the class list into the dropdown, keeping 'All Classes' as the first option."""
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItem("All Classes", None)
        for class_row in class_service.get_all_classes(include_inactive=True):
            label = class_row["class_name"]
            if not bool(class_row["is_active"]):
                label += " (Inactive)"
            self.class_combo.addItem(label, class_row["id"])
        self.class_combo.blockSignals(False)

    # -------------------------------------------------------------
    # Search / Clear
    # -------------------------------------------------------------

    def _perform_search(self):
        admission_number = (
            self.admission_number_input.text().strip().upper()
            or None
        )

        roll_number = self.roll_number_input.text().strip() or None
        if roll_number:
            try:
                student_service.parse_roll_number_search(roll_number)
            except ValueError as error:
                QMessageBox.warning(
                    self,
                    "Invalid Roll Number",
                    str(error),
                )
                self.roll_number_input.setFocus()
                return

        student_name = (
            self.student_name_input.text().strip()
            or None
        )

        father_name = (
            self.father_name_search_input.text().strip()
            or None
        )

        father_cnic = (
            self.father_cnic_search_input.text().strip()
        )

        if (
            father_cnic
            and not student_service.validate_cnic(
                father_cnic
            )
        ):
            QMessageBox.warning(
                self,
                "Invalid Father CNIC",
                (
                    "Father CNIC must contain exactly "
                    "13 numeric digits."
                ),
            )
            self.father_cnic_search_input.setFocus()
            return

        father_cnic = father_cnic or None
        class_id = self.class_combo.currentData()

        if not any(
            (
                admission_number,
                roll_number,
                student_name,
                father_name,
                father_cnic,
                class_id,
            )
        ):
            QMessageBox.warning(
                self,
                "Enter Search Details",
                "Enter at least one student detail or select a class. "
                "Loading every student as full detail cards can make the "
                "screen unresponsive.",
            )
            return

        try:
            results = student_service.search_students(
                admission_number=admission_number,
                student_name=student_name,
                father_name=father_name,
                father_cnic=father_cnic,
                class_id=class_id,
                roll_number=roll_number,
                include_archived=True,
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Student Search Error",
                "Student records could not be searched.\n\n"
                f"Details: {error}",
            )
            return

        self._populate_results(results)

    def _clear_search(self):
        self.admission_number_input.clear()
        self.roll_number_input.clear()
        self.student_name_input.clear()
        self.father_name_search_input.clear()
        self.father_cnic_search_input.clear()
        self.class_combo.setCurrentIndex(0)
        self._clear_results_layout()
        self.no_results_label = QLabel("Enter search details above and click Search.")
        self.results_layout.addWidget(self.no_results_label)

    # -------------------------------------------------------------
    # Results display
    # -------------------------------------------------------------

    def _clear_results_layout(self):
        """Remove every card currently shown in the results area."""
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate_results(self, results):
        self._clear_results_layout()

        if not results:
            no_results_label = QLabel("No matching student records were found.")
            self.results_layout.addWidget(no_results_label)
            return

        for student_row in results:
            card = StudentResultCard(student_row, self._open_edit)
            self.results_layout.addWidget(card)

    def _open_edit(self, student_id: int):
        self.main_window.pages[navigation.PAGE_EDIT_STUDENT].load_student(student_id)
        self.main_window.go_to_page(navigation.PAGE_EDIT_STUDENT)


# =====================================================================
# View Student Details
# (Kept available for future use, e.g. Class Management or reports.
#  Search Student now shows this same detail format directly.)
# =====================================================================

class ViewStudentDetailsPage(QWidget):
    """Read-only detailed view of a single student record."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(40, 40, 40, 40)
        outer_layout.setSpacing(15)

        title_label = QLabel("Student Details")
        title_label.setObjectName("pageTitle")
        outer_layout.addWidget(title_label)

        self.details_grid = QGridLayout()
        self.details_grid.setSpacing(10)
        outer_layout.addLayout(self.details_grid)
        outer_layout.addStretch()

        back_button = QPushButton("Back to Search Results")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(
            lambda: self.main_window.go_to_page(navigation.PAGE_SEARCH_STUDENT)
        )
        outer_layout.addWidget(back_button)

    def load_student(self, student_id: int):
        """Load and display the complete record for the given student_id."""
        student_row = student_service.get_student_by_id(student_id)

        while self.details_grid.count():
            item = self.details_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not student_row:
            self.details_grid.addWidget(QLabel("Student not found."), 0, 0)
            return

        fields = build_student_detail_fields(student_row)
        for row_index, (label_text, value_text) in enumerate(fields):
            label = QLabel(f"{label_text}:")
            label.setStyleSheet("font-weight: bold;")
            value = QLabel(value_text)
            self.details_grid.addWidget(label, row_index, 0)
            self.details_grid.addWidget(value, row_index, 1)


# =====================================================================
# Edit Student
# =====================================================================

class EditStudentPage(QWidget):
    """
    Three-stage Edit Student workflow:

    1. Student Information
    2. Documents Inquiry
    3. Updated Student Details
    """

    def __init__(
        self,
        main_window,
    ):
        super().__init__()

        self.main_window = main_window

        self.student_id = None
        self.original_status = None
        self.original_class_id = None
        self.original_roll_number = None
        self.original_admission_number = ""
        self.selected_class_id = None

        self.pending_update_data = None
        self.updated_student_row = None
        self.updated_detail_html = ""
        self.original_form_state = None
        self.original_student_snapshot = None

        outer_layout = QVBoxLayout(
            self
        )

        outer_layout.setContentsMargins(
            25,
            20,
            25,
            20,
        )

        outer_layout.setSpacing(
            10
        )

        self.workflow_stack = QStackedWidget()

        outer_layout.addWidget(
            self.workflow_stack
        )

        self._build_student_information_page()
        self._build_documents_page()
        self._build_updated_details_page()
        self._configure_keyboard_navigation()

    # ========================================================
    # Shared helpers
    # ========================================================

    @staticmethod
    def _digits_only_validator(
        maximum_digits: int,
    ):
        return QRegularExpressionValidator(
            QRegularExpression(
                rf"\d{{0,{maximum_digits}}}"
            )
        )

    @staticmethod
    def _set_checkbox_size(
        checkbox: QCheckBox,
    ) -> None:
        checkbox.setStyleSheet(
            """
            QCheckBox::indicator {
                width: 22px;
                height: 22px;
            }
            """
        )

    @staticmethod
    def _safe_text(
        value,
        empty_value: str = "-",
    ) -> str:
        if value is None:
            return empty_value

        cleaned = str(
            value
        ).strip()

        return cleaned or empty_value

    @staticmethod
    def _split_contact_number(
        stored_number,
    ) -> str:
        if not stored_number:
            return ""

        number_text = str(
            stored_number
        ).strip()

        if number_text.startswith(
            "+92"
        ):
            number_text = number_text[3:]

        return "".join(
            character
            for character in number_text
            if character.isdigit()
        )[-10:]

    @staticmethod
    def _split_admission_number(
        admission_number,
    ) -> tuple[str, str]:
        cleaned = str(
            admission_number
            or ""
        ).strip().upper()

        if (
            len(cleaned) >= 2
            and cleaned[0] in {
                "H",
                "P",
            }
            and cleaned[1:].isdigit()
        ):
            return (
                cleaned[0],
                cleaned[1:][:5],
            )

        numeric_part = "".join(
            character
            for character in cleaned
            if character.isdigit()
        )

        return (
            "H",
            numeric_part[-5:],
        )

    def _focus_widget(
        self,
        widget,
    ):
        def action():
            widget.setFocus()

        return action

    # ========================================================
    # Page 1 — Student Information
    # ========================================================

    def _build_student_information_page(
        self,
    ) -> None:
        self.student_information_page = QWidget()

        page_layout = QVBoxLayout(
            self.student_information_page
        )

        page_layout.setContentsMargins(
            5,
            2,
            5,
            2,
        )

        page_layout.setSpacing(
            8
        )

        heading_row = QHBoxLayout()

        title_label = QLabel(
            "Edit Student"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        section_label = QLabel(
            "Student Information"
        )

        section_label.setObjectName(
            "sectionHeading"
        )

        heading_row.addWidget(
            title_label
        )

        heading_row.addSpacing(
            15
        )

        heading_row.addWidget(
            section_label
        )

        heading_row.addStretch()

        page_layout.addLayout(
            heading_row
        )

        form_frame = QFrame()

        form_frame.setObjectName(
            "card"
        )

        form_grid = QGridLayout(
            form_frame
        )

        form_grid.setContentsMargins(
            18,
            12,
            18,
            12,
        )

        form_grid.setHorizontalSpacing(
            12
        )

        form_grid.setVerticalSpacing(
            8
        )

        form_grid.setColumnStretch(
            1,
            1,
        )

        form_grid.setColumnStretch(
            3,
            1,
        )

        def add_label(
            label_text: str,
            row: int,
            column: int,
        ) -> None:
            label = QLabel(
                label_text
            )

            label.setAlignment(
                Qt.AlignmentFlag.AlignRight
                | Qt.AlignmentFlag.AlignVCenter
            )

            form_grid.addWidget(
                label,
                row,
                column,
            )

        # ----------------------------------------------------
        # Left column
        # ----------------------------------------------------

        self.class_combo = QComboBox()

        self.class_combo.currentIndexChanged.connect(
            self._on_class_changed
        )

        add_label(
            "Class / Section:",
            0,
            0,
        )

        form_grid.addWidget(
            self.class_combo,
            0,
            1,
        )

        self.coming_session_checkbox = QCheckBox(
            "Admission for Coming Session"
        )
        self._set_checkbox_size(self.coming_session_checkbox)
        self.coming_session_checkbox.toggled.connect(
            self._on_edit_coming_session_toggled
        )
        form_grid.addWidget(
            self.coming_session_checkbox,
            1,
            0,
            1,
            2,
        )

        self.student_name_input = QLineEdit()
        self.student_name_input.setMaxLength(200)

        self.student_name_input.setPlaceholderText(
            "Enter student name"
        )

        add_label(
            "Student Name:",
            2,
            0,
        )

        form_grid.addWidget(
            self.student_name_input,
            2,
            1,
        )

        self.gender_combo = QComboBox()

        self.gender_combo.addItem(
            "-- Select Gender --",
            "",
        )

        self.gender_combo.addItem(
            "Male",
            "Male",
        )

        self.gender_combo.addItem(
            "Female",
            "Female",
        )

        self.gender_combo.currentIndexChanged.connect(
            self._on_class_changed
        )

        add_label(
            "Gender:",
            3,
            0,
        )

        form_grid.addWidget(
            self.gender_combo,
            3,
            1,
        )

        self.father_name_input = QLineEdit()
        self.father_name_input.setMaxLength(200)

        self.father_name_input.setPlaceholderText(
            "Enter father name"
        )

        add_label(
            "Father Name:",
            4,
            0,
        )

        form_grid.addWidget(
            self.father_name_input,
            4,
            1,
        )

        self.father_cnic_input = QLineEdit()

        self.father_cnic_input.setPlaceholderText(
            "13 digits, required"
        )

        self.father_cnic_input.setMaxLength(
            13
        )

        self.father_cnic_input.setValidator(
            self._digits_only_validator(
                13
            )
        )

        add_label(
            "Father CNIC:",
            5,
            0,
        )

        form_grid.addWidget(
            self.father_cnic_input,
            5,
            1,
        )

        phone_widget = QWidget()

        phone_layout = QHBoxLayout(
            phone_widget
        )

        phone_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        phone_layout.setSpacing(
            8
        )

        phone_prefix = QLabel(
            "+92"
        )

        phone_prefix.setStyleSheet(
            "font-weight: bold;"
        )

        self.phone_number_input = QLineEdit()

        self.phone_number_input.setPlaceholderText(
            "10 digits"
        )

        self.phone_number_input.setMaxLength(
            10
        )

        self.phone_number_input.setValidator(
            self._digits_only_validator(
                10
            )
        )

        phone_layout.addWidget(
            phone_prefix
        )

        phone_layout.addWidget(
            self.phone_number_input,
            1,
        )

        add_label(
            "Phone Number:",
            6,
            0,
        )

        form_grid.addWidget(
            phone_widget,
            6,
            1,
        )

        status_widget = QWidget()
        status_layout = QHBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)

        self.status_combo = QComboBox()
        self.status_combo.addItems(["Active", "Dropped"])
        self.status_combo.currentTextChanged.connect(
            self._on_class_changed
        )

        self.reactivate_button = QPushButton(
            "Reactivate Student"
        )
        self.reactivate_button.setObjectName(
            "secondaryButton"
        )
        self.reactivate_button.clicked.connect(
            self._reactivate_student
        )
        self.reactivate_button.setVisible(False)

        status_layout.addWidget(self.status_combo, 1)
        status_layout.addWidget(self.reactivate_button)

        add_label(
            "Student Status:",
            7,
            0,
        )

        form_grid.addWidget(
            status_widget,
            7,
            1,
        )

        # ----------------------------------------------------
        # Right column
        # ----------------------------------------------------

        whatsapp_widget = QWidget()

        whatsapp_layout = QHBoxLayout(
            whatsapp_widget
        )

        whatsapp_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        whatsapp_layout.setSpacing(
            8
        )

        whatsapp_prefix = QLabel(
            "+92"
        )

        whatsapp_prefix.setStyleSheet(
            "font-weight: bold;"
        )

        self.whatsapp_number_input = QLineEdit()

        self.whatsapp_number_input.setPlaceholderText(
            "10 digits, optional"
        )

        self.whatsapp_number_input.setMaxLength(
            10
        )

        self.whatsapp_number_input.setValidator(
            self._digits_only_validator(
                10
            )
        )

        whatsapp_layout.addWidget(
            whatsapp_prefix
        )

        whatsapp_layout.addWidget(
            self.whatsapp_number_input,
            1,
        )

        add_label(
            "WhatsApp Number:",
            0,
            2,
        )

        form_grid.addWidget(
            whatsapp_widget,
            0,
            3,
        )

        self.current_address_input = QLineEdit()

        self.current_address_input.setPlaceholderText(
            "Enter current address"
        )

        add_label(
            "Current Address:",
            1,
            2,
        )

        form_grid.addWidget(
            self.current_address_input,
            1,
            3,
        )

        self.permanent_address_input = QLineEdit()

        self.permanent_address_input.setPlaceholderText(
            "Enter permanent address"
        )

        add_label(
            "Permanent Address:",
            2,
            2,
        )

        form_grid.addWidget(
            self.permanent_address_input,
            2,
            3,
        )

        admission_date_widget = QWidget()

        admission_date_layout = QHBoxLayout(
            admission_date_widget
        )

        admission_date_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        admission_date_layout.setSpacing(
            6
        )

        self.admission_day_input = QSpinBox()

        self.admission_day_input.setRange(
            1,
            31,
        )

        self.admission_month_input = QSpinBox()

        self.admission_month_input.setRange(
            1,
            12,
        )

        self.admission_year_input = QSpinBox()

        self.admission_year_input.setRange(
            2000,
            2100,
        )

        self.admission_day_input.setFixedWidth(
            70
        )

        self.admission_month_input.setFixedWidth(
            70
        )

        self.admission_year_input.setFixedWidth(
            95
        )

        for admission_date_input in (
            self.admission_day_input,
            self.admission_month_input,
            self.admission_year_input,
        ):
            admission_date_input.setEnabled(False)
            admission_date_input.setToolTip(
                "Admission Date is system-controlled. "
                "It changes automatically to today's date "
                "only when a non-Active student is reactivated."
            )

        admission_date_layout.addWidget(
            self.admission_day_input
        )

        admission_date_layout.addWidget(
            QLabel("/")
        )

        admission_date_layout.addWidget(
            self.admission_month_input
        )

        admission_date_layout.addWidget(
            QLabel("/")
        )

        admission_date_layout.addWidget(
            self.admission_year_input
        )

        admission_date_layout.addStretch()

        add_label(
            "Admission Date:",
            3,
            2,
        )

        form_grid.addWidget(
            admission_date_widget,
            3,
            3,
        )

        self.roll_number_input = QLineEdit()

        self.roll_number_input.setReadOnly(
            True
        )

        self.roll_number_input.setPlaceholderText(
            "Automatically assigned"
        )

        add_label(
            "Roll Number:",
            4,
            2,
        )

        form_grid.addWidget(
            self.roll_number_input,
            4,
            3,
        )

        self.admission_number_input = QLineEdit()
        self.admission_number_input.setMaxLength(32)
        self.admission_number_input.setReadOnly(True)
        self.admission_number_input.setPlaceholderText(
            "Permanent Admission Number"
        )
        add_label("Admission Number:", 5, 2)
        form_grid.addWidget(self.admission_number_input, 5, 3)

        effective_period_widget = QWidget()

        effective_period_layout = QHBoxLayout(
            effective_period_widget
        )

        effective_period_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )

        effective_period_layout.setSpacing(
            8
        )

        self.class_change_month_combo = QComboBox()

        self.class_change_month_combo.addItems(
            MONTH_NAMES
        )

        self.class_change_year_input = QSpinBox()

        self.class_change_year_input.setRange(
            2000,
            2100,
        )

        effective_period_layout.addWidget(
            self.class_change_month_combo,
            1,
        )

        effective_period_layout.addWidget(
            self.class_change_year_input
        )

        add_label(
            "Class Change Effective:",
            6,
            2,
        )

        form_grid.addWidget(
            effective_period_widget,
            6,
            3,
        )

        page_layout.addWidget(
            form_frame,
            1,
        )

        self.roll_rule_label = QLabel(
            "Roll Number remains unchanged in the same class and section."
        )

        self.roll_rule_label.setWordWrap(
            True
        )

        page_layout.addWidget(
            self.roll_rule_label
        )

        button_row = QHBoxLayout()

        self.next_button = QPushButton(
            "Next"
        )

        self.next_button.clicked.connect(
            self._go_to_documents
        )

        self.cancel_coming_session_button = QPushButton(
            "Cancel Coming-Session Admission"
        )
        self.cancel_coming_session_button.setObjectName(
            "dangerButton"
        )
        self.cancel_coming_session_button.setVisible(False)
        self.cancel_coming_session_button.clicked.connect(
            self._cancel_coming_session_admission
        )

        back_button = QPushButton(
            "Back to Search"
        )

        back_button.setObjectName(
            "secondaryButton"
        )

        back_button.clicked.connect(
            self._go_back_to_search
        )

        button_row.addWidget(
            self.next_button
        )

        button_row.addWidget(
            self.cancel_coming_session_button
        )

        button_row.addWidget(
            back_button
        )

        button_row.addStretch()

        page_layout.addLayout(
            button_row
        )

        self.workflow_stack.addWidget(
            self.student_information_page
        )

    # ========================================================
    # Page 2 — Documents Inquiry
    # ========================================================

    def _build_documents_page(
        self,
    ) -> None:
        self.documents_page = QWidget()

        page_layout = QVBoxLayout(
            self.documents_page
        )

        page_layout.setContentsMargins(
            25,
            20,
            25,
            20,
        )

        page_layout.setSpacing(
            18
        )

        title_label = QLabel(
            "Documents Inquiry"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        page_layout.addWidget(
            title_label
        )

        instruction_label = QLabel(
            "Update the student's Date of Birth and "
            "submitted admission documents."
        )

        instruction_label.setWordWrap(
            True
        )

        page_layout.addWidget(
            instruction_label
        )

        documents_frame = QFrame()

        documents_frame.setObjectName(
            "card"
        )

        form_layout = QFormLayout(
            documents_frame
        )

        form_layout.setContentsMargins(
            25,
            25,
            25,
            25,
        )

        form_layout.setSpacing(
            20
        )

        self.date_of_birth_input = QLineEdit()

        self.date_of_birth_input.setPlaceholderText(
            "00-00-0000"
        )

        self.date_of_birth_input.setInputMask(
            "00-00-0000;_"
        )

        self.date_of_birth_input.setMaximumWidth(
            220
        )

        form_layout.addRow(
            "Date of Birth:",
            self.date_of_birth_input,
        )

        self.slc_checkbox = QCheckBox()

        self._set_checkbox_size(
            self.slc_checkbox
        )

        form_layout.addRow(
            "School Leaving Certificate:",
            self.slc_checkbox,
        )

        self.form_b_checkbox = QCheckBox()

        self._set_checkbox_size(
            self.form_b_checkbox
        )

        form_layout.addRow(
            "Form B:",
            self.form_b_checkbox,
        )

        self.birth_certificate_checkbox = QCheckBox()

        self._set_checkbox_size(
            self.birth_certificate_checkbox
        )

        form_layout.addRow(
            "Birth Certificate:",
            self.birth_certificate_checkbox,
        )

        page_layout.addWidget(
            documents_frame
        )

        requirement_label = QLabel(
            "School Leaving Certificate is optional for "
            "Play Group and required for Nursery, KG, "
            "and numbered classes. Form B and Birth "
            "Certificate are optional."
        )

        requirement_label.setWordWrap(
            True
        )

        page_layout.addWidget(
            requirement_label
        )

        page_layout.addStretch()

        button_row = QHBoxLayout()

        documents_back_button = QPushButton(
            "Back"
        )

        documents_back_button.setObjectName(
            "secondaryButton"
        )

        documents_back_button.clicked.connect(
            self._return_to_student_information
        )

        self.save_button = QPushButton(
            "Save Changes"
        )

        self.save_button.clicked.connect(
            self._save_changes
        )

        button_row.addWidget(
            documents_back_button
        )

        button_row.addWidget(
            self.save_button
        )

        button_row.addStretch()

        page_layout.addLayout(
            button_row
        )

        self.workflow_stack.addWidget(
            self.documents_page
        )

    # ========================================================
    # Page 3 — Updated Student Details
    # ========================================================

    def _build_updated_details_page(
        self,
    ) -> None:
        self.updated_details_page = QWidget()

        page_layout = QVBoxLayout(
            self.updated_details_page
        )

        page_layout.setContentsMargins(
            20,
            15,
            20,
            15,
        )

        page_layout.setSpacing(
            10
        )

        title_label = QLabel(
            "Updated Student Details"
        )

        title_label.setObjectName(
            "pageTitle"
        )

        page_layout.addWidget(
            title_label
        )

        self.updated_on_label = QLabel(
            "Updated on: -"
        )

        self.updated_on_label.setStyleSheet(
            "font-weight: bold;"
        )

        page_layout.addWidget(
            self.updated_on_label
        )

        update_note = QLabel(
            (
                "Changed fields are marked as: "
                "Updated: OLD VALUE → NEW VALUE"
            )
        )

        update_note.setWordWrap(
            True
        )

        page_layout.addWidget(
            update_note
        )

        details_frame = QFrame()

        details_frame.setObjectName(
            "card"
        )

        self.updated_details_grid = QGridLayout(
            details_frame
        )

        self.updated_details_grid.setContentsMargins(
            18,
            14,
            18,
            14,
        )

        self.updated_details_grid.setHorizontalSpacing(
            18
        )

        self.updated_details_grid.setVerticalSpacing(
            8
        )

        self.updated_details_grid.setColumnStretch(
            1,
            1,
        )

        self.updated_details_grid.setColumnStretch(
            3,
            1,
        )

        page_layout.addWidget(
            details_frame,
            1,
        )

        button_row = QHBoxLayout()

        print_button = QPushButton(
            "Print"
        )

        print_button.clicked.connect(
            self._print_updated_student
        )

        download_button = QPushButton(
            "Download PDF"
        )

        download_button.clicked.connect(
            self._download_updated_student_pdf
        )

        back_button = QPushButton(
            "Back to Search"
        )

        back_button.setObjectName(
            "secondaryButton"
        )

        back_button.clicked.connect(
            self._back_after_update
        )

        button_row.addWidget(
            print_button
        )

        button_row.addWidget(
            download_button
        )

        button_row.addWidget(
            back_button
        )

        button_row.addStretch()

        page_layout.addLayout(
            button_row
        )

        self.workflow_stack.addWidget(
            self.updated_details_page
        )



    # ========================================================
    # Keyboard navigation
    # ========================================================

    def _configure_keyboard_navigation(
        self,
    ) -> None:
        self._enter_action_map = {
            self.class_combo:
                self._focus_widget(
                    self.coming_session_checkbox
                ),

            self.coming_session_checkbox:
                self._focus_widget(
                    self.student_name_input
                ),

            self.student_name_input:
                self._focus_widget(
                    self.gender_combo
                ),

            self.gender_combo:
                self._focus_widget(
                    self.father_name_input
                ),

            self.father_name_input:
                self._focus_widget(
                    self.father_cnic_input
                ),

            self.father_cnic_input:
                self._focus_widget(
                    self.phone_number_input
                ),

            self.phone_number_input:
                self._focus_widget(
                    self.whatsapp_number_input
                ),

            self.whatsapp_number_input:
                self._focus_widget(
                    self.current_address_input
                ),

            self.current_address_input:
                self._focus_widget(
                    self.permanent_address_input
                ),

            self.permanent_address_input:
                self._focus_widget(
                    self.admission_day_input
                ),

            self.admission_day_input:
                self._focus_widget(
                    self.admission_month_input
                ),

            self.admission_month_input:
                self._focus_widget(
                    self.admission_year_input
                ),

            self.admission_year_input:
                self._focus_widget(
                    self.status_combo
                ),

            self.status_combo:
                self._focus_widget(self.admission_number_input),

            self.admission_number_input:
                self._focus_widget(self.next_button),

            self.next_button:
                self._go_to_documents,

            self.date_of_birth_input:
                self._focus_widget(
                    self.slc_checkbox
                ),

            self.slc_checkbox:
                self._focus_widget(
                    self.form_b_checkbox
                ),

            self.form_b_checkbox:
                self._focus_widget(
                    self.birth_certificate_checkbox
                ),

            self.birth_certificate_checkbox:
                self._focus_widget(
                    self.save_button
                ),

            self.save_button:
                self._save_changes,
        }

        for widget in self._enter_action_map:
            widget.installEventFilter(
                self
            )

    def eventFilter(
        self,
        watched,
        event,
    ):
        if (
            event.type()
            == QEvent.Type.KeyPress
            and event.key()
            in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            )
        ):
            action = self._enter_action_map.get(
                watched
            )

            if action is not None:
                action()
                return True

        return super().eventFilter(
            watched,
            event,
        )

    def _edit_is_pending_coming_session(self) -> bool:
        original_pending = bool(
            self.original_student_snapshot
            and str(
                self.original_student_snapshot.get("student_status") or ""
            ).strip() == "Coming Session"
            and str(
                self.original_student_snapshot.get("coming_session_status") or ""
            ).strip().upper() == "PENDING"
        )
        if original_pending and not self.coming_session_checkbox.isVisible():
            return True
        return bool(
            self.coming_session_checkbox.isVisible()
            and self.coming_session_checkbox.isChecked()
        )

    def _refresh_edit_coming_session_controls(self) -> None:
        if self.original_student_snapshot is None:
            self.coming_session_checkbox.setVisible(False)
            self.cancel_coming_session_button.setVisible(False)
            return
        row = self.original_student_snapshot
        available = coming_session_service.is_coming_session_option_available(
            int(row["admission_year"]),
            int(row["admission_month"]),
            int(row["admission_day"]),
        )
        pending = str(row.get("student_status") or "") == "Coming Session"
        eligible_status = str(row.get("student_status") or "") in {"Active", "Coming Session"}
        visible = bool(available and eligible_status)
        self.coming_session_checkbox.blockSignals(True)
        self.coming_session_checkbox.setChecked(pending)
        self.coming_session_checkbox.blockSignals(False)
        self.coming_session_checkbox.setVisible(visible)
        self.coming_session_checkbox.setEnabled(visible)
        self.cancel_coming_session_button.setVisible(bool(available and pending))
        self._apply_edit_coming_session_roll_state()

    def _on_edit_coming_session_toggled(self, checked: bool) -> None:
        if self.original_student_snapshot is None:
            return
        original_pending = str(
            self.original_student_snapshot.get("student_status") or ""
        ) == "Coming Session"
        if checked == original_pending:
            self._apply_edit_coming_session_roll_state()
            return
        if checked:
            message = (
                "Convert this normal admission to a coming-session admission?\n\n"
                "The current roll will be released, eligible unpaid pre-April "
                "fees will be removed, fee will start in April, and the student "
                "will be excluded from the ending session's promotion."
            )
        else:
            message = (
                "Convert this coming-session admission to a normal admission?\n\n"
                "A roll will be assigned immediately and monthly fee will start "
                "from the original Admission Date month."
            )
        confirmation = QMessageBox.question(
            self, "Confirm Admission Type Change", message
        )
        if confirmation != QMessageBox.Yes:
            self.coming_session_checkbox.blockSignals(True)
            self.coming_session_checkbox.setChecked(original_pending)
            self.coming_session_checkbox.blockSignals(False)
        self._apply_edit_coming_session_roll_state()
        self._on_class_changed()

    def _apply_edit_coming_session_roll_state(self) -> None:
        pending = self._edit_is_pending_coming_session()
        if pending:
            self.roll_number_input.setText("Pending")
            self.roll_number_input.setPlaceholderText("Assigned when the coming session starts")
            self.class_change_month_combo.setEnabled(False)
            self.class_change_year_input.setEnabled(False)
            self.roll_rule_label.setText(
                "Coming-session admission: Roll remains Pending and fee starts in April."
            )

    def _cancel_coming_session_admission(self) -> None:
        if self.student_id is None:
            return
        confirmation = QMessageBox.warning(
            self,
            "Permanently Cancel Coming-Session Admission",
            "This pending student never joined the school session. The student "
            "and all eligible pending records will be permanently removed from "
            "the active database. This action cannot be reversed.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirmation != QMessageBox.Yes:
            return
        try:
            coming_session_service.cancel_pending_admission(
                int(self.student_id), cancelled_by="admin"
            )
        except Exception as error:
            QMessageBox.critical(
                self, "Cancellation Failed",
                f"The pending admission could not be cancelled.\n\nDetails: {error}",
            )
            return
        QMessageBox.information(
            self, "Admission Cancelled",
            "The coming-session admission was permanently removed successfully.",
        )
        self.main_window.go_to_page(navigation.PAGE_SEARCH_STUDENT)

    # ========================================================
    # Load
    # ========================================================

    def load_student(
        self,
        student_id: int,
    ) -> None:
        student_row = (
            student_service.get_student_by_id(
                student_id
            )
        )

        if not student_row:
            QMessageBox.warning(
                self,
                "Student Not Found",
                "Student record could not be found.",
            )

            self.main_window.go_to_page(
                navigation.PAGE_SEARCH_STUDENT
            )
            return

        self.original_student_snapshot = dict(
            student_row
        )

        self.student_id = int(
            student_id
        )

        self.original_status = (
            student_row["student_status"]
        )

        self.original_class_id = int(
            student_row["class_id"]
        )

        self.selected_class_id = int(
            student_row["class_id"]
        )

        self.original_roll_number = int(
            student_row["roll_number"]
        )

        self.original_admission_number = str(
            student_row["admission_number"]
            or ""
        )

        self.pending_update_data = None
        self.updated_student_row = None
        self.updated_detail_html = ""

        self.class_combo.blockSignals(
            True
        )

        self.class_combo.clear()

        class_rows = list(class_service.get_all_classes())
        if not any(int(row["id"]) == self.original_class_id for row in class_rows):
            current_class = class_service.get_class_by_id(self.original_class_id)
            if current_class is not None:
                class_rows.append(current_class)
        for class_row in class_rows:
            label = class_row["class_name"]
            if not bool(class_row["is_active"]):
                label += " (Inactive - current only)"
            self.class_combo.addItem(label, class_row["id"])

        class_index = self.class_combo.findData(
            self.original_class_id
        )

        if class_index >= 0:
            self.class_combo.setCurrentIndex(
                class_index
            )

        self.class_combo.blockSignals(
            False
        )

        self.roll_number_input.setText(
            student_service.display_roll_number(student_row)
        )
        self.admission_number_input.setText(self.original_admission_number)

        self.student_name_input.setText(
            student_row["student_name"]
            or ""
        )

        gender_text = str(
            student_row["gender"]
            or ""
        ).strip().title()

        gender_index = self.gender_combo.findData(
            gender_text
        )

        self.gender_combo.setCurrentIndex(
            gender_index
            if gender_index >= 0
            else 0
        )

        self.father_name_input.setText(
            student_row["father_name"]
            or ""
        )

        self.father_cnic_input.setText(
            student_row["father_cnic"]
            or ""
        )

        self.phone_number_input.setText(
            self._split_contact_number(
                student_row[
                    "father_contact_number"
                ]
            )
        )

        self.whatsapp_number_input.setText(
            self._split_contact_number(
                student_row[
                    "whatsapp_number"
                ]
            )
        )

        self.current_address_input.setText(
            student_row["current_address"]
            or ""
        )

        self.permanent_address_input.setText(
            student_row["permanent_address"]
            or ""
        )

        loaded_status = str(
            student_row["student_status"] or ""
        ).strip()

        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        if loaded_status == "Active":
            self.status_combo.addItems(["Active", "Dropped"])
            self.status_combo.setEnabled(True)
            self.reactivate_button.setVisible(False)
        elif loaded_status in {"Dropped", "Archived"}:
            self.status_combo.addItem(loaded_status)
            self.status_combo.setEnabled(False)
            self.reactivate_button.setVisible(True)
        else:
            self.status_combo.addItem(loaded_status)
            self.status_combo.setEnabled(False)
            self.reactivate_button.setVisible(False)
        self.status_combo.setCurrentText(loaded_status)
        self.status_combo.blockSignals(False)

        lifecycle_locked = (
            loaded_status != "Coming Session"
            and (
                loaded_status in {"Graduated", "Partially Graduated"}
                or student_row["released_roll_number"] is not None
                or int(student_row["roll_number"]) <= 0
            )
        )
        self.class_combo.setEnabled(not lifecycle_locked)
        self.roll_number_input.setReadOnly(True)

        self.admission_day_input.setValue(
            int(
                student_row["admission_day"]
            )
        )

        self.admission_month_input.setValue(
            int(
                student_row["admission_month"]
            )
        )

        self.admission_year_input.setValue(
            int(
                student_row["admission_year"]
            )
        )
        self._refresh_edit_coming_session_controls()

        now = datetime.now()

        self.class_change_month_combo.setCurrentIndex(
            now.month - 1
        )

        self.class_change_year_input.setValue(
            now.year
        )

        self.class_change_month_combo.setEnabled(
            False
        )

        self.class_change_year_input.setEnabled(
            False
        )

        stored_date_of_birth = (
            student_row["date_of_birth"]
            or ""
        )

        display_date_of_birth = (
            student_service
            .format_date_of_birth_for_display(
                stored_date_of_birth
            )
        )

        self.date_of_birth_input.clear()

        if display_date_of_birth != "-":
            self.date_of_birth_input.setText(
                display_date_of_birth
            )

        self.slc_checkbox.setChecked(
            bool(
                student_row[
                    "school_leaving_certificate_submitted"
                ]
            )
        )

        self.form_b_checkbox.setChecked(
            bool(
                student_row[
                    "form_b_submitted"
                ]
            )
        )

        self.birth_certificate_checkbox.setChecked(
            bool(
                student_row[
                    "birth_certificate_submitted"
                ]
            )
        )

        self.roll_rule_label.setText(
            "Roll Number remains unchanged in the same class and section."
        )

        self.workflow_stack.setCurrentWidget(
            self.student_information_page
        )

        self.original_form_state = (
            self._capture_form_state()
        )

        self.class_combo.setFocus()

    def _reactivate_student(self) -> None:
        """Run the controlled Dropped/Archived reactivation workflow."""
        if self.student_id is None:
            return

        target_class_id = self.class_combo.currentData()
        if target_class_id is None:
            QMessageBox.warning(
                self,
                "Select Class",
                "Please select the class for reactivation.",
            )
            return

        confirmation = QMessageBox.question(
            self,
            "Reactivate Student",
            (
                "Reactivate this student as Active?\n\n"
                "A fresh positive Roll Number will be assigned. "
                "Historical class and fee records will remain unchanged."
            ),
        )
        if confirmation != QMessageBox.Yes:
            return

        try:
            result = student_service.reactivate_student(
                student_id=int(self.student_id),
                class_id=int(target_class_id),
                updated_by="admin",
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Reactivation Failed",
                f"Student could not be reactivated.\n\nDetails: {error}",
            )
            return

        QMessageBox.information(
            self,
            "Student Reactivated",
            (
                "Student has been reactivated successfully.\n\n"
                f"Fresh Roll Number: {result.get('roll_number', '-')}"
            ),
        )
        self.load_student(int(self.student_id))

    # ========================================================
    # Class / section and Roll Number
    # ========================================================


    # EDIT_STUDENT_GENDER_SECTION_AUTO_ROUTING_UI_V1
    def _on_class_changed(
        self,
        *_args,
    ) -> None:
        self.selected_class_id = (
            self.class_combo.currentData()
        )

        if self._edit_is_pending_coming_session():
            self.roll_number_input.setText("Pending")
            self.class_change_month_combo.setEnabled(False)
            self.class_change_year_input.setEnabled(False)
            self.roll_rule_label.setText(
                "Coming-session admission: class/section is reserved for April; Roll remains Pending."
            )
            return

        if (
            self.student_id is None
            or self.original_class_id is None
            or self.selected_class_id is None
        ):
            return

        gender = (
            self.gender_combo.currentData()
            or ""
        )

        student_status = (
            self.status_combo.currentText()
            if hasattr(
                self,
                "status_combo",
            )
            else self.original_status
        )

        # Before Gender is selected, retain the normal class-change
        # Roll Number preview. Final save validation still requires Gender.
        if not student_service.validate_gender(
            gender
        ):
            class_changed = (
                int(
                    self.selected_class_id
                )
                != int(
                    self.original_class_id
                )
            )

            self.class_change_month_combo.setEnabled(
                class_changed
            )

            self.class_change_year_input.setEnabled(
                class_changed
            )

            if not class_changed:
                self.roll_number_input.setText(
                    str(
                        self.original_roll_number
                    )
                )

                self.roll_rule_label.setText(
                    (
                        "Roll Number remains unchanged "
                        "in the same class and section."
                    )
                )

                return

            new_roll_number = (
                student_service
                .generate_transfer_roll_number(
                    class_id=(
                        self.selected_class_id
                    ),
                    exclude_student_id=(
                        self.student_id
                    ),
                )
            )

            self.roll_number_input.setText(
                str(
                    new_roll_number
                )
            )

            self.roll_rule_label.setText(
                (
                    f"Class / section changed to "
                    f"{self.class_combo.currentText()}. "
                    f"New Roll Number {new_roll_number} "
                    "has been assigned automatically."
                )
            )

            return

        requested_class_id = int(
            self.selected_class_id
        )

        requested_class_name = (
            self.class_combo.currentText()
        )

        try:
            route_result = (
                student_service
                .preview_student_gender_section_route(
                    student_id=(
                        self.student_id
                    ),
                    selected_class_id=(
                        requested_class_id
                    ),
                    gender=gender,
                    student_status=(
                        student_status
                    ),
                )
            )

        except ValueError as error:
            class_changed = (
                requested_class_id
                != int(
                    self.original_class_id
                )
            )

            self.class_change_month_combo.setEnabled(
                class_changed
            )

            self.class_change_year_input.setEnabled(
                class_changed
            )

            self.roll_number_input.clear()

            self.roll_rule_label.setText(
                (
                    "Automatic gender-section routing "
                    f"is blocked: {error}"
                )
            )

            return

        resolved_class_id = int(
            route_result[
                "resolved_class_id"
            ]
        )

        resolved_class_name = (
            route_result[
                "resolved_class_name"
            ]
        )

        resolved_roll_number = int(
            route_result[
                "resolved_roll_number"
            ]
        )

        if (
            resolved_class_id
            != requested_class_id
        ):
            resolved_index = (
                self.class_combo.findData(
                    resolved_class_id
                )
            )

            if resolved_index >= 0:
                self.class_combo.blockSignals(
                    True
                )

                self.class_combo.setCurrentIndex(
                    resolved_index
                )

                self.class_combo.blockSignals(
                    False
                )

            self.selected_class_id = (
                resolved_class_id
            )

        class_changed = bool(
            route_result[
                "class_changed"
            ]
        )

        self.class_change_month_combo.setEnabled(
            class_changed
        )

        self.class_change_year_input.setEnabled(
            class_changed
        )

        self.roll_number_input.setText(
            str(
                resolved_roll_number
            )
        )

        if route_result[
            "auto_routed"
        ]:
            self.roll_rule_label.setText(
                (
                    "Automatic Gender Routing: "
                    f"{requested_class_name} changed to "
                    f"{resolved_class_name}. "
                    f"New Roll Number "
                    f"{resolved_roll_number}. "
                    f"Capacity after update: "
                    f"{route_result['resulting_active_strength']}/"
                    f"{route_result['student_capacity']}."
                )
            )

        elif class_changed:
            self.roll_rule_label.setText(
                (
                    f"Class / section changed to "
                    f"{resolved_class_name}. "
                    f"New Roll Number "
                    f"{resolved_roll_number} "
                    "has been assigned automatically."
                )
            )

        else:
            self.roll_rule_label.setText(
                (
                    f"Gender is compatible with "
                    f"{resolved_class_name}. "
                    "Class / section and Roll Number "
                    "remain unchanged."
                )
            )

    # ========================================================
    # First-page validation
    # ========================================================

    def _collect_student_information(
        self,
    ):
        if not self.student_id:
            QMessageBox.warning(
                self,
                "Student Not Loaded",
                "Please load a student record first.",
            )
            return None

        if not self.selected_class_id:
            QMessageBox.warning(
                self,
                "Missing Class",
                "Please select a class and section.",
            )

            self.class_combo.setFocus()
            return None

        class_row = class_service.get_class_by_id(
            self.selected_class_id
        )

        if not class_row:
            QMessageBox.warning(
                self,
                "Class Not Found",
                "The selected class could not be found.",
            )
            return None

        student_name = (
            self.student_name_input.text().strip()
        )

        if not student_service.validate_person_name(
            student_name
        ):
            QMessageBox.warning(
                self,
                "Invalid Student Name",
                (
                    "Please enter a valid student "
                    "name using letters only."
                ),
            )

            self.student_name_input.setFocus()
            return None

        gender = (
            self.gender_combo.currentData()
            or ""
        )

        if not student_service.validate_gender(
            gender
        ):
            QMessageBox.warning(
                self,
                "Missing Gender",
                "Please select the student's gender.",
            )

            self.gender_combo.setFocus()
            return None

        requested_class_id = int(
            self.selected_class_id
        )

        requested_class_name = (
            class_row[
                "class_name"
            ]
        )

        desired_coming_session = self._edit_is_pending_coming_session()
        student_status = self.status_combo.currentText()

        if desired_coming_session:
            route_result = {
                "resolved_class_id": requested_class_id,
                "resolved_roll_number": -int(self.student_id),
                "auto_routed": False,
            }
        else:
            route_status = (
                "Active" if self.original_status == "Coming Session"
                else student_status
            )
            try:
                route_result = (
                    student_service.preview_student_gender_section_route(
                        student_id=self.student_id,
                        selected_class_id=requested_class_id,
                        gender=gender,
                        student_status=route_status,
                    )
                )
            except ValueError as error:
                QMessageBox.warning(
                    self, "Gender / Section Routing Blocked", str(error)
                )
                self.gender_combo.setFocus()
                return None

        resolved_class_id = int(route_result["resolved_class_id"])

        if (
            resolved_class_id
            != int(
                self.selected_class_id
            )
        ):
            resolved_index = (
                self.class_combo.findData(
                    resolved_class_id
                )
            )

            if resolved_index >= 0:
                self.class_combo.blockSignals(
                    True
                )

                self.class_combo.setCurrentIndex(
                    resolved_index
                )

                self.class_combo.blockSignals(
                    False
                )

            self.selected_class_id = (
                resolved_class_id
            )

        class_row = (
            class_service.get_class_by_id(
                resolved_class_id
            )
        )

        if not class_row:
            QMessageBox.warning(
                self,
                "Resolved Class Not Found",
                (
                    "The automatically resolved class "
                    "could not be loaded."
                ),
            )

            return None

        resolved_roll_number = int(route_result["resolved_roll_number"])
        lifecycle_locked_for_roll = (
            self.original_status != "Coming Session"
            and (
                self.original_status in {"Graduated", "Partially Graduated"}
                or self.original_student_snapshot.get("released_roll_number") is not None
                or self.original_roll_number <= 0
            )
        )
        if lifecycle_locked_for_roll:
            self.roll_number_input.setText(
                student_service.display_roll_number(self.original_student_snapshot)
            )
        else:
            self.roll_number_input.setText(str(resolved_roll_number))

        father_name = (
            self.father_name_input.text().strip()
        )

        if not student_service.validate_person_name(
            father_name
        ):
            QMessageBox.warning(
                self,
                "Invalid Father Name",
                (
                    "Please enter a valid father "
                    "name using letters only."
                ),
            )

            self.father_name_input.setFocus()
            return None

        father_cnic = (
            self.father_cnic_input.text().strip()
        )

        if not student_service.validate_cnic(
            father_cnic
        ):
            QMessageBox.warning(
                self,
                "Invalid Father CNIC",
                (
                    "Father CNIC is required and must contain exactly "
                    "13 numeric digits."
                ),
            )

            self.father_cnic_input.setFocus()
            return None

        existing_student = (
            student_service.find_existing_student_identity(
                student_name=student_name,
                father_name=father_name,
                father_cnic=father_cnic,
                exclude_student_id=self.student_id,
            )
        )

        if existing_student is not None:
            QMessageBox.warning(
                self,
                "Student Record Already Exists",
                (
                    "Another student record already "
                    "uses the same student name, "
                    "father name, and Father CNIC."
                ),
            )
            return None

        phone_digits = (
            self.phone_number_input.text().strip()
        )

        if not student_service.validate_contact_number(
            phone_digits
        ):
            QMessageBox.warning(
                self,
                "Invalid Phone Number",
                (
                    "Please enter exactly 10 digits "
                    "after +92."
                ),
            )

            self.phone_number_input.setFocus()
            return None

        whatsapp_digits = (
            self.whatsapp_number_input.text().strip()
        )

        if (
            not student_service
            .validate_optional_contact_number(
                whatsapp_digits
            )
        ):
            QMessageBox.warning(
                self,
                "Invalid WhatsApp Number",
                (
                    "WhatsApp Number must contain "
                    "exactly 10 digits after +92 "
                    "or be left empty."
                ),
            )

            self.whatsapp_number_input.setFocus()
            return None

        current_address = (
            self.current_address_input.text().strip()
        )

        if not current_address:
            QMessageBox.warning(
                self,
                "Missing Current Address",
                "Please enter the current address.",
            )

            self.current_address_input.setFocus()
            return None

        permanent_address = (
            self.permanent_address_input.text().strip()
        )

        if not permanent_address:
            QMessageBox.warning(
                self,
                "Missing Permanent Address",
                "Please enter the permanent address.",
            )

            self.permanent_address_input.setFocus()
            return None

        admission_day = (
            self.admission_day_input.value()
        )

        admission_month = (
            self.admission_month_input.value()
        )

        admission_year = (
            self.admission_year_input.value()
        )

        if not student_service.validate_admission_date(
            admission_year,
            admission_month,
            admission_day,
        ):
            QMessageBox.warning(
                self,
                "Invalid Admission Date",
                (
                    "The selected admission date is "
                    "not a valid calendar date."
                ),
            )

            self.admission_day_input.setFocus()
            return None

        if desired_coming_session:
            roll_number = -int(self.student_id)
        else:
            roll_number_text = self.roll_number_input.text().strip()
            normalized_roll_text = (
                roll_number_text[:-1].strip()
                if roll_number_text.endswith("*")
                else roll_number_text
            )
            if not normalized_roll_text.isdigit() or int(normalized_roll_text) <= 0:
                QMessageBox.warning(
                    self, "Invalid Roll Number",
                    "The displayed Roll Number must be a positive number.",
                )
                return None
            roll_number = int(normalized_roll_text)
            lifecycle_locked = (
                self.original_status not in {"Coming Session"}
                and (
                    self.original_status in {"Graduated", "Partially Graduated"}
                    or self.original_student_snapshot.get("released_roll_number") is not None
                    or self.original_roll_number <= 0
                )
            )
            if lifecycle_locked:
                roll_number = self.original_roll_number
            elif not student_service.is_roll_number_unique_in_class(
                class_id=self.selected_class_id,
                roll_number=roll_number,
                exclude_student_id=self.student_id,
            ):
                QMessageBox.warning(
                    self, "Duplicate Roll Number",
                    "The assigned Roll Number is already used in the selected class or section.",
                )
                return None

        admission_number = self.admission_number_input.text().strip()
        if not admission_number:
            QMessageBox.warning(
                self, "Invalid Admission Number", "Admission Number cannot be blank."
            )
            self.admission_number_input.setFocus()
            return None
        if admission_number != self.original_admission_number:
            QMessageBox.warning(
                self,
                "Admission Number Locked",
                "Admission Number is permanent and cannot be edited after saving.",
            )
            return None
        if not student_service.is_admission_number_unique(
            admission_number, exclude_student_id=self.student_id
        ):
            QMessageBox.warning(
                self, "Duplicate Admission Number",
                f"Admission Number {admission_number} is already used by another student.",
            )
            self.admission_number_input.setFocus()
            return None

        class_changed = (
            int(self.selected_class_id)
            != int(self.original_class_id)
        )

        effective_month = None
        effective_year = None

        if class_changed and not (
            desired_coming_session or self.original_status == "Coming Session"
        ):
            effective_month = (
                self.class_change_month_combo
                .currentIndex()
                + 1
            )

            effective_year = (
                self.class_change_year_input.value()
            )

            try:
                student_service.validate_manual_class_change_period(
                    student_id=int(self.student_id),
                    current_class_id=int(self.original_class_id),
                    effective_month=int(effective_month),
                    effective_year=int(effective_year),
                )
            except ValueError as error:
                QMessageBox.warning(
                    self,
                    "Invalid Class-Change Period",
                    str(error),
                )
                return None

        return {
            "student_id": self.student_id,
            "class_id": self.selected_class_id,
            "class_name": class_row["class_name"],
            "class_level": class_row["class_level"],
            "requested_class_id": (
                requested_class_id
            ),
            "requested_class_name": (
                requested_class_name
            ),
            "auto_routed": (
                route_result[
                    "auto_routed"
                ]
            ),
            "confirmation_class_name": (
                (
                    f"{class_row['class_name']} "
                    f"(automatically routed from "
                    f"{requested_class_name} for "
                    f"{gender})"
                )
                if route_result[
                    "auto_routed"
                ]
                else class_row[
                    "class_name"
                ]
            ),
            "class_changed": class_changed,
            "effective_month": effective_month,
            "effective_year": effective_year,
            "student_name": student_name,
            "gender": gender,
            "father_name": father_name,
            "father_cnic": father_cnic,
            "father_contact_number": (
                student_service.format_contact_number(
                    phone_digits
                )
            ),
            "whatsapp_number": (
                student_service
                .format_optional_contact_number(
                    whatsapp_digits
                )
            ),
            "current_address": current_address,
            "permanent_address": permanent_address,
            "student_status": (
                self.status_combo.currentText()
            ),
            "admission_day": admission_day,
            "admission_month": admission_month,
            "admission_year": admission_year,
            "roll_number": roll_number,
            "admission_number": admission_number,
            "desired_coming_session": desired_coming_session,
        }

    def _go_to_documents(
        self,
    ) -> None:
        information = (
            self._collect_student_information()
        )

        if information is None:
            return

        self.pending_update_data = information

        self.workflow_stack.setCurrentWidget(
            self.documents_page
        )

        self.date_of_birth_input.setFocus()

    def _return_to_student_information(
        self,
    ) -> None:
        self.workflow_stack.setCurrentWidget(
            self.student_information_page
        )

        self.student_name_input.setFocus()

    # ========================================================
    # Documents and Save
    # ========================================================

    def _extract_date_of_birth(
        self,
    ):
        raw_text = (
            self.date_of_birth_input.text()
        )

        digits = "".join(
            character
            for character in raw_text
            if character.isdigit()
        )

        if len(digits) != 8:
            QMessageBox.warning(
                self,
                "Invalid Date of Birth",
                (
                    "Please enter Date of Birth in "
                    "DD-MM-YYYY format."
                ),
            )

            self.date_of_birth_input.setFocus()
            return None

        display_date = (
            f"{digits[0:2]}-"
            f"{digits[2:4]}-"
            f"{digits[4:8]}"
        )

        try:
            student_service.normalize_date_of_birth(display_date)
            return display_date

        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Date of Birth",
                (
                    "The entered Date of Birth is not "
                    "a valid calendar date."
                ),
            )

            self.date_of_birth_input.setFocus()
            return None

    def _save_changes(
        self,
    ) -> None:
        if self.pending_update_data is None:
            information = (
                self._collect_student_information()
            )

            if information is None:
                self.workflow_stack.setCurrentWidget(
                    self.student_information_page
                )
                return

            self.pending_update_data = information

        date_of_birth = (
            self._extract_date_of_birth()
        )

        if date_of_birth is None:
            return

        admission_datetime = datetime(
            self.pending_update_data["admission_year"],
            self.pending_update_data["admission_month"],
            self.pending_update_data["admission_day"],
        )
        try:
            student_service.validate_date_of_birth_before_admission(
                date_of_birth,
                admission_datetime,
            )
        except ValueError as error:
            QMessageBox.warning(
                self,
                "Invalid Date of Birth",
                str(error),
            )
            self.date_of_birth_input.setFocus()
            return

        slc_submitted = (
            self.slc_checkbox.isChecked()
        )

        if (
            student_service.is_slc_required(
                self.pending_update_data[
                    "class_level"
                ]
            )
            and not slc_submitted
        ):
            QMessageBox.warning(
                self,
                "School Leaving Certificate Required",
                (
                    "School Leaving Certificate is "
                    "required for the selected class."
                ),
            )

            self.slc_checkbox.setFocus()
            return

        new_status = (
            self.pending_update_data[
                "student_status"
            ]
        )

        if new_status != self.original_status:
            confirmation = QMessageBox.question(
                self,
                "Confirm Status Change",
                (
                    f"Change Student Status from "
                    f"{self.original_status} to "
                    f"{new_status}?"
                ),
            )

            if confirmation != QMessageBox.Yes:
                return

        use_coming_session_workflow = bool(
            self.original_status == "Coming Session"
            or self.pending_update_data.get("desired_coming_session")
        )

        if self.pending_update_data["class_changed"] and not use_coming_session_workflow:
            old_class_row = (
                class_service.get_class_by_id(
                    self.original_class_id
                )
            )

            old_class_name = (
                old_class_row["class_name"]
                if old_class_row
                else str(
                    self.original_class_id
                )
            )

            new_class_name = (
                self.pending_update_data.get(
                    "confirmation_class_name",
                    self.pending_update_data[
                        "class_name"
                    ],
                )
            )

            effective_month = (
                self.pending_update_data[
                    "effective_month"
                ]
            )

            effective_year = (
                self.pending_update_data[
                    "effective_year"
                ]
            )

            month_name = MONTH_NAMES[
                effective_month - 1
            ]

            new_roll_number = (
                self.pending_update_data[
                    "roll_number"
                ]
            )

            confirmation = QMessageBox.question(
                self,
                "Confirm Class / Section Change",
                (
                    f"Change Class / Section from "
                    f"{old_class_name} to "
                    f"{new_class_name}?\n\n"
                    f"Effective from: "
                    f"{month_name} {effective_year}\n"
                    f"New Roll Number: "
                    f"{new_roll_number}\n\n"
                    f"Paid fee records and previous "
                    f"sessions will remain unchanged. "
                    f"Existing tested fee recalculation "
                    f"rules will apply to unpaid fees."
                ),
            )

            if confirmation != QMessageBox.Yes:
                return

        try:
            if use_coming_session_workflow:
                coming_session_service.save_pending_admission_edit(
                    student_id=int(self.pending_update_data["student_id"]),
                    desired_coming_session=bool(
                        self.pending_update_data.get("desired_coming_session")
                    ),
                    class_id=int(self.pending_update_data["class_id"]),
                    student_name=self.pending_update_data["student_name"],
                    gender=self.pending_update_data["gender"],
                    father_name=self.pending_update_data["father_name"],
                    father_cnic=self.pending_update_data["father_cnic"],
                    father_contact_number=self.pending_update_data["father_contact_number"],
                    whatsapp_number=self.pending_update_data["whatsapp_number"],
                    current_address=self.pending_update_data["current_address"],
                    permanent_address=self.pending_update_data["permanent_address"],
                    school_leaving_certificate_submitted=slc_submitted,
                    form_b_submitted=self.form_b_checkbox.isChecked(),
                    birth_certificate_submitted=self.birth_certificate_checkbox.isChecked(),
                    date_of_birth=date_of_birth,
                    updated_by="admin",
                )
            else:
                student_service.update_student(
                    student_id=(
                        self.pending_update_data[
                            "student_id"
                        ]
                    ),
                    admission_number=(
                        self.pending_update_data[
                            "admission_number"
                        ]
                    ),
                    roll_number=(
                        self.pending_update_data[
                            "roll_number"
                        ]
                    ),
                    class_id=(
                        self.pending_update_data[
                            "class_id"
                        ]
                    ),
                    student_name=(
                        self.pending_update_data[
                            "student_name"
                        ]
                    ),
                    gender=(
                        self.pending_update_data[
                            "gender"
                        ]
                    ),
                    father_name=(
                        self.pending_update_data[
                            "father_name"
                        ]
                    ),
                    father_cnic=(
                        self.pending_update_data[
                            "father_cnic"
                        ]
                    ),
                    father_contact_number=(
                        self.pending_update_data[
                            "father_contact_number"
                        ]
                    ),
                    whatsapp_number=(
                        self.pending_update_data[
                            "whatsapp_number"
                        ]
                    ),
                    current_address=(
                        self.pending_update_data[
                            "current_address"
                        ]
                    ),
                    permanent_address=(
                        self.pending_update_data[
                            "permanent_address"
                        ]
                    ),
                    student_status=(
                        self.pending_update_data[
                            "student_status"
                        ]
                    ),
                    school_leaving_certificate_submitted=(
                        slc_submitted
                    ),
                    form_b_submitted=(
                        self.form_b_checkbox.isChecked()
                    ),
                    birth_certificate_submitted=(
                        self.birth_certificate_checkbox
                        .isChecked()
                    ),
                    date_of_birth=date_of_birth,
                    admission_year=(
                        self.pending_update_data[
                            "admission_year"
                        ]
                    ),
                    admission_month=(
                        self.pending_update_data[
                            "admission_month"
                        ]
                    ),
                    admission_day=(
                        self.pending_update_data[
                            "admission_day"
                        ]
                    ),
                    admission_time="",
                    effective_month=(
                        self.pending_update_data[
                            "effective_month"
                        ]
                    ),
                    effective_year=(
                        self.pending_update_data[
                            "effective_year"
                        ]
                    ),
                )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Update Failed",
                (
                    "Student record could not be "
                    "updated.\n\n"
                    f"Details: {error}"
                ),
            )
            return

        QMessageBox.information(
            self,
            "Student Updated",
            (
                "Student record has been updated "
                "successfully."
            ),
        )

        self._load_updated_details(
            self.student_id
        )

    # ========================================================
    # Updated Details, Print and PDF
    # ========================================================

    def _build_updated_detail_fields(
        self,
        student_row,
    ):
        """
        Build all student details and compare each editable field
        against the values loaded before editing.
        """
        original = (
            self.original_student_snapshot
            or {}
        )

        def row_value(
            row,
            key: str,
            default=None,
        ):
            try:
                value = row[key]

            except (
                KeyError,
                IndexError,
                TypeError,
            ):
                value = default

            return value

        def admission_date_text(
            row,
        ) -> str:
            try:
                day = int(
                    row_value(
                        row,
                        "admission_day",
                    )
                )

                month = int(
                    row_value(
                        row,
                        "admission_month",
                    )
                )

                year = int(
                    row_value(
                        row,
                        "admission_year",
                    )
                )

                month_name = MONTH_NAMES[
                    month - 1
                ]

                return (
                    f"{day} {month_name} {year}"
                )

            except (
                TypeError,
                ValueError,
                IndexError,
            ):
                return "-"

        def date_of_birth_text(
            row,
        ) -> str:
            return (
                student_service
                .format_date_of_birth_for_display(
                    row_value(
                        row,
                        "date_of_birth",
                    )
                    or ""
                )
            )

        field_values = [
            (
                "Admission Number",
                row_value(
                    student_row,
                    "admission_number",
                ),
                row_value(
                    original,
                    "admission_number",
                ),
            ),
            (
                "Roll Number",
                row_value(
                    student_row,
                    "roll_number",
                ),
                row_value(
                    original,
                    "roll_number",
                ),
            ),
            (
                "Class",
                row_value(
                    student_row,
                    "class_name",
                ),
                row_value(
                    original,
                    "class_name",
                ),
            ),
            (
                "Student Name",
                row_value(
                    student_row,
                    "student_name",
                ),
                row_value(
                    original,
                    "student_name",
                ),
            ),
            (
                "Gender",
                row_value(
                    student_row,
                    "gender",
                ),
                row_value(
                    original,
                    "gender",
                ),
            ),
            (
                "Father Name",
                row_value(
                    student_row,
                    "father_name",
                ),
                row_value(
                    original,
                    "father_name",
                ),
            ),
            (
                "Father CNIC",
                row_value(
                    student_row,
                    "father_cnic",
                ),
                row_value(
                    original,
                    "father_cnic",
                ),
            ),
            (
                "Phone Number",
                row_value(
                    student_row,
                    "father_contact_number",
                ),
                row_value(
                    original,
                    "father_contact_number",
                ),
            ),
            (
                "WhatsApp Number",
                row_value(
                    student_row,
                    "whatsapp_number",
                ),
                row_value(
                    original,
                    "whatsapp_number",
                ),
            ),
            (
                "Current Address",
                row_value(
                    student_row,
                    "current_address",
                ),
                row_value(
                    original,
                    "current_address",
                ),
            ),
            (
                "Permanent Address",
                row_value(
                    student_row,
                    "permanent_address",
                ),
                row_value(
                    original,
                    "permanent_address",
                ),
            ),
            (
                "Admission Date",
                admission_date_text(
                    student_row
                ),
                admission_date_text(
                    original
                ),
            ),
            (
                "Date of Birth",
                date_of_birth_text(
                    student_row
                ),
                date_of_birth_text(
                    original
                ),
            ),
            (
                "Student Status",
                row_value(
                    student_row,
                    "student_status",
                ),
                row_value(
                    original,
                    "student_status",
                ),
            ),
        ]

        fields = []

        for (
            label_text,
            current_value,
            previous_value,
        ) in field_values:
            current_text = self._safe_text(
                current_value
            )

            previous_text = self._safe_text(
                previous_value
            )

            was_changed = (
                current_text
                != previous_text
            )

            change_note = ""

            if was_changed:
                change_note = (
                    f"Updated: "
                    f"{previous_text} → "
                    f"{current_text}"
                )

            fields.append(
                (
                    label_text,
                    current_text,
                    change_note,
                    was_changed,
                )
            )

        return fields



    def _load_updated_details(
        self,
        student_id: int,
    ) -> None:
        student_row = (
            student_service.get_student_by_id(
                student_id
            )
        )

        if not student_row:
            QMessageBox.warning(
                self,
                "Student Not Found",
                (
                    "The updated student record "
                    "could not be loaded."
                ),
            )
            return

        self.updated_student_row = student_row

        updated_at_text = self._safe_text(
            student_row["updated_at"]
        )

        updated_display = (
            updated_at_text
        )

        try:
            parsed_updated_at = datetime.strptime(
                updated_at_text,
                "%Y-%m-%d %H:%M:%S",
            )

            updated_display = (
                parsed_updated_at.strftime(
                    "%d %B %Y, %I:%M %p"
                )
            )

        except ValueError:
            pass

        self.updated_on_label.setText(
            f"Updated on: {updated_display}"
        )

        while self.updated_details_grid.count():
            item = (
                self.updated_details_grid
                .takeAt(0)
            )

            if item.widget():
                item.widget().deleteLater()

        fields = self._build_updated_detail_fields(
            student_row
        )

        for field_index, (
            label_text,
            value_text,
            change_note,
            was_changed,
        ) in enumerate(fields):
            row_index = (
                field_index // 2
            )

            start_column = (
                0
                if field_index % 2 == 0
                else 2
            )

            label = QLabel(
                f"{label_text}:"
            )

            label.setStyleSheet(
                "font-weight: bold;"
            )

            if was_changed:
                value_html = (
                    f"{escape(str(value_text))}"
                    "<br>"
                    "<span style='"
                    "font-weight: bold;"
                    "color: #9A3412;"
                    "'>"
                    f"{escape(change_note)}"
                    "</span>"
                )

            else:
                value_html = escape(
                    str(value_text)
                )

            value = QLabel(
                value_html
            )

            value.setTextFormat(
                Qt.TextFormat.RichText
            )

            value.setWordWrap(
                True
            )

            value.setTextInteractionFlags(
                Qt.TextInteractionFlag
                .TextSelectableByMouse
            )

            self.updated_details_grid.addWidget(
                label,
                row_index,
                start_column,
                Qt.AlignmentFlag.AlignTop,
            )

            self.updated_details_grid.addWidget(
                value,
                row_index,
                start_column + 1,
            )

        self.updated_detail_html = (
            self._build_updated_detail_html(
                fields,
                updated_display,
            )
        )

        self.workflow_stack.setCurrentWidget(
            self.updated_details_page
        )



    def _build_updated_detail_html(
        self,
        fields,
        updated_display: str,
    ) -> str:
        """
        Build a clean final updated record for print and PDF.

        The Updated: OLD -> NEW comparison remains visible on
        the Edit Student UI but is intentionally excluded here.
        """
        if not self.updated_student_row:
            return ""

        return (
            build_professional_student_print_html(
                student_row=(
                    self.updated_student_row
                ),
                document_title=(
                    "UPDATED STUDENT RECORD"
                ),
                date_caption=(
                    "Updated on"
                ),
                date_value=(
                    updated_display
                ),
            )
        )




    def _print_updated_student(
        self,
    ) -> None:
        if not self.updated_detail_html:
            QMessageBox.warning(
                self,
                "No Student Details",
                (
                    "No updated student details "
                    "are available."
                ),
            )
            return

        printer = QPrinter(
            QPrinter.HighResolution
        )

        printer.setPageSize(
            QPageSize(
                QPageSize.PageSizeId.A4
            )
        )

        printer.setPageOrientation(
            QPageLayout.Orientation.Portrait
        )

        printer.setPageMargins(
            QMarginsF(
                10,
                10,
                10,
                10,
            ),
            QPageLayout.Unit.Millimeter,
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )

        print_dialog.setWindowTitle(
            "Print Updated Student Details"
        )

        if (
            print_dialog.exec()
            != QDialog.Accepted
        ):
            return

        try:
            document = QTextDocument()

            document.setPageSize(
                printer.pageRect(
                    QPrinter.Unit.Point
                ).size()
            )

            document.setDocumentMargin(
                12
            )

            document.setHtml(
                self.updated_detail_html
            )

            document.print_(
                printer
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Print Error",
                (
                    "Updated student details could "
                    "not be printed.\n\n"
                    f"Details: {error}"
                ),
            )

    def _download_updated_student_pdf(
        self,
    ) -> None:
        if (
            not self.updated_detail_html
            or not self.updated_student_row
        ):
            QMessageBox.warning(
                self,
                "No Student Details",
                (
                    "No updated student details "
                    "are available."
                ),
            )
            return

        admission_number = (
            self.updated_student_row[
                "admission_number"
            ]
        )

        suggested_name = (
            f"Student_{admission_number}_Updated.pdf"
        )

        save_path, _selected_filter = (
            QFileDialog.getSaveFileName(
                self,
                "Download Updated Student Details",
                suggested_name,
                "PDF Files (*.pdf)",
            )
        )

        if not save_path:
            return

        if not save_path.lower().endswith(
            ".pdf"
        ):
            save_path += ".pdf"

        try:
            printer = QPrinter(
                QPrinter.HighResolution
            )

            printer.setOutputFormat(
                QPrinter.OutputFormat.PdfFormat
            )

            printer.setOutputFileName(
                save_path
            )

            document = QTextDocument()

            document.setPageSize(
                printer.pageRect(
                    QPrinter.Unit.Point
                ).size()
            )

            document.setDocumentMargin(
                12
            )

            document.setHtml(
                self.updated_detail_html
            )

            document.print_(
                printer
            )

            QMessageBox.information(
                self,
                "PDF Downloaded",
                (
                    "Updated student details were "
                    "downloaded successfully."
                ),
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                (
                    "Updated student details could "
                    "not be downloaded.\n\n"
                    f"Details: {error}"
                ),
            )

    # ========================================================
    # Back navigation and unsaved changes
    # ========================================================

    def _capture_form_state(
        self,
    ):
        return (
            self.selected_class_id,
            self.student_name_input.text(),
            self.gender_combo.currentData(),
            self.father_name_input.text(),
            self.father_cnic_input.text(),
            self.phone_number_input.text(),
            self.whatsapp_number_input.text(),
            self.current_address_input.text(),
            self.permanent_address_input.text(),
            self.status_combo.currentText(),
            self.admission_day_input.value(),
            self.admission_month_input.value(),
            self.admission_year_input.value(),
            self.roll_number_input.text(),
            self.admission_number_input.text(),
            self.class_change_month_combo.currentIndex(),
            self.class_change_year_input.value(),
            self.date_of_birth_input.text(),
            self.slc_checkbox.isChecked(),
            self.form_b_checkbox.isChecked(),
            self.birth_certificate_checkbox.isChecked(),
        )

    def _has_unsaved_changes(
        self,
    ) -> bool:
        if self.original_form_state is None:
            return False

        return (
            self._capture_form_state()
            != self.original_form_state
        )

    def _go_back_to_search(
        self,
    ) -> None:
        if self._has_unsaved_changes():
            confirmation = QMessageBox.question(
                self,
                "Unsaved Changes",
                (
                    "The student record contains "
                    "unsaved changes.\n\n"
                    "Do you want to leave this page?"
                ),
            )

            if confirmation != QMessageBox.Yes:
                return

        self.main_window.go_to_page(
            navigation.PAGE_SEARCH_STUDENT
        )

    def _back_after_update(
        self,
    ) -> None:
        self.main_window.go_to_page(
            navigation.PAGE_SEARCH_STUDENT
        )

