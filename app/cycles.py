"""Cycle, phase and prediction rules. Pure functions, no database.

The rules follow SPEC.md, section 「周期与时期计算」:

- A period day is a day whose 月经 switch is on. Days with bleeding but the switch
  off are spotting (handled by the caller).
- A new cycle starts on a period day that is more than ``gap_days`` after the
  previous period day.
- Fewer than 2 complete cycles: predict with the typical lengths from settings.
  Otherwise use the mean of the last ``prediction_cycles`` complete cycles.
- Complete cycles longer than ``outlier_cycle`` days (usually unrecorded months)
  are shown but left out of the averages and the range.
- Fertile window (排卵期) = 6 days. With a positive LH test, the first positive day
  is day 5 of the window (Apple's rule) and ovulation is the day after it.
  Without one, ovulation = next period start - ``ovulation_offset`` days and the
  window is that day plus the 5 days before it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

DAY = timedelta(days=1)


@dataclass(frozen=True)
class Rules:
    typical_cycle: int = 28
    typical_period: int = 5
    ovulation_offset: int = 13
    gap_days: int = 7
    prediction_cycles: int = 6
    outlier_cycle: int = 60


@dataclass
class Stats:
    complete_cycles: int
    avg_cycle: float | None
    avg_period: float | None
    min_cycle: int | None
    max_cycle: int | None
    cycle_length: int      # length used for predictions
    period_length: int     # period length used for predictions
    from_history: bool     # False while predictions still use the typical lengths
    excluded_cycles: int = 0  # complete cycles left out as longer than outlier_cycle


@dataclass
class Cycle:
    start: date
    end: date                      # exclusive: the next cycle's first day (actual or predicted)
    complete: bool                 # end is a recorded next period
    predicted: bool                # the whole cycle is a forecast
    menstrual: set[date]           # days counted as 月经期
    window_start: date
    ovulation: date                # last day of the fertile window
    measured: bool                 # window placed from a positive LH test

    @property
    def length(self) -> int:
        return (self.end - self.start).days


@dataclass
class Timeline:
    cycles: list[Cycle] = field(default_factory=list)
    stats: Stats | None = None
    next_start: date | None = None
    next_range: tuple[date, date] | None = None


def round_half_up(x: float) -> int:
    return int(x + 0.5)


def group_periods(period_dates: Iterable[date], gap_days: int) -> list[list[date]]:
    """Split sorted period days into periods; a gap of more than ``gap_days`` starts a new one."""
    groups: list[list[date]] = []
    for d in sorted(set(period_dates)):
        if groups and (d - groups[-1][-1]).days <= gap_days:
            groups[-1].append(d)
        else:
            groups.append([d])
    return groups


def compute_stats(groups: list[list[date]], today: date, rules: Rules) -> Stats:
    starts = [g[0] for g in groups]
    all_lengths = [(b - a).days for a, b in zip(starts, starts[1:])]
    lengths = [n for n in all_lengths if n <= rules.outlier_cycle]
    recent = lengths[-rules.prediction_cycles:]

    spans = [(g[-1] - g[0]).days + 1 for g in groups]
    if groups and groups[-1][-1] >= today - DAY:
        spans = spans[:-1]  # the latest period may still be going on
    recent_spans = spans[-rules.prediction_cycles:]

    avg_cycle = round(sum(recent) / len(recent), 1) if recent else None
    avg_period = round(sum(recent_spans) / len(recent_spans), 1) if recent_spans else None
    from_history = len(lengths) >= 2  # 2 complete cycles overall, even if the window is 1

    cycle_length = round_half_up(sum(recent) / len(recent)) if from_history else rules.typical_cycle
    if from_history and recent_spans:
        period_length = max(1, round_half_up(sum(recent_spans) / len(recent_spans)))
    else:
        period_length = rules.typical_period

    return Stats(
        complete_cycles=len(all_lengths),
        avg_cycle=avg_cycle,
        avg_period=avg_period,
        min_cycle=min(recent) if recent else None,
        max_cycle=max(recent) if recent else None,
        cycle_length=max(1, cycle_length),
        period_length=period_length,
        from_history=from_history,
        excluded_cycles=len(all_lengths) - len(lengths),
    )


def _place_window(start: date, end: date, lh_positive: list[date], rules: Rules) -> tuple[date, date, bool]:
    first_positive = next((d for d in lh_positive if start <= d < end), None)
    if first_positive is not None:
        ovulation = first_positive + DAY
        window_start = first_positive - 4 * DAY
        measured = True
    else:
        ovulation = end - rules.ovulation_offset * DAY
        window_start = ovulation - 5 * DAY
        measured = False
    return max(window_start, start), ovulation, measured


def build_timeline(
    period_dates: Iterable[date],
    lh_positive_dates: Iterable[date],
    today: date,
    rules: Rules,
    horizon: date,
) -> Timeline:
    """Recorded cycles, then forecast cycles until ``horizon`` is covered."""
    groups = group_periods(period_dates, rules.gap_days)
    stats = compute_stats(groups, today, rules)
    timeline = Timeline(stats=stats)
    if not groups:
        return timeline

    lh_positive = sorted(set(lh_positive_dates))
    starts = [g[0] for g in groups]
    cycle_len = stats.cycle_length

    next_start = starts[-1] + cycle_len * DAY
    if next_start <= today:  # late: the next period is at the earliest tomorrow
        next_start = today + DAY
    timeline.next_start = next_start
    if stats.from_history and stats.min_cycle is not None and stats.max_cycle is not None:
        low = max(starts[-1] + stats.min_cycle * DAY, today + DAY)
        high = max(starts[-1] + stats.max_cycle * DAY, low)
        timeline.next_range = (low, high)

    for i, g in enumerate(groups):
        start = g[0]
        complete = i + 1 < len(groups)
        end = starts[i + 1] if complete else next_start
        span = {g[0] + k * DAY for k in range((g[-1] - g[0]).days + 1)}
        window_start, ovulation, measured = _place_window(start, end, lh_positive, rules)
        timeline.cycles.append(Cycle(start, end, complete, False, span, window_start, ovulation, measured))

    start = next_start
    while start <= horizon:
        end = start + cycle_len * DAY
        menstrual = {start + k * DAY for k in range(min(stats.period_length, cycle_len))}
        window_start, ovulation, measured = _place_window(start, end, [], rules)
        timeline.cycles.append(Cycle(start, end, False, True, menstrual, window_start, ovulation, measured))
        start = end
    return timeline


def find_cycle(cycles: list[Cycle], d: date) -> Cycle | None:
    for c in cycles:
        if c.start <= d < c.end:
            return c
    return None


def describe_day(d: date, cycles: list[Cycle], today: date) -> dict:
    """Cycle day and phase of one date. ``estimated`` = not confirmed by records or an LH test."""
    c = find_cycle(cycles, d)
    if c is None:
        return {"cycle_day": None, "phase": None, "estimated": None, "ovulation": False,
                "cycle_start": None, "predicted_cycle": None, "future": d > today}
    if d in c.menstrual:
        phase, estimated = "menstrual", c.predicted
    elif c.window_start <= d <= c.ovulation:
        phase, estimated = "fertile", c.predicted or not c.measured
    elif d < c.window_start:
        phase, estimated = "follicular", c.predicted or not c.measured
    else:
        phase, estimated = "luteal", c.predicted or not c.measured
    return {
        "cycle_day": (d - c.start).days + 1,
        "phase": phase,
        "estimated": estimated,
        "ovulation": d == c.ovulation,
        "cycle_start": c.start.isoformat(),
        "predicted_cycle": c.predicted,
        "future": d > today,
    }
