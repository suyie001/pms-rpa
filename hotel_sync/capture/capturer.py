"""人工捕获 + AI 辅助：为陌生厂商的公安客户端生成 recipe。

真实捕获（仅 Windows + pywinauto 可用）：

    python -m hotel_sync.capture.capturer --title "旅店业" --out config/recipes/psb_win.yaml

流程：
    1. dump 目标窗口的完整控件树（AutomationId / 名称 / 类型 / 坐标）
    2. 逐个标准字段提示“鼠标悬停到控件上按回车”，按坐标反查控件（取面积最小的命中项）
    3. 合并成 recipe 的 fields / controls 段，套用默认动作模板
    4. （可选）先跑 AI 建议映射做参考，再人工确认

非 Windows 环境下可用 synthetic_dump() + find_control_at() 做逻辑自测。
"""
from __future__ import annotations

import argparse
import ctypes
import json
from typing import Optional

# 标准字段 → 中文标签（捕获时按这个顺序提示）
STANDARD_FIELDS = [
    "room_no",
    "name",
    "gender",
    "nation",
    "id_number",
    "address",
    "check_in_time",
    "check_out_time",
]

FIELD_LABELS = {
    "room_no": "房号",
    "name": "姓名",
    "gender": "性别",
    "nation": "民族",
    "id_number": "证件号码",
    "address": "住址",
    "check_in_time": "入住时间",
    "check_out_time": "退房时间",
}


# ------------------------------------------------------------ 控件树 dump


def dump_controls(title_regex: str, max_depth: int = 10) -> list[dict]:
    """dump 匹配窗口的控件树（真实捕获，仅 Windows）。"""
    try:
        from pywinauto import Application
    except ImportError as exc:
        raise RuntimeError(
            "真实捕获需要 Windows + pywinauto（pip install pywinauto）"
        ) from exc

    app = Application(backend="uia").connect(title_re=title_regex, timeout=15)
    win = app.window(title_re=title_regex)
    out: list[dict] = []

    def walk(element, depth: int) -> None:
        if depth > max_depth:
            return
        info = element.element_info
        try:
            rect = info.rectangle
            rect_list = [rect.left, rect.top, rect.width, rect.height]
        except Exception:
            rect_list = []
        out.append(
            {
                "depth": depth,
                "control_type": info.control_type,
                "name": info.name,
                "automation_id": info.automation_id,
                "class_name": info.class_name,
                "rect": rect_list,
            }
        )
        try:
            for child in element.children():
                walk(child, depth + 1)
        except Exception:
            pass

    walk(win, 0)
    return out


def synthetic_dump() -> list[dict]:
    """构造一份常见公安客户端的合成控件树，用于本地测试 AI 映射。"""
    return [
        {"control_type": "Window", "name": "旅店业治安管理信息系统", "automation_id": "FrmMain", "rect": [0, 0, 1024, 768], "depth": 0},
        {"control_type": "Edit", "name": "房号", "automation_id": "txtRoomNo", "rect": [120, 40, 70, 22], "depth": 2},
        {"control_type": "Edit", "name": "姓名", "automation_id": "txtName", "rect": [120, 80, 150, 22], "depth": 2},
        {"control_type": "ComboBox", "name": "性别", "automation_id": "cbGender", "rect": [300, 80, 80, 22], "depth": 2},
        {"control_type": "ComboBox", "name": "民族", "automation_id": "cbNation", "rect": [420, 80, 90, 22], "depth": 2},
        {"control_type": "Edit", "name": "证件号码", "automation_id": "txtCardNo", "rect": [120, 110, 200, 22], "depth": 2},
        {"control_type": "Edit", "name": "户籍地址", "automation_id": "txtAddress", "rect": [120, 140, 320, 22], "depth": 2},
        {"control_type": "Edit", "name": "来店日期", "automation_id": "dtpInDate", "rect": [120, 170, 120, 22], "depth": 2},
        {"control_type": "Edit", "name": "离店日期", "automation_id": "dtpOutDate", "rect": [260, 170, 120, 22], "depth": 2},
        {"control_type": "Button", "name": "保存", "automation_id": "btnSave", "rect": [400, 400, 80, 26], "depth": 2},
        {"control_type": "MenuItem", "name": "入住登记", "automation_id": "menuCheckIn", "rect": [10, 30, 80, 20], "depth": 1},
        {"control_type": "Button", "name": "增加同住人", "automation_id": "btnAddGuest", "rect": [400, 200, 90, 26], "depth": 2},
    ]


# ------------------------------------------------------------ 坐标拾取


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos() -> tuple[int, int]:
    """当前鼠标坐标（Windows）。"""
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def find_control_at(controls: list[dict], x: int, y: int) -> Optional[dict]:
    """返回包含坐标 (x, y) 且面积最小的控件（最内层命中）。"""
    hits = []
    for c in controls:
        r = c.get("rect")
        if not r or len(r) != 4:
            continue
        left, top, w, h = r
        if left <= x <= left + w and top <= y <= top + h and w > 0 and h > 0:
            hits.append(c)
    if not hits:
        return None
    return min(hits, key=lambda c: c["rect"][2] * c["rect"][3])


# ------------------------------------------------------------ 交互式捕获


def capture_fields(
    title_regex: str,
    fields: Optional[list[str]] = None,
    controls: Optional[list[dict]] = None,
    input_fn=input,
    output_fn=print,
) -> dict:
    """逐个字段人工拾取，返回 {标准字段: 控件定位}。"""
    fields = fields or STANDARD_FIELDS
    controls = controls if controls is not None else dump_controls(title_regex)
    output_fn(f"共 dump 到 {len(controls)} 个控件，开始逐字段拾取：")
    mapping: dict = {}
    for f in fields:
        label = FIELD_LABELS.get(f, f)
        input_fn(f"请把鼠标悬停到【{label}】的输入控件上，然后按回车…")
        x, y = cursor_pos()
        c = find_control_at(controls, x, y)
        if not c:
            output_fn(f"  ! 坐标 ({x},{y}) 未命中控件，已跳过 {f}")
            continue
        mapping[f] = {
            k: c.get(k)
            for k in ("automation_id", "name", "control_type")
            if c.get(k)
        }
        output_fn(f"  ✓ {f} → {mapping[f]}")
    return mapping


# ------------------------------------------------------------ recipe 生成

DEFAULT_ACTIONS = {
    "check_in": {
        "steps": [
            {"op": "menu", "control": "menu_check_in"},
            {"op": "fill", "field": "room_no"},
            {
                "op": "guest_loop",
                "add_guest": "menu_add_guest",
                "fields": [
                    "name",
                    "gender",
                    "nation",
                    "id_number",
                    "address",
                    "check_in_time",
                    "check_out_time",
                ],
            },
            {"op": "click", "control": "btn_save"},
            {"op": "wait_success"},
        ]
    },
    "add_guest": {
        "steps": [
            {"op": "menu", "control": "menu_check_in"},
            {"op": "fill", "field": "room_no"},
            {
                "op": "guest_loop",
                "add_guest": "menu_add_guest",
                "fields": [
                    "name",
                    "gender",
                    "nation",
                    "id_number",
                    "address",
                    "check_in_time",
                    "check_out_time",
                ],
            },
            {"op": "click", "control": "btn_save"},
            {"op": "wait_success"},
        ]
    },
    "check_out": {
        "steps": [
            {"op": "menu", "control": "menu_check_out"},
            {"op": "fill", "field": "room_no"},
            {"op": "click", "control": "btn_save"},
            {"op": "wait_success"},
        ]
    },
    "change_room": {
        "steps": [
            {"op": "menu", "control": "menu_change_room"},
            {"op": "fill", "field": "old_room_no"},
            {"op": "fill", "field": "room_no"},
            {"op": "click", "control": "btn_save"},
            {"op": "wait_success"},
        ]
    },
    "extend": {
        "steps": [
            {"op": "menu", "control": "menu_extend"},
            {"op": "fill", "field": "room_no"},
            {"op": "fill", "field": "new_check_out"},
            {"op": "click", "control": "btn_save"},
            {"op": "wait_success"},
        ]
    },
}


def build_recipe(title_regex: str, fields_mapping: dict, controls_mapping: dict | None = None) -> dict:
    return {
        "kind": "win32",
        "window": {"title_regex": title_regex},
        "fields": fields_mapping,
        "controls": controls_mapping or {},
        "success": {"dialog_text_regex": "成功|已保存|上报成功"},
        "actions": DEFAULT_ACTIONS,
    }


def save_recipe(path: str, recipe: dict, output_fn=print) -> None:
    import yaml

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(recipe, f, allow_unicode=True, sort_keys=False)
    output_fn(f"recipe 已保存: {path}")


def run_capture(
    title: str,
    out: str = "config/recipes/psb_win.yaml",
    use_ai: bool = False,
    input_fn=input,
    output_fn=print,
    dump_path: str = "controls_dump.json",
) -> dict:
    """执行一次完整捕获，返回生成的 recipe（管理员工具复用此函数）。"""
    controls = dump_controls(title)
    output_fn(f"dump 到 {len(controls)} 个控件")
    with open(dump_path, "w", encoding="utf-8") as f:
        json.dump(controls, f, ensure_ascii=False, indent=2)
    output_fn(f"控件树已存到 {dump_path}（可交给 AI 分析）")

    fields_mapping: Optional[dict] = None
    if use_ai:
        from ..ai.mapper import suggest_mapping_llm

        try:
            suggestion = suggest_mapping_llm(controls)
            output_fn("AI 建议映射：" + json.dumps(suggestion, ensure_ascii=False, indent=2))
            if input_fn("采用 AI 建议作为底稿？[y/N] ").strip().lower() == "y":
                fields_mapping = suggestion
        except Exception as exc:
            output_fn(f"AI 不可用（{exc}），转为人工捕获")

    if fields_mapping is None:
        fields_mapping = capture_fields(
            title, controls=controls, input_fn=input_fn, output_fn=output_fn
        )

    controls_mapping = capture_controls(
        title, controls, input_fn=input_fn, output_fn=output_fn
    )
    recipe = build_recipe(title, fields_mapping, controls_mapping)
    save_recipe(out, recipe, output_fn=output_fn)
    return recipe


def main() -> None:
    parser = argparse.ArgumentParser(description="公安客户端控件捕获工具")
    parser.add_argument("--title", required=True, help="主窗口标题（正则）")
    parser.add_argument("--out", default="config/recipes/psb_win.yaml")
    parser.add_argument("--ai", action="store_true", help="先用 AI 给出映射建议")
    args = parser.parse_args()
    run_capture(args.title, args.out, args.ai)


def capture_controls(title_regex: str, controls: list[dict], input_fn=input, output_fn=print) -> dict:
    """拾取按钮/菜单（menu_check_in / btn_save 等）。"""
    names = [
        "menu_check_in",
        "menu_add_guest",
        "menu_check_out",
        "menu_change_room",
        "menu_extend",
        "btn_save",
    ]
    output_fn("\n接下来拾取按钮/菜单（没有的可以直接回车跳过）：")
    out: dict = {}
    for key in names:
        input_fn(f"请把鼠标悬停到【{key}】上，然后按回车（没有则直接回车）…")
        x, y = cursor_pos()
        c = find_control_at(controls, x, y)
        if not c:
            continue
        out[key] = {
            k: c.get(k)
            for k in ("automation_id", "name", "control_type")
            if c.get(k)
        }
        output_fn(f"  ✓ {key} → {out[key]}")
    return out


if __name__ == "__main__":
    main()
