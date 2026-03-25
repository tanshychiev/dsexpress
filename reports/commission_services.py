from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from django.utils import timezone


COMMISSION_START_AFTER_PC = 10
COMMISSION_PER_PC_KHR = 1500


def _new_day_row():
    return {
        "date": None,
        "morning_assign": 0,
        "afternoon_assign": 0,
        "done_morning": 0,
        "done_afternoon": 0,
        "total_done_pc": 0,
        "commission_pc": 0,
        "commission_khr": 0,
        "is_all_zero": True,
    }


def _to_local(dt_value):
    if not dt_value:
        return None
    try:
        if timezone.is_aware(dt_value):
            return timezone.localtime(dt_value)
    except Exception:
        pass
    return dt_value


def _get_shift_name(dt_value) -> str:
    """
    Business rule:
    - morning   = 12:00 AM -> 11:59:59 AM
    - afternoon = 12:00 PM -> 11:59:59 PM
    """
    dt_value = _to_local(dt_value)
    if not dt_value:
        return "afternoon"
    return "morning" if 0 <= dt_value.hour < 12 else "afternoon"


def _get_day_key(dt_value):
    dt_value = _to_local(dt_value)
    if not dt_value:
        return None
    return dt_value.date()


def _finalize_day_row(row):
    row["total_done_pc"] = row["done_morning"] + row["done_afternoon"]
    row["commission_pc"] = max(row["total_done_pc"] - COMMISSION_START_AFTER_PC, 0)
    row["commission_khr"] = row["commission_pc"] * COMMISSION_PER_PC_KHR
    row["is_all_zero"] = (
        row["morning_assign"] == 0
        and row["afternoon_assign"] == 0
        and row["done_morning"] == 0
        and row["done_afternoon"] == 0
        and row["total_done_pc"] == 0
        and row["commission_khr"] == 0
    )


def _daterange(start_date, end_date):
    if not start_date or not end_date:
        return []

    out = []
    cur = start_date
    while cur <= end_date:
        out.append(cur)
        cur += timedelta(days=1)
    return out


def build_shipper_commission_report(pp_batches, pp_items, start_date=None, end_date=None):
    """
    Group by SHIPPER + ASSIGN DATE

    IMPORTANT BUSINESS RULE:
    - morning/afternoon is decided by batch.assigned_at
    - assign count uses batch.assigned_at
    - done count also uses batch.assigned_at
    - do NOT use delivery clear datetime
    - do NOT use clear COD datetime
    - if batch assigned in morning and done in afternoon, still record done_morning
    - if batch assigned in afternoon and done next day, still record done_afternoon on assign date
    """
    grouped = defaultdict(_new_day_row)
    shipper_names = set()
    activity_dates = []

    # -----------------------------
    # ASSIGN
    # -----------------------------
    for batch in pp_batches:
        assigned_at = getattr(batch, "assigned_at", None)
        if not assigned_at:
            continue

        day_key = _get_day_key(assigned_at)
        shift = _get_shift_name(assigned_at)
        if not day_key:
            continue

        shipper = getattr(batch, "shipper", None)
        shipper_name = getattr(shipper, "name", "") or "-"
        shipper_names.add(shipper_name)

        activity_dates.append(day_key)

        key = (shipper_name, day_key)
        row = grouped[key]
        row["date"] = day_key

        prefetched_items = getattr(batch, "_prefetched_objects_cache", {}).get("items")
        item_count = len(prefetched_items) if prefetched_items is not None else batch.items.count()

        if shift == "morning":
            row["morning_assign"] += item_count
        else:
            row["afternoon_assign"] += item_count

    # -----------------------------
    # DONE
    # count by ASSIGN DATE + ASSIGN SHIFT
    # -----------------------------
    for item in pp_items:
        if not getattr(item, "ticked", False):
            continue

        batch = getattr(item, "batch", None)
        if not batch:
            continue

        assigned_at = getattr(batch, "assigned_at", None)
        if not assigned_at:
            continue

        day_key = _get_day_key(assigned_at)
        shift = _get_shift_name(assigned_at)
        if not day_key:
            continue

        shipper = getattr(batch, "shipper", None)
        shipper_name = getattr(shipper, "name", "") or "-"
        shipper_names.add(shipper_name)

        activity_dates.append(day_key)

        key = (shipper_name, day_key)
        row = grouped[key]
        row["date"] = day_key

        if shift == "morning":
            row["done_morning"] += 1
        else:
            row["done_afternoon"] += 1

    # -----------------------------
    # DATE RANGE
    # -----------------------------
    if activity_dates:
        min_activity_date = min(activity_dates)
        max_activity_date = max(activity_dates)
    else:
        min_activity_date = None
        max_activity_date = None

    final_start_date = start_date or min_activity_date
    final_end_date = end_date or max_activity_date
    all_dates = _daterange(final_start_date, final_end_date)

    # -----------------------------
    # FILL MISSING DATES
    # -----------------------------
    if all_dates:
        for shipper_name in shipper_names:
            for d in all_dates:
                key = (shipper_name, d)
                row = grouped[key]
                row["date"] = d

    # -----------------------------
    # GROUP BY SHIPPER
    # -----------------------------
    shipper_map = defaultdict(list)

    for (shipper_name, _day_key), row in grouped.items():
        _finalize_day_row(row)
        shipper_map[shipper_name].append(row)

    shipper_groups = []
    grand_total = {
        "morning_assign": 0,
        "afternoon_assign": 0,
        "done_morning": 0,
        "done_afternoon": 0,
        "total_done_pc": 0,
        "commission_pc": 0,
        "commission_khr": 0,
    }

    for shipper_name in sorted(shipper_map.keys(), key=lambda x: (x or "").lower()):
        rows = sorted(shipper_map[shipper_name], key=lambda x: x["date"])

        shipper_total = {
            "morning_assign": sum(x["morning_assign"] for x in rows),
            "afternoon_assign": sum(x["afternoon_assign"] for x in rows),
            "done_morning": sum(x["done_morning"] for x in rows),
            "done_afternoon": sum(x["done_afternoon"] for x in rows),
            "total_done_pc": sum(x["total_done_pc"] for x in rows),
            "commission_pc": sum(x["commission_pc"] for x in rows),
            "commission_khr": sum(x["commission_khr"] for x in rows),
        }

        grand_total["morning_assign"] += shipper_total["morning_assign"]
        grand_total["afternoon_assign"] += shipper_total["afternoon_assign"]
        grand_total["done_morning"] += shipper_total["done_morning"]
        grand_total["done_afternoon"] += shipper_total["done_afternoon"]
        grand_total["total_done_pc"] += shipper_total["total_done_pc"]
        grand_total["commission_pc"] += shipper_total["commission_pc"]
        grand_total["commission_khr"] += shipper_total["commission_khr"]

        shipper_groups.append(
            {
                "shipper_name": shipper_name,
                "rows": rows,
                "shipper_total": shipper_total,
            }
        )

    return {
        "shipper_groups": shipper_groups,
        "grand_total": grand_total,
    }