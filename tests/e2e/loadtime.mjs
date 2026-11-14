// Initial load of the dashboard after sign-in: time until the live map shows cars, cold cache,
// on the local network and with a throttled connection (10 Mbit/s, 40 ms extra latency).
// Usage: node tests/e2e/loadtime.mjs <url> <email> <password>
import { chromium } from 'playwright-core';

const [url, email, password] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const results = {};
for (const [name, net] of [['local', null], ['10 Mbit/s + 40 ms', { latency: 40, downloadThroughput: 1_250_000, uploadThroughput: 1_250_000 }]]) {
  const times = [];
  for (let i = 0; i < 3; i += 1) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
    const page = await ctx.newPage();
    if (net) {
      const cdp = await ctx.newCDPSession(page);
      await cdp.send('Network.emulateNetworkConditions', { offline: false, ...net });
    }
    await page.goto(`${url}/login`);
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Password').fill(password);
    const t0 = Date.now();
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 0, null, { timeout: 30_000 });
    const map = Date.now() - t0;
    // Fresh page load of a signed-in user (cold cache, new context keeps nothing)
    const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 950 }, storageState: await ctx.storageState() });
    const page2 = await ctx2.newPage();
    if (net) await (await ctx2.newCDPSession(page2)).send('Network.emulateNetworkConditions', { offline: false, ...net });
    const t1 = Date.now();
    await page2.goto(url);
    await page2.waitForFunction(() => document.querySelectorAll('svg.map-svg g.car-g').length > 0, null, { timeout: 30_000 });
    times.push({ sign_in_to_cars_ms: map, cold_load_to_cars_ms: Date.now() - t1 });
    await ctx.close();
    await ctx2.close();
  }
  results[name] = times;
}
await browser.close();
console.log(JSON.stringify(results, null, 1));
