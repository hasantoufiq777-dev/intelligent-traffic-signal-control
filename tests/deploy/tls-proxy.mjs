// Test-only HTTPS front for a local gateway, standing in for nginx: terminates TLS, forwards HTTP and
// WebSocket upgrades, keeps the Host header and adds X-Forwarded-For / X-Forwarded-Proto like the
// nginx configuration in deploy/nginx.conf. Not for production use.
// Usage: node tests/deploy/tls-proxy.mjs <listen port> <gateway port> <cert.pem> <key.pem>
import fs from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';

const [listen, upstream, cert, key] = process.argv.slice(2);
const target = Number(upstream);
const forwarded = (req) => ({ ...req.headers, 'x-forwarded-for': req.socket.remoteAddress, 'x-forwarded-proto': 'https' });

const server = https.createServer({ cert: fs.readFileSync(cert), key: fs.readFileSync(key) }, (req, res) => {
  const up = http.request({ host: '127.0.0.1', port: target, method: req.method, path: req.url, headers: forwarded(req) }, (r) => {
    res.writeHead(r.statusCode, r.rawHeaders);
    r.pipe(res);
  });
  up.on('error', () => { res.writeHead(502); res.end('bad gateway'); });
  req.pipe(up);
});

server.on('upgrade', (req, socket, head) => {
  const up = net.connect(target, '127.0.0.1', () => {
    const headers = Object.entries(forwarded(req)).map(([k, v]) => `${k}: ${v}`).join('\r\n');
    up.write(`${req.method} ${req.url} HTTP/1.1\r\n${headers}\r\n\r\n`);
    if (head?.length) up.write(head);
    socket.pipe(up).pipe(socket);
  });
  up.on('error', () => socket.destroy());
  socket.on('error', () => up.destroy());
});

server.listen(Number(listen), '127.0.0.1', () => console.log(`TLS proxy https://localhost:${listen} -> http://127.0.0.1:${target}`));
