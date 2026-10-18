"""HotelSync 核心数据模型：客人、住宿动作、执行结果。"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Optional

# ---------------------------------------------------------------- 基础工具


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- 枚举与常量


class ActionType:
    """住宿动作类型（用字符串值，方便直接存 JSON/YAML）。"""

    CHECK_IN = "check_in"        # 开房 / 入住
    ADD_GUEST = "add_guest"      # 增加同住人
    CHANGE_ROOM = "change_room"  # 换房
    EXTEND = "extend"            # 续住
    CHECK_OUT = "check_out"      # 退房


ACTION_LABELS = {
    ActionType.CHECK_IN: "开房/入住",
    ActionType.ADD_GUEST: "增加同住人",
    ActionType.CHANGE_ROOM: "换房",
    ActionType.EXTEND: "续住",
    ActionType.CHECK_OUT: "退房",
}

ALL_ACTION_TYPES = list(ACTION_LABELS.keys())

ID_TYPES = [
    "居民身份证",
    "护照",
    "港澳居民来往内地通行证",
    "台湾居民来往大陆通行证",
    "其他",
]

TASK_STATUS = ("pending", "running", "success", "failed")


# ---------------------------------------------------------------- 客人


@dataclass
class GuestRecord:
    """一位入住客人。身份证号可自动推导出生日期和性别。"""

    name: str = ""                 # 姓名
    gender: str = ""               # 男 / 女
    nation: str = ""               # 民族，如“汉”
    id_type: str = "居民身份证"
    id_number: str = ""            # 证件号
    birth_date: str = ""           # YYYY-MM-DD
    address: str = ""              # 住址
    photo_b64: Optional[str] = None  # 证件照片（读卡器可选提供）
    room_no: str = ""              # 房间号
    check_in_time: str = ""        # 实际入住时间
    check_out_time: str = ""       # 预计退房时间
    guest_id: str = ""

    def __post_init__(self) -> None:
        if not self.guest_id:
            self.guest_id = new_id("g_")
        # 身份证号推导出生日期（第 7-14 位）和性别（第 17 位奇男偶女）
        if self.id_number and len(self.id_number) >= 14 and not self.birth_date:
            self.birth_date = (
                f"{self.id_number[6:10]}-{self.id_number[10:12]}-{self.id_number[12:14]}"
            )
        if self.id_number and len(self.id_number) >= 17 and not self.gender:
            self.gender = "男" if int(self.id_number[16]) % 2 == 1 else "女"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GuestRecord":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------- 住宿动作


@dataclass
class StayAction:
    """一次前厅操作（开房/加人/换房/续住/退房），需要同步到多个系统。"""

    action_type: str                     # ActionType.*
    room_no: str                         # 房间号（换房时为新房号）
    guests: list[GuestRecord] = field(default_factory=list)
    old_room_no: Optional[str] = None    # 换房时的原房号
    new_check_out: Optional[str] = None  # 续住后的新退房时间
    action_id: str = ""
    created_at: str = ""
    source: str = "reader"               # reader=刷证 / manual=手动 / api=接口

    def __post_init__(self) -> None:
        if not self.action_id:
            self.action_id = new_id("a_")
        if not self.created_at:
            self.created_at = now_iso()

    @property
    def idem_key(self) -> str:
        """幂等键：同一动作 + 房间 + 客人集合 + 新房号/新退房时间，视为同一次操作。"""
        raw = "|".join(
            [
                self.action_type,
                self.room_no,
                self.old_room_no or "",
                self.new_check_out or "",
                ",".join(sorted(g.id_number for g in self.guests)),
            ]
        )
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("idem_key", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StayAction":
        guests = [GuestRecord.from_dict(g) for g in d.get("guests", [])]
        known = set(cls.__dataclass_fields__)
        fields = {k: v for k, v in d.items() if k in known and k != "guests"}
        return cls(guests=guests, **fields)

    def label(self) -> str:
        return ACTION_LABELS.get(self.action_type, self.action_type)


# ---------------------------------------------------------------- 执行结果


@dataclass
class ActionResult:
    """一个目标系统（pms / psb）对某个任务的执行结果。"""

    target: str                # pms / psb
    ok: bool
    message: str = ""
    filled: dict = field(default_factory=dict)  # 实际填入的字段（调试用）
    screenshot: Optional[str] = None
    at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ActionResult":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})
