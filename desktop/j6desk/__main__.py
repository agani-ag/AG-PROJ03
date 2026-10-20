"""One executable, two faces.

    j6.exe                  the window
    j6.exe --cli            the terminal client, in the console you ran it from
    j6.exe --cli open Mani  straight into a conversation
    j6.exe --portable       ignore %APPDATA% for this run and use the folder beside the EXE
    j6.exe --version

Windows makes the two faces awkward: a windowed program has no console, and a console program
flashes a black box when double-clicked. So the build is **windowed**, and `--cli` borrows a
console — the one it was launched from if there is one, a new one if there is not. About fifteen
lines of ctypes and no dependency.
"""
import ctypes
import sys

from . import __version__
from .core import config

ATTACH_PARENT_PROCESS = -1


def attach_console():
    """Give a windowed process somewhere to print.

    `AttachConsole` succeeds when this was started from a shell, which is the nicer case: output
    lands in the window the person typed into. Failing that, `AllocConsole` opens one. Then the
    three standard streams are pointed at it, because a frozen windowed build has none.
    """
    if sys.platform != 'win32':
        return True
    kernel32 = ctypes.windll.kernel32
    if not kernel32.AttachConsole(ATTACH_PARENT_PROCESS):
        if not kernel32.AllocConsole():
            return False
    try:
        sys.stdin = open('CONIN$', 'r', encoding='utf-8', errors='replace')
        sys.stdout = open('CONOUT$', 'w', encoding='utf-8', errors='replace', buffering=1)
        sys.stderr = open('CONOUT$', 'w', encoding='utf-8', errors='replace', buffering=1)
    except OSError:
        return False
    return True


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if '--portable' in argv:
        # Keep everything beside the EXE for this run only — no marker file is written, so the
        # next launch goes back to %APPDATA% unless one is there.
        argv.remove('--portable')
        config.force_portable()

    if '--version' in argv:
        # A windowed build has no stdout, and printing to a stdout that is None is dropped on
        # the floor rather than failing — so borrow a console first, exactly as --cli does.
        attach_console()
        print(f'J6 {__version__}')
        return 0

    cfg = config.load()

    if '--cli' in argv:
        argv.remove('--cli')
        if not attach_console():
            return 1
        from . import ui_term
        try:
            return ui_term.run(argv, cfg)
        except KeyboardInterrupt:
            print()
            return 0

    from . import ui_web
    return ui_web.run(cfg)


if __name__ == '__main__':
    sys.exit(main())
