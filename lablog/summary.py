"""記録を 1 日分・1 か月分に集計する（ダッシュボードと日報で使う）。"""

import calendar
import os
from collections import Counter, defaultdict
from datetime import date

from .categorize import BROWSER_EXES, Categorizer, SeenTitles, TitleDomainMap, app_label, clean_title
from .days import day_range, fmt_date_jp, logical_date
from .presence import stay_end

TIMELINE_MERGE_GAP = 60  # これ以下の隙間なら同じ分類の区間をつなげて表示する
AI_HOSTS = {"claude.exe"}  # ウィンドウ名が「Claude」だけになるアプリ。Claude Code の会話の題名で補う
SESSION_MARGIN = 120       # ウィンドウを使っていた時間の前後この秒数のやりとりも、その会話とみなす


def _claude_minutes(conn, t0: float, t1: float) -> dict[float, list[str]]:
    """分ごとの、やりとりがあった Claude Code の会話（表示用の名前）。"""
    out: dict[float, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT a.minute, s.title, s.folder FROM ai_activity a JOIN ai_sessions s "
                          "ON s.source = a.source AND s.session_id = a.session_id "
                          "WHERE a.minute >= ? AND a.minute < ?", (t0, t1)):
        name = r["title"] or "（題名なし）"
        out[r["minute"]].append(f"Claude Code：{name}（{r['folder']}）" if r["folder"] else f"Claude Code：{name}")
    return out


def _busiest_session(minutes: dict[float, list[str]], start: float, end: float) -> str | None:
    counts: Counter = Counter()
    m = (start - SESSION_MARGIN) // 60 * 60
    while m <= end + SESSION_MARGIN:
        counts.update(minutes.get(m, ()))
        m += 60
    return counts.most_common(1)[0][0] if counts else None


def _stays(conn, t0, t1, now, leave_after):
    out = []
    for row in conn.execute("SELECT * FROM stays WHERE start_ts >= ? AND start_ts < ? ORDER BY start_ts",
                            (t0, t1)):
        stay = dict(row)
        end, state = stay_end(stay, now, leave_after)
        out.append({"id": stay["id"], "start": stay["start_ts"], "end": end, "state": state,
                    "duration": max(0.0, end - stay["start_ts"]), "reason": stay["end_reason"],
                    "manual": bool(stay["manual"])})
    return out


def _rel_path(folder: str, path: str) -> str:
    try:
        rel = os.path.join(os.path.basename(folder.rstrip("\\/")), os.path.relpath(path, folder))
    except ValueError:
        rel = path
    return rel.replace("\\", "/")  # 日本語フォントで「\」が「¥」に見えるのを避ける


def day_summary(conn, settings, d: date, now: float) -> dict:
    t0, t1 = day_range(d)
    leave_after = settings.get("leave_after_hours") * 3600
    categorizer = Categorizer(settings.get("categories"))

    stays = _stays(conn, t0, t1, now, leave_after)

    visit_rows = conn.execute("SELECT ts, url, title, domain, browser FROM web_visits "
                              "WHERE ts >= ? AND ts < ? ORDER BY ts", (t0 - 3600, t1)).fetchall()
    title_map = TitleDomainMap([(clean_title(r["title"]), r["domain"]) for r in visit_rows])

    work_total = 0.0
    by_location: dict[str, float] = defaultdict(float)
    by_category: dict[str, float] = defaultdict(float)
    apps: dict[str, dict] = {}
    timeline: list[dict] = []
    browser_titles: list[str] = []
    claude = _claude_minutes(conn, t0 - 3600, t1 + 3600) if settings.get("claude_code", {}).get("enabled", True) else {}
    for seg in conn.execute("SELECT * FROM segments WHERE end_ts > ? AND start_ts < ? ORDER BY start_ts",
                            (t0, t1)):
        start, end = max(seg["start_ts"], t0), min(seg["end_ts"], t1)
        dur = end - start
        if dur <= 0:
            continue
        if seg["idle"]:
            category, label, title = "idle", "", ""
        else:
            title = clean_title(seg["title"])
            if claude and seg["exe"].lower() in AI_HOSTS:
                title = _busiest_session(claude, start, end) or title
            domain = None
            if seg["exe"].lower() in BROWSER_EXES:
                domain = title_map.lookup(title)
                browser_titles.append(title)
            category = categorizer.classify(seg["exe"], title, domain)
            label = app_label(seg["exe"])
            work_total += dur
            by_location[seg["location"] or "その他"] += dur
            by_category[category] += dur
            app = apps.setdefault(label, {"app": label, "total": 0.0, "category": category, "titles": {}})
            app["total"] += dur
            entry = app["titles"].setdefault(title, [0.0, category])
            entry[0] += dur

        last = timeline[-1] if timeline else None
        if last and last["cat"] == category and start - last["end"] <= TIMELINE_MERGE_GAP:
            last["end"] = max(last["end"], end)
        else:
            last = {"cat": category, "start": start, "end": end, "items": {}}
            timeline.append(last)
        if title or label:
            key = f"{label}｜{title}" if title else label
            last["items"][key] = last["items"].get(key, 0.0) + dur

    for block in timeline:
        items = sorted(block.pop("items").items(), key=lambda kv: -kv[1])[:4]
        block["top"] = [{"label": k, "sec": v} for k, v in items]

    app_list = []
    for app in sorted(apps.values(), key=lambda a: -a["total"]):
        titles = sorted(app["titles"].items(), key=lambda kv: -kv[1][0])
        app_list.append({"app": app["app"], "total": app["total"], "category": app["category"],
                         "titles": [{"title": t or "（タイトルなし）", "sec": s, "category": c}
                                    for t, (s, c) in titles[:20]],
                         "more": max(0, len(titles) - 20)})

    files: dict[str, dict] = {}
    for ev in conn.execute("SELECT * FROM file_events WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1)):
        f = files.setdefault(ev["path"], {"path": ev["path"], "rel": _rel_path(ev["folder"], ev["path"]),
                                          "saves": 0, "added": False, "removed": False, "renamed_from": None,
                                          "first": ev["ts"], "last": ev["ts"], "size": None})
        f["last"] = ev["ts"]
        if ev["action"] == "modified":
            f["saves"] += 1
        elif ev["action"] == "added":
            f["added"] = True
        elif ev["action"] == "removed":
            f["removed"] = True
        elif ev["action"] == "renamed":
            f["renamed_from"] = ev["old_path"] and os.path.basename(ev["old_path"])
        if ev["size"] is not None:
            f["size"] = ev["size"]
    file_list = sorted(files.values(), key=lambda f: (-(f["saves"] + f["added"]), -f["last"]))

    commits = [{"repo": os.path.basename(r["repo"]), "repo_path": r["repo"], "ts": r["ts"],
                "subject": r["subject"], "hash": r["hash"][:7]}
               for r in conn.execute("SELECT * FROM commits WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1))]

    # 開いたままのタブがバックグラウンドで読み込まれただけのページは除く（画面に表示したページだけ）
    seen = SeenTitles(browser_titles)
    hidden = 0
    domains: dict[str, dict] = {}
    for v in visit_rows:
        if not (t0 <= v["ts"] < t1):
            continue
        if v["title"] not in seen:
            hidden += 1
            continue
        dom = domains.setdefault(v["domain"] or "(不明)", {"domain": v["domain"] or "(不明)", "count": 0,
                                                            "pages": {}})
        dom["count"] += 1
        # 同じタイトルのページ（クエリだけ違う URL など）は 1 つにまとめる
        page = dom["pages"].setdefault(v["title"].strip() or v["url"],
                                       {"url": v["url"], "title": v["title"], "count": 0, "first": v["ts"]})
        page["count"] += 1
    web = []
    for dom in sorted(domains.values(), key=lambda x: -x["count"]):
        pages = sorted(dom["pages"].values(), key=lambda p: p["first"])
        web.append({"domain": dom["domain"], "count": dom["count"],
                    "category": categorizer.by_domain(dom["domain"]) or "その他",
                    "pages": pages[:30], "more": max(0, len(pages) - 30)})

    seen = set()
    recent = []
    for r in conn.execute("SELECT ts, path FROM recent_files WHERE ts >= ? AND ts < ? ORDER BY ts DESC",
                          (t0, t1)):
        if r["path"] not in seen:
            seen.add(r["path"])
            recent.append({"ts": r["ts"], "path": r["path"], "name": os.path.basename(r["path"])})

    screen_texts = [{"ts": r["ts"], "app": app_label(r["exe"]), "text": r["text"]}
                    for r in conn.execute("SELECT ts, exe, text FROM screen_texts WHERE ts >= ? AND ts < ? ORDER BY ts",
                                          (t0, t1))]

    report = conn.execute("SELECT * FROM reports WHERE date = ?", (d.isoformat(),)).fetchone()

    return {
        "date": d.isoformat(), "date_label": fmt_date_jp(d), "range": [t0, t1], "now": now,
        "is_today": logical_date(now) == d,
        "stays": stays, "stay_total": sum(s["duration"] for s in stays),
        "work_total": work_total, "work_by_location": dict(by_location),
        "categories": [{"name": n, "sec": by_category[n]} for n in categorizer.names if by_category.get(n)],
        "category_names": categorizer.names,
        "apps": app_list, "timeline": timeline, "files": file_list[:200], "files_total": len(file_list),
        "commits": commits, "web": web, "web_total": sum(d_["count"] for d_ in web), "web_hidden": hidden,
        "recent": recent[:100], "screen_texts": screen_texts,
        "report": dict(report) if report else None,
    }


def month_summary(conn, settings, year: int, month: int, now: float) -> dict:
    days_in_month = calendar.monthrange(year, month)[1]
    dates = [date(year, month, i) for i in range(1, days_in_month + 1)]
    t0, t1 = day_range(dates[0])[0], day_range(dates[-1])[1]
    leave_after = settings.get("leave_after_hours") * 3600
    per_day = {d: {"date": d.isoformat(), "stay": 0.0, "work": 0.0, "by_location": defaultdict(float),
                   "first_in": None, "last_out": None} for d in dates}

    for stay in _stays(conn, t0, t1, now, leave_after):
        entry = per_day.get(logical_date(stay["start"]))
        if entry is None:
            continue
        entry["stay"] += stay["duration"]
        entry["first_in"] = min(entry["first_in"] or stay["start"], stay["start"])
        entry["last_out"] = max(entry["last_out"] or 0, stay["end"])

    for seg in conn.execute("SELECT start_ts, end_ts, location FROM segments WHERE idle = 0 AND end_ts > ? "
                            "AND start_ts < ?", (t0, t1)):
        s, e = max(seg["start_ts"], t0), min(seg["end_ts"], t1)
        while s < e:
            d = logical_date(s)
            boundary = day_range(d)[1]
            piece = min(e, boundary) - s
            entry = per_day.get(d)
            if entry is not None:
                entry["work"] += piece
                entry["by_location"][seg["location"] or "その他"] += piece
            s = min(e, boundary)

    reported = {r["date"] for r in conn.execute("SELECT date FROM reports WHERE date >= ? AND date <= ? "
                                                 "AND text != ''", (dates[0].isoformat(), dates[-1].isoformat()))}
    days = []
    for d in dates:
        entry = per_day[d]
        entry["by_location"] = dict(entry["by_location"])
        entry["has_report"] = d.isoformat() in reported
        days.append(entry)
    return {"month": f"{year:04d}-{month:02d}", "days": days,
            "stay_total": sum(x["stay"] for x in days), "work_total": sum(x["work"] for x in days),
            "stay_days": sum(1 for x in days if x["stay"] > 0)}
