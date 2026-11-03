// Load test: ramp up N Socket.io viewers, hold, and record connections, drops, latency, server load.
//
//   node loadtest/viewers.js [--url http://127.0.0.1:4000] [--viewers 1000] [--ramp 60] [--hold 120]
//                            [--token ADMIN_TOKEN] [--report loadtest/REPORT.md]
//
// The gateway should run with USE_MOCK=true and LIVE_LOOP=true (or pass --token to start a mock run).
// Latency = client receive time - server "sent_at" stamp on each state message (same machine clock).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { io as ioc } from 'socket.io-client';

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, def) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : process.env[`LT_${name.toUpperCase()}`] ?? def;
};
const URL = arg('url', 'http://127.0.0.1:4000');
const N = parseInt(arg('viewers', '1000'), 10);
const RAMP_S = parseFloat(arg('ramp', '60'));
const HOLD_S = parseFloat(arg('hold', '120'));
const TOKEN = arg('token', '');
const REPORT = path.resolve(arg('report', path.join(here, 'REPORT.md')));

const sockets = [];
const latencies = [];
let connected = 0;
let peak = 0;
let connectErrors = 0;
let drops = 0;
let rampDrops = 0;
const disconnectReasons = {};
let statesReceived = 0;
let initReceived = 0;
let holding = false;
let finished = false;
const timeline = [];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pct = (arr, p) => {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
};

async function getJson(p, opts) {
  const res = await fetch(`${URL}${p}`, opts);
  return res.json();
}

function openViewer(i) {
  const s = ioc(URL, { transports: ['websocket'], forceNew: true, reconnection: false, timeout: 20_000 });
  s.on('connect', () => {
    connected += 1;
    peak = Math.max(peak, connected);
  });
  s.on('connect_error', () => {
    connectErrors += 1;
  });
  s.on('disconnect', (reason) => {
    connected -= 1;
    if (!finished) disconnectReasons[reason] = (disconnectReasons[reason] || 0) + 1;
    if (holding) drops += 1;
    else if (!finished) rampDrops += 1;
  });
  s.on('init', () => {
    initReceived += 1;
  });
  s.on('state', (m) => {
    statesReceived += 1;
    if (m.sent_at) latencies.push(Date.now() - m.sent_at);
  });
  sockets[i] = s;
}

async function main() {
  const health = await getJson('/api/health').catch(() => null);
  if (!health?.ok) throw new Error(`Gateway not reachable at ${URL}`);
  if (TOKEN && !health.live_running) {
    await getJson('/api/live/start', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-admin-token': TOKEN },
      body: JSON.stringify({ scenario: 'emergency', controller: 'adaptive_ev_vanet', nav: 'none', seed: 1 }),
    });
  }
  console.error(`Load test: ${N} viewers, ramp ${RAMP_S}s, hold ${HOLD_S}s against ${URL} (mock=${health.mock})`);

  // Sample server metrics every 5 s
  let prev = await getJson('/api/metrics');
  let prevT = Date.now();
  const t0 = Date.now();
  const sampler = setInterval(async () => {
    try {
      const m = await getJson('/api/metrics');
      const now = Date.now();
      const dt = (now - prevT) / 1000;
      const cpuS = (m.cpu.user + m.cpu.system - prev.cpu.user - prev.cpu.system) / 1e6;
      timeline.push({
        t_s: Math.round((now - t0) / 1000),
        phase: holding ? 'hold' : 'ramp',
        viewers_server: m.viewers,
        connected_client: connected,
        cpu_pct_one_core: +((100 * cpuS) / dt).toFixed(1),
        rss_mb: m.memory_mb.rss,
        heap_mb: m.memory_mb.heapUsed,
        out_kb_s: +((m.live.bytes_broadcast - prev.live.bytes_broadcast) / 1024 / dt).toFixed(1),
        states_per_s: +((m.live.states_broadcast - prev.live.states_broadcast) / dt).toFixed(2),
      });
      prev = m;
      prevT = now;
      const last = timeline.at(-1);
      console.error(`t=${last.t_s}s ${last.phase} viewers=${last.viewers_server} cpu=${last.cpu_pct_one_core}% rss=${last.rss_mb}MB out=${last.out_kb_s}KB/s`);
    } catch (e) {
      console.error('metrics poll failed:', e.message);
    }
  }, 5000);

  // Ramp
  const gap = (RAMP_S * 1000) / N;
  for (let i = 0; i < N; i += 1) {
    openViewer(i);
    await sleep(gap);
  }
  await sleep(2000);
  // Hold
  holding = true;
  latencies.length = 0; // latency is reported for the hold phase only
  const statesAtHold = statesReceived;
  await sleep(HOLD_S * 1000);
  holding = false;
  finished = true;
  const statesInHold = statesReceived - statesAtHold;
  clearInterval(sampler);

  const hold = timeline.filter((r) => r.phase === 'hold');
  const avg = (k) => (hold.length ? +(hold.reduce((a, r) => a + r[k], 0) / hold.length).toFixed(1) : null);
  const result = {
    date: new Date().toISOString(),
    url: URL,
    viewers_target: N,
    ramp_s: RAMP_S,
    hold_s: HOLD_S,
    peak_connected: peak,
    connected_at_end: connected,
    connect_errors: connectErrors,
    drops_during_hold: drops,
    drops_during_ramp: rampDrops,
    disconnect_reasons: { ...disconnectReasons },
    init_received: initReceived,
    states_received_hold: statesInHold,
    states_per_viewer_per_s: +(statesInHold / Math.max(1, connected) / HOLD_S).toFixed(2),
    latency_ms: { p50: pct(latencies, 50), p95: pct(latencies, 95), p99: pct(latencies, 99), max: pct(latencies, 100),
      samples: latencies.length },
    server_hold_avg: { cpu_pct_one_core: avg('cpu_pct_one_core'), rss_mb: avg('rss_mb'), out_kb_s: avg('out_kb_s') },
    server_hold_max: {
      cpu_pct_one_core: hold.length ? Math.max(...hold.map((r) => r.cpu_pct_one_core)) : null,
      rss_mb: hold.length ? Math.max(...hold.map((r) => r.rss_mb)) : null,
    },
    hardware: {
      platform: `${os.type()} ${os.release()} (${os.arch()})`,
      cpu: os.cpus()[0]?.model?.trim(),
      cores: os.cpus().length,
      memory_gb: +(os.totalmem() / 2 ** 30).toFixed(1),
      node: process.version,
    },
    timeline,
  };

  for (const s of sockets) s?.close();
  fs.writeFileSync(path.join(here, 'last-result.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(REPORT, renderReport(result));
  console.log(JSON.stringify({ ...result, timeline: undefined }, null, 2));
  process.exit(0);
}

function renderReport(r) {
  const ok = r.peak_connected >= r.viewers_target && r.drops_during_hold === 0;
  const rows = r.timeline.map((x) =>
    `| ${x.t_s} | ${x.phase} | ${x.viewers_server} | ${x.cpu_pct_one_core} | ${x.rss_mb} | ${x.out_kb_s} | ${x.states_per_s} |`);
  return `# Load test report: live broadcast

Generated by \`node loadtest/viewers.js\` on ${r.date}.

**Result: ${ok ? 'PASS' : 'CHECK'}** - ${r.peak_connected} of ${r.viewers_target} viewers connected, ${r.drops_during_hold} dropped during the hold.

## Setup
- Gateway: mock simulation (USE_MOCK=true), one shared live run, state broadcast throttled to 1 per second.
- Viewers: ${r.viewers_target} Socket.io clients (WebSocket transport) in **one Node process on the same machine** as the gateway, ramped over ${r.ramp_s} s, then held for ${r.hold_s} s.
- Hardware: ${r.hardware.cpu}, ${r.hardware.cores} logical cores, ${r.hardware.memory_gb} GB RAM, ${r.hardware.platform}, Node ${r.hardware.node}.

## Results
| Measure | Value |
|---|---|
| Peak connected viewers | ${r.peak_connected} |
| Connected at end of hold | ${r.connected_at_end} |
| Connection errors | ${r.connect_errors} |
| Dropped connections during ramp-up | ${r.drops_during_ramp} |
| Dropped connections during hold | ${r.drops_during_hold} |
| Disconnect reasons (ramp + hold) | ${Object.entries(r.disconnect_reasons).map(([k, v]) => `${k}: ${v}`).join(', ') || 'none'} |
| Late-join "init" messages received | ${r.init_received} |
| State messages per viewer per second (hold) | ${r.states_per_viewer_per_s} |
| Message latency p50 / p95 / p99 / max (ms) | ${r.latency_ms.p50} / ${r.latency_ms.p95} / ${r.latency_ms.p99} / ${r.latency_ms.max} |
| Gateway CPU, hold average / max (% of one core) | ${r.server_hold_avg.cpu_pct_one_core} / ${r.server_hold_max.cpu_pct_one_core} |
| Gateway memory RSS, hold average / max (MB) | ${r.server_hold_avg.rss_mb} / ${r.server_hold_max.rss_mb} |
| Outbound payload bandwidth, hold average | ${r.server_hold_avg.out_kb_s} KB/s |

Latency is measured from the gateway's \`sent_at\` stamp to the client's receive time, so it covers
serialisation, the socket and the client's event loop (clock shared on one machine).
Bandwidth counts message payloads x viewers, without WebSocket/TCP framing.

## Caveats
- Clients and gateway share one machine, so client CPU competes with the gateway; a remote client
  fleet gives a cleaner gateway measurement.
- The mock simulation is used so SUMO does not compete for CPU; the gateway path (parse, throttle,
  broadcast) is the same for the real simulation.
- On Linux, 1000+ sockets need a higher open-file limit (\`ulimit -n 65535\`), see the README.

## Timeline (every 5 s)
| t (s) | Phase | Viewers (server) | CPU % (one core) | RSS (MB) | Out (KB/s) | States/s |
|---|---|---|---|---|---|---|
${rows.join('\n')}
`;
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
