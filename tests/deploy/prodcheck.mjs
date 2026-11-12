// Production-configuration check against a running deployment (HTTPS front + gateway with
// NODE_ENV=production). Prints one line per check and exits 1 if any fails.
// Usage: node tests/deploy/prodcheck.mjs <https url> <admin email> <admin password> [--insecure]
//   --insecure accepts a self-signed certificate (local test only)
import https from 'node:https';
import { createRequire } from 'node:module';
import zlib from 'node:zlib';

const require = createRequire(new URL('../../server/package.json', import.meta.url));
const { io } = require('socket.io-client');
const { chromium } = await import('playwright-core');

const args = process.argv.slice(2);
const insecure = args.includes('--insecure');
const [base, email, password] = args.filter((a) => !a.startsWith('--'));
const origin = new URL(base).origin;
const host = new URL(base).host;
const results = [];
const check = async (name, fn) => {
  try {
    const note = await fn();
    results.push({ name, ok: true });
    console.log(`ok   ${name}${note ? ` (${note})` : ''}`);
  } catch (err) {
    results.push({ name, ok: false, error: err.message });
    console.log(`FAIL ${name}: ${err.message}`);
  }
};
const expect = (cond, msg) => { if (!cond) throw new Error(msg); };

/** Raw HTTPS request (no automatic decompression), so headers and bodies are seen as sent. */
function req(method, path, { headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const r = https.request(new URL(path, base), { method, headers, rejectUnauthorized: !insecure }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, raw: Buffer.concat(chunks) }));
    });
    r.on('error', reject);
    if (body !== undefined) r.write(typeof body === 'string' ? body : JSON.stringify(body));
    r.end();
  });
}
const json = (r) => JSON.parse(r.raw.toString('utf8') || 'null');
const post = (path, body, extra = {}) => req('POST', path, { headers: { 'content-type': 'application/json', origin, ...extra }, body });

let page = null;
await check('index page: security headers', async () => {
  const r = await req('GET', '/');
  page = r;
  expect(r.status === 200, `status ${r.status}`);
  const h = r.headers;
  const csp = h['content-security-policy'] ?? '';
  expect(csp.includes(`connect-src 'self' wss://${host} ws://${host}`), `connect-src not limited to this host: ${csp}`);
  expect(csp.includes("frame-ancestors 'none'") && csp.includes("object-src 'none'"), 'frame-ancestors/object-src');
  expect(/max-age=\d+/.test(h['strict-transport-security'] ?? ''), 'no HSTS');
  expect(h['x-content-type-options'] === 'nosniff', 'no nosniff');
  expect(!h['x-powered-by'], 'X-Powered-By present');
  expect(/no-cache/.test(h['cache-control'] ?? ''), `index.html cache-control: ${h['cache-control']}`);
  return 'CSP, HSTS, nosniff, no X-Powered-By, index not cached';
});

await check('build files: gzip and long-term caching', async () => {
  const js = /src="(\/assets\/[^"]+\.js)"/.exec(page.raw.toString())?.[1];
  expect(js, 'main script not found in index.html');
  const r = await req('GET', js, { headers: { 'accept-encoding': 'gzip' } });
  expect(r.headers['content-encoding'] === 'gzip', `content-encoding ${r.headers['content-encoding']}`);
  expect(/immutable/.test(r.headers['cache-control'] ?? '') && /max-age=31536000/.test(r.headers['cache-control']), r.headers['cache-control']);
  const size = zlib.gunzipSync(r.raw).length;
  return `${Math.round(r.raw.length / 1024)} KB sent for ${Math.round(size / 1024)} KB`;
});

let cookie = null;
let csrf = null;
await check('sign-in cookie is Secure, HttpOnly, SameSite=Lax', async () => {
  const r = await post('/api/auth/login', { email, password });
  expect(r.status === 200, `status ${r.status}`);
  const set = [r.headers['set-cookie'] ?? []].flat().find((c) => c.startsWith('itsc_sid='));
  expect(set, 'no session cookie');
  for (const attr of ['Secure', 'HttpOnly', 'SameSite=Lax']) expect(set.includes(attr), `missing ${attr}`);
  cookie = set.split(';')[0];
  csrf = json(r).csrf;
  return set.split(';').slice(1).map((s) => s.trim()).filter((s) => !s.startsWith('Expires') && !s.startsWith('Max-Age')).join('; ');
});

await check('requests from another site are refused', async () => {
  const r = await post('/api/auth/login', { email, password }, { origin: 'https://evil.example' });
  expect(r.status === 403 && json(r).code === 'bad_origin', `status ${r.status}`);
  const w = await req('POST', '/api/live/stop', { headers: { cookie, origin, 'content-type': 'application/json' } });
  expect(w.status === 403 && json(w).code === 'csrf', `write without CSRF token: ${w.status}`);
});

await check('errors are JSON without internal details', async () => {
  const bad = await post('/api/auth/login', '{"email": ');
  expect(bad.status === 400 && json(bad).code === 'invalid', `bad JSON: ${bad.status}`);
  const missing = await req('GET', '/api/does-not-exist', { headers: { cookie } });
  expect(missing.status === 404, `unknown route: ${missing.status}`);
  const anon = await req('GET', '/api/runs');
  expect(anon.status === 401, `no session: ${anon.status}`);
  for (const r of [bad, missing, anon]) expect(!/\bat .+\.js:\d+/.test(r.raw.toString()), 'stack trace in a response');
});

await check('the gateway runs in production mode', async () => {
  const r = await req('GET', '/api/system', { headers: { cookie } });
  expect(r.status === 200, `status ${r.status}`);
  const s = json(r);
  expect(s.env === 'production', `env ${s.env}`);
  return `node ${s.node}`;
});

const socketOutcome = (headers) => new Promise((resolve) => {
  const s = io(base, { transports: ['websocket'], forceNew: true, reconnection: false, rejectUnauthorized: !insecure, extraHeaders: headers });
  const done = (v) => { s.close(); resolve(v); };
  s.on('init', (m) => done(`init with ${m.network?.edges?.length ?? 0} roads`));
  s.on('connect_error', (e) => done(e.data?.code ?? e.message));
  setTimeout(() => done('timeout'), 15_000);
});
await check('secure WebSocket: session required, other sites refused', async () => {
  const ok = await socketOutcome({ Cookie: cookie, Origin: origin });
  expect(ok.startsWith('init'), `own site: ${ok}`);
  const evil = await socketOutcome({ Cookie: cookie, Origin: 'https://evil.example' });
  expect(evil === 'bad_origin', `other site: ${evil}`);
  const anon = await socketOutcome({ Origin: origin });
  expect(anon === 'unauthenticated', `no session: ${anon}`);
  return ok;
});

await check('browser: sign in over HTTPS, live cars on the map, no CSP violations', async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: insecure, viewport: { width: 1440, height: 950 } });
    const p = await ctx.newPage();
    const errors = [];
    p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    p.on('pageerror', (e) => errors.push(e.message));
    await p.goto(`${base}/login`);
    await p.getByLabel('Email').fill(email);
    await p.getByLabel('Password').fill(password);
    await p.getByRole('button', { name: 'Sign in' }).click();
    await p.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 0, null, { timeout: 60_000 });
    await p.reload();
    await p.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 0, null, { timeout: 60_000 });
    expect(errors.length === 0, errors.join(' | '));
    const cookies = await ctx.cookies();
    const sid = cookies.find((c) => c.name === 'itsc_sid');
    expect(sid?.secure && sid.httpOnly, 'session cookie flags in the browser');
    return 'cars shown after sign-in and after reload';
  } finally {
    await browser.close();
  }
});

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} production checks passed`);
process.exit(failed.length ? 1 : 0);
