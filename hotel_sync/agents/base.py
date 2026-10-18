"""Agent 基类：轮询任务 → 执行 → 回写结果。"""
from __future__ import annotations

import time
import traceback
from typing import Optional

from ..core.fields import resolve_field  # noqa: F401  （子类复用）
from ..core.models import ActionResult, StayAction, now_iso


class BaseAgent:
    """每个目标系统（pms / psb）对应一个 Agent。

    - simulate=True：不碰真实软件，只按 recipe 记录“填了什么”，用于开发和联调
    - simulate=False：驱动真实系统（浏览器 / Windows 客户端）
    """

    def __init__(
        self,
        target: str,
        client,
        simulate: bool = True,
        name: str = "",
        on_log=None,
    ):
        self.target = target
        self.client = client
        self.simulate = simulate
        self.name = name or f"{target}-agent"
        self.on_log = on_log  # 可选日志回调（GUI 用）
        self.log_lines: list[str] = []

    # ------------------------------------------------------------ 日志

    def log(self, msg: str) -> None:
        line = f"[{now_iso()}] [{self.name}] {msg}"
        self.log_lines.append(line)
        print(line, flush=True)
        if self.on_log:
            try:
                self.on_log(line)
            except Exception:
                pass

    # ------------------------------------------------------------ 主循环

    def poll_once(self) -> Optional[ActionResult]:
        """领取并执行一个任务。没有任务时返回 None。"""
        task = self.client.next_task(self.target)
        if not task:
            return None
        action = StayAction.from_dict(task["payload"])
        self.log(
            f"领取任务 {task['task_id']}｜{action.label()}｜房间 {action.room_no}"
            f"｜客人 {len(action.guests)} 人"
        )
        try:
            result = self.execute(task)
        except Exception as exc:  # 兜底：任何异常都不能让 agent 挂掉
            traceback.print_exc()
            result = ActionResult(target=self.target, ok=False, message=f"执行异常: {exc}")
        self.client.finish(task["task_id"], result)
        self.log(
            f"任务 {task['task_id']} → {'成功' if result.ok else '失败：' + result.message}"
        )
        return result

    def run_forever(self, interval: float = 2.0) -> None:
        self.log("启动，开始轮询任务（Ctrl+C 退出）…")
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                self.log("收到退出信号，已停止")
                return
            except Exception as exc:
                self.log(f"轮询异常（已忽略，继续）: {exc}")
            time.sleep(interval)

    # ------------------------------------------------------------ 子类实现

    def execute(self, task: dict) -> ActionResult:
        raise NotImplementedError
