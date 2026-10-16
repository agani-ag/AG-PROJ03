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
    excludes=['tkinter', 'unittest', 'pydoc', 'test', 'distutils', 'email', 'html', 'http',
              'xmlrpc', 'pdb'],
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
