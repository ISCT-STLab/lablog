"""「1日」の扱い。朝 5 時を日付の区切りにする。"""

from datetime import date, datetime, time, timedelta

from .config import DAY_START_HOUR

WEEKDAYS = "月火水木金土日"


def logical_date(ts: float) -> date:
    return (datetime.fromtimestamp(ts) - timedelta(hours=DAY_START_HOUR)).date()


def day_range(d: date) -> tuple[float, float]:
    start = datetime.combine(d, time(DAY_START_HOUR))
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def parse_date(text: str) -> date:
    return date.fromisoformat(text)


def fmt_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def fmt_duration(sec: float) -> str:
    minutes = int(round(sec / 60))
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h}時間{m}分"
    if h:
        return f"{h}時間"
    return f"{m}分"


def fmt_date_jp(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）"
