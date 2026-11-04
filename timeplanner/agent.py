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
from .core import (activitywatch, backend, cubox, gcal, memory, notes, observations, recovery,
                   timeline, weather, weekly)


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
        recovery.summary(date),
        backend.summary(date, "plan"),
    ]
    refs = gcal.refs_summary(date)   # subscribed calendars → external fixed constraints
    if refs:
        parts.append(refs)
    return "\n\n".join(parts)


SYSTEM_PROMPT = """你是 TimePlanner，一个**辅助式**时间规划 agent。你服务的人用 Obsidian 记日记、\
用四个工作块管理一天：main（科研/deadline）· side（写代码/PhD 申请）· 生活 · 健身/娱乐。

排 block 时给每段标一个 bucket（用于日历配色，对齐 ta 的 Time record）：
- main / side —— 工作（蓝）
- life —— 生活杂务（黄）
- health —— 健康：健身、运动、睡眠、身体（浅绿）
- fun —— 娱乐：放松、玩、回血（深绿）
「健身/娱乐」那一格按当下活动性质选 health 或 fun。

你的价值观来自下面的原则库，必须严格遵守：

{principles}

规则（硬约束）：
1. 辅助式：你只**建议**。任何写日历的动作都要先给出清晰 diff，等人确认。绝不擅自写。
2. 永不覆盖外部事件：日历里没带 timeplanner 标的事件是固定约束，只在其空隙排块。
3. workload 推断已有规则给出建议 block 数，你可微调但要说明理由。
4. 每块到点物理硬停；专注 block 默认 90min。
5. 天气不好时把「生活/身体」那格挪室内。
6. 休息与恢复（排 plan 前**先调 recovery_status** 看今天是哪种日子，按它排）：
   - 休息日：全天不排 main/side，也别排「半办公」；只排 life/fun/health，出门最好。
   - 大事件当天（终面/onsite/deadline/投稿）：事件结束后全 off；收工前留一个 5 分钟能开始的回来动作。
   - 回来日（昨天大事件）：上午 off，下午回主场，底线只有一件小事；别安排「补进度」。
   - 小事件当天（普通面试）：面完那个晚上 off，明天照常。
   - 每天：fun 紧跟在 main/side 后面时，中间加 10–15min life 收尾（写 next action → 收拾房间 → 玩）。
   - 休息是排好的，不是逃避：措辞里别带愧疚感，也别劝 ta 把休息日缩成半天。
   stage_plan 会返回休息与恢复提醒；有提醒就改了再 stage，或说明为什么保留。

输出面向终端，务必克制篇幅：
- **别用宽 Markdown 表格**（终端会折行成一团）；timeline 用「时间 — 块 — 事件」的短行列表。
- 少用多级标题和分隔线；总长控制在一屏内，理由每条一句话。
- 先给结论（timeline + block 数），再简短理由，最后一句行动提示。

plan 任务：读 notes/AW/天气/现有 timeline → 产出一天的 timeline 草案（几点做哪块、几个专注 block），\
用清晰列表呈现并标注理由；然后**调用 stage_plan** 把这份草案 stage 起来（辅助式：这只是暂存，\
人跑 `timeplanner confirm` 才真写进本地 Plan timeline），最后提示人去 confirm。
reflect 任务：对比 ①Plan ②Actual（timeline_read）③Observed（AW），走「收工 4 问」记分板，给一行 takeaway 建议。\
遇到今天暴露的问题（拖延、专注差、作息、精力管理…），可用 cubox_search 在 ta 收藏的学习/工作/健康文章里找对症的方法再给建议。

weekly 任务（本周回顾）：调用 weekly_context 读这一周的 Time record 时间、各 bucket 小时数、项目名册、完成的 TODO/今日感想。\
各 bucket 的小时数已在里面算好，**别自己重算**。你的活是**把每条 Time record 事件归到某个 project**——\
用名册里的线索消歧：project 文件夹名、README 的 slug/alias、以及每个 project 文件夹下的子笔记名（事件摘要「拆 openinfer PR」\
对得上子笔记 `拆openinfer pr` → 归到 sideproj）；也参考日记里出现的项目/子笔记 wikilink。归不进任何 project 的时间（生活/健康/娱乐/杂项）\
单列一类，别硬塞。产出**逐项目**一行：`- **项目名**：X 小时 — 这周具体做了啥（用完成的 TODO/感想佐证，一句话）`；\
末尾附一行「未归类/生活类」时间小计。然后**调用 stage_weekly**（传该周任意一天的 date + 这份 markdown 正文 + `alloc_json`）把汇总 stage 起来——`alloc_json` 是 `{项目文件夹名: 分钟数}` 的 JSON，键**原样抄名册里的 folder**，未归类的放 `"_unclassified"`；面板靠它画图，必须传。\
接着做**本周想法 & 洞见**：从 weekly_context 的今日感想(💭)里挑**有信息量**的想法——科研/技术 idea、可沉淀成原则的观察、\
project 点子、职业/生活 ops、作息洞见——按主题归并成 markdown（别记流水账、别抄鸡毛蒜皮），**调用 stage_weekly_insights**（同一天 date）。\
并对其中**真正有价值**的每条观察调 **remember_observation**（带 theme + 观察发生的 date）沉淀进 observation 库——它会累积、以后能被 observations_search 召回。\
最后提示人跑 `timeplanner confirm-weekly` 预览、`--yes` 落地（两段一起写）。辅助式：绝不擅自写笔记。

记忆（自进化）：你有两个记忆工具——
- remember_thought：把这次规划里**重要的判断/取舍/观察**记下来（如「今天把 side 压到一格因为 main 有 deadline」），下次开工会读回，给你连续性。
- remember_principle：当你和 ta 聊出一条**可复用的时间管理原则**时记下来（候选，之后并入 principles.md）。
- remember_observation / observations_search：**observation 库**——把提炼过的观察/洞见沉淀进去（会累积），plan/reflect 时按需用 observations_search 召回过去的观察（如「上次精力差是怎么回事」）。它和 principles 的区别：principles 是成文规则、observation 是海量可检索的原始判断。
别记流水账；只记对未来排班真正有用的。每次 plan/reflect 结束前，回顾一下有没有值得记/该召回的。
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

    @tool("timeline_read",
          "读当前后端当天的 ①Plan 与 ②Actual 事件，以及订阅日历里的只读外部约束（🔒，排 plan 时避开）",
          {"date": str})
    async def timeline_read(args):
        d = _parse_date(args.get("date"))
        text = backend.summary(d, "plan", color=False) + "\n\n" + backend.summary(d, "actual", color=False)
        refs = gcal.refs_summary(d, color=False)   # subscribed calendars → external constraints
        if refs:
            text += "\n\n" + refs
        return {"content": [{"type": "text", "text": text}]}

    @tool("recovery_status",
          "今天是哪种日子：休息日 / 大事件当天 / 小事件当天 / 回来日 / 普通日，及对应排班规则。"
          "按订阅日历、Actual 记录和 📅 deadline 里的面试/deadline 关键词判断。",
          {"date": str})
    async def recovery_status(args):
        d = _parse_date(args.get("date"))
        return {"content": [{"type": "text", "text": recovery.summary(d)}]}

    @tool("stage_plan",
          "把今日 timeline 草案 stage 到待确认区（人跑 `timeplanner confirm` 才真写）。"
          "events_json 是 JSON 数组，每项 {start:'HH:MM', end:'HH:MM', bucket:'main|side|life|health|fun', summary:'...'}",
          {"date": str, "events_json": str})
    async def stage_plan(args):
        d = _parse_date(args.get("date"))
        try:
            spec = json.loads(args["events_json"])
        except (json.JSONDecodeError, KeyError) as e:
            return {"content": [{"type": "text", "text": f"⚠️ events_json 解析失败：{e}"}]}
        evs = timeline.stage_plan(d, spec)
        body = "\n".join(f"  {e.line(color=False)}" for e in evs)
        warns = recovery.lint_plan(recovery.day_mode(d), evs)
        lint = ("\n⚠️ 休息与恢复提醒：\n" + "\n".join(f"  - {w}" for w in warns)) if warns else ""
        return {"content": [{"type": "text",
                "text": f"🗂️ 已 stage {len(evs)} 个事件，等人确认：\n{body}{lint}\n"
                        f"让 ta 跑 `timeplanner confirm` 预览、`timeplanner confirm --yes` 落地。"}]}

    @tool("weekly_context",
          "读某一周（ISO 周一至周日）的 Time record 时间：按天列事件 + 各 bucket 小时数 + 项目名册 + 完成的 TODO/今日感想 + 近期 deadline。date 传该周内任意一天。",
          {"date": str})
    async def weekly_context(args):
        d = _parse_date(args.get("date"))
        return {"content": [{"type": "text", "text": weekly.context_blob(d)}]}

    @tool("stage_weekly",
          "把每个项目『本周投入时间 + 具体做了什么』的 markdown 汇总 stage 到待确认区（辅助式：这只是暂存，"
          "人跑 `timeplanner confirm-weekly` 才真写进 Obsidian Weekly 笔记）。返回写回预览。"
          "date 传该周内任意一天；markdown 是给人看的项目回顾正文；"
          "alloc_json 是同一份数据的机器可读版：JSON 对象 {项目文件夹名: 分钟数}，"
          "键必须**原样**用项目名册里的 folder（如 \"发展-求职-26fall秋招\"），归不进任何 project 的时间放 \"_unclassified\"。"
          "它会被写成一行 HTML 注释跟在正文后面，Obsidian 不渲染，Weekly 面板靠它出图——所以务必传。",
          {"date": str, "markdown": str, "alloc_json": str})
    async def stage_weekly(args):
        d = _parse_date(args.get("date"))
        md = args.get("markdown", "").strip()
        if not md:
            return {"content": [{"type": "text", "text": "⚠️ markdown 为空，未 stage。"}]}
        raw = (args.get("alloc_json") or "").strip()
        alloc = {}
        if raw:
            try:
                alloc = json.loads(raw)
            except ValueError:
                return {"content": [{"type": "text", "text": "⚠️ alloc_json 不是合法 JSON，未 stage。重发一次。"}]}
        body = md + ("\n\n" + weekly.alloc_comment(alloc) if alloc else "")
        weekly.stage(d, body)
        return {"content": [{"type": "text", "text": weekly.write_back(d, body, dry_run=True)}]}

    @tool("stage_weekly_insights",
          "把『本周想法 & 洞见』的 markdown 汇总 stage 到待确认区（辅助式，人跑 `timeplanner confirm-weekly` 才写进 Weekly 笔记）。"
          "从 weekly_context 的今日感想(💭)里挑有信息量的想法——科研/技术 idea、可沉淀成原则的观察、project 点子、职业/生活 ops、作息洞见——按主题归并，别记流水账。返回写回预览。",
          {"date": str, "markdown": str})
    async def stage_weekly_insights(args):
        d = _parse_date(args.get("date"))
        md = args.get("markdown", "").strip()
        if not md:
            return {"content": [{"type": "text", "text": "⚠️ markdown 为空，未 stage。"}]}
        weekly.stage(d, md, kind="insights")
        return {"content": [{"type": "text", "text": weekly.write_back(d, md, dry_run=True, kind="insights")}]}

    @tool("cubox_search",
          "在本地 Cubox 语料（我收藏的学习/工作/健康/心理文章 + 我的高亮批注）里搜关键词，"
          "找可借鉴的方法/观点。query 用空格分词。",
          {"query": str})
    async def cubox_search(args):
        return {"content": [{"type": "text", "text": cubox.search_summary(args.get("query", ""))}]}

    @tool("observations_search",
          "在『observation 库』（自己过去提炼的观察/洞见，可累积检索）里按关键词召回相关观察。"
          "遇到反复出现的问题、要做决定、或想引用过去的判断时用。query 用空格分词。",
          {"query": str})
    async def observations_search(args):
        return {"content": [{"type": "text", "text": observations.search_summary(args.get("query", ""))}]}

    @tool("remember_observation",
          "把一条**提炼过的**观察/洞见沉淀进 observation 库（会累积、可被 observations_search 召回）。"
          "只记有信息量的（科研/技术 idea、可复用的判断、project 点子、作息/精力规律…），别记流水账。"
          "theme 给个主题短标签（如『排班原则』『作息』『科研品味』）；date 传观察发生的日期。",
          {"text": str, "theme": str, "date": str})
    async def remember_observation(args):
        added = observations.add(args["text"], theme=args.get("theme", ""),
                                 source="agent", date=_parse_date(args.get("date")))
        msg = "🔭 已沉淀进 observation 库。" if added else "（库里已有相同观察，未重复记。）"
        return {"content": [{"type": "text", "text": msg}]}

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
        tools=[notes_summary, aw_summary, weather_summary, timeline_read, recovery_status, stage_plan,
               weekly_context, stage_weekly, stage_weekly_insights, cubox_search,
               observations_search, remember_observation, remember_thought, remember_principle],
    )
    tool_names = [
        "mcp__timeplanner__notes_summary",
        "mcp__timeplanner__activitywatch_summary",
        "mcp__timeplanner__weather_summary",
        "mcp__timeplanner__timeline_read",
        "mcp__timeplanner__recovery_status",
        "mcp__timeplanner__stage_plan",
        "mcp__timeplanner__weekly_context",
        "mcp__timeplanner__stage_weekly",
        "mcp__timeplanner__stage_weekly_insights",
        "mcp__timeplanner__cubox_search",
        "mcp__timeplanner__observations_search",
        "mcp__timeplanner__remember_observation",
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
    "recovery_status": "看今天是哪种日子",
    "stage_plan": "暂存 plan 草案",
    "weekly_context": "读本周时间/项目",
    "stage_weekly": "暂存本周汇总",
    "stage_weekly_insights": "暂存本周洞见",
    "cubox_search": "搜 Cubox 语料",
    "observations_search": "召回 observation 库",
    "remember_observation": "沉淀 observation",
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


async def run_interactive(
    prompt: str,
    on_round=None,
    input_hint: str = "\n💬 补充/调整后再排一轮（直接回车结束；结束后 tp confirm 落地）> ",
    closing: str = "（已结束。最新草案已 stage —— 跑 `tp confirm` 预览、`tp confirm --yes` 落地。）",
) -> None:
    """Multi-turn session: after the draft, keep supplying info and let the agent re-plan until you're done.

    on_round(): optional no-arg hook run after each agent turn (e.g. show the freshly re-staged allocation).
    input_hint / closing let a command tailor the prompt line and the goodbye (e.g. confirm-weekly instead of confirm).
    """
    sdk = _sdk()
    if not sdk:
        return
    console, dim = _renderer()
    options = build_options()

    def _round():
        if on_round:
            try:
                on_round()
            except Exception as e:  # noqa: BLE001 — a display hook must never break the session
                dim(f"（展示更新失败：{e}）")

    async with sdk.ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        await _consume(client.receive_response(), sdk, console, dim)
        _round()
        while True:
            try:
                more = await _ainput(input_hint)
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if more.strip().lower() in _EXIT_WORDS:
                break
            await client.query(more)
            await _consume(client.receive_response(), sdk, console, dim)
            _round()
    print(closing)
