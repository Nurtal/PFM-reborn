"""CSV export helper shared by every module."""

from __future__ import annotations

from decimal import Decimal


def csv_safe(value: object) -> str:
    """Text for a CSV cell, with spreadsheet formulas neutralised (CSV injection)."""
    text = "" if value is None else str(value)
    if text and text[0] in "=+-@\t\r" and not _is_number(text):
        return "'" + text
    return text


def _is_number(value: str) -> bool:
    try:
        Decimal(value)
    except ArithmeticError:
        return False
    return True
