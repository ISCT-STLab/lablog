// 初期設定の画面（#/setup）。LabLog を入れて最初に開いたときに出す。設定の画面からやり直すこともできる。
// 順番：はじめに → 日報 → 作業内容の記録 → 入退室の判定 → 研究室の Wi-Fi → 監視するフォルダ → Microsoft アカウント連携 → 完了
// 利用者の状況（PC の種類など）をこちらで決めつけず、どうするかを利用者に選んでもらう。文言は短く。
// 選んだ内容は最後にまとめて保存する（Wi-Fi・フォルダの登録と、Ollama の準備だけはその場で行う）。
// 終えるまで LabLog は何も記録しない（main.py の App.ready）。app.js の関数（api・esc・view など）を使う。
"use strict";

const setupState = { step: 0, a: null, timer: null };

// 日報の例（架空の 1 日）
const SETUP_SAMPLE_AI = `## 主な作業
- preprocess.py で外れ値の除去を追加し、前処理をやり直した（約2時間）
- 中間発表のスライドで、研究背景と手法の部分を作成（約1時間30分）

## 読んだ・調べたもの
- Attention Is All You Need`;
const SETUP_SAMPLE_RULE = `## 主な作業（自動集計）
- コーディング・解析（2時間10分）：preprocess.py
- 文書・資料作成（1時間30分）：中間発表.pptx`;

const SETUP_MATERIALS = [
  { key: "files", label: "ファイルの編集履歴", desc: "ファイルの場所・時刻・大きさだけ。中身は読みません。" },
  { key: "git", label: "Git のコミットと、まだコミットしていない変更", desc: "" },
  { key: "chrome", label: "Chrome の閲覧履歴", desc: "画面に表示したページのタイトルと URL。シークレットウィンドウは対象外です。" },
  { key: "edge", label: "Edge の閲覧履歴", desc: "画面に表示したページのタイトルと URL。InPrivate ウィンドウは対象外です。" },
  { key: "recent", label: "Windows の「最近使ったファイル」", desc: "" },
  { key: "claude", label: "Claude Code の会話の題名と作業フォルダ", desc: "会話の中身は記録しません。" },
  { key: "ocr", label: "画面の文字", desc: "一部のアプリケーション利用時、画面のスクリーンショットから文字を抽出し、作業内容を推測します。スクリーンショット画像はその場で破棄されます。テキストは日報の作成に利用されたあと、3日で削除されます。" },
];
const SETUP_APPS = [["title", "アプリ名とウィンドウ名"], ["app", "アプリ名だけ"], ["off", "記録しない"]];
// 作業内容の記録を、まとめて選ぶ（選んだあとも、下の項目を一つずつ変えられる）
const SETUP_PRESETS = [
  ["時間だけ", "作業した時間だけを記録します。",
   { apps: "off", files: false, git: false, chrome: false, edge: false, recent: false, claude: false, ocr: false }],
  ["基本", "閲覧履歴と画面の文字を除いて記録します。",
   { apps: "title", files: true, git: true, chrome: false, edge: false, recent: true, claude: true, ocr: false }],
  ["すべて", "下の項目をすべて記録します。",
   { apps: "title", files: true, git: true, chrome: true, edge: true, recent: true, claude: true, ocr: true }],
];
// 判定方法の選択肢は設定の画面と同じもの（app.js の presenceMethods）
const SETUP_PLACES = [["home", "自宅"], ["other", "その他"]];
const SETUP_REMOTE = [
  ["none", "しない", ""],
  ["rdp", "Windows のリモートデスクトップ", "接続している間は、研究室にいないとみなします。"],
  ["other", "それ以外のソフト", "Chrome リモートデスクトップ・AnyDesk・TeamViewer など。操作している間は研究室にいるとみなされます。"],
];

const SETUP_STEPS = [
  { id: "hello", title: "はじめに", render: setupHello, ok: () => true },
  { id: "report", title: "日報", render: setupReport, ok: () => true },
  { id: "record", title: "作業内容の記録", render: setupRecord,
    ok: (a) => a.apps !== null && SETUP_MATERIALS.every((m) => a[m.key] !== null) },
  { id: "method", title: "入退室の判定", render: setupMethod,
    ok: (a) => a.mode !== null && (a.mode !== "activity" || a.remote !== null) && (a.mode !== "off" || a.place !== null) },
  { id: "wifi", title: "研究室の Wi-Fi", render: setupWifi, ok: () => true, when: (a) => a.mode === "wifi" },
  { id: "folders", title: "監視するフォルダ", render: setupFolders, ok: () => true, when: (a) => a.files || a.git },
  { id: "account", title: "Microsoft アカウント連携", render: setupAccount, ok: (a) => a.cloud !== null },
  { id: "done", title: "完了", render: setupDone, ok: () => true },
];

// 答え。初めてのときは、作業内容の記録などは選んでいない状態から始める（記録してよいものを一つずつ選んでもらう）
function setupAnswers(s) {
  if (!s.setup_done) {
    return { autoReport: true, ai: false, model: null, apps: null, files: null, git: null, chrome: null, edge: null,
             recent: null, claude: null, ocr: null, mode: null, remote: null, place: null, cloud: null, reports: true };
  }
  return { autoReport: s.ollama.auto, ai: s.ollama.enabled, model: s.ollama.model,
           apps: s.record.apps, files: s.record.files, git: s.record.git, chrome: s.browsers.chrome, edge: s.browsers.edge,
           recent: s.record.recent_files, claude: s.claude_code.enabled, ocr: s.screen_text.enabled, mode: s.presence.mode,
           remote: s.presence.remote || "none", place: s.presence.place || "home", cloud: s.cloud.enabled,
           reports: s.cloud.reports !== false };
}

const setupSteps = () => SETUP_STEPS.filter((st) => !st.when || st.when(setupState.a));

async function renderSetup() {
  const { settings: s } = await loadSettings();
  if (!setupState.a) setupState.a = setupAnswers(s);
  drawSetup();
}

function drawSetup() {
  const steps = setupSteps();
  setupState.step = Math.min(setupState.step, steps.length - 1);
  const st = steps[setupState.step];
  view.innerHTML = `
    <div class="setup">
      <div class="page-head"><h1>LabLog の初期設定</h1></div>
      <ol class="setup-steps"></ol>
      <section class="card setup-card">
        <div class="card-head"><h2>${esc(st.title)}</h2><span class="spacer"></span><span class="sub" id="setup-count"></span></div>
        <div id="setup-body"></div>
        <div class="setup-nav">
          ${setupState.step ? `<button class="btn" id="setup-back">戻る</button>` : ""}
          <span class="spacer"></span>
          ${st.id === "done" ? "" : `<button class="btn btn-primary" id="setup-next">次へ</button>`}
        </div>
      </section>
    </div>`;
  const back = $("#setup-back"), next = $("#setup-next");
  if (back) back.addEventListener("click", () => { setupState.step -= 1; drawSetup(); window.scrollTo(0, 0); });
  if (next) next.addEventListener("click", () => { setupState.step += 1; drawSetup(); window.scrollTo(0, 0); });
  drawSetupBody();
}

// 選んだときは、いまのステップの中身だけを描き直す（スクロールの位置を保つ）。
// 選んだ内容で手順の数が変わる（Wi-Fi で判定するときだけ Wi-Fi の登録がある など）ので、手順の一覧も描き直す
function drawSetupBody() {
  clearTimeout(setupState.timer);
  const steps = setupSteps();
  const st = steps[setupState.step];
  const body = $("#setup-body");
  if (!body) return;
  $(".setup-steps").innerHTML = steps.map((x, i) =>
    `<li class="${i === setupState.step ? "now" : i < setupState.step ? "done" : ""}">${esc(x.title)}</li>`).join("");
  $("#setup-count").textContent = `${setupState.step + 1} / ${steps.length}`;
  st.render(body, setupState.a);
  bindSetupInputs(body, drawSetupBody);
  const next = $("#setup-next");
  if (next) next.disabled = !st.ok(setupState.a);
}

function bindSetupInputs(root, redraw) {
  $$("[data-setup-key]", root).forEach((b) => b.addEventListener("click", () => {
    setupState.a[b.dataset.setupKey] = JSON.parse(b.dataset.value);
    redraw();
  }));
  $$("[data-preset]", root).forEach((b) => b.addEventListener("click", () => {
    Object.assign(setupState.a, SETUP_PRESETS[Number(b.dataset.preset)][2]);
    redraw();
  }));
  $$("[data-setup-toggle]", root).forEach((input) => input.addEventListener("change", () => {
    setupState.a[input.dataset.setupToggle] = input.checked;
    redraw();
  }));
}

// 選択肢：choice は説明付きの大きいボタン、segment は横に並ぶ小さいボタン、toggle はオン・オフのスイッチ
function setupChoice(key, value, label, desc = "", extra = "") {
  const on = setupState.a[key] === value;
  return `<button type="button" class="choice ${on ? "on" : ""}" data-setup-key="${key}" data-value="${esc(JSON.stringify(value))}"
      aria-pressed="${on}"><b>${esc(label)}${extra}</b>${desc ? `<span>${esc(desc)}</span>` : ""}</button>`;
}
function setupSegment(key, value, label) {
  const on = setupState.a[key] === value;
  return `<button type="button" class="seg-btn ${on ? "on" : ""}" data-setup-key="${key}" data-value="${esc(JSON.stringify(value))}"
      aria-pressed="${on}">${esc(label)}</button>`;
}
function setupToggle(key, label) {
  return `<label class="switch-row"><span>${esc(label)}</span>
    <input type="checkbox" class="switch" data-setup-toggle="${key}" ${setupState.a[key] ? "checked" : ""}></label>`;
}

// ---- 各ステップ

function setupHello(el) {
  const dir = state.settings && state.settings.data_dir;
  el.innerHTML = `
    <h3 class="inner-title">LabLog でできること</h3>
    <ul class="setup-list">
      <li><b>タイムカード機能</b>：入退室時刻の記録</li>
      <li><b>作業内容の記録</b>：PC でした操作を記録</li>
      <li><b>日報機能</b>：その日の作業内容をメモ</li>
    </ul>
    <h3 class="inner-title">記録の扱い</h3>
    <ul class="help setup-list">
      <li>記録はすべて、この PC の中${dir ? `（<code>${esc(dir)}</code>）` : ""}に保存します。<br><span class="muted">※ Microsoft アカウント連携機能の利用時を除く</span></li>
      <li>AI 機能はローカル環境で処理され、外部には送信されません。</li>
    </ul>
    <h3 class="inner-title">以前の記録</h3>
    <div class="row"><span class="help">バックアップ（ZIP）があれば、記録を引き継げます。</span>${restoreButtonHTML()}</div>
    <div class="notice"><span class="ico">!</span><div>LabLog は AI で作成したアプリです。動作や安全性は保証できません。</div></div>`;
  bindRestore();
}

function setupReport(el, a) {
  el.innerHTML = `
    <div class="switch-list">
      ${setupToggle("autoReport", "日報を自動で作成する")}
      ${setupToggle("ai", "日報の作成に AI を使用する")}
    </div>
    <figure class="sample">
      <figcaption>例</figcaption>
      <div class="md note">${renderMarkdown(a.ai ? SETUP_SAMPLE_AI : SETUP_SAMPLE_RULE)}</div>
    </figure>
    <div id="ai-panel"></div>`;
  if (a.ai) loadSetupAI();
}

const modelReady = (o, name) => (o.models || []).some((m) => m === name || m.split(":")[0] === name);

async function loadSetupAI() {
  const o = await api.get("/api/ollama").catch((e) => ({ error: e.message }));
  const el = $("#ai-panel");
  if (!el) return;
  const a = setupState.a;
  if (o.error) {
    el.innerHTML = `<p class="help"><span class="tag warn">確認できませんでした</span> ${esc(o.error)}</p>`;
    return;
  }
  if (!a.model || !o.catalog.some((m) => m.name === a.model)) a.model = o.recommended || o.catalog[o.catalog.length - 1].name;
  const job = o.job || {};
  const model = o.catalog.find((m) => m.name === a.model);
  const size = model ? `約 ${model.size_gb} GB` : "";
  const ready = o.running && modelReady(o, a.model);
  const models = o.catalog.map((m) => setupChoice("model", m.name, m.name, `${m.label}・${`約 ${m.size_gb} GB`}`,
    m.name === o.recommended ? ` <span class="tag">おすすめ</span>` : "")).join("");

  let action = "";
  if (job.state === "running") {
    const pct = job.progress == null ? null : Math.round(job.progress * 100);
    action = `<p class="help">${esc(job.message || "")}${pct == null ? "" : `（${pct}%）`}</p>
      <div class="progress ${pct == null ? "busy" : ""}"><i style="width:${pct ?? 0}%"></i></div>`;
  } else if (ready) {
    action = `<p><span class="tag">準備完了</span></p>`;
  } else {
    const error = job.state === "error" ? `<p class="help"><span class="tag warn">失敗しました</span> ${esc(job.message)}</p>` : "";
    if (o.installed) {
      action = `${error}<button class="btn btn-primary" id="ai-start" data-install="false">モデルをダウンロード（${size}）</button>`;
    } else if (o.winget) {
      action = `${error}<button class="btn btn-primary" id="ai-start" data-install="true">Ollama とモデルをインストール（${size}）</button>`;
    } else {
      action = `${error}<p class="help"><a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">Ollama</a> をインストールしてから、再確認してください。</p>
        <button class="btn" id="ai-recheck">再確認</button>`;
    }
  }
  el.innerHTML = `
    <h3 class="inner-title">AI のモデル</h3>
    ${o.memory_gb ? `<p class="help">この PC のメモリ：${o.memory_gb} GB</p>` : ""}
    <div class="choices">${models}</div>
    ${action}`;
  bindSetupInputs(el, loadSetupAI);
  const start = $("#ai-start");
  if (start) start.addEventListener("click", async () => {
    const install = start.dataset.install === "true";
    if (!confirm(`${install ? "Ollama（MIT ライセンス）と" : ""}モデル ${a.model}（${size}）をインストールします。よろしいですか？`)) return;
    start.disabled = true;
    try {
      await api.post("/api/ollama/setup", { install, model: a.model });
    } catch (err) { alert(err.message); }
    loadSetupAI();
  });
  const recheck = $("#ai-recheck");
  if (recheck) recheck.addEventListener("click", loadSetupAI);
  if (job.state === "running") setupState.timer = setTimeout(loadSetupAI, 1500);
}

function setupRecord(el, a) {
  const row = (label, desc, buttons) => `
    <div class="setup-item">
      <div class="setup-item-text"><b>${esc(label)}</b>${desc ? `<span class="help">${esc(desc)}</span>` : ""}</div>
      <div class="seg">${buttons}</div>
    </div>`;
  const presets = SETUP_PRESETS.map(([label, desc, p], i) => {
    const on = Object.entries(p).every(([k, v]) => a[k] === v);
    return `<button type="button" class="choice ${on ? "on" : ""}" data-preset="${i}" aria-pressed="${on}">
      <b>${esc(label)}</b><span>${esc(desc)}</span></button>`;
  }).join("");
  el.innerHTML = `
    <p>PC での作業内容を記録できます。${a.autoReport ? "記録をもとに日報が自動作成されます。" : ""}</p>
    <p class="help">組み合わせを選ぶと、下の項目がまとめて切り替わります。</p>
    <div class="choices presets">${presets}</div>
    ${row("使ったアプリ", "アプリの名前とウィンドウ名", SETUP_APPS.map(([v, l]) => setupSegment("apps", v, l)).join(""))}
    ${SETUP_MATERIALS.map((m) => row(m.label, m.desc, setupSegment(m.key, true, "記録する") + setupSegment(m.key, false, "しない"))).join("")}`;
}

function setupMethod(el, a) {
  el.innerHTML = `
    <p>研究室への入室/退室時刻の記録方法を選択してください。</p>
    <div class="choices">${presenceMethods.map(([v, l, d]) => setupChoice("mode", v, l, d)).join("")}</div>
    ${a.mode === "activity" ? `
      <h3 class="inner-title">自宅などから、この PC にリモートで接続して作業しますか？</h3>
      <div class="choices">${SETUP_REMOTE.map(([v, l, d]) => setupChoice("remote", v, l, d)).join("")}</div>` : ""}
    ${a.mode === "off" ? `
      <h3 class="inner-title">この PC での作業時間を、どこでの作業として数えますか？</h3>
      <div class="choices">${SETUP_PLACES.map(([v, l]) => setupChoice("place", v, l)).join("")}</div>` : ""}`;
}

async function setupWifi(el) {
  const s = state.settings.settings;
  const ssids = (state.status && state.status.ssids) || [];
  const current = ssids[0];
  const where = !current ? "未接続" : s.wifi.univ.includes(current) ? "研究室として登録済み"
    : s.wifi.home.includes(current) ? "自宅として登録済み" : s.wifi.ignore.includes(current) ? "判定に使わない" : "未登録";
  const list = (kind) => s.wifi[kind].length ? s.wifi[kind].map(esc).join("、") : "なし";
  el.innerHTML = `
    <p>研究室の Wi-Fi に接続して「研究室として登録」を押してください。研究室専用と大学共通（ScienceTokyo など）の両方を登録します。</p>
    <p class="help">eduroam・Wi2 は登録しないでください。</p>
    <p>接続中の Wi-Fi：${state.status && state.status.wifi_error && !current ? "<b>読み取れません</b>"
      : `<b>${esc(current || "なし")}</b>（${where}）`}</p>
    ${state.status && state.status.wifi_error ? `<p class="help">${esc(state.status.wifi_error)}
      <a href="ms-settings:privacy-location">位置情報の設定を開く</a></p>` : ""}
    <div class="row">
      <button class="btn btn-primary" data-wifi="univ" ${current ? "" : "disabled"}>研究室として登録</button>
      <button class="btn" data-wifi="home" ${current ? "" : "disabled"}>自宅として登録</button>
      <button class="btn" data-wifi="ignore" ${current ? "" : "disabled"}>判定に使わない</button>
    </div>
    <ul class="help setup-list">
      <li>研究室：${list("univ")}</li><li>自宅：${list("home")}</li><li>判定に使わない：${list("ignore")}</li>
    </ul>`;
  $$("[data-wifi]", el).forEach((b) => b.addEventListener("click", async () => {
    try {
      await api.post("/api/wifi/register", { kind: b.dataset.wifi });
      await loadSettings();
      flash("登録しました");
      drawSetupBody();
    } catch (err) { alert(err.message); }
  }));
  // Wi-Fi をつなぎ替えたときのために、少しずつ確かめ直す
  setupState.timer = setTimeout(async () => { await refreshStatus(); drawSetupBody(); }, 5000);
}

function setupFolders(el) {
  const s = state.settings.settings;
  el.innerHTML = `
    <p>研究のファイルや Git リポジトリがあるフォルダを登録してください。</p>
    <div class="set-list">${s.watch_folders.length ? s.watch_folders.map((f) => `
      <div class="set-item"><span class="main">${esc(f)}</span>
      <button class="icon-btn" data-folder-remove="${esc(f)}" aria-label="削除">✕</button></div>`).join("") : `<div class="empty">まだありません</div>`}</div>
    <div class="row"><button class="btn btn-primary" id="setup-folder-pick">フォルダを選んで追加…</button></div>
    <form class="row" id="setup-folder-form" style="margin-top:8px">
      <input type="text" name="path" placeholder="または、フォルダのパスを貼り付け" style="flex:1;min-width:200px">
      <button class="btn" type="submit">追加</button>
    </form>`;
  const added = async (path) => {
    await api.post("/api/folders/add", { path });
    await loadSettings();
    flash("フォルダを追加しました");
    drawSetupBody();
  };
  $("#setup-folder-pick").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "フォルダ選択画面を開いています…";
    try {
      const { path } = await api.post("/api/folders/pick");
      if (path) await added(path);
    } catch (err) { alert(err.message); }
    finally { if (btn.isConnected) { btn.disabled = false; btn.textContent = "フォルダを選んで追加…"; } }
  });
  $("#setup-folder-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await added(e.target.path.value); } catch (err) { alert(err.message); }
  });
  $$("[data-folder-remove]", el).forEach((b) => b.addEventListener("click", async () => {
    await api.post("/api/folders/remove", { path: b.dataset.folderRemove });
    await loadSettings();
    drawSetupBody();
  }));
}

async function setupAccount(el, a) {
  const c = await api.get("/api/cloud").catch(() => ({}));
  if (!el.isConnected) return;
  const url = c.viewer_url || "";
  el.innerHTML = `
    <p>アカウント連携すると、入退室履歴をスマホからも確認できます。</p>
    <p class="help">${c.account ? `この PC の OneDrive（<b>${esc(c.account)}</b>）と同じアカウントで、スマホからサインインします。`
      : "この PC の OneDrive に、個人の Microsoft アカウントでサインインしておく必要があります（大学のアカウントは不可）。"}</p>
    <div class="choices">
      ${setupChoice("cloud", true, "連携する")}
      ${setupChoice("cloud", false, "連携しない")}
    </div>
    ${a.cloud ? `<div class="switch-list">${setupToggle("reports", "日報もスマホに送る")}</div>
      <p class="help">日報には、ファイル名などが入ることがあります。</p>` : ""}
    ${a.cloud && url ? viewerLinkHTML(url) : ""}`;
  bindSetupInputs(el, drawSetupBody);
}

function setupDone(el, a) {
  const s = state.settings.settings;
  el.innerHTML = `
    <p>設定が完了しました。</p>
    <div class="row"><span class="spacer"></span>
      <button class="btn btn-primary" id="setup-finish">${s.setup_done ? "設定を保存" : "記録を始める"}</button></div>`;
  $("#setup-finish").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const { settings: cur } = await loadSettings(); // AI の準備でモデルが変わっていることがあるので読み直す
    if (!a.cloud && await isLinked() && !confirm(UNLINK_CONFIRM)) { btn.disabled = false; return; }
    const ok = await saveSettings({
      setup_done: true,
      record: { apps: a.apps, files: a.files, git: a.git, recent_files: a.recent },
      browsers: { chrome: a.chrome, edge: a.edge },
      claude_code: { enabled: a.claude },
      screen_text: { ...cur.screen_text, enabled: a.ocr },
      ollama: { ...cur.ollama, auto: a.autoReport, enabled: a.ai, model: a.ai && a.model ? a.model : cur.ollama.model },
      presence: { ...cur.presence, mode: a.mode, remote: a.mode === "activity" ? a.remote : "none",
                  place: a.mode === "off" ? a.place : cur.presence.place },
      cloud: { ...cur.cloud, enabled: a.cloud, reports: a.reports },
    }, cur.setup_done ? "設定を保存しました" : "記録を始めました");
    if (!ok) { btn.disabled = false; return; }
    setupState.a = null;
    setupState.step = 0;
    refreshStatus();
    location.hash = "#/day";
  });
}
