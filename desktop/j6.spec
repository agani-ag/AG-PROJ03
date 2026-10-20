# PyInstaller spec for j6.exe — one file, windowed, no console flash.
#   cd desktop && pyinstaller --clean j6.spec      →  dist\j6.exe
#
# Windowed on purpose: `--cli` borrows or opens a console itself (see j6desk/__main__.py), so one
# binary is both the window and the terminal client.
block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['webview.platforms.edgechromium', 'pystray._win32', 'PIL.Image',
                   'PIL.ImageDraw'],
    hookspath=[],
    runtime_hooks=[],
    # Trimming the standard library is worth a few MB, but only where nothing reaches for it.
    # `email`, `html` and `http` stay because urllib.request imports all three and urllib is the
    # client's entire transport; `distutils`, `pydoc` and `unittest` stay because setuptools and
    # PyInstaller's own graph pull them in and excluding them fails the build outright. tkinter
    # is the one that actually pays: several MB for a toolkit this client never opens.
    excludes=['tkinter', 'test', 'xmlrpc'],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='j6',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,              # windowed: --cli attaches or allocates its own console
    icon=None,                  # J6 has no mark of its own; the window carries the name
)
