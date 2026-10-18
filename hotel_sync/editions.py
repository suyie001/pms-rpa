"""版本（发行包）能力边界 —— 数据安全的核心约束。

HotelSync 有两个发行包：

- ADMIN（管理员版）：包含全部能力——流程录制（capture）、AI 字段映射（ai）、
  模板导出、本机调试运行。
- OPERATOR（业务员版）：只运行业务流程。安装包中【物理上不包含】
  capture / ai 两个模块（见 packaging/operator.spec 的 excludes），
  业务员电脑上不存在任何 AI 相关代码，客户数据不会被送往任何大模型。

三层保证：
  1. 代码层：ai / capture 包在 import 时调用 require_admin()，
     非管理员版进程一导入就报错（本模块实现）。
  2. 运行时层：业务员版入口第一件事就是 set_edition(OPERATOR)，
     GUI 上实时显示“AI 模块未包含 ✓”。
  3. 打包层：PyInstaller 打包业务员版时显式排除这两个包，
     发行文件里根本没有这些代码（tests/test_editions.py 自检）。
"""
from __future__ import annotations

from enum import Enum


class Edition(str, Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


# 默认管理员版：源码树直接跑 demo / 管理员工具体验不变
_current: Edition = Edition.ADMIN


def set_edition(edition: str | Edition) -> Edition:
    global _current
    _current = Edition(edition)
    return _current


def current_edition() -> Edition:
    return _current


def is_admin() -> bool:
    return _current == Edition.ADMIN


def require_admin(feature: str) -> None:
    """非管理员版调用管理员能力时抛出 PermissionError。"""
    if not is_admin():
        raise PermissionError(
            f"「{feature}」仅管理员版可用（当前进程已声明为业务员版，"
            f"该能力未随包分发）"
        )
