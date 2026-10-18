"""PMS 网页连接器（Playwright）。

recipe 结构（config/recipes/pms_web.example.yaml）：

    kind: web
    url: "http://pms.internal/"
    success_selector: ".el-message--success"     # 出现即视为成功
    actions:
      check_in:
        steps:
          - {op: goto, url: "..."}                # 或打开菜单
          - {op: fill, field: room_no, selector: "#room-no"}
          - op: guest_loop                        # 对每个同住人重复执行
            add_guest_selector: "#btn-add-guest"  # 第 2 人起先点“增加同住人”
            steps:
              - {op: fill, field: guest.name, selector: "input[name=guestName]"}
              - {op: select, field: guest.nation, selector: "select[name=nation]"}
          - {op: click, selector: "#btn-submit"}
          - {op: wait_success}

field 取值规则见 hotel_sync/core/fields.py。
"""
from __future__ import annotations

import time

from ..core.fields import resolve_field
from ..core.models import ActionResult, StayAction
from .base import BaseAgent


class PMSAgent(BaseAgent):
    def __init__(
        self,
        client,
        recipe: dict,
        simulate: bool = True,
        headless: bool = False,
        browser_ws: str | None = None,
    ):
        super().__init__("pms", client, simulate, name="pms-agent")
        self.recipe = recipe
        self.headless = headless
        self.browser_ws = browser_ws  # 已打开 Chrome 的 CDP 地址，如 http://127.0.0.1:9222
        self._pw = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------ Playwright 生命周期

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        if self.browser_ws:
            # 连接前台已经在用的浏览器，保留登录态
            self._browser = self._pw.chromium.connect_over_cdp(self.browser_ws)
            ctx = self._browser.contexts[0]
            self._page = ctx.pages[0] if ctx.pages else ctx.new_page()
        else:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._page = self._browser.new_page()
        return self._page

    def close(self) -> None:
        for obj in (self._page, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass

    # ------------------------------------------------------------ 执行

    def execute(self, task: dict) -> ActionResult:
        action = StayAction.from_dict(task["payload"])
        action_cfg = self.recipe.get("actions", {}).get(action.action_type, {})
        steps = action_cfg.get("steps", [])
        if not steps:
            return ActionResult(
                self.target, False, f"recipe 缺少 {action.action_type} 的步骤定义"
            )
        if self.simulate:
            return self._simulate(action, steps)

        page = self._ensure_page()
        filled: dict = {}
        try:
            self._run_steps(page, action, steps, filled)
        except Exception as exc:
            self._screenshot(action.action_id)
            return ActionResult(self.target, False, f"网页操作失败: {exc}", filled=filled)
        return ActionResult(self.target, True, "PMS 填写完成", filled=filled)

    # ------------------------------------------------------------ 步骤引擎

    def _run_steps(self, page, action: StayAction, steps: list, filled: dict) -> None:
        for step in steps:
            op = step["op"]
            if op == "goto":
                page.goto(step["url"])
            elif op == "click":
                page.click(step["selector"], timeout=step.get("timeout", 5000))
            elif op == "fill":
                value = resolve_field(step["field"], action, None) or ""
                page.fill(step["selector"], value, timeout=step.get("timeout", 5000))
                filled[step["field"]] = value
            elif op == "select":
                value = resolve_field(step["field"], action, None)
                page.select_option(step["selector"], value=value)
                filled[step["field"]] = value
            elif op == "guest_loop":
                self._guest_loop(page, action, step, filled)
            elif op == "wait":
                page.wait_for_selector(step["selector"], timeout=step.get("timeout", 8000))
            elif op == "wait_success":
                sel = self.recipe.get("success_selector")
                if sel:
                    page.wait_for_selector(sel, timeout=step.get("timeout", 10000))
            elif op == "sleep":
                time.sleep(step.get("seconds", 1))
            else:
                raise ValueError(f"未知 op: {op}")

    def _guest_loop(self, page, action: StayAction, step: dict, filled: dict) -> None:
        add_sel = step.get("add_guest_selector")
        for i, guest in enumerate(action.guests):
            if i > 0 and add_sel:
                page.click(add_sel, timeout=5000)
                time.sleep(0.3)
            for sub in step["steps"]:
                op = sub["op"]
                if op == "fill":
                    value = resolve_field(sub["field"], action, guest) or ""
                    page.fill(sub["selector"], value, timeout=sub.get("timeout", 5000))
                    filled[f"guest[{i}].{sub['field']}"] = value
                elif op == "select":
                    value = resolve_field(sub["field"], action, guest)
                    page.select_option(sub["selector"], value=value)
                    filled[f"guest[{i}].{sub['field']}"] = value
                elif op == "click":
                    page.click(sub["selector"], timeout=5000)
                elif op == "wait":
                    page.wait_for_selector(sub["selector"], timeout=sub.get("timeout", 5000))
                elif op == "sleep":
                    time.sleep(sub.get("seconds", 0.3))

    # ------------------------------------------------------------ 模拟模式

    def _simulate(self, action: StayAction, steps: list) -> ActionResult:
        filled: dict = {}
        for step in steps:
            if step["op"] == "guest_loop":
                for i, guest in enumerate(action.guests):
                    for sub in step["steps"]:
                        if sub["op"] in ("fill", "select"):
                            key = f"guest[{i}].{sub['field']}"
                            filled[key] = resolve_field(sub["field"], action, guest)
            elif step["op"] in ("fill", "select"):
                filled[step["field"]] = resolve_field(step["field"], action, None)
        self.log(
            f"[模拟] {action.label()} 房间 {action.room_no}：按 recipe 填充 "
            f"{len(filled)} 个字段"
        )
        return ActionResult(self.target, True, "PMS 填写完成（模拟）", filled=filled)

    # ------------------------------------------------------------ 工具

    def _screenshot(self, tag: str) -> None:
        try:
            path = f"pms_error_{tag}.png"
            self._page.screenshot(path=path)
            self.log(f"已保存错误截图: {path}")
        except Exception:
            pass


def main() -> None:
    """命令行启动 PMS agent（跨机部署时用）。

    python -m hotel_sync.agents.pms_agent \
        --coordinator http://192.168.1.20:8765 \
        --recipe config/recipes/pms_web.example.yaml
    """
    import argparse

    import yaml

    from ..core.client import HttpClient, LocalClient
    from ..core.queue import TaskQueue

    parser = argparse.ArgumentParser(description="PMS 网页连接器 Agent")
    parser.add_argument("--coordinator", default="", help="协调服务地址；缺省为本地直连")
    parser.add_argument("--recipe", default="config/recipes/pms_web.example.yaml")
    parser.add_argument("--db", default="hotel_sync.db")
    parser.add_argument("--simulate", action="store_true", help="模拟模式，不打开浏览器")
    parser.add_argument("--browser-ws", default="", help="已打开 Chrome 的 CDP 地址")
    args = parser.parse_args()

    with open(args.recipe, encoding="utf-8") as f:
        recipe = yaml.safe_load(f)

    if args.coordinator:
        client = HttpClient(args.coordinator)
    else:
        client = LocalClient(TaskQueue(args.db))

    agent = PMSAgent(
        client,
        recipe,
        simulate=args.simulate,
        browser_ws=args.browser_ws or None,
    )
    agent.run_forever()


if __name__ == "__main__":
    main()
