"""Talking to J6, with nothing but the standard library.

`urllib` rather than `requests` on purpose: the terminal client then runs on a bare Python with
no install step at all, and the frozen EXE carries one dependency fewer. The Self-ID travels in
`X-J6-Self` — no cookies, no CSRF, nothing to pretend about.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 15


class ApiError(Exception):
    """Something the person should be told: a refusal, a cap, a server that is not there."""


class Api:
    def __init__(self, server, self_id=''):
        self.server = (server or '').rstrip('/')
        self.self_id = self_id or ''

    # ---- plumbing
    def _url(self, path, params=None):
        url = f'{self.server}/j6/api/{path}'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        return url

    def _call(self, path, data=None, params=None, method=None, auth=True):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self._url(path, params), data=body,
                                         method=method or ('POST' if body is not None else 'GET'))
        request.add_header('Content-Type', 'application/json')
        if auth and self.self_id:
            request.add_header('X-J6-Self', self.self_id)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read()
            return json.loads(raw or b'{}')
        except urllib.error.HTTPError as exc:
            detail = ''
            try:
                detail = json.loads(exc.read() or b'{}').get('error', '')
            except ValueError:
                pass
            if exc.code == 401:
                raise ApiError('That Self-ID is not recognised, or the account is suspended.')
            raise ApiError(detail or f'The server said {exc.code}.')
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(f'Cannot reach {self.server} — {getattr(exc, "reason", exc)}')
        except ValueError:
            raise ApiError('The server answered something that was not JSON. Wrong address?')

    # ---- the four calls the clients make
    def start(self, name):
        """Create an account. The first run does this instead of asking for a Self-ID."""
        answer = self._call('start', {'name': name}, auth=False)
        self.self_id = answer['self_id']
        return answer

    def hello(self):
        return self._call('hello', {})

    def poll(self, chat=None, since=0):
        params = {}
        if chat:
            params.update({'chat': chat, 'since': since})
        return self._call('poll', params=params)

    def send(self, chat, body):
        return self._call('send', {'chat': chat, 'body': body})

    def new_chat(self, account_id):
        return self._call('new', {'account_id': account_id})

    def read(self, chat, seq=None):
        return self._call('read', {'chat': chat, 'seq': seq})

    def version(self):
        return self._call('version', auth=False)
