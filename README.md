# TimePlanner

辅助式时间规划 agent —— 读你的 Obsidian 笔记 / ActivityWatch / 天气，出一天的 plan 草案，
**你确认才写日历**。独立 Python 应用，Obsidian 库只当只读数据源。

架构与分阶段计划见 [docs/](docs/)。当前进度：**Phase 0 / M0–M3（本地版）**——CLI + 四个模块 + agent
出 plan/reflect + **辅助式确认写入本地 timeline**（同 GCal schema），均已在真实数据上跑通。
GCal 后端待 OAuth；届时换存储后端即可，core/agent 一行不动。

## Next todo
1. timeline write back to obsidian daily note
2. add daily note template to current repo
3. correctly render time record timeline in `timeplanner summary`
4. read, parse, write cubox article to principles
5. read local health data(apple or other opensource)

## 快速开始

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .              # 只读功能
pip install -e '.[agent]'     # 想跑 plan/reflect（需 Claude Agent SDK）

cp .env.example .env          # 填库路径等（默认已指向本机）
timeplanner doctor            # 环境自检
timeplanner summary           # M1：笔记/AW/天气/日历 只读汇总
```

## 命令

| 命令 | 作用 | 依赖 |
|---|---|---|
| `timeplanner doctor` | 环境自检（库、AW、天气、GCal、SDK） | — |
| `timeplanner summary [--date]` | 四个只读信号汇总（零 agent、零写入） | — |
| `timeplanner plan [--date]` | 出今日 plan 草案 → stage 到待确认区 | Agent SDK |
| `timeplanner confirm [--date] [--yes]` | 辅助式闸门：预览 diff / `--yes` 写进本地 Plan timeline | — |
| `timeplanner log START END BUCKET SUMMARY...` | 录一条 Actual 事件（②自报层） | — |
| `timeplanner reflect [--date]` | 晚间复盘：①Plan vs ②Actual vs ③Observed | Agent SDK |
| `timeplanner memory [--clear]` | 看/清 planner 记忆（规划思考 + 候选原则） | — |

写入流程（辅助式）：`plan`（agent stage 草案）→ `confirm`（看 diff）→ `confirm --yes`（落地）。
本地 timeline 存在 `data/plan.json` / `data/actual.json`（gitignore），schema 与 GCal 事件一致。

### Planner 记忆（自进化雏形）

plan/reflect 时 agent 可用 `remember_thought` / `remember_principle` 把**规划思考**和\
**提炼的原则**落到 `.cache/memory.json`（gitignore）。下次开工先读回，planner 于是有连续性、\
会长经验。定位：`principles.md`（git 追踪）是你 curate 的源头真相，`memory.json` 是 agent 积累的\
候选，你觉得好的再手动并入 `principles.md`。

## 三层时间线

| 层 | 载体（当前 / 目标） | 含义 |
|---|---|---|
| ① Plan | 本地 `data/plan.json` → GCal「Plan」 | 你打算怎么过（planner 生成、你确认） |
| ② Actual | 本地 `data/actual.json` → GCal「Actual」 | 你说你实际怎么过（`log`） |
| ③ Observed | ActivityWatch | 机器观测（只读，交叉验证②） |

planner 写的事件都带标记 `extendedProperties.private={timeplanner:"1", bucket:...}`；
**没这个标的一律当外部固定约束**，planner 只在空隙里排块，永不覆盖。

## 目录

```
timeplanner/
├─ timeplanner/
│  ├─ config.py          # .env 配置
│  ├─ cli.py             # Phase 0 入口
│  ├─ agent.py           # Agent SDK loop + 系统 prompt + 工具注册
│  └─ core/              # 界面无关的大脑
│     ├─ notes.py        # 解析日记/project → workload
│     ├─ activitywatch.py# 本地 AW REST
│     ├─ weather.py      # Open-Meteo
│     └─ gcal.py         # Google Calendar 读写（标记 + dry-run）
├─ principles.md         # 原则库（agent 的价值观，从日记模板抽取）
└─ docs/                 # 需求 + 架构实施计划
```

## 存储后端：local / gcal

一个 env 切换写到哪，core/agent 一行不动（`timeline.py` 与 `gcal.py` 同 `Event` schema）：

```
TIMEPLANNER_BACKEND=local   # 默认，写本地 data/*.json
TIMEPLANNER_BACKEND=gcal    # 写真 Google 日历
```

staging（`plan` 的草案）永远本地；`confirm`/`log`/读取按后端路由。

### 配置 Google Calendar

1. Google Cloud Console → 建 OAuth 客户端（**Desktop app**）→ 下载 json → 路径填 `.env` 的 `GCAL_CREDENTIALS`
   - OAuth consent screen 里把自己加进 **Test users**（个人自用无需 Google 验证）
2. 建两个日历「Plan」「Actual」，各自 ID 填 `GCAL_PLAN_ID` / `GCAL_ACTUAL_ID`
3. 首次写操作弹浏览器授权，token 存 `token.json`（都已 gitignore）
4. 标记机制：planner 写的事件带 `timeplanner` 标；**没标的一律当外部固定约束**，
   planner 只在空隙排块、`confirm` 时只删自己写的块，**永不覆盖你手建的会议**

## 下一步（见 docs 里程碑）

- **M2** plan 草案：agent 出一天 timeline（不写日历）
- **M3** 确认写入：辅助式写 GCal + 读回外部事件
- **M4** reflect 闭环：takeaway 回写日记
- **Phase 1** Telegram bot + Docker 自启
