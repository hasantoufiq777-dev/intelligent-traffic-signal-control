// Browser smoke check: sign in, see the live map with moving cars, open each page, sign out.
// Usage: node tests/e2e/smoke.mjs <url> <email> <password> [screenshot dir]
import { chromium } from 'playwright-core';

const [url, email, password, shots] = process.argv.slice(2);
const shot = (name) => (shots ? page.screenshot({ path: `${shots}/${name}` }) : null);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const problems = [];
page.on('console', (m) => { // 401s are provoked on purpose (wrong password, request after sign-out)
  if (m.type() === 'error' && !/status of 401/.test(m.text())) problems.push(`console: ${m.text()}`); });
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

await step('redirects to the sign-in page', async () => {
  await page.goto(url);
  await page.waitForURL('**/login');
  await page.getByRole('heading', { name: 'Sign in' }).waitFor();
  await shot('01-login.png');
});
await step('refuses a wrong password', async () => {
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill('definitely-wrong-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('alert').filter({ hasText: 'Invalid email or password' }).waitFor();
});
await step('signs in and shows the live map with cars', async () => {
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.locator('svg.map-svg').waitFor({ timeout: 20_000 });
  await page.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 3, null, { timeout: 60_000 });
  await page.getByText('Live', { exact: true }).waitFor();
  await shot('02-map.png');
});
await step('cars move between frames', async () => {
  const snap = () => page.evaluate(() => [...document.querySelectorAll('svg.map-svg g.car-g')].map((g) => g.style.transform).join('|'));
  const a = await snap();
  await page.waitForTimeout(2500);
  if (a === await snap()) throw new Error('car positions did not change');
});
for (const [link, text] of [['Live overview', 'J0'], ['Results', 'Signals'], ['Run control', 'Live run control'], ['Administration', 'Add a user']]) {
  await step(`opens ${link}`, async () => {
    await page.getByRole('link', { name: link }).click();
    await page.getByText(text, { exact: false }).first().waitFor({ timeout: 15_000 });
  });
}
await step('administration lists users and system health', async () => {
  await page.getByRole('cell', { name: email }).waitFor();
  await page.getByRole('tab', { name: 'System' }).click();
  await page.getByText('Live connections').waitFor();
  await shot('03-system.png');
});
await step('signs out and loses access', async () => {
  await page.getByRole('button', { name: /Administrator/ }).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
  await page.waitForURL('**/login');
  const status = await page.evaluate(() => fetch('/api/options').then((r) => r.status));
  if (status !== 401) throw new Error(`/api/options answered ${status} after sign-out`);
});

await browser.close();
if (problems.length) {
  console.log('\nProblems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nAll browser checks passed.');
