// Live details added in Phase 10, against a gateway with the REAL simulation: measured VANET jitter,
// the navigator's latest route decision with ACO probabilities, the traffic demand setting, and
// simulation events in the event centre. Starts runs as the given administrator or researcher.
// Usage: node tests/e2e/live-details.mjs <url> <email> <password>
import { chromium } from 'playwright-core';

const [url, email, password] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 950 } })).newPage();
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
await page.locator('main').waitFor({ timeout: 30_000 }); // no run may be playing yet

await step('run control starts a run with half the traffic demand', async () => {
  await page.goto(`${url}/admin`);
  await page.getByLabel(/^scenario/).selectOption('navigation');
  await page.getByLabel(/^controller/).selectOption('adaptive_vanet');
  await page.getByLabel(/^nav/).selectOption('aco');
  await page.getByLabel(/^demand/).fill('0.5');
  await page.getByRole('button', { name: 'Start live run' }).click();
  await page.getByText(/Started run live-/).waitFor({ timeout: 20_000 });
  const entry = await page.evaluate(async () => (await (await fetch('/api/live/status')).json()).entry?.entry);
  if (entry?.demand !== 0.5 || entry?.nav !== 'aco') throw new Error(`entry ${JSON.stringify(entry)}`);
});
await step('the event centre lists the simulation start', async () => {
  await page.goto(`${url}/events`);
  await page.getByRole('cell', { name: 'sim started' }).first().waitFor({ timeout: 30_000 });
});
await step('the VANET page shows the measured jitter', async () => {
  await page.goto(`${url}/vanet`);
  const tile = page.locator('.tile', { hasText: 'Jitter' });
  // above zero once messages with different delays have been delivered (the channel adds 0-5 ms)
  await page.waitForFunction(() => {
    const m = /([\d.]+) ms/.exec([...document.querySelectorAll('.tile')].find((t) => t.textContent.includes('Jitter'))?.textContent ?? '');
    return m && Number(m[1]) > 0;
  }, null, { timeout: 60_000 });
  console.log(`     ${(await tile.textContent()).replace(/\s+/g, ' ').trim()}`);
});
await step('the navigation page shows the latest ACO decision with probabilities adding up to 100 %', async () => {
  await page.goto(`${url}/navigation`);
  const table = page.getByRole('region', { name: 'Route alternatives' });
  await table.waitFor({ timeout: 120_000 }); // first route searches after about 30 simulated seconds
  const probs = await table.locator('tbody tr td:nth-child(3)').allTextContents();
  const sum = probs.reduce((a, p) => a + Number(p.replace(/[^\d.]/g, '')), 0);
  if (!probs.length || Math.abs(sum - 100) > 2) throw new Error(`probabilities ${probs.join(', ')}`);
  if ((await table.locator('tbody td', { hasText: '✓' }).count()) !== 1) throw new Error('no single chosen route');
  console.log(`     ${probs.length} alternatives: ${probs.join(', ')}`);
});

await browser.close();
if (problems.length) {
  console.log('\nProblems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nAll live-detail checks passed.');
