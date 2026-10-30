"""大学での滞在（入室・仮退室・退室確定）の判定。

利用者が選んだ判定方法（設定の presence.mode）で、判定に使うものが変わる（judge_net）。
- wifi（Wi-Fi で判定）：Wi-Fi と操作の有無で、下のルールのとおりに判定する
- activity（この PC の操作で判定）：手元で操作しているかどうか。操作中を「大学の Wi-Fi」、操作していない・
  ロック中・リモートデスクトップ中を「Wi-Fi が切れた」とみなして、同じルールで判定する
- manual（手動）：自動では入室にしない（ボタンだけ）。押し忘れたときのために、「判定に使わない Wi-Fi」と同じ扱いにして、
  操作がない・PC が止まっていた時間が leave_after を超えたら、最後に操作した時刻で退室確定する
- off（記録しない）：判定しない
どの方法でも、リモートデスクトップで操作している間は研究室にいないとみなす。

ルール
- 大学の Wi-Fi につながる → 入室（仮退室中なら取り消して滞在を続ける）
- Wi-Fi が切れる（スリープ・シャットダウンを含む） → 仮退室
- 大学外の Wi-Fi につながる → 仮退室した時刻で退室確定
- 仮退室のまま leave_after（4 時間）たつ → 仮退室した時刻で退室確定
- 大学の Wi-Fi のまま leave_after 操作がない → 最後に操作した時刻で退室確定
- 判定に使わない Wi-Fi（テザリングなど）につながる → そのときの状態を続ける
  （在学中なら滞在を続け、仮退室中なら仮退室のまま。新しく入室にはしない）

手動（ダッシュボードのボタン）
- 入室 → すぐに入室。その滞在は、未登録の Wi-Fi につないでも閉じない（判定に使わない Wi-Fi と同じ扱い）。
  PC を閉じて仮退室になっても、判定に使わない Wi-Fi のまま戻って操作すれば、在室に戻す
- 退室 → いま退室確定。研究室を一度離れたと分かるまで（大学の Wi-Fi から離れる・操作をやめる）、
  自動では入室にしない（押したあとも研究室の Wi-Fi のまま・操作したままのことが多いため）
"""

import time
from datetime import datetime

from .days import fmt_hm
from .db import kv_get, kv_set
from .wifi import IGNORED, LOCATION_LABELS, OFFLINE, OTHER, UNIV

MANUAL_KEY = "presence_manual_stay"  # 手動で入室した滞在の id
# これより短い滞在は残さない（入室をすぐ取り消した・Wi-Fi が一瞬つながっただけ など）
MIN_STAY_SEC = 60
SUPPRESS_KEY = "presence_suppress"   # "1"：手動で退室したので、自動の入室を止めている


def judge_net(mode: str, wifi_net: str, local_active: bool, remote: bool) -> str:
    """判定に使う「つながり」（Presence.tick に渡すもの）。"""
    if mode == "off":
        return OTHER
    if remote:
        return OFFLINE
    if mode == "activity":
        return UNIV if local_active else OFFLINE
    if mode == "manual":
        return IGNORED
    return wifi_net


def work_location(mode: str, wifi_net: str, state: str, remote: bool, place: str = "home") -> str:
    """作業していた場所（segments.location に記録する名前）。place：記録しないときに数える場所（設定で選ぶ）。"""
    if remote:
        return "自宅"  # リモートデスクトップ：外からこの PC に入っている
    if mode == "activity":
        return "大学"
    if mode == "off":
        return "自宅" if place == "home" else "その他"
    if mode == "manual" or wifi_net == IGNORED:  # 手動・テザリングなど：滞在中なら大学での作業とみなす
        return "大学" if state == "in" else "その他"
    return LOCATION_LABELS[wifi_net]


class Presence:
    def __init__(self, conn, leave_after_sec: float = 4 * 3600, idle_threshold_sec: float = 300,
                 gap_sec: float = 180):
        self.conn = conn
        self.leave_after = leave_after_sec
        self.idle_threshold = idle_threshold_sec
        # トラッカーがこれ以上止まっていたら、スリープ等で Wi-Fi が切れていたとみなす
        self.gap = gap_sec

    def open_stay(self) -> dict | None:
        row = self.conn.execute("SELECT * FROM stays WHERE status != 'closed' "
                                "ORDER BY start_ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def tick(self, now: float, net: str, idle: float, by_wifi: bool = False) -> list[tuple[str, int]]:
        """1 回分の判定。起きたこと（"opened" / "closed", 滞在 id）の一覧を返す。

        by_wifi：Wi-Fi で判定している（リモートデスクトップ中は False）。入室ボタンで始めた滞在は、未登録の Wi-Fi や、
        Wi-Fi が無い・読めないとき（有線・位置情報がオフ）も「判定に使わない Wi-Fi」と同じ扱いにして閉じない。
        """
        events: list[tuple[str, int]] = []
        stay = self.open_stay()
        manual = kv_get(self.conn, MANUAL_KEY) or ""
        if stay is not None:
            entered = manual == str(stay["id"])  # 入室ボタンで始めた滞在
            stay_net = IGNORED if entered and by_wifi and net in (OTHER, OFFLINE) else net
            stay = self._advance(stay, now, stay_net, idle, events, resume=entered and idle < self.idle_threshold)
        if stay is None and manual:
            kv_set(self.conn, MANUAL_KEY, "")
        suppressed = kv_get(self.conn, SUPPRESS_KEY) == "1"
        if suppressed and net != UNIV:  # 研究室を一度離れた
            kv_set(self.conn, SUPPRESS_KEY, "0")
            suppressed = False
        if stay is None and net == UNIV and idle < self.idle_threshold and not suppressed:
            events.append(("opened", self._open(now)))
        return events

    def manual_enter(self, now: float, at: float | None = None) -> list[tuple[str, int]]:
        """入室ボタン。仮退室中なら滞在を続け、そうでなければ新しく入室する。

        at：押し忘れたときに指定する入室の時刻。在室中に指定したときは、入室の時刻をその時刻に直す。
        """
        if at is not None and at > now:
            raise ValueError("これからの時刻は指定できません")
        stay = self.open_stay()
        expired = (stay is not None and stay["status"] == "tentative"
                   and now - stay["tentative_since"] >= self.leave_after)
        if at is not None:  # 前の滞在と重ならないか（直すと、前の滞在の記録が壊れるため）
            prev_end = stay["tentative_since"] if expired else self._last_end(stay["id"] if stay else None)
            if prev_end is not None and at < prev_end:
                raise ValueError(f"{fmt_hm(at)} は、前の滞在の退室（{fmt_hm(prev_end)}）より前です。"
                                 "入退室の記録の「編集」で直してください")
        events: list[tuple[str, int]] = []
        if expired:
            stay = self._close(stay, stay["tentative_since"], "timeout", events)
        if stay is None:
            stay_id = self._open(now if at is None else at, now)
            events.append(("opened", stay_id))
        else:
            fields = {"status": "in", "tentative_since": None, "last_seen_ts": now}
            if at is not None:
                fields |= {"start_ts": at, "manual": 1}
            self._set(stay, **fields)
            stay_id = stay["id"]
        kv_set(self.conn, MANUAL_KEY, stay_id)
        kv_set(self.conn, SUPPRESS_KEY, "0")
        return events

    def manual_leave(self, now: float, at: float | None = None) -> list[tuple[str, int]]:
        """退室ボタン。いま（仮退室中なら仮退室した時刻）で退室を確定する。at：押し忘れたときに指定する退室の時刻。"""
        stay = self.open_stay()
        if at is not None:
            if stay is None:
                raise ValueError("いまは在室中ではありません。前の退室の時刻は、入退室の記録の「編集」で直してください")
            if at > now:
                raise ValueError("これからの時刻は指定できません")
            if at <= stay["start_ts"]:
                raise ValueError(f"退室は、入室（{fmt_hm(stay['start_ts'])}）より後の時刻にしてください")
        events: list[tuple[str, int]] = []
        if stay is not None:
            end = at if at is not None else stay["tentative_since"] if stay["status"] == "tentative" else now
            self._close(stay, end, "manual_out", events)
        kv_set(self.conn, MANUAL_KEY, "")
        kv_set(self.conn, SUPPRESS_KEY, "1")
        return events

    def _open(self, start: float, seen: float | None = None) -> int:
        cur = self.conn.execute("INSERT INTO stays (start_ts, last_seen_ts, status) VALUES (?, ?, 'in')",
                                (start, start if seen is None else seen))
        return cur.lastrowid

    def _last_end(self, exclude: int | None) -> float | None:
        """閉じた滞在のうち、いちばん遅い退室の時刻。"""
        row = self.conn.execute("SELECT MAX(end_ts) FROM stays WHERE status = 'closed' AND id IS NOT ?",
                                (exclude,)).fetchone()
        return row[0]

    def _advance(self, stay: dict, now: float, net: str, idle: float, events, resume: bool = False) -> dict | None:
        """resume：入室ボタンで始めた滞在で、いま操作している（判定に使わない Wi-Fi でも仮退室から戻す）。"""
        if stay["status"] == "in" and now - stay["last_seen_ts"] > self.gap:
            self._set(stay, status="tentative", tentative_since=stay["last_seen_ts"])

        if stay["status"] == "in":
            if net in (UNIV, IGNORED):
                if idle >= self.leave_after:
                    return self._close(stay, now - idle, "idle", events)
                self._set(stay, last_seen_ts=now)
                return stay
            if net == OFFLINE:
                self._set(stay, status="tentative", tentative_since=stay["last_seen_ts"])
                return stay
            return self._close(stay, stay["last_seen_ts"], "offcampus", events)

        since = stay["tentative_since"]
        expired = now - since >= self.leave_after
        if net == UNIV or (resume and net == IGNORED):
            if expired:
                return self._close(stay, since, "timeout", events)
            self._set(stay, status="in", tentative_since=None, last_seen_ts=now)
            return stay
        if net in (OFFLINE, IGNORED):
            return self._close(stay, since, "timeout", events) if expired else stay
        return self._close(stay, since, "offcampus", events)

    def _set(self, stay: dict, **fields):
        stay.update(fields)
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE stays SET {cols} WHERE id = ?", (*fields.values(), stay["id"]))

    def _close(self, stay: dict, end: float, reason: str, events) -> None:
        if end - stay["start_ts"] < MIN_STAY_SEC:
            self.conn.execute("DELETE FROM stays WHERE id = ?", (stay["id"],))
            events.append(("removed", stay["id"]))
            return None
        self._set(stay, status="closed", end_ts=max(end, stay["start_ts"]), end_reason=reason,
                  tentative_since=None)
        events.append(("closed", stay["id"]))
        return None

    # ---- 表示用 ----

    def status_view(self, now: float) -> dict:
        stay = self.open_stay()
        if stay is None:
            return {"state": "out"}
        if stay["status"] == "tentative":
            if now - stay["tentative_since"] >= self.leave_after:
                return {"state": "out"}
            return {"state": "tentative", "since": stay["tentative_since"], "stay_start": stay["start_ts"]}
        return {"state": "in", "since": stay["start_ts"], "stay_start": stay["start_ts"]}


def stay_end(stay: dict, now: float, leave_after: float) -> tuple[float, str]:
    """表示に使う終了時刻と状態（closed / in / tentative）。"""
    if stay["status"] == "closed":
        return stay["end_ts"], "closed"
    if stay["status"] == "tentative":
        since = stay["tentative_since"]
        return since, ("closed" if now - since >= leave_after else "tentative")
    return now, "in"


def _fmt_when(ts: float, ref: float) -> str:
    """時刻（ref と別の日なら日付も）。"""
    d = datetime.fromtimestamp(ts)
    day = "" if d.date() == datetime.fromtimestamp(ref).date() else f"{d.month}/{d.day} "
    return f"{day}{d.hour}:{d.minute:02d}"


def save_stay(conn, stay_id: int | None, start: float, end: float | None, now: float | None = None):
    """ダッシュボード・スマホ版からの手動修正。これからの時刻や、ほかの滞在と重なる時刻は受け付けない
    （重なると滞在時間を二重に数えてしまう）。end が None なら、開いている滞在の入室の時刻だけを直す。"""
    now = time.time() if now is None else now
    if end is not None and end <= start:
        raise ValueError("退室は入室より後の時刻にしてください")
    if start > now or (end is not None and end > now):
        raise ValueError("これからの時刻は入れられません")
    if stay_id is not None:
        row = conn.execute("SELECT status, tentative_since FROM stays WHERE id = ?", (stay_id,)).fetchone()
        if row is None:
            raise ValueError("直そうとした滞在が見つかりません")
        if end is None and row["status"] == "tentative" and start > row["tentative_since"]:
            raise ValueError("入室は仮退室の時刻より前にしてください")
    until = end if end is not None else now  # 開いたままの滞在は、いままで続いている
    for other in conn.execute("SELECT * FROM stays WHERE id IS NOT ?", (stay_id,)):
        other_end = (other["end_ts"] if other["status"] == "closed"
                     else other["tentative_since"] if other["status"] == "tentative" else now)
        if start < other_end and other["start_ts"] < until:
            raise ValueError(f"ほかの入退室（{_fmt_when(other['start_ts'], start)}〜{_fmt_when(other_end, start)}）と重なっています")
    if stay_id is None:
        if end is None:
            raise ValueError("追加するときは退室時刻も入れてください")
        conn.execute("INSERT INTO stays (start_ts, end_ts, status, end_reason, manual) "
                     "VALUES (?, ?, 'closed', 'manual', 1)", (start, end))
        return
    if end is None:
        conn.execute("UPDATE stays SET start_ts = ?, manual = 1 WHERE id = ?", (start, stay_id))
    else:
        conn.execute("UPDATE stays SET start_ts = ?, end_ts = ?, status = 'closed', end_reason = 'manual', "
                     "tentative_since = NULL, manual = 1 WHERE id = ?", (start, end, stay_id))


def delete_stay(conn, stay_id: int):
    conn.execute("DELETE FROM stays WHERE id = ?", (stay_id,))
