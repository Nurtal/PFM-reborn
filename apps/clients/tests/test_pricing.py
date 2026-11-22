"""Pricing is pure: every rule and edge case without a database."""

import datetime as dt
import zoneinfo
from decimal import Decimal as D

import pytest

from apps.clients.pricing import GridRules, PackageTerms, Period, Rates, quote

PARIS = zoneinfo.ZoneInfo("Europe/Paris")


def at(y, m, d, h=0, mi=0):
    return dt.datetime(y, m, d, h, mi, tzinfo=PARIS)


RATES = Rates(day=D("30.00"), night=D("45.00"), weekend=D("60.00"))
PLAIN = GridRules(tz=PARIS)
FULL = GridRules(tz=PARIS, night_enabled=True, weekend_enabled=True)
MONDAY = (2026, 11, 2)


def by_period(q):
    return {line.period: (line.minutes, line.amount) for line in q.lines}


def test_day_rate_only():
    q = quote(RATES, PLAIN, at(*MONDAY, 9), at(*MONDAY, 11, 30))
    assert q.total == D("75.00")
    assert by_period(q) == {Period.DAY: (150, D("75.00"))}


def test_night_and_weekend_ignored_when_grid_disables_them():
    q = quote(RATES, PLAIN, at(2026, 11, 7, 22), at(2026, 11, 8, 2))  # Saturday night
    assert q.total == D("120.00")


def test_evening_split_day_and_night():
    q = quote(RATES, FULL, at(*MONDAY, 18), at(*MONDAY, 21))
    assert by_period(q) == {Period.DAY: (60, D("30.00")), Period.NIGHT: (120, D("90.00"))}
    assert q.total == D("120.00")


def test_overnight_crosses_midnight_and_morning():
    q = quote(RATES, FULL, at(*MONDAY, 22), at(2026, 11, 3, 9))
    assert by_period(q) == {Period.NIGHT: (600, D("450.00")), Period.DAY: (60, D("30.00"))}


def test_weekend_beats_night():
    q = quote(RATES, FULL, at(2026, 11, 7, 20), at(2026, 11, 7, 23))
    assert by_period(q) == {Period.WEEKEND: (180, D("180.00"))}


def test_friday_night_into_saturday():
    q = quote(RATES, FULL, at(2026, 11, 6, 23), at(2026, 11, 7, 1))
    assert by_period(q) == {Period.NIGHT: (60, D("45.00")), Period.WEEKEND: (60, D("60.00"))}


def test_sunday_night_into_monday_morning():
    q = quote(RATES, FULL, at(2026, 11, 8, 23), at(2026, 11, 9, 9))
    assert by_period(q) == {
        Period.WEEKEND: (60, D("60.00")),
        Period.NIGHT: (480, D("360.00")),
        Period.DAY: (60, D("30.00")),
    }


def test_missing_night_rate_falls_back_to_day_rate():
    rates = Rates(day=D("30"))
    q = quote(rates, FULL, at(*MONDAY, 18), at(*MONDAY, 21))
    assert by_period(q) == {Period.DAY: (180, D("90.00"))}


def test_daytime_night_window_not_crossing_midnight():
    grid = GridRules(tz=PARIS, night_enabled=True, night_start=dt.time(0), night_end=dt.time(6))
    q = quote(RATES, grid, at(*MONDAY, 5), at(*MONDAY, 7))
    assert by_period(q) == {Period.NIGHT: (60, D("45.00")), Period.DAY: (60, D("30.00"))}


def test_custom_weekend_days():
    friday_only = GridRules(tz=PARIS, weekend_enabled=True, weekend_days=1 << 4)
    assert quote(RATES, friday_only, at(2026, 11, 6, 9), at(2026, 11, 6, 10)).total == D("60.00")
    assert quote(RATES, friday_only, at(2026, 11, 7, 9), at(2026, 11, 7, 10)).total == D("30.00")


def test_rounding_half_up_per_line():
    rates = Rates(day=D("10.00"))
    # 7 minutes at 10/h = 1.1666... -> 1.17
    assert quote(rates, PLAIN, at(*MONDAY, 9), at(*MONDAY, 9, 7)).total == D("1.17")
    # 3 minutes at 10.10/h = 0.505 -> 0.51 (half up, not banker's rounding)
    assert quote(Rates(day=D("10.10")), PLAIN, at(*MONDAY, 9), at(*MONDAY, 9, 3)).total == D("0.51")


def test_total_is_decimal_never_float():
    q = quote(RATES, FULL, at(*MONDAY, 9), at(*MONDAY, 10))
    assert isinstance(q.total, D)
    assert all(isinstance(line.amount, D) for line in q.lines)


def test_empty_or_reversed_slot_costs_nothing():
    assert quote(RATES, FULL, at(*MONDAY, 10), at(*MONDAY, 10)).total == D("0.00")
    assert quote(RATES, FULL, at(*MONDAY, 11), at(*MONDAY, 10)).lines == ()


def test_dst_end_night_lasts_one_hour_longer():
    """2026-10-25: 03:00 CEST -> 02:00 CET, so Saturday 22:00 to Sunday 06:00
    lasts 9 real hours (all weekend)."""
    q = quote(RATES, FULL, at(2026, 10, 24, 22), at(2026, 10, 25, 6))
    assert by_period(q) == {Period.WEEKEND: (540, D("540.00"))}


def test_dst_start_night_is_one_hour_shorter():
    """2026-03-29: 02:00 CET -> 03:00 CEST, so 22:00 to 06:00 lasts 7 real hours.
    Weekend pricing is off here, so the whole slot is night."""
    grid = GridRules(tz=PARIS, night_enabled=True)
    q = quote(RATES, grid, at(2026, 3, 28, 22), at(2026, 3, 29, 6))
    assert by_period(q) == {Period.NIGHT: (420, D("315.00"))}


def test_utc_inputs():
    start = dt.datetime(2026, 11, 2, 17, 0, tzinfo=dt.UTC)  # 18:00 Paris
    q = quote(RATES, FULL, start, start + dt.timedelta(hours=2))
    assert by_period(q) == {Period.DAY: (60, D("30.00")), Period.NIGHT: (60, D("45.00"))}


def test_package_covers_its_duration():
    pkg = PackageTerms("Half day", dt.timedelta(hours=4), D("100.00"))
    q = quote(RATES, FULL, at(*MONDAY, 9), at(*MONDAY, 13), package=pkg)
    assert q.total == D("100.00")
    assert [line.period for line in q.lines] == [Period.PACKAGE]


def test_package_shorter_use_still_costs_the_package():
    pkg = PackageTerms("Half day", dt.timedelta(hours=4), D("100.00"))
    q = quote(RATES, FULL, at(*MONDAY, 9), at(*MONDAY, 10), package=pkg)
    assert q.total == D("100.00")


def test_overage_beyond_package_is_billed_hourly_with_periods():
    pkg = PackageTerms("Day", dt.timedelta(hours=8), D("200.00"))
    q = quote(RATES, FULL, at(*MONDAY, 10), at(*MONDAY, 20), package=pkg)
    # 10-18 package, 18-19 day, 19-20 night
    assert q.total == D("200.00") + D("30.00") + D("45.00")


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError):
        quote(RATES, PLAIN, dt.datetime(2026, 11, 2, 9), dt.datetime(2026, 11, 2, 10))
