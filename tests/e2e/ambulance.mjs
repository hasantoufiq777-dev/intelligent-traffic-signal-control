// Ambulance on demand, in the browser, against a gateway with the REAL simulation: start a fixed-time
// run on the grid from Run control, press "Send an ambulance", and follow it in the panel, on the map
// and in the event centre until it arrives with green given at every junction.
// Usage: node tests/e2e/ambulance.mjs <url> <email> <password>
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
await page.locator('main').waitFor({ timeout: 30_000 });

const amb = () => page.getByRole('group', { name: 'Ambulance' });

await step('the ambulance button is off while nothing runs, and explains the priority', async () => {
  await page.goto(`${url}/admin`);
  await page.getByRole('button', { name: 'Stop' }).click().catch(() => {});
  await page.getByText(/simulation: (stopped|finished|idle|unknown|failed)/i).first().waitFor({ timeout: 20_000 }).catch(() => {});
  await page.getByLabel(/^scenario/).selectOption('normal');
  await page.getByLabel(/^controller/).selectOption('fixed');
  await page.getByLabel(/^network/).selectOption('grid2x2');
  await page.getByRole('button', { name: 'Start live run' }).click();
  await page.getByText(/Started run live-/).waitFor({ timeout: 20_000 });
  await amb().getByText('fixed has no ambulance priority of its own').waitFor({ timeout: 30_000 });
});
await step('a chosen start needs a destination before it can be sent', async () => {
  const send = amb().getByRole('button', { name: 'Send an ambulance' });
  await send.waitFor();
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find((b) => b.textContent === 'Send an ambulance')?.disabled,
    null, { timeout: 60_000 });
  const from = amb().getByLabel('from');
  await from.selectOption({ index: 1 });
  if (!(await send.isDisabled())) throw new Error('sendable without a destination');
  await from.selectOption('');
  if (await send.isDisabled()) throw new Error('not sendable with the automatic route');
});
await step('pressing the button sends an ambulance through three junctions', async () => {
  await amb().getByRole('button', { name: 'Send an ambulance' }).click();
  await amb().getByText('Ambulance requested').waitFor({ timeout: 10_000 });
  await amb().getByText(/Ambulance amb_dispatch_1 sent from .+ through J\d, J\d, J\d \(ambulance priority switched on/)
    .waitFor({ timeout: 30_000 });
});
await step('the live map shows it and the junction ahead turns to ambulance priority', async () => {
  await page.goto(`${url}/`);
  await page.getByRole('button', { name: /ambulance priority/i }).first().waitFor({ timeout: 60_000 });
});
await step('it arrives with green at every junction (Run control panel)', async () => {
  await page.goto(`${url}/admin`);
  await amb().getByText(/Ambulance amb_dispatch_1 reached .+ green given at 3 of 3 junctions/).waitFor({ timeout: 120_000 });
});
await step('the event centre lists it in the Ambulance group', async () => {
  await page.goto(`${url}/events`);
  await page.getByRole('cell', { name: 'ambulance dispatched' }).first().waitFor({ timeout: 30_000 });
  await page.getByRole('cell', { name: 'ambulance arrived' }).first().waitFor({ timeout: 30_000 });
});

await browser.close();
if (problems.length) {
  console.log('\nProblems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nAll ambulance checks passed.');
