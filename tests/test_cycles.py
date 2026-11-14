"""Cycle rules. All dates are made up."""

from datetime import date, timedelta

from app import cycles
from app.cycles import Rules

D = timedelta(days=1)
RULES = Rules()  # 28 / 5 / 13 / gap 7 / last 6


def days(start: date, n: int) -> list[date]:
    return [start + k * D for k in range(n)]


def test_gap_of_seven_days_is_still_the_same_period():
    a = date(2030, 1, 1)
    groups = cycles.group_periods([a, a + 7 * D], gap_days=7)
    assert len(groups) == 1
    groups = cycles.group_periods([a, a + 8 * D], gap_days=7)
    assert len(groups) == 2


def test_typical_lengths_until_two_complete_cycles():
    a = date(2030, 1, 1)
    periods = days(a, 4) + days(a + 30 * D, 4)  # one complete cycle of 30 days
    groups = cycles.group_periods(periods, 7)
    stats = cycles.compute_stats(groups, today=a + 40 * D, rules=RULES)
    assert stats.complete_cycles == 1
    assert stats.from_history is False
    assert stats.cycle_length == 28 and stats.period_length == 5
    assert stats.avg_cycle == 30.0  # still shown, just not used yet


def test_average_of_recent_cycles_rounds_half_up():
    a = date(2030, 1, 1)
    starts = [a, a + 30 * D, a + 61 * D]  # cycles of 30 and 31 days -> 30.5 -> 31
    periods = [d for s in starts for d in days(s, 4)]
    stats = cycles.compute_stats(cycles.group_periods(periods, 7), today=a + 70 * D, rules=RULES)
    assert stats.from_history is True
    assert stats.cycle_length == 31
    assert stats.period_length == 4
    assert (stats.min_cycle, stats.max_cycle) == (30, 31)


def test_only_the_last_n_cycles_count():
    a = date(2030, 1, 1)
    lengths = [40, 28, 28]
    starts = [a]
    for n in lengths:
        starts.append(starts[-1] + n * D)
    periods = [d for s in starts for d in days(s, 3)]
    stats = cycles.compute_stats(cycles.group_periods(periods, 7), today=starts[-1] + 5 * D,
                                 rules=Rules(prediction_cycles=2))
    assert stats.cycle_length == 28


def test_ongoing_period_is_left_out_of_the_period_average():
    a = date(2030, 1, 1)
    starts = [a, a + 28 * D, a + 56 * D]
    periods = days(starts[0], 4) + days(starts[1], 4) + days(starts[2], 2)  # last one still going
    stats = cycles.compute_stats(cycles.group_periods(periods, 7), today=starts[2] + D, rules=RULES)
    assert stats.period_length == 4


def test_next_period_and_range():
    a = date(2030, 1, 1)
    starts = [a, a + 29 * D, a + 60 * D]  # 29, 31
    periods = [d for s in starts for d in days(s, 4)]
    today = starts[-1] + 3 * D
    t = cycles.build_timeline(periods, [], today, RULES, horizon=today)
    assert t.next_start == starts[-1] + 30 * D
    assert t.next_range == (starts[-1] + 29 * D, starts[-1] + 31 * D)


def test_late_period_moves_the_prediction_to_tomorrow():
    a = date(2030, 1, 1)
    today = a + 40 * D
    t = cycles.build_timeline(days(a, 5), [], today, RULES, horizon=today)
    assert t.next_start == today + D


def test_fertile_window_from_positive_lh():
    a = date(2030, 1, 1)
    positive = a + 14 * D
    t = cycles.build_timeline(days(a, 5), [positive], a + 2 * D, RULES, horizon=a + 2 * D)
    c = t.cycles[0]
    assert c.measured is True
    assert c.window_start == positive - 4 * D
    assert c.ovulation == positive + D
    info = cycles.describe_day(positive, t.cycles, today=a + 20 * D)
    assert info["phase"] == "fertile" and info["estimated"] is False


def test_fertile_window_without_lh():
    a = date(2030, 1, 1)
    today = a + 2 * D
    t = cycles.build_timeline(days(a, 5), [], today, RULES, horizon=today)
    c = t.cycles[0]
    assert c.measured is False
    assert c.ovulation == t.next_start - 13 * D
    assert c.window_start == c.ovulation - 5 * D


def test_phases_across_one_cycle():
    a = date(2030, 1, 1)
    today = a + 2 * D
    t = cycles.build_timeline(days(a, 5), [], today, RULES, horizon=a + 60 * D)
    c = t.cycles[0]
    phase = lambda d: cycles.describe_day(d, t.cycles, today)["phase"]  # noqa: E731
    assert phase(a) == "menstrual"
    assert phase(a + 5 * D) == "follicular"
    assert phase(c.window_start) == "fertile"
    assert phase(c.ovulation) == "fertile"
    assert phase(c.ovulation + D) == "luteal"
    nxt = cycles.describe_day(t.next_start, t.cycles, today)
    assert nxt["phase"] == "menstrual" and nxt["predicted_cycle"] is True and nxt["cycle_day"] == 1
    assert cycles.describe_day(a - D, t.cycles, today)["phase"] is None


def test_window_of_one_cycle_still_counts_as_history():
    a = date(2030, 1, 1)
    starts = [a, a + 30 * D, a + 56 * D, a + 88 * D]  # 30, 26, 32
    periods = [d for s in starts for d in days(s, 4)]
    stats = cycles.compute_stats(cycles.group_periods(periods, 7), today=starts[-1] + 5 * D,
                                 rules=Rules(prediction_cycles=1))
    assert stats.from_history is True
    assert stats.cycle_length == 32  # only the latest complete cycle


def test_very_long_cycles_are_left_out_of_predictions():
    a = date(2030, 1, 1)
    starts = [a, a + 30 * D, a + 136 * D, a + 168 * D, a + 198 * D]  # 30, 106 (months not recorded), 32, 30
    periods = [d for s in starts for d in days(s, 5)]
    stats = cycles.compute_stats(cycles.group_periods(periods, 7), today=starts[-1] + 3 * D, rules=RULES)
    assert stats.complete_cycles == 4 and stats.excluded_cycles == 1
    assert stats.cycle_length == 31 and (stats.min_cycle, stats.max_cycle) == (30, 32)
    loose = cycles.compute_stats(cycles.group_periods(periods, 7), today=starts[-1] + 3 * D,
                                 rules=Rules(outlier_cycle=120))
    assert loose.excluded_cycles == 0 and loose.max_cycle == 106
