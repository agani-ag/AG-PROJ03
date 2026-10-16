"""J6 in a console — CMD, PowerShell or Windows Terminal.

A live tail you can type into: the poll runs on its own thread and prints arriving messages
*above* whatever is half-typed, rather than interrupting it. Media stays a URL, because a
console is the one place where "it is a link" is the right answer.

    j6.exe --cli                     the chat list, then pick one
    j6.exe --cli open Mani           straight into a conversation
    j6.exe --cli send Mani "late"    one line, for scripts
    j6.exe --cli whoami              both IDs, any time
"""
import sys
import threading
import time

from . import __version__
from .core import config, notify
from .core.api import Api, ApiError
from .core.poller import Poller

ESC = '\x1b['
DIM, CYAN, GREEN, YELLOW, RESET = ESC + '90m', ESC + '36m', ESC + '32m', ESC + '33m', ESC + '0m'


def _colour(enabled=True):
    """Windows Terminal and modern consoles do ANSI; older ones get plain text."""
    if not enabled or not sys.stdout.isatty():
        return {k: '' for k in ('dim', 'cyan', 'green', 'yellow', 'reset')}
    return {'dim': DIM, 'cyan': CYAN, 'green': GREEN, 'yellow': YELLOW, 'reset': RESET}


C = _colour()


def say(*parts):
    print(*parts)
    sys.stdout.flush()


def ask(prompt):
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ''


def clock(ms):
    return time.strftime('%H:%M', time.localtime(ms / 1000)) if ms else ''


# --------------------------------------------------------------------------- #
# Getting in — the terminal can make an account, not only sign one in
# --------------------------------------------------------------------------- #
def ensure_account(cfg, server):
    api = Api(server, cfg.get('self_id'))
    if cfg.get('self_id'):
        try:
            me = api.hello()
            return api, me
        except ApiError as exc:
            say(f'{C["yellow"]}{exc}{C["reset"]}')
            cfg['self_id'] = ''

    say(f'{C["dim"]}No account on this machine.{C["reset"]}')
    existing = ask('Paste a Self-ID, or press Enter to make a new account: ').strip()
    if existing:
        api = Api(server, existing)
        me = api.hello()                       # raises ApiError if it is not real
        config.update(server=server, self_id=existing, name=me['name'])
        say(f'{C["green"]}Signed in as {me["name"]}.{C["reset"]}')
        return api, me

    name = ''
    while len(name.strip()) < 2:
        name = ask('What should people call you? ')
    made = api.start(name.strip())
    config.update(server=server, self_id=made['self_id'], name=made['name'])
    say('')
    say(f'{C["green"]}Account created.{C["reset"]}')
    say(f'  Account ID  {C["cyan"]}{made["account_id"]}{C["reset"]}   {C["dim"]}share this{C["reset"]}')
    say(f'  Self-ID     {C["yellow"]}{made["self_id"]}{C["reset"]}   {C["dim"]}keep this{C["reset"]}')
    say('')
    say(f'{C["dim"]}Saved to {config.path()} — read it again any time with{C["reset"]}')
    say(f'{C["dim"]}  j6 --cli whoami{C["reset"]}')
    say('')
    return api, api.hello()


# --------------------------------------------------------------------------- #
# Screens
# --------------------------------------------------------------------------- #
def print_list(chats):
    if not chats:
        say(f'  {C["dim"]}No chats yet. Give someone your Account ID, or:{C["reset"]}')
        say(f'  {C["dim"]}  j6 --cli new j6-…{C["reset"]}')
        return
    for row in chats:
        unread = f'{C["yellow"]}{row["unread"]:>2}{C["reset"]}' if row['unread'] else '  '
        last = (('You: ' if row['last_mine'] else '') + (row['last'] or ''))[:48]
        say(f'  {C["cyan"]}{row["name"][:14]:<14}{C["reset"]} {unread}  '
            f'{clock(row["last_at"]):>5}  {last}')


def find_chat(chats, needle):
    needle = (needle or '').strip().lower()
    for row in chats:
        if row['name'].lower() == needle or str(row['id']) == needle:
            return row
    for row in chats:
        if needle and needle in row['name'].lower():
            return row
    return None


def open_chat(api, row, notify_on=True):
    """The live view: poll in the background, read from the keyboard in the foreground."""
    say('')
    say(f'{C["dim"]}──  {row["name"]}  ·  {"Active now" if row["online"] else "away"}  '
        f'{"─" * 40}{C["reset"]}')

    seen = {'typing': False}
    lock = threading.Lock()

    def on_change(payload):
        thread = payload.get('thread') or {}
        with lock:
            for m in thread.get('messages', []):
                who = f'{C["green"]}you{C["reset"]}  ' if m['mine'] else \
                      f'{C["cyan"]}{m["sender"][:10]:<10}{C["reset"]}'
                tick = f' {C["dim"]}✓✓{C["reset"]}' if m['mine'] and m['delivered'] else ''
                say(f'\r  {clock(m["at"])}  {who} {m["body"]}{tick}')
                if not m['mine'] and notify_on:
                    notify.toast(m['sender'], m['body'][:120])
            if thread.get('typing') != seen['typing']:
                seen['typing'] = thread.get('typing')
                if seen['typing']:
                    say(f'  {C["yellow"]}{row["name"]} is typing…{C["reset"]}')
            if thread.get('messages'):
                sys.stdout.write('> ')
                sys.stdout.flush()

    poller = Poller(api, on_change, chat=row['id'])
    poller.start()
    say(f'{C["dim"]}Type a message and press Enter. /back for the list, /quit to leave.{C["reset"]}')
    try:
        while True:
            line = ask('> ')
            if line.strip() in ('/back', '/b'):
                return 'back'
            if line.strip() in ('/quit', '/q', 'exit'):
                return 'quit'
            if not line.strip():
                continue
            try:
                api.send(row['id'], line.strip())
                api.read(row['id'])
            except ApiError as exc:
                say(f'  {C["yellow"]}{exc}{C["reset"]}')
    finally:
        poller.stop()


# --------------------------------------------------------------------------- #
def run(args, cfg):
    """Entry point from __main__. `args` is whatever followed --cli."""
    server = cfg.get('server') or ''
    if not server:
        server = ask('Which J6 server? (e.g. https://synchub.example.com) ').strip()
        if not server:
            say('Nothing to connect to.')
            return 2
        cfg = config.update(server=server)

    try:
        api, me = ensure_account(cfg, server)
    except ApiError as exc:
        say(f'{C["yellow"]}{exc}{C["reset"]}')
        return 1

    command = (args[0] if args else '').lower()
    rest = args[1:]

    if command == 'whoami':
        say(f'  Name        {me["name"]}')
        say(f'  Account ID  {C["cyan"]}{me["account_id"]}{C["reset"]}')
        say(f'  Self-ID     {C["yellow"]}{config.load()["self_id"]}{C["reset"]}')
        say(f'  {C["dim"]}config: {config.path()}{C["reset"]}')
        return 0

    if command == 'new' and rest:
        try:
            made = api.new_chat(rest[0])
            say(f'{C["green"]}Chat with {made["name"]} is open.{C["reset"]}')
        except ApiError as exc:
            say(f'{C["yellow"]}{exc}{C["reset"]}')
            return 1
        me = api.hello()
        command = 'open'
        rest = [made['name']]

    if command == 'send' and len(rest) >= 2:
        row = find_chat(me['chats'], rest[0])
        if not row:
            say(f'No chat with "{rest[0]}".')
            return 1
        try:
            api.send(row['id'], ' '.join(rest[1:]))
            say(f'{C["green"]}Sent to {row["name"]}.{C["reset"]}')
            return 0
        except ApiError as exc:
            say(f'{C["yellow"]}{exc}{C["reset"]}')
            return 1

    say(f'{C["green"]}J6 {__version__}{C["reset"]}  signed in as {me["name"]}  '
        f'{C["dim"]}(config: {config.path()}){C["reset"]}')
    try:
        latest = api.version().get('version', '')
        if latest and latest != __version__:
            say(f'{C["yellow"]}A newer build exists ({latest}).{C["reset"]} '
                f'{C["dim"]}https://github.com/agani-ag/AG-PROJ03/releases/latest{C["reset"]}')
    except ApiError:
        pass
    say('')

    while True:
        me = api.hello()
        if command == 'open' and rest:
            row = find_chat(me['chats'], rest[0])
            rest = []
            command = ''
            if not row:
                say(f'No chat with that name.')
                continue
        else:
            print_list(me['chats'])
            say('')
            pick = ask('Open which chat? (name, /new <id>, /quit) ').strip()
            if pick in ('/quit', '/q', 'exit', ''):
                return 0
            if pick.startswith('/new '):
                try:
                    api.new_chat(pick.split(' ', 1)[1].strip())
                except ApiError as exc:
                    say(f'  {C["yellow"]}{exc}{C["reset"]}')
                continue
            row = find_chat(me['chats'], pick)
            if not row:
                say('  No chat with that name.')
                continue
        if open_chat(api, row, cfg.get('notify', True)) == 'quit':
            return 0
        say('')
