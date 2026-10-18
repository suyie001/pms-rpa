"""一键演示：无需真实硬件和第三方驱动，跑通全流程 + 失败补录。

    python3 run_demo.py

演示内容：
    [1] 开房   801 房 2 位客人（模拟刷证）→ 自动分发 PMS + 公安
    [2] 增加同住人（第 3 位客人）
    [3] 换房   801 → 802
    [4] 续住   802 延长到次日 12:00
    [5] 退房   802（注入一次“公安上报失败”，演示 提醒→待补录→人工重试）
    [6] AI / 启发式字段映射演示（合成控件树）
    [7] 任务汇总
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import yaml  # noqa: E402

from hotel_sync.agents.pms_agent import PMSAgent  # noqa: E402
from hotel_sync.agents.psb_agent import PSBAgent  # noqa: E402
from hotel_sync.capture.capturer import synthetic_dump  # noqa: E402
from hotel_sync.core.client import LocalClient  # noqa: E402
from hotel_sync.core.models import GuestRecord, StayAction  # noqa: E402
from hotel_sync.core.queue import TaskQueue  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_recipe(name: str) -> dict:
    with open(os.path.join(ROOT, "config", "recipes", name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    db_path = os.path.join(tempfile.gettempdir(), "hotel_sync_demo.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)

    queue = TaskQueue(db_path)
    client = LocalClient(queue)

    pms = PMSAgent(client, load_recipe("pms_web.example.yaml"), simulate=True)
    psb = PSBAgent(client, load_recipe("psb_win.example.yaml"), simulate=True)
    agents = [pms, psb]

    def drain(rounds: int = 8) -> None:
        for _ in range(rounds):
            for agent in agents:
                agent.poll_once()

    def show(action_id: str, tag: str) -> None:
        summary = queue.action_summary(action_id)
        line = " | ".join(f"{t}: {v['status']}" for t, v in summary.items())
        print(f"  ⇒ {tag}: {line}")

    print("=" * 74)
    print("HotelSync 演示｜PMS（网页）+ 公安旅店系统（Win 客户端）双系统自动分发")
    print("=" * 74)

    # ------------------------------------------------ [1] 开房
    action = StayAction(
        action_type="check_in",
        room_no="801",
        guests=[
            GuestRecord(name="张伟", nation="汉", id_number="110101199003078811"),
            GuestRecord(name="李娜", nation="汉", id_number="310101199205152426"),
        ],
    )
    print(f"\n[1] 开房/入住｜房间 801｜2 位客人（模拟刷证）")
    client.submit(action, ["pms", "psb"])
    drain()
    show(action.action_id, "分发结果")

    # ------------------------------------------------ [2] 增加同住人
    action2 = StayAction(
        action_type="add_guest",
        room_no="801",
        guests=[GuestRecord(name="王小萌", nation="汉", id_number="510101199807151234")],
    )
    print(f"\n[2] 增加同住人｜房间 801｜+1 位客人")
    client.submit(action2, ["pms", "psb"])
    drain()
    show(action2.action_id, "分发结果")

    # ------------------------------------------------ [3] 换房
    action3 = StayAction(action_type="change_room", room_no="802", old_room_no="801")
    print(f"\n[3] 换房｜801 → 802")
    client.submit(action3, ["pms", "psb"])
    drain()
    show(action3.action_id, "分发结果")

    # ------------------------------------------------ [4] 续住
    action4 = StayAction(
        action_type="extend", room_no="802", new_check_out="2026-10-18 12:00"
    )
    print(f"\n[4] 续住｜房间 802｜新退房时间 2026-10-18 12:00")
    client.submit(action4, ["pms", "psb"])
    drain()
    show(action4.action_id, "分发结果")

    # ------------------------------------------------ [5] 退房（注入公安失败）
    psb._fail_next = 1  # 演示故障注入
    action5 = StayAction(action_type="check_out", room_no="802")
    print(f"\n[5] 退房｜房间 802（注入一次“公安上报失败”，演示兜底流程）")
    client.submit(action5, ["pms", "psb"])
    drain()
    show(action5.action_id, "分发结果")

    pending = queue.pending_manual()
    print(f"  ⇒ 待补录清单：{len(pending)} 条 → "
          + ", ".join(f"{t['target']}({t['last_error']})" for t in pending))
    print("  ⇒ 前台收到提醒（弹窗/声音），人工点击“一键补录”…")
    n = queue.retry_all_failed()
    print(f"  ⇒ 已重新入队 {n} 条")
    drain()
    show(action5.action_id, "补录后结果")
    print(f"  ⇒ 待补录清单剩余：{len(queue.pending_manual())} 条")

    # ------------------------------------------------ [6] AI / 启发式映射
    print("\n[6] 字段映射演示（合成控件树，真实场景来自 capture 工具 dump）")
    print("    注意：AI/映射只在【管理员录制流程】时使用，业务员版不含此能力")
    controls = synthetic_dump()
    from hotel_sync.ai.mapper import heuristic_mapping

    mapping = heuristic_mapping(controls)
    for field_name, ref in mapping.items():
        print(f"  {field_name:<14} → {ref}")

    # ------------------------------------------------ [7] 模板导出 → 导入
    print("\n[7] 模板文件流转演示（管理员导出 → 业务员导入）")
    from hotel_sync.template import export_template, import_template

    tpl_path = os.path.join(tempfile.gettempdir(), "demo酒店.hstemplate")
    bundle = export_template(
        name="演示酒店-2026",
        recipes={
            "pms": load_recipe("pms_web.example.yaml"),
            "psb": load_recipe("psb_win.example.yaml"),
        },
        out_path=tpl_path,
        settings={"reader": {"mode": "manual"}},
    )
    print(f"  管理员导出模板：{tpl_path}")
    print(f"    名称：{bundle.name}｜目标系统：{bundle.targets}｜校验和：{bundle.checksum[:12]}…")
    loaded = import_template(tpl_path)
    recipes = loaded.load_recipes()
    print(f"  业务员导入成功：解析出 {list(recipes)} 两套配方，可直接运行")
    # 防篡改
    import json as _json

    doc = _json.loads(open(tpl_path, encoding="utf-8").read())
    doc["settings"]["tampered"] = True
    bad_path = tpl_path.replace(".hstemplate", "_bad.hstemplate")
    _json.dump(doc, open(bad_path, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        import_template(bad_path)
        print("  !! 防篡改校验失效")
    except ValueError as exc:
        print(f"  防篡改校验生效：{exc}")

    # ------------------------------------------------ [8] 版本边界
    print("\n[8] 版本边界演示（AI 只在录制时，业务员版物理上不含）")
    import hotel_sync.editions as editions

    editions.set_edition(editions.Edition.OPERATOR)
    try:
        editions.require_admin("AI 字段映射")
        print("  !! 边界失效")
    except PermissionError as exc:
        print(f"  业务员版调用 AI 能力被拒绝：{exc}")
    editions.set_edition(editions.Edition.ADMIN)
    print("  （完整 7 项边界自检：python3 tests/test_editions.py）")

    # ------------------------------------------------ [9] 汇总
    print("\n[9] 任务汇总")
    print("  ", queue.stats())
    print(f"\n演示数据库：{db_path}")
    print("真实部署（两台电脑 + 双版本）见 README.md。")


if __name__ == "__main__":
    main()
