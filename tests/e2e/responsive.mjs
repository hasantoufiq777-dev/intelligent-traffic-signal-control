// Responsive check: every page at every required width must have no horizontal page overflow,
// and no console errors. Usage: node tests/e2e/responsive.mjs <url> <email> <password> [out dir]
import { chromium } from 'playwright-core';

const [url, email, password, out] = process.argv.slice(2);
const WIDTHS = [1920, 1440, 1024, 768, 390, 360];
const PAGES = ['/', '/live', '/signals', '/vanet', '/emergency', '/navigation', '/incidents', '/work-zone', '/glosa', '/events',
  '/demo', '/results', '/ga', '/runs', '/docs', '/admin', '/administration', '/account'];
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const problems = [];
page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text()}`); });
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));

// Signed-out pages
for (const w of [1440, 360]) {
  await page.setViewportSize({ width: w, height: 800 });
  for (const p of ['/login', '/forgot-password']) {
    await page.goto(url + p);
    await page.getByRole('heading', { level: 1 }).waitFor();
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (over > 0) problems.push(`${p} @${w}: overflow ${over}px`);
  }
}
await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('svg.map-svg').waitFor({ timeout: 30_000 });

let checked = 0;
for (const w of WIDTHS) {
  await page.setViewportSize({ width: w, height: w < 700 ? 800 : 900 });
  for (const p of PAGES) {
    await page.goto(url + p);
    await page.locator('main').waitFor();
    await page.waitForTimeout(900);
    const r = await page.evaluate(() => ({
      over: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      title: document.querySelector('.page-title')?.textContent,
      drawerButton: getComputedStyle(document.querySelector('.topbar .only-mobile')).display !== 'none',
      sidebarVisible: document.querySelector('.sidebar').getBoundingClientRect().right > 0,
    }));
    checked += 1;
    if (r.over > 0) problems.push(`${p} @${w}: horizontal overflow ${r.over}px`);
    if (w <= 900 && (!r.drawerButton || r.sidebarVisible)) problems.push(`${p} @${w}: sidebar should be a closed drawer`);
    if (w > 900 && (r.drawerButton || !r.sidebarVisible)) problems.push(`${p} @${w}: sidebar should be visible`);
    if (out && p === '/' ) await page.screenshot({ path: `${out}/resp-live-${w}.png` });
  }
}
// The mobile drawer opens, navigates and closes
await page.setViewportSize({ width: 390, height: 800 });
await page.goto(url);
await page.getByRole('button', { name: 'Open navigation' }).click();
await page.getByRole('link', { name: 'Documentation' }).click();
await page.waitForURL('**/docs');
await page.waitForTimeout(500); // the drawer slides out
if (await page.evaluate(() => document.querySelector('.sidebar').getBoundingClientRect().right > 0)) problems.push('drawer stayed open after navigating');

await browser.close();
console.log(`${checked} page/width combinations checked`);
if (problems.length) {
  console.log('Problems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('No overflow, no console errors.');
