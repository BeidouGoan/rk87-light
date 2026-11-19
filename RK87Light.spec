# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['rk87light.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/ui-bg.png', 'assets'),
        ('assets/rk87-light.png', 'assets'),
        ('assets/rk87-light-24.png', 'assets'),
        ('assets/rk87-light.ico', 'assets'),
    ],
    hiddenimports=['hid', 'pystray._win32'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RK87Light',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/rk87-light.ico'],
)
