"""Phase 0 入口：终端对话。

    timeplanner summary [--date YYYY-MM-DD]   # M1：四个只读信号，不碰 agent
    timeplanner doctor                        # 环境自检（M0）
    timeplanner plan    [--date ...]          # 出今日 plan 草案（需 agent SDK）
    timeplanner reflect [--date ...]          # 晚间复盘（需 agent SDK）
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys

from .config import config
from .core import activitywatch, gcal, memory, notes, weather


def _date(s: str | None) -> dt.date:
    return dt.date.fromisoformat(s) if s else dt.date.today()


def cmd_doctor(_args) -> int:
    """环境自检：库、AW、天气、GCal、agent SDK。"""
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
          else "⚠️  GCal 未配置（M3 才需要；见 README）")

    try:
        import claude_agent_sdk  # noqa: F401
        print("✅ Claude Agent SDK 已安装（plan/reflect 可用）")
    except ImportError:
        print("⚠️  Claude Agent SDK 未安装：pip install -e '.[agent]'（summary 不需要它）")

    print("\n结论：M1 只读闭环" + ("可跑通 ✅" if ok else "缺库路径 ❌"))
    return 0 if ok else 1


def cmd_summary(args) -> int:
    """M1：四个只读模块各输出 summary。零 agent、零写入。"""
    d = _date(args.date)
    print(notes.summary(d))
    print("\n" + activitywatch.summary(d))
    print("\n" + weather.summary(d))
    print("\n" + gcal.summary(d))
    return 0


def cmd_plan(args) -> int:
    from . import agent
    d = _date(args.date)
    mem = memory.context_block()
    prompt = (f"今天是 {d:%Y-%m-%d}。请先用工具读 notes/AW/天气/现有日历，"
              "然后给我一份今日 plan 草案（timeline + 专注 block 数 + 理由），"
              "最后问我是否确认写入。")
    if mem:
        prompt += f"\n\n{mem}"
    asyncio.run(agent.run(prompt))
    return 0


def cmd_reflect(args) -> int:
    from . import agent
    d = _date(args.date)
    mem = memory.context_block()
    prompt = (f"今天是 {d:%Y-%m-%d}，晚间复盘。请对比 ①Plan(日历) ②Actual ③Observed(AW)，"
              "走「收工 4 问」记分板，给我一行 takeaway 建议。")
    if mem:
        prompt += f"\n\n{mem}"
    asyncio.run(agent.run(prompt))
    return 0


def cmd_memory(args) -> int:
    """看 / 清 planner 记忆缓存（思考 + 候选原则）。"""
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

    mp = sub.add_parser("memory", help="planner 记忆缓存（思考 + 候选原则）")
    mp.add_argument("--clear", action="store_true", help="清空记忆")

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 0

    return {
        "doctor": cmd_doctor,
        "summary": cmd_summary,
        "plan": cmd_plan,
        "reflect": cmd_reflect,
        "memory": cmd_memory,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
