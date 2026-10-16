"""The list of feature modules.

This is the one place that knows what modules exist. The sidebar and the dashboard both read it,
so adding a module means adding an entry here (plus its Django app and its include() in
main/urls.py).

Naming: a module inside SyncHub keeps a plain name (Chat, Health tracker) and is shown as
"SyncHub · Chat". The family's `<Topic>Sync` form (GSTSync, AudioSync) is for an app that ships
on its own, so rename a module only if it leaves the hub.

A module is "live" once `url_name` is set: the sidebar and its dashboard card then link straight
to it. Until then both link to the placeholder page and show a "Not built yet" badge.

    key          short id, used in the placeholder URL /m/<key>/
    title        what the sidebar and card show
    icon         a Lucide name from static/icons.svg
    group        sidebar heading the link sits under
    description  one line on the dashboard card
    url_name     the module's landing URL name once it exists, else None
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    key: str
    title: str
    icon: str
    group: str
    description: str
    url_name: str | None = None

    @property
    def is_live(self) -> bool:
        return bool(self.url_name)


MODULES = [
    Module(
        key='hibiscus',
        title='Hibiscus',
        icon='flower',                    # the module's own mark, the same one its pages use
        group='Modules',
        description='Cycle diary — day-by-day log, calendar and stocks. Was Red Hibiscus.',
        url_name='hibiscus_people',
    ),
    Module(
        key='j6',
        title='J6',
        icon='messages-square',           # no mark of its own: the name is the mark
        group='Modules',
        description='Chat with no sign-up — a name and two generated IDs.',
        url_name='j6_accounts',
    ),
]

MODULES_BY_KEY = {m.key: m for m in MODULES}


def groups():
    """Modules grouped for the sidebar, keeping the order they are declared in."""
    out = {}
    for module in MODULES:
        out.setdefault(module.group, []).append(module)
    return out
