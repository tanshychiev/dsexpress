from datetime import datetime
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

HEADERS = [
    "Tracking No",
    "Seller Order Code",
    "Seller Name",
    "Receiver Name",
    "Receiver Phone",
    "Receiver Address",
    "Product Desc",
    "Qty",
    "Price",
    "Delivery Fee",
    "Additional Fee",
    "COD",
    "Status",
    "Reason",
    "Delivery Shipper",
]

def export_update_template_xlsx(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Update Orders"

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D0D0D0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for col, h in enumerate(HEADERS, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border

    for i, o in enumerate(rows, start=2):
        ws.cell(i, 1, o.tracking_no or "")
        ws.cell(i, 2, o.seller_order_code or "")
        ws.cell(i, 3, o.seller_name or (o.seller.name if o.seller else ""))
        ws.cell(i, 4, o.receiver_name or "")
        ws.cell(i, 5, o.receiver_phone or "")
        ws.cell(i, 6, o.receiver_address or "")
        ws.cell(i, 7, o.product_desc or "")
        ws.cell(i, 8, o.quantity or 0)
        ws.cell(i, 9, float(o.price or 0))
        ws.cell(i, 10, float(o.delivery_fee or 0))
        ws.cell(i, 11, float(o.additional_fee or 0))
        ws.cell(i, 12, float(o.cod or 0))
        ws.cell(i, 13, o.status or "")
        ws.cell(i, 14, o.reason or "")
        ws.cell(i, 15, o.delivery_shipper.name if o.delivery_shipper else "")

    widths = [18, 18, 22, 18, 16, 26, 20, 8, 12, 12, 12, 12, 18, 20, 18]
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    filename = f"delivery_report_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response