// Car following in the browser, against a gateway with the REAL simulation: a followed car shows the
// vehicle ahead (speed, acceleration, gap) with the advice for following it and the vehicle behind;
// the driven car can follow the car ahead (adaptive cruise, key F); the 3D view's HUD shows both.
// Usage: node tests/e2e/following.mjs <url> <email> <password> [screenshot folder]
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';

const AXE = fs.readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

const [url, email, password, shots] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 }, bypassCSP: true })).newPage();
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
    if (shots) await page.screenshot({ path: `${shots}/FAIL-${name.slice(0, 40).replace(/\W+/g, '_')}.png` }).catch(() => {});
  }
};
// axe (WCAG 2.2 AA) on the car-following card in both themes
const audit = async (label, selector) => {
  for (const theme of ['light', 'dark']) {
    await page.evaluate((t) => { document.documentElement.dataset.theme = t; }, theme);
    await page.waitForTimeout(300);
    await page.addScriptTag({ content: AXE });
    const r = await page.evaluate(async ([tags, sel]) => (await window.axe.run(sel, { runOnly: { type: 'tag', values: tags } }))
      .violations.map((v) => `${v.id} ${v.nodes.map((n) => n.target.join(' ')).join(',')}`), [TAGS, selector]);
    if (r.length) throw new Error(`${label} (${theme}): ${r.join('; ')}`);
  }
  console.log(`     ${label}: no WCAG 2.2 AA violations, light and dark`);
};
const shot = async (name, loc = page) => { if (shots) await loc.screenshot({ path: `${shots}/${name}.png` }); };

await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('main').waitFor({ timeout: 30_000 });

await step('start a congested run', async () => {
  await page.goto(`${url}/admin`);
  await page.getByLabel(/^scenario/).selectOption('congested');
  await page.getByLabel(/^controller/).selectOption('fixed');
  await page.getByRole('button', { name: 'Start live run' }).click();
  await page.getByText(/Started run live-/).waitFor({ timeout: 20_000 });
});

const car = page.locator('section[aria-labelledby="mycar-h"]');

await step('a followed car shows the car ahead, the advice for following it, and the car behind', async () => {
  await page.goto(`${url}/`);
  await page.locator('.map-svg').waitFor({ timeout: 60_000 });
  await page.waitForFunction(() => document.querySelectorAll('.car-g').length > 25, null, { timeout: 120_000 });
  let found = false;
  for (let i = 0; i < 40 && !found; i += 1) {
    const cars = page.locator('.car-g');
    const n = await cars.count();
    await cars.nth((i * 7) % n).dispatchEvent('click');
    await page.waitForTimeout(1300);
    const card = car.locator('.follow-card');
    if (!(await card.count())) continue;
    const sides = await card.locator('.follow-side').allTextContents();
    found = /km\/h.*gap \d+ m/.test(sides[0] ?? '') && /km\/h.*behind you by \d+ m/.test(sides[1] ?? '');
  }
  if (!found) throw new Error('no followed car had both a car ahead and a car behind');
  const text = (await car.locator('.follow-card').textContent()).replace(/\s+/g, ' ');
  if (!/(Keep your speed|Ease off|Too close|Brake|stopped|pulling away)/.test(text)) throw new Error(`advice: ${text}`);
  if (!/(steady speed|slowing down|speeding up|braking hard)/.test(text)) throw new Error(`acceleration: ${text}`);
  console.log(`     ${text.slice(0, 400)}`);
  await shot('mycar-following', car);
  await audit('My car with the car-following card', 'section[aria-labelledby="mycar-h"]');
});

await step('ride with the car ahead from the card', async () => {
  const before = await car.locator('#mycar-h').textContent();
  await car.getByRole('button', { name: 'Ride with the car ahead' }).click();
  await page.waitForFunction((b) => document.querySelector('#mycar-h')?.textContent !== b, before, { timeout: 10_000 });
  console.log(`     ${before} -> ${await car.locator('#mycar-h').textContent()}`);
});

await step('drive: follow the car ahead (adaptive cruise) with the button and key F', async () => {
  await page.getByRole('tab', { name: 'Vehicle' }).click().catch(() => {});
  const dp = page.locator('section[aria-labelledby="drive-h"]');
  await dp.getByRole('button', { name: 'Drive', exact: true }).click();
  await dp.getByRole('button', { name: 'Take the car' }).click();
  await page.getByText(/^speed limit \d+ km\/h/).waitFor({ timeout: 60_000 });
  await dp.locator('.follow-card').waitFor({ timeout: 20_000 });
  await dp.getByRole('button', { name: /^Follow the car ahead/ }).click().catch(async () => { await page.keyboard.press('f'); });
  await dp.getByText('Follow the car ahead (adaptive cruise)').waitFor({ timeout: 10_000 });
  await page.keyboard.press('g');
  await dp.getByText('Normal driving (up to the limit)').waitFor({ timeout: 10_000 });
  await page.keyboard.press('f');
  await dp.getByText('Follow the car ahead (adaptive cruise)').waitFor({ timeout: 10_000 });
  await page.waitForTimeout(4000);
  console.log(`     ${(await dp.locator('.follow-card').textContent()).replace(/\s+/g, ' ').slice(0, 300)}`);
  await shot('drive-following', dp);
  await audit('Drive panel in follow mode', 'section[aria-labelledby="drive-h"]');
});

await step('the 3D HUD shows the car ahead', async () => {
  await page.getByRole('button', { name: 'Driver perspective' }).click();
  await page.locator('.driver-hud').waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => /Car ahead|Car behind|No signal|in \d+ m/.test(document.querySelector('.driver-hud')?.textContent ?? ''),
    null, { timeout: 20_000 });
  console.log(`     HUD: ${(await page.locator('.driver-hud').textContent()).replace(/\s+/g, ' ').trim()}`);
  await shot('hud-following');
});

await browser.close();
const real = problems.filter((p) => !/Failed to load resource.*(401|429)/.test(p));
console.log(real.length ? `\nPROBLEMS:\n${real.join('\n')}` : '\nall ok');
process.exit(real.length ? 1 : 0);
