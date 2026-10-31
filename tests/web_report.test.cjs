// Real dashboard handlers with synthetic DOM/storage and deliberately delayed responses.
// Run: node --test tests/web_report.test.cjs
const { test: nodeTest } = require('node:test');
const test = (name, fn) => nodeTest(name, { timeout: 2000 }, fn);
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lablog/web/app.js'), 'utf8').replace(/\r\n/g, '\n');

function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, `Missing source section: ${start}`);
  return source.slice(a, b);
}
function element() {
  return { value: '', disabled: false, textContent: '', events: {},
    addEventListener(event, handler) { this.events[event] = handler; }, focus() {} };
}
function environment() {
  const elements = {}, requests = [], toasts = [], alerts = [], data = new Map();
  const localStorage = {
    getItem: k => data.get(k) ?? null,
    setItem: (k, v) => data.set(k, String(v)),
    removeItem: k => data.delete(k),
  };
  const card = element();
  let html = '';
  elements['#report-card'] = card;
  Object.defineProperty(card, 'innerHTML', {
    get: () => html,
    set(value) {
      html = value;
      for (const k of Object.keys(elements)) if (k.startsWith('#report-') && k !== '#report-card') delete elements[k];
      for (const m of value.matchAll(/id="(report-[\w-]+)"/g)) elements['#' + m[1]] = element();
      const ta = elements['#report-text'];
      if (ta) ta.value = /<textarea[^>]*>([\s\S]*?)<\/textarea>/.exec(value)[1];
    },
  });
  const state = { nav: 1 };
  const ctx = vm.createContext({ state, cloud: null, localStorage, $: s => elements[s] || null,
    esc: v => v, renderMarkdown: v => v, fitTextarea() {}, reportMeta: () => '',
    flash: (text, action) => toasts.push({ text, action }), alert: text => alerts.push(text), confirm: () => true,
    api: { post: (url, body) => new Promise((resolve, reject) => requests.push({ url, body, resolve, reject })) },
  });
  vm.runInContext(section('const unsavedKey =', 'function reportMeta('), ctx);
  vm.runInContext(section('function renderReport(', '// ------------------------------------------------------------ 月'), ctx);
  const day = (date = '2026-10-30', text = 'original') => ({ date, report: { text, edited: 1 } });
  return { ctx, state, elements, requests, toasts, alerts, card, day,
    kept: date => localStorage.getItem('lablog.unsaved:' + date),
    edit(d) { ctx.renderReport(d, { editing: true }); },
    input(text) { const ta = elements['#report-text']; ta.value = text; ta.events.input(); return ta; },
    click(id) { return elements['#' + id].events.click(); },
    leave() { state.nav++; for (const k of Object.keys(elements)) delete elements[k]; },
  };
}

test('saving in place retains additions and prevents a duplicate submission', async () => {
  const e = environment(), d = e.day(); e.edit(d);
  e.input('sent'); const saving = e.click('report-save');
  const ta = e.input('addition');
  assert.equal(e.elements['#report-save'].disabled, true);
  await e.click('report-save');
  assert.equal(e.requests.length, 1);
  e.requests[0].resolve({}); await saving;
  assert.equal(d.report.text, 'sent');
  assert.equal(e.kept(d.date), 'addition');
  assert.equal(e.elements['#report-text'], ta);
  assert.equal(e.elements['#report-save'].disabled, false);
});

for (const destination of ['other day', 'month', 'settings', 'route loading']) {
  test(`save completion preserves additions after navigation to ${destination}`, async () => {
    const e = environment(), d = e.day(); e.edit(d);
    e.input('sent'); const saving = e.click('report-save'); e.input('addition');
    if (destination === 'other day') e.ctx.renderReport(e.day('2026-10-31', 'other day'));
    else if (destination === 'route loading') e.state.nav++;
    else e.leave();
    const html = e.card.innerHTML;
    e.requests[0].resolve({}); await saving;
    assert.equal(e.kept(d.date), 'addition');
    assert.equal(e.card.innerHTML, html);
  });
}

test('matching saved text is cleared without repainting another date', async () => {
  const e = environment(), d = e.day(); e.edit(d);
  e.input('sent'); const saving = e.click('report-save');
  e.ctx.renderReport(e.day('2026-10-31', 'other'));
  const html = e.card.innerHTML;
  e.requests[0].resolve({}); await saving;
  assert.equal(e.kept(d.date), null);
  assert.equal(e.card.innerHTML, html);
});

test('returning to the same date serializes writes and protects the newer editor', async () => {
  const e = environment(), a = e.day(), b = e.day(); e.edit(a);
  e.input('first'); const first = e.click('report-save');
  e.edit(b); e.input('second'); const second = e.click('report-save');
  const ta = e.input('third');
  assert.equal(e.requests.length, 1);
  e.requests[0].resolve({}); await first;
  assert.equal(e.requests.length, 2);
  assert.equal(e.requests[1].body.text, 'second');
  assert.equal(e.kept(a.date), 'third');
  e.requests[1].resolve({}); await second;
  assert.equal(b.report.text, 'second');
  assert.equal(e.elements['#report-text'], ta);
  assert.equal(e.kept(a.date), 'third');
});

test('a rejected save keeps the editor and can be retried', async () => {
  const e = environment(), d = e.day(); e.edit(d); const ta = e.input('new');
  const saving = e.click('report-save'); e.requests[0].reject(new Error('403')); await saving;
  assert.equal(d.report.text, 'original');
  assert.equal(e.elements['#report-text'], ta);
  assert.equal(e.kept(d.date), 'new');
  assert.equal(e.elements['#report-save'].disabled, false);
  const retry = e.click('report-save'); e.requests[1].resolve({}); await retry;
  assert.equal(d.report.text, 'new');
  assert.equal(e.kept(d.date), null);
});

test('a failed earlier write does not block a queued save', async () => {
  const e = environment(), a = e.day(), b = e.day(); e.edit(a);
  e.input('first'); const first = e.click('report-save');
  e.edit(b); e.input('second'); const second = e.click('report-save');
  e.requests[0].reject(new Error('failed')); await first;
  await Promise.resolve();
  e.requests[1].resolve({}); await second;
  assert.equal(b.report.text, 'second');
});

test('generation and its undo never repaint another date', async () => {
  const e = environment(), d = e.day(); e.ctx.renderReport(d);
  const generating = e.click('report-gen');
  e.ctx.renderReport(e.day('2026-10-31', 'other'));
  const html = e.card.innerHTML;
  e.requests[0].resolve({ text: 'generated' }); await generating;
  assert.equal(e.card.innerHTML, html);
  const undoing = e.toasts.at(-1).action.run();
  e.requests[1].resolve({}); await undoing;
  assert.equal(d.report.text, 'original');
  assert.equal(e.card.innerHTML, html);
});

test('failed undo leaves the generated report intact', async () => {
  const e = environment(), d = e.day(); e.ctx.renderReport(d);
  const generating = e.click('report-gen');
  e.requests[0].resolve({ text: 'generated' }); await generating;
  const html = e.card.innerHTML, undoing = e.toasts.at(-1).action.run();
  e.requests[1].reject(new Error('failed')); await undoing;
  assert.equal(d.report.text, 'generated');
  assert.equal(e.card.innerHTML, html);
  assert.equal(e.alerts.length, 1);
});

test('undo does not discard edits begun while the request was pending', async () => {
  const e = environment(), d = e.day(); e.ctx.renderReport(d);
  const generating = e.click('report-gen');
  e.requests[0].resolve({ text: 'generated' }); await generating;
  const undoing = e.toasts.at(-1).action.run();
  e.edit(d); const ta = e.input('new unsaved text');
  e.requests[1].resolve({}); await undoing;
  assert.equal(e.elements['#report-text'], ta);
  assert.equal(e.kept(d.date), 'new unsaved text');
});

function quitEnvironment() {
  const elements = Object.fromEntries(['#app-quit', '#status', '#pause', '#notices'].map(k => [k, element()]));
  const requests = [], alerts = [], stopped = [], state = {}, view = { innerHTML: 'settings' };
  const ctx = vm.createContext({ $: s => elements[s], state, view, confirm: () => true,
    alert: text => alerts.push(text), clearTimeout: () => stopped.push('refresh'), clearInterval: () => stopped.push('status'),
    api: { post: () => new Promise((resolve, reject) => requests.push({ resolve, reject })) },
  });
  vm.runInContext(section('  $("#app-quit").addEventListener(', '  bindRestore();'), ctx);
  return { elements, requests, alerts, stopped, state, view,
    click: () => elements['#app-quit'].events.click({ currentTarget: elements['#app-quit'] }), ctx };
}
for (const error of ['HTTP 403', 'Network connection lost']) {
  test(`quit ${error} keeps settings and permits retry`, async () => {
    const e = quitEnvironment(); const quit = e.click();
    e.requests[0].reject(new Error(error)); await quit;
    assert.equal(e.view.innerHTML, 'settings');
    assert.equal(e.elements['#app-quit'].disabled, false);
    assert.ok(!e.state.quitting);
    assert.equal(e.stopped.length, 0);
    assert.equal(e.alerts.length, 1);
  });
}
test('quit requires acknowledgement and prevents duplicate requests', async () => {
  const e = quitEnvironment(); const quit = e.click(); await e.click();
  assert.equal(e.requests.length, 1);
  e.requests[0].resolve({}); await quit;
  assert.ok(!e.state.quitting);
  const retry = e.click(); e.requests[1].resolve({ ok: true }); await retry;
  assert.equal(e.state.quitting, true);
  assert.equal(e.elements['#status'].textContent, '終了しています');
  assert.equal(e.elements['#pause'].disabled, true);
  assert.equal(e.stopped.length, 2);
  assert.match(e.view.innerHTML, /終了しています/);
  assert.doesNotMatch(e.view.innerHTML, /終了しました/);
});

test('status requests do not resume after quit has been accepted', async () => {
  let finish, rendered = 0, calls = 0;
  const state = {};
  const ctx = vm.createContext({ state, cloud: null, renderStatus: () => rendered++,
    api: { get: () => { calls++; return new Promise(resolve => { finish = resolve; }); } },
  });
  vm.runInContext(section('async function refreshStatus()', '\n}\n') + '\n}', ctx);
  const pending = ctx.refreshStatus(); state.quitting = true;
  finish({}); await pending; await ctx.refreshStatus();
  assert.equal(rendered, 0); assert.equal(calls, 1);
});
