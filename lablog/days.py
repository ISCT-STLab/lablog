"""「1日」の扱い。朝 5 時を日付の区切りにする。"""

from datetime import date, datetime, time, timedelta

from .config import DAY_START_HOUR

WEEKDAYS = "月火水木金土日"


def logical_date(ts: float) -> date:
    return (datetime.fromtimestamp(ts) - timedelta(hours=DAY_START_HOUR)).date()


def day_range(d: date) -> tuple[float, float]:
    start = datetime.combine(d, time(DAY_START_HOUR))
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


# 扱う日付の範囲（これより外は、Windows で時刻に直せず落ちることがある）
MIN_YEAR, MAX_YEAR = 2000, 2100


def parse_date(text: str) -> date:
    d = date.fromisoformat(str(text))
    if not MIN_YEAR <= d.year <= MAX_YEAR:
        raise ValueError(f"日付の範囲が正しくありません: {text}")
    return d


def parse_month(text: str) -> tuple[int, int]:
    """「2026-10」を (2026, 10) にする。"""
    year, month = map(int, str(text).split("-"))
    if not (MIN_YEAR <= year <= MAX_YEAR and 1 <= month <= 12):
        raise ValueError(f"月が正しくありません: {text}")
    return year, month


def recent_clock_time(now: float, hhmm: str) -> float:
    """「9:30」のような時刻を、now 以前でいちばん近いその時刻にする（いまより後なら前日の時刻）。"""
    try:
        h, m = (int(x) for x in hhmm.strip().split(":"))
    except ValueError:
        raise ValueError("時刻は 9:30 のように入力してください")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("時刻は 0:00〜23:59 で入力してください")
    t = datetime.fromtimestamp(now).replace(hour=h, minute=m, second=0, microsecond=0)
    if t.timestamp() > now:
        t -= timedelta(days=1)
    return t.timestamp()


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
