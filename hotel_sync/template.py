"""流程模板文件（.hstemplate）—— 管理员录制 → 业务员导入的交付载体。

一个模板 = 一次完整部署所需的全部配方 + 运行设置，打成单个 JSON 文件：

    {
      "format":   "hotel-sync-template",
      "version":  1,
      "metadata": {模板名/ID/创建人/时间/适用目标系统...},
      "recipes":  {"pms": "<yaml 文本>", "psb": "<yaml 文本>"},
      "settings": {reader/face_verify/alert 等运行设置},
      "checksum": "sha256(...)"     # 除 checksum 外的规范化 JSON 哈希
    }

安全设计：
- checksum 防篡改：业务员导入时校验，文件被改过一个字节都会拒绝
- 模板只包含“怎么操作界面”的配方，不含任何客户数据
- 业务员日常导入/运行模板的全过程不涉及 AI（AI 只在管理员录制时用）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .core.models import now_iso, new_id

TEMPLATE_FORMAT = "hotel-sync-template"
TEMPLATE_VERSION = 1
TEMPLATE_SUFFIX = ".hstemplate"


@dataclass
class TemplateBundle:
    """已校验的模板内容。"""

    metadata: dict
    recipes: dict[str, str]                 # target -> yaml 文本
    settings: dict = field(default_factory=dict)
    checksum: str = ""

    @property
    def name(self) -> str:
        return self.metadata.get("name", "(未命名模板)")

    @property
    def targets(self) -> list[str]:
        return list(self.recipes.keys())

    def load_recipes(self) -> dict[str, dict]:
        """把模板里的 yaml 文本解析成 recipe 字典。"""
        import yaml

        return {t: yaml.safe_load(text) for t, text in self.recipes.items()}

    def save_recipes_to(self, out_dir: str | Path) -> dict[str, str]:
        """把模板中的配方落盘到本机运行时目录，返回 {target: 路径}。"""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        paths = {}
        for target, text in self.recipes.items():
            p = out / f"{target}.yaml"
            p.write_text(text, encoding="utf-8")
            paths[target] = str(p)
        return paths


# ------------------------------------------------------------ 校验和


def _compute_checksum(metadata: dict, recipes: dict, settings: dict) -> str:
    payload = json.dumps(
        {"metadata": metadata, "recipes": recipes, "settings": settings},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ------------------------------------------------------------ 导出（管理员）


def export_template(
    *,
    name: str,
    recipes: dict[str, Any],
    out_path: str | Path,
    settings: Optional[dict] = None,
    created_by: str = "admin",
    targets: Optional[list[str]] = None,
) -> TemplateBundle:
    """把配方打包成模板文件。

    recipes: {"pms": <dict 或 yaml 文本>, "psb": ...}
    """
    import yaml

    norm: dict[str, str] = {}
    for target, value in recipes.items():
        if isinstance(value, str):
            norm[target] = value
        else:
            norm[target] = yaml.safe_dump(value, allow_unicode=True, sort_keys=False)

    metadata = {
        "format": TEMPLATE_FORMAT,
        "version": TEMPLATE_VERSION,
        "template_id": new_id("tpl_"),
        "name": name,
        "created_at": now_iso(),
        "created_by": created_by,
        "targets": targets or list(norm.keys()),
        "min_app_version": "0.2.0",
        "note": "仅包含界面操作配方，不含客户数据；日常运行不调用 AI",
    }
    settings = settings or {}
    checksum = _compute_checksum(metadata, norm, settings)

    doc = {
        "metadata": metadata,
        "recipes": norm,
        "settings": settings,
        "checksum": checksum,
    }
    out = Path(out_path)
    if out.suffix != TEMPLATE_SUFFIX:
        out = out.with_suffix(TEMPLATE_SUFFIX)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return TemplateBundle(metadata, norm, settings, checksum)


# ------------------------------------------------------------ 导入（业务员）


def import_template(path: str | Path) -> TemplateBundle:
    """业务员导入模板：校验格式与 checksum，任何不符都拒绝。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("metadata", {}).get("format") != TEMPLATE_FORMAT:
        raise ValueError("不是有效的 HotelSync 模板文件（format 不匹配）")
    metadata = doc.get("metadata", {})
    recipes = doc.get("recipes", {})
    settings = doc.get("settings", {})
    expected = _compute_checksum(metadata, recipes, settings)
    if expected != doc.get("checksum"):
        raise ValueError(
            "模板校验失败：文件被篡改或损坏（checksum 不匹配），已拒绝导入"
        )
    if not recipes:
        raise ValueError("模板内容为空：没有任何系统的配方")
    return TemplateBundle(metadata, recipes, settings, doc["checksum"])


def describe_template(bundle: TemplateBundle) -> str:
    m = bundle.metadata
    return (
        f"模板名：{m.get('name')}\n"
        f"模板 ID：{m.get('template_id')}\n"
        f"创建人：{m.get('created_by')}  创建时间：{m.get('created_at')}\n"
        f"适用系统：{', '.join(bundle.targets)}\n"
        f"校验和：{bundle.checksum[:16]}…"
    )


# ------------------------------------------------------------ 运行时目录


def runtime_dir() -> Path:
    """业务员版导入模板后，配方落到本机的运行时目录。"""
    p = Path(os_environ("HOTEL_SYNC_RUNTIME_DIR", "runtime"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def os_environ(key: str, default: str) -> str:
    import os

    return os.environ.get(key, default)
