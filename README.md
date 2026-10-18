# HotelSync — 酒店前台信息自动分发系统

刷一次身份证，**PMS（网页）和公安旅店系统（Windows 客户端）同时录好**。
自包含的独立应用：Python 开发，recipe 驱动，换厂商只换配置不换代码。

```
┌──────────────────── 前台电脑 ───────────────────┐
│  业务员版 app（pms 角色）                         │
│  刷证/操作台 ──► PMS Agent（Playwright 网页）     │
└───────────────────────────┬─────────────────────┘
                            │ HTTP（局域网）
┌───────────────────────────▼─────────────────────┐
│              协调服务 Coordinator                  │
│  任务队列(SQLite) · 幂等去重 · 失败重试 · 待补录    │
└───────────────────────────┬─────────────────────┘
                            │ 本地直连
┌───────────────────────────▼─────────────────────┐
│              公安系统电脑                          │
│  业务员版 app（psb 角色）                          │
│  PSB Agent（pywinauto 驱动公安客户端）             │
└──────────────────────────────────────────────────┘

        ┌───────────────────────────────┐
        │  管理员电脑（仅管理员本人持有）  │
        │  管理员版：录制流程 + AI 映射    │
        │  → 导出模板文件发给业务员        │
        └───────────────────────────────┘
```

## 双版本设计（数据安全的核心）

| 发行包 | 入口 | 能力 | 分发给 |
|---|---|---|---|
| **业务员版** | `apps/operator_app.py` | 导入模板、运行流程、开房操作台、待补录。**不含录制、不含 AI** | 前台电脑 + 公安系统电脑 |
| **管理员版** | `apps/admin_app.py` | 录制流程、AI 字段映射、导出模板、本机调试、自检 | 仅管理员本人 |

**AI 大模型只在录制流程时由管理员使用。** 业务员日常工作的电脑上，
安装包里物理上不存在 AI 代码——三层保证：

1. **代码层**：`hotel_sync/ai`、`hotel_sync/capture` 两个包在 import 时
   调用 `require_admin()`，非管理员版进程一导入就报 `PermissionError`
2. **运行时层**：业务员版入口第一件事声明 `set_edition(OPERATOR)`，
   GUI 顶部实时显示「AI 映射模块未包含 ✓」徽标，可当场核验
3. **打包层**：`packaging/operator.spec` 的 `excludes` 显式排除这两个包，
   发行的 exe 里根本没有这些代码

自检命令（7 项全过即边界完好）：

```bash
python3 tests/test_editions.py
# 或打包后：HotelSync-管理员版.exe selftest
```

## 模板文件流转（管理员 → 业务员）

```
管理员：录制控件（capture，可叠 AI 建议）→ recipe.yaml
        → admin export → xxx.hstemplate ──内网拷贝/U盘──► 业务员：导入模板 → 启动运行
```

`.hstemplate` 是单个 JSON 文件，含：适用系统配方（pms/psb）+ 运行设置 +
**sha256 校验和**。业务员导入时校验，文件被改过一个字节都会拒绝，防止
配方被篡改后执行非预期操作。模板只描述「怎么操作界面」，不含任何客户数据。

## 已验证能力（`python3 run_demo.py`）

| 能力 | 状态 |
|---|---|
| 开房 / 增加同住人 / 换房 / 续住 / 退房 全流程 | ✅ 演示通过 |
| 一次操作同时分发 PMS + 公安两个系统 | ✅ |
| 同住人按房间逐个录入两个系统 | ✅ |
| 重复提交不重复录入（幂等） | ✅ |
| 上报失败 → 待补录清单 → 一键重试 | ✅ |
| agent 中途崩溃 → 僵死任务自动回收（5 分钟） | ✅ |
| 模板导出 → 导入往返 + 防篡改校验 | ✅ |
| 版本边界 7 项自检（AI/录制不进业务员版） | ✅ 全部通过 |

## 目录结构

```
hotel-sync/
├── run_demo.py                          # 一键演示（无需真实系统）
├── requirements.txt
├── apps/
│   ├── operator_app.py                  # 业务员版 GUI（打包分发）
│   └── admin_app.py                     # 管理员版 CLI（全能力）
├── tests/test_editions.py               # 版本边界 + 模板安全自检
├── packaging/
│   ├── operator.spec / admin.spec       # PyInstaller 双包配置
│   └── build.md                         # 打包发布指南
├── config/
│   ├── settings.yaml                    # 全局配置
│   ├── operator.example.yaml            # 业务员版运行配置（角色/地址/读卡器）
│   ├── admin.yaml                       # 管理员版专用（LLM 配置，仅管理员机存在）
│   └── recipes/
│       ├── pms_web.example.yaml         # PMS 网页 recipe 模板
│       └── psb_win.example.yaml         # 公安客户端 recipe 模板
└── hotel_sync/
    ├── editions.py                      # 版本边界（ADMIN / OPERATOR）
    ├── template.py                      # 模板文件（.hstemplate）导出/导入
    ├── core/                            # 数据模型 / SQLite 队列 / 编排
    ├── agents/                          # PMS 网页 + 公安客户端连接器
    ├── capture/capturer.py              # 人工捕获工具（仅管理员）
    ├── ai/mapper.py                     # AI 字段映射（仅管理员录制时用）
    ├── reader/idcard.py                 # 读卡器接入（模拟/手动/SDK）
    └── coordinator.py                   # 协调服务（跨机部署核心）
```

## 快速开始

```bash
cd hotel-sync
pip install -r requirements.txt
python3 run_demo.py        # 无需真实系统，看全流程 + 模板流转 + 版本边界
python3 tests/test_editions.py   # 安全边界自检
```

## 业务员日常使用（前台电脑 / 公安电脑）

1. 拿到管理员发的 `HotelSync-业务员版` 文件夹和 `.hstemplate` 模板文件
2. 双击 `HotelSync-业务员版.exe`
3. **导入模板** → 选本机角色（前台机 = PMS 执行 + 开房操作台；公安机 = 协调服务 + 公安执行）→ **启动运行**
4. 前台机：填房号 → 刷证自动带出客人 → 点「开房」（换房/续住/退房同理），
   PMS 自动填好，公安系统在另一台电脑上同步录好
5. 万一上报失败：右侧「待补录清单」会出现该条，处理完点「一键补录」

## 管理员工作流（自己的电脑）

```bash
# 1. 录制公安客户端流程（鼠标悬停拾取控件，--ai 叠加 AI 建议）
python -m apps.admin_app capture --title "旅店业" --out config/recipes/psb_win.yaml --ai

# 2. 对已 dump 的控件树单独跑 AI 映射（可选）
python -m apps.admin_app ai-map --controls controls_dump.json

# 3. 本机模拟试跑（不碰真实系统）
python -m apps.admin_app run --role psb --recipe config/recipes/psb_win.yaml --simulate

# 4. 导出模板分发给业务员
python -m apps.admin_app export --name "XX酒店-2026" \
    --pms config/recipes/pms_web.yaml --psb config/recipes/psb_win.yaml \
    --out dist/templates/xx酒店.hstemplate

# 5. 校验模板完整性
python -m apps.admin_app verify dist/templates/xx酒店.hstemplate
```

AI 映射环境变量（仅管理员机需要，见 `config/admin.yaml`）：
`HOTEL_SYNC_LLM_API_KEY` / `HOTEL_SYNC_LLM_BASE_URL` / `HOTEL_SYNC_LLM_MODEL`，
支持任意 OpenAI 兼容接口（DeepSeek / 通义 / GLM / 本地 vLLM）。

## 两台电脑部署拓扑

```
公安电脑：业务员版（psb 角色）= 协调服务 + 公安执行 + 待补录界面
前台电脑：业务员版（pms 角色）= PMS 执行 + 开房操作台，HTTP 连公安电脑
```

管理员用源码跑时也可不用 GUI：

```bash
# 公安电脑
python -m hotel_sync.coordinator --db hotel_sync.db --host 0.0.0.0 --port 8765
python -m hotel_sync.agents.psb_agent --recipe config/recipes/psb_win.yaml
# 前台电脑
python -m hotel_sync.agents.pms_agent --coordinator http://<公安机IP>:8765 \
    --recipe config/recipes/pms_web.yaml
```

> PMS 是前台已在用的浏览器时：Chrome 加 `--remote-debugging-port=9222` 启动，
> agent 加 `--browser-ws http://127.0.0.1:9222` 接管已登录会话。

## recipe 写法速查

recipe 是「标准步骤 + 定位信息」的声明式配置，两种系统同构：

```yaml
# 公安客户端（win32）
actions:
  check_in:
    steps:
      - {op: menu, control: menu_check_in}      # 点菜单
      - {op: fill, field: room_no}              # 填字段（值从动作里取）
      - op: guest_loop                          # 同住人循环
        add_guest: menu_add_guest               # 第 2 人起先点“增加同住人”
        fields: [name, gender, nation, id_number, address, check_in_time, check_out_time]
      - {op: click, control: btn_save}
      - {op: wait_success}                      # 等成功提示框
```

支持的 op：`menu / click / fill / guest_loop / wait_success / sleep`（win32）；
`goto / click / fill / select / guest_loop / wait / wait_success / sleep`（web）。
字段名写法：`room_no`、`action.old_room_no`、`action.new_check_out`、`guest.name` 等。

界面改版导致定位失效时：管理员重新捕获更新 recipe → 重新导出模板 → 业务员重新导入，
**代码全程不用动**。

## 读卡器接入

`config/operator.example.yaml` 里 `reader.mode` 三选一：

| 模式 | 说明 |
|---|---|
| `manual` | 手动输入身份证号+姓名（默认，无需硬件） |
| `simulate` | 模拟刷证（演示用） |
| `sdk` | 接入厂商 DLL，在 `reader/idcard.py` 的 `SDKReader` 里按 SDK 文档补解析逻辑 |

以华视 CVR-100U 为例的调用序列已写在 `SDKReader` 注释里
（`CVR_Init_Comm → CVR_Authenticate → CVR_Read_Content`），
精伦/神思等换成对应函数名即可，`read()` 返回 `GuestRecord` 的契约不变。

## 人证核验（以后要加时）

`config/settings.yaml` → `face_verify.mode`：

- `off`：不涉及
- `cooperate`：配合——流程中插入「等待核验通过」步骤，通过后再继续分发
- `skip`：互斥——检测到核验弹窗自动处理（**需先确认当地监管允许**）

## 失败兜底与补录

1. 任一步失败 → 任务进 `failed`，进入**待补录清单**
2. 业务员收到提醒（弹窗/声音）→ 人工处理
3. 一键重试全部或单条；重试仍失败会累加 `attempts`，超过 `retry.max_attempts` 需升级人工
4. agent 崩溃导致的僵死任务，5 分钟后自动回收重新排队

> 公安上报一般有时限要求（各地不同，常见入住后 30 分钟~2 小时），
> 补录动作要尽快；建议把待补录数量做成醒目提示。

## 打包发布

```bash
pip install -r requirements.txt pyinstaller
pyinstaller packaging/admin.spec --clean --noconfirm      # 管理员版（留自己电脑）
pyinstaller packaging/operator.spec --clean --noconfirm   # 业务员版（分发）
```

详细说明与核验方法见 `packaging/build.md`。

## 生产化路线（原型 → 上线）

1. 双包 PyInstaller 打包（见上）
2. 协调服务和业务员版设为开机自启（任务计划程序 / nssm）
3. 业务员版操作台可继续增强：房态可视化、批量导入团队客人、补录语音提醒
4. 监控：`GET /stats` 接钉钉/企微机器人推送；失败截图自动存盘
5. 多酒店：SQLite 已是持久化队列，多前台电脑各跑业务员版连同一协调服务即可

## 安全与合规提醒

- 客户信息属于敏感个人信息，遵守《个人信息保护法》：日志脱敏、传输仅限内网
- **业务员电脑无 AI 代码**：日常运行不调用任何大模型，客户数据不出内网
  （自检：`python3 tests/test_editions.py`）
- 协调服务端口只对局域网开放，不要暴露到公网
- 模板文件内网/U盘传递，不要走公网渠道
- 正式环境跑通前，先用 `--simulate` 与人工双轨并行 1~2 周

## 常见问题

**Q：PMS 有数据库/API，还用 RPA 吗？**
先查有没有接口——有就直接对接（最快最稳），RPA 只兜底纯 UI 的场景。

**Q：公安客户端有 USBKey 加密狗怎么办？**
Key 插在公安电脑上即可，agent 只操作界面不碰 Key；如果系统强制要求定期按键/输密码，
在 recipe 里加对应步骤或保留人工环节。

**Q：能支持第三、第四个系统吗？**
可以。仿照 `pms_agent.py` / `psb_agent.py` 写一个新 Connector（实现 `execute()`），
recipe 加一份，`Orchestrator(targets=[...])` 里加上目标名即可。

**Q：业务员误操作把系统搞乱怎么办？**
幂等保证同一次开房不会重复录入；每步失败有截图和日志；
模板带校验和，业务员改不动配方——改流程是管理员的专属动作。
