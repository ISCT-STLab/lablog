// 初期設定の画面（#/setup）。LabLog を入れて最初に開いたときに出す。設定の画面からやり直すこともできる。
// 順番：はじめに → 日報 → 記録する材料 → 入退室の判定 → 研究室の Wi-Fi → 監視するフォルダ → スマホで見る → 完了
// 利用者の状況（PC の種類など）をこちらで決めつけず、どうするかを利用者に選んでもらう。
// 選んだ内容は最後にまとめて保存する（Wi-Fi・フォルダの登録と、Ollama の準備だけはその場で行う）。
// 終えるまで LabLog は何も記録しない（main.py の App.ready）。app.js の関数（api・esc・view など）を使う。
"use strict";

const setupState = { step: 0, a: null, timer: null };

// 日報の見本（架空の 1 日）
const SETUP_SAMPLE_AI = `## 主な作業
- preprocess.py で外れ値の除去を追加し、前処理をやり直した（約2時間）
- 中間発表のスライドで、研究背景と手法の部分を作成（約1時間30分）
- 先行研究の比較表を Overleaf で更新（約40分）

## 読んだ・調べたもの
- Attention Is All You Need
- scikit-learn の IsolationForest の説明`;
const SETUP_SAMPLE_RULE = `## 主な作業（自動集計）
- コーディング・解析（2時間10分）：preprocess.py
- 文書・資料作成（1時間30分）：中間発表.pptx

## コミット
- [research] 外れ値の除去を追加`;

const SETUP_MATERIALS = [
  { key: "files", label: "監視するフォルダで編集したファイル", desc: "ファイルの場所・時刻・大きさだけ。中身は読みません。" },
  { key: "git", label: "Git のコミットと、まだコミットしていない変更", desc: "監視するフォルダの中のリポジトリ。コミットのメッセージを日報に使います。" },
  { key: "chrome", label: "Chrome の閲覧履歴", desc: "画面に表示したページのタイトルと URL。シークレットウィンドウは対象外です。" },
  { key: "edge", label: "Edge の閲覧履歴", desc: "画面に表示したページのタイトルと URL。InPrivate ウィンドウは対象外です。" },
  { key: "recent", label: "Windows の「最近使ったファイル」", desc: "監視するフォルダの外で開いた文書などの名前。" },
  { key: "claude", label: "Claude Code の会話の題名と作業フォルダ", desc: "ウィンドウ名が「Claude」だけのときに補います。会話の中身は記録しません。" },
  { key: "ocr", label: "画面の文字", desc: "Claude・ChatGPT・Teams・Zoom・ターミナルなど、ウィンドウ名から作業が分からないアプリが前面にあるときだけ、5 分に 1 回そのウィンドウの文字を読み取ります。画像は保存せず、文字は 3 日で消します。スマホには送りません。" },
];
const SETUP_APPS = [["title", "アプリ名とウィンドウ名"], ["app", "アプリ名だけ"], ["off", "記録しない"]];
// 判定方法の選択肢は設定の画面と同じもの（app.js の presenceMethods）
const SETUP_PLACES = [["home", "自宅", ""], ["other", "その他", "場所を分けずに数えます"]];
const SETUP_REMOTE = [
  ["none", "しない", ""],
  ["rdp", "Windows のリモートデスクトップ", "操作している間は研究室にいないとみなし、作業時間は「自宅」として数えます。"],
  ["other", "それ以外のソフト", "Chrome リモートデスクトップ・AnyDesk・TeamViewer など。今の版では手元の操作と見分けられないため、操作している間は研究室にいるとみなされます。その日の入退室は、日別の画面で直してください。"],
];

const SETUP_STEPS = [
  { id: "hello", title: "はじめに", render: setupHello, ok: () => true },
  { id: "report", title: "日報", render: setupReport, ok: (a) => a.report === "manual" || (a.report === "auto" && a.ai !== null) },
  { id: "record", title: "記録する材料", render: setupRecord,
    ok: (a) => a.apps !== null && SETUP_MATERIALS.every((m) => a[m.key] !== null) },
  { id: "method", title: "入退室の判定", render: setupMethod,
    ok: (a) => a.mode !== null && (a.mode !== "activity" || a.remote !== null) && (a.mode !== "off" || a.place !== null) },
  { id: "wifi", title: "研究室の Wi-Fi", render: setupWifi, ok: () => true, when: (a) => a.mode === "wifi" },
  { id: "folders", title: "監視するフォルダ", render: setupFolders, ok: () => true, when: (a) => a.files || a.git },
  { id: "phone", title: "スマホで見る", render: setupPhone, ok: (a) => a.cloud !== null },
  { id: "done", title: "完了", render: setupDone, ok: () => true },
];

// 答え。初めてのときは、どれも選んでいない状態から始める（記録してよいものを一つずつ選んでもらう）
function setupAnswers(s) {
  if (!s.setup_done) {
    return { report: null, ai: null, model: null, apps: null, files: null, git: null, chrome: null, edge: null,
             recent: null, claude: null, ocr: null, mode: null, remote: null, place: null, cloud: null };
  }
  return { report: s.ollama.auto ? "auto" : "manual", ai: s.ollama.auto ? s.ollama.enabled : null, model: s.ollama.model,
           apps: s.record.apps, files: s.record.files, git: s.record.git, chrome: s.browsers.chrome, edge: s.browsers.edge,
           recent: s.record.recent_files, claude: s.claude_code.enabled, ocr: s.screen_text.enabled, mode: s.presence.mode,
           remote: s.presence.remote || "none", place: s.presence.place || "home", cloud: s.cloud.enabled };
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
          <span class="help" id="setup-hint"></span>
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
// 選んだ内容で手順の数が変わる（持ち帰る PC だけ Wi-Fi の登録がある など）ので、手順の一覧も描き直す
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
  $$("[data-setup-key]", body).forEach((b) => b.addEventListener("click", () => {
    setupState.a[b.dataset.setupKey] = JSON.parse(b.dataset.value);
    drawSetupBody();
  }));
  const next = $("#setup-next");
  if (next) {
    const ok = st.ok(setupState.a);
    next.disabled = !ok;
    $("#setup-hint").textContent = ok ? "" : "すべて選ぶと、次へ進めます";
  }
}

// 選択肢：choice は説明付きの大きいボタン、segment は横に並ぶ小さいボタン
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

// ---- 各ステップ

function setupHello(el) {
  const dir = state.settings && state.settings.data_dir;
  el.innerHTML = `
    <h3 class="inner-title">LabLog でできること</h3>
    <ul class="setup-list setup-features">
      <li><b>研究室の入退室を記録する</b>：研究室に来た時刻・帰った時刻と、滞在時間を記録します。判定のしかた（Wi-Fi・PC の操作・手動）は、このあと選べます。</li>
      <li><b>作業内容を記録する</b>：PC でどのアプリやファイルを使ったか、どのページを見たかなどを記録し、作業の分類ごとの時間やタイムラインで振り返れます。</li>
      <li><b>日報を残す</b>：その日の記録から日報の下書きを自動で作れます。自分で書くこともできます。</li>
      <li><b>スマホで見る</b>：同じ画面を、スマホのブラウザでも見られます（使う場合だけ）。</li>
    </ul>
    <h3 class="inner-title">記録の扱い</h3>
    <ul class="help setup-list">
      <li>記録はすべて、この PC の中${dir ? `（<code>${esc(dir)}</code>）` : ""}に保存します。LabLog を配った人や先生を含め、誰にも送りません。</li>
      <li>日報に AI を使う場合も、AI はこの PC の中で動きます。</li>
      <li>スマホで見る場合だけ、要約（入退室・作業時間・日報）を自分の OneDrive に置きます。</li>
      <li>何を記録するかは、このあと一つずつ選べます。あとから設定の画面でいつでも変えられ、上部の ■ で一時停止もできます。</li>
    </ul>
    <p class="help">この設定を終えるまでは、何も記録しません。</p>`;
}

function setupReport(el, a) {
  el.innerHTML = `
    <p>日報のつけ方を選んでください。</p>
    <div class="choices">
      ${setupChoice("report", "auto", "自動で下書きを作る", "退室したときと日付が変わったときに、その日の記録から日報の下書きを作ります。下書きは、あとから書き直せます")}
      ${setupChoice("report", "manual", "自分で書く", "自動では作りません。日別の画面で、自分で書きます")}
    </div>
    ${a.report === "auto" ? `
      <h3 class="inner-title">下書きの作り方</h3>
      <div class="setup-samples">
        <div><h3 class="inner-title">AI を使う場合の例</h3><div class="md note">${renderMarkdown(SETUP_SAMPLE_AI)}</div></div>
        <div><h3 class="inner-title">AI を使わない場合の例</h3><div class="md note">${renderMarkdown(SETUP_SAMPLE_RULE)}</div></div>
      </div>
      <p class="help">AI は Ollama というアプリを使って、この PC の中で動かします。記録が PC の外に送られることはありません。1 日分の日報を作るのに 1 分ほどかかります。</p>
      <div class="choices">
        ${setupChoice("ai", true, "AI を使う", "記録を読んで、文章にまとめます。このあと、AI の準備（Ollama とモデル）ができているかを確かめます")}
        ${setupChoice("ai", false, "AI を使わない", "集計から箇条書きの下書きを作ります。あとから設定の画面で使い始めることもできます")}
      </div>
      <div id="ai-panel"></div>` : ""}`;
  if (a.report === "auto" && a.ai) loadSetupAI();
}

const modelReady = (o, name) => (o.models || []).some((m) => m === name || m.split(":")[0] === name);

async function loadSetupAI() {
  const o = await api.get("/api/ollama").catch((e) => ({ error: e.message }));
  const el = $("#ai-panel");
  if (!el) return;
  const a = setupState.a;
  if (o.error) {
    el.innerHTML = `<p class="help"><span class="tag warn">確かめられませんでした</span> ${esc(o.error)}</p>`;
    return;
  }
  if (!a.model || !o.catalog.some((m) => m.name === a.model)) a.model = o.recommended || o.catalog[o.catalog.length - 1].name;
  const job = o.job || {};
  const model = o.catalog.find((m) => m.name === a.model);
  const ready = o.running && modelReady(o, a.model);
  const memory = o.memory_gb ? `この PC のメモリは ${o.memory_gb} GB です。` : "";
  const advice = o.recommended ? `おすすめは ${o.recommended} です。`
    : "メモリが少ないため、AI の日報は動作が重くなるかもしれません（8 GB 以上をおすすめします）。";
  const models = o.catalog.map((m) => setupChoice("model", m.name, m.name,
    `${m.label}・ダウンロード約 ${m.size_gb} GB・メモリ ${m.memory_gb} GB 以上`,
    m.name === o.recommended ? ` <span class="tag">おすすめ</span>` : "")).join("");

  let action = "";
  if (job.state === "running") {
    const pct = job.progress == null ? null : Math.round(job.progress * 100);
    action = `<p class="help">${esc(job.message || "")}${pct == null ? "" : `（${pct}%）`}</p>
      <div class="progress ${pct == null ? "busy" : ""}"><i style="width:${pct ?? 0}%"></i></div>
      <p class="help">先のステップへ進んでも、準備は続きます。</p>`;
  } else if (ready) {
    action = `<p><span class="tag">準備完了</span> ${esc(a.model)} で日報を作ります。</p>`;
  } else {
    const size = model ? `約 ${model.size_gb} GB` : "";
    const error = job.state === "error" ? `<p class="help"><span class="tag warn">うまくいきませんでした</span> ${esc(job.message)}</p>` : "";
    if (o.installed) {
      action = `${error}<p class="help">Ollama は入っています。日報に使うモデル（${esc(a.model)}）をダウンロードします。</p>
        <button class="btn btn-primary" id="ai-start" data-install="false">モデルをダウンロード（${size}）</button>`;
    } else if (o.winget) {
      action = `${error}<p class="help">Ollama がまだ入っていません。ボタンを押すと、Ollama のインストールとモデルのダウンロードをまとめて行います（数分〜数十分）。</p>
        <button class="btn btn-primary" id="ai-start" data-install="true">Ollama とモデルを入れる（${size}）</button>`;
    } else {
      action = `${error}<p class="help">Ollama がまだ入っていません。<a href="https://ollama.com/download" target="_blank" rel="noopener noreferrer">ollama.com</a> から入れてから、「もう一度確かめる」を押してください。</p>
        <button class="btn" id="ai-recheck">もう一度確かめる</button>`;
    }
  }
  el.innerHTML = `
    <h3 class="inner-title">AI の準備</h3>
    <p class="help">${memory}${advice}</p>
    <div class="choices">${models}</div>
    ${action}`;
  $$("[data-setup-key]", el).forEach((b) => b.addEventListener("click", () => {
    setupState.a[b.dataset.setupKey] = JSON.parse(b.dataset.value);
    loadSetupAI();
  }));
  const start = $("#ai-start");
  if (start) start.addEventListener("click", async () => {
    const install = start.dataset.install === "true";
    const what = install ? `Ollama（ローカル AI を動かすアプリ。MIT ライセンス）を Windows の winget でインストールし、モデル ${a.model}（${model ? `約 ${model.size_gb} GB` : ""}）を Ollama のサイトからダウンロードします。`
      : `モデル ${a.model}（${model ? `約 ${model.size_gb} GB` : ""}）を Ollama のサイトからダウンロードします。`;
    if (!confirm(`${what}よろしいですか？`)) return;
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
      <div class="setup-item-text"><b>${esc(label)}</b><span class="help">${esc(desc)}</span></div>
      <div class="seg">${buttons}</div>
    </div>`;
  el.innerHTML = `
    <p>記録するものを、一つずつ選んでください。${a.report === "auto"
      ? `記録したものは日報の下書きの材料になり、多いほど下書きが具体的になります${a.ai ? "（AI を使う場合は特に）" : ""}。`
      : "記録したものは日別の画面で振り返れ、日報を書くときの手がかりになります。"}
      どれもこの PC の中だけに保存し、あとから設定の画面で変えられます。</p>
    ${row("使ったアプリとウィンドウ", "前面にあるアプリの名前と、ウィンドウ名（開いている文書やページの名前）。どれを選んでも、PC作業時間は記録します。",
      SETUP_APPS.map(([v, l]) => setupSegment("apps", v, l)).join(""))}
    ${SETUP_MATERIALS.map((m) => row(m.label, m.desc, setupSegment(m.key, true, "記録する") + setupSegment(m.key, false, "しない"))).join("")}`;
}

function setupMethod(el, a) {
  const hours = state.settings.settings.leave_after_hours;
  el.innerHTML = `
    <p>研究室に来た時刻・帰った時刻（入退室）を、どうやって記録するかを選んでください。</p>
    <div class="choices">${presenceMethods(hours).map(([v, l, d]) => setupChoice("mode", v, l, d)).join("")}</div>
    ${a.mode === "wifi" ? `<p class="help">次の手順で、研究室の Wi-Fi を登録します。</p>` : ""}
    ${a.mode === "activity" ? `
      <h3 class="inner-title">自宅などから、この PC にリモートで接続して作業しますか？</h3>
      <div class="choices">${SETUP_REMOTE.map(([v, l, d]) => setupChoice("remote", v, l, d)).join("")}</div>` : ""}
    ${a.mode === "off" ? `
      <h3 class="inner-title">この PC での作業時間を、どこでの作業として数えますか？</h3>
      <div class="choices">${SETUP_PLACES.map(([v, l, d]) => setupChoice("place", v, l, d)).join("")}</div>` : ""}
    <p class="help">どの方法でも、Windows のリモートデスクトップで外から操作している間は研究室にいないとみなし、作業時間は「自宅」として数えます。
      「記録しない」以外では、日別の画面の「入室する」「退室する」で手動でも切り替えられます。あとから設定の画面で変えられます。</p>`;
}

async function setupWifi(el) {
  const s = state.settings.settings;
  const ssids = (state.status && state.status.ssids) || [];
  const current = ssids[0];
  const where = !current ? "未接続" : s.wifi.univ.includes(current) ? "研究室として登録済み"
    : s.wifi.home.includes(current) ? "自宅として登録済み" : s.wifi.ignore.includes(current) ? "判定に使わない Wi-Fi" : "未登録";
  const list = (kind) => s.wifi[kind].length ? s.wifi[kind].map(esc).join("、") : "なし";
  el.innerHTML = `
    <p>研究室にいるときにつながっている Wi-Fi を「研究室」として登録すると、入退室を自動で判定できます。
      研究室専用の Wi-Fi と、大学共通の Wi-Fi（ScienceTokyo など）は、どちらも「研究室」にします。</p>
    <p class="help">eduroam や Wi2 は他の場所にもあるので、登録しないでください。スマホのテザリングなど、研究室でも外でも使う Wi-Fi は「判定に使わない」にします。登録していない Wi-Fi は「大学の外」とみなします。</p>
    <p>いまつながっている Wi-Fi：<b>${esc(current || "なし")}</b>（${where}）</p>
    <div class="row">
      <button class="btn btn-primary" data-wifi="univ" ${current ? "" : "disabled"}>研究室として登録</button>
      <button class="btn" data-wifi="home" ${current ? "" : "disabled"}>自宅として登録</button>
      <button class="btn" data-wifi="ignore" ${current ? "" : "disabled"}>判定に使わない</button>
    </div>
    <ul class="help setup-list">
      <li>研究室：${list("univ")}</li><li>自宅：${list("home")}</li><li>判定に使わない：${list("ignore")}</li>
    </ul>
    <p class="help">いま研究室にいなければ、次に研究室に来たときに、設定の画面の「Wi-Fi の登録」から登録できます。</p>`;
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
    <p>研究のファイルや Git リポジトリを置いているフォルダを登録すると、そこで編集したファイルや Git のコミットを記録します。中にある Git リポジトリは自動で見つけます。</p>
    <div class="set-list">${s.watch_folders.length ? s.watch_folders.map((f) => `
      <div class="set-item"><span class="main">${esc(f)}</span>
      <button class="icon-btn" data-folder-remove="${esc(f)}" aria-label="削除">✕</button></div>`).join("") : `<div class="empty">まだありません</div>`}</div>
    <div class="row"><button class="btn btn-primary" id="setup-folder-pick">フォルダを選んで追加…</button></div>
    <form class="row" id="setup-folder-form" style="margin-top:8px">
      <input type="text" name="path" placeholder="または、フォルダのパスを貼り付け" style="flex:1;min-width:200px">
      <button class="btn" type="submit">追加</button>
    </form>
    <p class="help">あとから設定の画面でも追加できます。</p>`;
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

async function setupPhone(el, a) {
  const c = await api.get("/api/cloud").catch(() => ({}));
  if (!el.isConnected) return;
  const url = c.viewer_url || "";
  el.innerHTML = `
    <p>PC と同じ画面を、スマホのブラウザで見られます。日報を書き直したり、入退室を直したりもできます。</p>
    <ul class="help setup-list">
      <li>スマホに届くのは要約だけです（入退室・作業時間・分類別の時間・日報）。ウィンドウ名・閲覧したページ・ファイルの場所は、この PC から出しません。</li>
      <li>要約は、あなたの OneDrive の専用フォルダ（アプリ/LabLog）を通して届きます。</li>
      <li><b>個人の Microsoft アカウント</b>と、この PC で同期している<b>個人用の OneDrive</b> が必要です。大学の Microsoft アカウントでは使えません。</li>
    </ul>
    <div class="choices">
      ${setupChoice("cloud", true, "スマホで見る", "OneDrive に要約を書き出します")}
      ${setupChoice("cloud", false, "使わない", "あとから設定の画面で始めることもできます")}
    </div>
    ${a.cloud ? `
      <h3 class="inner-title">スマホでの始め方</h3>
      <ol class="help setup-list">
        <li>スマホのブラウザで ${url ? `<a href="${safeHref(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>` : "ビューアー"} を開きます。</li>
        <li>個人の Microsoft アカウントでサインインします。</li>
        <li>共有ボタン（Android はメニュー）から「ホーム画面に追加」すると、アプリのように開けます。</li>
      </ol>
      <p class="help">サインインすると数分で、この PC が書き出しを始めます。</p>` : ""}`;
  $$("[data-setup-key]", el).forEach((b) => b.addEventListener("click", () => {
    setupState.a[b.dataset.setupKey] = JSON.parse(b.dataset.value);
    drawSetupBody();
  }));
}

function setupDone(el, a) {
  const s = state.settings.settings;
  const on = SETUP_MATERIALS.filter((m) => a[m.key]).map((m) => m.label);
  const off = SETUP_MATERIALS.filter((m) => !a[m.key]).map((m) => m.label);
  const apps = Object.fromEntries(SETUP_APPS)[a.apps];
  const method = presenceMethods(s.leave_after_hours).find(([v]) => v === a.mode);
  const report = a.report === "manual" ? "自分で書く（自動では作らない）"
    : `自動で下書きを作る（${a.ai ? `AI：${esc(a.model || s.ollama.model)}` : "集計から"}）`;
  el.innerHTML = `
    <p>${s.setup_done ? "「設定を保存」を押すと、選んだ内容で記録を続けます。" : "設定はこれで終わりです。「記録を始める」を押すと、記録を始めます。"}</p>
    <ul class="help setup-list">
      <li>日報：${report}</li>
      <li>使ったアプリとウィンドウ：${esc(apps)}</li>
      <li>記録する：${on.length ? on.map(esc).join("、") : "なし"}</li>
      ${off.length ? `<li>記録しない：${off.map(esc).join("、")}</li>` : ""}
      <li>入退室の判定：${esc(method ? method[1] : "")}${a.mode === "off" ? `（PC作業時間は「${a.place === "home" ? "自宅" : "その他"}」として数える）` : ""}</li>
      <li>スマホで見る：${a.cloud ? "使う" : "使わない"}</li>
    </ul>
    <p class="help">どれも、あとから設定の画面で変えられます。設定の画面の「初期設定をやり直す」で、この画面をもう一度開けます。</p>
    <div class="row"><span class="spacer"></span>
      <button class="btn btn-primary" id="setup-finish">${s.setup_done ? "設定を保存" : "記録を始める"}</button></div>`;
  $("#setup-finish").addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    const { settings: cur } = await loadSettings(); // AI の準備でモデルが変わっていることがあるので読み直す
    const useAI = a.report === "auto" && !!a.ai;
    const ok = await saveSettings({
      setup_done: true,
      record: { apps: a.apps, files: a.files, git: a.git, recent_files: a.recent },
      browsers: { chrome: a.chrome, edge: a.edge },
      claude_code: { enabled: a.claude },
      screen_text: { ...cur.screen_text, enabled: a.ocr },
      ollama: { ...cur.ollama, auto: a.report === "auto", enabled: useAI, model: useAI && a.model ? a.model : cur.ollama.model },
      presence: { ...cur.presence, mode: a.mode, remote: a.mode === "activity" ? a.remote : "none",
                  place: a.mode === "off" ? a.place : cur.presence.place },
      cloud: { ...cur.cloud, enabled: a.cloud },
    }, cur.setup_done ? "設定を保存しました" : "記録を始めました");
    if (!ok) { e.currentTarget.disabled = false; return; }
    setupState.a = null;
    setupState.step = 0;
    refreshStatus();
    location.hash = "#/day";
  });
}
