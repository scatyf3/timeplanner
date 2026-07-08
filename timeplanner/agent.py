"""Agent SDK loop + system prompt.

The four core modules are registered as tools, running through the Claude Agent SDK
(subscription quota, no extra per-token billing).
The SDK is a soft dependency: when not installed, plan/reflect prompt to install
`pip install -e '.[agent]'`; read-only summary commands don't depend on it at all.
"""

from __future__ import annotations

import datetime as dt

import json

from .config import config
from .core import activitywatch, backend, memory, notes, timeline, weather


def load_principles() -> str:
    p = config.principles_path
    base = p.read_text(encoding="utf-8") if p.is_file() else "(principles.md 缺失)"
    return base + "\n" + memory.prompt_addendum()  # layer on the accumulated candidate principles


def gather_context(date: dt.date | None = None) -> str:
    """Stitch the four read-only signals into one context blob for the agent (also viewable on its own via `timeplanner summary`)."""
    date = date or dt.date.today()
    parts = [
        notes.summary(date),
        activitywatch.summary(date),
        weather.summary(date),
        backend.summary(date, "plan"),
    ]
    return "\n\n".join(parts)


SYSTEM_PROMPT = """你是 TimePlanner，一个**辅助式**时间规划 agent。你服务的人用 Obsidian 记日记、\
用四个工作块管理一天：main（科研/deadline）· side（写代码/PhD 申请）· 生活 · 健身/娱乐。

你的价值观来自下面的原则库，必须严格遵守：

{principles}

规则（硬约束）：
1. 辅助式：你只**建议**。任何写日历的动作都要先给出清晰 diff，等人确认。绝不擅自写。
2. 永不覆盖外部事件：日历里没带 timeplanner 标的事件是固定约束，只在其空隙排块。
3. workload 推断已有规则给出建议 block 数，你可微调但要说明理由。
4. 每块到点物理硬停；专注 block 默认 90min。
5. 天气不好时把「生活/身体」那格挪室内。

输出面向终端，务必克制篇幅：
- **别用宽 Markdown 表格**（终端会折行成一团）；timeline 用「时间 — 块 — 事件」的短行列表。
- 少用多级标题和分隔线；总长控制在一屏内，理由每条一句话。
- 先给结论（timeline + block 数），再简短理由，最后一句行动提示。

plan 任务：读 notes/AW/天气/现有 timeline → 产出一天的 timeline 草案（几点做哪块、几个专注 block），\
用清晰列表呈现并标注理由；然后**调用 stage_plan** 把这份草案 stage 起来（辅助式：这只是暂存，\
人跑 `timeplanner confirm` 才真写进本地 Plan timeline），最后提示人去 confirm。
reflect 任务：对比 ①Plan ②Actual（timeline_read）③Observed（AW），走「收工 4 问」记分板，给一行 takeaway 建议。

记忆（自进化）：你有两个记忆工具——
- remember_thought：把这次规划里**重要的判断/取舍/观察**记下来（如「今天把 side 压到一格因为 main 有 deadline」），下次开工会读回，给你连续性。
- remember_principle：当你和 ta 聊出一条**可复用的时间管理原则**时记下来（候选，之后并入 principles.md）。
别记流水账；只记对未来排班真正有用的。每次 plan/reflect 结束前，回顾一下有没有值得记的。
"""


def build_options():
    """Build ClaudeAgentOptions, registering the four modules as tools. Only called when the SDK is installed."""
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server, tool

    @tool("notes_summary", "读当天 Obsidian 日记 + 近期 deadline，估计 workload", {"date": str})
    async def notes_summary(args):
        d = _parse_date(args.get("date"))
        return {"content": [{"type": "text", "text": notes.summary(d)}]}

    @tool("activitywatch_summary", "读 ActivityWatch 观测：在机时长、专注 block、Top 应用", {"date": str})
    async def aw_summary(args):
        d = _parse_date(args.get("date"))
        return {"content": [{"type": "text", "text": activitywatch.summary(d)}]}

    @tool("weather_summary", "取当天天气预报 + 户外建议", {"date": str})
    async def weather_summary(args):
        d = _parse_date(args.get("date"))
        return {"content": [{"type": "text", "text": weather.summary(d)}]}

    @tool("timeline_read", "读当前后端当天的 ①Plan 与 ②Actual 事件", {"date": str})
    async def timeline_read(args):
        d = _parse_date(args.get("date"))
        text = backend.summary(d, "plan") + "\n\n" + backend.summary(d, "actual")
        return {"content": [{"type": "text", "text": text}]}

    @tool("stage_plan",
          "把今日 timeline 草案 stage 到待确认区（人跑 `timeplanner confirm` 才真写）。"
          "events_json 是 JSON 数组，每项 {start:'HH:MM', end:'HH:MM', bucket:'main|side|life|fit', summary:'...'}",
          {"date": str, "events_json": str})
    async def stage_plan(args):
        d = _parse_date(args.get("date"))
        try:
            spec = json.loads(args["events_json"])
        except (json.JSONDecodeError, KeyError) as e:
            return {"content": [{"type": "text", "text": f"⚠️ events_json 解析失败：{e}"}]}
        evs = timeline.stage_plan(d, spec)
        body = "\n".join(f"  {e.line()}" for e in evs)
        return {"content": [{"type": "text",
                "text": f"🗂️ 已 stage {len(evs)} 个事件，等人确认：\n{body}\n"
                        f"让 ta 跑 `timeplanner confirm` 预览、`timeplanner confirm --yes` 落地。"}]}

    @tool("remember_thought", "把一条重要的规划思考/取舍/观察记进 planner 记忆，下次开工能读回", {"text": str, "kind": str})
    async def remember_thought(args):
        memory.add_thought(args["text"], kind=args.get("kind", "plan"))
        return {"content": [{"type": "text", "text": "🧠 已记入 planner 记忆。"}]}

    @tool("remember_principle", "把一条提炼出的时间管理原则记进记忆（候选，之后合流到 principles.md）", {"text": str})
    async def remember_principle(args):
        added = memory.add_principle(args["text"], source="agent")
        msg = "📌 已积累为候选原则。" if added else "（已有相同原则，未重复记。）"
        return {"content": [{"type": "text", "text": msg}]}

    server = create_sdk_mcp_server(
        name="timeplanner",
        version="0.1.0",
        tools=[notes_summary, aw_summary, weather_summary, timeline_read, stage_plan,
               remember_thought, remember_principle],
    )
    tool_names = [
        "mcp__timeplanner__notes_summary",
        "mcp__timeplanner__activitywatch_summary",
        "mcp__timeplanner__weather_summary",
        "mcp__timeplanner__timeline_read",
        "mcp__timeplanner__stage_plan",
        "mcp__timeplanner__remember_thought",
        "mcp__timeplanner__remember_principle",
    ]
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT.format(principles=load_principles()),
        mcp_servers={"timeplanner": server},
        allowed_tools=tool_names,
    )


def _parse_date(s) -> dt.date:
    if not s:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(str(s))
    except ValueError:
        return dt.date.today()


_TOOL_LABEL = {
    "notes_summary": "读笔记 workload",
    "activitywatch_summary": "读 ActivityWatch",
    "weather_summary": "读天气",
    "timeline_read": "读 Plan/Actual timeline",
    "stage_plan": "暂存 plan 草案",
    "remember_thought": "记规划思考",
    "remember_principle": "记候选原则",
}


def _sdk():
    """Lazily import the SDK; if missing, print a hint and return None."""
    try:
        from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock  # noqa: F401
        import claude_agent_sdk as sdk
        return sdk
    except ImportError:
        print("⚠️  未安装 Claude Agent SDK。装它才能跑 agent：\n"
              "    pip install -e '.[agent]'\n"
              "（只读上下文可用 `timeplanner summary` 直接看，不需要 SDK。）")
        return None


def _renderer():
    """Return (console, dim). rich is optional: render Markdown if present, else fall back to plain text."""
    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        console = None

    def dim(msg: str) -> None:
        if console:
            console.print(msg, style="dim")
        else:
            print(f"\033[2m{msg}\033[0m")

    return console, dim


async def _consume(message_iter, sdk, console, dim) -> None:
    """Consume one round of agent messages: show tool calls as dim progress lines, render the final answer uniformly."""
    chunks: list[str] = []
    async for message in message_iter:
        if isinstance(message, sdk.AssistantMessage):
            for block in message.content:
                if isinstance(block, sdk.TextBlock):
                    chunks.append(block.text)
                elif isinstance(block, sdk.ToolUseBlock):
                    name = block.name.split("__")[-1]
                    if name in _TOOL_LABEL:  # only show our own tools, skip SDK built-in noise
                        dim(f"  · {_TOOL_LABEL[name]}…")
    text = "".join(chunks).strip()
    if not text:
        return
    print()
    if console:
        from rich.markdown import Markdown
        console.print(Markdown(text))
    else:
        print(text)


async def run(prompt: str) -> None:
    """Run a single agent round one-shot (used by reflect, etc.)."""
    sdk = _sdk()
    if not sdk:
        return
    console, dim = _renderer()
    await _consume(sdk.query(prompt=prompt, options=build_options()), sdk, console, dim)


_EXIT_WORDS = {"", "done", "ok", "好了", "结束", "q", "quit", "exit", "confirm", "确认"}


try:
    import readline  # noqa: F401  importing alone enables line editing for input() (arrow keys move cursor, backspace, history)
except ImportError:
    pass


async def _ainput(prompt: str) -> str:
    """input() that doesn't block the event loop (readline line editing enabled)."""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(None, lambda: input(prompt))


async def run_interactive(prompt: str) -> None:
    """Multi-turn session: after the draft, keep supplying info and let the agent re-plan until you're done."""
    sdk = _sdk()
    if not sdk:
        return
    console, dim = _renderer()
    options = build_options()
    async with sdk.ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        await _consume(client.receive_response(), sdk, console, dim)
        while True:
            try:
                more = await _ainput("\n💬 补充/调整后再排一轮（直接回车结束；结束后 tp confirm 落地）> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if more.strip().lower() in _EXIT_WORDS:
                break
            await client.query(more)
            await _consume(client.receive_response(), sdk, console, dim)
    print("（已结束。最新草案已 stage —— 跑 `tp confirm` 预览、`tp confirm --yes` 落地。）")
