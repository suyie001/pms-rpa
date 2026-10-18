"""公安旅店系统连接器（pywinauto + recipe）。

不同厂商（普天 / 天科安威 / 鸿达 / 邦途…）的客户端界面都不一样，
但都可以用同一套 recipe 描述：

    kind: win32
    window:
      title_regex: ".*旅店业.*|.*治安.*"      # 用正则匹配主窗口标题
    fields:                                   # 标准字段 → 控件定位（捕获/AI 生成）
      name:      {automation_id: "txtName"}
      gender:    {automation_id: "cbGender", control_type: "ComboBox"}
      nation:    {automation_id: "cbNation", control_type: "ComboBox"}
      id_number: {automation_id: "txtCardNo"}
      address:   {automation_id: "txtAddress"}
      room_no:   {automation_id: "txtRoomNo"}
      check_in_time:  {automation_id: "dtpInDate"}
      check_out_time: {automation_id: "dtpOutDate"}
    controls:                                 # 按钮 / 菜单
      menu_check_in:  {name: "入住登记"}
      menu_add_guest: {name: "增加同住人"}
      btn_save:       {automation_id: "btnSave"}
    success:
      dialog_text_regex: "成功|已保存"          # 弹窗出现这些文字视为成功
    actions:
      check_in:
        steps:
          - {op: menu, control: menu_check_in}
          - {op: fill, field: room_no}
          - op: guest_loop
            add_guest: menu_add_guest
            fields: [name, gender, nation, id_number, address, check_in_time, check_out_time]
          - {op: click, control: btn_save}
          - {op: wait_success}

控件定位优先用 automation_id（最稳），退化到窗口标题/控件名。
生成方式：capture 工具人工捕获，或 AI 根据控件树 dump 给出建议。
"""
from __future__ import annotations

import re
import time

from ..core.fields import resolve_field
from ..core.models import ActionResult, StayAction
from .base import BaseAgent


class PSBAgent(BaseAgent):
    def __init__(self, client, recipe: dict, simulate: bool = True, fail_next: int = 0):
        """
        fail_next：演示用故障注入，接下来第 N 次执行强制失败（测试失败兜底流程）。
        """
        super().__init__("psb", client, simulate, name="psb-agent")
        self.recipe = recipe
        self._fail_next = fail_next
        self._app = None

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
            if self._fail_next > 0:
                self._fail_next -= 1
                self.log("[模拟故障] 公安客户端无响应（演示失败 → 待补录 → 人工重试）")
                return ActionResult(self.target, False, "公安客户端无响应（模拟故障）")
            return self._simulate(action, steps)

        try:
            from pywinauto import Application  # 仅 Windows 可用
        except ImportError:
            return ActionResult(self.target, False, "未安装 pywinauto（真实模式仅支持 Windows）")

        title_re = self.recipe["window"]["title_regex"]
        try:
            self._app = Application(backend="uia").connect(title_re=title_re, timeout=15)
            win = self._app.window(title_re=title_re)
            win.restore()
            win.set_focus()
            time.sleep(0.5)
        except Exception as exc:
            return ActionResult(self.target, False, f"连接公安客户端失败: {exc}")

        filled: dict = {}
        try:
            self._run_steps(win, action, steps, filled)
        except Exception as exc:
            return ActionResult(self.target, False, f"录入失败: {exc}", filled=filled)
        return ActionResult(self.target, True, "公安系统录入完成", filled=filled)

    # ------------------------------------------------------------ 步骤引擎

    def _run_steps(self, win, action: StayAction, steps: list, filled: dict) -> None:
        for step in steps:
            op = step["op"]
            if op == "menu":
                self._click_control(win, step["control"])
                time.sleep(0.8)  # 等菜单窗口/对话框弹出
            elif op == "click":
                self._click_control(win, step["control"])
                time.sleep(0.3)
            elif op == "fill":
                value = resolve_field(step["field"], action, None)
                if value:
                    self._set_field(win, step["field"], value)
                    filled[step["field"]] = value
            elif op == "guest_loop":
                self._guest_loop(win, action, step, filled)
            elif op == "wait_success":
                if not self._wait_success():
                    raise RuntimeError("未检测到成功提示（保存/上报确认框）")
            elif op == "sleep":
                time.sleep(step.get("seconds", 0.5))
            else:
                raise ValueError(f"未知 op: {op}")

    def _guest_loop(self, win, action: StayAction, step: dict, filled: dict) -> None:
        for i, guest in enumerate(action.guests):
            if i > 0 and step.get("add_guest"):
                self._click_control(win, step["add_guest"])
                time.sleep(0.5)
            for field_name in step["fields"]:
                value = resolve_field(field_name, action, guest)
                if not value:
                    continue
                self._set_field(win, field_name, value)
                filled[f"guest[{i}].{field_name}"] = value

    # ------------------------------------------------------------ 控件操作

    def _control_ref(self, key: str) -> dict:
        """从 recipe 的 fields / controls 中取控件定位描述。"""
        table = {}
        table.update(self.recipe.get("fields", {}))
        table.update(self.recipe.get("controls", {}))
        ref = table.get(key)
        if not ref:
            raise KeyError(f"recipe 中缺少控件定义: {key}")
        return ref

    def _ctrl(self, win, ref: dict):
        if ref.get("automation_id"):
            return win.child_window(auto_id=ref["automation_id"])
        return win.child_window(title=ref.get("name", ""), control_type=ref.get("control_type"))

    def _set_field(self, win, field_name: str, value: str) -> None:
        key = field_name.split(".")[-1] if "." in field_name else field_name
        ref = self._control_ref(key)
        ctrl = self._ctrl(win, ref)
        ctrl.wait("exists enabled", timeout=10)
        if ref.get("control_type") == "ComboBox":
            try:
                ctrl.select(value)  # 标准下拉
            except Exception:
                ctrl.set_edit_text(value)  # 可编辑下拉
        else:
            ctrl.set_edit_text(value)

    def _click_control(self, win, control_name: str) -> None:
        ref = self._control_ref(control_name)
        ctrl = self._ctrl(win, ref)
        ctrl.wait("exists enabled", timeout=10)
        try:
            ctrl.click_input()
        except Exception:
            ctrl.click()

    def _wait_success(self, timeout: float = 12.0) -> bool:
        """扫描所有窗口，出现成功提示文字即认为成功，并关闭提示框。"""
        pattern = self.recipe.get("success", {}).get("dialog_text_regex", "成功")
        deadline = time.time() + timeout
        while time.time() < deadline:
            for w in self._app.windows():
                try:
                    texts = [c.window_text() or "" for c in w.descendants()]
                except Exception:
                    continue
                if any(re.search(pattern, t) for t in texts):
                    self._close_dialog(w)
                    return True
            time.sleep(0.5)
        return False

    def _close_dialog(self, dialog) -> None:
        """尝试点击提示框上的“确定/OK”。"""
        for name in ("确定", "OK", "是"):
            try:
                btn = dialog.child_window(title=name, control_type="Button")
                if btn.exists():
                    btn.click_input()
                    return
            except Exception:
                continue
        try:
            dialog.close()
        except Exception:
            pass

    # ------------------------------------------------------------ 模拟模式

    def _simulate(self, action: StayAction, steps: list) -> ActionResult:
        filled: dict = {}
        for step in steps:
            if step["op"] == "guest_loop":
                for i, guest in enumerate(action.guests):
                    for field_name in step["fields"]:
                        value = resolve_field(field_name, action, guest)
                        if value:
                            filled[f"guest[{i}].{field_name}"] = value
            elif step["op"] == "fill":
                value = resolve_field(step["field"], action, None)
                if value:
                    filled[step["field"]] = value
        self.log(
            f"[模拟] {action.label()} 房间 {action.room_no}：按 recipe 录入公安系统，"
            f"共 {len(filled)} 个字段"
        )
        return ActionResult(self.target, True, "公安系统录入完成（模拟）", filled=filled)


def main() -> None:
    """命令行启动公安系统 agent（跨机部署时用）。

    python -m hotel_sync.agents.psb_agent \
        --coordinator http://192.168.1.10:8765 \
        --recipe config/recipes/psb_win.yaml
    """
    import argparse

    import yaml

    from ..core.client import HttpClient, LocalClient
    from ..core.queue import TaskQueue

    parser = argparse.ArgumentParser(description="公安旅店系统连接器 Agent")
    parser.add_argument("--coordinator", default="", help="协调服务地址；缺省为本地直连")
    parser.add_argument("--recipe", default="config/recipes/psb_win.example.yaml")
    parser.add_argument("--db", default="hotel_sync.db")
    parser.add_argument("--simulate", action="store_true", help="模拟模式，不操作真实客户端")
    args = parser.parse_args()

    with open(args.recipe, encoding="utf-8") as f:
        recipe = yaml.safe_load(f)

    if args.coordinator:
        client = HttpClient(args.coordinator)
    else:
        client = LocalClient(TaskQueue(args.db))

    agent = PSBAgent(client, recipe, simulate=args.simulate)
    agent.run_forever()


if __name__ == "__main__":
    main()
