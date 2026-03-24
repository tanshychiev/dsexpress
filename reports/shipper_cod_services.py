from __future__ import annotations

from collections import defaultdict, OrderedDict
from datetime import time
from decimal import Decimal


RATE_KHR_PER_USD = Decimal("4100")


def _to_decimal(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0.00")


def _new_row() -> dict:
    return {
        "cod": Decimal("0.00"),
        "cash_usd": Decimal("0.00"),
        "cash_khr": Decimal("0.00"),
        "aba_usd": Decimal("0.00"),
        "aba_khr": Decimal("0.00"),
        "expense": Decimal("0.00"),
        "balance_usd": Decimal("0.00"),
        "remark": "",
    }


def _append_text(old_text: str, new_text: str) -> str:
    old_text = str(old_text or "").strip()
    new_text = str(new_text or "").strip()

    if not new_text:
        return old_text
    if not old_text:
        return new_text
    if new_text in old_text:
        return old_text
    return f"{old_text} | {new_text}"


def _get_shift_name(assigned_at) -> str:
    if not assigned_at:
        return "afternoon"

    t = assigned_at.time()
    if time(7, 0, 0) <= t <= time(11, 59, 59):
        return "morning"
    return "afternoon"


def _finalize_balance(row: dict):
    total_receive_usd = (
        row["cash_usd"]
        + row["aba_usd"]
        + (row["cash_khr"] / RATE_KHR_PER_USD)
        + (row["aba_khr"] / RATE_KHR_PER_USD)
    )
    row["balance_usd"] = total_receive_usd - row["cod"] - row["expense"]


def _sum_rows(rows: list[dict]) -> dict:
    total = _new_row()
    for r in rows:
        total["cod"] += _to_decimal(r.get("cod", 0))
        total["cash_usd"] += _to_decimal(r.get("cash_usd", 0))
        total["cash_khr"] += _to_decimal(r.get("cash_khr", 0))
        total["aba_usd"] += _to_decimal(r.get("aba_usd", 0))
        total["aba_khr"] += _to_decimal(r.get("aba_khr", 0))
        total["expense"] += _to_decimal(r.get("expense", 0))
        total["remark"] = _append_text(total["remark"], r.get("remark", ""))
    _finalize_balance(total)
    return total


def build_shipper_cod_report(clear_cod_rows):
    """
    Group by ASSIGN DATE -> ASSIGN SHIFT -> SHIPPER

    Business rule:
    - If assigned in morning but clear COD in afternoon, record in morning of assign date
    - If assigned in afternoon but clear COD next day morning, record in afternoon of assign date
    """

    grouped = OrderedDict()

    for obj in clear_cod_rows:
        batch = getattr(obj, "batch", None)
        if not batch:
            continue

        shipper = getattr(batch, "shipper", None)
        shipper_name = getattr(shipper, "name", "") or "-"
        assigned_at = getattr(batch, "assigned_at", None)

        # IMPORTANT:
        # Always use assigned_at, not COD clear time
        if not assigned_at:
            continue

        day_key = assigned_at.date()
        shift_name = _get_shift_name(assigned_at)

        if day_key not in grouped:
            grouped[day_key] = {
                "morning": defaultdict(_new_row),
                "afternoon": defaultdict(_new_row),
            }

        row = grouped[day_key][shift_name][shipper_name]

        row["cod"] += _to_decimal(getattr(obj, "target_total_usd", 0))
        row["cash_usd"] += _to_decimal(getattr(obj, "cash_usd", 0))
        row["cash_khr"] += _to_decimal(getattr(obj, "cash_khr", 0))
        row["aba_usd"] += _to_decimal(getattr(obj, "aba_usd", 0))
        row["aba_khr"] += _to_decimal(getattr(obj, "aba_khr", 0))
        row["expense"] += _to_decimal(getattr(obj, "expense", 0))
        row["remark"] = _append_text(row["remark"], getattr(obj, "note", ""))

    report_days = []

    grand_morning_rows = []
    grand_afternoon_rows = []
    all_rows = []

    for day_key, day_data in grouped.items():
        morning_rows = []
        for shipper_name, row in day_data["morning"].items():
            _finalize_balance(row)
            x = {"shipper_name": shipper_name, **row}
            morning_rows.append(x)
            grand_morning_rows.append(x)
            all_rows.append(x)

        afternoon_rows = []
        for shipper_name, row in day_data["afternoon"].items():
            _finalize_balance(row)
            x = {"shipper_name": shipper_name, **row}
            afternoon_rows.append(x)
            grand_afternoon_rows.append(x)
            all_rows.append(x)

        morning_rows = sorted(morning_rows, key=lambda x: (x["shipper_name"] or "").lower())
        afternoon_rows = sorted(afternoon_rows, key=lambda x: (x["shipper_name"] or "").lower())

        report_days.append(
            {
                "date": day_key,
                "morning_rows": morning_rows,
                "afternoon_rows": afternoon_rows,
                "morning_total": _sum_rows(morning_rows),
                "afternoon_total": _sum_rows(afternoon_rows),
                "day_total": _sum_rows(morning_rows + afternoon_rows),
            }
        )

    return {
        "days": report_days,
        "grand_morning_total": _sum_rows(grand_morning_rows),
        "grand_afternoon_total": _sum_rows(grand_afternoon_rows),
        "grand_total": _sum_rows(all_rows),
    }