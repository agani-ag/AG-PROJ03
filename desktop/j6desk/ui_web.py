"""The window: J6's web interface in a frame, with a tray icon and Windows toasts.

Deliberately not a second chat UI. Every fix made for the browser — bubbles, ticks, typing, the
media renderer — lands here the same day, because this *is* the browser page. What the frame
adds is the part a web page cannot do: a taskbar entry, a tray icon that survives closing the
window, and notifications while you are looking at something else.

First run is native, though: the app needs the Self-ID in its own config so the terminal client
and the toaster share the sign-in, so it creates the account itself and then navigates the view
to /j6/s/<self-id> — the one-tap link — so the embedded page ends up signed in on that same
account rather than a second one.
"""
import threading
import time

from . import __version__
from .core import config, notify, tray
from .core.api import Api, ApiError
from .core.poller import Poller

TITLE = 'J6'
DOWNLOAD = 'https://github.com/agani-ag/AG-PROJ03/releases/latest/download/j6.exe'


def _lazy_webview():
    try:
        import webview                     # pywebview, EdgeChromium (WebView2) on Windows
        return webview
    except ImportError:
        return None


class Desktop:
    """Glue: the window, the background poll and the toasts."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.api = Api(cfg.get('server', ''), cfg.get('self_id', ''))
        self.window = None
        self.poller = None
        self.unread = 0
        self.me = {}
        self.tray = None

    # ---- the JS bridge, reachable from the page as window.pywebview.api.*
    def save_account(self, self_id, name=''):
        """Called by the page if it ever creates an account inside the frame."""
        config.update(self_id=self_id, name=name)
        self.api.self_id = self_id
        return True

    def open_terminal(self):
        """The Terminal button: launch this same EXE with --cli in a console of its own."""
        import subprocess
        import sys
        flags = subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0
        subprocess.Popen([sys.executable, '--cli'], creationflags=flags, close_fds=True)
        return True

    # ---- notifications
    def _on_change(self, payload):
        total = sum(row.get('unread', 0) for row in payload.get('chats', []))
        if total > self.unread:
            newest = max((r for r in payload.get('chats', []) if r.get('unread')),
                         key=lambda r: r['last_at'], default=None)
            if newest and self.cfg.get('notify', True) and not self._focused():
                notify.toast(newest['name'], (newest.get('last') or 'New message')[:150])
        self.unread = total
        if self.tray:
            self.tray.set_unread(total)

    # ---- the tray's menu
    def _show_window(self):
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def _copy_account_id(self):
        tray.copy(self.me.get('account_id', ''))

    def _quit(self):
        if self.tray:
            self.tray.stop()
        try:
            self.window.destroy()
        except Exception:
            pass

    def check_version(self):
        """Tell somebody once when a newer build exists, rather than leaving them to look."""
        try:
            latest = self.api.version().get('version', '')
        except ApiError:
            return
        if latest and latest != __version__ and self.cfg.get('seen_version') != latest:
            config.update(seen_version=latest)
            notify.toast('J6 — a newer version is available',
                         f'You have {__version__}, the server expects {latest}. {DOWNLOAD}')

    def _focused(self):
        try:
            return bool(self.window and self.window.on_top is False and self.window.hidden is False
                        and getattr(self.window, 'focused', False))
        except Exception:
            return False

    # ---- running
    def start_poller(self):
        if not self.api.self_id:
            return
        self.poller = Poller(self.api, self._on_change)
        self.poller.idle = True              # the page does its own fast polling; this is for toasts
        self.poller.start()

    def start_tray(self):
        self.tray = tray.Tray(self._show_window, self.open_terminal, self._copy_account_id,
                              self._quit, name=self.cfg.get('name', ''))
        if not self.tray.start():
            self.tray = None                 # this build has no pystray; the window still works

    def run(self):
        webview = _lazy_webview()
        if webview is None:
            print('This build has no window: install pywebview, or use --cli.')
            return 2

        size = self.cfg.get('window') or {}
        url = self.cfg['server'].rstrip('/') + '/j6/s/' + self.api.self_id
        self.window = webview.create_window(
            TITLE, url, js_api=self, width=size.get('w', 1040), height=size.get('h', 740),
            min_size=(420, 560), text_select=True)
        try:
            self.me = self.api.hello()
        except ApiError:
            self.me = {}
        self.start_poller()
        self.start_tray()
        threading.Thread(target=self.check_version, daemon=True).start()
        try:
            webview.start()
        finally:
            if self.poller:
                self.poller.stop()
            if self.tray:
                self.tray.stop()
            try:
                config.update(window={'w': int(self.window.width), 'h': int(self.window.height)})
            except Exception:
                pass
        return 0


def first_run(cfg):
    """Make an account before the window opens, so the config has the key the toaster needs.

    Returns the updated config, or None if the person closed the dialog.
    """
    webview = _lazy_webview()
    server = cfg.get('server', '')
    if webview is None:
        return None

    answer = webview.create_window('J6', html=_FIRST_RUN_HTML.replace('{{server}}', server),
                                   width=460, height=520)
    state = {}

    def bridge(window):
        while not state.get('done'):
            time.sleep(0.2)

    # The first-run page posts back through a tiny JS API rather than a form.
    class Bridge:
        def submit(self, server_url, name, self_id):
            api = Api(server_url)
            try:
                if self_id.strip():
                    api.self_id = self_id.strip()
                    me = api.hello()
                    config.update(server=server_url, self_id=api.self_id, name=me['name'])
                else:
                    made = api.start(name.strip())
                    config.update(server=server_url, self_id=made['self_id'], name=made['name'])
                state['done'] = True
                answer.destroy()
                return {'ok': True}
            except ApiError as exc:
                return {'ok': False, 'error': str(exc)}

    answer._js_api = Bridge()
    webview.start(bridge, answer)
    return config.load() if state.get('done') else None


_FIRST_RUN_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
 body{font:15px -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:28px 24px;
      background:#eef2f4;color:#0f172a;text-align:center}
 .w{font-size:44px;font-weight:800;letter-spacing:-.04em}
 .s{color:#64748b;margin-top:2px}
 label{display:block;text-align:left;font-weight:600;margin:18px 0 6px;font-size:13px}
 input{width:100%;padding:11px 13px;border:1px solid #cbd5e1;border-radius:11px;font:inherit}
 button{width:100%;margin-top:16px;padding:12px;border:0;border-radius:12px;background:#0d9488;
        color:#fff;font:650 15px inherit;cursor:pointer}
 .t{margin-top:12px;color:#94a3b8;font-size:12px;text-align:left}
 .e{color:#dc2626;font-size:13px;margin-top:8px;min-height:18px}
</style></head><body>
 <div class="w">J6</div><div class="s">Chat without signing up.</div>
 <label>Server</label><input id="server" value="{{server}}" placeholder="https://…">
 <label>What should people call you?</label>
 <input id="name" placeholder="Your name" maxlength="24">
 <div class="t">Two letters is enough. Already have a Self-ID? Paste it below instead.</div>
 <label>Self-ID <span style="font-weight:400;color:#94a3b8">optional</span></label>
 <input id="self" placeholder="j6s-…">
 <div class="e" id="err"></div>
 <button onclick="go()">Start chatting</button>
<script>
function go(){
  var s=document.getElementById('server').value.trim();
  var n=document.getElementById('name').value.trim();
  var k=document.getElementById('self').value.trim();
  if(!s){document.getElementById('err').textContent='Which server?';return;}
  if(!k&&n.length<2){document.getElementById('err').textContent='A name needs two letters.';return;}
  window.pywebview.api.submit(s,n,k).then(function(r){
    if(!r.ok){document.getElementById('err').textContent=r.error;}
  });
}
</script></body></html>"""


def run(cfg):
    if not cfg.get('server') or not cfg.get('self_id'):
        updated = first_run(cfg)
        if not updated or not updated.get('self_id'):
            return 1
        cfg = updated
    return Desktop(cfg).run()
