import re

from django import template

register = template.Library()


def _digits(value):
    """Return only phone-number digits."""
    if value is None:
        return ""
    return re.sub(r"\D+", "", str(value).strip())


@register.filter(name="kh_phone")
def kh_phone(value):
    """
    Display Cambodian phone numbers without changing country-code numbers.

    Examples:
      12345678        -> 012345678
      012345678       -> 012345678
      85512345678     -> 85512345678
      +85512345678    -> +85512345678
      0085512345678   -> 0085512345678
    """
    if value is None:
        return ""

    original = str(value).strip()
    digits = _digits(original)
    if not digits:
        return ""

    # Keep international formats unchanged for display.
    if original.startswith("+855"):
        return f"+{digits}"
    if digits.startswith("855") or digits.startswith("00855"):
        return digits

    # Keep local numbers that already start with 0.
    if digits.startswith("0"):
        return digits

    # Imported local number without the first zero.
    return f"0{digits}"


@register.filter(name="kh_phone_tel")
def kh_phone_tel(value):
    """Return a clickable Cambodian international tel: value."""
    digits = _digits(value)
    if not digits:
        return ""

    if digits.startswith("00855"):
        return f"+855{digits[5:].lstrip('0')}"

    if digits.startswith("855"):
        return f"+{digits}"

    if digits.startswith("0"):
        digits = digits[1:]

    return f"+855{digits}"
