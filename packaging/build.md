# 打包发布指南

两个发行包，用途与分发对象完全不同：

| 发行包 | 入口 | 包含能力 | 分发给 |
|---|---|---|---|
| 业务员版 | `apps/operator_app.py` | 导入模板、运行业务流程、待补录。**不含录制、不含 AI** | 前台电脑 + 公安系统电脑 |
| 管理员版 | `apps/admin_app.py` | 录制流程、AI 字段映射、导出模板、本机调试、自检 | 仅管理员本人 |

## 打包命令（在装有 Python + 依赖的 Windows 电脑上执行）

```bat
pip install -r requirements.txt pyinstaller
pyinstaller packaging/admin.spec --clean --noconfirm
pyinstaller packaging/operator.spec --clean --noconfirm
```

产物在 `dist/` 下：`HotelSync-业务员版\HotelSync-业务员版.exe`（双击即用）、
`HotelSync-管理员版\HotelSync-管理员版.exe`（命令行）。

## 分发与核验

1. 管理员电脑上录制流程 → `export` 生成 `.hstemplate` 模板文件
2. 把**业务员版文件夹 + 模板文件**发给业务电脑（内网拷贝/U盘，不要走公网）
3. 业务员双击 exe → 导入模板 → 选角色 → 启动运行
4. 核验业务员版确实不含 AI/录制能力：

```bat
rem 方法一：解包检查（_internal 下不应出现 hotel_sync\ai、hotel_sync\capture）
dir dist\HotelSync-业务员版\_internal\hotel_sync

rem 方法二：管理员版自检（源码层面）
HotelSync-管理员版.exe selftest
```

## 常见问题

- **exe 报缺 DLL**：把整个 `HotelSync-业务员版` 文件夹一起拷贝，不要只拷 exe
- **公安客户端操作不了**：确认那台电脑装的是业务员版且角色选了「公安机」
- **想给业务员版加功能**：不要直接改业务员版——在管理员版录好新流程、导出新模板，
  让业务员重新导入，能力边界不被破坏
