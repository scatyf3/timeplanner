"""Phase 0 entry point: terminal conversation.

    timeplanner summary [--date YYYY-MM-DD]   # M1: read-only signals + local timeline
    timeplanner doctor                        # environment self-check (M0)
    timeplanner plan    [--date ...]          # produce today's plan draft and stage it (needs agent SDK)
    timeplanner confirm [--date] [--yes]      # assisted confirm: write the proposal into the local Plan timeline
    timeplanner log START END BUCKET SUMMARY  # record one Actual event (layer ②, self-report)
    timeplanner reflect [--date ...]          # evening review (needs agent SDK)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from .config import config
from .core import activitywatch, aw_sync, backend, cubox, gcal, memory, notes, rollover, weather


def _date(s: str | None) -> dt.date:
    return dt.date.fromisoformat(s) if s else dt.date.today()


def _now_hint(d: dt.date) -> str:
    """When d is today, give the agent the exact current time (otherwise it guesses from AW and other clues)."""
    now = dt.datetime.now().astimezone()
    if d == now.date():
        wd = "一二三四五六日"[now.weekday()]
        return f"现在是周{wd} {now:%H:%M}（{now:%Z}）。"
    return ""


def cmd_doctor(_args) -> int:
    """Environment self-check: vault, AW, weather, GCal, agent SDK."""
    print("# 🩺 TimePlanner 环境自检\n")
    ok = True

    if config.vault_ok():
        print(f"✅ Obsidian 库：{config.vault}")
    else:
        print(f"❌ Obsidian 库不存在：{config.vault}（在 .env 里设 TIMEPLANNER_VAULT）")
        ok = False

    if activitywatch.is_up():
        print(f"✅ ActivityWatch 在跑：{config.aw_host}")
    else:
        print(f"⚠️  ActivityWatch 未响应：{config.aw_host}（M1 可先跳过 AW）")

    w = weather.fetch()
    print(f"✅ 天气 API 可用：{w.desc} {w.t_max:.0f}°C" if w.available
          else f"⚠️  天气 API：{w.note}")

    print("✅ GCal 已配置 OAuth" if gcal.is_configured()
          else "⚠️  GCal 未配置（见 README）")
    print(f"🎯 存储后端：{backend.name()}"
          + ("（写真日历）" if backend.name() == "gcal" else "（写本地 data/*.json）"))

    try:
        import claude_agent_sdk  # noqa: F401
        print("✅ Claude Agent SDK 已安装（plan/reflect 可用）")
    except ImportError:
        print("⚠️  Claude Agent SDK 未安装：pip install -e '.[agent]'（summary 不需要它）")

    print("\n结论：M1 只读闭环" + ("可跑通 ✅" if ok else "缺库路径 ❌"))
    return 0 if ok else 1


def cmd_summary(args) -> int:
    """M1: read-only module summary + current backend's timeline (Plan/Actual). No agent, no writes."""
    d = _date(args.date)
    if not args.plain:
        try:
            from . import render
            render.render(d)
            return 0
        except ImportError:
            pass  # rich missing → fall back to plain text
    print(notes.summary(d))
    print("\n" + aw_sync.summary(d))
    print("\n" + weather.summary(d))
    print("\n" + backend.summary(d, "plan"))
    print("\n" + backend.summary(d, "actual"))
    refs = gcal.refs_summary(d)
    if refs:
        print("\n" + refs)
    return 0


def cmd_plan(args) -> int:
    from . import agent
    d = _date(args.date)
    mem = memory.context_block()
    prompt = (f"今天是 {d:%Y-%m-%d}。{_now_hint(d)}请先用工具读 notes/AW/天气/现有 timeline，"
              "然后给我一份今日 plan 草案（timeline + 专注 block 数 + 理由），"
              "从现在这个时刻往后排，别排已经过去的时段；"
              "并**调用 stage_plan** 把草案 stage 起来，提示我跑 `timeplanner confirm` 确认。"
              "\n\n**结束前必须调用 remember_thought**，一句话记下今天的关键取舍/判断"
              "（即使还在等我确认），好让下次开工有连续性。")
    if mem:
        prompt += f"\n\n{mem}"
    asyncio.run(agent.run_interactive(prompt))
    return 0


def cmd_confirm(args) -> int:
    """Assisted gate: write the day's staged plan proposal into the current backend's Plan. Defaults to dry-run, --yes commits it."""
    d = _date(args.date)
    print(backend.confirm(d, dry_run=not args.yes))
    return 0


def cmd_log(args) -> int:
    """Record one Actual event (layer ②, self-report) to the current backend."""
    d = _date(args.date)
    e = backend.log_actual(d, args.start, args.end, args.bucket, " ".join(args.summary))
    print(f"📝 已录入 Actual（{backend.name()}）：{e.line()}")
    return 0


def cmd_reflect(args) -> int:
    from . import agent
    d = _date(args.date)
    mem = memory.context_block()
    prompt = (f"今天是 {d:%Y-%m-%d}，晚间复盘。{_now_hint(d)}请用 timeline_read 读 ①Plan ②Actual，"
              "配合 ③Observed(AW)，走「收工 4 问」记分板，给我一行 takeaway 建议。"
              "\n\n**结束前必须调用 remember_thought** 记下今天的 takeaway/观察；"
              "若这条经验能推广成一条可复用的时间管理原则，再调 remember_principle。")
    if mem:
        prompt += f"\n\n{mem}"
    asyncio.run(agent.run(prompt))
    return 0


def cmd_weekly(args) -> int:
    """Weekly review: aggregate the week's Time-record time into projects (agent), preview a write-back to the Weekly note."""
    from . import agent
    from .core import weekly
    d = weekly.target_week(args.date)
    mon, sun = weekly.week_bounds(d)
    iso = d.isocalendar()
    if not args.plain:
        try:
            from . import render
            render.render_weekly(d)
        except ImportError:
            pass  # rich missing → skip the bucket-hours chart, agent narration still runs
    mem = memory.context_block()
    prompt = (f"本周回顾：{iso.year}-W{iso.week:02d}（{mon:%Y-%m-%d} 周一 ~ {sun:%Y-%m-%d} 周日）。{_now_hint(dt.date.today())}"
              f"请先调用 weekly_context（date 传 {mon:%Y-%m-%d}）读这一周的 Time record 时间/项目名册/完成情况，"
              "把时间按名册归到各 project，逐项目给『X 小时 — 这周做了啥』，"
              f"然后调用 stage_weekly（date 同样传 {mon:%Y-%m-%d}）暂存，提示我跑 `timeplanner confirm-weekly` 落地。")
    if mem:
        prompt += f"\n\n{mem}"

    def _show_allocation():
        """After each round, echo the freshly re-staged per-project allocation (agent re-stages when you nudge it)."""
        try:
            from . import render
            render.render_weekly_allocation(d)
        except ImportError:
            body = weekly.staged(d)
            if body:
                print("\n📊 当前项目时间分配（已暂存，最新一轮）：\n" + body)

    asyncio.run(agent.run_interactive(
        prompt,
        on_round=_show_allocation,
        input_hint="\n💬 补充/调整后再排一轮（直接回车结束；结束后 tp confirm-weekly 落地）> ",
        closing="（已结束。最新汇总已 stage —— 跑 `timeplanner confirm-weekly` 预览、`--yes` 落地。）",
    ))
    return 0


def cmd_confirm_weekly(args) -> int:
    """Assisted gate: write the staged weekly rollup into the Obsidian Weekly note. Defaults to dry-run, --yes commits."""
    from .core import weekly
    d = weekly.target_week(args.date)
    pending = [(k, weekly.staged(d, k)) for k in weekly.KINDS]
    pending = [(k, b) for k, b in pending if b]
    if not pending:
        iso = d.isocalendar()
        print(f"（{iso.year}-W{iso.week:02d} 没有待确认的周汇总；先跑 `timeplanner weekly`。）")
        return 1
    for k, b in pending:            # review first, then insights (append order → review above insights)
        print(weekly.write_back(d, b, dry_run=not args.yes, kind=k))
        if args.yes:
            weekly.clear_staged(d, k)
    return 0


def cmd_confirm_daily(args) -> int:
    """Assisted gate: write a day's Actual timeline into its Obsidian Daily note. Defaults to dry-run, --yes commits."""
    from .core import daily_wb
    print(daily_wb.write_back(_date(args.date), dry_run=not args.yes))
    return 0


def cmd_reclassify(args) -> int:
    """Re-bucket our own logged Actual events by keyword-classifying their summaries. Defaults to dry-run."""
    d = _date(args.date)
    print(backend.reclassify(d, dry_run=not args.yes))
    return 0


def cmd_tui(args) -> int:
    """Launch the interactive day dashboard (read-only). Textual is a soft dependency."""
    d = _date(args.date)
    try:
        from . import tui
    except ImportError:
        print("⚠️ 未安装 Textual。装它才能跑仪表盘：\n    pip install -e '.[tui]'\n"
              "（只读汇总用 `timeplanner summary`，不需要它。）")
        return 1
    tui.run(d)
    return 0


def cmd_observations(args) -> int:
    """View or keyword-search the observation library (accumulated distilled observations)."""
    from .core import observations
    if args.search:
        print(observations.search_summary(" ".join(args.search)))
    else:
        print(observations.render())
    return 0


def cmd_cubox(args) -> int:
    """Sync the configured Cubox folders into the local corpus (agent search/reflect material)."""
    full = not args.no_full
    print(f"⏬ 同步 Cubox 文件夹 {config.cubox_folders}（{'含全文' if full else '仅高亮'}）…")
    st = cubox.sync(full=full)
    tail = f"、约 {st['chars']//1000}k 字全文" if full else ""
    print(f"✅ {st['cards']} 篇、{st['highlights']} 条高亮{tail}。各文件夹：{st['folders']}")
    return 0


def cmd_aw_export(args) -> int:
    """Export this machine's local AW observation to the shared snapshot folder (for cross-machine merge)."""
    if not config.aw_sync_dir:
        print("（未配置 TIMEPLANNER_AW_SYNC_DIR —— 见 .env.example / README 的跨机 AW 同步）")
        return 1
    dates = [_date(args.date)] if args.date else [dt.date.today(), dt.date.today() - dt.timedelta(days=1)]
    n = 0
    for d in dates:
        p = aw_sync.export_day(d)
        if p:
            print(f"📤 已导出 {d:%Y-%m-%d} 的 AW 快照 → {p.name}")
            n += 1
    if not n:
        print("（本机 AW 无数据可导出，跳过）")
    return 0


def cmd_calendars(args) -> int:
    """List every calendar in the Google account, so you can pick IDs for TIMEPLANNER_GCAL_REFS."""
    if not gcal.is_configured():
        print("⚠️  未配置 Google OAuth —— 见 README（GCAL_CREDENTIALS）。")
        return 1
    try:
        cals = gcal.list_calendars()
    except gcal.GCalNotConfigured as e:
        print(f"⚠️  {e}")
        return 1
    except Exception as e:  # noqa: BLE001 — network/auth errors shouldn't traceback
        print(f"⚠️  读日历列表出错：{e}")
        return 1
    refs = set(config.gcal_ref_ids)
    print(f"📚 账号里的日历（共 {len(cals)} 个）。把想只读的日历 ID 填进 .env 的 TIMEPLANNER_GCAL_REFS：\n")
    for c in cals:
        flags = []
        if c["primary"]:
            flags.append("primary")
        if c["id"] in refs:
            flags.append("✓ 已在 refs")
        tail = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  • {c['name']}  ({c['access']}){tail}\n    {c['id']}")
    return 0


def cmd_memory(args) -> int:
    """View / clear the planner memory cache (thoughts + candidate principles)."""
    if args.clear:
        memory.clear()
        print("🧹 已清空 planner 记忆缓存。")
        return 0
    print(memory.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="timeplanner", description="辅助式时间规划 agent")
    sub = p.add_subparsers(dest="cmd")

    for name, help_ in [("doctor", "环境自检"), ("summary", "只读信号汇总（M1）"),
                        ("plan", "出今日 plan 草案"), ("reflect", "晚间复盘")]:
        sp = sub.add_parser(name, help=help_)
        if name != "doctor":
            sp.add_argument("--date", help="YYYY-MM-DD，默认今天")
        if name == "summary":
            sp.add_argument("--plain", action="store_true", help="纯文本输出（不用 rich 渲染，便于管道/重定向）")

    mp = sub.add_parser("memory", help="planner 记忆缓存（思考 + 候选原则）")
    mp.add_argument("--clear", action="store_true", help="清空记忆")

    ob = sub.add_parser("observations", help="看/搜 observation 库（累积的提炼观察）")
    ob.add_argument("--search", nargs="+", help="按关键词召回（空格分词）")

    tp = sub.add_parser("tui", help="交互式终端仪表盘（timeline + 时间占比 + 实时时钟 + 记活动）")
    tp.add_argument("--date", help="YYYY-MM-DD，默认今天")

    rc = sub.add_parser("reclassify", help="按描述重判 bucket、给记过的 Actual 事件重新上色")
    rc.add_argument("--date", help="YYYY-MM-DD，默认今天")
    rc.add_argument("--yes", action="store_true", help="真改（默认 dry-run 只预览）")

    cb = sub.add_parser("cubox", help="同步配置的 Cubox 文件夹到本地语料（供 agent 搜索/复盘）")
    cb.add_argument("--no-full", action="store_true", help="只拉高亮、不拉全文（更快）")

    ax = sub.add_parser("aw-export", help="导出本机 AW 观测快照到共享文件夹（跨机同步，配 Syncthing）")
    ax.add_argument("--date", help="YYYY-MM-DD，默认今天+昨天")

    sub.add_parser("calendars", help="列出账号里所有日历的 ID（用于填 TIMEPLANNER_GCAL_REFS）")

    cf = sub.add_parser("confirm", help="确认写入本地 Plan timeline")
    cf.add_argument("--date", help="YYYY-MM-DD，默认今天")
    cf.add_argument("--yes", action="store_true", help="真写（默认 dry-run 只预览）")

    wk = sub.add_parser("weekly", help="本周回顾：把 Time record 时间按 project 聚合（需 Agent SDK）")
    wk.add_argument("--date", help="该周内任意一天 YYYY-MM-DD，默认上一个完整周")
    wk.add_argument("--plain", action="store_true", help="纯文本（不用 rich 渲染 bucket 小时条）")

    cw = sub.add_parser("confirm-weekly", help="确认把本周汇总写回 Obsidian Weekly 笔记")
    cw.add_argument("--date", help="该周内任意一天 YYYY-MM-DD，默认上一个完整周")
    cw.add_argument("--yes", action="store_true", help="真写（默认 dry-run 只预览）")

    cd_ = sub.add_parser("confirm-daily", help="确认把某天的 Time record timeline 写回 Obsidian Daily 笔记")
    cd_.add_argument("--date", help="YYYY-MM-DD，默认今天")
    cd_.add_argument("--yes", action="store_true", help="真写（默认 dry-run 只预览）")

    lg = sub.add_parser("log", help="录一条 Actual 事件")
    lg.add_argument("start", help="开始 HH:MM")
    lg.add_argument("end", help="结束 HH:MM")
    lg.add_argument("bucket", choices=["main", "side", "life", "health", "fun"], help="工作块")
    lg.add_argument("summary", nargs="+", help="事件描述")
    lg.add_argument("--date", help="YYYY-MM-DD，默认今天")

    args = p.parse_args(argv)

    # Day-rollover hook: on any invocation, flush past days' timeline to Obsidian & clear cache.
    try:
        for d in rollover.run():
            print(f"📥 {d:%Y-%m-%d} 的 timeline 已回填到 Obsidian 日记，并从本地缓存清除。")
    except Exception as e:  # noqa: BLE001 — a rollover hiccup must never break the actual command
        print(f"⚠️ 跨日回填跳过：{e}", file=sys.stderr)

    if not args.cmd:
        p.print_help()
        return 0

    return {
        "doctor": cmd_doctor,
        "summary": cmd_summary,
        "plan": cmd_plan,
        "reflect": cmd_reflect,
        "memory": cmd_memory,
        "observations": cmd_observations,
        "tui": cmd_tui,
        "reclassify": cmd_reclassify,
        "cubox": cmd_cubox,
        "aw-export": cmd_aw_export,
        "calendars": cmd_calendars,
        "confirm": cmd_confirm,
        "log": cmd_log,
        "weekly": cmd_weekly,
        "confirm-weekly": cmd_confirm_weekly,
        "confirm-daily": cmd_confirm_daily,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
