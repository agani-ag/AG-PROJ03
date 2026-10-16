"""The background poll, shared by the window and the terminal.

Same cadence as the browser — 3s on an open chat, 8s on the list, 20s when nobody is looking —
because it is the same server with the same small budget. One thread, one callback, and it stops
when the program does.
"""
import threading
import time

FOCUSED = 3.0
LIST = 8.0
IDLE = 20.0


class Poller(threading.Thread):
    def __init__(self, api, on_change, chat=None, since=0):
        super().__init__(daemon=True)
        self.api = api
        self.on_change = on_change
        self.chat = chat
        self.since = since
        self.idle = False
        self._stop = threading.Event()
        self._wake = threading.Event()

    def open_chat(self, chat, since=0):
        """Switch which conversation is being watched, and ask again immediately."""
        self.chat, self.since = chat, since
        self._wake.set()

    def wake(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def _cadence(self):
        if self.idle:
            return IDLE
        return FOCUSED if self.chat else LIST

    def run(self):
        while not self._stop.is_set():
            try:
                payload = self.api.poll(self.chat, self.since)
                thread = payload.get('thread') or {}
                for message in thread.get('messages', []):
                    self.since = max(self.since, message['seq'])
                self.on_change(payload)
            except Exception:
                # A dropped network is normal; the next poll asks for everything since `since`.
                pass
            self._wake.wait(self._cadence())
            self._wake.clear()
