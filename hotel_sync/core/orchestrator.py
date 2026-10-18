"""编排器：把一次前厅操作分发给 PMS + 公安两个目标系统。"""
from __future__ import annotations

from typing import Iterable

from .client import LocalClient
from .models import StayAction

DEFAULT_TARGETS = ("pms", "psb")


class Orchestrator:
    def __init__(self, client: LocalClient, targets: Iterable[str] = DEFAULT_TARGETS):
        self.client = client
        self.targets = list(targets)

    def submit(self, action: StayAction) -> dict:
        """提交动作。返回任务列表；duplicated=True 表示该动作已提交过（幂等命中）。"""
        task_ids = self.client.submit(action, self.targets)
        return {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "label": action.label(),
            "room_no": action.room_no,
            "guests": len(action.guests),
            "tasks": task_ids,
            "duplicated": len(task_ids) == 0,
        }

    def action_summary(self, action_id: str) -> dict:
        return self.client.action_summary(action_id)
