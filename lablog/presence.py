"""大学での滞在（入室・仮退出・退出確定）の判定。

ルール
- 大学の Wi-Fi につながる → 入室（仮退出中なら取り消して滞在を続ける）
- Wi-Fi が切れる（スリープ・シャットダウンを含む） → 仮退出
- 大学外の Wi-Fi につながる → 仮退出した時刻で退出確定
- 仮退出のまま leave_after（4 時間）たつ → 仮退出した時刻で退出確定
- 大学の Wi-Fi のまま leave_after 操作がない → 最後に操作した時刻で退出確定
- 判定に使わない Wi-Fi（テザリングなど）につながる → そのときの状態を続ける
  （在学中なら滞在を続け、仮退出中なら仮退出のまま。新しく入室にはしない）
"""

from .wifi import IGNORED, OFFLINE, UNIV


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

    def tick(self, now: float, net: str, idle: float) -> list[tuple[str, int]]:
        """1 回分の判定。起きたこと（"opened" / "closed", 滞在 id）の一覧を返す。"""
        events: list[tuple[str, int]] = []
        stay = self.open_stay()
        if stay is not None:
            stay = self._advance(stay, now, net, idle, events)
        if stay is None and net == UNIV and idle < self.idle_threshold:
            cur = self.conn.execute("INSERT INTO stays (start_ts, last_seen_ts, status) VALUES (?, ?, 'in')",
                                    (now, now))
            events.append(("opened", cur.lastrowid))
        return events

    def _advance(self, stay: dict, now: float, net: str, idle: float, events) -> dict | None:
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
        if net == UNIV:
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


def save_stay(conn, stay_id: int | None, start: float, end: float | None):
    """ダッシュボードからの手動修正。"""
    if end is not None and end <= start:
        raise ValueError("退室は入室より後の時刻にしてください")
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
