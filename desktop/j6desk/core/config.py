"""Where J6 keeps its settings on a Windows machine.

In the **user's** folder, not beside the executable, because the executable moves and the person
does not: `%APPDATA%\\J6\\config.json`. Run j6.exe from Downloads, from the Desktop, from a stick,
delete it and fetch it again next month — it finds the same account every time, and the window
and the terminal share one sign-in.

An empty file named `j6.portable` beside the executable switches to `.\\j6.config` instead, for
the day you want a stick that leaves nothing behind on a borrowed machine.

Nothing here is a cache of the chat: a key, a name and a window size. Messages live on the
server, which is why losing this file costs nothing but a paste of the Self-ID.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

APP_DIR_NAME = 'J6'
FILE_NAME = 'config.json'
PORTABLE_MARKER = 'j6.portable'
PORTABLE_FILE = 'j6.config'

DEFAULTS = {
    'server': '',
    'self_id': '',
    'name': '',
    'notify': True,
    'window': {'w': 1040, 'h': 740},
    'seen_version': '',
}


def exe_dir():
    """The folder the running program sits in — the EXE's own, not Python's."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


_forced_portable = False


def force_portable(on=True):
    """`--portable` for one run, without leaving a marker file behind."""
    global _forced_portable
    _forced_portable = bool(on)


def is_portable():
    return _forced_portable or (exe_dir() / PORTABLE_MARKER).exists()


def path():
    if is_portable():
        return exe_dir() / PORTABLE_FILE
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return Path(base) / APP_DIR_NAME / FILE_NAME


def load():
    """Whatever is on disk, merged over the defaults. A broken file is not fatal."""
    data = dict(DEFAULTS)
    try:
        with open(path(), 'r', encoding='utf-8') as handle:
            found = json.load(handle)
        if isinstance(found, dict):
            data.update(found)
    except (OSError, ValueError):
        pass
    return data


def save(data):
    """Written atomically: a crash mid-save must not leave a truncated file.

    The Self-ID is the only copy of the key on this machine. Losing it is survivable — paste it
    again — but it should never happen because the power went out during a write.
    """
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', delete=False,
                                         dir=str(target.parent), suffix='.tmp')
    try:
        json.dump(data, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, target)
    return target


def update(**fields):
    data = load()
    data.update(fields)
    save(data)
    return data


def forget():
    """Sign out: the file goes. The account itself is untouched on the server."""
    try:
        os.remove(path())
        return True
    except OSError:
        return False
