"""前面ウィンドウを一定間隔で調べ、「同じウィンドウを使っていた区間」として記録する。"""

IDLE_EXES = {"lockapp.exe"}  # ロック画面
EXCLUDED_LABEL = "(除外)"
UNRECORDED_LABEL = "(アプリを記録しない設定)"  # 設定の record.apps が off のとき（作業時間だけ残す）
MAX_TITLE = 300


class ActivityRecorder:
    def __init__(self, conn, interval_sec: float):
        self.conn = conn
        self.interval = interval_sec
        self.cur: dict | None = None  # 記録中の区間 {id, key, start, end}

    def tick(self, now: float, fg: tuple[str, str] | None, idle: float, idle_threshold: float,
             location: str, excluded: set[str], paused: bool, apps_mode: str = "title"):
        if paused:
            self.cur = None
            return
        exe, title = fg if fg else ("", "")
        is_idle = fg is None or idle >= idle_threshold or exe.lower() in IDLE_EXES
        if is_idle:
            key = ("", "", 1, location)
        else:
            if apps_mode == "off":
                exe, title = UNRECORDED_LABEL, ""
            elif exe.lower() in excluded:
                exe, title = EXCLUDED_LABEL, ""
            elif apps_mode == "app":
                title = ""
            key = (exe, title[:MAX_TITLE], 0, location)

        cur = self.cur
        contiguous = cur is not None and now - cur["end"] <= self.interval * 3
        if contiguous and cur["key"] == key:
            cur["end"] = now
            self._write_end(now)
            return

        # 新しい区間。直前の区間と途切れていなければ、前回の確認時刻から始まったとみなす。
        start = cur["end"] if contiguous else now
        if is_idle and contiguous and not cur["key"][2]:
            # 操作が止まった時点まで、作業区間をさかのぼって切り詰める
            idle_start = max(cur["start"], now - idle)
            if idle_start < cur["end"]:
                cur["end"] = idle_start
                self._write_end(idle_start)
                start = idle_start
        self._insert(start, now, key)

    def _insert(self, start: float, end: float, key: tuple):
        exe, title, idle, location = key
        row = self.conn.execute(
            "INSERT INTO segments (start_ts, end_ts, exe, title, idle, location) VALUES (?, ?, ?, ?, ?, ?)",
            (start, end, exe, title, idle, location))
        self.cur = {"id": row.lastrowid, "key": key, "start": start, "end": end}

    def _write_end(self, end: float):
        self.conn.execute("UPDATE segments SET end_ts = ? WHERE id = ?", (end, self.cur["id"]))
