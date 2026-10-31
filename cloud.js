// スマホ用ビューアー：PC の LabLog が OneDrive に書き出した要約を読み、
// 日報と滞在の修正を OneDrive に書き戻す（PC の LabLog が次に動いたときに取り込む）。
// PC のダッシュボードと同じ画面（app.js）を使うため、/api/... の呼び出しをここで OneDrive の読み書きに置き換える。
// Microsoft へのサインインは PKCE 方式。パスワードはこのページでは扱わず、外部のライブラリも使わない。
"use strict";

window.LabLogCloud = (() => {
  const cfg = window.LABLOG_CLOUD;
  if (!cfg) return null;

  const AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0";
  // アプリ専用フォルダ（OneDrive/アプリ/LabLog）だけを読み書きする。ほかのファイルには触れない。
  const GRAPH = "https://graph.microsoft.com/v1.0/me/drive/special/approot";
  const SCOPE = "Files.ReadWrite.AppFolder offline_access";
  const HERE = location.origin + location.pathname;
  const KEY = { token: "lablog.token", login: "lablog.login", silent: "lablog.silent", pending: "lablog.pending",
                rejected: "lablog.rejected",
                cache: "lablog.cache:", cacheIndex: "lablog.cache-index", marker: "lablog.marker",
                unlinkAck: "lablog.unlink-ack" };
  const MARKER = { app: "LabLog viewer" }; // PC の LabLog はこの中身の viewer.json を目印に書き出し先を探す
  const CACHE_SEC = 60;
  const CACHE_FILES = 80;
  const DAY_START_HOUR = 5;
  const WEEKDAYS = "日月火水木金土";
  const state = { offline: false, pendingChecked: 0, linkChecked: 0, linkError: "" };

  class SignInRequired extends Error {
    constructor(msg) { super(msg); this.name = "SignInRequired"; }
  }

  // ---- 端末への保存（読めない・書けないときも動くようにする）

  const load = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } };
  const save = (k, v) => {
    try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch { /* 容量不足など */ }
  };

  // ---- サインイン

  const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const randomId = (n) => b64url(crypto.getRandomValues(new Uint8Array(n)));
  let leaving = false;

  async function signIn({ silent = false } = {}) {
    if (!leaving) {
      leaving = true;
      const verifier = randomId(48), st = randomId(16);
      const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
      save(KEY.login, { verifier, state: st, hash: location.hash, silent });
      const q = new URLSearchParams({
        client_id: cfg.clientId, response_type: "code", response_mode: "query", redirect_uri: HERE,
        scope: SCOPE, state: st, code_challenge: challenge, code_challenge_method: "S256",
      });
      if (silent) q.set("prompt", "none");
      location.assign(`${AUTH}/authorize?${q}`);
    }
    return new Promise(() => {}); // ページを移動するまで待つ
  }

  async function tokenRequest(params) {
    const res = await fetch(`${AUTH}/token`, {
      method: "POST", body: new URLSearchParams({ client_id: cfg.clientId, scope: SCOPE, ...params }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error((data.error_description || data.error || res.statusText).split("\n")[0]);
      err.auth = true;
      throw err;
    }
    const prev = load(KEY.token) || {};
    const tok = { access: data.access_token, exp: Date.now() + (data.expires_in - 120) * 1000,
                  refresh: data.refresh_token || prev.refresh };
    save(KEY.token, tok);
    return tok;
  }

  // ボタンからサインインしたときだけ true。自動の認証更新では連携解除を取り消さない。
  async function finishSignIn() {
    const p = new URLSearchParams(location.search);
    if (!p.has("code") && !p.has("error")) return false;
    const pending = load(KEY.login);
    save(KEY.login, null);
    history.replaceState(null, "", HERE + ((pending && pending.hash) || ""));
    if (p.has("error")) {
      if (pending && pending.silent) return false; // 自動でのサインインはできなかった → ボタンを出す
      throw new Error((p.get("error_description") || p.get("error")).split("\n")[0]);
    }
    if (!pending || pending.state !== p.get("state")) {
      throw new Error("サインインを確認できませんでした。もう一度お試しください。");
    }
    await tokenRequest({ grant_type: "authorization_code", code: p.get("code"),
                         code_verifier: pending.verifier, redirect_uri: HERE });
    return !pending.silent;
  }

  let refreshing = null;
  async function accessToken() {
    const tok = load(KEY.token);
    if (tok && tok.access && tok.exp > Date.now()) return tok.access;
    if (tok && tok.refresh) {
      refreshing = refreshing || tokenRequest({ grant_type: "refresh_token", refresh_token: tok.refresh })
        .finally(() => { refreshing = null; });
      try {
        return (await refreshing).access;
      } catch (e) {
        if (!e.auth) throw e; // 通信できないだけなら、保存してある内容を出す
        save(KEY.token, { refresh: null });
      }
    }
    // サインインの期限（約1日）が切れた。Microsoft にサインイン済みなら、画面は一瞬で戻ってくる
    if (tok && Date.now() - (load(KEY.silent) || 0) > 10 * 60 * 1000) {
      save(KEY.silent, Date.now());
      return signIn({ silent: true });
    }
    throw new SignInRequired("サインインが必要です");
  }

  async function graph(path, init = {}) {
    for (let attempt = 0; ; attempt++) {
      const token = await accessToken();
      const res = await fetch(GRAPH + path, {
        ...init, cache: "no-store", headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
      });
      if (res.status === 401 && attempt === 0) {
        save(KEY.token, { ...load(KEY.token), exp: 0 });
        continue;
      }
      return res;
    }
  }

  // ---- OneDrive のアプリフォルダ（開発中は PC 内のテスト用サーバー）

  async function graphError(res, what) {
    const body = await res.json().catch(() => ({}));
    const detail = body.error ? `${body.error.code}: ${body.error.message}` : res.statusText;
    return new Error(`OneDrive ${what}（${res.status} ${detail}）`);
  }

  const graphStore = {
    // アプリフォルダは、フォルダそのものを一度読んだときに OneDrive が作る（無いまま中に書くと 404 になる）
    async ensureRoot() {
      const res = await graph("");
      if (!res.ok) throw await graphError(res, "のアプリフォルダを用意できませんでした");
    },
    async read(path) {
      const res = await graph(`:/${path}`);
      if (res.status === 404) return null;
      if (!res.ok) throw await graphError(res, "から読み込めませんでした");
      // ファイルの中身は、認証なしで読める一時的な URL から取る（JavaScript からはこの方法が必要）
      const file = await fetch((await res.json())["@microsoft.graph.downloadUrl"], { cache: "no-store" });
      if (!file.ok) throw new Error(`OneDrive から読み込めませんでした（${file.status}）`);
      return file.json();
    },
    async write(path, obj) {
      const put = () => graph(`:/${path}:/content`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(obj),
      });
      let res = await put();
      if (res.status === 404) { // アプリフォルダがまだ無い
        await graphStore.ensureRoot();
        res = await put();
      }
      if (!res.ok) throw await graphError(res, "に保存できませんでした");
    },
    async list(dir) {
      const res = await graph(`:/${dir}:/children?$select=name&$top=500`);
      if (res.status === 404) return [];
      if (!res.ok) throw await graphError(res, "から読み込めませんでした");
      return (await res.json()).value.map((x) => x.name);
    },
    async remove(path) {
      const res = await graph(`:/${path}`, { method: "DELETE" });
      if (!res.ok && res.status !== 404) throw await graphError(res, "から消せませんでした");
    },
  };

  const devStore = {
    async ensureRoot() {},
    async read(path) {
      const res = await fetch(cfg.dev + path, { cache: "no-store" });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`読み込めませんでした（${res.status}）`);
      return res.json();
    },
    async write(path, obj) {
      const res = await fetch(cfg.dev + path, { method: "PUT", body: JSON.stringify(obj) });
      if (!res.ok) throw new Error(`保存できませんでした（${res.status}）`);
    },
    async list(dir) {
      const res = await fetch(`${cfg.dev}${dir}/`, { cache: "no-store" });
      return res.ok ? res.json() : [];
    },
    async remove(path) {
      await fetch(cfg.dev + path, { method: "DELETE" });
    },
  };

  const store = cfg.dev ? devStore : graphStore;

  function checkUnlinked(unlinked) {
    // 再連携時に確認済みの解除通知が遅れて届いても、再びサインアウトしない。
    if (unlinked && !(Number.isFinite(unlinked.unlinked) && unlinked.unlinked === load(KEY.unlinkAck))) {
      const e = new Error("PC で Microsoft アカウント連携が解除されました。再開するには、PC で連携をオンにしてからサインインしてください。");
      e.name = "Unlinked";
      throw e;
    }
  }

  let linking = null;
  function ensureViewerLink(reconnect = false) {
    if (cfg.dev) return Promise.resolve();
    if (linking) return linking;
    linking = (async () => {
      const unlinked = await store.read("unlinked.json");
      if (!reconnect) checkUnlinked(unlinked);
      if (reconnect && Number.isFinite(unlinked && unlinked.unlinked)) save(KEY.unlinkAck, unlinked.unlinked);
      const marker = await store.read("viewer.json");
      if (reconnect || !marker || marker.app !== MARKER.app) {
        await store.ensureRoot();
        const fresh = { ...MARKER, signed_in: Date.now() / 1000 };
        await store.write("viewer.json", fresh);
        save(KEY.marker, fresh);
        clearCache();
      } else {
        save(KEY.marker, marker); // 端末の「連携済み」だけで判断せず、OneDrive 上の目印を確かめる
      }
      // 解除通知は残し、この端末が確認した通知だけを無視する。
      // 読んだ直後に PC が再び解除しても、新しい通知を削除してしまわないため。
      state.linkError = "";
      state.linkChecked = Date.now();
    })().catch((e) => { state.linkError = e.message; throw e; })
      .finally(() => { linking = null; });
    return linking;
  }

  // ---- 読み込み（1分間は読み直さない。通信できないときは前に読んだ内容を使う）

  const mem = new Map();
  function remember(path) {
    const index = (load(KEY.cacheIndex) || []).filter((p) => p !== path);
    index.unshift(path);
    index.splice(CACHE_FILES).forEach((p) => save(KEY.cache + p, null));
    save(KEY.cacheIndex, index);
  }

  async function read(path) {
    const hit = mem.get(path) || load(KEY.cache + path);
    const ttl = path === "unlinked.json" || (hit && hit.data == null) ? 10 : CACHE_SEC;
    if (hit && Date.now() - hit.t < ttl * 1000) return hit.data;
    try {
      const data = await store.read(path);
      const entry = { t: Date.now(), data };
      mem.set(path, entry);
      save(KEY.cache + path, entry);
      remember(path);
      state.offline = false;
      return data;
    } catch (e) {
      if (e.name === "SignInRequired" || !hit) throw e;
      state.offline = true;
      return hit.data;
    }
  }

  function clearCache() {
    mem.clear();
    (load(KEY.cacheIndex) || []).forEach((p) => {
      const hit = load(KEY.cache + p);
      if (hit) save(KEY.cache + p, { ...hit, t: 0 }); // 通信できないときのために中身は残す
    });
    state.pendingChecked = 0;
  }

  // ---- 日付

  const pad = (n) => String(n).padStart(2, "0");
  const isoDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const today = () => isoDate(new Date(Date.now() - DAY_START_HOUR * 3600 * 1000));

  function dayRange(date) {
    const [y, m, d] = date.split("-").map(Number);
    return [new Date(y, m - 1, d, DAY_START_HOUR) / 1000, new Date(y, m - 1, d + 1, DAY_START_HOUR) / 1000];
  }

  function emptyDay(date, meta) {
    const [y, m, d] = date.split("-").map(Number);
    const range = dayRange(date);
    return {
      date, date_label: `${y}年${m}月${d}日（${WEEKDAYS[new Date(y, m - 1, d).getDay()]}）`, range,
      now: Math.min(Date.now() / 1000, range[1]), stays: [], stay_total: 0, work_total: 0, work_by_location: {},
      categories: [], category_names: (meta && meta.category_names) || [], timeline: [], report: null,
    };
  }

  function emptyMonth(month) {
    const [y, m] = month.split("-").map(Number);
    const days = [];
    for (let d = 1; d <= new Date(y, m, 0).getDate(); d++) {
      days.push({ date: `${month}-${pad(d)}`, stay: 0, work: 0, by_location: {}, first_in: null, last_out: null,
                  has_report: false });
    }
    return { month, days, stay_total: 0, work_total: 0, stay_days: 0 };
  }

  // ---- 直した内容（PC が取り込むまでは、この端末で直した内容を重ねて表示する）

  const pendingEdits = () => (load(KEY.pending) || []).filter((e) => Date.now() / 1000 - e.ts < 7 * 86400);

  async function submitEdit(edit) {
    const ts = Date.now() / 1000;
    const name = `${Date.now()}-${randomId(6)}.json`;
    const body = { v: 1, ...edit, ts };
    await store.write(`edits/${name}`, body);
    save(KEY.pending, [...pendingEdits(), { name, ...body }]);
  }

  async function checkPending(meta) {
    const list = pendingEdits();
    if (!list.length || Date.now() - state.pendingChecked < 30000) return;
    state.pendingChecked = Date.now();
    try {
      const names = new Set(await store.list("edits"));
      // PC が取り込めなかったもの（cloud.py の _reject）：理由を覚えて、お知らせに出す
      const rejected = list.filter((e) => names.has(e.name.replace(/\.json$/, ".rejected")));
      for (const e of rejected) {
        const file = `edits/${e.name.replace(/\.json$/, ".rejected")}`;
        const info = await store.read(file).catch(() => null);
        save(KEY.rejected, [...(load(KEY.rejected) || []),
          { date: e.date, type: e.type, error: (info && info.error) || "PC の LabLog が取り込めませんでした" }]);
        await store.remove(file).catch(() => {});
      }
      // PC が取り込んで消し、その後の書き出しも届いたものは外す
      save(KEY.pending, list.filter((e) => !rejected.includes(e) && (names.has(e.name) || !meta || meta.exported < e.ts)));
    } catch { /* 通信できないときは次の機会に */ }
  }

  // PC が取り込み済みのもの（書き出された内容に同じものがある）は重ねない（二重に出さない）
  const sameTime = (a, b) => a != null && b != null && Math.abs(a - b) < 1;
  const alreadyIn = (day, e) => (e.type === "report" ? day.report && day.report.text === e.text
    : e.type === "stay_save" ? day.stays.some((s) => (e.id == null || s.id === e.id) && sameTime(s.start, e.start)
                                                     && (e.end == null || sameTime(s.end, e.end)))
    : e.type === "stay_delete" ? !day.stays.some((s) => s.id === e.id) : false);

  function applyPending(day) {
    const edits = pendingEdits().filter((e) => e.date === day.date && !alreadyIn(day, e));
    for (const e of edits) {
      if (e.type === "report") {
        day.report = { text: e.text, edited: 1, generated_ts: null, source: "user", pending: true };
      } else if (e.type === "stay_delete") {
        day.stays = day.stays.filter((s) => s.id !== e.id);
      } else if (e.type === "stay_save") {
        const old = e.id != null && day.stays.find((s) => s.id === e.id);
        const stay = old ? { ...old, start: e.start, pending: true } : { id: null, start: e.start, reason: "manual", pending: true };
        if (e.end != null) Object.assign(stay, { end: e.end, state: "closed", reason: "manual" });
        stay.duration = Math.max(0, stay.end - stay.start);
        day.stays = old ? day.stays.map((s) => (s === old ? stay : s)) : [...day.stays, stay];
      }
    }
    day.stays.sort((a, b) => a.start - b.start);
    day.stay_total = day.stays.reduce((sum, s) => sum + s.duration, 0);
    day.pending = edits.length;
  }

  // ---- app.js からの /api/... の呼び出しを受ける

  async function getStatus() {
    const [meta, unlinked] = await Promise.all([read("meta.json"), read("unlinked.json")]);
    checkUnlinked(unlinked); // 古い記録のキャッシュがあっても、明示的な連携解除は反映する
    if ((!meta || state.linkError) && Date.now() - state.linkChecked >= 30000) {
      try { await ensureViewerLink(); }
      catch (e) { if (e.name === "Unlinked" || e.name === "SignInRequired") throw e; }
    }
    await checkPending(meta);
    const base = { today: today(), offline: state.offline, pending: pendingEdits().length,
                   rejected: load(KEY.rejected) || [], link_error: state.linkError };
    if (!meta) return { ...base, state: "none" };
    // app_version：PC の LabLog のバージョン（前のバージョンの PC は書かない）。viewer_version：このビューアーを公開したときのバージョン
    return { ...meta.status, ...base, exported: meta.exported, app_version: meta.app_version || null,
             viewer_version: cfg.version || null, device: meta.device ? meta.device.name : null };
  }

  async function getDay(date) {
    const [meta, data] = await Promise.all([read("meta.json"), read(`days/${date}.json`)]);
    await checkPending(meta); // 取り込み済みの修正を、反映待ちの一覧から外す
    const day = data ? structuredClone(data) : emptyDay(date, meta);
    day.is_today = date === today();
    day.reports_off = !!meta && meta.reports === false; // PC の設定で、日報をスマホに送っていない
    if (meta && meta.exported > day.range[0]) day.now = Math.min(meta.exported, day.range[1]);
    // PC だけで見られる記録（ウィンドウ名・ファイル・閲覧履歴など）はクラウドに無い
    Object.assign(day, { apps: [], files: [], files_total: 0, commits: [], web: [], web_total: 0, recent: [] });
    day.timeline = day.timeline.map((b) => ({ ...b, top: [] }));
    applyPending(day);
    return day;
  }

  async function getMonth(month) {
    const data = await read(`months/${month}.json`);
    return data ? structuredClone(data) : emptyMonth(month);
  }

  const toTs = (text) => (text ? new Date(text).getTime() / 1000 : null);

  async function saveStay(body) {
    const start = toTs(body.start), end = toTs(body.end);
    if (!start || Number.isNaN(start) || Number.isNaN(end)) throw new Error("時刻の形式が正しくありません");
    if (end != null && end <= start) throw new Error("退室は入室より後の時刻にしてください");
    if (body.id == null && end == null) throw new Error("追加するときは退室時刻も入れてください");
    // PC と同じ確かめ（presence.py の save_stay）。PC で受け付けられない修正は、ここで止める
    const now = Date.now() / 1000;
    if (start > now || (end != null && end > now)) throw new Error("これからの時刻は入れられません");
    const day = await getDay(body.date);
    const until = end ?? now;
    if (day.stays.some((s) => s.id !== body.id && start < (s.state === "in" ? now : s.end) && s.start < until)) {
      throw new Error("ほかの入退室と重なっています");
    }
    await submitEdit({ type: "stay_save", id: body.id, start, end, date: body.date });
  }

  const PC_ONLY = "この操作は PC の LabLog でだけできます";
  const api = {
    async get(path) {
      const [p, qs] = path.split("?");
      const q = new URLSearchParams(qs);
      if (p === "/api/status") return getStatus();
      if (p === "/api/day") return getDay(q.get("date") || today());
      if (p === "/api/month") return getMonth(q.get("month") || today().slice(0, 7));
      throw new Error(PC_ONLY);
    },
    async post(path, body) {
      if (path === "/api/report/save") await submitEdit({ type: "report", date: body.date, text: String(body.text || "") });
      else if (path === "/api/stay/save") await saveStay(body);
      else if (path === "/api/stay/delete") await submitEdit({ type: "stay_delete", id: body.id, date: body.date });
      else throw new Error(PC_ONLY);
      clearCache();
      return { ok: true };
    },
  };

  // 起動時：サインインの確認と、画面ファイルの保存（電波がないときも開けるように）
  async function ready() {
    if ("serviceWorker" in navigator && !cfg.dev) navigator.serviceWorker.register("sw.js").catch(() => {});
    if (cfg.dev) return { signedIn: true };
    if (!cfg.clientId) return { signedIn: false, error: "ビューアーの準備（Microsoft へのアプリ登録）がまだ終わっていません。" };
    let reconnect = false;
    try {
      reconnect = await finishSignIn();
    } catch (e) {
      return { signedIn: false, error: e.message };
    }
    const tok = load(KEY.token);
    const signedIn = !!(tok && (tok.access || tok.refresh)) || (!!tok && Date.now() - (load(KEY.silent) || 0) > 10 * 60 * 1000);
    if (signedIn) {
      try {
        await ensureViewerLink(reconnect);
      } catch (e) {
        if (e.name === "Unlinked") { signOut(); return { signedIn: false, error: e.message }; }
        if (e.name === "SignInRequired") return { signedIn: false };
        // 一時的な通信エラーならサインインをやり直させず、状態の確認時に再試行する。
      }
    }
    return { signedIn };
  }

  function signOut() {
    Object.keys(localStorage).filter((k) => k.startsWith("lablog.")).forEach((k) => localStorage.removeItem(k));
    mem.clear();
    state.linkChecked = 0;
    state.linkError = "";
  }

  return { api, ready, signIn: () => signIn(), signOut, clearCache, reconnect: () => ensureViewerLink(true),
           dev: !!cfg.dev, build: cfg.build || "",
           version: cfg.version || "",
           dismissRejected: () => save(KEY.rejected, null) };
})();
