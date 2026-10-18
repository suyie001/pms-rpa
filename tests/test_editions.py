"""版本边界与模板安全自检。

覆盖：
  1. require_admin 守卫：业务员版进程调用管理员能力必须被拒绝
  2. AI / 录制包的导入边界：业务员版身份下 import 即报错
  3. 业务员版应用模块的导入图：不得包含 hotel_sync.ai / hotel_sync.capture
  4. 日常运行链路源码扫描：agents / core / 业务员版入口不得引用 AI/录制
  5. 模板导出 → 导入往返一致
  6. 模板防篡改：改一个字节校验和即失败

运行：
    python tests/test_editions.py
    python -m apps.admin_app selftest
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _run_py(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(ROOT),
        timeout=120,
    )


# ------------------------------------------------------------ 单项测试


def test_require_admin_guard() -> None:
    import hotel_sync.editions as editions

    editions.set_edition(editions.Edition.OPERATOR)
    try:
        editions.require_admin("AI 字段映射")
        raise AssertionError("业务员版竟然调用了管理员能力")
    except PermissionError:
        pass
    editions.set_edition(editions.Edition.ADMIN)
    editions.require_admin("AI 字段映射")  # 管理员版必须放行
    print("  ✓ require_admin 守卫正常")


def test_ai_package_import_boundary() -> None:
    code = (
        "import hotel_sync.editions as e; e.set_edition('operator');"
        " import hotel_sync.ai"
    )
    r = _run_py(code)
    assert r.returncode != 0, "业务员版身份下 AI 包导入未被阻止"
    assert "仅管理员版" in (r.stderr + r.stdout), "缺少明确的拒绝信息"
    print("  ✓ 业务员版身份下 AI 包导入被拒绝")


def test_capture_package_import_boundary() -> None:
    code = (
        "import hotel_sync.editions as e; e.set_edition('operator');"
        " import hotel_sync.capture"
    )
    r = _run_py(code)
    assert r.returncode != 0, "业务员版身份下录制包导入未被阻止"
    assert "仅管理员版" in (r.stderr + r.stdout), "缺少明确的拒绝信息"
    print("  ✓ 业务员版身份下录制包导入被拒绝")


def test_operator_module_import_graph() -> None:
    code = (
        "import sys; import apps.operator_app;"
        " bad = [m for m in sys.modules"
        "        if m.startswith('hotel_sync.ai') or m.startswith('hotel_sync.capture')];"
        " print(bad); sys.exit(1 if bad else 0)"
    )
    r = _run_py(code)
    assert r.returncode == 0, f"业务员版模块导入图被污染: {r.stdout} {r.stderr}"
    print("  ✓ 业务员版应用模块导入图不含 AI/录制模块")


def _imports_of(path: Path) -> set[str]:
    """用 AST 提取文件的 import 模块名（注释/字符串里的不算）。"""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
                for alias in node.names:
                    mods.add(f"{node.module}.{alias.name}")
    return mods


def test_daily_paths_have_no_ai_reference() -> None:
    """日常运行链路（agents/core/业务员入口）不得 import AI/录制模块。"""
    files = [
        "hotel_sync/agents/base.py",
        "hotel_sync/agents/pms_agent.py",
        "hotel_sync/agents/psb_agent.py",
        "hotel_sync/core/client.py",
        "hotel_sync/core/queue.py",
        "hotel_sync/core/models.py",
        "hotel_sync/core/orchestrator.py",
        "hotel_sync/template.py",
        "apps/operator_app.py",
    ]
    banned = ("hotel_sync.ai", "hotel_sync.capture")
    for rel in files:
        for mod in _imports_of(ROOT / rel):
            for b in banned:
                assert not mod.startswith(b), f"{rel} 导入禁用模块: {mod}"
        # 源码里也不得出现大模型接口调用痕迹
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "chat/completions" not in src, f"{rel} 中出现大模型接口调用"
    print("  ✓ 日常运行链路无 AI/录制引用")


def test_template_roundtrip() -> None:
    from hotel_sync.template import export_template, import_template

    with tempfile.TemporaryDirectory() as td:
        pms_recipe = {
            "kind": "web",
            "url": "http://pms.internal/",
            "actions": {"check_in": {"steps": [{"op": "fill", "field": "room_no"}]}},
        }
        psb_recipe = {
            "kind": "win32",
            "window": {"title_regex": ".*旅店业.*"},
            "fields": {"name": {"automation_id": "txtName"}},
            "actions": {"check_in": {"steps": [{"op": "fill", "field": "name"}]}},
        }
        out = os.path.join(td, "demo.hstemplate")
        bundle = export_template(
            name="自检模板",
            recipes={"pms": pms_recipe, "psb": psb_recipe},
            out_path=out,
            settings={"reader": {"mode": "manual"}},
        )
        assert bundle.name == "自检模板"
        loaded = import_template(out)
        assert loaded.recipes == bundle.recipes
        recipes = loaded.load_recipes()
        assert recipes["pms"]["kind"] == "web"
        assert recipes["psb"]["fields"]["name"]["automation_id"] == "txtName"
        assert loaded.settings["reader"]["mode"] == "manual"
    print("  ✓ 模板导出 → 导入往返一致")


def test_template_tamper_detection() -> None:
    from hotel_sync.template import export_template, import_template

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "tamper.hstemplate")
        export_template(
            name="防篡改测试",
            recipes={"psb": {"kind": "win32", "actions": {}}},
            out_path=out,
        )
        doc = json.loads(Path(out).read_text(encoding="utf-8"))
        doc["recipes"]["psb"] = doc["recipes"]["psb"] + "# tampered"  # 改一个字节
        Path(out).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        try:
            import_template(out)
            raise AssertionError("被篡改的模板竟然通过了校验")
        except ValueError as exc:
            assert "校验失败" in str(exc)
    print("  ✓ 模板防篡改生效（checksum 校验）")


# ------------------------------------------------------------ 汇总

ALL_TESTS = [
    test_require_admin_guard,
    test_ai_package_import_boundary,
    test_capture_package_import_boundary,
    test_operator_module_import_graph,
    test_daily_paths_have_no_ai_reference,
    test_template_roundtrip,
    test_template_tamper_detection,
]


def run_all(verbose: bool = False) -> int:
    print("HotelSync 版本边界与模板安全自检")
    print("-" * 56)
    failed = 0
    for test in ALL_TESTS:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  ✗ {test.__name__}: {exc}")
            if verbose:
                import traceback

                traceback.print_exc()
    print("-" * 56)
    if failed:
        print(f"结果：{failed}/{len(ALL_TESTS)} 项失败")
        return 1
    print(f"结果：全部 {len(ALL_TESTS)} 项通过")
    print("业务员版不含 AI 映射与流程录制能力，日常运行零 AI 调用。")
    return 0


if __name__ == "__main__":
    sys.exit(run_all(verbose=True))
