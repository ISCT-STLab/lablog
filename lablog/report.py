"""日報の下書きを作る。

ローカル AI（Ollama）が動いていればそれで要約し、無ければ集計から定型文を作る。
Ollama へは PC 内（127.0.0.1）にしか接続せず、プロキシ設定も使わない。
"""

import json
import logging
import re
import threading
import time
import urllib.request
from datetime import date

from .config import OTHER_CATEGORY, is_cloud_model, is_local_url
from .days import fmt_duration, fmt_hm
from .summary import day_summary

log = logging.getLogger(__name__)

_no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BROWSER_LABELS = {"Chrome", "Edge"}
READING = {"論文・調べもの", "コーディング・解析"}  # 「読んだ・調べたもの」の材料にする分類
SKIP_IN_DIGEST = {"メール・連絡", "ファイル整理"}  # 日報の「主な作業」の材料にしない分類
GENERIC_TITLES = {"", "（タイトルなし）", "claude", "chatgpt", "powershell", "windows powershell",
                  "コマンド プロンプト", "command prompt", "ターミナル", "terminal", "新しいタブ", "new tab"}
# 読んだものとして書かないページ（検索結果・ログイン画面）
NOISE_PAGE = re.compile(r"(?:\s[-–—|]\s(?:Google\s?検索|Google Search|Bing|Yahoo!検索)\s*$)"
                        r"|サインイン|ログイン|Sign in|Sign-in|Log in|Login", re.I)
LOCAL_DOMAINS = {"", "127.0.0.1", "localhost", "::1", "file"}  # PC 内のページ（LabLog のダッシュボードなど）
MIN_WINDOW_SEC = 300      # これより短く使ったウィンドウは材料にしない
WINDOWS_PER_CATEGORY = 6
MAX_FILES, MAX_PAGES, MAX_RECENT = 20, 15, 15
MAX_SCREEN_TEXTS, SCREEN_TEXT_CHARS = 6, 300  # 画面から読み取った文字は、1 日の中から間をあけて選ぶ
_generate_lock = threading.Lock()

SYSTEM_PROMPT = """あなたは大学院生の1日の作業ログから「日報の下書き」を作るアシスタントです。

ルール:
- 日本語で、ログから読み取れる事実だけを書く。ログに無いこと（作業の目的や成果など）を推測で書き足さない。
- 細かい操作やページを並べず、テーマ（研究のどの部分か、どの資料か）ごとにまとめて、1日の流れが一目で分かる長さにする。
- 「主な作業」は2〜5項目。1項目は1行（40字くらいまで）。同じファイル・同じ資料・同じテーマの作業は1項目にまとめる。
- 15分未満の作業や、メール・ファイル整理などの雑務は書かない。
- 記録が少ない日は短くてよい（1項目でもよい）。開いたファイルやページの名前から、していない作業を想像して書かない。
- 「Gitコミット」がある場合は、そのメッセージを作業の説明に使う（例：「preprocess.py で前処理の正規化を修正」）。
- 具体名（ファイル名・論文タイトル・会議名など）は、主なものだけ残す。
- 「画面から読み取った文字」は、そのとき何の話題・作業だったかをつかむためだけに使う。人名・メールアドレス・会話の文章をそのまま書き写さない。
- 「主な作業」の各項目の最後に、時間の目安を「（約1時間）」のように添える。分類別・ウィンドウの時間をもとにし、それより長くしない。
- 「読んだ・調べたもの」は、「閲覧したページ」のうち主な論文・技術資料を最大5件。同じ論文や同じ資料のページは1件にまとめ、時間は付けない。リポジトリの設定画面など、読み物でないページは書かない。
- 出力は次の形式だけにする。前置きや締めの言葉は不要。

## 主な作業
- …

## 読んだ・調べたもの
- …（無ければ「- なし」）
"""


def reading_pages(s: dict) -> list[tuple[str, str]]:
    """「読んだ・調べたもの」の材料：論文・技術資料のサイトのページ（検索結果・ログイン画面は除く）。"""
    pages = [(p["count"], dom["domain"], p["title"]) for dom in s["web"]
             if dom["category"] in READING and dom["domain"] not in LOCAL_DOMAINS
             for p in dom["pages"] if p["title"] and not NOISE_PAGE.search(p["title"])]
    pages.sort(key=lambda x: -x[0])
    return [(d, t) for _, d, t in pages]


def build_digest(s: dict) -> str:
    """集計結果を、AI に渡す短いテキストにまとめる。"""
    lines = [f"日付: {s['date_label']}"]
    if s["stays"]:
        spans = "、".join(f"{fmt_hm(x['start'])}〜{fmt_hm(x['end'])}" for x in s["stays"])
        lines.append(f"大学にいた時間: {spans}（合計 {fmt_duration(s['stay_total'])}）")
    else:
        lines.append("大学にいた時間: なし")
    loc = "、".join(f"{k} {fmt_duration(v)}" for k, v in s["work_by_location"].items())
    lines.append(f"PC作業時間: {fmt_duration(s['work_total'])}（{loc}）" if loc else "PC作業時間: なし")
    if s["categories"]:
        lines.append("分類別: " + " / ".join(f"{c['name']} {fmt_duration(c['sec'])}" for c in s["categories"]))

    # 小さいモデルは渡したものを全部並べがちなので、日報に書かないものは最初から渡さない：
    # 分類できなかった Web サイト（動画・買い物など）、メール・ファイル整理、中身の分からないウィンドウ、短すぎるもの
    by_cat: dict[str, list] = {}
    for app in s["apps"]:
        for t in app["titles"]:
            if (t["sec"] < MIN_WINDOW_SEC or t["category"] in SKIP_IN_DIGEST
                    or (app["app"] in BROWSER_LABELS and t["category"] == OTHER_CATEGORY)
                    or t["title"].strip().lower() in GENERIC_TITLES | {app["app"].lower()}):
                continue
            by_cat.setdefault(t["category"], []).append((t["sec"], app["app"], t["title"]))
    if by_cat:
        lines.append("\n使っていたウィンドウ（分類ごと・長い順）:")
        totals = {c["name"]: c["sec"] for c in s["categories"]}
        for cat in sorted(by_cat, key=lambda c: -totals.get(c, 0)):
            lines.append(f"【{cat}】計 {fmt_duration(totals.get(cat, 0))}")
            items = sorted(by_cat[cat], reverse=True)[:WINDOWS_PER_CATEGORY]
            lines += [f"- [{a}] {t[:120]}（{fmt_duration(sec)}）" for sec, a, t in items]

    if s["files"]:
        lines.append("\n編集したファイル:")
        for f in s["files"][:MAX_FILES]:
            notes = []
            if f["added"]:
                notes.append("新規作成")
            if f["saves"]:
                notes.append(f"保存{f['saves']}回")
            if f["removed"]:
                notes.append("削除")
            if f["renamed_from"]:
                notes.append(f"{f['renamed_from']} から名前変更")
            lines.append(f"- {f['rel']}（{'、'.join(notes) or '変更'}）")

    if s["commits"]:
        lines.append("\nGitコミット:")
        lines += [f"- [{c['repo']}] {c['subject']}（{fmt_hm(c['ts'])}）" for c in s["commits"][:50]]

    texts = s.get("screen_texts") or []
    if texts:
        step = max(1, len(texts) // MAX_SCREEN_TEXTS)
        lines.append("\n画面から読み取った文字（ウィンドウ名から内容が分からないアプリ。話題をつかむためだけに使う）:")
        for t in texts[::step][:MAX_SCREEN_TEXTS]:
            body = " ".join(t["text"].split())[:SCREEN_TEXT_CHARS]
            lines.append(f"- [{t['app']} {fmt_hm(t['ts'])}] {body}")

    pages = reading_pages(s)
    if pages:
        lines.append("\n閲覧したページ（論文・技術資料のサイト、見た回数の多い順）:")
        lines += [f"- {d}: {t[:120]}" for d, t in pages[:MAX_PAGES]]

    if s["recent"]:
        lines.append("\n開いたファイル:")
        lines += [f"- {r['name']}" for r in s["recent"][:MAX_RECENT]]
    return "\n".join(lines)


def rule_based_report(s: dict) -> str:
    """AI を使わずに集計から作る日報。"""
    out = ["## 主な作業（自動集計）"]
    by_cat: dict[str, list[str]] = {}
    for app in s["apps"]:
        names = [t["title"] for t in app["titles"][:3] if t["sec"] >= 120 and t["title"] != "（タイトルなし）"]
        by_cat.setdefault(app["category"], []).extend(names)
    for c in s["categories"]:
        examples = "、".join(list(dict.fromkeys(by_cat.get(c["name"], [])))[:3])
        out.append(f"- {c['name']}（{fmt_duration(c['sec'])}）" + (f"：{examples}" if examples else ""))
    if len(out) == 1:
        out.append("- 記録なし")
    if s["files"]:
        out.append("\n## 編集したファイル")
        out += [f"- {f['rel']}" + (f"（保存{f['saves']}回）" if f["saves"] else "") for f in s["files"][:5]]
    if s["commits"]:
        out.append("\n## コミット")
        out += [f"- [{c['repo']}] {c['subject']}" for c in s["commits"][:10]]
    papers = [t for d, t in reading_pages(s)]
    if papers:
        out.append("\n## 読んだ・調べたもの")
        out += [f"- {t[:100]}" for t in list(dict.fromkeys(papers))[:5]]
    return "\n".join(out)


def ollama_status(cfg: dict) -> dict:
    url = cfg.get("url", "")
    if not is_local_url(url):
        return {"running": False, "error": "PC 内の URL ではありません"}
    try:
        with _no_proxy.open(url.rstrip("/") + "/api/tags", timeout=3) as r:
            # クラウドモデル（PC の外で動く）は使わない。選べないように一覧からも除く
            models = [m["name"] for m in json.load(r).get("models", [])
                      if not m.get("remote_host") and not is_cloud_model(m["name"])]
    except Exception:
        return {"running": False, "models": [], "model_ready": False}
    want = cfg.get("model", "")
    ready = any(m == want or m.split(":")[0] == want for m in models)
    return {"running": True, "models": models, "model_ready": ready}


def ollama_generate(cfg: dict, digest: str) -> str:
    url = cfg.get("url", "")
    if not is_local_url(url):
        raise RuntimeError("Ollama の URL が PC 内ではないため送信しません")
    model = cfg.get("model", "")
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": "次の作業ログから日報の下書きを作ってください。\n\n" + digest}],
        "stream": False,
        "keep_alive": "2m",
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    if model.startswith("qwen3"):
        body["think"] = False
    req = urllib.request.Request(url.rstrip("/") + "/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _no_proxy.open(req, timeout=900) as r:
        text = json.load(r)["message"]["content"]
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    return _tidy(text)


def _tidy(text: str) -> str:
    """AI の出力の小さな崩れを直す（「- - なし」や、項目があるのに残った「なし」など）。"""
    sections: list[list[str]] = []
    for line in text.strip().splitlines():
        line = re.sub(r"^(\s*)-\s+-\s+", r"\1- ", line.rstrip())
        if line.startswith("#") or not sections:
            sections.append([line])
        else:
            sections[-1].append(line)
    out = []
    for sec in sections:
        items = [l for l in sec[1:] if l.strip().startswith("-")]
        if len(items) > 1:
            sec = [l for l in sec if not re.fullmatch(r"\s*-\s*なし\s*", l)]
        out.extend(sec)
    return "\n".join(out)


def generate_report(conn, settings, d: date, force: bool = False) -> dict:
    """日報を作って保存する。自分で書き直した日報は force でなければ上書きしない。"""
    with _generate_lock:
        row = conn.execute("SELECT * FROM reports WHERE date = ?", (d.isoformat(),)).fetchone()
        if row and row["edited"] and not force:
            return dict(row)
        now = time.time()
        s = day_summary(conn, settings, d, now)
        if not s["work_total"] and not s["stays"] and not s["files"]:
            return dict(row) if row else {"date": d.isoformat(), "text": "", "edited": 0, "source": None}
        cfg = settings.get("ollama")
        source = "rule"
        text = None
        if cfg.get("enabled", True) and ollama_status(cfg).get("model_ready"):
            try:
                started = time.time()
                text = ollama_generate(cfg, build_digest(s))
                source = f"ai:{cfg.get('model')}"
                log.info("日報を AI で作成しました（%s, %.0f 秒）", d, time.time() - started)
            except Exception:
                log.exception("ローカル AI での日報作成に失敗しました。集計だけで作ります")
        if not text:
            text = rule_based_report(s)
        # AI の応答を待つあいだに画面で保存した日報は上書きしない（作り始めたときと同じ内容のときだけ書く）
        if row:
            cur = conn.execute("UPDATE reports SET text = ?, edited = 0, generated_ts = ?, source = ? "
                               "WHERE date = ? AND text IS ? AND edited IS ?",
                               (text, now, source, d.isoformat(), row["text"], row["edited"]))
        else:
            cur = conn.execute("INSERT OR IGNORE INTO reports (date, text, edited, generated_ts, source) "
                               "VALUES (?, ?, 0, ?, ?)", (d.isoformat(), text, now, source))
        if not cur.rowcount:
            log.info("日報を作っているあいだに保存されたため、作った内容は使いません（%s）", d)
            return dict(conn.execute("SELECT * FROM reports WHERE date = ?", (d.isoformat(),)).fetchone())
        return {"date": d.isoformat(), "text": text, "edited": 0, "generated_ts": now, "source": source}


MAX_REPORT_CHARS = 50_000  # 画面・スマホ版から保存できる日報の長さ


def save_report(conn, d: date, text: str):
    conn.execute("INSERT INTO reports (date, text, edited, generated_ts, source, saved_ts) "
                 "VALUES (?, ?, 1, NULL, 'user', ?) "
                 "ON CONFLICT(date) DO UPDATE SET text = excluded.text, edited = 1, saved_ts = excluded.saved_ts",
                 (d.isoformat(), text, time.time()))
