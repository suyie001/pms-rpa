"""协调服务（Coordinator）：跑在公安那台电脑（或任一台常开机器）上。

职责：
    - 接收 PMS 机提交的住宿动作，拆成 pms / psb 两个任务
    - 两个 agent 通过 HTTP 拉取自己负责的任务、回写结果
    - 维护待补录清单（failed），支持一键重试
    - SQLite 持久化，重启不丢

启动：
    python -m hotel_sync.coordinator --db hotel_sync.db --host 0.0.0.0 --port 8765

两台电脑部署：
    公安机（本服务 + psb-agent 本地直连）：
        python -m hotel_sync.coordinator
        python -m hotel_sync.agents.psb_agent --recipe config/recipes/psb_win.yaml
    PMS 机（pms-agent 通过 HTTP 接入）：
        python -m hotel_sync.agents.pms_agent \
            --coordinator http://<公安机IP>:8765 --recipe config/recipes/pms_web.example.yaml
"""
from __future__ import annotations

import argparse
import os

from fastapi import FastAPI
from pydantic import BaseModel

from .core.client import LocalClient
from .core.models import ActionResult, StayAction
from .core.orchestrator import Orchestrator
from .core.queue import TaskQueue

DEFAULT_DB = os.environ.get("HOTEL_SYNC_DB", "hotel_sync.db")


class ActionPayload(BaseModel):
    action: dict
    targets: list[str] | None = None


class ResultPayload(BaseModel):
    target: str
    ok: bool
    message: str = ""
    filled: dict = {}
    screenshot: str | None = None


def create_app(db_path: str = DEFAULT_DB) -> FastAPI:
    queue = TaskQueue(db_path)
    orch = Orchestrator(LocalClient(queue))

    app = FastAPI(title="HotelSync Coordinator", version="0.1.0")

    # ---------------------------------------------------------- 健康检查

    @app.get("/health")
    def health():
        return {"ok": True, "db": db_path}

    @app.get("/stats")
    def stats():
        return queue.stats()

    # ---------------------------------------------------------- 动作提交

    @app.post("/actions")
    def submit_action(payload: ActionPayload):
        action = StayAction.from_dict(payload.action)
        targets = payload.targets or orch.targets
        task_ids = orch.client.submit(action, targets)
        return {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "label": action.label(),
            "room_no": action.room_no,
            "guests": len(action.guests),
            "tasks": task_ids,
            "duplicated": len(task_ids) == 0,
        }

    @app.get("/actions/{action_id}")
    def action_status(action_id: str):
        return queue.action_summary(action_id)

    # ---------------------------------------------------------- 任务拉取 / 回写

    @app.get("/tasks/next")
    def tasks_next(target: str):
        task = queue.next_task(target)
        return {"task": task}

    @app.post("/tasks/{task_id}/result")
    def tasks_result(task_id: str, result: ResultPayload):
        queue.finish(task_id, ActionResult.from_dict(result.model_dump()))
        return {"ok": True}

    # ---------------------------------------------------------- 待补录

    @app.get("/pending")
    def pending():
        return {"tasks": queue.pending_manual()}

    @app.post("/tasks/{task_id}/retry")
    def retry_task(task_id: str):
        return {"ok": queue.retry(task_id)}

    @app.post("/tasks/retry-all")
    def retry_all():
        return {"retried": queue.retry_all_failed()}

    # ---------------------------------------------------------- 调试

    @app.get("/tasks")
    def all_tasks(limit: int = 200):
        return {"tasks": queue.all_tasks(limit)}

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="HotelSync 协调服务")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    import uvicorn

    print(f"Coordinator 启动: http://{args.host}:{args.port}  db={args.db}")
    uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
