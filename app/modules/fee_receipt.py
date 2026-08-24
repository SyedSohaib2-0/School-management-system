"""
Fee receipt preview and payment confirmation screen.

The screen previews two receipt copies:
1. School Copy
2. Student Copy

One payment prints in the top half of A4, with both copies side by side.
Payment is saved only when the user clicks Confirm Payment.
"""

from datetime import datetime
from html import escape

from PySide6.QtCore import Qt, QRectF, QSizeF
from PySide6.QtGui import QTextDocument
from PySide6.QtGui import QPageSize
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtWidgets import (
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
    QScrollArea,
    QGroupBox,
    QFileDialog,
)

from app import navigation
from app.services import fee_service
from app.services.receipt_filename_service import (
    build_paid_receipt_default_filename,
    make_duplicate_safe_pdf_path,
)
from app.modules.student_management import load_school_print_profile



# RECEIPT_HALF_PAGE_TWO_COPY_V1
# RECEIPT_NUMBER_REOPEN_V16

def _receipt_school_profile() -> dict:
    """Load the current School Print Settings for every preview/print."""
    fallback = {
        "school_name": "HIRA MODEL SCHOOL",
        "school_address": "Warsak Road, Mathra, Peshawar",
        "bicep_number": "BICEP No. 1443",
        "mobile_number": "Mobile: 0346-0951718",
    }

    try:
        loaded = load_school_print_profile()

    except Exception:
        loaded = {}

    profile = {}

    for key, default_value in fallback.items():
        value = str(
            loaded.get(key, default_value)
        ).strip()

        profile[key] = (
            value
            if value
            else default_value
        )

    return profile


def _receipt_month_label(item: dict) -> str:
    """Return a month label such as 'January 2025-2026'."""
    item_name = str(
        item.get("item_name", "")
    ).strip()

    month_name = (
        item_name.split()[0]
        if item_name
        else "Monthly Fee"
    )

    session_year = str(
        item.get("session_year", "")
    ).strip()

    base_label = (
        f"{month_name} {session_year}"
        if session_year
        else item_name
        or "Monthly Fee"
    )

    class_name = str(
        item.get("class_name", "")
    ).strip()

    return (
        f"{base_label} — {class_name}"
        if class_name
        else base_label
    )


def _session_history_html(data: dict) -> str:
    """Build the compact immutable April-March history grid."""
    history = list(data.get("session_history") or [])
    if not history:
        return ""

    headers = []
    amounts = []
    dates = []
    month_abbreviations = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
    }
    for item in history:
        month = int(item.get("month_number") or 0)
        year = int(item.get("year") or 0)
        status = str(item.get("status") or "")
        headers.append(
            f"<th>{month_abbreviations.get(month, '-')}<br>{year % 100:02d}</th>"
        )
        if status == "Paid" and item.get("amount") is not None:
            amounts.append(f"<td>{float(item['amount']):,.0f}</td>")
            raw_date = str(item.get("paid_date") or "")
            try:
                display_date = datetime.fromisoformat(raw_date).strftime("%d-%m-%y")
            except ValueError:
                display_date = raw_date[:10]
            dates.append(f"<td>{escape(display_date)}</td>")
        elif status == "Waived":
            amounts.append("<td>Waived</td>")
            dates.append("<td>-</td>")
        else:
            amounts.append("<td></td>")
            dates.append("<td></td>")

    session = escape(str(data.get("session_year") or "-"))
    return (
        f"<div class='history-title'>Fee Status History — Session {session}</div>"
        "<table class='history'><tr>"
        + "".join(headers)
        + "</tr><tr>"
        + "".join(amounts)
        + "</tr><tr>"
        + "".join(dates)
        + "</tr></table>"
    )


def _receipt_density(item_count: int) -> dict:
    """Keep one to twelve monthly rows readable inside one quarter A4."""
    item_count = max(
        1,
        int(item_count),
    )

    if item_count <= 4:
        return {
            "body_font": "8.6pt",
            "table_font": "8.2pt",
            "cell_padding": "3px 4px",
            "header_font": "13pt",
        }

    if item_count <= 8:
        return {
            "body_font": "7.8pt",
            "table_font": "7.5pt",
            "cell_padding": "2px 3px",
            "header_font": "12pt",
        }

    return {
        "body_font": "7.0pt",
        "table_font": "6.8pt",
        "cell_padding": "1px 2px",
        "header_font": "11pt",
    }


# FEE_TRACKING_ID_UI_V1
class FeeReceiptPage(QWidget):
    """Preview two receipt copies and confirm the payment."""

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.preview_data = None
        self.student_id = None
        self.selected_record_ids = []
        self.payment_completed = False
        self.payment_cancelled = False
        self.cancel_window_open = False
        self.historical_receipt_mode = False

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        content_widget = QWidget()

        layout = QVBoxLayout(content_widget)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(25, 20, 25, 25)
        layout.setSpacing(15)

        title_label = QLabel("Fee Receipt Preview")
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        self.instruction_label = QLabel(
            "Review the School Copy and Student Copy carefully "
            "before confirming the payment."
        )
        self.instruction_label.setWordWrap(True)
        layout.addWidget(self.instruction_label)

        # ---------------------------------------------------------------
        # Receipt copies
        # ---------------------------------------------------------------

        self.school_copy_group = QGroupBox("School Copy")
        school_layout = QVBoxLayout(
            self.school_copy_group
        )

        self.school_copy_label = QLabel()
        self.school_copy_label.setWordWrap(True)
        self.school_copy_label.setTextFormat(
            Qt.RichText
        )
        school_layout.addWidget(
            self.school_copy_label
        )

        layout.addWidget(
            self.school_copy_group
        )



        self.parent_copy_group = QGroupBox(
            "Student Copy"
        )
        parent_layout = QVBoxLayout(
            self.parent_copy_group
        )

        self.parent_copy_label = QLabel()
        self.parent_copy_label.setWordWrap(True)
        self.parent_copy_label.setTextFormat(
            Qt.RichText
        )
        parent_layout.addWidget(
            self.parent_copy_label
        )

        layout.addWidget(
            self.parent_copy_group
        )

        # ---------------------------------------------------------------
        # Actions
        # ---------------------------------------------------------------

        action_row = QHBoxLayout()
        action_row.setSpacing(12)

        self.confirm_button = QPushButton(
            "Confirm Payment"
        )
        self.confirm_button.clicked.connect(
            self._confirm_payment
        )
        action_row.addWidget(
            self.confirm_button
        )

        self.cancel_button = QPushButton(
            "Cancel This Payment"
        )
        self.cancel_button.setObjectName(
            "dangerButton"
        )
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(
            self._cancel_confirmed_payment
        )
        action_row.addWidget(
            self.cancel_button
        )

        self.print_button = QPushButton(
            "Print Paid Receipt"
        )
        self.print_button.setEnabled(False)
        self.print_button.clicked.connect(
            self._print_paid_receipt
        )
        action_row.addWidget(
            self.print_button
        )

        self.download_button = QPushButton(
            "Download Paid Receipt PDF"
        )
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(
            self._download_paid_receipt_pdf
        )
        action_row.addWidget(
            self.download_button
        )

        back_button = QPushButton(
            "Back to Pay Fee"
        )
        back_button.setObjectName(
            "secondaryButton"
        )
        back_button.clicked.connect(
            self._back_to_pay_fee
        )
        action_row.addWidget(
            back_button
        )

        layout.addLayout(action_row)
        layout.addStretch()

        scroll_area.setWidget(content_widget)
        outer_layout.addWidget(scroll_area)

        self.clear_receipt()

    # ------------------------------------------------------------------
    # Receipt loading
    # ------------------------------------------------------------------

    def load_pending_payment(self):
        """
        Load the selected student and fee items stored by the Pay Fee
        screen in main_window.pending_fee_payment.
        """
        pending_payment = getattr(
            self.main_window,
            "pending_fee_payment",
            None,
        )

        if not pending_payment:
            self.clear_receipt()

            QMessageBox.warning(
                self,
                "No Pending Payment",
                "No fee payment has been selected.",
            )
            return False

        self.student_id = pending_payment.get(
            "student_id"
        )
        self.selected_record_ids = pending_payment.get(
            "selected_record_ids",
            [],
        )

        try:
            self.preview_data = (
                fee_service.get_payment_preview(
                    self.student_id,
                    self.selected_record_ids,
                )
            )
        except Exception as error:
            self.clear_receipt()

            QMessageBox.critical(
                self,
                "Receipt Preview Error",
                f"The receipt preview could not be loaded."
                f"\n\n{error}",
            )
            return False

        self.payment_completed = False
        self.payment_cancelled = False
        self.cancel_window_open = False
        self.historical_receipt_mode = False
        self.payment_data = None
        self.paid_receipt_html = None

        self.instruction_label.setText(
            "Review the School Copy and Student Copy carefully "
            "before confirming the payment."
        )
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.confirm_button.setVisible(True)
        self.confirm_button.setEnabled(True)
        self.print_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.confirm_button.setText(
            "Confirm Payment"
        )

        receipt_html = self._build_receipt_html(
            self.preview_data
        )

        self.school_copy_label.setText(
            receipt_html
        )
       
        self.parent_copy_label.setText(
            receipt_html
        )

        return True

    def load_paid_receipt_by_number(
        self,
        receipt_number,
    ):
        """Load a completed historical receipt in print-only mode."""
        try:
            payment_data = (
                fee_service.
                get_completed_payment_by_receipt_number(
                    receipt_number
                )
            )
        except ValueError as error:
            self.clear_receipt()
            QMessageBox.warning(
                self,
                "Receipt Not Available",
                str(error),
            )
            return False
        except Exception as error:
            self.clear_receipt()
            QMessageBox.critical(
                self,
                "Receipt Search Error",
                "The paid receipt could not be loaded."
                f"\n\n{error}",
            )
            return False

        self.preview_data = None
        self.student_id = int(
            payment_data["student_id"]
        )
        self.selected_record_ids = []
        self.payment_completed = True
        self.payment_cancelled = False
        self.cancel_window_open = False
        self.historical_receipt_mode = True
        self.payment_data = payment_data
        self.paid_receipt_html = (
            self._build_paid_receipt_html(
                payment_data
            )
        )

        self.school_copy_label.setText(
            self.paid_receipt_html
        )
        self.parent_copy_label.setText(
            self.paid_receipt_html
        )

        self.instruction_label.setText(
            f"Completed Receipt {payment_data['receipt_no']} loaded. "
            "You may print it, download it, or go back to Pay Fee."
        )
        self.confirm_button.setVisible(False)
        self.confirm_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.print_button.setEnabled(True)
        self.download_button.setEnabled(True)

        return True

    def clear_receipt(self):
        """Clear all receipt information."""
        self.preview_data = None
        self.student_id = None
        self.selected_record_ids = []
        self.payment_completed = False
        self.payment_cancelled = False
        self.cancel_window_open = False
        self.historical_receipt_mode = False
        self.payment_data = None
        self.paid_receipt_html = None

        empty_text = (
            "<p><b>No receipt is currently loaded.</b></p>"
        )

        self.school_copy_label.setText(
            empty_text
        )
        
        self.parent_copy_label.setText(
            empty_text
        )

        self.instruction_label.setText(
            "Review the School Copy and Student Copy carefully "
            "before confirming the payment."
        )
        self.confirm_button.setVisible(True)
        self.confirm_button.setEnabled(False)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.print_button.setEnabled(False)
        self.confirm_button.setText(
            "Confirm Payment"
        )

    # ------------------------------------------------------------------
    # Receipt HTML
    # ------------------------------------------------------------------

    def _build_receipt_html(self, data):
        """Build the compact pending receipt preview."""
        profile = _receipt_school_profile()
        current_date = datetime.now().strftime(
            "%d-%m-%Y"
        )
        fee_items = list(
            data.get("fee_items", [])
        )
        density = _receipt_density(
            len(fee_items)
        )

        item_rows = ""

        for serial_number, item in enumerate(
            fee_items,
            start=1,
        ):
            item_rows += (
                "<tr>"
                f"<td class='serial'>{serial_number}</td>"
                "<td>"
                f"{escape(_receipt_month_label(item))}"
                "</td>"
                "<td class='amount'>"
                f"Rs. {float(item['amount']):,.2f}"
                "</td>"
                "</tr>"
            )

        tracking_id = escape(
            str(data.get("tracking_id") or "-")
        )

        return f"""
        <!-- Tracking ID: {tracking_id} -->
        <html>
        <head>
            <title>School Dues Preview</title>
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: {density['body_font']};
                    color: #000000;
                    margin: 0;
                    padding: 0;
                }}
                .school-name {{
                    text-align: center;
                    font-size: {density['header_font']};
                    font-weight: 800;
                    margin: 0;
                }}
                .school-line {{
                    text-align: center;
                    font-weight: 600;
                    margin: 0;
                }}
                .title {{
                    text-align: center;
                    font-weight: 800;
                    text-decoration: underline;
                    margin: 2px 0 4px 0;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                }}
                .details td {{
                    padding: 2px 3px;
                    vertical-align: bottom;
                }}
                .label {{
                    font-weight: 700;
                    white-space: nowrap;
                }}
                .line-value {{
                    border-bottom: 1px solid #000000;
                }}
                .fees {{
                    margin-top: 4px;
                    font-size: {density['table_font']};
                }}
                .fees th,
                .fees td {{
                    border: 1px solid #000000;
                    padding: {density['cell_padding']};
                }}
                .serial {{
                    width: 9%;
                    text-align: center;
                }}
                .amount {{
                    width: 27%;
                    text-align: right;
                    white-space: nowrap;
                }}
                .status-row td,
                .total-row td {{
                    font-weight: 800;
                }}
                .accountant {{
                    margin-top: 9px;
                    font-weight: 700;
                }}
            </style>
        </head>
        <body>
            <div class="school-name">
                {escape(profile['school_name'])}
            </div>
            <div class="school-line">
                {escape(profile['school_address'])}
            </div>
            <div class="school-line">
                {escape(profile['bicep_number'])}
                &nbsp;&nbsp;|&nbsp;&nbsp;
                {escape(profile['mobile_number'])}
            </div>
            <div class="title">SCHOOL DUES</div>

            <table class="details">
                <tr>
                    <td class="label">Receipt No.</td>
                    <td class="line-value">{tracking_id}</td>
                    <td class="label">Date</td>
                    <td class="line-value">{escape(current_date)}</td>
                </tr>
                <tr>
                    <td class="label">Name of Student</td>
                    <td class="line-value" colspan="3">
                        {escape(str(data.get('student_name') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Father's Name</td>
                    <td class="line-value" colspan="3">
                        {escape(str(data.get('father_name') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Class</td>
                    <td class="line-value">
                        {escape(str(data.get('class_name') or '-'))}
                    </td>
                    <td class="label">Reg No.</td>
                    <td class="line-value">
                        {escape(str(data.get('admission_number') or '-'))}
                    </td>
                </tr>
            </table>

            <table class="fees">
                <tr>
                    <th class="serial">S.No</th>
                    <th>Particulars</th>
                    <th class="amount">Amount (Rs)</th>
                </tr>
                {item_rows}
                <tr class="status-row">
                    <td colspan="2">Status</td>
                    <td class="amount">PENDING</td>
                </tr>
                <tr class="total-row">
                    <td colspan="2">TOTAL</td>
                    <td class="amount">
                        Rs. {float(data.get('total_amount', 0)):,.2f}
                    </td>
                </tr>
            </table>

            <div class="accountant">
                ____________________<br>Accountant
            </div>
        </body>
        </html>
        """

    def _build_paid_receipt_html(
        self,
        payment_data,
    ):
        """Build the compact paid receipt content shown on screen."""
        profile = _receipt_school_profile()
        paid_items = list(
            payment_data.get("paid_items", [])
        )
        density = _receipt_density(
            len(paid_items)
        )
        receipt_no = escape(
            str(payment_data.get("receipt_no") or "-")
        )
        default_session = str(
            payment_data.get("session_year") or ""
        ).strip()
        session_history_html = _session_history_html(payment_data)

        item_rows = ""

        for serial_number, item in enumerate(
            paid_items,
            start=1,
        ):
            normalized_item = dict(item)
            normalized_item.setdefault(
                "session_year",
                default_session,
            )

            item_rows += (
                "<tr>"
                f"<td class='serial'>{serial_number}</td>"
                "<td>"
                f"{escape(_receipt_month_label(normalized_item))}"
                "</td>"
                "<td class='amount'>"
                f"Rs. {float(item['amount']):,.2f}"
                "</td>"
                "</tr>"
            )

        return f"""
        <!-- Receipt No: {receipt_no} -->
        <html>
        <head>
            <title>Paid Fee Receipt</title>
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: {density['body_font']};
                    color: #000000;
                    margin: 0;
                    padding: 0;
                }}
                .school-name {{
                    text-align: center;
                    font-size: {density['header_font']};
                    font-weight: 800;
                    margin: 0;
                }}
                .school-line {{
                    text-align: center;
                    font-weight: 600;
                    margin: 0;
                }}
                .title {{
                    text-align: center;
                    font-weight: 800;
                    text-decoration: underline;
                    margin: 2px 0 4px 0;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                }}
                .details td {{
                    padding: 2px 3px;
                    vertical-align: bottom;
                }}
                .label {{
                    font-weight: 700;
                    white-space: nowrap;
                }}
                .line-value {{
                    border-bottom: 1px solid #000000;
                }}
                .fees {{
                    margin-top: 4px;
                    font-size: {density['table_font']};
                }}
                .fees th,
                .fees td {{
                    border: 1px solid #000000;
                    padding: {density['cell_padding']};
                }}
                .serial {{
                    width: 9%;
                    text-align: center;
                }}
                .amount {{
                    width: 27%;
                    text-align: right;
                    white-space: nowrap;
                }}
                .status-row td,
                .total-row td {{
                    font-weight: 800;
                }}
                .accountant {{
                    margin-top: 9px;
                    font-weight: 700;
                }}
                .history-title {{
                    font-weight: 800;
                    margin-top: 6px;
                }}
                .history {{
                    font-size: 5.8pt;
                    table-layout: fixed;
                }}
                .history th,
                .history td {{
                    border: 1px solid #000000;
                    padding: 1px;
                    text-align: center;
                }}
            </style>
        </head>
        <body>
            <div class="school-name">
                {escape(profile['school_name'])}
            </div>
            <div class="school-line">
                {escape(profile['school_address'])}
            </div>
            <div class="school-line">
                {escape(profile['bicep_number'])}
                &nbsp;&nbsp;|&nbsp;&nbsp;
                {escape(profile['mobile_number'])}
            </div>
            <div class="title">SCHOOL DUES</div>

            <table class="details">
                <tr>
                    <td class="label">Receipt No.</td>
                    <td class="line-value">{receipt_no}</td>
                    <td class="label">Date</td>
                    <td class="line-value">
                        {escape(str(payment_data.get('payment_date') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Name of Student</td>
                    <td class="line-value" colspan="3">
                        {escape(str(payment_data.get('student_name') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Father's Name</td>
                    <td class="line-value" colspan="3">
                        {escape(str(payment_data.get('father_name') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Class</td>
                    <td class="line-value">
                        {escape(str(payment_data.get('class_name') or '-'))}
                    </td>
                    <td class="label">Reg No.</td>
                    <td class="line-value">
                        {escape(str(payment_data.get('admission_number') or '-'))}
                    </td>
                </tr>
            </table>

            <table class="fees">
                <tr>
                    <th class="serial">S.No</th>
                    <th>Particulars</th>
                    <th class="amount">Amount (Rs)</th>
                </tr>
                {item_rows}
                <tr class="status-row">
                    <td colspan="2">Status</td>
                    <td class="amount">PAID</td>
                </tr>
                <tr class="total-row">
                    <td colspan="2">TOTAL</td>
                    <td class="amount">
                        Rs. {float(payment_data.get('total_paid', 0)):,.2f}
                    </td>
                </tr>
            </table>

            <div class="accountant">
                ____________________<br>Accountant
            </div>

            {session_history_html}
        </body>
        </html>
        """

    def _confirm_payment(self):
        if self.payment_completed:
            QMessageBox.information(
                self,
                "Payment Already Saved",
                "This payment has already been saved.",
            )
            return

        if not self.preview_data:
            QMessageBox.warning(
                self,
                "No Receipt Loaded",
                "No payment receipt is available.",
            )
            return

        confirmation = QMessageBox.question(
            self,
            "Confirm Fee Payment",
            f"Confirm payment of "
            f"Rs. {self.preview_data['total_amount']:,.2f} "
            f"for {self.preview_data['student_name']}?\n\n"
            f"This action will mark the selected fee items as Paid.",
        )

        if confirmation != QMessageBox.Yes:
            return

        try:
            if not fee_service.validate_payment_selection(
                self.student_id,
                self.selected_record_ids,
            ):
                raise ValueError(
                    "The fee selection is no longer valid."
                )

            payment_data = fee_service.process_payment(
                student_id=self.student_id,
                selected_record_ids=self.selected_record_ids,
                received_by="admin",
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Payment Error",
                f"The payment could not be saved."
                f"\n\n{error}",
            )
            return

        self.payment_completed = True
        self.payment_data = payment_data

        paid_receipt_html = (
            self._build_paid_receipt_html(
                payment_data
            )
        )

        self.paid_receipt_html = paid_receipt_html

        self.school_copy_label.setText(
            paid_receipt_html
        )
        
        self.parent_copy_label.setText(
            paid_receipt_html
        )

        self.confirm_button.setEnabled(False)
        self.cancel_window_open = True
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self.print_button.setEnabled(True)
        self.confirm_button.setText(
            "Payment Completed"
        )

        self.main_window.pending_fee_payment = None

        # REALTIME_PAY_FEE_REFRESH
        pay_fee_page = self.main_window.pages.get(
            navigation.PAGE_PAY_FEE_SEARCH
        )

        if (
            pay_fee_page is not None
            and hasattr(
                pay_fee_page,
                "refresh_after_payment",
            )
        ):
            pay_fee_page.refresh_after_payment(
                self.student_id
            )

        QMessageBox.information(
            self,
            "Payment Successful",
            f"Payment was saved successfully.\n\n"
            f"Receipt Number: "
            f"{payment_data['receipt_no']}\n"
            f"Total Paid: "
            f"Rs. {payment_data['total_paid']:,.2f}\n\n"
            "Cancel This Payment is available only until "
            "you press Back to Pay Fee.",
        )

    def _cancel_confirmed_payment(self):
        """Cancel only the payment just confirmed on this page."""
        if (
            not self.cancel_window_open
            or not self.payment_completed
            or not self.payment_data
        ):
            QMessageBox.warning(
                self,
                "Cancellation Not Available",
                "This payment can no longer be cancelled from this screen.",
            )
            return

        payment_id = self.payment_data.get(
            "payment_id"
        )

        if not payment_id:
            QMessageBox.critical(
                self,
                "Cancellation Error",
                "The payment transaction could not be identified.",
            )
            return

        confirmation = QMessageBox.question(
            self,
            "Cancel Confirmed Payment",
            f"Cancel receipt {self.payment_data['receipt_no']} "
            f"for Rs. {self.payment_data['total_paid']:,.2f}?\n\n"
            "The selected fee months will become Unpaid again. "
            "Any automatic graduation or archive caused by this "
            "payment will also be reversed.",
        )

        if confirmation != QMessageBox.Yes:
            return

        try:
            cancellation_data = (
                fee_service.cancel_immediate_payment(
                    payment_id=payment_id,
                    cancelled_by="admin",
                )
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Payment Cancellation Error",
                "The payment could not be cancelled."
                f"\n\n{error}",
            )
            return

        self.cancel_window_open = False
        self.payment_completed = False
        self.payment_cancelled = True
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self.print_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.confirm_button.setEnabled(False)
        self.confirm_button.setText(
            "Payment Cancelled"
        )

        cancelled_html = (
            "<div style='font-size:14px; text-align:center;'>"
            "<h2 style='color:#b42318;'>PAYMENT CANCELLED</h2>"
            f"<p><b>Receipt No:</b> {cancellation_data['receipt_no']}</p>"
            f"<p><b>Amount Reopened:</b> "
            f"Rs. {cancellation_data['total_amount']:,.2f}</p>"
            "<p>The selected Monthly Fee records are Unpaid again.</p>"
            "<p>Press Back to Pay Fee to review the updated fee table.</p>"
            "</div>"
        )

        self.school_copy_label.setText(
            cancelled_html
        )
        self.parent_copy_label.setText(
            cancelled_html
        )

        pay_fee_page = self.main_window.pages.get(
            navigation.PAGE_PAY_FEE_SEARCH
        )

        if (
            pay_fee_page is not None
            and hasattr(
                pay_fee_page,
                "refresh_after_payment",
            )
        ):
            pay_fee_page.refresh_after_payment(
                self.student_id
            )

        QMessageBox.information(
            self,
            "Payment Cancelled",
            f"Receipt {cancellation_data['receipt_no']} was cancelled.\n\n"
            f"{cancellation_data['reopened_fee_count']} fee item(s) "
            "were restored to Unpaid.",
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    def _build_printable_paid_copy_html(
        self,
        copy_name,
    ):
        """Build one compact quarter-page School/Student receipt copy."""
        data = self.payment_data
        profile = _receipt_school_profile()
        paid_items = list(
            data.get("paid_items", [])
        )
        density = _receipt_density(
            len(paid_items)
        )
        receipt_no = escape(
            str(data.get("receipt_no") or "-")
        )
        default_session = str(
            data.get("session_year") or ""
        ).strip()
        session_history_html = _session_history_html(data)

        paid_rows = ""

        for serial_number, item in enumerate(
            paid_items,
            start=1,
        ):
            normalized_item = dict(item)
            normalized_item.setdefault(
                "session_year",
                default_session,
            )

            paid_rows += (
                "<tr>"
                f"<td class='serial'>{serial_number}</td>"
                "<td>"
                f"{escape(_receipt_month_label(normalized_item))}"
                "</td>"
                "<td class='amount'>"
                f"Rs. {float(item['amount']):,.2f}"
                "</td>"
                "</tr>"
            )

        return f"""
        <!-- Receipt No: {receipt_no} -->
        <html>
        <head>
            <title>Paid Fee Receipt</title>
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    font-size: {density['body_font']};
                    color: #000000;
                    margin: 0;
                    padding: 0;
                }}
                .school-name {{
                    text-align: center;
                    font-size: {density['header_font']};
                    font-weight: 800;
                    margin: 0;
                    line-height: 1.0;
                }}
                .school-line {{
                    text-align: center;
                    font-size: {density['table_font']};
                    font-weight: 600;
                    margin: 0;
                    line-height: 1.05;
                }}
                .copy-name {{
                    text-align: center;
                    font-weight: 800;
                    margin-top: 1px;
                }}
                .title {{
                    text-align: center;
                    font-weight: 800;
                    text-decoration: underline;
                    margin: 1px 0 3px 0;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                }}
                .details td {{
                    padding: 1px 2px;
                    vertical-align: bottom;
                }}
                .label {{
                    font-weight: 700;
                    white-space: nowrap;
                }}
                .line-value {{
                    border-bottom: 1px solid #000000;
                }}
                .fees {{
                    margin-top: 3px;
                    font-size: {density['table_font']};
                }}
                .fees th,
                .fees td {{
                    border: 1px solid #000000;
                    padding: {density['cell_padding']};
                }}
                .serial {{
                    width: 9%;
                    text-align: center;
                }}
                .amount {{
                    width: 27%;
                    text-align: right;
                    white-space: nowrap;
                }}
                .status-row td,
                .total-row td {{
                    font-weight: 800;
                }}
                .total-row td {{
                    border-top: 2px solid #000000;
                }}
                .accountant {{
                    margin-top: 6px;
                    font-weight: 700;
                }}
                .history-title {{
                    font-weight: 800;
                    margin-top: 4px;
                    font-size: 6.4pt;
                }}
                .history {{
                    font-size: 4.8pt;
                    table-layout: fixed;
                }}
                .history th,
                .history td {{
                    border: 1px solid #000000;
                    padding: 1px 0;
                    text-align: center;
                }}
            </style>
        </head>
        <body>
            <div class="school-name">
                {escape(profile['school_name'])}
            </div>
            <div class="school-line">
                {escape(profile['school_address'])}
            </div>
            <div class="school-line">
                {escape(profile['bicep_number'])}
                &nbsp;&nbsp;|&nbsp;&nbsp;
                {escape(profile['mobile_number'])}
            </div>
            <div class="copy-name">
                {escape(str(copy_name))}
            </div>
            <div class="title">SCHOOL DUES</div>

            <table class="details">
                <tr>
                    <td class="label">Receipt No.</td>
                    <td class="line-value">{receipt_no}</td>
                    <td class="label">Date</td>
                    <td class="line-value">
                        {escape(str(data.get('payment_date') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Name of Student</td>
                    <td class="line-value" colspan="3">
                        {escape(str(data.get('student_name') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Father's Name</td>
                    <td class="line-value" colspan="3">
                        {escape(str(data.get('father_name') or '-'))}
                    </td>
                </tr>
                <tr>
                    <td class="label">Class</td>
                    <td class="line-value">
                        {escape(str(data.get('class_name') or '-'))}
                    </td>
                    <td class="label">Reg No.</td>
                    <td class="line-value">
                        {escape(str(data.get('admission_number') or '-'))}
                    </td>
                </tr>
            </table>

            <table class="fees">
                <tr>
                    <th class="serial">S.No</th>
                    <th>Particulars</th>
                    <th class="amount">Amount (Rs)</th>
                </tr>
                {paid_rows}
                <tr class="status-row">
                    <td colspan="2">Status</td>
                    <td class="amount">PAID</td>
                </tr>
                <tr class="total-row">
                    <td colspan="2">TOTAL</td>
                    <td class="amount">
                        Rs. {float(data.get('total_paid', 0)):,.2f}
                    </td>
                </tr>
            </table>

            <div class="accountant">
                ____________________<br>Accountant
            </div>

            {session_history_html}
        </body>
        </html>
        """

    def _draw_paid_receipt_copy(
        self,
        painter,
        copy_rectangle,
        copy_name,
        printer_resolution,
    ):
        """Draw one compact receipt copy inside a quarter A4 rectangle."""
        logical_dpi = 96.0
        dpi_scale = max(
            1.0,
            float(printer_resolution)
            / logical_dpi,
        )
        logical_width = (
            copy_rectangle.width()
            / dpi_scale
        )
        logical_height = (
            copy_rectangle.height()
            / dpi_scale
        )

        document = QTextDocument()
        document.setDocumentMargin(2)
        document.setHtml(
            self._build_printable_paid_copy_html(
                copy_name
            )
        )
        document.setPageSize(
            QSizeF(
                logical_width,
                logical_height,
            )
        )
        document.setTextWidth(
            logical_width
        )
        document.adjustSize()

        document_height = max(
            1.0,
            document.size().height(),
        )
        height_fit = min(
            1.0,
            logical_height
            / document_height,
        )
        final_scale = (
            dpi_scale
            * height_fit
        )

        painter.save()
        painter.translate(
            copy_rectangle.left(),
            copy_rectangle.top(),
        )
        painter.scale(
            final_scale,
            final_scale,
        )
        document.drawContents(
            painter,
            QRectF(
                0,
                0,
                logical_width / height_fit,
                logical_height / height_fit,
            ),
        )
        painter.restore()

    def _paid_receipt_layout_rectangles(
        self,
        page_rectangle,
    ):
        """Return two side-by-side copies inside only the top half of A4."""
        page_left = float(
            page_rectangle.left()
        )
        page_top = float(
            page_rectangle.top()
        )
        page_width = float(
            page_rectangle.width()
        )
        page_height = float(
            page_rectangle.height()
        )

        horizontal_cut_y = (
            page_top
            + page_height / 2.0
        )
        side_margin = max(
            18.0,
            page_width * 0.018,
        )
        top_margin = max(
            14.0,
            page_height * 0.010,
        )
        horizontal_cut_clearance = max(
            18.0,
            page_height * 0.009,
        )
        vertical_cut_gap = max(
            18.0,
            page_width * 0.012,
        )

        receipt_top = (
            page_top
            + top_margin
        )
        receipt_bottom = (
            horizontal_cut_y
            - horizontal_cut_clearance
        )
        usable_height = max(
            1.0,
            receipt_bottom
            - receipt_top,
        )
        usable_width = max(
            2.0,
            page_width
            - side_margin * 2.0,
        )
        copy_width = max(
            1.0,
            (
                usable_width
                - vertical_cut_gap
            )
            / 2.0,
        )
        first_left = (
            page_left
            + side_margin
        )
        second_left = (
            first_left
            + copy_width
            + vertical_cut_gap
        )

        school_copy = QRectF(
            first_left,
            receipt_top,
            copy_width,
            usable_height,
        )
        student_copy = QRectF(
            second_left,
            receipt_top,
            copy_width,
            usable_height,
        )

        return {
            "school_copy": school_copy,
            "student_copy": student_copy,
            "horizontal_cut_y": horizontal_cut_y,
            "vertical_cut_x": (
                first_left
                + copy_width
                + vertical_cut_gap / 2.0
            ),
            "receipt_top": receipt_top,
            "receipt_bottom": receipt_bottom,
            "page_left": page_left,
            "page_width": page_width,
        }

    def _render_paid_receipt_page(
        self,
        printer,
    ):
        """
        Render one payment in the top half of A4:
        School Copy on the left and Student Copy on the right.
        The lower half remains blank for a second payment later.
        """
        painter = QPainter()

        if not painter.begin(printer):
            raise RuntimeError(
                "Printer painter could not be started."
            )

        try:
            page_rectangle = (
                printer.pageLayout()
                .paintRectPixels(
                    printer.resolution()
                )
            )
            layout = (
                self._paid_receipt_layout_rectangles(
                    page_rectangle
                )
            )

            self._draw_paid_receipt_copy(
                painter,
                layout["school_copy"],
                "School Copy",
                printer.resolution(),
            )
            self._draw_paid_receipt_copy(
                painter,
                layout["student_copy"],
                "Student Copy",
                printer.resolution(),
            )

            cut_pen = QPen(
                QColor("#000000")
            )
            cut_pen.setStyle(
                Qt.PenStyle.DashLine
            )
            cut_pen.setWidth(
                max(
                    1,
                    int(
                        printer.resolution()
                        / 180
                    ),
                )
            )

            painter.save()
            painter.setPen(cut_pen)

            # Horizontal line: this one payment occupies the upper A4 half.
            painter.drawLine(
                int(layout["page_left"]),
                int(layout["horizontal_cut_y"]),
                int(
                    layout["page_left"]
                    + layout["page_width"]
                ),
                int(layout["horizontal_cut_y"]),
            )

            # Vertical line: separates School Copy and Student Copy.
            painter.drawLine(
                int(layout["vertical_cut_x"]),
                int(layout["receipt_top"]),
                int(layout["vertical_cut_x"]),
                int(layout["receipt_bottom"]),
            )
            painter.restore()

        finally:
            painter.end()

    def _print_paid_receipt(self):
        """Print one half-A4 payment with two side-by-side copies."""
        if (
            not self.payment_completed
            or not self.payment_data
        ):
            QMessageBox.warning(
                self,
                "Payment Not Completed",
                "Complete the fee payment before "
                "printing the receipt.",
            )
            return

        printer = QPrinter(
            QPrinter.PrinterMode.HighResolution
        )

        printer.setResolution(
            300
        )
        printer.setPageSize(
            QPageSize(
                QPageSize.PageSizeId.A4
            )
        )

        print_dialog = QPrintDialog(
            printer,
            self,
        )

        print_dialog.setWindowTitle(
            "Print Paid Fee Receipt"
        )

        if (
            print_dialog.exec()
            != QDialog.DialogCode.Accepted
        ):
            return

        try:
            self._render_paid_receipt_page(
                printer
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "Receipt Printing Error",
                "The paid receipt could not "
                "be printed."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "Receipt Sent to Printer",
            "The paid receipt was sent to "
            "the selected printer.",
        )
    def _download_paid_receipt_pdf(self):
        """
        Save one A4 PDF with one payment in the top half:
        School Copy left, Student Copy right.
        """
        if (
            not self.payment_completed
            or not self.payment_data
        ):
            QMessageBox.warning(
                self,
                "Payment Not Completed",
                "Complete the fee payment before "
                "downloading the receipt.",
            )
            return

        data = self.payment_data

        default_name = build_paid_receipt_default_filename(data)

        file_path, _selected_filter = (
            QFileDialog.getSaveFileName(
                self,
                "Save Paid Fee Receipt",
                default_name,
                "PDF Files (*.pdf)",
            )
        )

        if not file_path:
            return

        file_path = make_duplicate_safe_pdf_path(file_path)

        try:
            printer = QPrinter(
                QPrinter.PrinterMode.HighResolution
            )

            printer.setResolution(
                300
            )
            printer.setPageSize(
                QPageSize(
                    QPageSize.PageSizeId.A4
                )
            )
            printer.setOutputFormat(
                QPrinter.OutputFormat.PdfFormat
            )
            printer.setOutputFileName(
                file_path
            )

            self._render_paid_receipt_page(
                printer
            )

        except Exception as error:
            QMessageBox.critical(
                self,
                "PDF Download Error",
                "The paid receipt PDF could not "
                "be created."
                f"\n\n{error}",
            )
            return

        QMessageBox.information(
            self,
            "Receipt Downloaded",
            "The paid receipt was saved successfully."
            "\n\n"
            "One payment uses the top half of A4. "
            "School Copy is left and Student Copy is right."
            "\n\n"
            f"{file_path}",
        )
    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _back_to_pay_fee(self):
        """
        Return to the Pay Fee screen with authoritative fee records.

        Leaving this screen permanently closes the one-time immediate
        cancellation window for the confirmed payment.

        After payment, the selected student remains open and the
        latest Paid/Unpaid statuses are loaded from the database.
        """
        self.cancel_window_open = False
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)

        pay_fee_page = self.main_window.pages.get(
            navigation.PAGE_PAY_FEE_SEARCH
        )

        refresh_student_id = (
            self.student_id
        )

        if self.historical_receipt_mode:
            if (
                pay_fee_page is not None
                and hasattr(
                    pay_fee_page,
                    "_show_search_view",
                )
            ):
                pay_fee_page._show_search_view()
        elif (
            pay_fee_page is not None
            and refresh_student_id is not None
            and hasattr(
                pay_fee_page,
                "refresh_after_payment",
            )
        ):
            pay_fee_page.refresh_after_payment(
                refresh_student_id
            )

        self.main_window.go_to_page(
            navigation.PAGE_PAY_FEE_SEARCH
        )

        self.clear_receipt()
