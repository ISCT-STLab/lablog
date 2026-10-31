// Run the real OneDrive adapter against an in-memory Graph server and a controlled clock.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lablog/web/cloud.js'), 'utf8');

function environment({ files = {}, storage = {}, login = false, silent = false } = {}) {
  let now = Date.UTC(2026, 9, 31, 8), offline = false, failWrites = false;
  const requests = [], remote = new Map(Object.entries(files));
  const localStorage = {};
  Object.defineProperties(localStorage, {
    getItem: { value: key => localStorage[key] ?? null },
    setItem: { value: (key, value) => { localStorage[key] = String(value); } },
    removeItem: { value: key => { delete localStorage[key]; } },
  });
  localStorage.setItem('lablog.token', JSON.stringify({ access: 'synthetic', exp: now + 3600000 }));
  for (const [key, value] of Object.entries(storage)) localStorage.setItem(key, JSON.stringify(value));
  if (login) localStorage.setItem('lablog.login', JSON.stringify({ state: 'test', verifier: 'test', silent }));
  const location = { origin: 'https://example.invalid', pathname: '/', search: login ? '?code=test&state=test' : '', hash: '' };
  const response = (status, data) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(data) });
  const fetch = async (url, init = {}) => {
    requests.push({ url, method: init.method || 'GET' });
    if (offline) throw new Error('synthetic network failure');
    if (url.includes('/oauth2/v2.0/token')) return response(200, { access_token: 'synthetic', expires_in: 3600 });
    if (url.startsWith('https://download.invalid/')) return response(200, remote.get(url.slice('https://download.invalid/'.length)));
    const name = url.split('approot:/')[1]?.replace(/:\/content$/, '');
    if (!name) return response(200, {});
    if (init.method === 'PUT') {
      if (failWrites) return response(503, { error: { code: 'unavailable', message: 'retry' } });
      remote.set(name, JSON.parse(init.body)); return response(200, {});
    }
    if (init.method === 'DELETE') { remote.delete(name); return response(204, {}); }
    return remote.has(name) ? response(200, { '@microsoft.graph.downloadUrl': 'https://download.invalid/' + name }) : response(404, {});
  };
  class Clock extends Date { static now() { return now; } }
  const window = { LABLOG_CLOUD: { clientId: 'test', version: '1.0.0b8' } };
  const ctx = vm.createContext({ window, localStorage, location, navigator: {}, fetch, URLSearchParams, Date: Clock,
    history: { replaceState() { location.search = ''; } }, structuredClone });
  vm.runInContext(source, ctx);
  return { cloud: window.LabLogCloud, remote, requests, localStorage,
    tick: ms => { now += ms; }, offline: value => { offline = value; }, failWrites: value => { failWrites = value; } };
}

test('a remembered login repairs a missing remote marker without signing out', async () => {
  const e = environment({ storage: { 'lablog.marker': 1 } });
  assert.equal((await e.cloud.ready()).signedIn, true);
  assert.equal(e.remote.get('viewer.json').app, 'LabLog viewer');
  assert.equal((await e.cloud.api.get('/api/status')).state, 'none');
});

test('fresh sign-in renews the marker and ignores only the unlink it acknowledged', async () => {
  const e = environment({ login: true, storage: { 'lablog.marker': 1 },
    files: { 'viewer.json': { app: 'LabLog viewer', signed_in: 1 }, 'unlinked.json': { unlinked: 100 } } });
  assert.equal((await e.cloud.ready()).signedIn, true);
  assert.ok(e.remote.get('viewer.json').signed_in > 1);
  assert.equal(e.remote.has('unlinked.json'), true); // 新しい解除通知との削除競合を避ける
  // The old deletion notification arrives late through OneDrive.
  e.remote.set('unlinked.json', { unlinked: 100 });
  assert.equal((await e.cloud.api.get('/api/status')).state, 'none');
  // A later, intentional unlink must still take effect, even with cached records.
  e.remote.set('meta.json', { status: { state: 'out' }, exported: 500 });
  e.tick(11000); await e.cloud.api.get('/api/status');
  e.remote.set('unlinked.json', { unlinked: 200 });
  e.tick(11000);
  await assert.rejects(e.cloud.api.get('/api/status'), { name: 'Unlinked' });
});

for (const silent of [false, true]) {
  test(`an intentional unlink is not silently reconnected (silent login: ${silent})`, async () => {
    const e = environment({ login: silent, silent, storage: { 'lablog.marker': 1 },
      files: { 'unlinked.json': { unlinked: 100 }, 'meta.json': { status: { state: 'out' } } } });
    const result = await e.cloud.ready();
    assert.equal(result.signedIn, false);
    assert.equal(e.remote.has('viewer.json'), false);
    assert.ok(e.remote.has('unlinked.json'));
    assert.equal(e.localStorage.getItem('lablog.token'), null);
    assert.equal(e.requests.some(r => r.method === 'PUT' || r.method === 'DELETE'), false);
  });
}

test('missing metadata is retried soon and delayed records appear without reloading', async () => {
  const e = environment(); await e.cloud.ready();
  assert.equal((await e.cloud.api.get('/api/status')).state, 'none');
  e.remote.set('meta.json', { status: { state: 'out' }, app_version: '1.0.0b7', exported: 500 });
  e.tick(11000);
  const status = await e.cloud.api.get('/api/status');
  assert.equal(status.state, 'out');
  assert.equal(status.app_version, '1.0.0b7');
});

test('a transient marker write failure is shown and retried automatically', async () => {
  const e = environment({ storage: { 'lablog.marker': 1 } }); e.failWrites(true);
  assert.equal((await e.cloud.ready()).signedIn, true);
  assert.match((await e.cloud.api.get('/api/status')).link_error, /503/);
  e.failWrites(false); e.tick(31000);
  assert.equal((await e.cloud.api.get('/api/status')).link_error, '');
  assert.equal(e.remote.get('viewer.json').app, 'LabLog viewer');
});

test('a temporary write failure after explicit re-sign-in does not undo that re-sign-in', async () => {
  const e = environment({ login: true, files: { 'unlinked.json': { unlinked: 100 } } });
  e.failWrites(true);
  assert.equal((await e.cloud.ready()).signedIn, true);
  assert.match((await e.cloud.api.get('/api/status')).link_error, /503/);
  e.failWrites(false); e.tick(31000);
  assert.equal((await e.cloud.api.get('/api/status')).link_error, '');
  assert.equal(e.remote.get('viewer.json').app, 'LabLog viewer');
});

test('offline startup retains cached records and retries link verification after recovery', async () => {
  const meta = { status: { state: 'out' }, exported: 500 };
  const e = environment({ storage: { 'lablog.marker': 1,
    'lablog.cache:meta.json': { t: 0, data: meta }, 'lablog.cache:unlinked.json': { t: 0, data: null } },
    files: { 'meta.json': meta } });
  e.offline(true);
  assert.equal((await e.cloud.ready()).signedIn, true);
  const status = await e.cloud.api.get('/api/status');
  assert.equal(status.state, 'out'); assert.equal(status.offline, true);
  e.offline(false); e.tick(31000);
  assert.equal((await e.cloud.api.get('/api/status')).link_error, '');
  assert.equal(e.remote.get('viewer.json').app, 'LabLog viewer');
});

test('manual recheck preserves records and pending phone edits', async () => {
  const e = environment({ storage: { 'lablog.pending': [{ text: 'pending' }], 'lablog.unsaved:2026-10-31': 'draft' },
    files: { 'meta.json': { status: { state: 'out' } }, 'days/2026-10-31.json': { report: 'existing' } } });
  await e.cloud.reconnect();
  assert.equal(e.remote.get('days/2026-10-31.json').report, 'existing');
  assert.ok(e.localStorage.getItem('lablog.pending').includes('pending'));
  assert.ok(e.localStorage.getItem('lablog.unsaved:2026-10-31').includes('draft'));
  assert.equal(e.requests.some(r => r.method === 'DELETE'), false);
});

const app = fs.readFileSync(path.join(__dirname, '../lablog/web/app.js'), 'utf8');
const refresh = app.slice(app.indexOf('async function refreshStatus()'), app.indexOf('const UNLINK_CONFIRM'));
function screen({ editing = false, error = null, pc = false } = {}) {
  const calls = [], state = { status: { state: 'none' }, nav: 2, reportEditing: editing };
  const cloud = pc ? null : { clearCache: () => calls.push('clear'), signOut: () => calls.push('signout') };
  const ctx = vm.createContext({ state, cloud, Date, clearTimeout() {}, clearInterval() {},
    $: selector => pc && selector === '#cloud-state' ? {} : null,
    api: { get: async () => { if (error) throw error; return { state: 'out' }; } },
    renderStatus: () => calls.push('status'), hasUnsaved: () => editing, route: async () => calls.push('route'),
    loadCloudState: async () => calls.push('pc-link-state'), renderSignIn: () => calls.push('signin'),
    renderNotices: () => calls.push('notice') });
  vm.runInContext(refresh, ctx);
  return { calls, state, refresh: ctx.refreshStatus };
}

test('the waiting screen redraws when records arrive, while protecting an active editor', async () => {
  for (const editing of [false, true]) {
    const e = screen({ editing }); await e.refresh();
    assert.equal(e.calls.includes('route'), !editing);
  }
});

test('PC connection status refreshes without reopening settings', async () => {
  const e = screen({ pc: true }); await e.refresh();
  assert.ok(e.calls.includes('pc-link-state'));
});

test('connection errors are visible and a successful retry clears them', async () => {
  const e = screen({ error: new Error('network failure') }); await e.refresh();
  assert.equal(e.state.cloudError, 'network failure');
  assert.ok(e.calls.includes('notice'));
  const recovered = screen(); recovered.state.cloudError = 'network failure'; await recovered.refresh();
  assert.equal(recovered.state.cloudError, '');
});

test('unlink and expired sign-in stop old page renders without erasing drafts on expiry', async () => {
  for (const name of ['Unlinked', 'SignInRequired']) {
    const error = Object.assign(new Error('sign in'), { name });
    const e = screen({ error }); await e.refresh();
    assert.ok(e.calls.includes('signin'));
    assert.equal(e.calls.includes('signout'), name === 'Unlinked');
    assert.equal(e.state.nav, 3);
    assert.equal(e.state.unlinked, true);
  }
});
