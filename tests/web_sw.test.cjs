// Run the viewer's service worker (sw.js) against an in-memory Cache Storage and a controlled network.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lablog/web/sw.js'), 'utf8');
const BASE = 'https://example.invalid/lablog/';
const SHELL = ['', 'index.html', 'style.css', 'config.js', 'cloud.js', 'setup.js', 'app.js', 'favicon.svg',
  'manifest.webmanifest', 'icon-180.png', 'vendor/qrcode.js'];
const site = (release, extra = {}) => Object.fromEntries(SHELL.map(name => [name, `${name || 'page'} ${release}`])
  .concat([['config.js', `window.LABLOG_CLOUD = {"release": "${release}"};`]], Object.entries(extra)));

function worker({ channel = 'stable', build = 'r2', files = site('r2'), caches: existing = {} } = {}) {
  const placeholder = 'const CHANNEL = "dev", BUILD = "dev";';
  assert.ok(source.includes(placeholder)); // tools/publish_viewer.py rewrites this line
  const code = source.replace(placeholder, `const CHANNEL = ${JSON.stringify(channel)}, BUILD = ${JSON.stringify(build)};`);
  const scriptUrl = new URL('sw.js', BASE);
  const store = new Map(Object.entries(existing).map(([name, entries]) => [name, new Map(Object.entries(entries))]));
  const network = [];
  class Res {
    constructor(body, status = 200) { Object.assign(this, { body, status, ok: status < 400 }); }
    clone() { return new Res(this.body, this.status); }
    async text() { return this.body; }
  }
  class Req { constructor(url, init = {}) { this.url = new URL(url, scriptUrl).href; this.cache = init.cache; } }
  const fetch = async req => {
    network.push({ url: req.url, cache: req.cache });
    const name = req.url.slice(BASE.length);
    return name in files ? new Res(files[name]) : new Res('', 404);
  };
  const key = k => new URL(typeof k === 'string' ? k : k.url, scriptUrl).href;
  const caches = {
    async open(name) {
      if (!store.has(name)) store.set(name, new Map());
      const entries = store.get(name);
      return {
        async addAll(reqs) {
          const got = await Promise.all(reqs.map(fetch));
          if (got.some(r => !r.ok)) throw new TypeError('addAll failed');
          reqs.forEach((r, i) => entries.set(key(r), got[i]));
        },
        async match(k) { const hit = entries.get(key(k)); return hit && hit.clone(); },
        async put(k, res) { entries.set(key(k), res); },
      };
    },
    async keys() { return [...store.keys()]; },
    async delete(name) { return store.delete(name); },
  };
  const handlers = {}, skipped = [];
  const self = { location: scriptUrl, addEventListener: (type, fn) => { handlers[type] = fn; },
    skipWaiting: async () => { skipped.push(true); }, clients: { claim: async () => {} } };
  vm.runInContext(code, vm.createContext({ self, caches, fetch, Request: Req, URL, Set }));
  const run = type => { let job; handlers[type]({ waitUntil: p => { job = p; } }); return job; };
  return {
    store, network, files, skipped,
    install: () => run('install'), activate: () => run('activate'),
    message: data => handlers.message({ data }),
    // Returns the response, or null when the worker leaves the request to the browser.
    request: async url => {
      let answer = null;
      handlers.fetch({ request: { method: 'GET', url: new URL(url, BASE).href }, respondWith: p => { answer = p; } });
      return answer && (await answer).body;
    },
  };
}

test('a new version is stored in full, bypassing the browser cache, before it replaces the old one', async () => {
  const w = worker({ caches: { 'lablog-stable-r1': { [BASE]: 'old' }, 'lablog-beta-r9': {}, 'lablog-viewer-v4': {},
    'lablog-assets-stable': {} } });
  await w.install();
  assert.equal(w.store.get('lablog-stable-r2').size, SHELL.length);
  assert.ok(w.network.every(r => r.cache === 'reload'));
  // It waits: switching while a page is loading could mix old and new files.
  assert.equal(w.skipped.length, 0);
  w.message('something else');
  assert.equal(w.skipped.length, 0);
  w.message('skipWaiting'); // the page asks, then reloads
  assert.equal(w.skipped.length, 1);
  await w.activate();
  // The old stable copy and the shared cache of the previous design go; beta's copy and the fonts stay.
  assert.deepEqual([...w.store.keys()].sort(), ['lablog-assets-stable', 'lablog-beta-r9', 'lablog-stable-r2']);
});

test('files from two different releases are never mixed', async () => {
  const w = worker({ files: site('r2', { 'config.js': 'window.LABLOG_CLOUD = {"release": "r1"};' }) });
  await assert.rejects(w.install());
  assert.equal(w.store.has('lablog-stable-r2'), false);
});

test('stored screen files open without the network, also when returning from sign-in', async () => {
  const w = worker();
  await w.install(); await w.activate();
  w.network.length = 0;
  assert.equal(await w.request('app.js'), 'app.js r2');
  assert.equal(await w.request('?code=abc&state=xyz'), 'page r2');
  assert.equal(w.network.length, 0);
});

test('fonts are stored once when first used and kept across versions', async () => {
  const w = worker({ files: site('r2', { 'fonts/KleeOne-Regular.woff2': 'font' }) });
  await w.install(); await w.activate();
  w.network.length = 0;
  assert.equal(await w.request('fonts/KleeOne-Regular.woff2'), 'font');
  assert.equal(await w.request('fonts/KleeOne-Regular.woff2'), 'font');
  assert.equal(w.network.length, 1);
  assert.equal(w.store.get('lablog-assets-stable').size, 1);
});

test('the stable worker leaves the beta viewer and other files to the browser', async () => {
  const w = worker();
  await w.install(); await w.activate();
  assert.equal(await w.request('beta/'), null);
  assert.equal(await w.request('beta/app.js'), null);
  assert.equal(await w.request('https://graph.microsoft.com/v1.0/me'), null);
});
