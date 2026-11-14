// Browser rendering cost while the live run plays: for each page, 20 s of main-thread long tasks
// (over 50 ms, the "jank" threshold) and frames per second, with the number of cars on the map.
// Usage: node tests/e2e/render.mjs <url> <email> <password> [seconds] [cpu slowdown, e.g. 4]
import { chromium } from 'playwright-core';

const [url, email, password, secs = '20', slowdown = '1'] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
const page = await ctx.newPage();
// Optional CPU slowdown (Chrome DevTools throttling) to approximate a slower laptop or phone
if (Number(slowdown) > 1) await (await ctx.newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate: Number(slowdown) });
await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('svg.map-svg').waitFor({ timeout: 30_000 });
const out = {};
for (const p of ['/', '/live', '/signals', '/vanet', '/navigation']) {
  await page.goto(url + p);
  await page.locator('main').waitFor();
  await page.waitForTimeout(2000); // settle (lazy chunks, first data)
  out[p] = await page.evaluate(async (ms) => {
    const long = [];
    const obs = new PerformanceObserver((l) => { for (const e of l.getEntries()) long.push(e.duration); });
    obs.observe({ type: 'longtask', buffered: false });
    let frames = 0;
    const t0 = performance.now();
    await new Promise((resolve) => {
      const tick = () => { frames += 1; if (performance.now() - t0 < ms) requestAnimationFrame(tick); else resolve(); };
      requestAnimationFrame(tick);
    });
    obs.disconnect();
    const s = (performance.now() - t0) / 1000;
    return { seconds: +s.toFixed(1), fps: +(frames / s).toFixed(1), long_tasks: long.length,
      long_task_ms_total: Math.round(long.reduce((a, b) => a + b, 0)), longest_ms: Math.round(Math.max(0, ...long)),
      cars_on_map: document.querySelectorAll('svg.map-svg g.car-g').length, dom_nodes: document.getElementsByTagName('*').length };
  }, Number(secs) * 1000);
}
await browser.close();
console.log(JSON.stringify(out, null, 1));
