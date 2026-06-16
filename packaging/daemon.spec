# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the AI-kms Daemon.

Entry point: ``daemon.app:main`` (the App Supervisor, NOT the CLI).

Hidden imports: required because PyInstaller static analysis cannot discover
dynamically-loaded backends for tray, keyring, watchdog, and extractor libs.

# COUPLING: the eight-lib hidden-import list is coupled to the handler set in
# ``src/handlers/__init__.py``. If a new file-format handler or extractor lib
# is added later, this list must grow.  Keep in sync with that file.
"""

import os
BASEDIR = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(BASEDIR, 'src', 'daemon', 'app.py')],
    pathex=[BASEDIR],
    binaries=[],
    datas=[],
    hiddenimports=[
        # tray backends — loaded dynamically by pystray
        'pystray._darwin',
        'pystray._win32',
        # keyring backends — loaded dynamically by keyring
        'keyring.backends.macOS',
        'keyring.backends.Windows',
        # watchdog backends — selected at runtime per platform
        'watchdog.observers.fsevents',
        'watchdog.observers.read_directory_changes',
        # COUPLING: eight extractor libs — keep in sync with handlers/__init__.py
        # (pypdf, docx, pptx, openpyxl, extract_msg,
        #  bs4, requests, youtube-transcript-api — NOTE: use Python module names,
        #  NOT PyPI distribution names)
        'pypdf',
        'docx',
        'pptx',
        'openpyxl',
        'extract_msg',
        'bs4',
        'requests',
        'youtube-transcript-api',
        # PIL — used by pystray for tray icon rendering
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='ai-kms-daemon',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No terminal window on macOS / Windows
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS .app bundle
app = BUNDLE(
    exe,
    name='AI-kms Daemon.app',
    icon=None,  # No custom icon for now
    bundle_identifier='com.kms.daemon',
)
