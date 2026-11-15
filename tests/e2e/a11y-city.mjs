// Accessibility audit of the supervisor features in the states the general audit (a11y.mjs) does not
// reach: the city map with its legend, the route explorer with results, the driving panel with the
// fuel-efficient speed advice, and the driver perspective (3D view with its speed display), in both
// themes. Starts the "city" scenario as the given administrator or researcher, and drives a car.
// Usage: node tests/e2e/a11y-city.mjs <url> <email> <password>
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const [url, email, password] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 }, bypassCSP: true })).newPage();
await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('main').waitFor();
const csrf = await page.evaluate(async () => (await (await fetch('/api/auth/me')).json()).csrf);
const st = await page.evaluate(async (c) => (await fetch('/api/live/start', { method: 'POST',
  headers: { 'content-type': 'application/json', 'x-csrf-token': c },
  body: JSON.stringify({ scenario: 'city', controller: 'adaptive_vanet', nav: 'astar' }) })).status, csrf);
if (st !== 202) throw new Error(`starting the city scenario answered ${st}`);

let failed = false;
const audit = async (label) => {
  await page.addScriptTag({ content: AXE });
  const r = await page.evaluate(async (tags) => (await window.axe.run(document, { runOnly: { type: 'tag', values: tags } }))
    .violations.map((v) => `${v.id} ${v.nodes.map((n) => n.target.join(' ')).join(',')}`), TAGS);
  console.log(`${label}: ${r.length ? r.join('; ') : 'no violations'}`);
  if (r.length) failed = true;
};

for (const theme of ['light', 'dark']) {
  await page.goto(url);
  await page.evaluate((t) => { localStorage.setItem('itsc-theme', t); localStorage.setItem('itsc-view', 'map'); }, theme);
  await page.reload();
  await page.locator('.map-svg').waitFor({ timeout: 60_000 });
  await page.waitForFunction(() => document.querySelectorAll('.car-g').length > 10, null, { timeout: 90_000 });
  await audit(`${theme}: city map and legend`);

  await page.getByRole('tab', { name: 'Routes' }).click();
  const rx = page.locator('section[aria-labelledby="routes-h"]');
  await rx.getByRole('button', { name: 'Find routes' }).click();
  await rx.getByRole('region', { name: 'Route alternatives' }).waitFor({ timeout: 15_000 });
  await audit(`${theme}: route explorer with results`);

  await page.getByRole('tab', { name: 'Vehicle' }).click();
  const dp = page.locator('section[aria-labelledby="drive-h"]');
  await dp.getByRole('button', { name: 'Drive', exact: true }).waitFor({ timeout: 60_000 }); // the seat is free
  await dp.getByRole('button', { name: 'Drive', exact: true }).click();
  await dp.getByLabel('Start road').selectOption('SWbottom0');
  await dp.getByLabel('Destination road').selectOption('right1NE');
  await dp.getByRole('button', { name: 'Take the car' }).click();
  await page.locator('.advice').filter({ hasText: 'Estimated fuel-efficient speed' }).waitFor({ timeout: 90_000 });
  await audit(`${theme}: driving with the fuel-efficient speed`);

  await page.getByRole('button', { name: 'Driver perspective' }).click();
  try {
    await page.locator('.driver-hud').waitFor({ timeout: 30_000 });
  } catch (err) {
    if (process.env.SHOT) await page.screenshot({ path: process.env.SHOT });
    throw err;
  }
  await audit(`${theme}: driver perspective`);
  await page.getByRole('button', { name: 'Traffic control view' }).click();
  await page.getByRole('button', { name: 'Hand the car back' }).click(); // free the seat for the next theme
  await page.getByRole('button', { name: 'Close' }).click().catch(() => {});
}
await browser.close();
process.exit(failed ? 1 : 0);
