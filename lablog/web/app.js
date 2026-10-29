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
const api = cloud ? {
  get: cloud.api.get,
  // 直した内容は PC が取り込むまで「反映待ち」になるので、上部のお知らせも出し直す
  post: (path, body) => cloud.api.post(path, body).then((res) => { refreshStatus(); return res; }),
} : {
  get: (path) => fetch(path, { cache: "no-store" }).then(toJson),
  post: (path, body) => fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-LabLog": "1" },
    body: JSON.stringify(body || {}),
  }).then(toJson),
};

// ------------------------------------------------------------ 書式

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function fmtDur(sec) {
  const m = Math.round((sec || 0) / 60);
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
// 現在時刻（日別の画面の日付の行の右端）。10 秒ごとに書き換える
function nowHM() {
  const d = new Date();
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}
const clockHTML = () => `<span class="clock"><span class="clock-label">現在</span><span class="clock-time">${nowHM()}</span></span>`;
setInterval(() => $$(".clock-time").forEach((el) => { el.textContent = nowHM(); }), 10000);
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
function flash(msg) {
  const el = document.createElement("div");
  el.className = "toast-msg";
  el.textContent = msg;
  document.body.append(el);
  setTimeout(() => el.remove(), 2600);
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
const PRESENCE_LABEL = { activity: "入退室：この PC の操作で判定", manual: "入退室：手動で記録", off: "入退室は記録しません" };
// 判定方法の説明（設定の画面と初期設定の画面で使う）。h：退出を確定するまでの時間
const presenceMethods = (h) => [
  ["wifi", "Wi-Fi で判定する", `研究室の Wi-Fi につながったら入室、研究室の外の Wi-Fi につながったら退室にします。Wi-Fi が切れたら仮退出にし、${h}時間戻らなければ退室にします。`],
  ["activity", "この PC の操作で判定する", `この PC を手元で操作し始めたら入室、操作しなくなったら（ロックしたら）仮退出にし、${h}時間戻らなければ最後に操作した時刻で退室にします。`],
  ["manual", "手動で記録する", `日別の画面の「入室する」「退室する」で記録します。押し忘れたときは、あとから時刻を指定して記録できます。退室を押し忘れたまま PC を${h}時間操作しなかったときは、最後に操作した時刻で退室にします。`],
  ["off", "記録しない", "入退室は記録せず、PC作業時間だけを記録します。"],
];
const leaveHours = () => (state.settings && state.settings.settings.leave_after_hours) || 4;

function renderStatus() {
  const s = state.status;
  if (!s) return;
  if (cloud) return renderCloudStatus(s);
  const parts = [];
  if (s.paused) {
    parts.push(`<span class="pill paused"><span class="dot"></span>記録を一時停止中</span>`);
  }
  if (s.remote) parts.push(`<span class="pill"><span class="dot"></span>リモートデスクトップで接続中</span>`);
  // 在室中かどうかは研究室のタイルに出すので、上部バーには判定に使っているもの（Wi-Fi など）だけを出す
  // 設定を読めていれば設定のほう（保存した直後から切り替わる。記録の処理から届く s.mode は数秒遅れる）
  const mode = state.settings ? presenceMode() : (s.mode || "wifi");
  if (mode === "wifi") {
    const net = s.ssids && s.ssids.length
      ? `Wi-Fi：${esc(s.ssids.join(", "))}${NET_LABEL[s.net] ? `（${NET_LABEL[s.net]}）` : ""}`
      : "Wi-Fi：未接続";
    parts.push(`<span class="net">${net}</span>`);
  } else {
    parts.push(`<span class="net">${PRESENCE_LABEL[mode] || ""}</span>`);
  }
  $("#status").innerHTML = parts.join("");
  const btn = $("#pause");
  const label = s.paused ? "記録を再開" : "記録を一時停止";
  btn.innerHTML = s.paused ? ICON_PLAY : ICON_STOP;
  btn.setAttribute("aria-label", label);
  btn.title = label;
  btn.classList.toggle("btn-primary", !!s.paused);
  renderNotices();
}

// ビューアー：PC が最後に書き出した時点の状態を出す
function renderCloudStatus(s) {
  const parts = [];
  if (s.paused) parts.push(`<span class="pill paused"><span class="dot"></span>記録を一時停止中</span>`);
  if (s.state !== "none") {
    const stale = s.exported && Date.now() / 1000 - s.exported > 15 * 60;
    const pc = s.device ? `PC（${esc(s.device)}）の` : "PC の";
    parts.push(`<span class="net ${stale ? "stale" : ""}">${pc}最終更新：${s.exported ? fmtWhen(s.exported) : "—"}</span>`);
  }
  $("#status").innerHTML = parts.join("");
  renderNotices();
}

function renderNotices() {
  if (location.hash.startsWith("#/setup")) { $("#notices").innerHTML = ""; return; } // 初期設定の画面では出さない
  const s = state.status, settings = state.settings && state.settings.settings;
  const items = [];
  if (cloud && s) {
    if (s.state === "none") items.push(["warn", "まだ PC から記録が届いていません。PC の LabLog が動いていれば、数分で表示されます。"]);
    if (s.offline) items.push(["warn", "インターネットにつながらないため、前に読み込んだ内容を表示しています。"]);
    if (s.pending) items.push(["warn", `直した内容（${s.pending} 件）は、PC の LabLog が次に動いたときに反映されます。`]);
    if (s.state !== "none" && s.viewer_version && versionLess(s.app_version, s.viewer_version)) {
      items.push(["warn", `PC の LabLog が古い版${s.app_version ? `（${esc(s.app_version)}）` : ""}です。PC で LabLog を開き、設定の画面の「LabLog の更新」から更新してください。古いままだと、表示が正しくないことがあります。`]);
    }
  }
  if (!cloud && s && s.update_failed) {
    items.push(["error", `新しい版（${esc(s.update_failed)}）を起動できなかったため、前の版（${esc(s.version)}）に戻しました。困ったときは、LabLog を配った人に知らせてください。`]);
  }
  const byWifi = presenceMode() === "wifi";
  if (byWifi && s && s.wifi_error) items.push(["error", esc(s.wifi_error)]);
  if (byWifi && settings && !settings.wifi.univ.length) {
    items.push(["warn", `研究室の Wi-Fi がまだ登録されていません。研究室にいるときに <a href="#/settings">設定</a> の「研究室として登録」を押してください（研究室専用と大学共通の Wi-Fi の両方）。`]);
  }
  if (settings && !settings.watch_folders.length) {
    items.push(["warn", `監視するフォルダがまだありません。<a href="#/settings">設定</a> から研究のファイルや Git リポジトリを置いているフォルダを追加すると、編集したファイルや Git のコミットも記録されます。`]);
  }
  $("#notices").innerHTML = items.map(([kind, html]) =>
    `<div class="notice ${kind}"><span class="ico">!</span><div>${html}</div></div>`).join("");
}

// 版の番号を比べる（update.py の parse_version と同じ形：1.2.3、テスト版は 1.2.3b1 など）。a が分からなければ古いとみなす
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
    state.status = await api.get("/api/status");
    renderStatus();
  } catch (e) { /* サーバー再起動中など。次の更新で戻る */ }
}

$("#pause").addEventListener("click", async () => {
  if (cloud) { // ビューアーでは「更新」ボタン：画面のファイルごと読み直す（ホーム画面アプリには再読み込みがないため）
    $("#pause").classList.add("spinning");
    cloud.clearCache();
    location.reload();
    return;
  }
  const paused = !(state.status && state.status.paused);
  if (paused && !confirm("作業内容の記録（ウィンドウ・ファイル・履歴）を一時停止します。入退室の判定は続けます。よろしいですか？")) return;
  state.status = await api.post("/api/pause", { paused });
  renderStatus();
  flash(paused ? "記録を一時停止しました" : "記録を再開しました");
});

// ページの一番上では背景に溶け込ませ、少しでも下がったら角丸で浮かせる。下へ送っているあいだは小さく
// （ロゴ・日/月だけ）、上へ戻し始めたら元の大きさに戻す（style.css の at-top・compact）。
// 大きさが変わるときは、帯（.topbar-bg）を古い形から新しい形へ伸び縮みさせ、残る部品は新しい位置まで滑らせる。
// 消える部品は写しを元の場所に残して薄く消し、現れる部品は少し遅れて浮かび上がらせる。すべて同時に動かす
const bar = $(".topbar"), barInner = $(".topbar-inner"), barBg = $(".topbar-bg");
const BAR_MOTION = { duration: 560, easing: "cubic-bezier(.32, .72, 0, 1)" };
const calm = matchMedia("(prefers-reduced-motion: reduce)");
let lastY = 0;

const barParts = () => [...bar.querySelectorAll(".brand, .tabs, .tools, #status > *")].filter((el) => !el.classList.contains("ghost"));
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

async function route() {
  clearTimeout(state.refreshTimer);
  state.tips = [];
  tip.hidden = true;
  const [, page, arg] = (location.hash || "#/day").split("/");
  // 初期設定を終えるまでは、ほかの画面を開いても初期設定に戻す（終えるまで何も記録しないため）。
  // 項目そのものが無い（初期設定ができる前の版のサーバー）ときは、設定済みとみなす
  if (!cloud && state.settings && state.settings.settings.setup_done === false && page !== "setup") {
    location.hash = "#/setup";
    return;
  }
  renderNotices(); // 初期設定の画面ではお知らせを出さないので、画面を切り替えるたびに描き直す
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
  }
}

// ------------------------------------------------------------ 日別

async function renderDay(dateArg, { keepScroll = false } = {}) {
  const today = (state.status && state.status.today) || isoDate(new Date());
  const date = dateArg || today;
  const day = await api.get(`/api/day?date=${encodeURIComponent(date)}`);
  const scrollY = window.scrollY;
  state.tips = [];
  const names = day.category_names;
  const rec = recordFlags();
  const loc = day.work_by_location;
  const locText = ["大学", "自宅", "その他"].filter((k) => loc[k] >= 60)
    .map((k) => `${placeName(k)} ${fmtDur(loc[k])}`).join("・") || "記録なし";

  view.innerHTML = `
    <div class="page-head">
      <a class="btn btn-ghost day-nav" href="#/day/${addDays(day.date, -1)}" aria-label="前の日">‹</a>
      <label class="date-pick" title="日付を選ぶ">
        <h1>${dateHTML(day.date_label)}<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></h1>
        <input type="date" id="pick-date" value="${esc(day.date)}" aria-label="日付を選ぶ">
      </label>
      <a class="btn btn-ghost day-nav" href="#/day/${addDays(day.date, 1)}" aria-label="次の日">›</a>
      ${day.is_today ? "" : `<a class="btn" href="#/day/${today}">今日</a>`}
      ${clockHTML()}
    </div>

    <!-- よく見る部分（メイン）：研究室の滞在と PC作業時間（1 つのタイル）、日報 -->
    <div class="main-area">
    <div class="tile day-tile">
      ${levelHTML(day)}
      <div class="day-stay">${stayHTML(day)}</div>
      <div class="day-work"><div class="label">PC作業時間</div>
        <div class="value">${numUnits(fmtDur(day.work_total))}</div>
        <div class="foot">${esc(locText)}</div></div>
    </div>
      <section class="card" id="report-card"></section>
    </div>

    <!-- 詳細：時間の内訳と、記録の中身（見出しは付けず、間隔と区切り線で分ける） -->
    <hr class="section-break">
    <div class="grid detail">
      <section class="card span-2">
        <div class="card-head"><h2>タイムライン</h2></div>
        ${timelineHTML(day, names)}
      </section>
      <section class="card">
        <div class="card-head"><h2>分類別の時間</h2><span class="sub">PC作業 ${esc(fmtDur(day.work_total))}</span></div>
        ${categoryBarsHTML(day, names)}
      </section>
      <section class="card">
        <div class="card-head"><h2>入退室の記録</h2><span class="spacer"></span>
          <button class="btn btn-sm" id="add-stay">＋ 追加</button></div>
        <div id="stays">${staysHTML(day)}</div>
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
        <div class="card-head"><h2>開いたファイル</h2><span class="sub">Windows の「最近使ったファイル」</span></div>
        <div class="scroll">${recentHTML(day)}</div>
      </section>` : ""}
      ${shown(rec.screen, day.screen_texts) ? `<section class="card">
        <div class="card-head"><h2>画面から読み取った文字</h2><span class="sub">日報の材料（この PC の中だけ・数日で消えます）</span></div>
        <div class="scroll">${screenTextsHTML(day)}</div>
      </section>` : ""}
      ${shown(rec.web, day.web_total) ? `<section class="card">
        <div class="card-head"><h2>閲覧したページ</h2><span class="sub">Chrome・Edge ${day.web_total} 件${day.web_hidden
          ? `（画面に表示しなかったタブ ${day.web_hidden} 件は除いています）` : ""}</span></div>
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
  const presenceBtn = $("#presence-btn");
  const sendPresence = async (time) => {
    const action = presenceBtn.dataset.action;
    presenceBtn.disabled = true;
    try {
      state.status = await api.post("/api/presence", time ? { action, time } : { action });
      renderStatus();
      flash(`${time ? `${time} に` : ""}${action === "enter" ? "入室" : "退室"}にしました`);
      await renderDay(dateArg, { keepScroll: true });
    } catch (err) {
      alert(err.message);
      presenceBtn.disabled = false;
    }
  };
  if (presenceBtn) {
    const timeForm = $("#presence-time");
    presenceBtn.addEventListener("click", () => sendPresence());
    $("#presence-time-open").addEventListener("click", () => {
      timeForm.hidden = !timeForm.hidden;
      if (!timeForm.hidden) { timeForm.time.value = hhmm(); timeForm.time.focus(); }
    });
    $("#presence-time-cancel").addEventListener("click", () => { timeForm.hidden = true; });
    timeForm.addEventListener("submit", (e) => { e.preventDefault(); sendPresence(timeForm.time.value); });
  }
  $$("[data-edit-stay]").forEach((b) => b.addEventListener("click", () =>
    openStayForm(day, day.stays.find((s) => s.id === Number(b.dataset.editStay)))));
  if (keepScroll) window.scrollTo(0, scrollY);

  if (day.is_today) {
    state.refreshTimer = setTimeout(() => {
      const editing = document.activeElement && ["TEXTAREA", "INPUT"].includes(document.activeElement.tagName);
      if (editing || state.reportEditing || state.reportBusy || $("#stay-form")) {
        state.refreshTimer = setTimeout(() => renderDay(dateArg, { keepScroll: true }), 60000);
      } else {
        renderDay(dateArg, { keepScroll: true }).catch(() => {});
      }
    }, 60000);
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
  "研究室": '<path d="M9 3h6M10 3v6.2L4.6 18.6A1.6 1.6 0 0 0 6 21h12a1.6 1.6 0 0 0 1.4-2.4L14 9.2V3"/><path d="M7 15h10"/>',
  "PC作業時間": LAPTOP,
  "日報": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3v18M12.5 8h3.5M12.5 12h3.5"/>',
  "タイムライン": '<path d="M4 6h9M4 12h16M4 18h6"/>',
  "分類別の時間": '<path d="M12 3a9 9 0 1 0 9 9h-9z"/><path d="M15 3.5a9 9 0 0 1 5.5 5.5H15z"/>',
  "入退室の記録": '<path d="M3 21h18M6 21V4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21"/><path d="M14.5 12.5v.01"/>',
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
  "Wi-Fi の登録": '<path d="M2 8.8a15 15 0 0 1 20 0M5 12.4a10 10 0 0 1 14 0M8.5 15.9a5 5 0 0 1 7 0"/><path d="M12 19.5v.01"/>',
  "判定のしかた": '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  "記録しないアプリ": '<path d="M3 3l18 18"/><path d="M10.6 5.1A9.8 9.8 0 0 1 12 5c5 0 8.7 4.2 9.7 7a12 12 0 0 1-2.3 3.4M6.6 6.6C4.4 8 2.9 10.2 2.3 12c1 2.8 4.7 7 9.7 7a9.5 9.5 0 0 0 4.4-1.1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  "スマホで見る": '<rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
  "ローカルAI（日報の要約）": '<path d="M12 3l1.8 4.7 4.7 1.8-4.7 1.8L12 16l-1.8-4.7-4.7-1.8 4.7-1.8z"/><path d="M19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"/>',
  "コミットの通知": '<path d="M6 16v-5a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 21h4"/>',
  "データの保存場所": '<ellipse cx="12" cy="5.5" rx="7.5" ry="2.5"/><path d="M4.5 5.5v13c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5v-13M4.5 12c0 1.4 3.4 2.5 7.5 2.5s7.5-1.1 7.5-2.5"/>',
  "分類のルール": '<path d="M3 12V4.5A1.5 1.5 0 0 1 4.5 3H12l9 9-9 9z"/><path d="M7.5 7.5v.01"/>',
  "監視するフォルダ": '<path d="M10 19H4.5A1.5 1.5 0 0 1 3 17.5v-11A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5V11"/><circle cx="16.5" cy="16" r="3"/><path d="M18.7 18.2L21 20.5"/>',
  "ウィンドウ名から分からない作業": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
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

// 研究室の滞在（日別のタイルの左側・スマホでは上側）
function stayHTML(day) {
  const stays = day.stays, t0 = day.range[0];
  if (!stays.length) {
    return `<div class="label">研究室</div>
      <div class="stay-span empty">
        <span class="cap io">${ICON_IN}IN</span><span class="total muted">研究室には来ていません</span><span class="cap right io">OUT${ICON_OUT}</span>
        <span class="t">—</span><span class="track"></span><span class="t right">—</span>
      </div>
      ${presenceActionsHTML(day)}`;
  }
  const first = stays[0].start, last = stays[stays.length - 1];
  const end = Math.max(...stays.map((s) => s.end));
  // 在室中は、入室から「いま」までをバーの左半分に描き、右半分（まだ来ていない時間）は空けておく
  const span = last.state === "in" ? 50 : 100;
  const pos = (t) => ((t - first) / Math.max(1, end - first)) * span;
  const segs = stays.map((s) => {
    const left = pos(s.start), width = Math.max(1, pos(s.end) - left);
    const what = s.state === "in" ? "在室中" : s.state === "tentative" ? "仮退出中" : "研究室";
    return `<span class="seg ${esc(s.state)}" style="left:${left.toFixed(2)}%;width:${width.toFixed(2)}%"
      ${tipAttr(`<b>${what}</b><div>${fmtHMDay(s.start, t0)}〜${s.state === "in" ? "いま" : fmtHMDay(s.end, t0)}（${fmtDur(s.duration)}）</div>`)}></span>`;
  }).join("");
  // 在室中はまだ OUT していないので薄く、仮退出は「OUT（仮）」
  const exit = last.state === "in" ? { t: "—", cls: "pending", cap: "OUT" }
    : last.state === "tentative" ? { t: fmtHMDay(last.end, t0), cls: "tentative", cap: "OUT（仮）" }
    : { t: fmtHMDay(last.end, t0), cls: "out", cap: "OUT" };
  const back = { activity: "この PC を操作すれば", manual: "「入室する」を押せば" }[presenceMode()] || "研究室の Wi-Fi に戻れば";
  const foot = last.state === "tentative" ? `仮退出から${leaveHours()}時間以内に${back}、滞在が続きます`
    : stays.length > 1 ? `出入り ${stays.length} 回。点線のあいだ（研究室の外）は滞在時間に含めません` : "";
  return `<div class="label">研究室</div>
    <div class="stay-span">
      <span class="cap io in" title="入室">${ICON_IN}IN</span>
      <span class="total">${numUnits(fmtDur(day.stay_total))}${stays.length > 1 ? `<small>${stays.length}回の合計</small>` : ""}</span>
      <span class="cap right io ${exit.cls}" title="退室">${esc(exit.cap)}${ICON_OUT}</span>
      <span class="t num">${fmtHMDay(first, t0)}</span>
      <span class="track ${esc(last.state)}">${segs}<i class="dot start"></i><i class="dot end"></i>${last.state === "in" ? `<span class="now-badge">在室中</span>` : ""}</span>
      <span class="t num right ${last.state === "in" ? "pending" : ""}">${esc(exit.t)}</span>
    </div>
    ${foot ? `<div class="foot">${foot}</div>` : ""}
    ${presenceActionsHTML(day)}`;
}

// 今日の画面だけに出す、手動の入室・退室のボタン（入退室を記録しない設定とビューアーでは出さない）
function presenceActionsHTML(day) {
  const mode = presenceMode();
  if (cloud || !day.is_today || mode === "off") return "";
  const st = state.status && state.status.state;
  const inLab = st === "in" || st === "tentative";
  const hint = inLab ? "研究室を出るときに押すと、いまの時刻で退室にします"
    : mode === "manual" ? "研究室に来たら押してください" : "自動で入室にならないときは、ここで入室にできます";
  const label = inLab ? "退室する" : "入室する";
  // 押し忘れたとき：時刻を指定して記録する（いまより後の時刻は前日とみなす。server.py の post_presence）
  return `<div class="stay-actions">
      <button class="btn btn-sm" id="presence-btn" data-action="${inLab ? "leave" : "enter"}">${label}</button>
      <button class="btn btn-sm btn-ghost" type="button" id="presence-time-open">時刻を指定…</button>
      <span class="muted">${hint}</span>
      <form class="presence-time" id="presence-time" hidden>
        <label>${inLab ? "退室した時刻" : "入室した時刻"} <input type="time" name="time" required></label>
        <button class="btn btn-sm btn-primary" type="submit">この時刻で${inLab ? "退室" : "入室"}</button>
        <button class="btn btn-sm btn-ghost" type="button" id="presence-time-cancel">やめる</button>
      </form>
    </div>`;
}

const hhmm = (d = new Date()) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

function timelineHTML(day, names) {
  const [t0, t1] = day.range;
  let lo = Infinity, hi = -Infinity;
  for (const b of day.timeline) { lo = Math.min(lo, b.start); hi = Math.max(hi, b.end); }
  for (const s of day.stays) { lo = Math.min(lo, s.start); hi = Math.max(hi, s.end); }
  if (!isFinite(lo)) return `<div class="empty">この日の記録はまだありません。</div>`;
  if (day.is_today) hi = Math.max(hi, Math.min(day.now, t1));
  let vs = t0 + Math.floor((lo - t0) / HOUR) * HOUR;
  let ve = t0 + Math.ceil((hi - t0) / HOUR) * HOUR;
  if (ve - vs < 6 * HOUR) { ve = Math.min(t1, vs + 6 * HOUR); vs = Math.max(t0, ve - 6 * HOUR); }
  const span = ve - vs;
  const L = (t) => `${(((t - vs) / span) * 100).toFixed(3)}%`;
  const W = (a, b) => `${((Math.max(0, b - a) / span) * 100).toFixed(3)}%`;

  const step = span > 12 * HOUR ? 2 * HOUR : HOUR;
  let ticks = "", grid = "";
  for (let t = vs; t <= ve + 1; t += step) {
    ticks += `<span class="tl-tick" style="left:${L(t)}">${new Date(t * 1000).getHours()}</span>`;
    grid += `<span class="tl-grid" style="left:calc(${L(t)})"></span>`;
  }

  const stays = day.stays.map((s) => {
    const label = s.state === "tentative" ? "仮退出中" : "研究室";
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

function reasonLabel(reason) {
  const h = leaveHours();
  return { offcampus: "研究室以外の Wi-Fi に接続", timeout: `${h}時間戻らず`, idle: `${h}時間操作なし`,
           manual: "手動で修正", manual_out: "手動で退室" }[reason] || reason;
}

function staysHTML(day) {
  if (!day.stays.length) return `<div class="empty">この日は研究室にいた記録がありません。</div>`;
  return `<div class="stay-list">${day.stays.map((s) => `
    <div class="stay-row">
      <span class="time">${fmtHMDay(s.start, day.range[0])} 〜 ${s.state === "in" ? "在室中" : fmtHMDay(s.end, day.range[0])}</span>
      <span class="ink2">${fmtDur(s.duration)}</span>
      ${s.state === "tentative" ? `<span class="tag warn">仮退出中</span>` : ""}
      ${s.reason ? `<span class="tag">${esc(reasonLabel(s.reason))}</span>` : ""}
      ${s.pending ? `<span class="tag warn">PC へ反映待ち</span>` : ""}
      <span class="spacer"></span>
      ${s.id == null ? "" : `<button class="btn btn-sm btn-ghost" data-edit-stay="${s.id}">編集</button>`}
    </div>`).join("")}</div>`;
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
      const since = r.last_commit_ts ? fmtDur(now - r.last_commit_ts) + "前" : "コミットなし";
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
    return `<div class="empty">${on ? "この日の読み取り結果はありません（残す日数を過ぎたものは消えています）。"
      : `読み取りはオフです。<a href="#/settings">設定</a>の「ウィンドウ名から分からない作業」で変えられます。`}</div>`;
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

function reportMeta(r) {
  if (!r) return "まだ作成されていません。退出が確定したときと、日付が変わったときに自動で作ります。";
  if (r.pending) return "保存しました（PC の LabLog が次に動いたときに反映されます）";
  if (r.edited) return "自分で編集した日報です（自動では上書きしません）";
  const when = r.generated_ts ? `${new Date(r.generated_ts * 1000).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} に作成` : "";
  if ((r.source || "").startsWith("ai:")) return `ローカルAI（${esc(r.source.slice(3))}）が作成した下書き・${when}`;
  return `集計から作成した下書き・${when}（ローカルAIを設定すると文章で要約します）`;
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
  const r = day.report;
  const text = (r && r.text) || "";
  state.reportEditing = editing;
  state.reportDirty = false;
  const buttons = editing
    ? `<button class="btn" id="report-cancel">キャンセル</button>
       <button class="btn btn-primary" id="report-save" disabled>保存</button>`
    : `${cloud ? "" : `<button class="btn" id="report-gen">${text ? "下書きを作り直す" : "下書きを作成"}</button>`}
       <button class="btn ${text ? "" : "btn-primary"}" id="report-edit">${text ? "編集" : "書く"}</button>`;
  const body = editing
    ? `<textarea id="report-text" class="note" placeholder="今日やったことをメモできます。「## 見出し」「- 箇条書き」のように書くと、整えて表示します。">${esc(text)}</textarea>`
    : text ? `<div class="md note">${renderMarkdown(text)}</div>`
    : `<div class="empty">${cloud ? "「書く」で、この日のメモを残せます。" : "「下書きを作成」で記録から下書きを作ります。「書く」で自分でメモすることもできます。"}</div>`;
  card.innerHTML = `
    <div class="card-head report-head"><h2>日報</h2><span class="spacer"></span>${buttons}</div>
    <div class="report-meta" id="report-meta">${reportMeta(r)}</div>
    ${body}`;

  if (editing) {
    const ta = $("#report-text"), save = $("#report-save");
    fitTextarea(ta);
    ta.focus();
    ta.addEventListener("input", () => { state.reportDirty = true; save.disabled = false; fitTextarea(ta); });
    $("#report-cancel").addEventListener("click", () => {
      if (state.reportDirty && !confirm("書きかけの内容を捨てて戻ります。よろしいですか？")) return;
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
      day.report = { ...(r || {}), text: ta.value, edited: 1, pending: !!cloud };
      renderReport(day);
      flash("日報を保存しました");
    });
    return;
  }

  $("#report-edit").addEventListener("click", () => renderReport(day, { editing: true }));
  const gen = $("#report-gen");
  if (gen) gen.addEventListener("click", async () => {
    if (r && r.edited && !confirm("編集した内容を、記録から作った下書きで置き換えます。よろしいですか？")) return;
    gen.disabled = true;
    $("#report-edit").disabled = true;
    gen.textContent = "作成中…";
    $("#report-meta").textContent = "作成中です。ローカルAIを使う場合は1分前後かかります。";
    state.reportBusy = true;
    try {
      day.report = await api.post("/api/report/generate", { date: day.date, force: true });
    } catch (err) {
      alert(err.message);
    } finally {
      state.reportBusy = false;
      renderReport(day);
    }
  });
}

// ------------------------------------------------------------ 月

async function renderMonth(monthArg) {
  const today = (state.status && state.status.today) || isoDate(new Date());
  const month = monthArg || today.slice(0, 7);
  const m = await api.get(`/api/month?month=${encodeURIComponent(month)}`);
  const [y, mo] = month.split("-").map(Number);
  const maxStay = Math.max(HOUR, ...m.days.map((d) => d.stay));
  const lead = (new Date(y, mo - 1, 1).getDay() + 6) % 7; // 月曜始まり
  const avg = m.stay_days ? m.stay_total / m.stay_days : 0;
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(`<div class="cell blank"></div>`);
  for (const d of m.days) {
    const n = Number(d.date.slice(8));
    const tipHtml = `<b>${esc(d.date)}</b>`
      + (d.stay ? `<div>研究室 ${fmtHM(d.first_in)}〜${fmtHM(d.last_out)}（${fmtDur(d.stay)}）</div>` : `<div>研究室：なし</div>`)
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
      <a class="btn btn-ghost" href="#/month/${addMonths(month, -1)}" aria-label="前の月">‹</a>
      <h1>${y}年${mo}月</h1>
      <a class="btn btn-ghost" href="#/month/${addMonths(month, 1)}" aria-label="次の月">›</a>
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

// 記録する材料のオンオフ（設定を読めていないときは全部オンとみなす）
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

async function renderSettings() {
  const { settings: s, data_dir: dataDir, repos, ssids } = await loadSettings();
  const current = ssids && ssids[0];
  const where = !current ? "未接続" : s.wifi.univ.includes(current) ? "研究室として登録済み"
    : s.wifi.home.includes(current) ? "自宅として登録済み"
    : s.wifi.ignore.includes(current) ? "判定に使わない Wi-Fi として登録済み" : "未登録";
  const ssidList = (kind) => s.wifi[kind].length ? s.wifi[kind].map((x) => `
      <div class="set-item"><span class="main">${esc(x)}</span>
      <button class="icon-btn" data-wifi-remove="${esc(kind)}" data-ssid="${esc(x)}" aria-label="${esc(x)} を削除">✕</button></div>`).join("")
    : `<div class="empty">まだありません</div>`;

  const catNames = s.categories.map((c) => c.name).concat(["その他"]);

  const rec = s.record;
  const modeOption = ([value, label, desc]) => `<label class="check"><input type="radio" name="mode" value="${value}" ${s.presence.mode === value ? "checked" : ""}>
      <span><b>${label}</b><br><span class="help">${esc(desc)}</span></span></label>`;
  const placeOption = (value, label) => `<option value="${value}" ${s.presence.place === value ? "selected" : ""}>${label}</option>`;
  const appsOption = (value, label) => `<option value="${value}" ${rec.apps === value ? "selected" : ""}>${label}</option>`;

  view.innerHTML = `
    <div class="page-head"><h1>設定</h1><span class="spacer"></span><a class="btn" href="#/setup">初期設定をやり直す</a></div>
    <div class="grid">
      <section class="card span-2">
        <div class="card-head"><h2>記録する材料</h2><span class="sub">どれもこの PC の中だけに保存し、日報の材料にします</span></div>
        <form id="record-form">
          <label class="field"><span>使ったアプリとウィンドウ</span>
            <select name="apps">${appsOption("title", "アプリ名とウィンドウ名を記録する")}${appsOption("app", "アプリ名だけ記録する")}${appsOption("off", "記録しない（PC作業時間だけ残す）")}</select></label>
          <p class="help">ウィンドウ名には、開いている文書やページの名前が出ます。ウィンドウ名を記録しないと、ブラウザで見ていたサイトの分類や、日報の作業内容が分かりにくくなります。</p>
          <label class="check"><input type="checkbox" name="files" ${rec.files ? "checked" : ""}> 監視するフォルダで編集したファイル（パス・時刻・サイズだけ。中身は読みません）</label>
          <label class="check"><input type="checkbox" name="git" ${rec.git ? "checked" : ""}> 監視するフォルダにある Git リポジトリのコミットと、未コミットの変更</label>
          <label class="check"><input type="checkbox" name="chrome" ${s.browsers.chrome ? "checked" : ""}> Chrome の閲覧履歴（シークレットウィンドウは対象外）</label>
          <label class="check"><input type="checkbox" name="edge" ${s.browsers.edge ? "checked" : ""}> Edge の閲覧履歴（InPrivate ウィンドウは対象外）</label>
          <label class="check"><input type="checkbox" name="recent" ${rec.recent_files ? "checked" : ""}> Windows の「最近使ったファイル」</label>
          <label class="check"><input type="checkbox" name="claude" ${s.claude_code.enabled ? "checked" : ""}> Claude Code の会話の題名と作業フォルダ（会話の中身は記録しません）</label>
          <label class="check"><input type="checkbox" name="ocr" ${s.screen_text.enabled ? "checked" : ""}> 画面の文字（対象のアプリが前面にあるときだけ読み取ります。対象と間隔は「ウィンドウ名から分からない作業」で変えられます）</label>
          <div class="row"><span class="spacer"></span><button class="btn btn-primary" type="submit">保存</button></div>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>入退室の判定</h2></div>
        <form id="judge-form">
          <p class="help">研究室に来た時刻・帰った時刻を、どうやって判定するかを選んでください。</p>
          ${presenceMethods(s.leave_after_hours).map(modeOption).join("")}
          <p class="help">どの方法でも、Windows のリモートデスクトップで外から操作している間は研究室にいないとみなし、作業時間は「自宅」として数えます。「記録しない」以外では、日別の画面の「入室する」「退室する」で手動でも切り替えられます。</p>
          <label class="field"><span>仮退出にするまでの時間（「この PC の操作で判定する」のとき。手元の操作がない分数）</span>
            <input type="number" name="away" min="1" max="120" step="1" value="${s.presence.away_min}" style="width:8em"></label>
          <label class="field"><span>PC作業時間の場所（「記録しない」のとき）</span>
            <select name="place" style="width:12em">${placeOption("home", "自宅")}${placeOption("other", "その他")}</select></label>
          <label class="field"><span>離席とみなす時間（操作がない分数。PC作業時間から除きます）</span>
            <input type="number" name="idle" min="1" max="60" step="1" value="${s.idle_threshold_min}" style="width:8em"></label>
          <label class="field"><span>退出を確定するまでの時間（仮退出・操作なしの時間数）</span>
            <input type="number" name="leave" min="1" max="24" step="0.5" value="${s.leave_after_hours}" style="width:8em"></label>
          <button class="btn btn-primary" type="submit">保存</button>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>Wi-Fi の登録</h2></div>
        ${s.presence.mode !== "wifi" ? `<p class="help"><span class="tag">いまは使っていません</span> Wi-Fi を使うのは、入退室の判定方法が「Wi-Fi で判定する」のときだけです。</p>` : ""}
        <p class="help">いま接続中：<b>${esc(current || "なし")}</b>（${where}）</p>
        <div class="row">
          <button class="btn btn-primary" data-wifi-register="univ" ${current ? "" : "disabled"}>研究室として登録</button>
          <button class="btn" data-wifi-register="home" ${current ? "" : "disabled"}>自宅として登録</button>
          <button class="btn" data-wifi-register="ignore" ${current ? "" : "disabled"}>判定に使わない</button>
        </div>
        <p class="help">研究室専用の Wi-Fi と、大学共通の Wi-Fi（ScienceTokyo など）は、どちらも「研究室」として登録してください。
          スマホのテザリングなど、研究室でも外でも使う Wi-Fi は「判定に使わない」にします。つないでいる間は、直前の状態（在室中・仮退出中）をそのまま続けます。</p>
        <h3 class="help" style="margin-top:12px">研究室</h3><div class="set-list">${ssidList("univ")}</div>
        <h3 class="help">自宅</h3><div class="set-list">${ssidList("home")}</div>
        <h3 class="help">判定に使わない</h3><div class="set-list">${ssidList("ignore")}</div>
      </section>

      <section class="card">
        <div class="card-head"><h2>監視するフォルダ</h2></div>
        <p class="help">フォルダ内で作成・保存・削除したファイルと、その中の Git リポジトリのコミットを記録します（ファイルの中身は読みません）。</p>
        <div class="set-list">${s.watch_folders.length ? s.watch_folders.map((f) => `
          <div class="set-item"><span class="main">${esc(f)}</span>
          <button class="icon-btn" data-folder-remove="${esc(f)}" aria-label="削除">✕</button></div>`).join("") : `<div class="empty">まだありません</div>`}</div>
        <div class="row">
          <button class="btn btn-primary" id="folder-pick">フォルダを選んで追加…</button>
        </div>
        <form class="row" id="folder-form" style="margin-top:8px">
          <input type="text" name="path" placeholder="または、フォルダのパスを貼り付け" style="flex:1;min-width:200px">
          <button class="btn" type="submit">追加</button>
        </form>
        <h3 class="help" style="margin-top:12px">見つかった Git リポジトリ</h3>
        <div class="set-list">${repos.length ? repos.map((r) => `<div class="set-item"><span class="main">${esc(r)}</span></div>`).join("")
          : `<div class="empty">まだ見つかっていません（フォルダ追加後、数秒〜数分で表示されます）</div>`}</div>
      </section>

      <section class="card">
        <div class="card-head"><h2>コミットの通知</h2></div>
        <form id="git-form">
          <label class="check"><input type="checkbox" name="enabled" ${s.git_notify.enabled ? "checked" : ""}> 未コミットのまま時間がたったら Windows の通知で知らせる</label>
          <label class="field" style="margin-top:10px"><span>知らせるまでの時間（時間）</span>
            <input type="number" name="hours" min="0.5" max="24" step="0.5" value="${s.git_notify.hours}" style="width:8em"></label>
          <p class="help">作業中（最近ファイルを編集した）のリポジトリだけが対象です。一度知らせたら、同じ時間がたつまで再通知しません。</p>
          <button class="btn btn-primary" type="submit">保存</button>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>ローカルAI（日報の要約）</h2></div>
        <p class="help" id="ollama-state"><span class="tag">確認中…</span></p>
        <form id="ollama-form">
          <label class="check"><input type="checkbox" name="enabled" ${s.ollama.enabled ? "checked" : ""}> 日報に AI を使う（使わないときは、集計から箇条書きの下書きを作ります）</label>
          <label class="field"><span>モデル名</span><input type="text" name="model" value="${esc(s.ollama.model)}"></label>
          <label class="check"><input type="checkbox" name="auto" ${s.ollama.auto ? "checked" : ""}> 退出確定時と日付の切り替わりに、日報の下書きを自動で作る（外すと、日報は自分で書きます）</label>
          <p class="help">接続先は PC 内（${esc(s.ollama.url)}）だけです。記録が外部に送られることはありません。</p>
          <button class="btn btn-primary" type="submit">保存</button>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>スマホで見る</h2></div>
        <p class="help" id="cloud-state"><span class="tag">確認中…</span></p>
        <p class="help">OneDrive に置くのは要約だけです（入退室・PC作業時間・分類別の時間・日報）。ウィンドウ名・閲覧したページ・ファイルの場所は、この PC から出しません。</p>
        <form id="cloud-form" class="row">
          <label class="check"><input type="checkbox" name="enabled" ${s.cloud.enabled ? "checked" : ""}> OneDrive に書き出す</label>
          <span class="spacer"></span>
          <button class="btn" type="button" id="cloud-export">今すぐ書き出す</button>
          <button class="btn btn-primary" type="submit">保存</button>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>ウィンドウ名から分からない作業</h2></div>
        <form id="insight-form">
          <p class="help">Claude Code の会話の題名は、ウィンドウ名が「Claude」だけのときに補うために使います。画面の文字は、下のアプリが前面にあるときに読み取って日報の材料にします。どちらも「記録する材料」でオンオフできます。</p>
          <label class="field" style="margin-top:8px"><span>画面の文字を読み取るアプリ（実行ファイル名を 1 行に 1 つ）</span>
            <textarea name="apps" rows="6">${esc(s.screen_text.apps.join("\n"))}</textarea></label>
          <p class="help">ウィンドウ名から作業内容が分からないアプリだけを入れます。ブラウザ（閲覧履歴を使う）、Word・Excel・PowerPoint・VS Code・Notion（ウィンドウ名に文書やページの名前が出る）、エクスプローラー、Git（コミットを記録する）は入れなくて大丈夫です。</p>
          <div class="row">
            <label class="field"><span>読み取る間隔（分）</span><input type="number" name="interval" min="1" max="60" step="1" value="${s.screen_text.interval_min}" style="width:7em"></label>
            <label class="field"><span>読み取った文字を残す日数</span><input type="number" name="keep" min="1" max="30" step="1" value="${s.screen_text.keep_days}" style="width:7em"></label>
          </div>
          <p class="help">撮るのは対象アプリのウィンドウだけで、画像は保存しません（すぐ文字にして捨てます）。文字はこの PC の中だけに置いて日報の材料にし、残す日数を過ぎたら消します。OneDrive には書き出しません。一時停止中・記録しないアプリ・2 分以上操作していないときは読み取りません。</p>
          <div class="row">
            <span class="help">いま残っている読み取り結果：${state.settings.screen_text_count} 件</span>
            <button class="btn btn-sm" type="button" id="ocr-clear" ${state.settings.screen_text_count ? "" : "disabled"}>すべて消す</button>
            <span class="spacer"></span>
            <button class="btn btn-primary" type="submit">保存</button>
          </div>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>記録しないアプリ</h2></div>
        <p class="help">ここに入れたアプリは、使った時間だけを「(除外)」として残し、アプリ名とウィンドウ名は記録しません。実行ファイル名を 1 行に 1 つ（例：<code>line.exe</code>）。</p>
        <form id="exclude-form">
          <textarea name="apps" rows="4">${esc(s.excluded_apps.join("\n"))}</textarea>
          <div class="row" style="margin-top:8px">
            <span class="spacer"></span>
            <button class="btn btn-primary" type="submit">保存</button>
          </div>
        </form>
      </section>

      <section class="card">
        <div class="card-head"><h2>LabLog の更新</h2></div>
        <p class="help" id="update-current">いまの版：確認中…</p>
        <form id="update-form">
          <label class="check"><input type="radio" name="channel" value="stable" ${s.update.channel === "stable" ? "checked" : ""}>
            <span><b>安定版</b><br><span class="help">正式に公開された版だけを受け取ります。</span></span></label>
          <label class="check"><input type="radio" name="channel" value="beta" ${s.update.channel === "beta" ? "checked" : ""}>
            <span><b>テスト版</b><br><span class="help">公開前の版も受け取ります。新しい機能を先に試せますが、不具合があるかもしれません。</span></span></label>
          <div class="row"><button class="btn btn-primary" type="submit">更新を確認</button></div>
        </form>
        <div id="update-result"></div>
        <p class="help">「更新を確認」を押したときだけ、配布元（GitHub）に新しい版があるかを問い合わせます。送るのは版を尋ねるリクエストだけで、記録は送りません。</p>
      </section>

      <section class="card span-2">
        <div class="card-head"><h2>分類のルール</h2>
          <span class="sub">上から順に色が決まります。ドメインの先頭に「=」を付けると完全一致（例：=google.com は検索だけ）</span></div>
        <form id="cat-form">
          <div class="cat-edit cat-head"><span>分類名</span><span>アプリ（実行ファイル名）</span><span>サイト（ドメイン）</span><span>ウィンドウ名に含む語</span><span></span></div>
          <div id="cat-rows">${s.categories.map((c, i) => catRowHTML(c, i, catNames)).join("")}</div>
          <div class="row" style="margin-top:10px">
            <button class="btn" type="button" id="cat-add">＋ 分類を追加</button>
            <button class="btn btn-ghost" type="button" id="cat-reset">初期値に戻す</button>
            <span class="spacer"></span>
            <button class="btn btn-primary" type="submit">分類を保存</button>
          </div>
        </form>
      </section>

      <section class="card span-2">
        <div class="card-head"><h2>データの保存場所</h2></div>
        <p class="help"><code>${esc(dataDir)}</code><br>記録そのものは OneDrive の外に保存しているので、クラウドには同期されません（スマホ用に書き出すのは要約だけです）。ダッシュボードはこの PC からだけ開けます。</p>
      </section>
    </div>`;

  api.get("/api/ollama").catch(() => ({ running: false })).then((o) => {
    const el = $("#ollama-state");
    if (!el) return;
    if (!s.ollama.enabled) {
      el.innerHTML = `<span class="tag">使わない設定</span> 日報は集計から作ります。`;
    } else if (!o.running) {
      el.innerHTML = `<span class="tag warn">Ollama が起動していません</span> 日報は集計から作ります。`;
    } else if (!o.model_ready) {
      el.innerHTML = `<span class="tag warn">モデル ${esc(s.ollama.model)} が未ダウンロード</span> ターミナルで <code>ollama pull ${esc(s.ollama.model)}</code> を実行してください。`;
    } else {
      el.innerHTML = `<span class="tag">準備完了</span> ${esc(s.ollama.model)} で日報を要約します。`;
    }
  });
  loadCloudState();
  loadUpdateState();
  $("#update-form").addEventListener("submit", (e) => { e.preventDefault(); checkUpdate(e.target.channel.value); });
  $("#cloud-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (await saveSettings({ cloud: { ...s.cloud, enabled: e.target.enabled.checked } })) loadCloudState();
  });
  $("#cloud-export").addEventListener("click", async () => {
    await api.post("/api/cloud/export").catch((err) => alert(err.message));
    flash("書き出しを始めました");
    setTimeout(loadCloudState, 3000);
  });
  $$("[data-wifi-register]").forEach((b) => b.addEventListener("click", async () => {
    await api.post("/api/wifi/register", { kind: b.dataset.wifiRegister }).catch((e) => alert(e.message));
    flash("登録しました");
    renderSettings();
    refreshStatus();
  }));
  $$("[data-wifi-remove]").forEach((b) => b.addEventListener("click", async () => {
    await api.post("/api/wifi/remove", { kind: b.dataset.wifiRemove, ssid: b.dataset.ssid });
    renderSettings();
  }));
  $$("[data-folder-remove]").forEach((b) => b.addEventListener("click", async () => {
    if (!confirm(`監視をやめます：\n${b.dataset.folderRemove}\n（これまでの記録は残ります）`)) return;
    await api.post("/api/folders/remove", { path: b.dataset.folderRemove });
    await loadSettings();
    renderSettings();
  }));
  $("#folder-pick").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "フォルダ選択画面を開いています…";
    try {
      const { path } = await api.post("/api/folders/pick");
      if (path) {
        await api.post("/api/folders/add", { path });
        flash("フォルダを追加しました");
        await loadSettings();
        renderNotices();
        setTimeout(renderSettings, 4000);
        renderSettings();
      }
    } catch (err) { alert(err.message); }
    finally { btn.disabled = false; btn.textContent = "フォルダを選んで追加…"; }
  });
  $("#folder-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await api.post("/api/folders/add", { path: e.target.path.value });
      flash("フォルダを追加しました");
      await loadSettings();
      renderNotices();
      renderSettings();
    } catch (err) { alert(err.message); }
  });
  $("#record-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const ok = await saveSettings({
      record: { apps: f.apps.value, files: f.files.checked, git: f.git.checked, recent_files: f.recent.checked },
      browsers: { chrome: f.chrome.checked, edge: f.edge.checked },
      claude_code: { enabled: f.claude.checked },
      screen_text: { ...s.screen_text, enabled: f.ocr.checked },
    });
    if (ok) renderSettings();
  });
  $("#git-form").addEventListener("submit", (e) => {
    e.preventDefault();
    saveSettings({ git_notify: { enabled: e.target.enabled.checked, hours: Number(e.target.hours.value) } });
  });
  $("#ollama-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (await saveSettings({ ollama: { enabled: f.enabled.checked, url: s.ollama.url, model: f.model.value,
                                       auto: f.auto.checked } })) renderSettings();
  });
  $("#insight-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    if (await saveSettings({ screen_text: { enabled: s.screen_text.enabled, apps: lines(f.apps.value),
                                            interval_min: Number(f.interval.value), keep_days: Number(f.keep.value) } })) renderSettings();
  });
  $("#ocr-clear").addEventListener("click", async () => {
    if (!confirm("画面から読み取った文字をすべて消します。日報の材料からもなくなります。よろしいですか？")) return;
    await api.post("/api/screen_texts/clear");
    flash("読み取った文字を消しました");
    renderSettings();
  });
  $("#judge-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const ok = await saveSettings({ presence: { ...s.presence, mode: f.mode.value, away_min: Number(f.away.value), place: f.place.value },
      idle_threshold_min: Number(f.idle.value), leave_after_hours: Number(f.leave.value) });
    if (ok) { renderSettings(); refreshStatus(); }
  });
  $("#exclude-form").addEventListener("submit", (e) => {
    e.preventDefault();
    saveSettings({ excluded_apps: lines(e.target.apps.value) });
  });
  $("#cat-add").addEventListener("click", () => {
    const rows = $("#cat-rows");
    rows.insertAdjacentHTML("beforeend", catRowHTML({ name: "", apps: [], domains: [], titles: [] }, rows.children.length, catNames));
  });
  $("#cat-reset").addEventListener("click", async () => {
    if (!confirm("分類のルールを初期値に戻します。自分で変えた分は消えます。よろしいですか？")) return;
    await api.post("/api/categories/reset");
    await loadSettings();
    flash("分類を初期値に戻しました");
    renderSettings();
  });
  $("#cat-rows").addEventListener("click", (e) => {
    const b = e.target.closest("[data-cat-remove]");
    if (b) b.closest(".cat-edit").remove();
  });
  $("#cat-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const categories = $$("#cat-rows .cat-edit").map((row) => ({
      name: $("[name=name]", row).value.trim(),
      apps: lines($("[name=apps]", row).value),
      domains: lines($("[name=domains]", row).value),
      titles: lines($("[name=titles]", row).value),
    })).filter((c) => c.name);
    if (await saveSettings({ categories }, "分類を保存しました（過去の記録にも反映されます）")) renderSettings();
  });
}

// ------------------------------------------------------------ 更新（設定の画面の「LabLog の更新」）

async function loadUpdateState() {
  const u = await api.get("/api/update").catch(() => null);
  const el = $("#update-current");
  if (!el || !u) return;
  state.serverStarted = u.started;
  el.innerHTML = `いまの版：<b>${esc(u.current)}</b>${u.installed ? ""
    : `<br><span class="tag">開発用</span> リポジトリのフォルダから動いているため、ここからは更新しません（git pull で更新します）。`}`;
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
  if (!rel) { out.innerHTML = `<p class="help">公開されている版が、まだありません。</p>`; return; }
  if (!r.newer) { out.innerHTML = `<p class="help"><span class="tag">最新です</span> いまの版（${esc(r.current)}）が最新です。</p>`; return; }
  const size = rel.size ? `約 ${(rel.size / 1024 / 1024).toFixed(1)} MB` : "";
  const date = rel.published ? `、${new Date(rel.published).toLocaleDateString("ja-JP")} 公開` : "";
  out.innerHTML = `
    <p><b>新しい版 ${esc(rel.version)}</b> があります（${rel.prerelease ? "テスト版" : "安定版"}${date}）。</p>
    ${rel.notes ? `<div class="md update-notes">${renderMarkdown(rel.notes)}</div>` : ""}
    ${r.installed ? `
      <div class="row"><button class="btn btn-primary" id="update-install">更新する（${size}）</button></div>
      <p class="help">ダウンロードして切り替えたあと、LabLog を起動し直します。記録と設定はそのまま引き継ぎます。新しい版を起動できなかったときは、自動で前の版に戻します。</p>`
    : `<p class="help">開発用のフォルダから動いているため、ここからは更新しません。</p>`}`;
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
    out.innerHTML = `<p class="help"><span class="tag warn">更新できませんでした</span> ${esc(job.message)}（いまの版のまま動いています）</p>`;
    return;
  }
  const pct = job.progress == null ? null : Math.round(job.progress * 100);
  out.innerHTML = `<p class="help">${esc(job.message || "")}${pct == null ? "" : `（${pct}%）`}</p>
    <div class="progress ${pct == null ? "busy" : ""}"><i style="width:${pct ?? 0}%"></i></div>`;
  if (job.state === "restarting") { waitForRestart(); return; }
  if (job.state === "running") {
    setTimeout(async () => { const u = await api.get("/api/update").catch(() => null); if (u) renderUpdateJob(u.job); }, 1000);
  }
}

// 起動し直したら（サーバーが起動した時刻が変わったら）開き直す。止まっている時間は短く、確かめる合間に
// 終わることもあるので、止まったかどうかでは判断しない（新しい版を起動できず前の版に戻ったときも、上部のお知らせで分かる）
async function waitForRestart() {
  for (let i = 0; i < 180; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    const u = await api.get("/api/update").catch(() => null);
    if (u && u.started !== state.serverStarted) { location.reload(); return; }
  }
  const out = $("#update-result");
  if (out) out.innerHTML = `<p class="help"><span class="tag warn">起動し直すのに時間がかかっています</span> スタートメニューの LabLog から開き直してください。</p>`;
}

async function loadCloudState() {
  const c = await api.get("/api/cloud").catch(() => null);
  const el = $("#cloud-state");
  if (!el || !c) return;
  const viewer = c.viewer_url
    ? `ビューアー：<a href="${safeHref(c.viewer_url)}" target="_blank" rel="noopener noreferrer">${esc(c.viewer_url)}</a>` : "";
  if (!c.enabled) {
    el.innerHTML = `<span class="tag">オフ</span> 書き出しを止めています。`;
  } else if (c.blocked_by) {
    // スマホに出せるのは 1 台の PC の記録だけ（cloud.py の _other_writer）
    const other = esc(c.blocked_by.name);
    el.innerHTML = `<span class="tag warn">別の PC が書き出しています</span> 「${other}」が、この OneDrive に書き出しています（最終更新 ${fmtWhen(c.blocked_by.exported)}）。スマホに出せるのは 1 台の PC の記録だけなので、この PC からは書き出していません。
      <div class="row" style="margin-top:8px"><button class="btn btn-sm" type="button" id="cloud-takeover">この PC から書き出す</button></div>`;
    $("#cloud-takeover").addEventListener("click", async () => {
      if (!confirm(`書き出す PC を、この PC に切り替えます。「${c.blocked_by.name}」からの書き出しは止まり、スマホにはこの PC の記録が出ます。よろしいですか？`)) return;
      await api.post("/api/cloud/takeover").catch((err) => alert(err.message));
      flash("この PC から書き出します");
      setTimeout(loadCloudState, 3000);
    });
  } else if (c.last_error) {
    el.innerHTML = `<span class="tag warn">書き出せませんでした</span> ${esc(c.last_error)}`;
  } else if (!c.folder) {
    el.innerHTML = `<span class="tag warn">書き出し先がまだありません</span> スマホでビューアーを開いて Microsoft アカウントでサインインすると、OneDrive に専用のフォルダができて、数分で書き出しを始めます。<br>${viewer}`;
  } else {
    el.innerHTML = `<span class="tag">書き出し中</span> ${c.last_export ? `最終更新 ${fmtWhen(c.last_export)}・` : ""}書き出し先 <code>${esc(c.folder)}</code><br>${viewer}`;
  }
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
  $("#notices").innerHTML = "";
  view.innerHTML = `<section class="card signin">
      <h1>LabLog</h1>
      <p class="help">PC の LabLog が OneDrive に保存した記録（入退室・作業時間・日報）を表示します。<br>
        PC で使っている個人の Microsoft アカウントでサインインしてください。</p>
      ${error ? `<div class="notice error"><span class="ico">!</span><div>${esc(error)}</div></div>` : ""}
      <button class="btn btn-primary" id="signin">Microsoft でサインイン</button>
      <p class="help muted">このアプリが読み書きするのは、OneDrive の「アプリ／LabLog」フォルダだけです。</p>
    </section>`;
  $("#signin").addEventListener("click", () => cloud.signIn());
}

function renderFooter() {
  $("#foot").innerHTML = `${cloud.build ? `<span>画面の版：${esc(cloud.build)}</span>` : ""}
    ${cloud.dev ? "" : `<button class="btn btn-sm btn-ghost" id="signout">サインアウト</button>`}`;
  const out = $("#signout");
  if (out) out.addEventListener("click", () => {
    if (!confirm("サインアウトします。この端末に保存してある表示用のデータも消します。")) return;
    cloud.signOut();
    location.reload();
  });
}

// ------------------------------------------------------------ 起動

window.addEventListener("hashchange", route);
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
  renderNotices();
  await route();
  setInterval(refreshStatus, cloud ? 60000 : 15000);
})();
