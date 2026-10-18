# -*- mode: python ; coding: utf-8 -*-
# 管理员版打包配置 —— 全能力：录制（capture）+ AI 映射（ai）+ 模板导出 + 调试运行。
# 只分发给管理员本人，不要装到业务电脑上。
#
# 打包：pyinstaller packaging/admin.spec --clean --noconfirm
# 产物：dist/HotelSync-管理员版/HotelSync-管理员版.exe（命令行工具）

block_cipher = None

a = Analysis(
    ['apps/admin_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config', 'config'),
    ],
    hiddenimports=[
        'pywinauto.application',
        'pywinauto.findwindows',
        'comtypes',
        'comtypes.client',
        'playwright.sync_api',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tests',
        'pytest',
        'IPython',
        'matplotlib',
        'pandas',
        'numpy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='HotelSync-管理员版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,                # CLI 工具，保留控制台
    disable_directory_creation=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HotelSync-管理员版',
)
