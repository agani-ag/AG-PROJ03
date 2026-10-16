"""The tray icon, so closing the window does not mean signing out.

Optional by design: if `pystray` is not in the build, everything else still works and the window
simply behaves like an ordinary window. That is why every call here is guarded — a missing tray
is a smaller problem than a client that refuses to start.

The icon is drawn in code rather than shipped as a file: J6 has no artwork of its own, so it is
the name on a black square, which is also what the window and the lock screen show.
"""
import subprocess
import sys
import threading

try:                                   # both are optional; pystray needs Pillow for the image
    import pystray
    from PIL import Image, ImageDraw
except ImportError:                    # pragma: no cover - depends on what the build included
    pystray = None
    Image = ImageDraw = None


def available():
    return pystray is not None


def copy(text):
    """Put something on the clipboard without a dependency — Windows has `clip`."""
    if sys.platform != 'win32':
        return False
    try:
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        subprocess.run('clip', input=str(text).encode('utf-16le'), check=True,
                       creationflags=flags)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _image(unread=0):
    """A black tile with J6 on it, and a dot when something is waiting."""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=14, fill=(0, 0, 0, 255))
    draw.text((14, 20), 'J6', fill=(255, 255, 255, 255))
    if unread:
        draw.ellipse([size - 22, 2, size - 2, 22], fill=(13, 148, 136, 255))
    return img


class Tray:
    """A tray icon with an unread count. Every method is safe to call when there is none."""

    def __init__(self, on_open, on_terminal, on_copy_id, on_quit, name=''):
        self.icon = None
        self.unread = 0
        self.name = name
        self._handlers = {'open': on_open, 'terminal': on_terminal,
                          'copy': on_copy_id, 'quit': on_quit}

    @property
    def title(self):
        return self.icon.title if self.icon else ''

    @title.setter
    def title(self, value):
        if self.icon:
            self.icon.title = value

    def set_unread(self, count):
        if not self.icon or count == self.unread:
            return
        self.unread = count
        self.icon.title = f'J6 ({count})' if count else 'J6'
        self.icon.icon = _image(count)

    def start(self):
        if not available():
            return False
        menu = pystray.Menu(
            pystray.MenuItem('Open J6', lambda *_: self._handlers['open'](), default=True),
            pystray.MenuItem('Open terminal', lambda *_: self._handlers['terminal']()),
            pystray.MenuItem('Copy my Account ID', lambda *_: self._handlers['copy']()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Quit', lambda *_: self._handlers['quit']()),
        )
        self.icon = pystray.Icon('j6', _image(), 'J6', menu)
        threading.Thread(target=self.icon.run, daemon=True).start()
        return True

    def stop(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
