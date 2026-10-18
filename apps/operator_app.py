"""业务员版应用（Operator Edition）—— 打包给前台/公安机使用。

安全边界（数据安全的核心承诺）：
    1. 本模块第一件事就是声明 set_edition(OPERATOR)；
    2. 只导入 core / agents / reader / template，
       【不导入也不包含】 hotel_sync.capture（录制）和 hotel_sync.ai（AI 映射）；
    3. 打包时这两个包被显式排除（packaging/operator.spec），
       业务员电脑上不存在 AI 代码，日常工作零 AI 调用；
    4. GUI 顶部实时显示安全状态徽标，可当场核验。

功能：
    - 导入管理员导出的流程模板（.hstemplate，带校验和防篡改）
    - 按角色运行：公安机（协调服务 + 公安执行）/ 前台机（PMS 执行 + 开房操作台）
    - 开房/加同住人/换房/续住/退房 操作台（前台机）
    - 待补录清单 + 一键补录（失败兜底）

运行（源码）：
    python apps/operator_app.py
运行（打包后）：
    双击 HotelSync-业务员版.exe
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ---- 第一件事：声明本进程为业务员版（此后任何 AI/录制能力都会拒绝执行）----
import hotel_sync.editions as editions  # noqa: E402

editions.set_edition(editions.Edition.OPERATOR)

import tkinter as tk  # noqa: E402
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk  # noqa: E402

import yaml  # noqa: E402

from hotel_sync.agents.pms_agent import PMSAgent  # noqa: E402
from hotel_sync.agents.psb_agent import PSBAgent  # noqa: E402
from hotel_sync.core.client import HttpClient, LocalClient  # noqa: E402
from hotel_sync.core.models import GuestRecord, StayAction  # noqa: E402
from hotel_sync.core.queue import TaskQueue  # noqa: E402
from hotel_sync.reader.idcard import make_reader  # noqa: E402
from hotel_sync.template import describe_template, import_template, runtime_dir  # noqa: E402

CONFIG_PATH = os.path.join(_ROOT, "config", "operator.yaml")
CONFIG_EXAMPLE = os.path.join(_ROOT, "config", "operator.example.yaml")


# ------------------------------------------------------------ 配置


def _load_config() -> dict:
    path = CONFIG_PATH if os.path.exists(CONFIG_PATH) else CONFIG_EXAMPLE
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


# ------------------------------------------------------------ 主应用


class OperatorApp:
    ROLE_PSB = "psb"
    ROLE_PMS = "pms"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = _load_config()
        self.client = None
        self.agents = []
        self.running = False
        self.recipes: dict[str, dict] = {}
        self.template_bundle = None
        self.guests: list[GuestRecord] = []
        self.reader = None
        self.pending_data: list[dict] = []
        self._log_queue: list[str] = []
        self._log_lock = threading.Lock()

        self._build_ui()
        self._load_saved_template()
        self._refresh_security_badge()
        self.root.after(1000, self._periodic_refresh)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ UI 构建

    def _build_ui(self) -> None:
        self.root.title("HotelSync 业务员版")
        self.root.geometry("1020x700")
        self.root.minsize(920, 620)

        # 顶部：标题 + 安全徽标
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill=tk.X)
        ttk.Label(top, text="HotelSync 业务员版", font=("", 14, "bold")).pack(side=tk.LEFT)
        self.badge_label = ttk.Label(top, text="", font=("", 10))
        self.badge_label.pack(side=tk.RIGHT)

        # 中部：左右两栏
        mid = ttk.Frame(self.root, padding=4)
        mid.pack(fill=tk.BOTH, expand=True)
        mid.columnconfigure(0, weight=5)
        mid.columnconfigure(1, weight=4)

        self._build_console_panel(mid)   # 左：开房操作台（前台机）
        self._build_control_panel(mid)   # 右：运行控制

        # 底部：日志
        bottom = ttk.Frame(self.root, padding=4)
        bottom.pack(fill=tk.BOTH, expand=False)
        ttk.Label(bottom, text="运行日志").pack(anchor=tk.W)
        self.log_text = scrolledtext.ScrolledText(bottom, height=10, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_console_panel(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="开房操作台（仅前台机角色可用）", padding=8)
        frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="房号：").pack(side=tk.LEFT)
        self.room_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.room_var, width=12).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="（刷证自动添加客人 → 点“开房”）").pack(side=tk.LEFT)

        ttk.Label(frame, text="客人列表：").pack(anchor=tk.W, pady=(6, 0))
        self.guest_list = tk.Listbox(frame, height=8)
        self.guest_list.pack(fill=tk.BOTH, expand=True)

        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, pady=4)
        ttk.Button(row2, text="手动添加客人", command=self._manual_add_guest).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="清空", command=self._clear_guests).pack(side=tk.LEFT, padx=2)
        self.reader_label = ttk.Label(row2, text="读卡器：未启动")
        self.reader_label.pack(side=tk.RIGHT)

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Button(row3, text="开房", command=lambda: self._do_action("check_in")).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        ttk.Button(row3, text="加同住人", command=lambda: self._do_action("add_guest")).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        ttk.Button(row3, text="换房", command=lambda: self._do_action("change_room")).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        ttk.Button(row3, text="续住", command=lambda: self._do_action("extend")).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)
        ttk.Button(row3, text="退房", command=lambda: self._do_action("check_out")).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

    def _build_control_panel(self, parent) -> None:
        frame = ttk.Frame(parent, padding=4)
        frame.grid(row=0, column=1, sticky="nsew", padx=4, pady=4)

        # 角色
        role_box = ttk.LabelFrame(frame, text="本机角色", padding=6)
        role_box.pack(fill=tk.X, pady=2)
        self.role_var = tk.StringVar(value=self.cfg.get("role", "psb"))
        ttk.Radiobutton(
            role_box, text="公安机（协调服务 + 公安系统执行）",
            variable=self.role_var, value="psb", command=self._on_role_change,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            role_box, text="前台机（PMS 执行 + 开房操作台）",
            variable=self.role_var, value="pms", command=self._on_role_change,
        ).pack(anchor=tk.W)

        # 协调服务
        coord_box = ttk.LabelFrame(frame, text="协调服务", padding=6)
        coord_box.pack(fill=tk.X, pady=2)
        ttk.Label(coord_box, text="公安机地址（前台机填）：").pack(anchor=tk.W)
        self.coord_var = tk.StringVar(value=self.cfg.get("coordinator_url", "http://127.0.0.1:8765"))
        ttk.Entry(coord_box, textvariable=self.coord_var).pack(fill=tk.X)
        bind_row = ttk.Frame(coord_box)
        bind_row.pack(fill=tk.X, pady=2)
        ttk.Label(bind_row, text="本机绑定（公安机）：").pack(side=tk.LEFT)
        self.bind_var = tk.StringVar(value=self.cfg.get("coordinator_bind", "0.0.0.0"))
        ttk.Entry(bind_row, textvariable=self.bind_var, width=14).pack(side=tk.LEFT, padx=2)
        ttk.Label(bind_row, text="端口：").pack(side=tk.LEFT)
        self.port_var = tk.StringVar(value=str(self.cfg.get("coordinator_port", 8765)))
        ttk.Entry(bind_row, textvariable=self.port_var, width=8).pack(side=tk.LEFT)

        # 模板
        tpl_box = ttk.LabelFrame(frame, text="流程模板（管理员导出）", padding=6)
        tpl_box.pack(fill=tk.X, pady=2)
        ttk.Button(tpl_box, text="导入模板文件…", command=self._import_template).pack(fill=tk.X)
        self.template_label = ttk.Label(
            tpl_box, text="未导入模板", foreground="gray", wraplength=340
        )
        self.template_label.pack(fill=tk.X, pady=2)

        # 启停
        run_box = ttk.Frame(frame)
        run_box.pack(fill=tk.X, pady=4)
        self.start_btn = ttk.Button(run_box, text="启动运行", command=self._start)
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.stop_btn = ttk.Button(run_box, text="停止", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.status_label = ttk.Label(frame, text="状态：未启动", foreground="gray")
        self.status_label.pack(fill=tk.X)

        # 待补录
        pending_box = ttk.LabelFrame(frame, text="待补录清单（上报失败项）", padding=6)
        pending_box.pack(fill=tk.BOTH, expand=True, pady=2)
        self.pending_list = tk.Listbox(pending_box, height=6)
        self.pending_list.pack(fill=tk.BOTH, expand=True)
        btn_row = ttk.Frame(pending_box)
        btn_row.pack(fill=tk.X, pady=2)
        ttk.Button(btn_row, text="刷新", command=self._refresh_pending).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="一键补录（全部重试）", command=self._retry_all).pack(side=tk.LEFT, padx=2)
        self.pending_count_label = ttk.Label(btn_row, text="0 条")
        self.pending_count_label.pack(side=tk.RIGHT)

    # ------------------------------------------------------------ 安全徽标

    def _refresh_security_badge(self) -> None:
        ai_loaded = any(m.startswith("hotel_sync.ai") for m in sys.modules)
        cap_loaded = any(m.startswith("hotel_sync.capture") for m in sys.modules)
        if ai_loaded or cap_loaded:
            self.badge_label.config(
                text="安全状态：异常！检测到 AI/录制模块已加载", foreground="red"
            )
        else:
            self.badge_label.config(
                text="安全状态：AI 映射模块未包含 ✓｜录制模块未包含 ✓｜日常零 AI 调用",
                foreground="green",
            )

    # ------------------------------------------------------------ 模板

    def _load_saved_template(self) -> None:
        path = self.cfg.get("template_path", "")
        if path and os.path.exists(path):
            try:
                self._apply_template(import_template(path))
                self._log(f"已自动加载本机模板：{path}")
            except Exception as exc:
                self.template_label.config(text=f"模板加载失败：{exc}", foreground="red")

    def _import_template(self) -> None:
        path = filedialog.askopenfilename(
            title="选择流程模板文件",
            filetypes=[("HotelSync 模板", f"*{'.hstemplate'}"), ("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            bundle = import_template(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        self._apply_template(bundle)
        self.cfg["template_path"] = path
        _save_config(self.cfg)
        messagebox.showinfo(
            "导入成功",
            f"{describe_template(bundle)}\n\n模板已保存到本机配置，下次启动自动加载。",
        )

    def _apply_template(self, bundle) -> None:
        self.template_bundle = bundle
        self.recipes = bundle.load_recipes()
        self.template_label.config(
            text=f"已导入：{bundle.name}（适用系统：{', '.join(bundle.targets)}）",
            foreground="green",
        )
        self._log(f"模板导入成功：{bundle.name}，目标系统 {bundle.targets}")

    # ------------------------------------------------------------ 启动 / 停止

    def _on_role_change(self) -> None:
        self.cfg["role"] = self.role_var.get()
        _save_config(self.cfg)

    def _start(self) -> None:
        if self.running:
            return
        role = self.role_var.get()
        if not self.recipes:
            messagebox.showwarning("无法启动", "请先导入流程模板文件")
            return
        if role not in self.recipes:
            messagebox.showwarning(
                "无法启动", f"模板中不含「{role}」角色的配方（现有：{list(self.recipes)}）"
            )
            return

        try:
            if role == self.ROLE_PSB:
                db_path = str(runtime_dir() / "hotel_sync.db")
                self.client = LocalClient(TaskQueue(db_path))
                self._start_coordinator(db_path)
                agent = PSBAgent(self.client, self.recipes["psb"], simulate=False)
            else:
                self.client = HttpClient(self.coord_var.get().strip())
                agent = PMSAgent(
                    self.client, self.recipes["pms"], simulate=False,
                    browser_ws=self.cfg.get("pms", {}).get("browser_ws") or None,
                )
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))
            traceback.print_exc()
            return

        agent.on_log = self._enqueue_log
        self.agents = [agent]
        thread = threading.Thread(target=agent.run_forever, kwargs={"interval": 2}, daemon=True)
        thread.start()
        self.threads = [thread]

        if role == self.ROLE_PMS:
            self._start_reader()

        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_label.config(text=f"状态：运行中（角色：{role}）", foreground="green")
        self._log(f"已启动，角色：{role}")

    def _stop(self) -> None:
        self.running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="状态：已停止", foreground="gray")
        self._log("已停止（线程为守护线程，随退出结束）")

    def _start_coordinator(self, db_path: str) -> None:
        import uvicorn

        from hotel_sync.coordinator import create_app

        host = self.bind_var.get().strip() or "0.0.0.0"
        port = int(self.port_var.get().strip() or 8765)

        def run() -> None:
            config = uvicorn.Config(
                create_app(db_path), host=host, port=port, log_level="warning"
            )
            server = uvicorn.Server(config)
            self._enqueue_log(f"协调服务启动于 {host}:{port}")
            server.run()

        threading.Thread(target=run, daemon=True).start()

    def _start_reader(self) -> None:
        reader_cfg = self.cfg.get("reader", {})
        mode = reader_cfg.get("mode", "manual")
        if mode == "manual":
            self.reader_label.config(text="读卡器：手动模式（用左侧按钮添加客人）")
            return
        try:
            self.reader = make_reader(
                mode,
                dll_path=reader_cfg.get("sdk_dll", ""),
                com_port=int(reader_cfg.get("com_port", 1001)),
            )
        except Exception as exc:
            self.reader_label.config(text=f"读卡器启动失败：{exc}")
            return

        def loop() -> None:
            while self.running:
                try:
                    guest = self.reader.read()
                    if guest:
                        self.root.after(0, self._add_guest, guest)
                    else:
                        time.sleep(0.5)
                except Exception as exc:
                    self._enqueue_log(f"读卡器异常：{exc}")
                    time.sleep(2)

        threading.Thread(target=loop, daemon=True).start()
        self.reader_label.config(text=f"读卡器：{mode} 模式，监听中")

    # ------------------------------------------------------------ 操作台动作

    def _add_guest(self, guest: GuestRecord) -> None:
        self.guests.append(guest)
        self._refresh_guest_list()

    def _manual_add_guest(self) -> None:
        id_number = simpledialog.askstring("手动添加客人", "身份证号：", parent=self.root)
        if not id_number:
            return
        name = simpledialog.askstring("手动添加客人", "姓名：", parent=self.root) or ""
        nation = simpledialog.askstring("手动添加客人", "民族（回车默认“汉”）：", parent=self.root) or "汉"
        self._add_guest(GuestRecord(name=name.strip(), nation=nation.strip(), id_number=id_number.strip()))

    def _clear_guests(self) -> None:
        self.guests.clear()
        self._refresh_guest_list()

    def _refresh_guest_list(self) -> None:
        self.guest_list.delete(0, tk.END)
        for g in self.guests:
            self.guest_list.insert(tk.END, f"{g.name}｜{g.id_number}｜{g.nation}")

    def _do_action(self, action_type: str) -> None:
        if not self.running or not self.client:
            messagebox.showwarning("未启动", "请先导入模板并点击“启动运行”")
            return
        room = self.room_var.get().strip()
        if action_type in ("check_in", "add_guest") and not self.guests:
            messagebox.showwarning("缺少客人", "请先刷证或手动添加客人")
            return
        if not room:
            messagebox.showwarning("缺少房号", "请先填写房号")
            return

        guests = list(self.guests)
        if action_type == "check_in":
            action = StayAction(action_type="check_in", room_no=room, guests=guests)
            self._clear_guests()
        elif action_type == "add_guest":
            action = StayAction(action_type="add_guest", room_no=room, guests=guests)
            self._clear_guests()
        elif action_type == "change_room":
            new_room = simpledialog.askstring("换房", "新房号：", parent=self.root)
            if not new_room:
                return
            action = StayAction(
                action_type="change_room", room_no=new_room.strip(), old_room_no=room
            )
            self.room_var.set(new_room.strip())
        elif action_type == "extend":
            new_time = simpledialog.askstring(
                "续住", "新退房时间（如 2026-10-18 12:00）：", parent=self.root
            )
            if not new_time:
                return
            action = StayAction(
                action_type="extend", room_no=room, new_check_out=new_time.strip()
            )
        else:  # check_out
            action = StayAction(action_type="check_out", room_no=room)

        try:
            self.client.submit(action, ["pms", "psb"])
            self._log(
                f"已提交：{action.label()}｜房间 {action.room_no}｜客人 {len(action.guests)} 人"
            )
        except Exception as exc:
            messagebox.showerror("提交失败", f"无法连接协调服务：{exc}")

    # ------------------------------------------------------------ 待补录

    def _refresh_pending(self) -> None:
        if not self.client:
            return
        try:
            self.pending_data = self.client.pending()
        except Exception:
            return
        self.pending_list.delete(0, tk.END)
        for t in self.pending_data:
            room = t.get("payload", {}).get("room_no", "?")
            self.pending_list.insert(
                tk.END, f"[{t['target']}] {t['action_type']} 房{room} - {t['last_error'][:36]}"
            )
        self.pending_count_label.config(text=f"{len(self.pending_data)} 条")

    def _retry_all(self) -> None:
        if not self.client:
            return
        n = 0
        for t in self.pending_data:
            try:
                if self.client.retry(t["task_id"]):
                    n += 1
            except Exception:
                pass
        self._log(f"一键补录：已重新入队 {n} 条")
        self._refresh_pending()

    # ------------------------------------------------------------ 日志 / 周期

    def _enqueue_log(self, line: str) -> None:
        with self._log_lock:
            self._log_queue.append(line)

    def _pump_log(self) -> None:
        with self._log_lock:
            lines, self._log_queue = self._log_queue, []
        if lines:
            self.log_text.config(state=tk.NORMAL)
            for line in lines:
                self.log_text.insert(tk.END, line + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

    def _periodic_refresh(self) -> None:
        self._pump_log()
        self._refresh_pending()
        self.root.after(2000, self._periodic_refresh)

    def _on_close(self) -> None:
        self.running = False
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    OperatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
