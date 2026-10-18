# -*- mode: python ; coding: utf-8 -*-
# 业务员版打包配置 —— 数据安全的关键：
#   excludes 中显式排除 hotel_sync.ai（AI 映射）与 hotel_sync.capture（流程录制），
#   业务员电脑上的安装包里【不存在】这两部分代码，日常工作零 AI 调用。
#
# 打包：pyinstaller packaging/operator.spec --clean --noconfirm
# 产物：dist/HotelSync-业务员版/HotelSync-业务员版.exe

block_cipher = None

a = Analysis(
    ['apps/operator_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('config/operator.example.yaml', 'config'),
        ('config/settings.yaml', 'config'),
    ],
    # pywinauto / playwright 是在函数体内延迟 import 的，静态分析抓不到，需显式声明
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
        'hotel_sync.ai',        # AI 字段映射（仅管理员录制时用）
        'hotel_sync.capture',   # 流程录制（仅管理员用）
        'apps.admin_app',
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
    name='HotelSync-业务员版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,               # GUI 应用，不弹控制台
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
    name='HotelSync-业务员版',
)
