# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app\\styles.qss', 'app'),
        ('app\\license_public_key.pem', 'app'),
        (
            'app\\licence_generator\\encrypted_signing_key.json',
            'app\\licence_generator',
        ),
        (
            'app\\assets\\hira_model_school_icon.png',
            'app\\assets',
        ),
        (
            'app\\assets\\hira_model_school_icon.ico',
            'app\\assets',
        ),
        (
            'assets\\images\\hira_model_school_logo.png',
            'assets\\images',
        ),
        (
            'assets\\images\\hira_model_school_print_logo.png',
            'assets\\images',
        ),
    ],
    hiddenimports=[],
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
    [],
    exclude_binaries=True,
    name='HiraModelSchool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='app\\assets\\hira_model_school_icon.ico',
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HiraModelSchool',
)
