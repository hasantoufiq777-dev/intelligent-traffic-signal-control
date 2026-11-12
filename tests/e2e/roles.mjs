// Browser journeys per role. Creates a researcher and a viewer as the administrator, then checks
// what each one sees and may do, including refused actions and a reconnect; deletes them at the end.
// Usage: node tests/e2e/roles.mjs <url> <admin email> <admin password>
import { chromium } from 'playwright-core';

const [url, adminEmail, adminPassword] = process.argv.slice(2);
const stamp = Date.now().toString(36);
const PW = `role-test-${stamp}-pw`;
const people = { RESEARCHER: `researcher-${stamp}@test.local`, VIEWER: `viewer-${stamp}@test.local` };
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const problems = [];
const step = async (name, fn) => {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    problems.push(`${name}: ${err.message.split('\n')[0]}`);
    console.log(`FAIL ${name}`);
    if (process.env.SHOTS) { // failure screenshots only when asked for (SHOTS=<dir>)
      for (const [i, c] of browser.contexts().entries()) {
        for (const [j, pg] of c.pages().entries()) await pg.screenshot({ path: `${process.env.SHOTS}/fail-${problems.length}-${i}${j}.png` }).catch(() => {});
      }
    }
  }
};

async function signedIn(email, password) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => problems.push(`${email} pageerror: ${e.message}`));
  page.on('console', (m) => {
    // 400/401/403/409 are provoked on purpose (refused actions, a spent reset link)
    if (m.type() === 'error' && !/status of (400|401|403|409)/.test(m.text())) problems.push(`${email} console: ${m.text()}`);
  });
  await page.goto(`${url}/login`);
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.locator('svg.map-svg').waitFor({ timeout: 30_000 });
  return { ctx, page };
}

/** Call the API from inside the page, as that user (cookie + CSRF token). */
const call = (page, method, path, body) => page.evaluate(async ([m, p, b]) => {
  const me = await (await fetch('/api/auth/me')).json();
  const res = await fetch(p, { method: m, headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf ?? '' },
    body: b === undefined ? undefined : JSON.stringify(b) });
  return { status: res.status, body: await res.json().catch(() => null) };
}, [method, path, body]);

const admin = await signedIn(adminEmail, adminPassword);
const ids = {};
// Begin from a stopped simulation, so the researcher may start a run (a run another user started could not be replaced)
await call(admin.page, 'POST', '/api/live/stop');
await step('administrator creates a researcher and a viewer', async () => {
  for (const [role, email] of Object.entries(people)) {
    const r = await call(admin.page, 'POST', '/api/users', { email, name: `${role[0]}${role.slice(1).toLowerCase()} test`, password: PW, role });
    if (r.status !== 201) throw new Error(`${role}: ${r.status}`);
    ids[role] = r.body.user.id;
  }
});

// ---------------------------------------------------------------- viewer
const viewer = await signedIn(people.VIEWER, PW);
const vp = viewer.page;
await step('viewer sees monitoring and research, not run control or administration', async () => {
  const links = await vp.locator('.sidebar .nav-item').allTextContents();
  for (const want of ['Live traffic', 'Signals', 'Results', 'Guided demo', 'Documentation']) if (!links.includes(want)) throw new Error(`missing ${want}`);
  for (const not of ['Run control', 'Administration']) if (links.includes(not)) throw new Error(`should not see ${not}`);
});
await step('viewer opening /admin or /administration directly gets a refusal', async () => {
  for (const p of ['/admin', '/administration']) {
    await vp.goto(url + p);
    await vp.getByText('You do not have permission to open this page.').waitFor();
  }
});
await step('the server refuses the viewer\'s start, stop, user and settings requests', async () => {
  const tries = [['POST', '/api/live/start', { scenario: 'normal', controller: 'fixed' }], ['POST', '/api/live/stop'],
    ['GET', '/api/users'], ['POST', '/api/users', { email: 'x@test.local', name: 'X', password: PW, role: 'ADMIN' }],
    ['PATCH', `/api/users/${ids.VIEWER}`, { role: 'ADMIN' }], ['PUT', '/api/settings', { allow_registration: true }],
    ['GET', '/api/audit'], ['POST', '/api/driver', { enabled: false }]];
  for (const [m, p, b] of tries) {
    const r = await call(vp, m, p, b);
    if (r.status !== 403) throw new Error(`${m} ${p} answered ${r.status}`);
  }
});
await step('the guided demo tells a viewer that starting needs another role', async () => {
  await vp.goto(`${url}/demo`);
  await vp.getByRole('button', { name: /Normal traffic, fixed-time signals/ }).click();
  await vp.getByText('Starting a scenario needs a researcher or administrator account').waitFor();
  if (await vp.getByRole('button', { name: /Start this scenario/ }).count()) throw new Error('start button shown to a viewer');
});
await step('viewer can follow a car and read its details', async () => {
  await vp.goto(url);
  await vp.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 2, null, { timeout: 60_000 });
  await vp.locator('svg.map-svg g.car-g').first().dispatchEvent('click'); // cars move every second and are tiny
  await vp.getByRole('heading', { name: /^Vehicle / }).waitFor({ timeout: 15_000 });
});
await step('viewer reconnects after the connection drops', async () => {
  await viewer.ctx.setOffline(true);
  try {
    // An open WebSocket is not closed by going offline; with no data flowing (simulation stopped)
    // the client notices through the missing heartbeat: up to 25 s ping interval + 20 s timeout.
    await vp.getByText(/Reconnecting|Offline/).first().waitFor({ timeout: 60_000 });
  } finally {
    await viewer.ctx.setOffline(false);
  }
  await vp.getByRole('status').filter({ hasText: 'Live' }).first().waitFor({ timeout: 30_000 });
});

// ---------------------------------------------------------------- researcher
const researcher = await signedIn(people.RESEARCHER, PW);
const rp = researcher.page;
await step('researcher sees run control but not administration', async () => {
  const links = await rp.locator('.sidebar .nav-item').allTextContents();
  if (!links.includes('Run control')) throw new Error('missing Run control');
  if (links.includes('Administration')) throw new Error('should not see Administration');
});
let researcherRun = null;
await step('researcher starts a run from run control and it plays', async () => {
  await rp.goto(`${url}/admin`);
  await rp.getByLabel(/^scenario/).selectOption('emergency');
  await rp.getByLabel(/^controller/).selectOption('adaptive_ev_vanet');
  await rp.getByRole('button', { name: 'Start live run' }).click();
  await rp.getByText(/Started run live-/).waitFor({ timeout: 20_000 });
  researcherRun = (await call(rp, 'GET', '/api/live/status')).body.entry;
  if (researcherRun.started_by?.id !== ids.RESEARCHER) throw new Error('run not recorded as started by the researcher');
  await rp.getByText('The current run was started by you.').waitFor({ timeout: 15_000 });
});
await step('another researcher cannot stop or replace it, the administrator can', async () => {
  const other = `other-${stamp}@test.local`;
  const made = await call(admin.page, 'POST', '/api/users', { email: other, name: 'Other researcher', password: PW, role: 'RESEARCHER' });
  ids.OTHER = made.body.user.id;
  const o = await signedIn(other, PW);
  const stop = await call(o.page, 'POST', '/api/live/stop');
  const start = await call(o.page, 'POST', '/api/live/start', { scenario: 'normal', controller: 'fixed' });
  await o.ctx.close();
  if (stop.status !== 403 || start.status !== 409) throw new Error(`stop ${stop.status}, start ${start.status}`);
});
await step('researcher compares runs in the run history', async () => {
  await rp.goto(`${url}/runs`);
  const boxes = rp.locator('table input[type=checkbox]:not([disabled])');
  await boxes.nth(1).waitFor();
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await rp.getByRole('heading', { name: 'Comparison' }).waitFor();
});
await step('researcher stops the own run', async () => {
  await rp.goto(`${url}/admin`);
  await rp.getByRole('button', { name: 'Stop', exact: true }).click();
  await rp.getByText(/Stopped\./).waitFor({ timeout: 20_000 });
});

// ---------------------------------------------------------------- administrator actions on these users
await step('disabling the viewer signs them out at once', async () => {
  const r = await call(admin.page, 'PATCH', `/api/users/${ids.VIEWER}`, { status: 'disabled' });
  if (r.status !== 200) throw new Error(`disable answered ${r.status}`);
  await vp.reload();
  await vp.getByRole('heading', { name: 'Sign in' }).waitFor({ timeout: 15_000 });
  await vp.getByLabel('Email').fill(people.VIEWER);
  await vp.getByLabel('Password').fill(PW);
  await vp.getByRole('button', { name: 'Sign in' }).click();
  await vp.getByText('This account is disabled').waitFor();
});
await step('a password-reset link sets a new password once', async () => {
  const link = await call(admin.page, 'POST', `/api/users/${ids.RESEARCHER}/reset-link`);
  await rp.context().clearCookies();
  await rp.goto(url + link.body.path);
  await rp.getByLabel(/^New password/).fill(`${PW}-new`);
  await rp.getByLabel(/^Repeat password/).fill(`${PW}-new`);
  await rp.getByRole('button', { name: 'Set new password' }).click();
  await rp.getByText('Your password was changed').waitFor();
  await rp.goto(url + link.body.path);
  await rp.getByLabel(/^New password/).fill(`${PW}-again`);
  await rp.getByLabel(/^Repeat password/).fill(`${PW}-again`);
  await rp.getByRole('button', { name: 'Set new password' }).click();
  await rp.getByText('invalid or has expired').waitFor();
});

// Clean up: delete the test accounts; let the playlist go on
for (const id of Object.values(ids)) await call(admin.page, 'DELETE', `/api/users/${id}`);
await call(admin.page, 'POST', '/api/live/start', { scenario: 'normal', network: 'grid2x2', controller: 'adaptive_vanet', seed: 1, label: 'After role tests' });
await browser.close();
if (problems.length) {
  console.log('\nProblems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nAll role journeys passed.');
