"""任务客户端：本地直连（单机演示）或 HTTP 跨机（两台电脑部署）。

两台电脑部署时：
    - 公安那台电脑跑 coordinator（http://<公安机IP>:8765）
    - PMS 那台电脑的 agent 用 HttpClient 连上来
    - 公安机自己的 agent 用 LocalClient 直连本地队列
"""
from __future__ import annotations

from typing import Iterable, Optional

import requests

from .models import ActionResult, StayAction
from .queue import TaskQueue


class LocalClient:
    """单机/演示用：直接操作本地 SQLite 队列。"""

    def __init__(self, queue: TaskQueue):
        self.queue = queue

    def submit(self, action: StayAction, targets: Iterable[str]) -> list[str]:
        return self.queue.enqueue_action(action, targets)

    def next_task(self, target: str) -> Optional[dict]:
        return self.queue.next_task(target)

    def finish(self, task_id: str, result: ActionResult) -> None:
        self.queue.finish(task_id, result)

    def pending(self) -> list[dict]:
        return self.queue.pending_manual()

    def retry(self, task_id: str) -> bool:
        return self.queue.retry(task_id)

    def action_summary(self, action_id: str) -> dict:
        return self.queue.action_summary(action_id)


class HttpClient:
    """跨机部署用：通过 HTTP 访问 coordinator。"""

    def __init__(self, base_url: str, timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def submit(self, action: StayAction, targets: Iterable[str]) -> list[str]:
        r = requests.post(
            f"{self.base_url}/actions",
            json={"action": action.to_dict(), "targets": list(targets)},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json().get("tasks", [])

    def next_task(self, target: str) -> Optional[dict]:
        r = requests.get(
            f"{self.base_url}/tasks/next",
            params={"target": target},
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json().get("task")

    def finish(self, task_id: str, result: ActionResult) -> None:
        r = requests.post(
            f"{self.base_url}/tasks/{task_id}/result",
            json=result.to_dict(),
            timeout=self.timeout,
        )
        r.raise_for_status()

    def pending(self) -> list[dict]:
        r = requests.get(f"{self.base_url}/pending", timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("tasks", [])

    def retry(self, task_id: str) -> bool:
        r = requests.post(f"{self.base_url}/tasks/{task_id}/retry", timeout=self.timeout)
        r.raise_for_status()
        return bool(r.json().get("ok"))

    def action_summary(self, action_id: str) -> dict:
        r = requests.get(f"{self.base_url}/actions/{action_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()
