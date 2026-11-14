// Password hashing off the main thread. Argon2id (Node's built-in crypto.argon2, Node 24.7+) with
// the OWASP minimum parameters; on older Node versions scrypt (built in everywhere) is used. Both
// run on the libuv thread pool, so many sign-ins at once do not block the live stream.
// Hashes made by earlier versions with bcrypt are still accepted and replaced at the next sign-in.
import crypto from 'node:crypto';
import bcrypt from 'bcryptjs';

const ARGON = { memory: 19_456, passes: 2, parallelism: 1, tagLength: 32 }; // 19 MiB, t=2, p=1
const SCRYPT = { N: 2 ** 15, r: 8, p: 1, keylen: 32, maxmem: 64 * 1024 * 1024 };
const b64 = (buf) => buf.toString('base64').replace(/=+$/, '');
const unb64 = (s) => Buffer.from(s, 'base64');

export const hasArgon2 = typeof crypto.argon2 === 'function';

// At most MAX_PARALLEL hashes at a time: Node's thread pool has four threads by default and file
// reads and DNS lookups use it too, so a burst of sign-ins must not occupy all of them.
const MAX_PARALLEL = 2;
let running = 0;
const waiting = [];
async function limited(fn) {
  if (running >= MAX_PARALLEL) await new Promise((resolve) => waiting.push(resolve));
  running += 1;
  try {
    return await fn();
  } finally {
    running -= 1;
    waiting.shift()?.();
  }
}

function argon2(password, salt, p = ARGON) {
  return limited(() => new Promise((resolve, reject) => {
    crypto.argon2('argon2id', { message: Buffer.from(password, 'utf8'), nonce: salt, parallelism: p.parallelism,
      tagLength: p.tagLength, memory: p.memory, passes: p.passes }, (err, key) => (err ? reject(err) : resolve(key)));
  }));
}

function scrypt(password, salt, p = SCRYPT) {
  return limited(() => new Promise((resolve, reject) => {
    crypto.scrypt(Buffer.from(password, 'utf8'), salt, p.keylen, { N: p.N, r: p.r, p: p.p, maxmem: p.maxmem },
      (err, key) => (err ? reject(err) : resolve(key)));
  }));
}

/** Hash a password. Format: $argon2id$v=19$m=..,t=..,p=..$salt$hash or $scrypt$N=..,r=..,p=..$salt$hash. */
export async function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  if (hasArgon2) {
    const key = await argon2(password, salt);
    return `$argon2id$v=19$m=${ARGON.memory},t=${ARGON.passes},p=${ARGON.parallelism}$${b64(salt)}$${b64(key)}`;
  }
  const key = await scrypt(password, salt);
  return `$scrypt$N=${SCRYPT.N},r=${SCRYPT.r},p=${SCRYPT.p}$${b64(salt)}$${b64(key)}`;
}

const params = (text) => Object.fromEntries(text.split(',').map((kv) => kv.split('=')).map(([k, v]) => [k, Number(v)]));
const equal = (a, b) => a.length === b.length && crypto.timingSafeEqual(a, b);

/**
 * Check a password against a stored hash. Returns { ok, rehash }: rehash is true when the hash
 * was made with an older scheme or weaker parameters and should be replaced now.
 */
export async function verifyPassword(password, stored) {
  const text = String(stored ?? '');
  try {
    if (text.startsWith('$argon2id$')) {
      const [, , , p, salt, hash] = text.split('$');
      const { m, t, p: par } = params(p);
      if (!hasArgon2 || !(m > 0 && t > 0 && par > 0)) return { ok: false, rehash: false };
      const want = unb64(hash);
      const key = await argon2(password, unb64(salt), { memory: m, passes: t, parallelism: par, tagLength: want.length });
      return { ok: equal(key, want), rehash: m < ARGON.memory || t < ARGON.passes };
    }
    if (text.startsWith('$scrypt$')) {
      const [, , p, salt, hash] = text.split('$');
      const { N, r, p: par } = params(p);
      const want = unb64(hash);
      const key = await scrypt(password, unb64(salt), { N, r, p: par, keylen: want.length, maxmem: SCRYPT.maxmem });
      return { ok: equal(key, want), rehash: hasArgon2 || N < SCRYPT.N };
    }
    if (/^\$2[aby]\$/.test(text)) return { ok: await limited(() => bcrypt.compare(password, text)), rehash: true }; // legacy
  } catch {
    return { ok: false, rehash: false };
  }
  return { ok: false, rehash: false };
}
