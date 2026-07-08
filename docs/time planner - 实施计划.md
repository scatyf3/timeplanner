---
tags:
  - 发展/sideproj
aliases: "#发展/sideproj"
---
# TimePlanner 架构与实施计划

> 需求本体见 [[time planner]]。本文是配套的架构 + 分阶段落地方案。
> 三条已定的基调：**辅助式**（agent 建议、你确认才写日历）、**先薄后厚**（先最小 demo 跑通功能再上常驻服务）、**独立应用**——自己的 repo + 运行时，完全不依赖 Claude workspace / Cowork。

## 一句话结论

做成**一个独立的 Python 应用**，核心是一个**和界面无关的大脑**（agent loop + 四个工具模块）——风险和价值都在 agent 逻辑（读笔记判 workload、读天气判出行、生成 timeline、辨认非 planner 事件），不在 UI。三步走，每一步都是这个应用自己在跑，不进 workspace：

1. **Phase 0 — CLI**：应用自带一个命令行入口 `timeplanner plan / reflect`，内部用 Claude Agent SDK 跑 agent loop，把全部功能验证掉（零前端）。
2. **Phase 1 — TG bot**：同一套核心包成常驻服务，Telegram 当聊天面（现成客户端、常驻、手机可用、内联按钮做确认闸门）。
3. **Phase 2 — Web 看板**：网页只做**只读可视化**（plan vs actual 时间线），不做聊天面。

> **关于「自建应用里和 agent 聊」**：很省。用 **Claude Agent SDK** 时，agent loop、工具调用、流式输出它都给你了，你只写工具（那四个模块）+ 一个 prompt。CLI 阶段连 UI 都不用写，直接终端对话；TG 阶段 Telegram 就是 UI。真要做网页聊天也不难（调 SDK/Messages，后端 ~100–150 行），但没必要——TG 更省。

## 系统模型：一个核心循环

```
Plan  →  Execute  →  Track  →  Reflect  →  Replan
 ↑                                            │
 └────────────────── 自进化 ──────────────────┘
```

**三层时间线**是这个系统的骨架——同构、可 diff、别互相污染：

| 层 | 载体 | 含义 | 来源 |
|---|---|---|---|
| ① Plan | GCal「Plan」日历 | 你**打算**怎么过 | planner 生成，你确认 |
| ② Actual（自报） | GCal「Actual」日历 | 你**说**你实际怎么过了 | planner 录入（`/log`） |
| ③ Observed（机器观测） | ActivityWatch | 你**真的**在电脑上干了啥 | 自动采集，只读 |

用**两个独立的 Google 日历**（不是同一日历里混事件）：颜色/可见性分开，查询互不干扰。①②都由 planner 写、都带 `timeplanner` 标记，schema 一致——于是 reflect 就是干净的 ①vs② 对比；③（ActivityWatch）用来**交叉验证**②（防止自报失真），甚至可以由观测到的 focus block 预填一个「实际」事件、你点确认即可。

另外两个数据源：**Obsidian 笔记** = workload 信号 + 原则库（reflect 时回写 takeaway）；**天气 API** = 出行/娱乐是否合适。

replan 的本质：让 ① 向 ②③ 的现实妥协。planner **永不覆盖**你手动建的会议/外部事件（靠下面的标记机制辨认）。

## 四个工作块

沿用你日记模板里已成型的哲学：**main（科研/deadline）· side（写代码/PhD 申请）· 生活 · 健身/娱乐**。planner 的工作就是每天把这四块摆进日历，且每块「到点物理硬停」。

## 两个较硬的子问题

**1. workload 自动推断（最有价值、也最需要迭代）**
v1 用最笨但可解释的规则起步，别一上来搞复杂模型：

- 扫当天/近期日记的未完成 TODO 数
- 扫 N 天内的 deadline（你已用 Tasks 插件的 `📅` 语法）
- 读 PeriodicPARA 的 project 进度快照
- → 映射成「今天安排几个 focus block」的一个简单函数，事后按实际完成度校准。

**2. 辨认「非 planner 写入的事件」**
planner 写日历时给事件打标：`extendedProperties.private = {timeplanner:"1", bucket:"main"}`。读回来时，**没这个标的一律当作外部固定约束**（会议、别人拉的活动），planner 只在它们的空隙里排自己的块。这条是整个「实时修改而不添乱」的地基。

## 原则库怎么来

不用从零写——你的**日记模板本身就是一套成文的时间管理原则**：每日独立预算、四问记分板、meta-work≠复盘、到点硬停、生活/身体≥1 单元、热天去学校地下健身房……第一版原则库直接从模板 + 若干篇 thino/blog 抽取，再逐步和 agent 对话补充。Cubox 里时间管理相关的高亮做二期迁移。

## 应用形态 / 仓库

**一个独立 git repo**（如 `~/timeplanner/`，**放在 Obsidian 库外**——它只把库当只读数据源，不污染笔记）。所有界面都是这个 repo 的薄壳，共用同一个核心：

```
timeplanner/
├─ core/                # 界面无关的大脑
│  ├─ agent.py          # Agent SDK loop + 系统 prompt
│  ├─ gcal.py           # Google Calendar 读写（Plan/Actual 两日历）
│  ├─ activitywatch.py  # 查本地 AW REST
│  ├─ notes.py          # 解析 Obsidian 日记/project → workload
│  └─ weather.py        # Open-Meteo 预报
├─ principles.md        # 原则库（agent 的“价值观”）
├─ cli.py               # Phase 0 入口：终端对话
├─ bot.py               # Phase 1 入口：TG bot
├─ web/                 # Phase 2：只读看板
├─ Dockerfile          # Phase 1 起打包，PC 开机自启
└─ config / .env        # OAuth token、TG token、库路径
```

换界面 = 换壳，`core/` 一行不用动。

## 分阶段架构

### Phase 0 — 独立 CLI 应用（约 1 天，零前端）

先把 `core/` 四个模块 + `principles.md` + `cli.py` 写出来。`cli.py` 内部用 **Agent SDK** 跑 agent loop，四个模块注册成工具。你在**自己的终端**里运行：

```
$ timeplanner plan      # 读笔记+AW+天气 → 出今日 plan 草案 → 你确认 → 写 GCal
$ timeplanner reflect   # 晚间复盘：①plan vs ②actual vs ③observed
```

- `gcal.py` — Google Calendar 读写（OAuth），带 `timeplanner` 标记与 dry-run
- `activitywatch.py` — 查本地 AW REST（`localhost:5600`），汇总专注 block / 分类时长
- `notes.py` — 解析日记（TODO、thino、预算记分板）+ project 快照 → workload 估计
- `weather.py` — Open-Meteo（免 key）取预报 → 出行/户外建议
- `principles.md` — 原则库

**跑通的闭环**：读笔记+AW+天气 → 生成一天的 plan 草案 → CLI 里展示、你确认 → 写入 GCal（带标记）。这一步就把你列的功能**全部验证**了，且完全辅助式——全程在你自己的应用里，不碰 workspace。

### Phase 1 — 常驻 + 实时（TG bot，大脑验证后再做）

把 `bot.py` 加上：一个长连接服务（Docker，PC 开机自启），复用 `core/` + Agent SDK。

- Bot 指令：`/log`（实时 record，写 Actual 日历）·`/plan`·`/replan`·`/reflect`·`/status`
- 内联按钮做**辅助式确认**：agent 给草案 → 你点「确认/改」→ 才落 GCal
- 定时任务：早间出 plan 草案、晚间 reflect、白天周期性比对 AW 漂移并提示 replan
- 补上 CLI 做不到的：**手机随时、常驻在线、主动推送**

### Phase 2 — 自进化 + 更多端

- 比对 GCal（计划）vs ActivityWatch（实际）→ 漂移检测 → 自动提 replan 草案
- reflect agent 把 takeaway 回写日记、更新原则库
- 按需再加 Obsidian 插件 / Web 看板，读同一个后端
- 后端从 PC Docker 迁云

## 技术栈

Python 全程。`google-api-python-client`（GCal）、`requests`（AW + Open-Meteo）、`python-telegram-bot`（P1）。状态先用 SQLite 或干脆 JSON/markdown。P1 用 Docker 打包。**agent 本质 = 一个 prompt 好的 Claude 调用 + 一组等于核心函数的工具**。

### agent 怎么调用（省钱口径）

两条路：

- **API key**：按 token 付费。用量很小或想脱离订阅时用。
- **复用 Claude Max 订阅**（推荐）：用 **Claude Agent SDK**（Python/TS）或 `claude -p` 无头模式驱动，走**订阅额度**，不额外按 token 计费、不用单独 key。单人低频场景，5 小时用量窗口足够。

选 SDK 还有额外好处：它自带 agent loop + 工具机制，**四个模块直接注册成工具**即可，Phase 0 就用它写、不用先 API 再迁。

> ⚠️ 变数：Anthropic 曾于 2026-06-15 宣布要把 Agent SDK 用量拆成单独按量计费，但生效前暂停，目前仍走订阅。若恢复则退回需 API key。参见 support.claude.com。

## 关键设计决策 / 风险

1. **单一真相**：GCal=意图，AW=现实，二者永不混写；外部事件只读不改。
2. **辅助式闸门**：v1 每次写日历前必过确认（CLI/TG 里给 diff，你点头）。
3. **workload 推断先简单**：规则可解释，事后按实际校准，别过早上模型。
4. **前置依赖**：ActivityWatch 必须装好且在跑；GCal OAuth 一次性授权。P0 先做环境自检。
5. **隐私/本地**：笔记 + AW 都是本地数据，服务先跑本地，别急着上云。
6. **别过度工程**：P0 能手动跑就不写服务；证明有用了再固化成 bot。

## 里程碑

- **M0** 环境：GCal OAuth 通、AW 在跑、仓库骨架
- **M1** 只读：`core/` 三个模块能各自输出笔记/AW/天气 summary（CLI 跑通）
- **M2** plan 草案：能出一天 timeline（不写日历）
- **M3** 确认写入：辅助式写 GCal（带标记）+ 读回外部事件
- **M4** reflect 闭环：晚间复盘 + takeaway 回写日记
- **→ Phase 1** TG bot 包壳、Docker 自启
- **→ Phase 2** 漂移检测 / 自进化 / 多端

## 下一步

我可以直接搭出这个**独立 repo** 的 **M0/M1**：`core/` 骨架 + 四个只读模块、`cli.py` 入口、从你日记模板抽第一版 `principles.md`、Agent SDK 接线。搭好后你在自己终端 `pip install -e .` 就能跑 `timeplanner plan`。

开始前只需你定两件事：① repo 放哪（默认 `~/timeplanner/`，库外）；② ActivityWatch 是否已装并在 `localhost:5600` 跑（没有的话 M1 先跳过 AW，用笔记+天气跑通）。
