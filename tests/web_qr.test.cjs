const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../lablog/web');
const app = fs.readFileSync(path.join(root, 'app.js'), 'utf8').replace(/\r\n/g, '\n');
const setup = fs.readFileSync(path.join(root, 'setup.js'), 'utf8').replace(/\r\n/g, '\n');
const encoder = fs.readFileSync(path.join(root, 'vendor/qrcode.js'), 'utf8');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function func(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
function env(cloud = {}) {
  const card = { innerHTML: '' }, button = { addEventListener() {} };
  const ctx = vm.createContext({ URL, esc, api: { get: async () => cloud },
    $: sel => sel === '#cloud-state' ? card : button, fmtWhen: () => 'now',
    setupChoice: () => '', setupToggle: () => '', bindSetupInputs() {}, drawSetupBody() {},
  });
  vm.runInContext(encoder, ctx);
  vm.runInContext(func(app, 'viewerLinkHTML'), ctx);
  vm.runInContext('async ' + func(app, 'loadCloudState'), ctx);
  vm.runInContext('async ' + func(setup, 'setupAccount'), ctx);
  return { ctx, card };
}

for (const url of ['https://isct-stlab.github.io/lablog/', 'https://isct-stlab.github.io/lablog/beta/', 'https://example.invalid/研究室/?q=日報&x="test"']) {
  test(`QR and link for ${url}`, () => {
    const { ctx } = env();
    const html = ctx.viewerLinkHTML(url);
    assert.match(html, /class="viewer-qr"/);
    assert.match(html, /<svg /);
    assert.match(html, /fill="white"/);
    assert.match(html, /fill="black"/);
    assert.ok(html.includes(`href="${esc(new URL(url).href)}"`));
    assert.ok(html.includes(esc(url)));
  });
}
test('unavailable encoder and long URLs retain a usable text link', () => {
  const { ctx } = env();
  for (const url of ['https://example.invalid/' + 'a'.repeat(3000), 'https://example.invalid/']) {
    ctx.qrcode = undefined;
    const html = ctx.viewerLinkHTML(url);
    assert.doesNotMatch(html, /<svg|viewer-link-hint/);
    assert.ok(html.includes(`href="${url}"`));
  }
});
test('invalid URLs never become QR codes or executable links', () => {
  const { ctx } = env();
  for (const url of ['', undefined, 'javascript:alert(1)', 'data:text/html,<script>x</script>', '<img onerror=alert(1)>']) {
    assert.equal(ctx.viewerLinkHTML(url), '');
  }
});
for (const status of [{}, { folder: 'synthetic' }, { last_error: 'sync failed' }, { blocked_by: { name: 'Other PC', exported: 1 } }]) {
  test(`settings keeps the QR available: ${JSON.stringify(status)}`, async () => {
    const e = env({ enabled: true, viewer_url: 'https://isct-stlab.github.io/lablog/', ...status });
    await e.ctx.loadCloudState();
    assert.match(e.card.innerHTML, /class="viewer-qr"/);
  });
}
test('settings hides the QR when integration is disabled', async () => {
  const e = env({ enabled: false, viewer_url: 'https://isct-stlab.github.io/lablog/' });
  await e.ctx.loadCloudState();
  assert.equal(e.card.innerHTML, '');
});
test('setup shows the QR only after choosing integration', async () => {
  const e = env({ viewer_url: 'https://isct-stlab.github.io/lablog/' });
  const el = { isConnected: true, innerHTML: '' };
  await e.ctx.setupAccount(el, { cloud: true });
  assert.match(el.innerHTML, /class="viewer-qr"/);
  await e.ctx.setupAccount(el, { cloud: false });
  assert.doesNotMatch(el.innerHTML, /class="viewer-qr"/);
});
