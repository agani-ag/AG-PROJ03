# J6 for Windows

One portable `j6.exe` — about 15 MB, nothing to install — that is both a chat window and a
terminal client. It talks to the same server the browser does, over the same API, with the
Self-ID as its whole credential.

```
j6.exe                      the window
j6.exe --cli                the terminal client
j6.exe --cli open Mani      straight into a conversation
j6.exe --cli send Mani "running late"
j6.exe --cli whoami         both IDs, any time
j6.exe --portable           keep settings beside the EXE for this run
j6.exe --version
```

## Where things live

| | |
|---|---|
| Settings | `%APPDATA%\J6\config.json` — a key, a name, a window size. Never messages. |
| Truly portable | an empty file named `j6.portable` beside the EXE → `.\j6.config` instead |
| Chat history | on the server. The client holds none, which is why losing the config costs nothing but a paste of the Self-ID |

The EXE can live anywhere and be moved, deleted and downloaded again — it finds the same account
every time. Two people on one PC get two accounts and neither can read the other's.

## Layout

```
desktop/
  j6desk/
    core/      api.py · config.py · poller.py · notify.py   (no GUI import anywhere)
    ui_web.py  the WebView2 window — the web UI in a frame, plus tray and toasts
    ui_term.py the console client
    __main__.py  one entry point: --cli borrows or opens a console, otherwise the window opens
  run.py          what PyInstaller freezes
  j6.spec         the one-file, windowed build
  build.ps1       build, hash, release
  tests_desktop.py
```

`core/` imports nothing but the standard library, so the terminal client runs on a bare Python
and the frozen build carries one dependency fewer.

## Building

```powershell
cd desktop
.\build.ps1                     # → dist\j6.exe + dist\SHA256SUMS.txt
.\build.ps1 -Tag j6-v1.0.0      # …and creates the GitHub release
```

No CI. The EXE is never committed — release assets live outside git history, so the repository
stays the size it is however many builds you cut. The permanent download link is:

```
https://github.com/agani-ag/AG-PROJ03/releases/latest/download/j6.exe
```

Two things worth saying on any page that offers it: the build is **unsigned**, so SmartScreen
warns the first few times (More info → Run anyway) until it earns reputation, and the SHA-256
beside it lets anyone check they got what you built.

## Tests

```
cd desktop && python -m unittest tests_desktop
```

Nineteen tests covering the config file (where it goes, that a crash mid-save cannot truncate
it, that nothing about a conversation is kept) and the API client (the header, the URLs, and
what each kind of failure says). The window itself is not tested here: it needs a desktop
session and WebView2.

## How one file is also a terminal

The build is **windowed**, because a console build flashes a black box when double-clicked. With
`--cli` the program borrows the console it was launched from (`AttachConsole`) or opens one
(`AllocConsole`), then points the three standard streams at it — about fifteen lines of ctypes
and no dependency. The window's **Terminal** button launches the same EXE with `--cli` and
`CREATE_NEW_CONSOLE`, which is why there is one file rather than two.

One wrinkle: a windowed executable does not make the shell wait, so `j6.exe --cli` hands your
prompt straight back while the chat writes into the same window. Use the window's Terminal
button, or `start /wait j6.exe --cli`.
