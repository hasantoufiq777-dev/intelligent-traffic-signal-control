// Test helpers: accounts and signed-in sessions (cookie + CSRF token) for supertest and socket.io-client.
import request from 'supertest';
import { io as ioc } from 'socket.io-client';

// Fast password hashing and no rate limiting in tests (the limits have their own tests)
export const TEST_ENV = { BCRYPT_ROUNDS: '4', AUTH_RATE_LIMIT: '100000', RATE_LIMIT_PER_MIN: '1000000' };
export const PASSWORD = 'correct-horse-battery';

let counter = 0;

export async function createUser(gw, role = 'VIEWER', email = null, password = PASSWORD) {
  counter += 1;
  const address = email ?? `${role.toLowerCase()}${counter}@test.local`;
  const id = gw.db.createUser({ email: address, name: `${role} ${counter}`, role, password_hash: await gw.auth.hashPassword(password) });
  return { id, email: address, password, role };
}

/** Sign in and return helpers that send the session cookie (and the CSRF token on writes). */
export async function signIn(gw, email, password = PASSWORD) {
  const res = await request(gw.app).post('/api/auth/login').send({ email, password });
  if (res.status !== 200) throw new Error(`login failed for ${email}: ${res.status} ${JSON.stringify(res.body)}`);
  const cookie = res.headers['set-cookie'][0].split(';')[0];
  const { csrf, user } = res.body;
  const write = (method) => (url) => request(gw.app)[method](url).set('Cookie', cookie).set('X-CSRF-Token', csrf);
  return {
    cookie, csrf, user, setCookie: res.headers['set-cookie'][0],
    get: (url) => request(gw.app).get(url).set('Cookie', cookie),
    post: write('post'), put: write('put'), patch: write('patch'), del: write('delete'),
  };
}

export async function sessionFor(gw, role) {
  const u = await createUser(gw, role);
  return signIn(gw, u.email);
}

export const adminSession = (gw) => sessionFor(gw, 'ADMIN');

/** A live socket for a signed-in session. */
export function connect(url, session, opts = {}) {
  return ioc(url, { transports: ['websocket'], forceNew: true, reconnection: false,
    extraHeaders: session ? { Cookie: session.cookie } : {}, ...opts });
}
