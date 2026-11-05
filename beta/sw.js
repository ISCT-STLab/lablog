// スマホ用ビューアーの画面ファイルを端末に保存し、開いたらすぐ表示する（電波がないときも開ける）。
// 記録のデータ（OneDrive）は扱わない（同じサイトの画面ファイルだけが対象）。
//
// 新しいバージョンを公開すると、tools/publish_viewer.py が下の CHANNEL・BUILD を書き換えるので、この sw.js も変わる。
// ブラウザが変化に気づくと、新しいバージョンの画面ファイルを別の保存場所にまとめて取り、待機する。
// 切り替えるのは、開いているページから頼まれたとき（cloud.js の applyUpdate）だけ。ページはすぐ開き直す
// （読み込みの途中で切り替わって、古いファイルと混ざらないように）。
// 安定版（…/lablog/）とベータ版（…/lablog/beta/）は保存場所を分け、互いのファイルと保存場所には触れない。
"use strict";

const CHANNEL = "beta", BUILD = "1.0.0-20261105141306";
const PREFIX = `lablog-${CHANNEL}-`;
const CACHE = PREFIX + BUILD;
// 大きくてバージョンで変わらないもの（フォント・大きいアイコン）は、使ったときに別の保存場所に置き、切り替えても取り直さない
const ASSETS = `lablog-assets-${CHANNEL}`;
const SHELL = ["./", "index.html", "style.css", "config.js", "cloud.js", "setup.js", "app.js", "favicon.svg",
               "manifest.webmanifest", "icon-180.png", "vendor/qrcode.js"];
const LATER = ["fonts/KleeOne-Regular.woff2", "icon-192.png", "icon-512.png"];
const href = (path) => new URL(path, self.location).href;
const SHELL_URLS = new Set(SHELL.map(href)), LATER_URLS = new Set(LATER.map(href));

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    // GitHub Pages は 10 分間キャッシュさせるので、ブラウザのキャッシュを通さずに取る
    await cache.addAll(SHELL.map((path) => new Request(path, { cache: "reload" })));
    // 取った config.js が、この sw.js と同じ公開のものか確かめる（公開の途中で古いファイルが混ざったら、次の機会にやり直す）
    const config = await cache.match("config.js");
    if (BUILD !== "dev" && !(config && (await config.text()).includes(JSON.stringify(BUILD)))) {
      await caches.delete(CACHE);
      throw new Error("画面ファイルのバージョンがそろっていません");
    }
  })());
});

self.addEventListener("message", (e) => {
  if (e.data === "skipWaiting") self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const key of await caches.keys()) {
      // 同じ版（安定版・ベータ版）の古いバージョンと、前のしくみの保存場所（lablog-viewer-v4 など）だけを消す
      if ((key.startsWith(PREFIX) && key !== CACHE) || /^lablog-viewer-v\d+$/.test(key)) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);
  url.search = ""; // サインインから戻ったとき（?code=…）も、保存してあるページを使う
  url.hash = "";
  if (SHELL_URLS.has(url.href)) {
    e.respondWith(caches.open(CACHE).then((c) => c.match(url.href)).then((hit) => hit || fetch(e.request)));
  } else if (LATER_URLS.has(url.href)) {
    e.respondWith(caches.open(ASSETS).then(async (c) => {
      const hit = await c.match(url.href);
      if (hit) return hit;
      const res = await fetch(e.request);
      if (res.ok) await c.put(url.href, res.clone());
      return res;
    }));
  }
  // それ以外（ベータ版のページなど、ほかの場所のファイル）は扱わず、ブラウザにそのまま取らせる
});
