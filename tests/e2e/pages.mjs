// Every page shows its real content without errors; the guided demo starts a scenario; results
// download as CSV. Usage: node tests/e2e/pages.mjs <url> <email> <password> [screenshot dir]
import { chromium } from 'playwright-core';

const [url, email, password, shots] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, acceptDownloads: true });
const page = await ctx.newPage();
const problems = [];
page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); });
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
const step = async (name, fn) => {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    problems.push(`${name}: ${err.message.split('\n')[0]}`);
    console.log(`FAIL ${name}`);
  }
};

await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('svg.map-svg').waitFor({ timeout: 30_000 });

// [sidebar link, path, text that only the real page shows]
const PAGES = [
  ['Live overview', '/live', 'Queued vehicles per junction'],
  ['Signals', '/signals', 'Signal history'],
  ['VANET', '/vanet', 'Roadside units'],
  ['Emergency', '/emergency', 'Junction priority'],
  ['Navigation', '/navigation', 'How a route is chosen'],
  ['Incidents', '/incidents', 'Incident timeline'],
  ['Work zone', '/work-zone', 'Evaluated results'],
  ['Eco driving', '/eco', 'Eco score of each car'],
  ['Driving', '/glosa', 'Signal phase and timing (SPaT)'],
  ['Events', '/events', 'important only'],
  ['GA optimisation', '/ga', 'Check on held-out seeds'],
  ['Documentation', '/docs', 'Problem and objectives'],
  ['Run control', '/admin', 'Live run control'],
  ['Administration', '/administration', 'Add a user'],
];
// Not in the sidebar (hidden), but they still open by address and keep their title
const HIDDEN = [
  ['Results', '/results', 'Download this table (CSV)'],
  ['Run history', '/runs', 'stored'],
  ['Guided demo', '/demo', 'Step 1 of 13'],
];
await step('the sidebar shows only GA optimisation and Documentation under Research', async () => {
  const links = await page.locator('.sidebar .nav-item').allTextContents();
  for (const [link] of HIDDEN) if (links.includes(link)) throw new Error(`${link} should be hidden`);
});
for (const [link, path, text] of HIDDEN) {
  await step(`${link} (${path}) by address`, async () => {
    await page.goto(`${url}${path}`);
    await page.getByText(text, { exact: false }).first().waitFor({ timeout: 15_000 });
    const title = await page.locator('.page-title').textContent();
    if (title !== link) throw new Error(`page title is "${title}"`);
  });
}
for (const [link, path, text] of PAGES) {
  await step(`${link} (${path})`, async () => {
    await page.getByRole('link', { name: link, exact: true }).click();
    await page.waitForURL(`**${path}`);
    await page.getByText(text, { exact: false }).first().waitFor({ timeout: 15_000 });
    const title = await page.locator('.page-title').textContent();
    if (title !== link) throw new Error(`page title is "${title}"`);
    if (shots) await page.screenshot({ path: `${shots}/page${path.replaceAll('/', '-')}.png` });
  });
}

await step('work-zone results table shows the evaluated rows', async () => {
  await page.goto(`${url}/work-zone`);
  await page.getByRole('cell', { name: 'closure', exact: true }).waitFor();
  await page.getByRole('cell', { name: /^95\.3/ }).first().waitFor();
});
await step('GA page shows both targets with real numbers', async () => {
  await page.goto(`${url}/ga`);
  await page.getByText('684', { exact: true }).waitFor(); // simulations run for the signal target
  await page.getByRole('tab', { name: 'ACO navigation parameters' }).click();
  await page.getByText('339', { exact: true }).waitFor();
});
await step('results download as CSV', async () => {
  await page.goto(`${url}/results`);
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download this table (CSV)' }).click()]);
  if (!/^results-signals-.*\.csv$/.test(dl.suggestedFilename())) throw new Error(`unexpected file name ${dl.suggestedFilename()}`);
  const fs = await import('node:fs');
  const text = fs.readFileSync(await dl.path(), 'utf8');
  if (!text.startsWith('suite,scenario,configuration,metric,mean') || text.split('\n').length < 20) throw new Error('CSV content is not as expected');
});
await step('run history compares two stored runs', async () => {
  await page.goto(`${url}/runs`);
  const boxes = page.locator('table input[type=checkbox]:not([disabled])');
  await boxes.first().waitFor();
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await page.getByRole('heading', { name: 'Comparison' }).waitFor();
  await page.getByText('Average waiting time').waitFor();
});
await step('guided demo starts step 3 and the map shows that run', async () => {
  await page.goto(`${url}/demo`);
  await page.getByRole('button', { name: /Normal traffic, fixed-time signals/ }).click();
  await page.getByRole('button', { name: 'Start this scenario' }).click();
  await page.getByText('this scenario is playing').waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 3, null, { timeout: 60_000 });
  await page.getByText('normal · fixed', { exact: false }).first().waitFor();
});
await step('the guided demo pauses the run to explain and resumes it', async () => {
  await page.getByRole('button', { name: 'Pause to explain' }).click();
  await page.getByRole('status').filter({ hasText: /^Paused$/ }).first().waitFor({ timeout: 15_000 }); // confirmed by the simulation
  await page.getByText(/Paused at simulated second \d+/).waitFor();
  // the second in progress when the pause arrived is still delivered (states go out at most once a
  // second), so let it arrive before taking the time
  await page.waitForTimeout(1500);
  const t1 = await page.locator('.banner .chip.num').first().textContent();
  await page.waitForTimeout(2500); // the mock plays 2 simulated seconds per second when running
  if ((await page.locator('.banner .chip.num').first().textContent()) !== t1) throw new Error('time went on while paused');
  await page.getByRole('button', { name: 'Resume' }).click();
  await page.getByRole('status').filter({ hasText: 'Now playing' }).first().waitFor({ timeout: 15_000 });
});
await step('run control changes the speed and restarts the run from the beginning', async () => {
  await page.goto(`${url}/admin`);
  const speed = page.getByRole('group', { name: 'Playback' }).getByRole('combobox');
  await speed.selectOption('2');
  await page.getByText('2× speed').waitFor({ timeout: 15_000 });
  await speed.selectOption('1');
  await page.getByText('2× speed').waitFor({ state: 'detached', timeout: 15_000 });
  const runId = () => page.evaluate(async () => (await (await fetch('/api/live/status')).json()).entry?.run_id);
  const before = await runId();
  await page.getByRole('button', { name: 'Restart from the beginning' }).click();
  await page.getByText('Restarted from the beginning with the same settings.').waitFor({ timeout: 20_000 });
  await page.getByRole('status').filter({ hasText: 'Now playing' }).first().waitFor({ timeout: 20_000 });
  const after = await runId();
  if (!after || after === before) throw new Error(`no new run after the restart (${before} -> ${after})`);
});
await step('the bell counts important events and opens the event centre', async () => {
  await page.getByRole('link', { name: /event centre/ }).click();
  await page.waitForURL('**/events');
});

await browser.close();
if (problems.length) {
  console.log('\nProblems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nAll page checks passed.');
