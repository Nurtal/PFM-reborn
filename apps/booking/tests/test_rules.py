"""Pure rule checks: no database."""

import datetime as dt
import zoneinfo

import pytest

from apps.booking.services import SlotRules, check_slot

PARIS = zoneinfo.ZoneInfo("Europe/Paris")
ALL_DAYS = 0b1111111
WEEKDAYS = 0b0011111


def at(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=PARIS)


NOW = at(2026, 11, 2, 7, 0)  # a Monday


def rules(**kw):
    base = dict(
        tz=PARIS,
        open_weekdays=WEEKDAYS,
        day_start=dt.time(8),
        day_end=dt.time(19),
        slot_minutes=30,
    )
    return SlotRules(**{**base, **kw})


def test_valid_slot():
    assert check_slot(rules(), at(2026, 11, 2, 9), at(2026, 11, 2, 11), NOW) == []


def test_end_before_start():
    assert check_slot(rules(), at(2026, 11, 2, 11), at(2026, 11, 2, 9), NOW)


def test_past_is_refused():
    errors = check_slot(rules(), at(2026, 11, 2, 9), at(2026, 11, 2, 10), at(2026, 11, 2, 9, 30))
    assert any("past" in e for e in errors)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (at(2026, 11, 2, 7, 30), at(2026, 11, 2, 9)),  # before opening
        (at(2026, 11, 2, 18), at(2026, 11, 2, 19, 30)),  # after closing
        (at(2026, 11, 2, 18), at(2026, 11, 3, 9)),  # overnight while closed at night
    ],
)
def test_outside_opening_hours(start, end):
    assert any("opening hours" in e for e in check_slot(rules(), start, end, NOW))


def test_closing_time_is_inclusive_end():
    assert check_slot(rules(), at(2026, 11, 2, 18), at(2026, 11, 2, 19), NOW) == []


def test_weekend_closed():
    saturday = at(2026, 11, 7, 9)
    errors = check_slot(rules(), saturday, saturday + dt.timedelta(hours=1), NOW)
    assert any("closed" in e for e in errors)


def test_weekend_open_when_configured():
    saturday = at(2026, 11, 7, 9)
    r = rules(open_weekdays=ALL_DAYS)
    assert check_slot(r, saturday, saturday + dt.timedelta(hours=1), NOW) == []


def test_slot_grid():
    errors = check_slot(rules(), at(2026, 11, 2, 9, 10), at(2026, 11, 2, 10), NOW)
    assert any("slots" in e for e in errors)


def test_slot_grid_follows_opening_time():
    r = rules(day_start=dt.time(8, 15), slot_minutes=60)
    assert check_slot(r, at(2026, 11, 2, 9, 15), at(2026, 11, 2, 10, 15), NOW) == []
    assert check_slot(r, at(2026, 11, 2, 9), at(2026, 11, 2, 10), NOW)


def test_min_and_max_duration():
    r = rules(min_duration=dt.timedelta(hours=1), max_duration=dt.timedelta(hours=2))
    assert check_slot(r, at(2026, 11, 2, 9), at(2026, 11, 2, 9, 30), NOW)
    assert check_slot(r, at(2026, 11, 2, 9), at(2026, 11, 2, 12), NOW)
    assert check_slot(r, at(2026, 11, 2, 9), at(2026, 11, 2, 11), NOW) == []


def test_max_advance():
    r = rules(max_advance=dt.timedelta(days=7))
    assert check_slot(r, at(2026, 11, 16, 9), at(2026, 11, 16, 10), NOW)
    assert check_slot(r, at(2026, 11, 6, 9), at(2026, 11, 6, 10), NOW) == []


# --- Around the clock, midnight and DST ------------------------------------------


def test_24_7_overnight_booking():
    r = rules(open_weekdays=ALL_DAYS, day_start=dt.time(0), day_end=dt.time(0))
    assert check_slot(r, at(2026, 11, 2, 22), at(2026, 11, 3, 6), NOW) == []


def test_booking_ending_exactly_at_midnight():
    r = rules(open_weekdays=ALL_DAYS, day_start=dt.time(0), day_end=dt.time(0))
    assert check_slot(r, at(2026, 11, 2, 22), at(2026, 11, 3, 0), NOW) == []


def test_overnight_into_closed_day_is_refused():
    # Open every day but Saturday, around the clock.
    r = rules(open_weekdays=ALL_DAYS & ~(1 << 5), day_start=dt.time(0), day_end=dt.time(0))
    errors = check_slot(r, at(2026, 11, 6, 22), at(2026, 11, 7, 2), NOW)
    assert any("closed" in e for e in errors)


def test_dst_end_duration_is_real_time():
    """On 2026-10-25 Paris goes from 03:00 CEST back to 02:00 CET: 00:00-06:00 lasts 7 h."""
    r = rules(
        open_weekdays=ALL_DAYS,
        day_start=dt.time(0),
        day_end=dt.time(0),
        max_duration=dt.timedelta(hours=6),
    )
    now = at(2026, 10, 20)
    errors = check_slot(r, at(2026, 10, 25, 0), at(2026, 10, 25, 6), now)
    assert any("maximum duration" in e for e in errors)


def test_dst_start_opening_hours_are_wall_clock():
    """On 2026-03-29 clocks jump 02:00 -> 03:00; 08:00-19:00 is still wall-clock."""
    r = rules(open_weekdays=ALL_DAYS)
    now = at(2026, 3, 20)
    assert check_slot(r, at(2026, 3, 29, 8), at(2026, 3, 29, 19), now) == []
    assert check_slot(r, at(2026, 3, 29, 7, 30), at(2026, 3, 29, 9), now)


def test_utc_input_is_interpreted_in_space_timezone():
    # 08:00 UTC is 09:00 in Paris in November: open.
    start = dt.datetime(2026, 11, 2, 8, 0, tzinfo=dt.UTC)
    assert check_slot(rules(), start, start + dt.timedelta(hours=1), NOW) == []


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        check_slot(rules(), dt.datetime(2026, 11, 2, 9), dt.datetime(2026, 11, 2, 10), NOW)
