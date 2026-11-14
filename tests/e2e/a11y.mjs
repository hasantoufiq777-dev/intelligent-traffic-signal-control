// Automated accessibility audit with axe-core (WCAG 2.0/2.1/2.2 A and AA rules) on every page, in
// both themes and at a phone width, plus keyboard checks. Writes the findings to a JSON file.
// Usage: node tests/e2e/a11y.mjs <url> <email> <password> [out.json]
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';

const require = createRequire(import.meta.url);
const AXE = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const [url, email, password, out] = process.argv.slice(2);
const PAGES = ['/', '/live', '/signals', '/vanet', '/emergency', '/navigation', '/incidents', '/work-zone', '/glosa', '/events',
  '/demo', '/results', '/ga', '/runs', '/docs', '/admin', '/administration', '/account'];
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

const browser = await chromium.launch({ channel: 'msedge', headless: true });
// bypassCSP only lets the test inject axe; the site itself keeps its policy
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, bypassCSP: true });
const page = await ctx.newPage();
const findings = [];

async function audit(where) {
  await page.addScriptTag({ content: AXE });
  const res = await page.evaluate(async (tags) => {
    const r = await window.axe.run(document, { runOnly: { type: 'tag', values: tags }, resultTypes: ['violations'] });
    return r.violations.map((v) => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
      examples: v.nodes.slice(0, 3).map((n) => n.target.join(' ')) }));
  }, TAGS);
  for (const v of res) findings.push({ where, ...v });
  return res;
}

async function auditSignedOut(theme) {
  await page.goto(`${url}/login`);
  await page.evaluate((t) => localStorage.setItem('itsc-theme', t), theme);
  for (const p of ['/login', '/forgot-password', '/reset-password?token=x']) {
    await page.goto(url + p);
    await page.getByRole('heading', { level: 1 }).waitFor();
    await audit(`${p} (${theme})`);
  }
}

await auditSignedOut('dark');
await auditSignedOut('light');
await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('svg.map-svg').waitFor({ timeout: 30_000 });

for (const theme of ['dark', 'light']) {
  await page.evaluate((t) => localStorage.setItem('itsc-theme', t), theme);
  for (const p of PAGES) {
    await page.goto(url + p);
    await page.locator('main').waitFor();
    await page.waitForTimeout(1200);
    await audit(`${p} (${theme})`);
  }
}
await page.setViewportSize({ width: 390, height: 844 });
for (const p of ['/', '/signals', '/demo']) {
  await page.goto(url + p);
  await page.waitForTimeout(1000);
  await audit(`${p} (dark, 390 px)`);
}

// Keyboard: skip link first, every sidebar link reachable, focus always visible
await page.setViewportSize({ width: 1440, height: 950 });
await page.goto(url);
await page.locator('main').waitFor();
const keyboard = { no_ring: undefined };
await page.keyboard.press('Tab');
keyboard.skip_link_first = await page.evaluate(() => document.activeElement?.classList.contains('skip-link'));
await page.keyboard.press('Enter');
keyboard.skip_link_moves_focus = await page.evaluate(() => document.activeElement?.id === 'main');
const seen = new Set();
let invisible = 0;
await page.evaluate(() => document.body.focus());
for (let i = 0; i < 80; i += 1) {
  await page.keyboard.press('Tab');
  const info = await page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return null;
    const s = getComputedStyle(el);
    const visible = s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0;
    return { text: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40), nav: Boolean(el.closest('.sidebar')), visible };
  });
  if (!info) continue;
  if (info.nav) seen.add(info.text);
  if (!info.visible) { invisible += 1; keyboard.no_ring ??= []; keyboard.no_ring.push(info.text); }
}
keyboard.sidebar_links_reached = seen.size;
keyboard.focus_without_visible_outline = invisible;
// Map: keyboard zoom and pan
await page.locator('svg.map-svg').focus();
const before = await page.locator('svg.map-svg > g').getAttribute('transform');
await page.keyboard.press('+');
await page.keyboard.press('ArrowLeft');
keyboard.map_keyboard_zoom_pan = before !== (await page.locator('svg.map-svg > g').getAttribute('transform'));
await page.keyboard.press('0');

await browser.close();
const byRule = {};
for (const f of findings) {
  byRule[f.id] ??= { impact: f.impact, help: f.help, pages: [], nodes: 0, examples: f.examples };
  byRule[f.id].pages.push(f.where);
  byRule[f.id].nodes += f.nodes;
}
const summary = { date: new Date().toISOString(), axe: require('axe-core/package.json').version, tags: TAGS,
  audits: PAGES.length * 2 + 6 + 3, violations_total: findings.length, rules: byRule, keyboard };
if (out) fs.writeFileSync(out, JSON.stringify(summary, null, 2));
console.log(JSON.stringify({ ...summary, rules: Object.fromEntries(Object.entries(byRule).map(([k, v]) =>
  [k, { impact: v.impact, help: v.help, pages: v.pages.length, nodes: v.nodes, examples: v.examples }])) }, null, 2));
