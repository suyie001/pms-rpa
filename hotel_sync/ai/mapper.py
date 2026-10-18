"""AI 字段映射：把控件树 dump 变成 recipe 的 fields 段。

两条路径：
1. heuristic_mapping —— 本地关键词规则，无需 API key，捕获后立刻能用
2. suggest_mapping_llm —— 调任意 OpenAI 兼容接口（DeepSeek / 通义 / GLM / 本地 vLLM），
   控件事特别多或界面改版后重新定位时更准

环境变量：
    HOTEL_SYNC_LLM_API_KEY   必填（LLM 路径）
    HOTEL_SYNC_LLM_BASE_URL  默认 https://api.openai.com/v1
    HOTEL_SYNC_LLM_MODEL     默认 gpt-4o-mini
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import requests

# 标准字段 → 界面关键词（同时匹配控件名和 AutomationId）
FIELD_KEYWORDS = {
    "room_no": ["房号", "房间", "客房"],
    "name": ["姓名", "客人姓名", "旅客姓名"],
    "gender": ["性别"],
    "nation": ["民族"],
    "id_number": ["证件号", "身份证", "证件号码", "身份证明号码"],
    "address": ["地址", "住址", "户籍"],
    "check_in_time": ["入住时间", "来店日期", "登记时间", "入住房时间"],
    "check_out_time": ["退房时间", "离店日期", "预离时间"],
}

# 可输入的控件类型
INPUT_TYPES = {"Edit", "ComboBox", "Document", "Spinner", "DataItem"}

STANDARD_FIELDS = list(FIELD_KEYWORDS.keys())

SYSTEM_PROMPT = (
    "你是 RPA 控件映射助手。给你一个 Windows 客户端界面 dump 出的控件列表"
    "（含 control_type / name / automation_id / rect），以及一套标准字段名。"
    "请为每个标准字段找出最可能的输入控件，只返回 JSON，格式为："
    '{"字段名": {"automation_id": "...", "name": "...", "control_type": "..."}}。'
    "注意：Button/MenuItem 不是输入控件；优先匹配控件上可见的文字标签；"
    "找不到的字段不要猜，直接省略。"
)


# ------------------------------------------------------------ 启发式（本地）


def heuristic_mapping(controls: list[dict], fields: Optional[list[str]] = None) -> dict:
    """按关键词规则猜映射：控件名/AutomationId 包含字段关键词即命中。"""
    fields = fields or STANDARD_FIELDS
    mapping: dict = {}
    for c in controls:
        if c.get("control_type") not in INPUT_TYPES:
            continue
        text = f"{c.get('name', '')} {c.get('automation_id', '')}"
        for f in fields:
            if f in mapping:
                continue
            if any(k in text for k in FIELD_KEYWORDS[f]):
                mapping[f] = {
                    k: c.get(k)
                    for k in ("automation_id", "name", "control_type")
                    if c.get(k)
                }
    return mapping


# ------------------------------------------------------------ LLM（远程）


def suggest_mapping_llm(
    controls: list[dict],
    fields: Optional[list[str]] = None,
    model: Optional[str] = None,
) -> dict:
    """调 OpenAI 兼容接口拿映射建议。失败时抛出异常，由调用方回退到启发式。"""
    api_key = os.environ.get("HOTEL_SYNC_LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 HOTEL_SYNC_LLM_API_KEY，无法调用 AI 映射")
    base_url = os.environ.get("HOTEL_SYNC_LLM_BASE_URL", "https://api.openai.com/v1")
    model = model or os.environ.get("HOTEL_SYNC_LLM_MODEL", "gpt-4o-mini")

    slim = [
        {k: c.get(k) for k in ("control_type", "name", "automation_id", "rect")}
        for c in controls
    ]
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"standard_fields": fields or STANDARD_FIELDS, "controls": slim},
                    ensure_ascii=False,
                ),
            },
        ],
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=90,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_json_block(content)


def _parse_json_block(text: str) -> dict:
    """容错解析 LLM 返回的 JSON（可能包在 ```json 代码块里）。"""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if match:
        text = match.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 返回内容不含 JSON: {text[:200]}")
    return json.loads(text[start : end + 1])


def suggest_mapping(controls: list[dict], fields: Optional[list[str]] = None) -> dict:
    """便捷入口：优先 AI，失败自动回退启发式。"""
    try:
        mapping = suggest_mapping_llm(controls, fields)
        print("[ai] 已使用 LLM 映射建议")
        return mapping
    except Exception as exc:
        print(f"[ai] LLM 不可用（{exc}），回退到本地启发式规则")
        return heuristic_mapping(controls, fields)
