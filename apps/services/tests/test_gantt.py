import datetime as dt
from dataclasses import dataclass

import pytest

from apps.services.gantt import layout


@dataclass
class P:
    start_date: dt.date
    end_date: dt.date


D = dt.date


def test_bar_position_and_width():
    # Window: Jan + Feb 2026 = 59 days. Project Jan 1 - Jan 31 inclusive = 31 days.
    result = layout([P(D(2026, 1, 1), D(2026, 1, 31))], D(2026, 1, 15), 2, D(2026, 1, 10))
    bar = result.bars[0]
    assert bar.x == 0
    assert bar.width == pytest.approx(31 * 100 / 59, abs=0.01)
    assert not bar.clipped_left and not bar.clipped_right
    assert [t.label for t in result.months] == [D(2026, 1, 1), D(2026, 2, 1)]
    assert result.today == pytest.approx(9 * 100 / 59, abs=0.01)


def test_clipping_and_exclusion():
    items = [
        P(D(2025, 12, 1), D(2026, 1, 10)),  # starts before
        P(D(2026, 2, 20), D(2026, 4, 1)),  # ends after
        P(D(2025, 1, 1), D(2025, 2, 1)),  # entirely before: dropped
        P(D(2026, 3, 1), D(2026, 3, 2)),  # entirely after: dropped
    ]
    result = layout(items, D(2026, 1, 1), 2, D(2027, 1, 1))
    assert len(result.bars) == 2
    assert result.bars[0].clipped_left and result.bars[0].x == 0
    assert result.bars[1].clipped_right and result.bars[1].x + result.bars[
        1
    ].width == pytest.approx(100)
    assert result.today is None


def test_window_crossing_year_end():
    result = layout([], D(2026, 11, 3), 3, D(2026, 11, 3))
    assert [t.label for t in result.months] == [D(2026, 11, 1), D(2026, 12, 1), D(2027, 1, 1)]
    assert result.end == D(2027, 2, 1)


def test_one_day_project_stays_visible():
    result = layout([P(D(2026, 1, 5), D(2026, 1, 5))], D(2026, 1, 1), 12, D(2026, 1, 1))
    assert result.bars[0].width >= 0.27
