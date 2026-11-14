// Size of a new viewer's first message (the socket "init" snapshot), split by key, plus the size
// of one state from its history. Usage: node loadtest/initsize.js <url> <email> <password>
import zlib from 'node:zlib';
import { io as ioc } from 'socket.io-client';

const [url, email, password] = process.argv.slice(2);
const res = await fetch(`${url}/api/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json', origin: url },
  body: JSON.stringify({ email, password }) });
if (!res.ok) throw new Error(`sign-in failed: ${res.status}`);
const cookie = res.headers.get('set-cookie').split(';')[0];
const socket = ioc(url, { transports: ['websocket'], forceNew: true, reconnection: false, extraHeaders: { Cookie: cookie, Origin: url } });
const init = await new Promise((resolve, reject) => {
  socket.on('init', resolve);
  socket.on('connect_error', reject);
  setTimeout(() => reject(new Error('no init within 15 s')), 15_000);
});
socket.close();
const kb = (v) => +(Buffer.byteLength(JSON.stringify(v ?? null)) / 1024).toFixed(1);
const whole = JSON.stringify(init);
const out = {
  total_kb: kb(init),
  gzip_kb: +(zlib.gzipSync(whole).length / 1024).toFixed(1),
  keys_kb: Object.fromEntries(Object.keys(init).map((k) => [k, kb(init[k])])),
  history_states: init.states?.length ?? 0,
};
const sample = init.states?.at(-1) ?? init.latest;
if (sample) out.one_state_keys_kb = Object.fromEntries(Object.keys(sample).map((k) => [k, kb(sample[k])]).sort((a, b) => b[1] - a[1]));
if (process.env.DUMP_TLS && sample?.tls) out.tls_example = Object.entries(sample.tls)[0];
console.log(JSON.stringify(out, null, 1));
