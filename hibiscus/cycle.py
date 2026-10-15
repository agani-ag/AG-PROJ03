"""The cycle rules — the one piece that can be quietly wrong, so it lives alone and is pure.

Everything here takes plain data (dates and flow levels) and returns plain data. No database, no
request, no models. That is what makes the worked examples in the mockup usable as tests.

The rules, agreed 15 Oct 2026:

  1. One row per person per day.
  2. A period is a run of days. **One** missing day inside a run is bridged, so a forgotten log
     does not split a period in two. **Two or more** empty days start a new period.
  3. Day 1 is the first day of the run that counts — light, medium, heavy or not-recorded.
     Spotting before that shows on the calendar but is not day 1.
  4. The period ends on the last counting day. Spotting after it is shown, not counted.
  5. A run of only spotting is not a period at all.
  6. Period length = day 1 to the last counting day, inclusive (a bridged day inside counts).
  7. Cycle length = days between day 1 of consecutive periods, ignoring gaps under 1 or over 89.
  8. Predictions anchor on day 1 of the most recent period.
"""
from dataclasses import dataclass
from datetime import date, timedelta

# A run continues across this many missing days. 1 = a single forgotten day is bridged.
BRIDGE_DAYS = 1

# Defaults until there is enough history, and the fixed luteal phase. Same numbers as the Flask
# app; they are settings on the admin page, passed in here.
DEFAULT_CYCLE = 28
DEFAULT_PERIOD = 5
LUTEAL = 14

# Cycle gaps outside this range are ignored when averaging — a 3-month gap says nothing useful
# about a usual cycle, and a 0-day gap is a data-entry slip.
MIN_GAP, MAX_GAP = 1, 90

SPOTTING = 1          # mirrors models.Flow.SPOTTING without importing Django


def _counts(flow):
    return flow is not None and flow != SPOTTING


@dataclass(frozen=True)
class Period:
    """One period, as derived from the days. `start` is day 1 by rule 3."""
    start: date
    end: date
    shown_from: date      # including leading spotting, for the calendar
    shown_to: date
    days: int             # length by rule 6

    @property
    def length(self):
        return self.days


def runs(days):
    """Group `(date, flow)` pairs into runs, bridging a single missing day.

    `days` may be in any order. Returns a list of lists of (date, flow), each sorted by date.
    """
    rows = sorted({d: f for d, f in days}.items())
    out, current = [], []
    for day, flow in rows:
        if current and (day - current[-1][0]).days > BRIDGE_DAYS + 1:
            out.append(current)
            current = []
        current.append((day, flow))
    if current:
        out.append(current)
    return out


def periods(days):
    """Every period in the data, oldest first. Spotting-only runs are not periods (rule 5)."""
    found = []
    for run in runs(days):
        counting = [d for d, f in run if _counts(f)]
        if not counting:
            continue                                   # rule 5
        start, end = counting[0], counting[-1]
        found.append(Period(
            start=start,
            end=end,
            shown_from=run[0][0],
            shown_to=run[-1][0],
            days=(end - start).days + 1,               # rule 6
        ))
    return found


def gaps(found):
    """Cycle gaps in days between consecutive periods, filtered by rule 7."""
    out = []
    for before, after in zip(found, found[1:]):
        gap = (after.start - before.start).days
        if MIN_GAP <= gap < MAX_GAP:
            out.append(gap)
    return out


def _mean_half_up(values):
    """Average, rounded half-up. Python's round() is banker's rounding, so round(28.5) is 28 and
    hand-checked numbers disagree with the screen."""
    if not values:
        return None
    total = sum(values)
    n = len(values)
    return int((total * 2 + n) // (2 * n))


@dataclass
class Stats:
    count: int = 0                  # how many periods
    avg_cycle: int | None = None
    avg_period: int | None = None
    last_start: date | None = None
    last_end: date | None = None
    predicted_next: date | None = None
    days_until: int | None = None
    cycle_day: int | None = None
    cycle_len: int = DEFAULT_CYCLE
    period_len: int = DEFAULT_PERIOD
    estimated: bool = True          # True while the defaults are standing in for real averages

    @property
    def is_overdue(self):
        return self.days_until is not None and self.days_until < 0


def stats(days, today=None, default_cycle=DEFAULT_CYCLE, default_period=DEFAULT_PERIOD,
          cycle_hint=None):
    """Everything the screens show about where a cycle stands.

    `today` is the person's local date — always passed in, never read from the clock here, because
    the old app used the server's UTC date and was wrong by 5½ hours for anyone in India.
    `cycle_hint` is what they told us at sign-up, used before there are two periods to measure.
    """
    today = today or date.today()
    found = periods(days)
    s = Stats(count=len(found))

    measured_gaps = gaps(found)
    s.avg_cycle = _mean_half_up(measured_gaps)
    s.avg_period = _mean_half_up([p.days for p in found])

    s.cycle_len = s.avg_cycle or cycle_hint or default_cycle
    s.period_len = s.avg_period or default_period
    s.estimated = s.avg_cycle is None

    if found:
        last = found[-1]
        s.last_start, s.last_end = last.start, last.end
        s.predicted_next = last.start + timedelta(days=s.cycle_len)
        s.days_until = (s.predicted_next - today).days
        s.cycle_day = (today - last.start).days + 1
    return s


def windows(cycle_len):
    """Ovulation day and the fertile window, as day numbers in the cycle.

    A fixed 14-day luteal phase and a 7-day window, exactly as the Flask app had it.
    """
    ovulation = max(1, cycle_len - LUTEAL)
    return {
        'ovulation': ovulation,
        'fertile_from': max(1, ovulation - 5),
        'fertile_to': ovulation + 1,
    }


PHASES = {
    'menstrual': 'Menstrual',
    'follicular': 'Follicular',
    'fertile': 'Fertile window',
    'ovulation': 'Ovulation',
    'luteal': 'Luteal',
    'late': 'Late',
}


def phase(s, today=None):
    """Which phase today is in, or None when there is nothing to go on."""
    if not s.last_start:
        return None
    today = today or date.today()
    day = (today - s.last_start).days + 1
    if day < 1:
        return None
    w = windows(s.cycle_len)
    if day <= s.period_len:
        return 'menstrual'
    if w['fertile_from'] <= day <= w['fertile_to']:
        return 'ovulation' if day == w['ovulation'] else 'fertile'
    if day < w['fertile_from']:
        return 'follicular'
    if day <= s.cycle_len:
        return 'luteal'
    return 'late'


def expected_days(s, months_ahead=12):
    """The days to shade as expected: the next `months_ahead` cycles, each `period_len` long.

    Starts at the *next* cycle, so the current one is never shaded as a prediction.
    """
    out = set()
    if not s.last_start:
        return out
    for k in range(1, months_ahead + 1):
        start = s.last_start + timedelta(days=s.cycle_len * k)
        for i in range(s.period_len):
            out.add(start + timedelta(days=i))
    return out


def month_map(days, year, month, today=None, **stats_kwargs):
    """What each day of a month should look like: `{date: 'f3'|'pred'|'ovu'|'fer'}`.

    Priority, highest first: a logged day, then an expected day, then ovulation, then the fertile
    window — the same order the old calendar used, so nothing has to be relearned.
    """
    today = today or date.today()
    logged = {d: f for d, f in days}
    s = stats(days, today=today, **stats_kwargs)

    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)

    expected = expected_days(s)
    w = windows(s.cycle_len)
    special = {}
    if s.last_start:
        for k in range(0, 13):
            base = s.last_start + timedelta(days=s.cycle_len * k)
            for day_no in range(w['fertile_from'], w['fertile_to'] + 1):
                d = base + timedelta(days=day_no - 1)
                special[d] = 'ovu' if day_no == w['ovulation'] else 'fer'

    out = {}
    day = first
    while day <= last:
        if day in logged:
            out[day] = f'f{logged[day]}'
        elif day in expected:
            out[day] = 'pred'
        elif day in special:
            out[day] = special[day]
        day += timedelta(days=1)
    return out
