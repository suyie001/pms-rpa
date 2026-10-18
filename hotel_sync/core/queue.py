"""SQLite 任务队列：幂等入队、领取、结果回写、待补录清单。

设计要点：
- UNIQUE(action_id, target)：同一动作 × 同一目标系统只会产生一条任务，
  重复提交/重复领取都不会造成二次录入（幂等）。
- 任务生命周期：pending → running → success / failed；
  failed 可由人工一键 retry 重新入队。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Iterable, Optional

from .models import ActionResult, StayAction, TASK_STATUS, now_iso, new_id


class TaskQueue:
    def __init__(self, db_path: str = "hotel_sync.db", max_attempts: int = 3):
        self.db_path = db_path
        self.max_attempts = max_attempts
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------ 连接管理

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        task_id     TEXT PRIMARY KEY,
                        action_id   TEXT NOT NULL,
                        target      TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        idem_key    TEXT NOT NULL,
                        payload     TEXT NOT NULL,
                        status      TEXT NOT NULL DEFAULT 'pending',
                        attempts    INTEGER NOT NULL DEFAULT 0,
                        last_error  TEXT NOT NULL DEFAULT '',
                        result      TEXT,
                        created_at  TEXT NOT NULL,
                        updated_at  TEXT NOT NULL,
                        UNIQUE(action_id, target)
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_status "
                    "ON tasks(status, target, created_at)"
                )
        finally:
            conn.close()

    # ------------------------------------------------------------ 写入

    def enqueue_action(self, action: StayAction, targets: Iterable[str]) -> list[str]:
        """把一个住宿动作分发给多个目标系统。返回新建的 task_id 列表。

        已存在的 (action_id, target) 组合直接跳过，保证幂等。
        """
        task_ids: list[str] = []
        conn = self._conn()
        try:
            with self._lock, conn:
                for target in targets:
                    exists = conn.execute(
                        "SELECT 1 FROM tasks WHERE action_id=? AND target=?",
                        (action.action_id, target),
                    ).fetchone()
                    if exists:
                        continue
                    task_id = new_id("t_")
                    conn.execute(
                        "INSERT INTO tasks(task_id, action_id, target, action_type, idem_key,"
                        " payload, status, created_at, updated_at)"
                        " VALUES(?,?,?,?,?,?, 'pending', ?, ?)",
                        (
                            task_id,
                            action.action_id,
                            target,
                            action.action_type,
                            action.idem_key,
                            json.dumps(action.to_dict(), ensure_ascii=False),
                            action.created_at,
                            now_iso(),
                        ),
                    )
                    task_ids.append(task_id)
        finally:
            conn.close()
        return task_ids

    def next_task(self, target: str) -> Optional[dict]:
        """领取该目标系统最早的 pending 任务，置为 running。

        同时回收“僵死任务”：agent 中途崩溃会让任务停在 running，
        超过 stale_seconds 未更新的 running 任务自动退回 pending 重新排队。
        """
        conn = self._conn()
        try:
            with self._lock, conn:
                self._reap_stale(conn)
                row = conn.execute(
                    "SELECT * FROM tasks WHERE target=? AND status='pending'"
                    " ORDER BY created_at LIMIT 1",
                    (target,),
                ).fetchone()
                if not row:
                    return None
                conn.execute(
                    "UPDATE tasks SET status='running', attempts=attempts+1,"
                    " updated_at=? WHERE task_id=?",
                    (now_iso(), row["task_id"]),
                )
                task = self._row_to_task(row)
                task["status"] = "running"
                task["attempts"] = row["attempts"] + 1
                return task
        finally:
            conn.close()

    def _reap_stale(self, conn: sqlite3.Connection, stale_seconds: int = 300) -> None:
        cutoff = (datetime.now() - timedelta(seconds=stale_seconds)).isoformat(
            timespec="seconds"
        )
        conn.execute(
            "UPDATE tasks SET status='pending' WHERE status='running' AND updated_at < ?",
            (cutoff,),
        )

    def finish(self, task_id: str, result: ActionResult) -> None:
        status = "success" if result.ok else "failed"
        conn = self._conn()
        try:
            with self._lock, conn:
                conn.execute(
                    "UPDATE tasks SET status=?, last_error=?, result=?, updated_at=?"
                    " WHERE task_id=?",
                    (
                        status,
                        result.message,
                        json.dumps(result.to_dict(), ensure_ascii=False),
                        now_iso(),
                        task_id,
                    ),
                )
        finally:
            conn.close()

    def fail_task(self, task_id: str, error: str) -> None:
        conn = self._conn()
        try:
            with self._lock, conn:
                conn.execute(
                    "UPDATE tasks SET status='failed', last_error=?, updated_at=? WHERE task_id=?",
                    (error, now_iso(), task_id),
                )
        finally:
            conn.close()

    def retry(self, task_id: str) -> bool:
        """人工补录：把一条 failed 任务重新置为 pending。"""
        conn = self._conn()
        try:
            with self._lock, conn:
                cur = conn.execute(
                    "UPDATE tasks SET status='pending', updated_at=? WHERE task_id=?"
                    " AND status='failed'",
                    (now_iso(), task_id),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    def retry_all_failed(self) -> int:
        conn = self._conn()
        try:
            with self._lock, conn:
                cur = conn.execute(
                    "UPDATE tasks SET status='pending', updated_at=? WHERE status='failed'",
                    (now_iso(),),
                )
                return cur.rowcount
        finally:
            conn.close()

    # ------------------------------------------------------------ 查询

    def pending_manual(self) -> list[dict]:
        """待补录清单：所有 failed 任务。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status='failed' ORDER BY created_at"
            ).fetchall()
            return [self._row_to_task(r) for r in rows]
        finally:
            conn.close()

    def action_summary(self, action_id: str) -> dict:
        """某个动作在各目标系统上的执行情况汇总。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT target, status, last_error, attempts FROM tasks WHERE action_id=?",
                (action_id,),
            ).fetchall()
            return {
                r["target"]: {
                    "status": r["status"],
                    "attempts": r["attempts"],
                    "error": r["last_error"],
                }
                for r in rows
            }
        finally:
            conn.close()

    def stats(self) -> dict:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
            out = {s: 0 for s in TASK_STATUS}
            for r in rows:
                out[r["status"]] = r["n"]
            return out
        finally:
            conn.close()

    def all_tasks(self, limit: int = 200) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_task(r) for r in rows]
        finally:
            conn.close()

    # ------------------------------------------------------------ 工具

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        if d.get("result"):
            d["result"] = json.loads(d["result"])
        return d
