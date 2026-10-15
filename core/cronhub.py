"""The cron hub: SyncHub has exactly two scheduled jobs, and modules plug into them.

There is no in-process scheduler. An external cron service calls two URLs:

    every 15 min   GET /cron/tick?key=...       → anything outbound or time-based
    nightly        GET /cron/cleanup?key=...    → retention and housekeeping

A feature module never gets its own cron URL, key or lock. It writes a `cron.py` in its app with
one or both hooks:

    from core import cronhub

    @cronhub.tick('hibiscus.push', budget=5)
    def push(ctx):
        ...
        return {'sent': n}          # anything JSON-able; 'items' is counted if present

    @cronhub.cleanup('hibiscus.retention')
    def retention(ctx):
        return {'items': deleted}

`<app>/cron.py` is imported for every installed app at startup (see core/apps.py), the same way
Django finds `signals.py`, so registering is just importing.

Why it is shaped like this:

  * **isolated** — each hook runs inside its own try/except, so one module's failure can't stop
    another module's work, and the run still reports what did happen;
  * **bounded** — each hook gets a slice of the run's wall-clock budget and can ask how much is
    left (`ctx.seconds_left()`). Cron services abort the HTTP call around 30 s and then retry, so
    finishing early and reporting what remains is much safer than being killed mid-send;
  * **ordered** — hooks run in registration order, which is the order apps appear in
    INSTALLED_APPS, so behaviour is reproducible.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from django.utils import timezone

logger = logging.getLogger(__name__)

TICK = 'tick'
CLEANUP = 'cleanup'

# Each hook's own slice of the run budget, unless it asks for a different one. Small on purpose:
# a hook that wants more should claim work in batches and leave the rest for the next run.
DEFAULT_TICK_BUDGET = 5
DEFAULT_CLEANUP_BUDGET = 10


@dataclass
class Hook:
    name: str                 # "hibiscus.push" — module prefix keeps the run log readable
    kind: str                 # TICK | CLEANUP
    func: Callable
    budget: int


@dataclass
class Context:
    """Passed to every hook. `seconds_left` is the hook's own slice, not the whole run's."""
    kind: str
    now: object = field(default_factory=timezone.now)
    deadline: float = 0.0     # time.monotonic() value this hook should finish by
    triggered_by: str = 'cron'

    def seconds_left(self):
        return max(0.0, self.deadline - time.monotonic())

    def out_of_time(self):
        return self.seconds_left() <= 0


_hooks: list[Hook] = []


def _register(kind, name, func, budget):
    for existing in _hooks:
        if existing.name == name and existing.kind == kind:
            # Re-importing a module (test runner, autoreload) must not double-register.
            existing.func, existing.budget = func, budget
            return func
    _hooks.append(Hook(name=name, kind=kind, func=func, budget=budget))
    return func


def tick(name, budget=DEFAULT_TICK_BUDGET):
    """Register a hook for the 15-minute job."""
    def wrap(func):
        return _register(TICK, name, func, budget)
    return wrap


def cleanup(name, budget=DEFAULT_CLEANUP_BUDGET):
    """Register a hook for the nightly job."""
    def wrap(func):
        return _register(CLEANUP, name, func, budget)
    return wrap


def hooks(kind=None):
    return [h for h in _hooks if kind is None or h.kind == kind]


def run(kind, budget_seconds=20, only=None, triggered_by='cron', **options):
    """Run every hook registered for `kind` and return a summary dict.

    `only` runs a single hook by name (used by the admin page's "Run now"). `options` are passed
    through to the hooks that accept keyword arguments, so a cleanup hook can take `vacuum=True`.
    """
    started = time.monotonic()
    overall_deadline = started + budget_seconds
    summary = {'ok': True, 'job': kind, 'items': 0, 'hooks': {}}

    for hook in hooks(kind):
        if only and hook.name != only:
            continue
        if time.monotonic() >= overall_deadline:
            summary['hooks'][hook.name] = {'skipped': 'out of time'}
            summary['remaining'] = True
            continue

        slice_end = min(overall_deadline, time.monotonic() + hook.budget)
        ctx = Context(kind=kind, deadline=slice_end, triggered_by=triggered_by)
        at = time.monotonic()
        try:
            result = hook.func(ctx, **options) if _takes_options(hook.func) else hook.func(ctx)
            result = {} if result is None else dict(result)
            result['ms'] = int((time.monotonic() - at) * 1000)
            summary['items'] += int(result.get('items') or result.get('sent') or 0)
            summary['hooks'][hook.name] = result
        except Exception as exc:                                  # noqa: BLE001
            # One module failing is not the run failing: keep going, report it, and let the
            # endpoint answer 500 so the cron service's alerting notices.
            logger.exception('cron hook %s failed', hook.name)
            summary['ok'] = False
            summary['hooks'][hook.name] = {
                'error': f'{type(exc).__name__}: {exc}'[:300],
                'ms': int((time.monotonic() - at) * 1000),
            }

    summary['ms'] = int((time.monotonic() - started) * 1000)
    return summary


def _takes_options(func):
    """True if the hook accepts keyword arguments beyond ctx (e.g. `vacuum`)."""
    import inspect
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.kind in (p.KEYWORD_ONLY, p.VAR_KEYWORD) or
               (p.kind == p.POSITIONAL_OR_KEYWORD and p.default is not p.empty)
               for p in params)
