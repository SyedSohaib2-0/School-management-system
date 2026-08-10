# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['owner_license_generator.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app\\styles.qss', 'app'),
        ('app\\license_public_key.pem', 'app'),
        (
            'app\\licence_generator\\encrypted_signing_key.json',
            'app\\licence_generator',
        ),
        ('app\\assets\\hira_model_school_icon.png', 'app\\assets'),
        ('app\\assets\\hira_model_school_icon.ico', 'app\\assets'),
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
    name='HiraModelSchoolLicenseGenerator',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=['app\\assets\\hira_model_school_icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HiraModelSchoolLicenseGenerator',
)
