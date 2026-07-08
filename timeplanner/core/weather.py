"""Open-Meteo forecast (no key needed) → travel/outdoor advice.

Give the agent a signal: whether today is suitable for scheduling the "life/fitness"
slot outdoors or keeping it indoors (echoing template principles like "hit the school's
basement gym on hot days").
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import requests

from ..config import config

API = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 8

WMO = {
    0: "晴", 1: "大致晴", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


@dataclass
class Weather:
    date: dt.date
    t_min: float = 0.0
    t_max: float = 0.0
    precip_prob: int = 0
    code: int = 0
    available: bool = True
    note: str = ""

    @property
    def desc(self) -> str:
        return WMO.get(self.code, f"code {self.code}")

    def outdoor_advice(self) -> str:
        if self.t_max >= 32:
            return "🥵 高温，户外活动挪早晚，或按原则去室内/地下健身房。"
        if self.precip_prob >= 60:
            return "🌧️ 大概率降水，生活/健身那格建议室内。"
        if self.t_max <= 0:
            return "🥶 严寒，注意保暖，户外从简。"
        if 12 <= self.t_max <= 26 and self.precip_prob < 40:
            return "🌤️ 天气舒适，适合把「生活/身体」那格安排成户外。"
        return "🆗 天气一般，户外/室内都行。"


def fetch(date: dt.date | None = None) -> Weather:
    date = date or dt.date.today()
    try:
        r = requests.get(
            API,
            params={
                "latitude": config.lat,
                "longitude": config.lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
                "timezone": config.timezone,
                "start_date": date.isoformat(),
                "end_date": date.isoformat(),
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        d = r.json()["daily"]
        return Weather(
            date=date,
            t_min=d["temperature_2m_min"][0],
            t_max=d["temperature_2m_max"][0],
            precip_prob=d["precipitation_probability_max"][0] or 0,
            code=d["weathercode"][0],
        )
    except (requests.RequestException, KeyError, IndexError) as e:
        return Weather(date=date, available=False, note=f"天气获取失败：{e}")


def summary(date: dt.date | None = None) -> str:
    date = date or dt.date.today()
    w = fetch(date)
    lines = [f"# 🌤️ 天气 —— {date:%Y-%m-%d}（{config.lat},{config.lon}）"]
    if not w.available:
        lines.append(w.note)
        return "\n".join(lines)
    lines.append(f"{w.desc}，{w.t_min:.0f}–{w.t_max:.0f}°C，降水概率 {w.precip_prob}%")
    lines.append(w.outdoor_advice())
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())
