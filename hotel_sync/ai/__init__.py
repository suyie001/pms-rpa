"""AI 字段映射包 —— 仅管理员版（录制流程时使用）。

import 本包即要求管理员版身份；业务员版发行包不包含本包。
"""
from ..editions import require_admin

require_admin("AI 字段映射")
