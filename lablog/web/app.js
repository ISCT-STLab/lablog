// LabLog ダッシュボード。PC ではサーバー（127.0.0.1）とだけ通信する。
// スマホ用ビューアーでは同じ画面を使い、データは cloud.js が OneDrive から読む（要約だけ）。
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const view = $("#view");
const state = { status: null, settings: null, refreshTimer: null, tips: [] };
const HOUR = 3600;
const cloud = window.LabLogCloud; // ビューアーのときだけある
const touch = matchMedia("(hover: none)").matches;

// ------------------------------------------------------------ 通信

async function toJson(res) {
  const data = await res.json().catch(() => ({ error: "応答を読み取れませんでした" }));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    // 合言葉の Cookie が無い（ブックマークから直接開いた・別のアカウントから開いた）
    if (data.code === "key_required") err.name = "KeyRequired";
    throw err;
  }
  return data;
}
// ---- 処理中の印：通信が少し長くかかったら、画面の上に細い帯を流す（自動の更新では出さない：quiet）。
// ボタンを押してすぐ始まった通信は、終わるまでそのボタンに回る印を付け、二重に押せないようにする
let pending = 0, barTimer = null, pressed = null;
const busyCount = new WeakMap();
document.addEventListener("click", (e) => {
  pressed = e.target.closest("button, a.btn");
  setTimeout(() => { pressed = null; }, 0); // 押した処理の中で（最初の待ちより前に）始まった通信だけ
}, true);
function track(promise, quiet = false) {
  const btn = pressed;
  let btnTimer = null;
  if (btn) {
    busyCount.set(btn, (busyCount.get(btn) || 0) + 1);
    btnTimer = setTimeout(() => btn.classList.add("busy"), 120); // すぐ終わるときは出さない
  }
  if (!quiet && pending++ === 0) barTimer = setTimeout(() => document.body.classList.add("loading"), 250);
  return promise.finally(() => {
    if (btn) {
      clearTimeout(btnTimer);
      const n = busyCount.get(btn) - 1;
      busyCount.set(btn, n);
      if (n <= 0) btn.classList.remove("busy");
    }
    if (!quiet && --pending === 0) {
      clearTimeout(barTimer);
      document.body.classList.remove("loading");
    }
  });
}

const api = cloud ? {
  get: (path, { quiet } = {}) => track(cloud.api.get(path), quiet),
  // 直した内容は PC が取り込むまで「反映待ち」になるので、上部のお知らせも出し直す
  post: (path, body) => track(cloud.api.post(path, body)).then((res) => { refreshStatus(); return res; }),
} : {
  get: (path, { quiet } = {}) => track(fetch(path, { cache: "no-store" }).then(toJson), quiet),
  post: (path, body) => track(fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-LabLog": "1" },
    body: JSON.stringify(body || {}),
  }).then(toJson)),
};

// ------------------------------------------------------------ 書式

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDur(sec) {
  const m = Math.round((sec || 0) / 60);
  if (sec > 0 && m === 0) return "1分未満";
  const h = Math.floor(m / 60), r = m % 60;
  if (h && r) return `${h}時間${r}分`;
  if (h) return `${h}時間`;
  return `${r}分`;
}
// 数字はそのままの大きさで、単位（年・月・日・時間・分・曜日など）だけ小さくする（style.css の .u）
const numUnits = (text) => esc(text).replace(/[^\d:.\s]+/g, (u) => `<span class="u">${u}</span>`);
// 日付の見出し：年は小さく添え、スマホ幅では月日の上の段に置く
function dateHTML(label) {
  const [, year = "", rest = label] = /^(\d+年)?(.*)$/.exec(label);
  return `<span class="date-text">${year ? `<span class="year">${numUnits(year)}</span>` : ""}<span class="md">${numUnits(rest)}</span></span>`;
}
// 現在時刻（ヘッダー）。10 秒ごとに書き換える
function nowHM() {
  const d = new Date();
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}
const tickClock = () => { $("#clock").textContent = nowHM(); };
tickClock();
setInterval(tickClock, 10000);
function fmtClock(sec) {
  const m = Math.round((sec || 0) / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}
function fmtHM(ts) {
  const d = new Date(ts * 1000);
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}
// その日（朝5時区切り）の基準日と比べて、日付をまたいでいれば「翌」を付ける
function fmtHMDay(ts, dayStart) {
  const base = new Date(dayStart * 1000), d = new Date(ts * 1000);
  const nextDay = d.getDate() !== base.getDate() || d.getMonth() !== base.getMonth();
  return (nextDay ? "翌" : "") + fmtHM(ts);
}
function toLocalInput(ts) {
  const d = new Date(ts * 1000 - new Date(ts * 1000).getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 16);
}
function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function addDays(iso, n) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return isoDate(d);
}
function addMonths(ym, n) {
  const [y, m] = ym.split("-").map(Number);
  const d = new Date(y, m - 1 + n, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}
function fmtWhen(ts) {
  const d = new Date(ts * 1000), now = new Date();
  return d.toDateString() === now.toDateString() ? fmtHM(ts) : `${d.getMonth() + 1}/${d.getDate()} ${fmtHM(ts)}`;
}
function safeHref(url) {
  return /^(https?|file):/i.test(url) ? esc(url) : "#";
}
// action：{ label, run }。付けたときは長めに出し、押せるようにする
function flash(msg, action = null) {
  $$(".toast-msg").forEach((t) => t.remove());
  const el = document.createElement("div");
  el.className = "toast-msg";
  el.textContent = msg;
  if (action) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = action.label;
    b.addEventListener("click", () => { el.remove(); action.run(); });
    el.append(b);
  }
  document.body.append(el);
  setTimeout(() => el.remove(), action ? 7000 : 2600);
}

// ---- 作業量の演出：その日の作業時間で、背景（壁紙）とカードを少しずつ豪華にする（style.css）

const LEVEL_HOURS = [1, 3, 5, 7]; // この時間を超えるごとに 1 段階ずつ上がる（最大 4）
// PC作業時間を中心に、大学に長くいた日（PC を閉じてゼミに出ていたなど）も少し上がるようにする
const effortSec = (work, stay) => Math.max(work || 0, (stay || 0) * 0.75);
const levelOf = (sec) => LEVEL_HOURS.filter((h) => sec >= h * HOUR).length;
// 見た目を確かめるときは、URL に ?level=0〜4 を付けると段階を、?theme=light で明るい表示を固定できる
const preview = new URLSearchParams(location.search);
const forcedLevel = /^[0-4]$/.test(preview.get("level") || "") ? preview.get("level") : null;
if (preview.get("theme") === "light") document.documentElement.dataset.theme = "light";
function setLevel(level) {
  document.body.dataset.level = forcedLevel ?? String(level);
  paintEdge();
}

// iPhone・iPad では、ページは時計の下から始まり、時計の部分はホーム画面版では theme-color、Safari ではページの地の色で塗られる。
// 壁紙は時計の裏まで描けないので、そこを壁紙の上の端に近い色（青と緑の光を地の色に混ぜた色）で塗り、つなぎ目を目立たなくする
function edgeColor() {
  const cs = getComputedStyle(document.body);
  const rgb = (name) => {
    const m = /^#([0-9a-f]{6})$/i.exec(cs.getPropertyValue(name).trim());
    return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)) : null;
  };
  const page = rgb("--page") || [10, 10, 10];
  const a = rgb("--wa") || page, b = rgb("--wb") || page;
  // 光の濃さ。0.75 は iPhone の画面写真で、壁紙の上の端の色と合わせた値
  const s = (parseFloat(cs.getPropertyValue("--w-opacity")) || 0) * (parseFloat(cs.getPropertyValue("--w-dim")) || 1) * 0.75;
  return "#" + page.map((p, i) => Math.round(p + ((a[i] + b[i]) / 2 - p) * s).toString(16).padStart(2, "0")).join("");
}
function paintEdge() {
  const color = edgeColor();
  document.body.style.setProperty("--edge", color);
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => { m.content = color; });
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintEdge);

function levelHTML(day) {
  const sec = effortSec(day.work_total, day.stay_total);
  const level = forcedLevel != null ? Number(forcedLevel) : levelOf(sec);
  const next = LEVEL_HOURS[level];
  const tipHtml = `<b>この日のフロー ${level} / 4</b>`
    + `<div class="muted">${next ? `あと ${fmtDur(next * HOUR - sec)} でフローが深まります` : "いちばん深いフローです"}</div>`;
  return `<span class="level" ${tipAttr(tipHtml)} role="img" aria-label="この日のフロー ${level} / 4"><span class="level-name">フロー</span>${
    [0, 1, 2, 3].map((i) => `<i class="${i < level ? "on" : ""}"></i>`).join("")}</span>`;
}

// 分類の色：設定の順番で固定。9 番目以降と「その他」はグレー。
function catColor(name, names) {
  if (name === "idle") return "var(--c-idle)";
  const i = names.indexOf(name);
  if (name === "その他" || i < 0 || i >= 8) return "var(--c-other)";
  return `var(--c${i + 1})`;
}

// ------------------------------------------------------------ ツールチップ

function tipAttr(html) {
  state.tips.push(html);
  return `data-tip="${state.tips.length - 1}"`;
}
const tip = $("#tip");
function placeTip(x0, y0) {
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = x0 + pad, y = y0 + pad;
  if (x + w > innerWidth - 8) x = x0 - w - pad;
  if (y + h > innerHeight - 8) y = y0 - h - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
function showTip(el) {
  tip.innerHTML = state.tips[Number(el.dataset.tip)] || "";
  tip.hidden = false;
}
// マウスは重ねたときに出す。タッチは触れた場所に出す（リンクやボタンはそのまま押せるようにする）
document.addEventListener("pointerover", (e) => {
  if (e.pointerType !== "mouse") return;
  const el = e.target.closest("[data-tip]");
  if (!el) { tip.hidden = true; return; }
  showTip(el);
});
document.addEventListener("pointermove", (e) => {
  if (e.pointerType === "mouse" && !tip.hidden) placeTip(e.clientX, e.clientY);
});
document.addEventListener("pointerdown", (e) => {
  if (e.pointerType === "mouse") return;
  const el = e.target.closest("[data-tip]");
  if (!el || el.closest("a, button")) { tip.hidden = true; return; }
  showTip(el);
  placeTip(e.clientX, e.clientY);
});
document.addEventListener("mouseleave", () => { tip.hidden = true; });
window.addEventListener("scroll", () => { if (touch) tip.hidden = true; }, { passive: true });

// ------------------------------------------------------------ 上部バー

const NET_LABEL = { univ: "研究室", home: "自宅", ignored: "判定に使わない", other: "未登録", offline: "" };
// 記録の中の場所の名前（"大学" など）は変えず、表示するときだけ置き換える
const PLACE_LABEL = { "大学": "研究室" };
const placeName = (key) => PLACE_LABEL[key] || key;
// 入退室の判定方法（wifi / activity：この PC の操作 / manual：手動 / off：記録しない）。ビューアーでは設定が無いので wifi
const presenceMode = () => (state.settings && state.settings.settings.presence || {}).mode || "wifi";
const PRESENCE_LABEL = { activity: "この PC の操作で判定", manual: "手動で記録", off: "入退室は記録しません" };
// 判定方法の説明（設定の画面と初期設定の画面で使う）
const presenceMethods = [
  ["wifi", "Wi-Fi で判定", "研究室の Wi-Fi に接続したら入室、それ以外の Wi-Fi に接続したら退室にします。Wi-Fi が切れたら仮退室にし、一定時間戻らなければ退室を確定します。"],
  ["activity", "この PC の操作で判定", "この PC を操作し始めたら入室、操作しなくなったら（ロックしたら）仮退室にし、一定時間戻らなければ退室を確定します。"],
  ["manual", "手動で記録", "「入室」「退室」ボタンを押して記録します。"],
  ["off", "記録しない", "PC作業時間だけを記録します。"],
];

// いま記録しているもの（ヘッダーの「記録中」に添える）
function recordingItems() {
  const st = state.settings && state.settings.settings;
  if (!st) return [];
  const r = st.record || {};
  const items = [];
  if (presenceMode() !== "off") items.push("入退室");
  if (r.apps === "title") items.push("アプリとウィンドウ名");
  else if (r.apps === "app") items.push("アプリ名");
  if (r.files) items.push("ファイル");
  if (r.git) items.push("Git");
  if (r.recent_files) items.push("最近使ったファイル");
  if (Object.values(st.browsers || {}).some(Boolean)) items.push("閲覧履歴");
  if (st.claude_code && st.claude_code.enabled) items.push("Claude Code");
  if (st.screen_text && st.screen_text.enabled) items.push("画面の文字");
  if (st.cloud && st.cloud.enabled) items.push("スマホ版へ送信");
  return items;
}

function renderStatus() {
  const s = state.status;
  if (!s) return;
  if (cloud) return renderCloudStatus(s);
  const ready = !(state.settings && state.settings.settings.setup_done === false); // 初期設定の前は何も記録しない
  $("#status").innerHTML = !ready ? ""
    : s.paused ? `<span class="pill paused" title="作業内容の記録を止めています。入退室の判定は続けます。"><span class="dot"></span>一時停止中</span>`
    : `<span class="pill rec" title="${esc(`記録しているもの：${recordingItems().join("・") || "なし"}`)}"><span class="dot"></span>記録中</span>`;
  const info = $("#presence-info");
  if (info) info.innerHTML = presenceInfoHTML();
  const btn = $("#pause");
  const label = s.paused ? "再開" : "一時停止";
  btn.innerHTML = `${s.paused ? ICON_PLAY : ICON_STOP}<span class="btn-label">${label}</span>`;
  btn.setAttribute("aria-label", s.paused ? "記録を再開" : "記録を一時停止");
  btn.title = s.paused ? "記録を再開" : "記録を一時停止";
  btn.classList.toggle("btn-primary", !!s.paused);
  renderNotices();
}

// 入退室の判定に使っているもの（Wi-Fi など）。入退室状況のタイルに出す
function presenceInfoHTML() {
  const s = state.status;
  if (!s) return "";
  // 設定を読めていれば設定のほう（保存した直後から切り替わる。記録の処理から届く s.mode は数秒遅れる）
  const mode = state.settings ? presenceMode() : (s.mode || "wifi");
  const parts = [];
  if (s.remote) parts.push("リモートデスクトップで接続中");
  if (mode === "wifi") {
    parts.push(s.ssids && s.ssids.length
      ? `Wi-Fi：${esc(s.ssids.join(", "))}${NET_LABEL[s.net] ? `（${NET_LABEL[s.net]}）` : ""}`
      : s.wifi_error ? "Wi-Fi：読み取れません" : "Wi-Fi：未接続");
  } else {
    parts.push(PRESENCE_LABEL[mode] || "");
  }
  return parts.join("・");
}

// ビューアー：PC が最後に書き出した時点の状態を出す（最終更新は、ページの一番上の右に）
function renderCloudStatus(s) {
  $("#status").innerHTML = s.paused ? `<span class="pill paused"><span class="dot"></span>記録を一時停止中</span>` : "";
  const stale = s.exported && Date.now() / 1000 - s.exported > 15 * 60;
  $("#page-top").innerHTML = s.state === "none" ? ""
    : `<span class="${stale ? "stale" : ""}">最終更新: ${s.exported ? fmtWhen(s.exported) : "—"}</span>`;
  renderNotices();
}

function renderNotices() {
  if (location.hash.startsWith("#/setup")) { $("#notices").innerHTML = ""; return; } // 初期設定の画面では出さない
  const s = state.status, settings = state.settings && state.settings.settings;
  const items = [];
  if (cloud && s) {
    if (s.state === "none") items.push(["warn", "PC から記録が届いていません。"]);
    if (s.offline) items.push(["warn", "インターネットにつながらないため、前に読み込んだ内容を表示しています。"]);
    if (s.pending) items.push(["warn", `修正（${s.pending} 件）は、PC の LabLog が次に動いたときに反映されます。`]);
    for (const r of s.rejected || []) { // PC が取り込めなかった修正（cloud.js の checkPending）
      const what = r.type === "report" ? "日報" : "入退室";
      items.push(["error", `${esc(String(r.date).slice(5).replace("-", "/"))}の${what}の修正を、PC が取り込めませんでした（${esc(r.error)}）。
        <a href="#/day/${esc(r.date)}">もう一度直す</a> <button class="btn btn-sm btn-ghost" type="button" data-dismiss-rejected>閉じる</button>`]);
    }
    if (s.state !== "none" && s.viewer_version && versionLess(s.app_version, s.viewer_version)) {
      items.push(["warn", `PC の LabLog が古いバージョン${s.app_version ? `（${esc(s.app_version)}）` : ""}です。PC で更新してください。`]);
    }
  }
  if (!cloud && s && s.update_failed) {
    items.push(["error", `新しいバージョン（${esc(s.update_failed)}）を起動できなかったため、前のバージョン（${esc(s.version)}）に戻しました。`]);
  }
  const byWifi = presenceMode() === "wifi";
  if (byWifi && s && s.wifi_error) {
    items.push(["error", `${esc(s.wifi_error)}${s.wifi_error.includes("位置情報") ? ' <a href="ms-settings:privacy-location">位置情報の設定を開く</a>' : ""}`]);
  }
  if (byWifi && settings && !settings.wifi.univ.length) {
    items.push(["warn", `研究室の Wi-Fi が未登録です。研究室で <a href="#/settings">設定</a> から登録してください。`]);
  }
  if (settings && !settings.watch_folders.length) {
    items.push(["warn", `監視するフォルダが未登録です。<a href="#/settings">設定</a> から追加してください。`]);
  }
  $("#notices").innerHTML = items.map(([kind, html]) =>
    `<div class="notice ${kind}"><span class="ico">!</span><div>${html}</div></div>`).join("");
  $$("[data-dismiss-rejected]").forEach((b) => b.addEventListener("click", () => {
    cloud.dismissRejected();
    state.status.rejected = [];
    renderNotices();
  }));
}

// バージョン番号を比べる（update.py の parse_version と同じ形：1.2.3、ベータ版は 1.2.3b1 など）。a が分からなければ古いとみなす
function parseVersion(text) {
  const m = /^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$/.exec(String(text || "").trim());
  return m ? [+m[1], +m[2], +m[3], { a: 0, b: 1, rc: 2 }[m[4]] ?? 3, +(m[5] || 0)] : null;
}
function versionLess(a, b) {
  const x = parseVersion(a), y = parseVersion(b);
  if (!y) return false;
  if (!x) return true;
  for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] < y[i];
  return false;
}

async function refreshStatus() {
  try {
    state.status = await api.get("/api/status", { quiet: true });
    renderStatus();
  } catch (e) {
    // ビューアー：PC で連携を解除した → サインアウトする。ほかはサーバー再起動中など（次の更新で戻る）
    if (e.name === "Unlinked") {
      state.unlinked = true;
      cloud.signOut();
      renderSignIn(e.message);
    }
  }
}

const UNLINK_CONFIRM = "連携を解除します。OneDrive 上の LabLog の記録を削除し、スマホ版からサインアウトします。よろしいですか？";
// この PC が OneDrive に書き出しているか（解除すると記録を消すのはこのときだけ。cloud.py の unlink）
async function isLinked() {
  const c = await api.get("/api/cloud", { quiet: true }).catch(() => null);
  return !!(c && c.enabled && c.folder && !c.blocked_by);
}

$("#pause").addEventListener("click", async () => {
  if (cloud) { // ビューアーでは「更新」ボタン：画面のファイルごと読み直す（ホーム画面アプリには再読み込みがないため）
    $("#pause").classList.add("spinning");
    cloud.clearCache();
    location.reload();
    return;
  }
  const paused = !(state.status && state.status.paused);
  if (paused && !confirm("作業内容の記録を一時停止します。よろしいですか？")) return;
  state.status = { ...state.status, ...await api.post("/api/pause", { paused }) };
  renderStatus();
  flash(paused ? "記録を一時停止しました" : "記録を再開しました");
});

// ページの一番上では背景に溶け込ませ、少しでも下がったら角丸で浮かせる。下へ送っているあいだは小さく、
// 上へ戻し始めたら元の大きさに戻す（style.css の at-top・compact）。小さくしても、入りきる幅ならすべて表示する。
// 日別の画面のメイン（研究室・作業時間・日報）が見えているあいだは小さくしない。
// 大きさが変わるときは、帯（.topbar-bg）を古い形から新しい形へ伸び縮みさせ、残る部品は新しい位置まで滑らせる。
// 消える部品は写しを元の場所に残して薄く消し、現れる部品は少し遅れて浮かび上がらせる。すべて同時に動かす
const bar = $(".topbar"), barInner = $(".topbar-inner"), barBg = $(".topbar-bg");
const BAR_MOTION = { duration: 560, easing: "cubic-bezier(.32, .72, 0, 1)" };
const calm = matchMedia("(prefers-reduced-motion: reduce)");
let lastY = 0;

const barParts = () => [...bar.querySelectorAll(".brand, .tabs, .tools, #clock, #status > *")].filter((el) => !el.classList.contains("ghost"));
// 色の切り替え（CSS の transition）は止めずに、前回の伸び縮みだけを止める
const stopMotion = (el) => el.getAnimations().forEach((a) => { if (!(a instanceof CSSTransition)) a.cancel(); });

// 消える部品の写しを、元の場所に重ねて置き、薄くしてから捨てる（本物はすぐに隠れる）
function fadeOutCopy(el, rect, display, opacity) {
  const copy = el.cloneNode(true);
  copy.classList.add("ghost");
  for (const x of [copy, ...copy.querySelectorAll("[id]")]) x.removeAttribute("id");
  copy.setAttribute("aria-hidden", "true");
  copy.inert = true;
  Object.assign(copy.style, { position: "fixed", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`,
                              height: `${rect.height}px`, margin: "0", display, pointerEvents: "none" });
  el.after(copy);
  copy.animate({ opacity: [opacity, 0], transform: ["none", "scale(.96)"] }, { duration: 240, easing: "ease-out", fill: "forwards" })
    .finished.then(() => copy.remove(), () => copy.remove());
}

// 変える前の形と位置を測っておき、変えたあと、元の形に見せかけた状態から新しい形へ動かす
function morphBar(change) {
  if (calm.matches) return change();
  const parts = barParts();
  const before = new Map(parts.map((el) => {
    const cs = getComputedStyle(el);
    return [el, { rect: el.getBoundingClientRect(), display: cs.display, opacity: cs.opacity }];
  }));
  const bg0 = barBg.getBoundingClientRect();
  [barBg, ...parts].forEach(stopMotion);
  change();
  const bg1 = barBg.getBoundingClientRect();
  // 帯は、前の位置と大きさに見せかけた状態から、移動と拡大縮小だけで新しい形へ動かす（1 コマごとの組み直しがないので滑らか）。
  // 拡大縮小で角の丸みがゆがまないよう、丸みを逆向きに補正する
  const sx = bg0.width / bg1.width, sy = bg0.height / bg1.height;
  const radius = parseFloat(getComputedStyle(barBg).borderTopLeftRadius) || 0;
  barBg.animate({ transform: [`translate(${bg0.left - bg1.left}px, ${bg0.top - bg1.top}px) scale(${sx}, ${sy})`, "none"],
                  borderRadius: [`${radius / sx}px / ${radius / sy}px`, `${radius}px / ${radius}px`] }, BAR_MOTION);
  for (const el of parts) {
    const { rect: a, display, opacity } = before.get(el), b = el.getBoundingClientRect();
    if (!b.width) { // 消えた部品
      if (a.width) fadeOutCopy(el, a, display, opacity);
      continue;
    }
    if (!a.width) { // 現れた部品
      el.animate({ opacity: [0, 1], transform: ["scale(.96)", "none"] }, { ...BAR_MOTION, duration: 460, delay: 120, fill: "backwards" });
      continue;
    }
    // 位置を滑らせる。大きさが変わる部品（小さいバーのロゴ）は拡大縮小もする
    const dx = a.left - b.left, dy = a.top - b.top, s = a.height / b.height;
    if (Math.abs(dx) >= 0.5 || Math.abs(dy) >= 0.5 || Math.abs(s - 1) > 0.01) {
      el.animate({ transform: [`translate(${dx}px, ${dy}px) scale(${s})`, "none"] }, BAR_MOTION);
    }
  }
}

let settleUntil = 0, settleTimer = null, scrollQueued = false;
function setCompact(on) {
  if (bar.classList.contains("compact") === on) return;
  settleUntil = performance.now() + BAR_MOTION.duration;
  morphBar(() => bar.classList.toggle("compact", on));
}

// 行ったり来たりして震えないよう、切り替えには「ため」を持たせる。
// 小さくするのは、バーの高さより下まで来てから 8px 下へ進んだとき。大きくするのは、そこから 16px 上へ戻したとき。
// 形を変えている途中は切り替えず、動き終わってから見直す
function updateBar() {
  const max = Math.max(0, document.documentElement.scrollHeight - innerHeight);
  const y = Math.min(Math.max(scrollY, 0), max); // iPhone の引っぱり（ばね）で行き過ぎた分は数えない
  bar.classList.toggle("at-top", y < 1);
  if (y < 1) { setCompact(false); lastY = 0; return; }
  const main = $(".main-area");
  if (main && main.getBoundingClientRect().bottom > barInner.getBoundingClientRect().bottom) {
    setCompact(false);
    lastY = y;
    return;
  }
  const wait = settleUntil - performance.now();
  if (wait > 0) { clearTimeout(settleTimer); settleTimer = setTimeout(updateBar, wait + 20); return; }
  if (bar.classList.contains("compact")) {
    if (y > lastY) lastY = y;                                  // 下へ進んでいる間は、いちばん下を覚えておく
    else if (lastY - y > 16) { setCompact(false); lastY = y; } // 上へ
  } else {
    if (y < lastY) lastY = y;                                  // 上へ進んでいる間は、いちばん上を覚えておく
    else if (y - lastY > 8 && y > bar.offsetHeight) { setCompact(true); lastY = y; } // 下へ
  }
}
function onScroll() {
  if (scrollQueued) return;
  scrollQueued = true;
  requestAnimationFrame(() => { scrollQueued = false; updateBar(); });
}
addEventListener("scroll", onScroll, { passive: true });

// バーは画面に固定してあるので、大きいときのバーの分だけ本文の上に余白をとる（小さくなっても本文は動かさない）。
// 大きいときの高さは、小さいバーを上下の中央に置く枠（--bar-full）にも使う
function fitHeadSpace() {
  if (bar.classList.contains("compact")) return;
  const height = Math.ceil(barInner.getBoundingClientRect().height);
  bar.style.setProperty("--bar-full", `${height}px`);
  document.body.style.setProperty("--head-space", `${Math.ceil(bar.getBoundingClientRect().top) + height}px`);
}
new ResizeObserver(fitHeadSpace).observe(barInner);
addEventListener("resize", fitHeadSpace);

// ------------------------------------------------------------ スクロールのつまみ（PC）
// ページのスクロールバーは普段は隠しておき（style.css）、スクロールしたときだけ右端に細いつまみを薄く出す。
// 出ている間はつまんで動かせる。iPhone・iPad はもともと同じ動きなので何もしない
if (matchMedia("(hover: hover) and (pointer: fine)").matches) {
  const thumb = document.createElement("div");
  thumb.className = "scroll-thumb";
  thumb.setAttribute("aria-hidden", "true");
  document.body.append(thumb);
  let hideTimer = null, drag = null;
  const place = () => {
    const total = document.documentElement.scrollHeight, view = innerHeight;
    if (total <= view + 1) return false;
    const h = Math.max(36, (view * view) / total);
    thumb.style.height = `${h}px`;
    thumb.style.transform = `translateY(${(view - h) * (scrollY / (total - view))}px)`;
    return true;
  };
  const show = () => {
    if (!place()) return;
    thumb.classList.add("show");
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { if (!drag && !thumb.matches(":hover")) thumb.classList.remove("show"); }, 900);
  };
  addEventListener("scroll", show, { passive: true });
  thumb.addEventListener("pointerleave", () => { if (!drag) show(); });
  thumb.addEventListener("pointerdown", (e) => {
    drag = { y: e.clientY, top: scrollY };
    thumb.setPointerCapture(e.pointerId);
    thumb.classList.add("drag");
    e.preventDefault();
  });
  thumb.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const range = document.documentElement.scrollHeight - innerHeight;
    scrollTo(0, drag.top + ((e.clientY - drag.y) * range) / (innerHeight - thumb.offsetHeight));
  });
  const endDrag = () => { drag = null; thumb.classList.remove("drag"); show(); };
  thumb.addEventListener("pointerup", endDrag);
  thumb.addEventListener("pointercancel", endDrag);
}

// ------------------------------------------------------------ 画面の切り替え

function setTab(name) {
  $$("[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
}

// いま開いている画面（day / month / settings / setup）
const currentPage = () => {
  const page = (location.hash || "#/day").split("/")[1];
  return ["month", "settings", "setup"].includes(page) ? page : "day";
};

// 保存していない入力（日報・入退室の編集）があるか。画面を移るときと、閉じる・再読み込みのときに確かめる
const hasUnsaved = () => (state.reportEditing && state.reportDirty) || !!$("#stay-form[data-dirty]");

async function route() {
  clearTimeout(state.refreshTimer);
  state.reportEditing = state.reportDirty = false; // 画面を描き直すので、書きかけの状態も終わる
  state.tips = [];
  tip.hidden = true;
  const [, page, arg] = (location.hash || "#/day").split("/");
  // 初期設定を終えるまでは、ほかの画面を開いても初期設定に戻す（終えるまで何も記録しないため）。
  // 項目そのものが無い（初期設定ができる前のバージョンのサーバー）ときは、設定済みとみなす
  if (!cloud && state.settings && state.settings.settings.setup_done === false && page !== "setup") {
    location.hash = "#/setup";
    return;
  }
  renderNotices(); // 初期設定の画面ではお知らせを出さないので、画面を切り替えるたびに描き直す
  const nav = (state.nav = (state.nav || 0) + 1);
  const navTimer = setTimeout(() => document.body.classList.add("navigating"), 120);
  try {
    if (page === "setup" && !cloud) { setTab(""); setLevel(0); await renderSetup(); }
    else if (page === "month") { setTab("month"); await renderMonth(arg); }
    else if (page === "settings" && !cloud) { setTab("settings"); setLevel(0); await renderSettings(); }
    else { setTab("day"); await renderDay(arg); }
  } catch (e) {
    if (e.name === "SignInRequired") return renderSignIn();
    if (e.name === "KeyRequired") {
      view.innerHTML = `<div class="card"><b>LabLog から開き直してください</b><p class="muted">${esc(e.message)}</p></div>`;
      return;
    }
    view.innerHTML = `<div class="card"><b>読み込めませんでした</b><p class="muted">${esc(e.message)}</p></div>`;
  } finally {
    if (state.nav === nav) { // 続けて切り替えたときは、最後の切り替えが終わるまで薄いまま
      clearTimeout(navTimer);
      document.body.classList.remove("navigating");
    }
  }
}

// ------------------------------------------------------------ 日別

async function renderDay(dateArg, { keepScroll = false, quiet = false } = {}) {
  const today = (state.status && state.status.today) || isoDate(new Date());
  const date = dateArg || today;
  const hash = location.hash;
  const day = await api.get(`/api/day?date=${encodeURIComponent(date)}`, { quiet });
  // 読み込みを待つあいだに別の日・別の画面に移った、またはほかの画面を開いている（古い日の内容で上書きしない）
  if (location.hash !== hash || currentPage() !== "day") return;
  const scrollY = window.scrollY;
  state.tips = [];
  const names = day.category_names;
  const rec = recordFlags();
  const loc = day.work_by_location;
  const locText = ["大学", "自宅", "その他"].filter((k) => loc[k] >= 60)
    .map((k) => `${placeName(k)} ${fmtDur(loc[k])}`).join("・") || "記録なし";

  view.innerHTML = `
    <div class="page-head">
      <a class="btn btn-ghost day-nav" href="#/day/${addDays(day.date, -1)}" aria-label="前の日"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg></a>
      <label class="date-pick" title="日付を選ぶ">
        <h1>${dateHTML(day.date_label)}<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></h1>
        <input type="date" id="pick-date" value="${esc(day.date)}" aria-label="日付を選ぶ">
      </label>
      <a class="btn btn-ghost day-nav" href="#/day/${addDays(day.date, 1)}" aria-label="次の日"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></a>
      ${day.is_today ? "" : `<a class="btn" href="#/day/${today}">今日</a>`}
    </div>

    <!-- よく見る部分（メイン）：入退室状況と PC作業時間（1 つのタイル）、日報 -->
    <div class="main-area">
    <div class="tile day-tile">
      ${levelHTML(day)}
      <div class="day-stay">${stayHTML(day)}</div>
      <div class="day-work"><div class="label">PC作業時間</div>
        <div class="work-sum"><div class="value">${numUnits(fmtDur(day.work_total))}</div>
        <div class="foot">${esc(locText)}</div></div></div>
    </div>
      ${day.reports_off ? "" : `<section class="card" id="report-card"></section>`}
    </div>

    <!-- 詳細：時間の内訳と、記録の中身（見出しは付けず、間隔と区切り線で分ける） -->
    <hr class="section-break">
    <div class="grid detail">
      <section class="card span-2">
        <div class="card-head"><h2>タイムライン</h2></div>
        ${timelineHTML(day, names)}
      </section>
      <section class="card">
        <div class="card-head"><h2>分類別の時間</h2></div>
        ${categoryBarsHTML(day, names)}
      </section>
      ${cloud ? "" : `${shown(rec.apps, day.apps) ? `<section class="card">
        <div class="card-head"><h2>アプリ・ウィンドウ</h2><span class="sub">${day.apps.length} 個のアプリ</span></div>
        <div class="scroll">${appsHTML(day, names)}</div>
      </section>` : ""}
      ${shown(rec.files, day.files_total) ? `<section class="card">
        <div class="card-head"><h2>編集したファイル</h2><span class="sub">${day.files_total} 件</span></div>
        <div class="scroll">${filesHTML(day)}</div>
      </section>` : ""}
      ${shown(rec.git, day.commits) ? `<section class="card">
        <div class="card-head"><h2>Git</h2><span class="sub">コミット ${day.commits.length} 件</span></div>
        ${gitHTML(day)}
      </section>` : ""}
      ${shown(rec.recent, day.recent) ? `<section class="card">
        <div class="card-head"><h2>開いたファイル</h2></div>
        <div class="scroll">${recentHTML(day)}</div>
      </section>` : ""}
      ${shown(rec.screen, day.screen_texts) ? `<section class="card">
        <div class="card-head"><h2>画面から読み取った文字</h2><span class="sub">3日で削除</span></div>
        <div class="scroll">${screenTextsHTML(day)}</div>
      </section>` : ""}
      ${shown(rec.web, day.web_total) ? `<section class="card">
        <div class="card-head"><h2>閲覧したページ</h2><span class="sub">${day.web_total} 件</span></div>
        <div class="scroll">${webHTML(day, names)}</div>
      </section>` : ""}`}
    </div>`;

  setLevel(levelOf(effortSec(day.work_total, day.stay_total)));
  renderReport(day);
  // 日付の見出しの上に透明な日付の入力欄を重ねてある。押すとカレンダーが開く
  const pick = $("#pick-date");
  pick.addEventListener("change", (e) => { if (e.target.value) location.hash = `#/day/${e.target.value}`; });
  pick.addEventListener("click", () => { try { pick.showPicker(); } catch { /* 対応していないブラウザは標準の動き */ } });
  $("#add-stay").addEventListener("click", () => openStayForm(day, null));
  $$("[data-presence]").forEach((b) => b.addEventListener("click", async () => {
    const action = b.dataset.presence;
    b.disabled = true;
    try {
      state.status = { ...state.status, ...await api.post("/api/presence", { action }) };
      renderStatus();
      flash(action === "enter" ? "入室しました" : "退室しました");
      await renderDay(dateArg, { keepScroll: true });
    } catch (err) {
      alert(err.message);
      b.disabled = false;
    }
  }));
  $$("[data-edit-stay]").forEach((b) => b.addEventListener("click", () =>
    openStayForm(day, day.stays.find((s) => s.id === Number(b.dataset.editStay)))));
  if (keepScroll) window.scrollTo(0, scrollY);

  clearTimeout(state.refreshTimer); // 入室ボタンなどで描き直したときも、更新のタイマーは 1 つだけにする
  if (day.is_today) {
    // 1 分ごとに描き直す。書いている途中（日報・入退室の編集など）は、終わるまで 1 分ずつ待つ
    const tick = () => {
      const editing = document.activeElement && ["TEXTAREA", "INPUT"].includes(document.activeElement.tagName);
      if (editing || state.reportEditing || state.reportBusy || $("#stay-form")) {
        state.refreshTimer = setTimeout(tick, 60000);
      } else {
        renderDay(dateArg, { keepScroll: true, quiet: true }).catch(() => {});
      }
    };
    state.refreshTimer = setTimeout(tick, 60000);
  }
}

// 大学での滞在：入室と退室の時刻を線でつなぎ、その区間の長さ（滞在時間）を真ん中に出す。
// 1 日に何回か出入りした日は、大学にいた区間だけを塗り、あいだ（大学の外）は点線にする。
// 入る（扉に向かう矢印）・出る（扉から出る矢印）のアイコン
const ICON_IN = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5"/><path d="M15 12H3"/></svg>`;
const ICON_OUT = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg>`;
const ICON_RELOAD = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/></svg>`;
const ICON_STOP = `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor"/></svg>`;
const ICON_PLAY = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l10.5-6.5z" fill="currentColor"/></svg>`;

// ブロックのタイトルの前に付けるアイコン（線で描く 24×24）。タイトルの文字で選ぶ
const FOLDER = "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z";
const CALENDAR = '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>';
const LAPTOP = '<rect x="4" y="5" width="16" height="11" rx="1.5"/><path d="M2 19.5h20"/>';
const CLOCK = '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>';
const SCAN = '<path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/><path d="M8 9h8M8 12.5h8M8 16h5"/>';
const TITLE_ICONS = {
  "入退室状況": '<path d="M3 21h18M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21"/><path d="M14.5 12.5v.01"/>',
  "PC作業時間": LAPTOP,
  "日報": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3v18M12.5 8h3.5M12.5 12h3.5"/>',
  "タイムライン": '<path d="M4 6h9M4 12h16M4 18h6"/>',
  "分類別の時間": '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5a9 9 0 0 1 5.5 5.5H15z"/>',
  "アプリ・ウィンドウ": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M6.5 6.5v.01M9.5 6.5v.01"/>',
  "編集したファイル": '<path d="M14 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3v5h5M9 17l.6-2.4 4.4-4.4 1.8 1.8-4.4 4.4z"/>',
  "Git": '<circle cx="6" cy="5.5" r="2"/><circle cx="6" cy="18.5" r="2"/><circle cx="18" cy="7.5" r="2"/><path d="M6 7.5v9M18 9.5c0 4.5-7 4-11 7.5"/>',
  "開いたファイル": `<path d="${FOLDER}"/>`,
  "画面から読み取った文字": SCAN,
  "閲覧したページ": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.3 3 14.7 0 18M12 3c-3 3.3-3 14.7 0 18"/>',
  "カレンダー": CALENDAR,
  "研究室にいた日数": `${CALENDAR}<path d="M9 15.5l2 2 4-4"/>`,
  "滞在時間の合計": '<path d="M6 3h12M6 21h12M7 3c0 5 10 5 10 9s-10 4-10 9M17 3c0 5-10 5-10 9s10 4 10 9"/>',
  "1日あたりの滞在": CLOCK,
  "PC作業時間の合計": LAPTOP,
  "入退室の判定": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  "Microsoft アカウント連携": '<path d="M7 18.5h10.5a4 4 0 0 0 .5-7.97A6 6 0 0 0 6.4 9.1 4.7 4.7 0 0 0 7 18.5z"/>',
  "作業内容の記録": '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2.8h6V4M9 10h6M9 14h6M9 18h3"/>',
  "LabLog について": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5v.01"/>',
  "分類のルール": '<path d="M3 12V4.5A1.5 1.5 0 0 1 4.5 3H12l9 9-9 9z"/><path d="M7.5 7.5v.01"/>',
};
// 画面が描き変わるたびに、まだアイコンのないタイルとカードのタイトルへ付ける
function addTitleIcons() {
  for (const h of $$(".card-head h2:not(.has-icon), .tile .label:not(.has-icon)", view)) {
    const icon = TITLE_ICONS[h.textContent.trim()];
    if (!icon) continue;
    h.classList.add("has-icon");
    h.insertAdjacentHTML("afterbegin", `<svg class="title-icon" viewBox="0 0 24 24" aria-hidden="true">${icon}</svg>`);
  }
}
new MutationObserver(addTitleIcons).observe(view, { childList: true, subtree: true });

// タイトルと、判定に使っているもの（今日の PC の画面だけ。接続が変わると renderStatus が書き換える）
const stayHeadHTML = (day) => `<div class="label">入退室状況</div>${!cloud && day.is_today
  ? `<div class="presence-info" id="presence-info">${presenceInfoHTML()}</div>` : ""}`;

// 入退室状況（日別のタイルの左側・スマホでは上側）
function stayHTML(day) {
  const stays = day.stays, t0 = day.range[0];
  if (!stays.length) {
    return `${stayHeadHTML(day)}
      <div class="stay-span empty">
        <span class="cap io">${ICON_IN}IN</span><span class="total muted">未入室</span><span class="cap right io">OUT${ICON_OUT}</span>
        <span class="t">—</span><span class="track"></span><span class="t right">—</span>
      </div>
      ${presenceActionsHTML(day)}
      <div id="stays">${staysHTML(day)}</div>`;
  }
  const first = stays[0].start, last = stays[stays.length - 1];
  const end = Math.max(...stays.map((s) => s.end));
  // 在室中は、入室から「いま」までをバーの左半分に描き、右半分（まだ来ていない時間）は空けておく
  const span = last.state === "in" ? 50 : 100;
  const pos = (t) => ((t - first) / Math.max(1, end - first)) * span;
  const segs = stays.map((s) => {
    const left = pos(s.start), width = Math.max(1, pos(s.end) - left);
    const what = s.state === "in" ? "在室中" : s.state === "tentative" ? "仮退室中" : "研究室";
    return `<span class="seg ${esc(s.state)}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"
      ${tipAttr(`<b>${what}</b><div>${fmtHMDay(s.start, t0)}〜${s.state === "in" ? "いま" : fmtHMDay(s.end, t0)}（${fmtDur(s.duration)}）</div>`)}></span>`;
  }).join("");
  const bigTime = (ts) => esc(fmtHMDay(ts, t0)).replace(/^翌/, `<small class="next">翌</small>`);
  // 在室中はまだ OUT していないので薄く、仮退室は「OUT（仮）」
  const exit = last.state === "in" ? { t: "—", cls: "pending", cap: "OUT" }
    : last.state === "tentative" ? { t: bigTime(last.end), cls: "tentative", cap: "OUT（仮）" }
    : { t: bigTime(last.end), cls: "out", cap: "OUT" };
  return `${stayHeadHTML(day)}
    <div class="stay-span">
      <span class="cap io in" title="入室">${ICON_IN}IN</span>
      <span class="total">${numUnits(fmtDur(day.stay_total))}${stays.length > 1 ? `<small>${stays.length}回の合計</small>` : ""}</span>
      <span class="cap right io ${exit.cls}" title="退室">${esc(exit.cap)}${ICON_OUT}</span>
      <span class="t num">${bigTime(first)}</span>
      <span class="track ${esc(last.state)}">${segs}<i class="dot start"></i><i class="dot end"></i>${last.state === "in" ? `<span class="now-badge">在室中</span>` : ""}</span>
      <span class="t num right ${last.state === "in" ? "pending" : ""}">${exit.t}</span>
    </div>
    ${presenceActionsHTML(day)}
    <div id="stays">${staysHTML(day)}</div>`;
}

// 今日の画面だけに出す、入室・退室の大きなボタン（入室の時刻の下に入室、退室の時刻の下に退室）。
// 在室中は入室を、研究室にいないときは退室を押せなくする（仮退室中は、戻った・帰ったのどちらも押せる）。
// 入退室を記録しない設定とビューアーでは出さない
function presenceActionsHTML(day) {
  const mode = presenceMode();
  if (cloud || !day.is_today || mode === "off") return "";
  const st = state.status && state.status.state;
  const inLab = st === "in" || st === "tentative";
  return `<div class="presence-btns">
      <button class="btn presence-btn" data-presence="enter" ${st === "in" ? "disabled" : ""}>${ICON_IN}入室</button>
      <button class="btn presence-btn" data-presence="leave" ${inLab ? "" : "disabled"}>${ICON_OUT}退室</button>
    </div>`;
}

function timelineHTML(day, names) {
  // 横軸は 1 日（朝 5 時〜翌朝 5 時）の 24 時間で固定する（時間がたっても目盛りの幅は変わらない）
  const [t0, t1] = day.range;
  if (!day.timeline.length && !day.stays.length) return `<div class="empty">この日の記録はまだありません。</div>`;
  const vs = t0, ve = t1;
  const span = ve - vs;
  const L = (t) => `${(((t - vs) / span) * 100).toFixed(3)}%`;
  const W = (a, b) => `${((Math.max(0, b - a) / span) * 100).toFixed(3)}%`;

  const step = 3 * HOUR;
  let ticks = "", grid = "";
  for (let t = vs; t <= ve + 1; t += step) {
    ticks += `<span class="tl-tick" style="left:${L(t)}">${new Date(t * 1000).getHours()}</span>`;
    grid += `<span class="tl-grid" style="left:calc(${L(t)})"></span>`;
  }

  const stays = day.stays.map((s) => {
    const label = s.state === "tentative" ? "仮退室中" : "研究室";
    return `<span class="tl-stay ${s.state === "tentative" ? "tentative" : ""}" style="left:${L(Math.max(s.start, vs))};width:${W(Math.max(s.start, vs), Math.min(s.end, ve))}"
      ${tipAttr(`<b>${label}</b><div>${fmtHMDay(s.start, t0)}〜${fmtHMDay(s.end, t0)}（${fmtDur(s.duration)}）</div>`)}></span>`;
  }).join("");

  const blocks = day.timeline.map((b) => {
    const isIdle = b.cat === "idle";
    const rows = b.top.map((it) => `<div class="t-row"><span>${esc(it.label)}</span><span>${fmtDur(it.sec)}</span></div>`).join("");
    const head = isIdle ? "離席（操作なし）" : esc(b.cat);
    return `<span class="tl-block ${isIdle ? "idle" : ""}" style="left:${L(b.start)};width:${W(b.start, b.end)};${isIdle ? "" : `background:${catColor(b.cat, names)}`}"
      ${tipAttr(`<b>${head}</b><div>${fmtHMDay(b.start, t0)}〜${fmtHMDay(b.end, t0)}（${fmtDur(b.end - b.start)}）</div>${rows}`)}></span>`;
  }).join("");

  const now = day.is_today && day.now > vs && day.now < ve
    ? `<span class="tl-now" style="left:${L(day.now)}" ${tipAttr(`<b>${cloud ? "PC の最終更新" : "現在"}</b> ${fmtHM(day.now)}`)}></span>` : "";
  const used = names.filter((n) => day.timeline.some((b) => b.cat === n));
  const legend = used.map((n) => `<span><i class="sw" style="background:${catColor(n, names)}"></i>${esc(n)}</span>`).join("")
    + `<span><i class="sw" style="background:repeating-linear-gradient(135deg, var(--c-idle) 0 2px, transparent 2px 4px)"></i>離席</span>`
    + `<span><i class="sw" style="background:var(--stay)"></i>研究室にいた時間</span>`;

  return `<div class="tl">
      <div style="position:absolute;inset:0 0 0 64px">${grid}${now}</div>
      <div class="tl-row"><span class="tl-label">研究室</span><div class="tl-track" style="background:transparent">${stays}</div></div>
      <div class="tl-row"><span class="tl-label">PC作業</span><div class="tl-track">${blocks}</div></div>
      <div class="tl-axis">${ticks}</div>
    </div>
    <div class="legend">${legend}</div>`;
}

function categoryBarsHTML(day, names) {
  if (!day.categories.length) return `<div class="empty">まだ PC 作業の記録がありません。</div>`;
  const cats = [...day.categories].sort((a, b) => b.sec - a.sec);
  const max = cats[0].sec;
  return `<div class="bars">${cats.map((c) => `
    <div class="bar-row" ${tipAttr(`<b>${esc(c.name)}</b><div>${fmtDur(c.sec)}（${Math.round((c.sec / day.work_total) * 100)}%）</div>`)}>
      <span class="name"><i class="sw" style="background:${catColor(c.name, names)}"></i>${esc(c.name)}</span>
      <span class="bar-track"><span class="bar" style="display:block;width:${((c.sec / max) * 100).toFixed(2)}%;background:${catColor(c.name, names)}"></span></span>
      <span class="val">${fmtDur(c.sec)}</span>
    </div>`).join("")}</div>`;
}

// 入退室の履歴（入退室状況のタイルの下に並べる）。手で直す・足すときもここから
function staysHTML(day) {
  const title = `<h3 class="stays-title">${day.is_today ? "今日の入退室履歴" : "入退室履歴"}</h3>`;
  const add = `<button class="btn btn-sm btn-ghost stay-add" id="add-stay">＋ 入退室を追加</button>`;
  if (!day.stays.length) return `${title}<div class="stay-list">${add}</div>`;
  return `${title}<div class="stay-list">${day.stays.map((s) => `
    <div class="stay-row">
      <span class="time">${fmtHMDay(s.start, day.range[0])} 〜 ${s.state === "in" ? "在室中" : fmtHMDay(s.end, day.range[0])}</span>
      <span class="ink2">${fmtDur(s.duration)}</span>
      ${s.state === "tentative" ? `<span class="tag warn">仮退室中</span>` : ""}
      ${s.pending ? `<span class="tag warn">PC へ反映待ち</span>` : ""}
      <span class="spacer"></span>
      ${s.id == null ? "" : `<button class="btn btn-sm btn-ghost" data-edit-stay="${s.id}">編集</button>`}
    </div>`).join("")}${add}</div>`;
}

function openStayForm(day, stay) {
  const box = $("#stays");
  const startDefault = stay ? stay.start : day.range[0] + 4 * HOUR;
  const endDefault = stay ? (stay.state === "in" ? null : stay.end) : day.range[0] + 13 * HOUR;
  box.innerHTML = `<form id="stay-form" class="stay-list">
      <label class="field"><span>入室</span><input type="datetime-local" name="start" required value="${toLocalInput(startDefault)}"></label>
      <label class="field"><span>退室${stay && stay.state === "in" ? "（空欄なら在室中のまま）" : ""}</span>
        <input type="datetime-local" name="end" ${stay && stay.state === "in" ? "" : "required"} value="${endDefault ? toLocalInput(endDefault) : ""}"></label>
      <div class="row">
        <button class="btn btn-primary" type="submit">保存</button>
        <button class="btn" type="button" id="stay-cancel">キャンセル</button>
        <span class="spacer"></span>
        ${stay ? `<button class="btn btn-danger" type="button" id="stay-delete">この滞在を削除</button>` : ""}
      </div>
    </form>`;
  const form = $("#stay-form");
  form.addEventListener("input", () => { form.dataset.dirty = "1"; });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/api/stay/save", { id: stay ? stay.id : null, start: form.start.value, end: form.end.value || null, date: day.date });
      flash("滞在を保存しました");
      renderDay(day.date, { keepScroll: true });
    } catch (err) { alert(err.message); }
  });
  $("#stay-cancel").addEventListener("click", () => renderDay(day.date, { keepScroll: true }));
  const del = $("#stay-delete");
  if (del) del.addEventListener("click", async () => {
    if (!confirm("この滞在の記録を削除します。よろしいですか？")) return;
    await api.post("/api/stay/delete", { id: stay.id, date: day.date }).catch((err) => alert(err.message));
    renderDay(day.date, { keepScroll: true });
  });
}

function appsHTML(day, names) {
  if (!day.apps.length) return `<div class="empty">まだ記録がありません。</div>`;
  return day.apps.map((a) => `
    <details class="group">
      <summary><i class="sw" style="background:${catColor(a.category, names)}"></i>
        <span class="name">${esc(a.app || "（不明）")}</span><span class="side muted num">${fmtDur(a.total)}</span></summary>
      <div class="inner list">${a.titles.map((t) => `
        <div class="list-item"><span class="main">${esc(t.title)}</span><span class="side">${fmtDur(t.sec)}</span></div>`).join("")}
        ${a.more ? `<div class="list-item muted">ほか ${a.more} 件</div>` : ""}
      </div>
    </details>`).join("");
}

function filesHTML(day) {
  if (!day.files.length) {
    const hasFolders = state.settings && state.settings.settings.watch_folders.length;
    return `<div class="empty">${hasFolders ? "この日に編集したファイルはありません。" : `監視フォルダが未登録です。<a href="#/settings">設定</a>から追加してください。`}</div>`;
  }
  return `<div class="list">${day.files.map((f) => {
    const tags = [];
    if (f.added) tags.push("新規");
    if (f.saves) tags.push(`保存 ${f.saves}回`);
    if (f.renamed_from) tags.push(`名前変更（${esc(f.renamed_from)} から）`);
    if (f.removed) tags.push("削除");
    return `<div class="list-item" ${tipAttr(`<div>${esc(f.path)}</div>`)}>
      <span class="main">${esc(f.rel)} ${tags.map((t) => `<span class="tag">${t}</span>`).join(" ")}</span>
      <span class="side">${fmtHMDay(f.last, day.range[0])}</span></div>`;
  }).join("")}</div>`;
}

function gitHTML(day) {
  const parts = [];
  if (day.commits.length) {
    parts.push(`<div class="list">${day.commits.map((c) => `
      <div class="list-item"><span class="main"><span class="tag">${esc(c.repo)}</span> ${esc(c.subject)}</span>
      <span class="side">${fmtHMDay(c.ts, day.range[0])}</span></div>`).join("")}</div>`);
  } else {
    parts.push(`<div class="empty">この日のコミットはありません。</div>`);
  }
  const repos = (state.status && state.status.repos) || [];
  if (day.is_today && repos.length) {
    const hours = state.settings ? state.settings.settings.git_notify.hours : 2;
    const now = Date.now() / 1000;
    parts.push(`<h3 class="inner-title">いまの状態</h3><div class="list">${repos.map((r) => {
      const ago = now - (r.last_commit_ts || 0);
      const since = !r.last_commit_ts ? "コミットなし" : ago >= 48 * HOUR ? `${Math.floor(ago / (24 * HOUR))}日前` : `${fmtDur(ago)}前`;
      const from = Math.max(r.dirty_since || now, r.last_commit_ts || 0);
      const late = r.dirty_files && now - from >= hours * HOUR;
      const dirty = r.dirty_files
        ? `<span class="tag ${late ? "warn" : ""}">${late ? "⚠ " : ""}未コミット ${r.dirty_files} 件</span>` : `<span class="tag">変更なし</span>`;
      return `<div class="list-item" ${tipAttr(esc(r.repo))}><span class="main"><b>${esc(r.name)}</b> <span class="muted">${esc(r.branch || "")}</span> ${dirty}</span>
        <span class="side">最後のコミット ${since}</span></div>`;
    }).join("")}</div>`);
  }
  return parts.join("");
}

function recentHTML(day) {
  if (!day.recent.length) return `<div class="empty">記録がありません。</div>`;
  return `<div class="list">${day.recent.map((r) => `
    <div class="list-item" ${tipAttr(esc(r.path))}><span class="main">${esc(r.name)}</span>
    <span class="side">${fmtHMDay(r.ts, day.range[0])}</span></div>`).join("")}</div>`;
}

function screenTextsHTML(day) {
  const texts = day.screen_texts || [];
  if (!texts.length) {
    const on = state.settings && state.settings.settings.screen_text.enabled;
    return `<div class="empty">${on ? "記録がありません。" : `オフになっています（<a href="#/settings">設定</a>）。`}</div>`;
  }
  return texts.map((t) => `
    <details class="group">
      <summary><span class="name">${fmtHMDay(t.ts, day.range[0])}　${esc(t.app)}</span><span class="side muted num">${t.text.length} 文字</span></summary>
      <div class="inner screen-text">${esc(t.text)}</div>
    </details>`).join("");
}

function webHTML(day, names) {
  if (!day.web.length) return `<div class="empty">記録がありません。</div>`;
  return day.web.map((d) => `
    <details class="group">
      <summary><i class="sw" style="background:${catColor(d.category, names)}"></i>
        <span class="name">${esc(d.domain)}</span><span class="side muted num">${d.count} 回</span></summary>
      <div class="inner list">${d.pages.map((p) => `
        <div class="list-item"><span class="main"><a href="${safeHref(p.url)}" target="_blank" rel="noopener noreferrer">${esc(p.title || p.url)}</a></span>
        <span class="side">${fmtHMDay(p.first, day.range[0])}${p.count > 1 ? `・${p.count}回` : ""}</span></div>`).join("")}
        ${d.more ? `<div class="list-item muted">ほか ${d.more} 件</div>` : ""}
      </div>
    </details>`).join("");
}

// ---- 日報（ふだんは整形して表示し、「編集」で書き換える）

// 書いて保存していない日報を、日付ごとにこのブラウザに取っておく（閉じたり、再読み込みしたりしても戻せるように）
const unsavedKey = (date) => `lablog_unsaved_report_${date}`;
function keepUnsaved(date, text) {
  try { if (text === null) localStorage.removeItem(unsavedKey(date)); else localStorage.setItem(unsavedKey(date), text); } catch (e) { /* 保存できないブラウザ */ }
}
function unsavedText(date) {
  try { return localStorage.getItem(unsavedKey(date)); } catch (e) { return null; }
}

function reportMeta(r) {
  if (!r) return "";
  if (r.pending) return "保存しました（PC に反映待ち）";
  const at = (ts) => new Date(ts * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  if (r.edited) return r.saved_ts ? `${at(r.saved_ts)} 保存` : "編集済み";
  const when = r.generated_ts ? `${at(r.generated_ts)} 作成` : "";
  if ((r.source || "").startsWith("ai:")) return `AI（${esc(r.source.slice(3))}）で自動生成・${when}`;
  return `自動生成・${when}`;
}

// 日報で使うマークダウン（見出し・箇条書き・番号付き・太字・コード・リンク）を HTML にする。
// 先に文字をエスケープしてから書式を当てるので、日報の中に HTML があっても文字のまま出る。
function inlineMarkdown(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}

function renderMarkdown(text) {
  const out = [], lists = []; // lists: 開いている箇条書き（入れ子）{ tag, indent }
  let para = [];
  const flushPara = () => {
    if (para.length) out.push(`<p>${para.map(inlineMarkdown).join("<br>")}</p>`);
    para = [];
  };
  const closeLists = (indent = -1) => {
    while (lists.length && lists[lists.length - 1].indent > indent) out.push(`</li></${lists.pop().tag}>`);
  };
  for (const line of String(text).replace(/\r/g, "").split("\n")) {
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/);
    const item = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
    if (!line.trim()) {
      flushPara();
      closeLists();
    } else if (heading) {
      flushPara();
      closeLists();
      const tag = `h${Math.min(heading[1].length, 3) + 2}`; // #→h3、##→h4、###→h5（カードの題名が h2）
      out.push(`<${tag}>${inlineMarkdown(heading[2])}</${tag}>`);
    } else if (item) {
      flushPara();
      const indent = item[1].replace(/\t/g, "    ").length;
      const tag = /\d/.test(item[2]) ? "ol" : "ul";
      closeLists(indent);
      let top = lists[lists.length - 1];
      if (top && top.indent === indent && top.tag !== tag) {
        out.push(`</li></${lists.pop().tag}>`);
        top = lists[lists.length - 1];
      }
      if (top && top.indent === indent) {
        out.push("</li><li>");
      } else {
        out.push(`<${tag}><li>`);
        lists.push({ tag, indent });
      }
      out.push(inlineMarkdown(item[3]));
    } else if (lists.length) {
      out.push(`<br>${inlineMarkdown(line.trim())}`); // 箇条書きの続きの行
    } else {
      para.push(line.trim());
    }
  }
  flushPara();
  closeLists();
  return out.join("");
}

function fitTextarea(ta) {
  ta.style.height = "auto";
  ta.style.height = `${Math.max(180, ta.scrollHeight + 4)}px`;
}
// 画面の幅が変わったとき（スマホの向きを変えたときなど）に日報の欄の高さを合わせ直す
window.addEventListener("resize", () => { const ta = $("#report-text"); if (ta) fitTextarea(ta); });

function renderReport(day, { editing = false } = {}) {
  const card = $("#report-card");
  if (!card) return; // 日報を作っているあいだに、ほかの画面に移った
  const r = day.report;
  const text = (r && r.text) || "";
  state.reportEditing = editing;
  state.reportDirty = false;
  const buttons = editing
    ? `<button class="btn" id="report-cancel">キャンセル</button>
       <button class="btn btn-primary" id="report-save" disabled>保存</button>`
    : `${cloud ? "" : `<button class="btn" id="report-gen">自動生成</button>`}
       <button class="btn ${text ? "" : "btn-primary"}" id="report-edit">${text ? "編集" : "書く"}</button>`;
  const body = editing
    ? `<textarea id="report-text" class="note" placeholder="今日の作業をメモ（「## 見出し」「- 箇条書き」が使えます）">${esc(text)}</textarea>`
    : text ? `<div class="md note">${renderMarkdown(text)}</div>`
    : `<div class="empty">まだありません。</div>`;
  const kept = unsavedText(day.date);
  const hasKept = kept !== null && kept !== text;
  card.innerHTML = `
    <div class="card-head report-head"><h2>日報</h2><span class="spacer"></span>${buttons}</div>
    <div class="report-meta" id="report-meta">${!editing && hasKept ? "保存していない内容があります（編集で戻せます）" : reportMeta(r)}</div>
    ${body}`;

  if (editing) {
    const ta = $("#report-text"), save = $("#report-save");
    const markDirty = () => {
      state.reportDirty = true;
      save.disabled = false;
      $("#report-meta").textContent = "未保存";
    };
    if (hasKept) { // 前に書いて保存していなかった内容を戻す
      ta.value = kept;
      markDirty();
    }
    fitTextarea(ta);
    ta.focus();
    ta.addEventListener("input", () => { markDirty(); keepUnsaved(day.date, ta.value); fitTextarea(ta); });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !save.disabled) { e.preventDefault(); save.click(); }
      if (e.key === "Escape") { e.preventDefault(); $("#report-cancel").click(); }
    });
    $("#report-cancel").addEventListener("click", () => {
      if (state.reportDirty && !confirm("書きかけの内容を捨てて戻ります。よろしいですか？")) return;
      keepUnsaved(day.date, null);
      renderReport(day);
    });
    save.addEventListener("click", async () => {
      save.disabled = true;
      try {
        await api.post("/api/report/save", { date: day.date, text: ta.value });
      } catch (err) {
        alert(err.message);
        save.disabled = false;
        return;
      }
      keepUnsaved(day.date, null);
      day.report = { ...(r || {}), text: ta.value, edited: 1, saved_ts: Date.now() / 1000, pending: !!cloud };
      renderReport(day);
      flash("日報を保存しました");
    });
    return;
  }

  $("#report-edit").addEventListener("click", () => renderReport(day, { editing: true }));
  const gen = $("#report-gen");
  if (gen) gen.addEventListener("click", async () => {
    if (r && r.edited && !confirm("編集した日報を、自動生成した内容で置き換えます。よろしいですか？")) return;
    gen.disabled = true;
    $("#report-edit").disabled = true;
    gen.textContent = "作成中…";
    $("#report-meta").textContent = "作成中…";
    state.reportBusy = true;
    const before = r && r.text;
    try {
      day.report = await api.post("/api/report/generate", { date: day.date, force: true });
    } catch (err) {
      alert(err.message);
    } finally {
      state.reportBusy = false;
      renderReport(day);
    }
    // 自動生成で置き換えた前の内容に戻せるように（戻すと、自分で保存した日報になる）
    if (before && day.report && day.report.text !== before) {
      flash("日報を作り直しました", { label: "元に戻す", run: async () => {
        await api.post("/api/report/save", { date: day.date, text: before }).catch((err) => alert(err.message));
        day.report = { ...day.report, text: before, edited: 1, saved_ts: Date.now() / 1000 };
        renderReport(day);
      } });
    }
  });
}

// ------------------------------------------------------------ 月

async function renderMonth(monthArg) {
  const today = (state.status && state.status.today) || isoDate(new Date());
  const month = monthArg || today.slice(0, 7);
  const hash = location.hash;
  const m = await api.get(`/api/month?month=${encodeURIComponent(month)}`);
  if (location.hash !== hash || currentPage() !== "month") return;
  const [y, mo] = month.split("-").map(Number);
  const maxStay = Math.max(HOUR, ...m.days.map((d) => d.stay));
  const lead = (new Date(y, mo - 1, 1).getDay() + 6) % 7; // 月曜始まり
  const avg = m.stay_days ? m.stay_total / m.stay_days : 0;
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(`<div class="cell blank"></div>`);
  for (const d of m.days) {
    const n = Number(d.date.slice(8));
    const tipHtml = `<b>${mo}月${n}日（${"日月火水木金土"[new Date(y, mo - 1, n).getDay()]}）</b>`
      + (d.stay ? `<div>研究室 ${fmtHM(d.first_in)}〜${fmtHM(d.last_out)}（${fmtDur(d.stay)}）</div>` : `<div>入室なし</div>`)
      + `<div>PC作業 ${fmtDur(d.work)}</div>`
      + Object.entries(d.by_location).filter(([, v]) => v >= 60).map(([k, v]) => `<div class="t-row"><span>${esc(placeName(k))}</span><span>${fmtDur(v)}</span></div>`).join("");
    cells.push(`<a class="cell ${d.date === today ? "today" : ""}" href="#/day/${d.date}" ${tipAttr(tipHtml)}>
      <span class="d"><span>${n}</span>${d.has_report ? `<span class="rep" title="日報あり"></span>` : ""}</span>
      ${d.stay ? `<span class="st">${fmtClock(d.stay)}</span>` : `<span class="st muted">—</span>`}
      ${d.work ? `<span class="wk">PC ${fmtClock(d.work)}</span>` : ""}
      <span class="meter"><i style="width:${((d.stay / maxStay) * 100).toFixed(1)}%"></i></span>
    </a>`);
  }
  view.innerHTML = `
    <div class="page-head">
      <a class="btn btn-ghost day-nav" href="#/month/${addMonths(month, -1)}" aria-label="前の月"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg></a>
      <h1>${y}年${mo}月</h1>
      <a class="btn btn-ghost day-nav" href="#/month/${addMonths(month, 1)}" aria-label="次の月"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></a>
      <span class="spacer"></span>
      ${cloud ? `<button class="btn" id="csv">CSV で書き出す</button>`
        : `<a class="btn" href="/api/export/stays.csv?month=${esc(month)}">CSV で書き出す</a>`}
    </div>
    <div class="tiles">
      <div class="tile"><div class="label">研究室にいた日数</div><div class="value">${numUnits(`${m.stay_days}日`)}</div></div>
      <div class="tile"><div class="label">滞在時間の合計</div><div class="value">${numUnits(fmtDur(m.stay_total))}</div></div>
      <div class="tile"><div class="label">1日あたりの滞在</div><div class="value">${m.stay_days ? numUnits(fmtDur(avg)) : "—"}</div></div>
      <div class="tile"><div class="label">PC作業時間の合計</div><div class="value">${numUnits(fmtDur(m.work_total))}</div></div>
    </div>
    <section class="card">
      <div class="card-head"><h2>カレンダー</h2></div>
      <div class="cal">${["月", "火", "水", "木", "金", "土", "日"].map((w) => `<div class="dow">${w}</div>`).join("")}${cells.join("")}</div>
    </section>`;
  if (cloud) $("#csv").addEventListener("click", () => downloadCSV(m));
  // 月の画面は、作業した日の平均で段階を決める
  const active = m.days.filter((d) => d.work >= 600 || d.stay > 0);
  setLevel(active.length ? levelOf(active.reduce((sum, d) => sum + effortSec(d.work, d.stay), 0) / active.length) : 0);
}

// PC の「CSV で書き出す」と同じ形式（Excel で文字化けしないよう BOM 付き）
function downloadCSV(m) {
  const min = (sec) => Math.round((sec || 0) / 60);
  const hm = (ts) => fmtHM(ts).padStart(5, "0");
  const rows = [["日付", "曜日", "最初の入室", "最後の退室", "滞在時間", "滞在(分)", "PC作業時間", "PC作業(分)", "うち研究室(分)", "うち自宅(分)"]];
  for (const d of m.days) {
    rows.push([d.date, "日月火水木金土"[new Date(d.date + "T12:00:00").getDay()],
      d.first_in ? hm(d.first_in) : "", d.last_out ? hm(d.last_out) : "",
      d.stay ? fmtDur(d.stay) : "", min(d.stay), d.work ? fmtDur(d.work) : "", min(d.work),
      min(d.by_location["大学"]), min(d.by_location["自宅"])]);
  }
  const blob = new Blob(["\ufeff" + rows.map((r) => r.join(",")).join("\r\n") + "\r\n"], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `lablog_${m.month}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10000);
}

// ------------------------------------------------------------ 設定

// 作業内容の記録のオンオフ（設定を読めていないときは全部オンとみなす）
function recordFlags() {
  const s = state.settings && state.settings.settings;
  if (!s) return { apps: true, files: true, git: true, recent: true, screen: true, web: true };
  const r = s.record || {};
  return { apps: r.apps !== "off", files: r.files !== false, git: r.git !== false, recent: r.recent_files !== false,
           screen: !!s.screen_text.enabled, web: !!(s.browsers.chrome || s.browsers.edge) };
}
// オフにした材料の欄は隠す。ただし、その日の記録があれば（オフにする前の日など）出す
const shown = (on, data) => on || (Array.isArray(data) ? data.length > 0 : !!data);

async function loadSettings() {
  state.settings = await api.get("/api/settings");
  return state.settings;
}

async function saveSettings(patch, msg = "保存しました") {
  try {
    await api.post("/api/settings", patch);
    await loadSettings();
    renderNotices();
    flash(msg);
    return true;
  } catch (err) {
    alert(err.message);
    return false;
  }
}

const lines = (text) => text.split(/[\n,、]/).map((s) => s.trim()).filter(Boolean);

// Wi-Fi やフォルダを登録して描き直すとき（keep）は、入力しかけの内容をそのまま残す
const DRAFT_FORMS = ["record-form", "judge-form"];
function settingsDraft() {
  return DRAFT_FORMS.map((id) => $(`#${id}`)).filter(Boolean)
    .map((f) => [f.id, [...f.elements].filter((e) => e.name).map((e) => ({ name: e.name, value: e.value, checked: e.checked }))]);
}
function restoreDraft(draft) {
  for (const [id, items] of draft) {
    const f = $(`#${id}`);
    for (const it of items) {
      for (const e of [...f.elements].filter((x) => x.name === it.name)) {
        if (e.type === "radio") { if (e.value === it.value) e.checked = it.checked; }
        else if (e.type === "checkbox") e.checked = it.checked;
        else e.value = it.value;
      }
    }
  }
}

// 選んだ判定方法（data-modes）や、オンにした項目（data-when：同じ form のスイッチ、または選択欄が「記録しない」以外）に
// 関係する設定だけを出す
function syncSettingsSections() {
  const judge = $("#judge-form");
  $$("[data-modes]").forEach((el) => { el.hidden = !el.dataset.modes.split(" ").includes(judge.mode.value); });
  $$("[data-when]").forEach((el) => {
    const f = el.closest("form");
    const on = (name) => { const e = f.elements[name]; return e.type === "checkbox" ? e.checked : e.value !== "off"; };
    el.hidden = !el.dataset.when.split(" ").some(on);
  });
}

// 設定は変えたらすぐ保存する（スイッチ・選択は押したとき、数字や文字は入力欄から離れたとき）。
// build：form の入力から保存する設定を作る（null なら保存しない）。after：保存できたあとにすること
function autoSave(form, build, after) {
  form.addEventListener("submit", (e) => e.preventDefault()); // Enter キーでは送らない
  form.addEventListener("change", async (e) => {
    const input = e.target;
    if (!input.name || "noSave" in input.dataset) return;
    if (!input.checkValidity()) { input.reportValidity(); return; }
    const patch = await build(form, input);
    if (patch && await saveSettings(patch) && after) after(input);
  });
}

// 日報の AI：入っているモデルから選ぶ（選んでいるモデルが無ければ「未ダウンロード」として残す）
async function loadOllamaState() {
  const o = await api.get("/api/ollama", { quiet: true }).catch(() => ({ running: false, models: [] }));
  const f = $("#ollama-form"), el = $("#ollama-state");
  if (!f || !el) return;
  const cur = state.settings.settings.ollama.model;
  const have = o.models || [];
  const label = (name) => {
    const m = (o.catalog || []).find((x) => x.name === name);
    return `${name}${m ? `（${m.label}）` : ""}${have.includes(name) ? "" : "（未ダウンロード）"}`;
  };
  f.model.innerHTML = [...new Set([...have, cur])]
    .map((n) => `<option value="${esc(n)}" ${n === cur ? "selected" : ""}>${esc(label(n))}</option>`).join("");
  el.innerHTML = !o.installed ? `<span class="tag warn">Ollama が入っていません</span> 「初期設定をやり直す」から入れられます。`
    : !o.running ? `<span class="tag warn">Ollama が起動していません</span>`
    : !o.model_ready ? `<span class="tag warn">モデルがありません</span> 「初期設定をやり直す」からダウンロードできます。`
    : `<span class="tag">準備完了</span>`;
}

async function renderSettings({ keep = false } = {}) {
  const draft = keep ? settingsDraft() : [];
  const { settings: s, data_dir: dataDir, repos, ssids } = await loadSettings();
  const current = ssids && ssids[0];
  const where = !current ? "未接続" : s.wifi.univ.includes(current) ? "研究室として登録済み"
    : s.wifi.home.includes(current) ? "自宅として登録済み"
    : s.wifi.ignore.includes(current) ? "判定に使わない Wi-Fi として登録済み" : "未登録";
  const ssidList = (kind) => s.wifi[kind].length ? s.wifi[kind].map((x) => `
      <div class="set-item"><span class="main">${esc(x)}</span>
      <button class="icon-btn" type="button" data-wifi-remove="${esc(kind)}" data-ssid="${esc(x)}" aria-label="${esc(x)} を削除">✕</button></div>`).join("")
    : `<div class="empty">まだありません</div>`;

  const catNames = s.categories.map((c) => c.name).concat(["その他"]);

  const rec = s.record;
  const modeOption = ([value, label, desc]) => `<label class="check"><input type="radio" name="mode" value="${value}" ${s.presence.mode === value ? "checked" : ""}>
      <span><b>${label}</b><br><span class="help">${esc(desc)}</span></span></label>`;
  const placeOption = (value, label) => `<option value="${value}" ${s.presence.place === value ? "selected" : ""}>${label}</option>`;
  const appsOption = (value, label) => `<option value="${value}" ${rec.apps === value ? "selected" : ""}>${label}</option>`;

  const toggle = (name, label, on) => `<label class="switch-row"><span>${label}</span>
      <input type="checkbox" class="switch" name="${name}" ${on ? "checked" : ""}></label>`;

  view.innerHTML = `
    <div class="page-head"><h1>設定</h1><span class="spacer"></span><a class="btn" href="#/setup">初期設定をやり直す</a></div>
    <div class="grid settings">
      <section class="card">
        <div class="card-head"><h2>日報</h2></div>
        <form id="ollama-form">
          ${toggle("auto", "日報を自動で作成する", s.ollama.auto)}
          ${toggle("enabled", "日報の作成に AI を使用する", s.ollama.enabled)}
          <div data-when="enabled">
            <label class="field"><span>AI のモデル</span>
              <select name="model"><option value="${esc(s.ollama.model)}">${esc(s.ollama.model)}</option></select></label>
            <p class="help" id="ollama-state">確認中…</p>
          </div>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>入退室の判定</h2></div>
        <form id="judge-form">
          ${presenceMethods.map(modeOption).join("")}
          <div class="fields">
            <label class="field" data-modes="activity"><span>仮退室までの時間（分）</span>
              <input type="number" name="away" min="1" max="120" step="1" required value="${s.presence.away_min}"></label>
            <label class="field" data-modes="wifi activity manual"><span>退室を確定するまでの時間（時間）</span>
              <input type="number" name="leave" min="1" max="24" step="0.5" required value="${s.leave_after_hours}"></label>
            <label class="field"><span>離席とみなす時間（分）</span>
              <input type="number" name="idle" min="1" max="60" step="1" required value="${s.idle_threshold_min}"></label>
            <label class="field" data-modes="off"><span>作業時間の場所</span>
              <select name="place">${placeOption("home", "自宅")}${placeOption("other", "その他")}</select></label>
          </div>
        </form>
        <div class="judge-wifi" data-modes="wifi">
          <h3 class="inner-title">Wi-Fi の登録</h3>
          <p class="help">研究室専用と大学共通（ScienceTokyo など）の両方を登録します。</p>
          <p>接続中：<b>${esc(current || "なし")}</b>（${where}）</p>
          <div class="row">
            <button class="btn btn-primary" type="button" data-wifi-register="univ" ${current ? "" : "disabled"}>研究室として登録</button>
            <button class="btn" type="button" data-wifi-register="home" ${current ? "" : "disabled"}>自宅として登録</button>
            <button class="btn" type="button" data-wifi-register="ignore" ${current ? "" : "disabled"}>判定に使わない</button>
          </div>
          <div class="list-title">研究室</div><div class="set-list">${ssidList("univ")}</div>
          <div class="list-title">自宅</div><div class="set-list">${ssidList("home")}</div>
          <div class="list-title">判定に使わない</div><div class="set-list">${ssidList("ignore")}</div>
        </div>
      </section>

      <section class="card span-2">
        <div class="card-head"><h2>作業内容の記録</h2></div>
        <p class="help">オフにしても、記録済みの分は残ります。オフにしていた間の分は、オンに戻しても取り込みません。</p>
        <form id="record-form">
          <div class="record-cols">
            <div>
              <label class="field"><span>使ったアプリ</span>
                <select name="apps">${appsOption("title", "アプリ名とウィンドウ名")}${appsOption("app", "アプリ名だけ")}${appsOption("off", "記録しない")}</select></label>
              ${toggle("files", "ファイルの編集履歴", rec.files)}
              ${toggle("git", "Git のコミット", rec.git)}
              ${toggle("chrome", "Chrome の閲覧履歴", s.browsers.chrome)}
              ${toggle("edge", "Edge の閲覧履歴", s.browsers.edge)}
              <p class="help" id="browser-hint" ${rec.apps === "title" || !(s.browsers.chrome || s.browsers.edge) ? "hidden" : ""}>サイトごとに分類するには、「使ったアプリ」を「アプリ名とウィンドウ名」にしてください。</p>
              ${toggle("recent", "Windows の「最近使ったファイル」", rec.recent_files)}
              ${toggle("claude", "Claude Code の会話の題名", s.claude_code.enabled)}
              ${toggle("ocr", "画面の文字", s.screen_text.enabled)}
            </div>
            <div class="record-subs">
              <div class="record-sub" data-when="files git">
                <h3 class="sub-title">監視するフォルダ</h3>
                <p class="help">フォルダ内のファイルの編集と、Git のコミットを記録します。</p>
                <div class="set-list">${s.watch_folders.length ? s.watch_folders.map((f) => `
                  <div class="set-item"><span class="main">${esc(f)}</span>
                  <button class="icon-btn" type="button" data-folder-remove="${esc(f)}" aria-label="削除">✕</button></div>`).join("") : `<div class="empty">まだありません</div>`}</div>
                <div class="row"><button class="btn" type="button" id="folder-pick">フォルダを選んで追加…</button></div>
                <div class="row" style="margin-top:8px">
                  <input type="text" name="folder_path" data-no-save placeholder="または、フォルダのパスを貼り付け" style="flex:1;min-width:200px">
                  <button class="btn" type="button" id="folder-add">追加</button>
                </div>
                <div class="list-title">見つかった Git リポジトリ</div>
                <div class="set-list">${repos.length ? repos.map((r) => `<div class="set-item"><span class="main">${esc(r)}</span></div>`).join("")
                  : `<div class="empty">まだありません</div>`}</div>
              </div>
              <div class="record-sub" data-when="git">
                <h3 class="sub-title">コミットの通知</h3>
                ${toggle("notify", "コミットしていない変更を通知する", s.git_notify.enabled)}
                <label class="field"><span>通知までの時間（時間）</span>
                  <input type="number" name="notify_hours" min="0.5" max="24" step="0.5" required value="${s.git_notify.hours}"></label>
              </div>
              <div class="record-sub" data-when="ocr">
                <h3 class="sub-title">画面の文字</h3>
                <label class="field"><span>読み取るアプリ（1 行に 1 つ）</span>
                  <textarea name="ocr_apps" rows="5">${esc(s.screen_text.apps.join("\n"))}</textarea></label>
                <div class="fields">
                  <label class="field"><span>読み取る間隔（分）</span><input type="number" name="interval" min="1" max="60" step="1" required value="${s.screen_text.interval_min}"></label>
                  <label class="field"><span>残す日数</span><input type="number" name="keep" min="1" max="30" step="1" required value="${s.screen_text.keep_days}"></label>
                </div>
                <div class="row">
                  <span class="help">保存中：${state.settings.screen_text_count} 件</span>
                  <button class="btn btn-sm" type="button" id="ocr-clear" ${state.settings.screen_text_count ? "" : "disabled"}>すべて消す</button>
                </div>
              </div>
              <div class="record-sub" data-when="apps">
                <h3 class="sub-title">記録しないアプリ</h3>
                <label class="field"><span>時間だけを記録するアプリ（1 行に 1 つ。例：line.exe）</span>
                  <textarea name="excluded" rows="4">${esc(s.excluded_apps.join("\n"))}</textarea></label>
                ${(state.settings.recent_apps || []).length ? `<label class="field"><span>最近使ったアプリから足す</span>
                  <select id="excluded-pick"><option value="">選ぶ…</option>${state.settings.recent_apps.map((x) =>
                    `<option value="${esc(x.exe)}">${esc(x.label)}（${esc(x.exe)}）</option>`).join("")}</select></label>` : ""}
              </div>
            </div>
          </div>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>Microsoft アカウント連携</h2></div>
        <p class="help">連携すると、入退室履歴をスマホからも確認できます。</p>
        <form id="cloud-form">
          ${toggle("enabled", "連携する", s.cloud.enabled)}
          <div data-when="enabled">${toggle("reports", "日報もスマホに送る", s.cloud.reports !== false)}</div>
          <p class="help" id="cloud-state">${s.cloud.enabled ? "確認中…" : ""}</p>
          <div class="row" data-when="enabled"><span class="spacer"></span><button class="btn" type="button" id="cloud-export">今すぐ同期</button></div>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>LabLog について</h2></div>
        <p id="update-current">バージョン：確認中…</p>
        <form id="update-form">
          <label class="check"><input type="radio" name="channel" value="stable" ${s.update.channel === "stable" ? "checked" : ""}>
            <span><b>安定版</b><br><span class="help">正式なバージョンだけを受け取ります。</span></span></label>
          <label class="check"><input type="radio" name="channel" value="beta" ${s.update.channel === "beta" ? "checked" : ""}>
            <span><b>ベータ版</b><br><span class="help">公開前のバージョンも受け取ります。</span></span></label>
          <div class="row"><span class="spacer"></span><button class="btn btn-primary" type="button" id="update-check">更新を確認</button></div>
        </form>
        <div id="update-result"></div>
        <div class="list-title">データの保存場所</div>
        <p><code>${esc(dataDir)}</code></p>
        <div class="row">
          <a class="btn" href="/api/backup.zip" download>バックアップを保存</a>
          ${restoreButtonHTML()}
        </div>
      </section>

      <details class="advanced span-2" id="advanced" ${state.advancedOpen ? "open" : ""}>
        <summary>詳細な設定</summary>
      <section class="card">
        <div class="card-head"><h2>分類のルール</h2>
          <span class="sub">上から順に色が決まります。ドメインの先頭に「=」を付けると完全一致</span></div>
        <form id="cat-form">
          <div class="cat-edit cat-head"><span>分類名</span><span>アプリ（実行ファイル名）</span><span>サイト（ドメイン）</span><span>ウィンドウ名に含む語</span><span></span></div>
          <div id="cat-rows">${s.categories.map((c, i) => catRowHTML(c, i, catNames)).join("")}</div>
          <div class="row" style="margin-top:10px">
            <button class="btn" type="button" id="cat-add">＋ 分類を追加</button>
            <span class="spacer"></span>
            <button class="btn btn-ghost" type="button" id="cat-reset">初期値に戻す</button>
          </div>
        </form>
      </section>
      </details>
    </div>`;
  restoreDraft(draft);
  syncSettingsSections();
  $("#advanced").addEventListener("toggle", (e) => { state.advancedOpen = e.target.open; });
  $("#record-form").addEventListener("change", () => {
    const f = $("#record-form");
    $("#browser-hint").hidden = f.apps.value === "title" || !(f.chrome.checked || f.edge.checked);
  });
  // 最近使ったアプリから選んだものを、「時間だけを記録するアプリ」に足す（この変更で form の自動保存も動く）
  const pick = $("#excluded-pick");
  if (pick) pick.addEventListener("change", () => {
    const ta = $("#record-form").excluded;
    const have = lines(ta.value).map((x) => x.toLowerCase());
    if (pick.value && !have.includes(pick.value)) {
      ta.value = [...lines(ta.value), pick.value].join("\n");
      ta.dispatchEvent(new Event("change", { bubbles: true })); // 選ぶ欄は保存の対象ではないので、テキスト欄の変更として保存する
    }
    pick.value = "";
  });
  ["#judge-form", "#record-form", "#ollama-form", "#cloud-form"].forEach((id) => $(id).addEventListener("change", syncSettingsSections));
  loadOllamaState();
  loadCloudState();
  loadUpdateState();

  // ---- すぐ保存する
  const cur = () => state.settings.settings;
  autoSave($("#ollama-form"), (f) => ({ ollama: { ...cur().ollama, enabled: f.enabled.checked, model: f.model.value,
                                                   auto: f.auto.checked } }), loadOllamaState);
  autoSave($("#judge-form"), (f) => ({
    presence: { ...cur().presence, mode: f.mode.value, away_min: Number(f.away.value), place: f.place.value },
    idle_threshold_min: Number(f.idle.value), leave_after_hours: Number(f.leave.value),
  }), refreshStatus);
  autoSave($("#record-form"), (f) => ({
    record: { apps: f.apps.value, files: f.files.checked, git: f.git.checked, recent_files: f.recent.checked },
    browsers: { chrome: f.chrome.checked, edge: f.edge.checked },
    claude_code: { enabled: f.claude.checked },
    screen_text: { enabled: f.ocr.checked, apps: lines(f.ocr_apps.value),
                   interval_min: Number(f.interval.value), keep_days: Number(f.keep.value) },
    excluded_apps: lines(f.excluded.value),
    git_notify: { enabled: f.notify.checked, hours: Number(f.notify_hours.value) },
  }));
  autoSave($("#cloud-form"), async (f, input) => {
    if (input.name === "reports") return { cloud: { ...cur().cloud, reports: input.checked } };
    // この PC が書き出しているときは、解除すると OneDrive の記録を消すので、確かめる（やめたらスイッチを戻す）
    if (!input.checked && await isLinked() && !confirm(UNLINK_CONFIRM)) { input.checked = true; return null; }
    return { cloud: { ...cur().cloud, enabled: input.checked } };
  }, () => loadCloudState());
  autoSave($("#update-form"), (f) => ({ update: { ...cur().update, channel: f.channel.value } }));
  const categories = () => $$("#cat-rows .cat-edit").map((row) => ({
    name: $("[name=name]", row).value.trim(),
    apps: lines($("[name=apps]", row).value),
    domains: lines($("[name=domains]", row).value),
    titles: lines($("[name=titles]", row).value),
  })).filter((c) => c.name);
  autoSave($("#cat-form"), () => ({ categories: categories() }));

  // ---- そのほかの操作
  $("#update-check").addEventListener("click", () => checkUpdate($("#update-form").channel.value));
  bindRestore();
  $("#cloud-export").addEventListener("click", async () => {
    const before = await loadCloudState();
    try {
      await api.post("/api/cloud/export");
    } catch (err) {
      alert(err.message);
      return;
    }
    flash("同期を始めました");
    for (let i = 0; i < 15; i++) { // 書き終わる（最終更新が新しくなる）まで、2 秒ごとに確かめる
      await new Promise((r) => setTimeout(r, 2000));
      const c = await loadCloudState();
      if (!c || c.last_error || c.blocked_by) return;
      if (c.last_export && c.last_export !== (before && before.last_export)) { flash("同期しました"); return; }
    }
  });
  $$("[data-wifi-register]").forEach((b) => b.addEventListener("click", async () => {
    await api.post("/api/wifi/register", { kind: b.dataset.wifiRegister }).catch((e) => alert(e.message));
    flash("登録しました");
    renderSettings({ keep: true });
    refreshStatus();
  }));
  $$("[data-wifi-remove]").forEach((b) => b.addEventListener("click", async () => {
    await api.post("/api/wifi/remove", { kind: b.dataset.wifiRemove, ssid: b.dataset.ssid });
    renderSettings({ keep: true });
  }));
  $$("[data-folder-remove]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`監視をやめます：\n${b.dataset.folderRemove}`)) return;
    await api.post("/api/folders/remove", { path: b.dataset.folderRemove });
    renderSettings({ keep: true });
  }));
  const addFolder = async (path) => {
    await api.post("/api/folders/add", { path });
    flash("フォルダを追加しました");
    $("#record-form").folder_path.value = "";
    await renderSettings({ keep: true });
    renderNotices();
    // 見つかった Git リポジトリは少し後に分かるので、出し直す
    setTimeout(() => { if (location.hash === "#/settings") renderSettings({ keep: true }); }, 4000);
  };
  $("#folder-pick").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "フォルダ選択画面を開いています…";
    try {
      const { path } = await api.post("/api/folders/pick");
      if (path) await addFolder(path);
    } catch (err) { alert(err.message); }
    finally { btn.disabled = false; btn.textContent = "フォルダを選んで追加…"; }
  });
  const pasted = $("#record-form").folder_path;
  const addPasted = () => addFolder(pasted.value).catch((err) => alert(err.message));
  $("#folder-add").addEventListener("click", addPasted);
  pasted.addEventListener("keydown", (e) => { if (e.key === "Enter") addPasted(); });
  $("#ocr-clear").addEventListener("click", async () => {
    if (!confirm("画面から読み取った文字をすべて消します。よろしいですか？")) return;
    await api.post("/api/screen_texts/clear");
    flash("読み取った文字を消しました");
    renderSettings({ keep: true });
  });
  $("#cat-add").addEventListener("click", () => {
    const rows = $("#cat-rows");
    rows.insertAdjacentHTML("beforeend", catRowHTML({ name: "", apps: [], domains: [], titles: [] }, rows.children.length, catNames));
    $("#cat-rows .cat-edit:last-child [name=name]").focus();
  });
  $("#cat-reset").addEventListener("click", async () => {
    if (!confirm("分類のルールを初期値に戻します。よろしいですか？")) return;
    await api.post("/api/categories/reset");
    await loadSettings();
    flash("分類を初期値に戻しました");
    renderSettings();
  });
  $("#cat-rows").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-cat-remove]");
    if (!b) return;
    const row = b.closest(".cat-edit");
    const index = $$("#cat-rows .cat-edit").indexOf(row);
    const name = $("[name=name]", row).value.trim();
    row.remove();
    if (!name) return; // 名前をまだ入れていない行は、保存されていない
    try {
      await api.post("/api/settings", { categories: categories() });
      await loadSettings();
    } catch (err) {
      alert(err.message);
      return;
    }
    const gone = { name, apps: lines($("[name=apps]", row).value), domains: lines($("[name=domains]", row).value),
                   titles: lines($("[name=titles]", row).value) };
    flash(`「${name}」を消しました`, { label: "元に戻す", run: async () => {
      const list = state.settings.settings.categories;
      list.splice(Math.min(index, list.length), 0, gone);
      if (await saveSettings({ categories: list }, "元に戻しました") && location.hash === "#/settings") renderSettings({ keep: true });
    } });
  });
}

// ------------------------------------------------------------ バックアップから戻す（設定の画面・初期設定の画面）

const restoreButtonHTML = () => `<button class="btn" type="button" id="restore-pick">バックアップから戻す…</button>
  <input type="file" id="restore-file" accept=".zip,application/zip" hidden>`;

function bindRestore() {
  const input = $("#restore-file");
  $("#restore-pick").addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) restoreBackup(input.files[0]); input.value = ""; });
}

// ZIP を送ると LabLog が起動し直して記録を差し替えるので、起動し直したら開き直す
async function restoreBackup(file) {
  if (!confirm("いまの記録を、バックアップの内容に置き換えます（いまの記録は before-restore フォルダに残します）。よろしいですか？")) return;
  try {
    state.serverStarted = (await api.get("/api/update", { quiet: true })).started;
    await track(fetch("/api/backup/restore", {
      method: "POST", headers: { "Content-Type": "application/zip", "X-LabLog": "1" }, body: file,
    }).then(toJson));
  } catch (err) {
    alert(err.message);
    return;
  }
  flash("記録を戻しています（LabLog を起動し直します）…");
  document.body.classList.add("loading", "navigating");
  waitForRestart();
}

// ------------------------------------------------------------ 更新（設定の画面の「LabLog の更新」）

async function loadUpdateState() {
  const u = await api.get("/api/update", { quiet: true }).catch(() => null);
  const el = $("#update-current");
  if (!el || !u) return;
  state.serverStarted = u.started;
  el.innerHTML = `バージョン：<b>${esc(u.current)}</b>${u.installed ? ""
    : ` <span class="tag">開発用</span>`}`;
  if (u.job && ["running", "restarting", "error"].includes(u.job.state)) renderUpdateJob(u.job);
}

async function checkUpdate(channel) {
  const out = $("#update-result");
  out.innerHTML = `<p class="help">確認しています…</p>`;
  let r;
  try {
    r = await api.post("/api/update/check", { channel });
  } catch (err) {
    out.innerHTML = `<p class="help"><span class="tag warn">確認できませんでした</span> ${esc(err.message)}</p>`;
    return;
  }
  const rel = r.latest;
  if (!rel) { out.innerHTML = `<p class="help">公開されているバージョンがありません。</p>`; return; }
  if (!r.newer) { out.innerHTML = `<p class="help"><span class="tag">最新です</span></p>`; return; }
  const size = rel.size ? `約 ${(rel.size / 1024 / 1024).toFixed(1)} MB` : "";
  const date = rel.published ? `、${new Date(rel.published).toLocaleDateString("ja-JP")} 公開` : "";
  out.innerHTML = `
    <p><b>新しいバージョン ${esc(rel.version)}</b> があります（${rel.prerelease ? "ベータ版" : "安定版"}${date}）。</p>
    ${rel.notes ? `<div class="md update-notes">${renderMarkdown(rel.notes)}</div>` : ""}
    ${r.installed ? `
      <div class="row"><button class="btn btn-primary" id="update-install">更新する（${size}）</button></div>
      <p class="help">記録と設定は引き継がれます。</p>`
    : `<p class="help">開発用のため、ここからは更新しません。</p>`}`;
  const btn = $("#update-install");
  if (btn) btn.addEventListener("click", async () => {
    if (!confirm(`LabLog を ${rel.version} に更新します。よろしいですか？`)) return;
    btn.disabled = true;
    try {
      renderUpdateJob(await api.post("/api/update/install", { version: rel.version }));
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
    }
  });
}

function renderUpdateJob(job) {
  const out = $("#update-result");
  if (!out) return;
  if (job.state === "error") {
    out.innerHTML = `<p class="help"><span class="tag warn">更新できませんでした</span> ${esc(job.message)}</p>`;
    return;
  }
  const pct = job.progress == null ? null : Math.round(job.progress * 100);
  out.innerHTML = `<p class="help">${esc(job.message || "")}${pct == null ? "" : `（${pct}%）`}</p>
    <div class="progress ${pct == null ? "busy" : ""}"><i style="width:${pct ?? 0}%"></i></div>`;
  if (job.state === "restarting") { waitForRestart(); return; }
  if (job.state === "running") {
    setTimeout(async () => { const u = await api.get("/api/update", { quiet: true }).catch(() => null); if (u) renderUpdateJob(u.job); }, 1000);
  }
}

// 起動し直したら（サーバーが起動した時刻が変わったら）開き直す。止まっている時間は短く、確かめる合間に
// 終わることもあるので、止まったかどうかでは判断しない（新しいバージョンを起動できず前のバージョンに戻ったときも、上部のお知らせで分かる）
async function waitForRestart() {
  for (let i = 0; i < 180; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    const u = await api.get("/api/update", { quiet: true }).catch(() => null);
    if (u && u.started !== state.serverStarted) { location.reload(); return; }
  }
  const out = $("#update-result");
  if (out) out.innerHTML = `<p class="help"><span class="tag warn">再起動に時間がかかっています</span> スタートメニューから LabLog を開き直してください。</p>`;
}

async function loadCloudState() {
  const c = await api.get("/api/cloud", { quiet: true }).catch(() => null);
  const el = $("#cloud-state");
  if (!el || !c) return;
  const viewer = c.viewer_url
    ? `<a href="${safeHref(c.viewer_url)}" target="_blank" rel="noopener noreferrer">${esc(c.viewer_url)}</a>` : "";
  const who = c.account ? `<div>この PC の OneDrive：<b>${esc(c.account)}</b></div>`
    : `<div><span class="tag warn">個人用の OneDrive がありません</span> この PC の OneDrive に、個人の Microsoft アカウントでサインインしてください。</div>`;
  if (!c.enabled) {
    el.innerHTML = "";
  } else if (c.blocked_by) {
    // スマホに出せるのは 1 台の PC の記録だけ（cloud.py の _other_writer）
    const other = esc(c.blocked_by.name);
    el.innerHTML = `<span class="tag warn">別の PC と連携中</span> 「${other}」の記録を表示しています（最終更新 ${fmtWhen(c.blocked_by.exported)}）。
      <div class="row" style="margin-top:8px"><button class="btn btn-sm" type="button" id="cloud-takeover">この PC に切り替える</button></div>`;
    $("#cloud-takeover").addEventListener("click", async () => {
      if (!confirm(`スマホに表示する記録を、「${c.blocked_by.name}」からこの PC に切り替えます。よろしいですか？`)) return;
      await api.post("/api/cloud/takeover").catch((err) => alert(err.message));
      flash("この PC に切り替えました");
      setTimeout(loadCloudState, 3000);
    });
  } else if (c.last_error) {
    el.innerHTML = `<span class="tag warn">書き出せませんでした</span> ${esc(c.last_error)}`;
  } else if (!c.folder) {
    el.innerHTML = `${who}<span class="tag warn">サインイン待ち</span> スマホで下のページを開き、同じアカウントでサインインしてください。<br>${viewer}`;
  } else {
    el.innerHTML = `${who}<span class="tag">連携中</span> ${c.last_export ? `最終更新 ${fmtWhen(c.last_export)}` : ""}<br>${viewer}`;
  }
  return c;
}

function catRowHTML(c, i, names) {
  return `<div class="cat-edit">
    <label class="row" style="flex-wrap:nowrap"><i class="sw" style="background:${catColor(c.name, names)}"></i>
      <input type="text" name="name" value="${esc(c.name)}" placeholder="分類名" style="width:100%"></label>
    <textarea name="apps" placeholder="code.exe">${esc(c.apps.join("\n"))}</textarea>
    <textarea name="domains" placeholder="arxiv.org">${esc(c.domains.join("\n"))}</textarea>
    <textarea name="titles" placeholder="Jupyter">${esc(c.titles.join("\n"))}</textarea>
    <button class="icon-btn" type="button" data-cat-remove aria-label="この分類を削除">✕</button>
  </div>`;
}

// ------------------------------------------------------------ ビューアー（サインイン）

function renderSignIn(error) {
  clearTimeout(state.refreshTimer);
  setLevel(0);
  $(".tabs").classList.add("hidden");
  $("#pause").classList.add("hidden");
  $("#status").innerHTML = "";
  $("#page-top").innerHTML = "";
  $("#notices").innerHTML = "";
  $("#foot").innerHTML = "";
  view.innerHTML = `<section class="card signin">
      <h1>LabLog</h1>
      <p class="help">PC で連携した Microsoft アカウントでサインインしてください。</p>
      ${error ? `<div class="notice error"><span class="ico">!</span><div>${esc(error)}</div></div>` : ""}
      <button class="btn btn-primary" id="signin">Microsoft でサインイン</button>
      <p class="help muted">読み書きするのは OneDrive の「アプリ／LabLog」フォルダだけです。</p>
    </section>`;
  $("#signin").addEventListener("click", () => cloud.signIn());
}

function renderFooter() {
  $("#foot").innerHTML = `${cloud.build ? `<span>バージョン：${esc(cloud.build)}</span>` : ""}
    ${cloud.dev ? "" : `<button class="btn btn-sm btn-ghost" id="signout">サインアウト</button>`}`;
  const out = $("#signout");
  if (out) out.addEventListener("click", () => {
    if (!confirm("サインアウトします。この端末に保存してある表示用のデータも消します。")) return;
    cloud.signOut();
    location.reload();
  });
}

// ------------------------------------------------------------ 起動

let shownHash = location.hash;
window.addEventListener("hashchange", () => {
  if (location.hash === shownHash) return; // 移るのをやめて、元の画面の URL に戻したとき
  if (hasUnsaved() && !confirm("保存していない内容があります。移動すると消えます。移動しますか？")) {
    location.hash = shownHash;
    return;
  }
  shownHash = location.hash;
  route();
});
window.addEventListener("beforeunload", (e) => {
  if (hasUnsaved()) { e.preventDefault(); e.returnValue = ""; }
});
(async () => {
  if (cloud) {
    $("[data-tab=settings]").remove();
    const reload = $("#pause");
    reload.innerHTML = ICON_RELOAD;
    reload.setAttribute("aria-label", "更新");
    reload.title = "更新";
    const { signedIn, error } = await cloud.ready();
    if (!signedIn) return renderSignIn(error);
    renderFooter();
  }
  await Promise.all([refreshStatus(), cloud ? null : loadSettings().catch(() => null)]);
  if (state.unlinked) return;
  renderNotices();
  await route();
  setInterval(refreshStatus, cloud ? 60000 : 15000);
})();
