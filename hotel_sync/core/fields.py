"""字段解析：把 recipe 里的字段名解析成实际值。

支持的写法：
    room_no            → action.room_no
    action.old_room_no → action.old_room_no（换房原房号）
    action.new_check_out → action.new_check_out（续住新退房时间）
    guest.name         → 当前同住人的姓名（guest_loop 内部使用）
    name               → 优先取 action 上的属性，否则取当前 guest 的属性
"""
from __future__ import annotations

from typing import Any, Optional


def resolve_field(field: str, action: Any, guest: Optional[Any]) -> Optional[str]:
    if field.startswith("action."):
        return getattr(action, field.split(".", 1)[1], None)
    if field.startswith("guest."):
        return getattr(guest, field.split(".", 1)[1], None) if guest is not None else None
    if hasattr(action, field):
        return getattr(action, field)
    if guest is not None and hasattr(guest, field):
        return getattr(guest, field)
    return None
