// ビューアーの画面ファイルを端末に保存し、電波がないときも開けるようにする。
// 記録のデータ（OneDrive）は扱わない（同じサイトのファイルだけが対象）。
"use strict";

const CACHE = "lablog-viewer-v3";
const SHELL = ["./", "index.html", "style.css", "config.js", "cloud.js", "setup.js", "app.js", "favicon.svg",
               "manifest.webmanifest", "icon-180.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  // 新しい版をすぐ使えるよう、まずネットから取り、つながらないときだけ保存しておいた分を使う
  // GitHub Pages は 10 分間キャッシュさせるので、毎回サーバーに新しい版がないか確かめる
  e.respondWith(fetch(e.request, { cache: "no-cache" })
    .then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    })
    .catch(() => caches.match(e.request, { ignoreSearch: true })));
});
