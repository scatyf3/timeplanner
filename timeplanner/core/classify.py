"""Keyword → bucket auto-classifier for recorded activities.

The dumbest explainable thing that works (project philosophy: don't reach for a model too early).
`guess_bucket("下午 walk 一圈")` → "health". Edit the keyword lists below to calibrate; groups are
checked in order (health → fun → life → side → main), first hit wins, default "main".

Buckets mirror gcal.BUCKETS: main/side = 工作(蓝) · life = 生活(黄) · health = 健康(绿) · fun = 娱乐(深绿).
Used by the TUI recorder; an explicit leading bucket token still overrides this.
"""

from __future__ import annotations

import re

# (bucket, keywords) in priority order. Substring match, case-insensitive; CJK needs no word boundary.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("health", ("walk", "散步", "遛", "健身", "gym", "运动", "锻炼", "骑车", "单车", "跑步", "游泳",
                "椭圆", "拉伸", "瑜伽", "球", "睡", "午睡", "nap", "小憩", "冥想", "洗澡", "身体")),
    ("fun", ("l4d2", "ron", "tkf", "游戏", "game", "steam", "摸鱼", "刷", "b站", "bilibili",
             "youtube", "yt", "剧", "电影", "番", "漫画", "小说", "玩", "回血", "放松", "休闲", "娱乐")),
    ("life", ("吃", "饭", "早饭", "午饭", "晚饭", "做饭", "外卖", "买菜", "购物", "买", "起床",
              "cd", "打扫", "卫生", "家务", "杂务", "收拾", "通勤", "洗", "理发", "快递", "报销")),
    ("side", ("写代码", "code", "coding", "pr ", "openinfer", "timeplanner", "lawn", "uccl",
              "网站", "博客", "side", "phd", "申请", "套磁", "陶瓷", "leetcode", "lc", "刷题",
              "面试", "简历", "speccompress", "spec compress", "specompress", "speccontext")),
    ("main", ("科研", "磕盐", "实验", "收实验", "跑实验", "收菜", "论文", "paper", "开会", "meeting",
              "组会", "slides", "sweep", "kernel", "model", "训练", "eval", "deadline", "大科研", "写作")),
]

DEFAULT_BUCKET = "main"
_EXPLICIT = re.compile(r"^\s*[\[【(（]?\s*(main|side|life|health|fun)(?:\s*[\]】)）:：]|\s|$)", re.IGNORECASE)


def explicit_bucket(text: str) -> str | None:
    """A bucket written at the start of a title — `[fun] 打塔`, `fun 打塔`, `health：健身`. Used for events made
    outside timeplanner (Apple Watch shortcuts, hand-made) that carry no marker or color."""
    m = _EXPLICIT.match(text or "")
    return m.group(1).lower() if m else None


def guess_bucket(text: str) -> str:
    """Infer a bucket from an activity description via ordered keyword rules. Defaults to 'main'."""
    low = text.lower()
    for bucket, keywords in _RULES:
        if any(k in low for k in keywords):
            return bucket
    return DEFAULT_BUCKET
