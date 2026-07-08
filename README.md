# TimePlanner

辅助式时间规划 agent —— 读你的 Obsidian 笔记 / ActivityWatch / 天气，出一天的 plan 草案，
**你确认才写日历**。独立 Python 应用，Obsidian 库只当只读数据源。

架构与分阶段计划见 [docs/](docs/)。当前进度：**Phase 0 / M0–M2**——CLI + 四个模块 + agent 出 plan/reflect 草案，均已在真实数据上跑通。M3（确认写入 GCal）待 OAuth 配置。

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
| `timeplanner plan [--date]` | 出今日 plan 草案 → 你确认才写 GCal | Agent SDK |
| `timeplanner reflect [--date]` | 晚间复盘：①Plan vs ②Actual vs ③Observed | Agent SDK |

## 三层时间线

| 层 | 载体 | 含义 |
|---|---|---|
| ① Plan | GCal「Plan」日历 | 你打算怎么过（planner 生成、你确认） |
| ② Actual | GCal「Actual」日历 | 你说你实际怎么过（`/log`） |
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

## 配置 Google Calendar（M3 才需要）

1. Google Cloud Console → 建 OAuth 客户端（桌面应用）→ 下载 json → 存为 `credentials.json`
2. GCal 里建两个日历「Plan」「Actual」，把各自 ID 填进 `.env` 的 `GCAL_PLAN_ID` / `GCAL_ACTUAL_ID`
3. 首次跑写操作会弹浏览器授权，token 存 `token.json`（都已 gitignore）

## 下一步（见 docs 里程碑）

- **M2** plan 草案：agent 出一天 timeline（不写日历）
- **M3** 确认写入：辅助式写 GCal + 读回外部事件
- **M4** reflect 闭环：takeaway 回写日记
- **Phase 1** Telegram bot + Docker 自启
