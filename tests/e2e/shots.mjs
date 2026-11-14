// Screenshots of the live page during a chosen run, at several viewport widths.
// Usage: node tests/e2e/shots.mjs <url> <email> <password> <out dir> <scenario> <wait until sim time s> [widths]
import { chromium } from 'playwright-core';

const [url, email, password, out, scenario = 'accident_zone', untilT = '300', widths = '1440,390'] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
const page = await ctx.newPage();
const problems = [];
page.on('console', (m) => { if (m.type() === 'error') problems.push(m.text()); });
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));

await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('svg.map-svg').waitFor({ timeout: 30_000 });

const started = await page.evaluate(async (sc) => {
  const me = await (await fetch('/api/auth/me')).json();
  const res = await fetch('/api/live/start', { method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf-token': me.csrf },
    body: JSON.stringify({ scenario: sc, controller: 'adaptive_ev_vanet', nav: 'none', seed: 1, ev_routing: 'on' }) });
  return res.status;
}, scenario);
console.log('start run:', started);
// The new run first (its clock starts again), then the wanted moment
await page.waitForFunction(() => {
  const m = document.querySelector('.card-head .small.muted')?.textContent?.match(/t = (\d+)/);
  return m && Number(m[1]) < 40;
}, null, { timeout: 60_000, polling: 250 });
await page.waitForFunction((t) => {
  const m = document.querySelector('.card-head .small.muted')?.textContent?.match(/t = (\d+)/);
  return m && Number(m[1]) >= t;
}, Number(untilT), { timeout: 240_000, polling: 500 });

for (const w of widths.split(',').map(Number)) {
  await page.setViewportSize({ width: w, height: w < 700 ? 844 : 950 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/live-${scenario}-${w}.png`, fullPage: w < 700 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log(`width ${w}: horizontal overflow ${overflow}px`);
}
await browser.close();
console.log(problems.length ? `console problems:\n${problems.join('\n')}` : 'no console errors');
