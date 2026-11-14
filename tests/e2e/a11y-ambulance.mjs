// Accessibility audit of the live map at the moment an ambulance has signal priority (the junction
// shows an AMBULANCE note then). The general audit (a11y.mjs) only sees this state by chance.
// Starts the "emergency" scenario as the given user (administrator or researcher).
// Usage: node tests/e2e/a11y-ambulance.mjs <url> <email> <password>
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const [url, email, password] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, bypassCSP: true });
const page = await ctx.newPage();
await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('svg.map-svg').waitFor();
const csrf = await page.evaluate(async () => (await (await fetch('/api/auth/me')).json()).csrf);
const st = await page.evaluate(async (c) => (await fetch('/api/live/start', { method: 'POST', headers: { 'content-type': 'application/json', 'x-csrf-token': c }, body: JSON.stringify({ scenario: 'emergency', controller: 'adaptive_ev_vanet' }) })).status, csrf);
if (st !== 202) throw new Error(`starting the emergency scenario answered ${st}`);
let failed = false;
await page.goto(url);
for (const theme of ['light', 'dark']) {
  await page.evaluate((t) => localStorage.setItem('itsc-theme', t), theme);
  await page.reload();
  await page.locator('.junction-note').first().waitFor({ timeout: 240_000 }); // an ambulance has priority
  await page.addScriptTag({ content: AXE });
  const r = await page.evaluate(async () => (await window.axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] } })).violations.map((v) => `${v.id} ${v.nodes.map((n) => n.target.join(' ')).join(',')}`));
  const label = await page.locator('.junction[aria-label*="ambulance"]').first().getAttribute('aria-label');
  console.log(`${theme}: ${label} -> ${r.length ? r.join('; ') : 'no violations'}`);
  if (r.length || !/ambulance/.test(label ?? '')) failed = true;
}
await browser.close();
process.exit(failed ? 1 : 0);
