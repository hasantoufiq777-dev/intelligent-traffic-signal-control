// Accounts and access control: Argon2id passwords (password.js), server-side sessions in an HttpOnly cookie,
// roles (ADMIN / RESEARCHER / VIEWER), CSRF protection, login lockout, password-reset links,
// user management and the audit trail. Sockets are authenticated with the same session cookie.
import crypto from 'node:crypto';
import { rateLimit } from 'express-rate-limit';
import { z } from 'zod';
import { hashPassword, verifyPassword } from './password.js';

export const ROLES = ['ADMIN', 'RESEARCHER', 'VIEWER'];
export const COOKIE_NAME = 'itsc_sid';
const TOUCH_EVERY_MS = 60_000;
const MUTATING = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

const sha256 = (s) => crypto.createHash('sha256').update(String(s)).digest('hex');
const randomToken = (bytes = 32) => crypto.randomBytes(bytes).toString('base64url');
const iso = (ms) => new Date(ms).toISOString();

function safeEqual(a, b) {
  const x = Buffer.from(String(a ?? ''));
  const y = Buffer.from(String(b ?? ''));
  return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
}

export function readCookie(header, name) {
  for (const part of String(header ?? '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0 && part.slice(0, i).trim() === name) {
      try {
        return decodeURIComponent(part.slice(i + 1).trim());
      } catch {
        return null;
      }
    }
  }
  return null;
}

// The upper limit keeps hashing cost bounded (and matches what older bcrypt hashes could hold)
const Password = z.string().min(10, 'Password must be at least 10 characters')
  .refine((p) => Buffer.byteLength(p, 'utf8') <= 72, 'Password must be at most 72 bytes');
const Email = z.string().trim().toLowerCase().max(254).email('Enter a valid email address');
const Name = z.string().trim().min(1, 'Name is required').max(80);
const Role = z.enum(ROLES);

const LoginSchema = z.object({ email: Email, password: z.string().min(1).max(200) }).strict();
const RegisterSchema = z.object({ email: Email, name: Name, password: Password }).strict();
const CreateUserSchema = z.object({ email: Email, name: Name, password: Password, role: Role.default('VIEWER') }).strict();
const UpdateUserSchema = z.object({ name: Name.optional(), role: Role.optional(), status: z.enum(['active', 'disabled']).optional() })
  .strict().refine((v) => Object.keys(v).length > 0, 'Nothing to change');
const ChangePasswordSchema = z.object({ current_password: z.string().min(1).max(200), new_password: Password }).strict();
const ProfileSchema = z.object({ name: Name }).strict();
const ForgotSchema = z.object({ email: Email }).strict();
const ResetSchema = z.object({ token: z.string().min(20).max(200), password: Password }).strict();
const SettingsSchema = z.object({ allow_registration: z.boolean() }).strict();

const issues = (err) => err.issues.map((i) => ({ path: i.path.join('.'), message: i.message }));
const publicUser = (u) => (u ? { id: u.id, email: u.email, name: u.name, role: u.role } : null);

export function createAuth({ cfg, db, log, io }) {
  const ttlMs = cfg.SESSION_TTL_HOURS * 3_600_000;
  // Compared against when the email is unknown, so both cases take the same time
  const dummyHash = hashPassword(randomToken(8));

  const registrationOpen = () => db.getSetting('allow_registration', String(cfg.ALLOW_REGISTRATION)) === 'true';
  const ipOf = (req) => req.ip || req.socket?.remoteAddress || null;
  const audit = (req, action, extra = {}) => {
    try {
      db.audit({ user: extra.user ?? req?.user ?? null, action, target: extra.target, detail: extra.detail, ip: req ? ipOf(req) : null });
    } catch (err) {
      log.error('Audit log write failed:', err.message);
    }
  };

  // ------------------------------------------------------------------ sessions
  function lookupSession(cookieHeader) {
    const token = readCookie(cookieHeader, COOKIE_NAME);
    if (!token) return null;
    const row = db.getSession(sha256(token));
    if (!row) return null;
    if (row.expires_at <= iso(Date.now()) || row.status !== 'active') {
      db.deleteSession(row.session_id);
      return null;
    }
    if (Date.now() - Date.parse(row.last_seen_at) > TOUCH_EVERY_MS) db.touchSession(row.session_id);
    return row;
  }

  function startSession(req, res, user) {
    const token = randomToken();
    const csrf = randomToken(24);
    db.createSession({ token_hash: sha256(token), user_id: user.id, csrf, expires_at: iso(Date.now() + ttlMs),
      ip: ipOf(req), user_agent: String(req.get('user-agent') ?? '').slice(0, 200) });
    // Bound the number of sessions per account: the oldest ones are ended
    const ids = db.userSessionIds(user.id);
    for (const id of ids.slice(0, Math.max(0, ids.length - cfg.SESSIONS_PER_USER))) endSession(id);
    res.cookie(COOKIE_NAME, token, { httpOnly: true, sameSite: 'lax', secure: cfg.COOKIE_SECURE, path: '/', maxAge: ttlMs });
    return csrf;
  }

  /** Delete a session and disconnect its live sockets. */
  function endSession(sessionId) {
    db.deleteSession(sessionId);
    io?.in(`sess:${sessionId}`).disconnectSockets(true);
  }

  function endUserSessions(userId) {
    db.deleteUserSessions(userId);
    io?.in(`user:${userId}`).disconnectSockets(true);
  }

  const clearCookie = (res) => res.clearCookie(COOKIE_NAME, { httpOnly: true, sameSite: 'lax', secure: cfg.COOKIE_SECURE, path: '/' });

  // ------------------------------------------------------------------ middleware
  /** Attach req.user / req.session when the request carries a valid session cookie. */
  function authenticate(req, _res, next) {
    const row = lookupSession(req.headers.cookie);
    if (row) {
      req.user = { id: row.id, email: row.email, name: row.name, role: row.role };
      req.session = { id: row.session_id, csrf: row.csrf, expires_at: row.expires_at };
    }
    next();
  }

  function requireAuth(req, res, next) {
    if (!req.user) return res.status(401).json({ error: 'Sign in to continue', code: 'unauthenticated' });
    return next();
  }

  const requireRole = (...roles) => (req, res, next) => {
    if (!req.user) return res.status(401).json({ error: 'Sign in to continue', code: 'unauthenticated' });
    if (!roles.includes(req.user.role)) {
      audit(req, 'access_denied', { target: `${req.method} ${req.baseUrl}${req.path}`, detail: `needs ${roles.join(' or ')}` });
      return res.status(403).json({ error: 'You do not have permission for this action', code: 'forbidden' });
    }
    return next();
  };

  /** A browser Origin is accepted when it is this site (same host as the request) or an allowed origin. */
  function originAllowed(origin, requestHost) {
    let host = null;
    try {
      host = new URL(origin).host;
    } catch {
      host = null;
    }
    return Boolean(host && host === requestHost) || cfg.ALLOWED_ORIGINS.includes(origin.replace(/\/$/, ''));
  }

  /**
   * State-changing requests: the Origin (when the browser sends one) must be this site or an
   * allowed origin, and a signed-in request must carry its session's CSRF token.
   */
  function csrfProtect(req, res, next) {
    if (!MUTATING.has(req.method)) return next();
    const origin = req.get('origin');
    if (origin && !originAllowed(origin, req.get('host'))) {
      return res.status(403).json({ error: 'Request from an unknown origin was refused', code: 'bad_origin' });
    }
    if (req.session && !safeEqual(req.get('x-csrf-token'), req.session.csrf)) {
      return res.status(403).json({ error: 'Missing or invalid CSRF token. Reload the page and try again.', code: 'csrf' });
    }
    return next();
  }

  const limiter = (limit, windowMs, message) => rateLimit({
    windowMs, limit, standardHeaders: 'draft-8', legacyHeaders: false,
    validate: { xForwardedForHeader: false }, // behind a proxy set TRUST_PROXY so req.ip is the client
    message: { error: message, code: 'rate_limited' },
  });
  const apiLimiter = limiter(cfg.RATE_LIMIT_PER_MIN, 60_000, 'Too many requests. Slow down and try again in a minute.');
  const authLimiter = limiter(cfg.AUTH_RATE_LIMIT, 15 * 60_000, 'Too many attempts. Try again in 15 minutes.');

  // ------------------------------------------------------------------ sockets
  /** Socket.io handshake: valid session required, and room for one more connection. */
  function socketMiddleware(socket, next) {
    // A page on another site must not open a live connection with this user's cookie (the socket
    // also carries driver commands). Clients that send no Origin (scripts, load tests) are not browsers.
    const { origin, host } = socket.handshake.headers;
    if (origin && !originAllowed(origin, host)) {
      const err = new Error('bad_origin');
      err.data = { code: 'bad_origin', message: 'Live connections are accepted only from this site' };
      return next(err);
    }
    const row = lookupSession(socket.handshake.headers.cookie);
    if (!row) {
      const err = new Error('unauthenticated');
      err.data = { code: 'unauthenticated', message: 'Sign in to watch the live simulation' };
      return next(err);
    }
    if (io.of('/').sockets.size >= cfg.MAX_CONNECTIONS) return next(capacityError());
    socket.data.user = { id: row.id, email: row.email, name: row.name, role: row.role };
    socket.data.sessionId = row.session_id;
    return next();
  }

  let lastCapacityLog = 0;
  let capacityRejected = 0;
  function capacityError() {
    capacityRejected += 1;
    if (Date.now() - lastCapacityLog > 60_000) { // at most one log and audit row per minute
      lastCapacityLog = Date.now();
      log.warn(`Connection limit reached (${cfg.MAX_CONNECTIONS}); refusing new live connections`);
      audit(null, 'capacity_reject', { detail: `limit ${cfg.MAX_CONNECTIONS}, ${capacityRejected} refused so far` });
    }
    const err = new Error('capacity');
    err.data = { code: 'capacity', message: `The platform is at its limit of ${cfg.MAX_CONNECTIONS} live connections. Try again in a few minutes.`,
      max: cfg.MAX_CONNECTIONS };
    return err;
  }

  /** Called for every accepted socket (before anything else): rooms for targeted disconnects, hard cap. */
  function onSocketConnected(socket) {
    if (io.of('/').sockets.size > cfg.MAX_CONNECTIONS) { // two handshakes passed the check together
      socket.emit('capacity', capacityError().data);
      socket.disconnect(true);
      return false;
    }
    socket.join([`sess:${socket.data.sessionId}`, `user:${socket.data.user.id}`]);
    return true;
  }

  // ------------------------------------------------------------------ bootstrap
  /** Create the first administrator from ADMIN_EMAIL / ADMIN_PASSWORD when there is none. */
  async function bootstrapAdmin() {
    if (db.countActiveAdmins() > 0) return null;
    if (!cfg.ADMIN_EMAIL || !cfg.ADMIN_PASSWORD) {
      log.warn('No administrator account exists. Set ADMIN_EMAIL and ADMIN_PASSWORD, or run: npm run create-admin');
      return null;
    }
    const parsed = CreateUserSchema.safeParse({ email: cfg.ADMIN_EMAIL, name: 'Administrator', password: cfg.ADMIN_PASSWORD, role: 'ADMIN' });
    if (!parsed.success) {
      log.error(`ADMIN_EMAIL / ADMIN_PASSWORD are not usable: ${issues(parsed.error).map((i) => i.message).join('; ')}`);
      return null;
    }
    const existing = db.getUserByEmail(parsed.data.email);
    if (existing) {
      log.warn(`ADMIN_EMAIL ${parsed.data.email} exists but is not an active admin; not changing it`);
      return null;
    }
    const id = db.createUser({ ...parsed.data, password_hash: await hashPassword(parsed.data.password) });
    db.audit({ action: 'user_create', target: parsed.data.email, detail: 'first administrator (from environment)' });
    log.info(`Created the first administrator account: ${parsed.data.email}`);
    return id;
  }

  /** Insert an account; null when the email is already taken (also when two requests race). */
  function insertUser(fields) {
    try {
      return db.createUser(fields);
    } catch (err) {
      if (/UNIQUE constraint failed: users\.email/.test(err.message)) return null;
      throw err;
    }
  }

  // ------------------------------------------------------------------ routes
  function mount(app) {
    const body = (schema) => (req, res, next) => {
      const parsed = schema.safeParse(req.body ?? {});
      if (!parsed.success) return res.status(400).json({ error: 'Invalid input', code: 'invalid', issues: issues(parsed.error) });
      req.valid = parsed.data;
      return next();
    };
    const idParam = (req, res, next) => {
      const id = Number(req.params.id);
      if (!Number.isInteger(id) || id <= 0) return res.status(400).json({ error: 'Invalid id', code: 'invalid' });
      req.id = id;
      return next();
    };
    const page = (req, max = 200) => ({
      limit: Math.min(Math.max(parseInt(req.query.limit, 10) || 50, 1), max),
      offset: Math.max(parseInt(req.query.offset, 10) || 0, 0),
    });
    const admin = requireRole('ADMIN');

    // ---- session
    app.get('/api/auth/config', (_req, res) => res.json({ registration: registrationOpen() }));

    app.get('/api/auth/me', (req, res) => {
      if (!req.user) return res.json({ user: null, registration: registrationOpen() });
      return res.json({ user: req.user, csrf: req.session.csrf, expires_at: req.session.expires_at, registration: registrationOpen() });
    });

    app.post('/api/auth/login', authLimiter, body(LoginSchema), async (req, res) => {
      const { email, password } = req.valid;
      const user = db.getUserByEmail(email);
      const generic = () => res.status(401).json({ error: 'Invalid email or password', code: 'bad_credentials' });
      if (!user) {
        await verifyPassword(password, await dummyHash);
        audit(req, 'login_fail', { target: email, detail: 'unknown email' });
        return generic();
      }
      if (user.locked_until && user.locked_until > iso(Date.now())) {
        audit(req, 'login_fail', { user, target: email, detail: 'account locked' });
        return res.status(429).json({ error: 'Too many failed attempts. Try again later.', code: 'locked' });
      }
      const check = await verifyPassword(password, user.password_hash);
      if (!check.ok) {
        const fails = user.failed_logins + 1;
        const lock = fails >= cfg.LOGIN_MAX_FAILS ? iso(Date.now() + cfg.LOGIN_LOCK_MINUTES * 60_000) : null;
        db.recordLoginFailure(user.id, lock);
        if (lock) db.updateUser(user.id, { failed_logins: 0 }); // the lock replaces the counter
        audit(req, 'login_fail', { user, target: email, detail: lock ? 'wrong password; account locked' : 'wrong password' });
        return generic();
      }
      if (user.status !== 'active') {
        audit(req, 'login_fail', { user, target: email, detail: 'account disabled' });
        return res.status(403).json({ error: 'This account is disabled. Contact an administrator.', code: 'disabled' });
      }
      db.recordLoginSuccess(user.id);
      // A hash from an older scheme (bcrypt) is replaced now that the password is known to be right
      if (check.rehash) db.updateUser(user.id, { password_hash: await hashPassword(password) });
      const csrf = startSession(req, res, user);
      audit(req, 'login', { user, target: email });
      return res.json({ user: publicUser(user), csrf });
    });

    app.post('/api/auth/logout', (req, res) => {
      if (req.session) {
        audit(req, 'logout');
        endSession(req.session.id);
      }
      clearCookie(res);
      res.json({ ok: true });
    });

    app.post('/api/auth/register', authLimiter, body(RegisterSchema), async (req, res) => {
      if (!registrationOpen()) return res.status(403).json({ error: 'Registration is closed. Ask an administrator for an account.', code: 'registration_closed' });
      const { email, name, password } = req.valid;
      if (db.getUserByEmail(email)) return res.status(409).json({ error: 'An account with this email already exists', code: 'exists' });
      const id = insertUser({ email, name, password_hash: await hashPassword(password), role: 'VIEWER' });
      if (id === null) return res.status(409).json({ error: 'An account with this email already exists', code: 'exists' });
      const user = db.getUserById(id);
      const csrf = startSession(req, res, user);
      audit(req, 'register', { user, target: email });
      return res.status(201).json({ user: publicUser(user), csrf });
    });

    // A reset link is issued by an administrator (no mail server is configured); the request is
    // recorded so admins see it. The answer is the same whether or not the email exists.
    app.post('/api/auth/forgot', authLimiter, body(ForgotSchema), (req, res) => {
      const user = db.getUserByEmail(req.valid.email);
      if (user) audit(req, 'password_reset_request', { user, target: user.email });
      res.json({ ok: true, message: 'If the account exists, an administrator has been notified and will send you a reset link.' });
    });

    app.post('/api/auth/reset', authLimiter, body(ResetSchema), async (req, res) => {
      const row = db.getPasswordReset(sha256(req.valid.token));
      if (!row || row.used_at || row.expires_at <= iso(Date.now())) {
        return res.status(400).json({ error: 'This reset link is invalid or has expired', code: 'bad_token' });
      }
      const user = db.getUserById(row.user_id);
      if (!user) return res.status(400).json({ error: 'This reset link is invalid or has expired', code: 'bad_token' });
      const ok = db.completePasswordReset(row.id, user.id, await hashPassword(req.valid.password));
      if (!ok) return res.status(400).json({ error: 'This reset link is invalid or has expired', code: 'bad_token' });
      io?.in(`user:${user.id}`).disconnectSockets(true);
      audit(req, 'password_reset', { user, target: user.email });
      return res.json({ ok: true });
    });

    // ---- own account
    app.patch('/api/auth/profile', requireAuth, body(ProfileSchema), (req, res) => {
      db.updateUser(req.user.id, { name: req.valid.name });
      audit(req, 'profile_update');
      res.json({ user: { ...req.user, name: req.valid.name } });
    });

    app.post('/api/auth/password', requireAuth, authLimiter, body(ChangePasswordSchema), async (req, res) => {
      const user = db.getUserById(req.user.id);
      if (!(await verifyPassword(req.valid.current_password, user.password_hash)).ok) {
        audit(req, 'password_change_fail');
        return res.status(400).json({ error: 'The current password is not correct', code: 'bad_credentials' });
      }
      db.updateUser(user.id, { password_hash: await hashPassword(req.valid.new_password) });
      // Every other session of this account ends; this one stays
      for (const id of db.userSessionIds(user.id)) if (id !== req.session.id) endSession(id);
      audit(req, 'password_change');
      return res.json({ ok: true });
    });

    // ---- administration: users
    app.get('/api/users', admin, (req, res) => {
      const { limit, offset } = page(req);
      res.json({ users: db.listUsers(limit, offset), total: db.countUsers(), limit, offset });
    });

    app.post('/api/users', admin, body(CreateUserSchema), async (req, res) => {
      const { email, name, password, role } = req.valid;
      if (db.getUserByEmail(email)) return res.status(409).json({ error: 'An account with this email already exists', code: 'exists' });
      const id = insertUser({ email, name, role, password_hash: await hashPassword(password) });
      if (id === null) return res.status(409).json({ error: 'An account with this email already exists', code: 'exists' });
      audit(req, 'user_create', { target: email, detail: `role ${role}` });
      return res.status(201).json({ user: publicRow(id) });
    });

    const publicRow = (id) => {
      const u = db.getUserById(id);
      return u ? { id: u.id, email: u.email, name: u.name, role: u.role, status: u.status, created_at: u.created_at,
        updated_at: u.updated_at, last_login_at: u.last_login_at, locked_until: u.locked_until } : null;
    };
    /** The platform must always keep one active administrator. */
    const lastAdmin = (user) => user.role === 'ADMIN' && user.status === 'active' && db.countActiveAdmins() <= 1;

    app.patch('/api/users/:id', admin, idParam, body(UpdateUserSchema), (req, res) => {
      const user = db.getUserById(req.id);
      if (!user) return res.status(404).json({ error: 'User not found', code: 'not_found' });
      const change = req.valid;
      const losesAdmin = (change.role && change.role !== 'ADMIN') || change.status === 'disabled';
      if (losesAdmin && lastAdmin(user)) {
        return res.status(409).json({ error: 'This is the only active administrator; add another one first', code: 'last_admin' });
      }
      const fields = { ...change };
      if (change.status === 'active') Object.assign(fields, { failed_logins: 0, locked_until: null }); // also unlocks
      db.updateUser(user.id, fields);
      if (change.status === 'disabled') endUserSessions(user.id);
      audit(req, 'user_update', { target: user.email, detail: Object.entries(change).map(([k, v]) => `${k}=${v}`).join(', ') });
      return res.json({ user: publicRow(user.id) });
    });

    app.delete('/api/users/:id', admin, idParam, (req, res) => {
      const user = db.getUserById(req.id);
      if (!user) return res.status(404).json({ error: 'User not found', code: 'not_found' });
      if (user.id === req.user.id) return res.status(409).json({ error: 'You cannot delete your own account', code: 'self' });
      if (lastAdmin(user)) return res.status(409).json({ error: 'This is the only active administrator', code: 'last_admin' });
      io?.in(`user:${user.id}`).disconnectSockets(true);
      db.deleteUser(user.id); // sessions and reset links go with it (ON DELETE CASCADE)
      audit(req, 'user_delete', { target: user.email });
      return res.json({ deleted: true });
    });

    // Single-use reset link, shown once to the admin who hands it to the user
    app.post('/api/users/:id/reset-link', admin, idParam, (req, res) => {
      const user = db.getUserById(req.id);
      if (!user) return res.status(404).json({ error: 'User not found', code: 'not_found' });
      const token = randomToken();
      const expires = iso(Date.now() + cfg.RESET_LINK_MINUTES * 60_000);
      db.createPasswordReset(sha256(token), user.id, req.user.id, expires);
      audit(req, 'password_reset_link', { target: user.email });
      return res.status(201).json({ path: `/reset-password?token=${token}`, expires_at: expires });
    });

    // ---- administration: sessions, audit trail, settings
    app.get('/api/sessions', admin, (req, res) => {
      const { limit, offset } = page(req);
      res.json({ sessions: db.listSessions(limit, offset).map((s) => ({ ...s, current: s.id === req.session.id })),
        total: db.countSessions(), limit, offset });
    });

    app.delete('/api/sessions/:id', admin, idParam, (req, res) => {
      endSession(req.id);
      audit(req, 'session_revoke', { target: String(req.id) });
      res.json({ revoked: true });
    });

    app.get('/api/audit', admin, (req, res) => {
      const { limit, offset } = page(req);
      const action = typeof req.query.action === 'string' && /^[a-z_]{1,40}$/.test(req.query.action) ? req.query.action : null;
      res.json({ entries: db.listAudit(limit, offset, action), total: db.countAudit(), limit, offset });
    });

    app.get('/api/settings', admin, (_req, res) => res.json({ allow_registration: registrationOpen() }));

    app.put('/api/settings', admin, body(SettingsSchema), (req, res) => {
      db.setSetting('allow_registration', req.valid.allow_registration);
      audit(req, 'settings_update', { detail: `allow_registration=${req.valid.allow_registration}` });
      res.json({ allow_registration: registrationOpen() });
    });
  }

  // Housekeeping: expired sessions and old audit rows
  const sweep = () => {
    try {
      db.deleteExpiredSessions();
      db.pruneAudit(iso(Date.now() - cfg.AUDIT_RETENTION_DAYS * 86_400_000));
    } catch (err) {
      log.warn('Session/audit housekeeping failed:', err.message);
    }
  };
  const sweepTimer = setInterval(sweep, 10 * 60_000);
  sweepTimer.unref();

  return {
    authenticate, requireAuth, requireRole, csrfProtect, apiLimiter, socketMiddleware, onSocketConnected,
    bootstrapAdmin, mount, audit, hashPassword, sweep,
    stats: () => ({ capacity_rejected: capacityRejected }),
    close: () => clearInterval(sweepTimer),
  };
}
