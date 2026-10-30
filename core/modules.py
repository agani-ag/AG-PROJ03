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
    url_name     the module's CONSOLE landing once it exists, else None
    user_url_name  where a signed-in person goes. Not the console: /apps/ sends
                 people here, and the console would only bounce them to an admin
                 sign-in they have no business seeing
    join_url_name  the page that starts this module for somebody who has not

A module can also have a link in the SyncUp app (System → Tools in SyncUp sends it):

    syncup_path         the page it opens, on this site — '' for no SyncUp link
    syncup_description  the line under its title in SyncUp
    syncup_icon         an icon from SyncUp's own list (its mobileapi/link_icons.py), not a Lucide name
    syncup_color        one of SyncUp's nine icon colours
    syncup_token        give the page `window.SyncUp.token` (it pushes notifications to the phone)
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    key: str
    title: str
    icon: str
    group: str
    description: str
    url_name: str | None = None          # the CONSOLE landing, for the sidebar and dashboard
    user_url_name: str | None = None     # where a person goes — the module itself
    join_url_name: str | None = None     # where somebody starts it for the first time
    syncup_path: str = ''
    syncup_description: str = ''
    syncup_icon: str = ''
    syncup_color: str = ''
    syncup_token: bool = False

    @property
    def is_live(self) -> bool:
        return bool(self.url_name)

    @property
    def has_syncup_link(self) -> bool:
        return bool(self.syncup_path)


MODULES = [
    Module(
        key='hibiscus',
        title='Hibiscus',
        icon='flower',                    # the module's own mark, the same one its pages use
        group='Modules',
        description='Cycle diary — day-by-day log, calendar and stocks. Was Red Hibiscus.',
        url_name='hibiscus_people',
        user_url_name='hibiscus_me_home',
        join_url_name='hibiscus_join',
        syncup_path='/hibiscus/',
        syncup_description='Cycle diary',
        syncup_icon='flower',
        syncup_color='rose',
        syncup_token=True,
    ),
    Module(
        key='j6',
        title='J6',
        icon='messages-square',           # no mark of its own: the name is the mark
        group='Modules',
        description='Chat with no sign-up — a name and two generated IDs.',
        url_name='j6_accounts',
        user_url_name='j6_inbox',
        join_url_name='j6_start_hub',
        syncup_path='/j6/',
        syncup_description='Chat with no sign-up',
        syncup_icon='chat',
        syncup_color='teal',
        syncup_token=True,
    ),
    Module(
        key='pocket',
        title='Pocket Money',
        icon='wallet',                    # money, without choosing green or red: those two
        group='Modules',                  #   already mean "in" and "out" on every screen
        description='Your own money in and out, and a shared ledger of who owes whom.',
        url_name='pocket_accounts',
        user_url_name='pocket_tracker',
        join_url_name='pocket_join',
        syncup_path='/pocket/',
        syncup_description='Money in and out, and who owes whom',
        syncup_icon='wallet',
        syncup_color='amber',
        syncup_token=True,                # pocket/user.js sends the token for its reminders
    ),
]

MODULES_BY_KEY = {m.key: m for m in MODULES}


def groups():
    """Modules grouped for the sidebar, keeping the order they are declared in."""
    out = {}
    for module in MODULES:
        out.setdefault(module.group, []).append(module)
    return out
