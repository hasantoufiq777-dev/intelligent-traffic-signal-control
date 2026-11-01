// Gateway entry point: start HTTP + Socket.io, start the playlist if LIVE_LOOP, shut down cleanly.
import { createGateway } from './app.js';

const gw = createGateway();
const { cfg, log } = gw;

const addr = await gw.listen();
log.info(`Gateway listening on http://${addr.address}:${addr.port} (mock=${cfg.USE_MOCK}, loop=${cfg.LIVE_LOOP})`);
if (!cfg.ADMIN_TOKEN) log.warn('ADMIN_TOKEN is empty: admin endpoints are disabled');
if (cfg.LIVE_LOOP) gw.live.startLoop();

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  log.info(`${signal} received: stopping the simulation and closing connections`);
  const force = setTimeout(() => process.exit(1), 10_000);
  force.unref();
  try {
    await gw.close();
  } finally {
    process.exit(0);
  }
}
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
