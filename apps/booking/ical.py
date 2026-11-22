"""Minimal RFC 5545 writer for read-only booking feeds (no dependency needed)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from django.conf import settings

from apps.booking.models import Booking

PRODID = "-//PFM-NG//Booking feed//EN"


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold lines longer than 75 octets, as the RFC requires."""
    encoded = line.encode()
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    current = b""
    for char in line:
        b = char.encode()
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current.decode())
            current = b""
        current += b
    parts.append(current.decode())
    return "\r\n ".join(parts)


def _utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def render(bookings: Iterable[Booking]) -> str:
    host = settings.PFM_URL.split("://", 1)[-1]
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:PFM",
    ]
    for b in bookings:
        summary = f"{b.resource.name}" + (f" — {b.title}" if b.title else "")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{b.public_id}@{host}",
            f"DTSTAMP:{_utc(b.updated_at)}",
            f"DTSTART:{_utc(b.start)}",
            f"DTEND:{_utc(b.end)}",
            f"SUMMARY:{_escape(summary)}",
            f"LOCATION:{_escape(f'{b.space.name} — {b.resource.area}')}",
            f"URL:{settings.PFM_URL}/s/{b.space.slug}/booking/{b.resource.public_id}/",
        ]
        if b.note:
            lines.append(f"DESCRIPTION:{_escape(b.note)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
