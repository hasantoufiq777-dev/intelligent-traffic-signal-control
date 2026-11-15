// Supervisor features in the browser, against a gateway with the REAL simulation: the city network
// (ring, bus lanes, residential streets, mixed vehicles), 50 % VANET penetration, the A* route explorer,
// the traffic control view / driver perspective switch (3D), and driving a truck with the speed
// controls and the estimated fuel-efficient speed.
// Usage: node tests/e2e/city.mjs <url> <email> <password> [screenshot folder]
import { chromium } from 'playwright-core';

const [url, email, password, shots] = process.argv.slice(2);
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--enable-unsafe-swiftshader', '--use-angle=swiftshader'] });
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
    if (shots) await page.screenshot({ path: `${shots}/FAIL-${name.slice(0, 40).replace(/\W+/g, '_')}.png` }).catch(() => {});
  }
};
const shot = async (name) => { if (shots) await page.screenshot({ path: `${shots}/${name}.png` }); };

await page.goto(`${url}/login`);
await page.getByLabel('Email').fill(email);
await page.getByLabel('Password').fill(password);
await page.getByRole('button', { name: 'Sign in' }).click();
await page.locator('main').waitFor({ timeout: 30_000 });

await step('Run control defaults to 50 % penetration and starts the city scenario', async () => {
  await page.goto(`${url}/admin`);
  const pen = page.getByLabel(/^penetration/);
  if ((await pen.inputValue()) !== '0.5') throw new Error(`penetration default ${await pen.inputValue()}`);
  await page.getByLabel(/^scenario/).selectOption('city');
  await page.getByLabel(/^controller/).selectOption('adaptive_vanet');
  await page.getByLabel(/^nav/).selectOption('astar');
  await page.getByRole('button', { name: 'Start live run' }).click();
  await page.getByText(/Started run live-/).waitFor({ timeout: 20_000 });
});

await step('the map shows the city: ring, bus lanes, residential streets, vehicle types', async () => {
  await page.goto(`${url}/`);
  await page.locator('.map-svg').waitFor({ timeout: 60_000 });
  await page.waitForFunction(() => document.querySelectorAll('.car-g').length > 15, null, { timeout: 90_000 });
  for (const t of ['Bus lane (buses and ambulances)', 'Residential street, 30 km/h, no trucks or buses', 'Truck', 'Bus', 'Motorcycle',
    'Eco vehicle (always this green)']) {
    await page.locator('.map-legend').getByText(t, { exact: true }).first().waitFor({ timeout: 5000 });
  }
  const titles = await page.locator('.car-g > title').allTextContents();
  const kinds = ['Truck', 'Bus', 'Motorcycle', 'Eco car', 'Car'].filter((k) => titles.some((t) => t.includes(` – ${k},`)));
  if (kinds.length < 3) throw new Error(`vehicle categories on the map: ${kinds.join(', ')}`);
  console.log(`     categories on the map: ${kinds.join(', ')}`);
});

await step('the VANET chip shows the connected vehicles and 50 % penetration', async () => {
  const chip = await page.locator('.map-chip').textContent();
  const m = /VANET: (\d+) of (\d+) vehicles connected · penetration 50 %/.exec(chip);
  if (!m) throw new Error(chip);
  console.log(`     ${chip}`);
  if (Number(m[1]) >= Number(m[2])) throw new Error('every vehicle connected at 50 % penetration');
  await shot('city-map');
});

await step('eco vehicles all have the one eco colour', async () => {
  await page.waitForFunction(() => [...document.querySelectorAll('.car-g > title')].some((t) => /Eco car/.test(t.textContent)),
    null, { timeout: 90_000 });
  const fills = await page.$$eval('.car-g', (gs) => gs.filter((g) => /Eco car/.test(g.querySelector('title')?.textContent ?? ''))
    .map((g) => g.querySelector('rect,ellipse')?.getAttribute('fill')));
  if (!fills.length) throw new Error('no eco vehicle on the map yet');
  if (fills.some((f) => f !== 'var(--eco)')) throw new Error(`eco fills: ${[...new Set(fills)].join(', ')}`);
});

await step('the route explorer finds A* alternatives where the shortest is not the fastest', async () => {
  await page.getByRole('tab', { name: 'Routes' }).click();
  const rx = page.locator('section[aria-labelledby="routes-h"]');
  await rx.getByLabel('From').selectOption('SWbottom0');
  await rx.locator('select').nth(1).selectOption('right1NE');
  await rx.getByRole('button', { name: 'Find routes' }).click();
  const table = page.getByRole('region', { name: 'Route alternatives' });
  await table.waitFor({ timeout: 15_000 });
  const rows = await table.locator('tbody tr').count();
  if (rows < 2) throw new Error(`${rows} routes`);
  await page.getByText(/The shortest route .* is not the fastest now/).waitFor({ timeout: 3000 });
  // a straight route line has a zero-height box, which Playwright counts as invisible: check it is drawn
  await page.locator('svg [aria-label="Route alternatives"] path').first().waitFor({ state: 'attached', timeout: 5000 });
  console.log(`     ${rows} routes; ${(await page.getByText(/The shortest route/).textContent()).trim()}`);
  await shot('route-explorer');
});

await step('a truck gets only routes legal for trucks', async () => {
  const rx = page.locator('section[aria-labelledby="routes-h"]');
  await rx.getByLabel('Vehicle').selectOption('truck');
  await page.waitForTimeout(700);
  await rx.getByRole('button', { name: 'Find routes' }).click();
  await page.waitForTimeout(1500);
  const paths = await rx.locator('table tbody tr').count();
  const detail = await rx.getByText(/^Route 1:/).textContent();
  if (/bottom1right0|left0bottom0|right1top1|top0left1/.test(detail)) throw new Error(`truck sent through a residential street: ${detail}`);
  console.log(`     ${paths} truck routes; ${detail.trim()}`);
});

await step('drive a truck: accelerate, brake, desired speed; the fuel-efficient speed advice appears', async () => {
  await page.getByRole('tab', { name: 'Vehicle' }).click();
  const dp = page.locator('section[aria-labelledby="drive-h"]');
  await dp.getByRole('button', { name: 'Drive', exact: true }).click();
  await dp.getByLabel('Vehicle').selectOption('truck');
  await dp.getByLabel('Start road').selectOption('SWbottom0');
  await dp.getByLabel('Destination road').selectOption('right1NE');
  await dp.getByRole('button', { name: 'Take the car' }).click();
  await page.getByText(/^speed limit \d+ km\/h/).waitFor({ timeout: 60_000 });
  await page.getByText('Estimated fuel-efficient speed', { exact: false }).waitFor({ timeout: 20_000 });
  await page.getByRole('button', { name: /^Brake/ }).click();
  await page.getByText(/At most \d+ km\/h/).waitFor({ timeout: 10_000 });
  await page.getByRole('button', { name: /^Accelerate/ }).click();
  await page.waitForTimeout(1500);
  const reasons = await page.locator('.advice ul li').allTextContents();
  if (!reasons.some((r) => /Speed limit here/.test(r))) throw new Error(`reasons: ${reasons.join(' | ')}`);
  console.log(`     reasons: ${reasons.join(' | ')}`);
  await page.locator('.fuel-curve svg').waitFor({ timeout: 5000 });
  await shot('drive-truck');
});

await step('the driver perspective renders the 3D road from the truck', async () => {
  await page.getByRole('button', { name: 'Driver perspective' }).click();
  await page.locator('.driver-canvas canvas').waitFor({ timeout: 30_000 });
  await page.locator('.driver-hud').waitFor({ timeout: 20_000 });
  await page.waitForTimeout(2500);
  const hud = (await page.locator('.driver-hud').textContent()).replace(/\s+/g, ' ');
  if (!/Truck/.test(hud)) throw new Error(`HUD: ${hud}`);
  // the rendered frame (a screenshot: WebGL buffers cannot be read back) must show sky, ground and road
  const png = await page.locator('.driver-canvas').screenshot();
  const colours = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const g = document.createElement('canvas');
    g.width = 96; g.height = 54;
    const ctx = g.getContext('2d');
    ctx.drawImage(img, 0, 0, 96, 54);
    const d = ctx.getImageData(0, 0, 96, 54).data;
    const set = new Set();
    let road = 0; // dark grey asphalt pixels in the lower half
    for (let i = 0; i < d.length; i += 4) {
      set.add(`${d[i] >> 4},${d[i + 1] >> 4},${d[i + 2] >> 4}`);
      const y = Math.floor(i / 4 / 96);
      if (y > 27 && Math.abs(d[i] - d[i + 1]) < 12 && Math.abs(d[i + 1] - d[i + 2]) < 14 && d[i] < 110 && d[i] > 35) road += 1;
    }
    return { n: set.size, road };
  }, png.toString('base64'));
  if (colours.road < 150) throw new Error(`no road surface in the lower half of the view (${colours.road} grey pixels)`);
  if (colours.n < 8) throw new Error(`3D view looks blank (${colours.n} colours)`);
  console.log(`     HUD: ${hud.trim()} · ${colours.n} colour groups, ${colours.road} road pixels`);
  await shot('driver-seat');
  await page.getByRole('button', { name: 'Behind the vehicle' }).click();
  await page.waitForTimeout(1500);
  await shot('driver-behind');
});

await step('switching back to the traffic control view keeps the run and the drive', async () => {
  await page.getByRole('button', { name: 'Traffic control view' }).click();
  await page.locator('.map-svg').waitFor({ timeout: 10_000 });
  await page.getByRole('button', { name: 'Hand the car back' }).click();
  await page.getByText(/You handed the car back|Trip report/).first().waitFor({ timeout: 20_000 });
});

await browser.close();
if (problems.length) {
  console.log('\nProblems:\n' + problems.join('\n'));
  process.exit(1);
}
console.log('\nAll city checks passed.');
