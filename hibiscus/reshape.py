"""Correcting dates that are already written down.

`cycle.py` works out what a set of days *means*. This works out what a correction *does* to
them, and like `cycle.py` the rules are plain data in, plain data out, so the console side and
the diary side cannot drift apart on the awkward cases.

There are two corrections and they are deliberately not one:

  **Move** — "the whole thing is on the wrong dates." Every day shifts by the same number of
  days. Nothing is added and nothing is lost: a heavy Tuesday is a heavy Wednesday.

  **Resize** — "it started before I logged it", "it went on longer", "it was shorter than
  that." Every day still inside the new span stays exactly where it is, days outside it go,
  and the stretch between a new edge and the nearest surviving day is filled in.

Typed dates alone cannot tell the two apart. 5–9 Sep becoming 6–10 Sep is a move to one
person and a resize to another, and guessing would quietly rewrite which day was heavy. So
the screens ask which is meant, and each has its own function here.

Everything above the line is pure. `apply` at the bottom is the one thing that writes, and it
lives here so that both sides write a plan the same way.
"""
from dataclasses import dataclass
from datetime import timedelta

from . import cycle


@dataclass(frozen=True)
class Plan:
    """What a correction does to the rows, and where the period ends up."""
    moves: tuple = ()      # ((from, to), …) — already in an order that is safe row by row
    adds: tuple = ()       # dates that need a row they do not have
    removes: tuple = ()    # dates whose rows go
    after: tuple = ()      # every date the period occupies once this is applied

    @property
    def changes(self):
        """False when the dates typed are the dates already there."""
        return bool(self.moves or self.adds or self.removes)


def run_starting(days, start):
    """The period whose day 1 is `start`, or None. `days` is `(date, flow)` pairs."""
    return next((p for p in cycle.periods(days) if p.start == start), None)


def _span(first, last):
    """Every date from `first` to `last` inclusive, or nothing at all when last < first."""
    if last < first:
        return ()
    return tuple(first + timedelta(days=i) for i in range((last - first).days + 1))


def move(dates, first_day):
    """Shift the whole run so its first day lands on `first_day`."""
    rows = sorted(set(dates))
    if not rows:
        return Plan()
    shift = first_day - rows[0]
    if not shift:
        return Plan(after=tuple(rows))

    # One row at a time trips over "one day per person" unless the order is right: moving
    # forward, start from the last day; moving back, start from the first. The other way
    # round, 1→2 lands on the 2 that has not moved out of the way yet.
    order = reversed(rows) if shift > timedelta(0) else rows
    return Plan(moves=tuple((day, day + shift) for day in order),
                after=tuple(day + shift for day in rows))


def resize(dates, first_day, last_day):
    """Trim or stretch the run to `first_day`–`last_day`, leaving the days inside alone.

    Returns None when not one logged day would survive — that is not a resize at all, it is a
    move with the old days thrown away, and the caller says so rather than doing it quietly.
    """
    rows = sorted(set(dates))
    kept = [day for day in rows if first_day <= day <= last_day]
    if not kept:
        return None

    # Only the stretch between a new edge and the nearest surviving day is filled. Gaps in the
    # middle are left alone: a day missing inside a run was bridged on purpose, and filling it
    # here would invent a flow nobody recorded.
    adds = _span(first_day, kept[0] - timedelta(days=1)) + _span(kept[-1] + timedelta(days=1),
                                                                last_day)
    removes = tuple(day for day in rows if day < first_day or day > last_day)
    return Plan(adds=adds, removes=removes, after=tuple(sorted(set(kept) | set(adds))))


def clashes_with(plan, taken):
    """Dates where this plan would land on a row belonging to some other period."""
    return sorted(set(taken) & set(plan.after)) if plan else []


# --------------------------------------------------------------------------- #
# The one impure thing here
# --------------------------------------------------------------------------- #
def apply(person, plan, flow=None, actor=None):
    """Write a plan. `flow` is required when the plan adds days; `actor` marks them as
    entered on the person's behalf, so it is None when they did it themselves.

    Django is imported inside this function rather than at the top, so that everything above
    stays importable — and testable — without a database.
    """
    from django.db import transaction
    from django.utils import timezone

    from .models import PeriodDay

    if not plan.changes:
        return
    now = timezone.now()
    with transaction.atomic():
        for was, becomes in plan.moves:
            # update() rather than save(): auto_now does not fire on it, so updated_at is set
            # here by hand and the move still shows as a change.
            PeriodDay.objects.filter(person=person, date=was).update(date=becomes,
                                                                     updated_at=now)
        if plan.removes:
            PeriodDay.objects.filter(person=person, date__in=plan.removes).delete()
        for day in plan.adds:
            PeriodDay.objects.create(person=person, date=day, flow=flow, created_by=actor)
