"""管理员版（Admin Edition）—— 只有管理员电脑上才有这个入口。

全能力：
    capture    录制流程（dump 控件树 + 人工拾取，可叠加 AI 建议）
    ai-map     对已 dump 的控件树跑 AI 字段映射（也可用本地启发式）
    export     把录制好的 recipe 打包成模板文件（.hstemplate）分发给业务员
    verify     校验模板文件完整性（格式 + checksum）
    run        本机调试运行某个角色（不打包也能跑）
    selftest   版本边界自检（验证业务员版不含 AI/录制能力）

日常工作开展时，业务员电脑上运行的是业务员版，本工具的所有 AI/录制
能力都不随包分发，从物理上保证客户数据不接触大模型。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import yaml


def _load_admin_config() -> dict:
    path = os.path.join(_ROOT, "config", "admin.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# ------------------------------------------------------------ 子命令


def cmd_capture(args) -> int:
    from hotel_sync.capture.capturer import run_capture

    run_capture(args.title, args.out, use_ai=args.ai)
    return 0


def cmd_ai_map(args) -> int:
    from hotel_sync.ai.mapper import heuristic_mapping, suggest_mapping_llm

    with open(args.controls, encoding="utf-8") as f:
        controls = json.load(f)
    try:
        mapping = suggest_mapping_llm(controls)
        print("[ai] 已使用 LLM 映射建议")
    except Exception as exc:
        print(f"[ai] LLM 不可用（{exc}），回退本地启发式规则")
        mapping = heuristic_mapping(controls)
    print(json.dumps(mapping, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"已保存: {args.out}")
    return 0


def cmd_export(args) -> int:
    from hotel_sync.template import describe_template, export_template

    recipes: dict = {}
    if args.pms:
        recipes["pms"] = _read_recipe(args.pms)
    if args.psb:
        recipes["psb"] = _read_recipe(args.psb)
    if not recipes:
        print("错误：至少指定一个 recipe（--pms 或 --psb）")
        return 2
    settings = {}
    if args.settings:
        with open(args.settings, encoding="utf-8") as f:
            settings = yaml.safe_load(f) or {}
    bundle = export_template(
        name=args.name,
        recipes=recipes,
        out_path=args.out,
        settings=settings,
        created_by=args.created_by,
    )
    print("模板导出成功：")
    print(describe_template(bundle))
    print(f"文件：{args.out}")
    print("\n把此文件发给业务员，在业务员版里“导入模板”即可使用。")
    return 0


def cmd_verify(args) -> int:
    from hotel_sync.template import describe_template, import_template

    try:
        bundle = import_template(args.path)
    except Exception as exc:
        print(f"校验失败：{exc}")
        return 1
    print("校验通过：")
    print(describe_template(bundle))
    return 0


def cmd_run(args) -> int:
    from hotel_sync.agents.pms_agent import PMSAgent
    from hotel_sync.agents.psb_agent import PSBAgent
    from hotel_sync.core.client import HttpClient, LocalClient
    from hotel_sync.core.queue import TaskQueue

    with open(args.recipe, encoding="utf-8") as f:
        recipe = yaml.safe_load(f)

    if args.coordinator:
        client = HttpClient(args.coordinator)
    else:
        client = LocalClient(TaskQueue(args.db))

    if args.role == "psb":
        agent = PSBAgent(client, recipe, simulate=args.simulate)
    else:
        agent = PMSAgent(
            client, recipe, simulate=args.simulate, browser_ws=args.browser_ws or None
        )
    agent.run_forever()
    return 0


def cmd_selftest(args) -> int:
    from tests.test_editions import run_all

    return run_all(verbose=True)


# ------------------------------------------------------------ 工具


def _read_recipe(path: str):
    """支持 .yaml/.json 模板文件或 recipe 文件。"""
    if path.endswith(".json") or path.endswith(".hstemplate"):
        from hotel_sync.template import import_template

        bundle = import_template(path)
        recipes = bundle.load_recipes()
        if len(recipes) != 1:
            raise ValueError(f"{path} 含多个系统配方，请改用 --pms/--psb 指定 yaml")
        return next(iter(recipes.values()))
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------ 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="admin", description="HotelSync 管理员版（录制 + AI + 模板导出）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("capture", help="录制流程（人工捕获控件，可叠加 AI 建议）")
    p.add_argument("--title", required=True, help="目标窗口标题（正则）")
    p.add_argument("--out", default="config/recipes/psb_win.yaml")
    p.add_argument("--ai", action="store_true", help="先用 AI 给出映射建议")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("ai-map", help="对控件树 dump 跑 AI 字段映射")
    p.add_argument("--controls", default="controls_dump.json")
    p.add_argument("--out", default="", help="保存映射结果 JSON（可选）")
    p.set_defaults(func=cmd_ai_map)

    p = sub.add_parser("export", help="导出模板文件（.hstemplate）分发给业务员")
    p.add_argument("--name", required=True, help="模板名称，如“XX酒店-2026”")
    p.add_argument("--pms", default="", help="PMS 网页 recipe 路径")
    p.add_argument("--psb", default="", help="公安客户端 recipe 路径")
    p.add_argument("--settings", default="", help="随模板分发的运行设置 yaml（可选）")
    p.add_argument("--out", required=True, help="输出模板文件路径")
    p.add_argument("--created-by", default="admin")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("verify", help="校验模板文件完整性")
    p.add_argument("path")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("run", help="本机调试运行某个角色")
    p.add_argument("--role", choices=["pms", "psb"], required=True)
    p.add_argument("--recipe", required=True)
    p.add_argument("--coordinator", default="", help="填则为跨机模式，否则本地直连")
    p.add_argument("--db", default="hotel_sync.db")
    p.add_argument("--simulate", action="store_true")
    p.add_argument("--browser-ws", default="")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("selftest", help="版本边界自检")
    p.set_defaults(func=cmd_selftest)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
