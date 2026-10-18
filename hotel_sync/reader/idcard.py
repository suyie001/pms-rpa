"""身份证读卡器接入。

- SimulatedReader：演示用，模拟连续刷证
- ManualReader：无硬件时手动输入（身份证号 + 姓名 + 民族）
- SDKReader：厂商 DLL 骨架（华视 / 精伦 / 神思等），按实际 SDK 文档替换函数名

以华视 CVR-100U（Termb.dll）为例的典型调用序列：
    CVR_Init_Comm(端口) → CVR_Authenticate() → CVR_Read_Content(端口)
    读到的文字/照片按 SDK 文档解析（文字通常在指定路径的 txt，照片为 base64/wlt）
"""
from __future__ import annotations

from typing import Callable, Optional

from ..core.models import GuestRecord


class SimulatedReader:
    """演示用：按顺序返回预置客人，模拟刷身份证。"""

    def __init__(self, guests: Optional[list[GuestRecord]] = None):
        self._guests = list(
            guests
            or [
                GuestRecord(name="张伟", nation="汉", id_number="110101199003078811"),
                GuestRecord(name="李娜", nation="汉", id_number="310101199205152426"),
            ]
        )
        self._i = 0

    def read(self) -> Optional[GuestRecord]:
        if self._i >= len(self._guests):
            return None
        g = self._guests[self._i]
        self._i += 1
        return g


class ManualReader:
    """无硬件时手动输入（原型默认）。只要求身份证号和姓名，民族可缺省。"""

    def __init__(self, input_fn: Callable = input, output_fn: Callable = print):
        self._input = input_fn
        self._output = output_fn

    def read(self) -> Optional[GuestRecord]:
        id_number = self._input("身份证号（直接回车结束）: ").strip()
        if not id_number:
            return None
        name = self._input("姓名: ").strip()
        nation = self._input("民族（回车默认“汉”）: ").strip() or "汉"
        return GuestRecord(name=name, nation=nation, id_number=id_number)


class SDKReader:
    """厂商读卡器 DLL 骨架。

    用法：按你的读卡器 SDK 文档修改 _load_dll / read 内的函数名与解析逻辑，
    然后保持 read() 返回 GuestRecord 的契约不变。
    """

    def __init__(self, dll_path: str, com_port: int = 1001):
        self.dll_path = dll_path
        self.com_port = com_port
        self._dll = None

    def _load_dll(self):
        import ctypes

        self._dll = ctypes.WinDLL(self.dll_path)  # 仅 Windows
        # 以华视为例：初始化串口
        self._dll.CVR_Init_Comm(self.com_port)

    def read(self) -> Optional[GuestRecord]:
        if self._dll is None:
            self._load_dll()
        # 以华视为例：认证 + 读卡（各厂商函数名/返回值定义不同，按文档替换）
        if self._dll.CVR_Authenticate() not in (0, 1):
            raise RuntimeError("读卡器认证失败")
        if self._dll.CVR_Read_Content(self.com_port) not in (0, 1):
            raise RuntimeError("读卡失败，请重新放卡")
        # TODO: 按厂商文档解析文字（姓名/性别/民族/住址）与照片，
        #       填充并返回 GuestRecord
        raise NotImplementedError("请按读卡器 SDK 文档实现解析逻辑")


def make_reader(mode: str = "manual", dll_path: str = "", com_port: int = 1001):
    """工厂函数：mode = simulate | manual | sdk。"""
    if mode == "simulate":
        return SimulatedReader()
    if mode == "sdk" and dll_path:
        return SDKReader(dll_path, com_port)
    return ManualReader()
