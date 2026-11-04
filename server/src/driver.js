// One-driver mode: one viewer at a time drives one car in the shared live run.
//
// The seat goes to the first viewer who asks while a run is live. Their commands are validated,
// rate limited and sent to the simulation as JSON lines on stdin; nobody else's commands are
// accepted. The seat is freed when the trip ends, on "hand back", after DRIVER_IDLE_MS without
// input, after DRIVER_MAX_MS, when the driver leaves (after a short reconnect grace), when the run
// ends, or by the admin. Everyone receives the driver messages, so they can watch the car.
import crypto from 'node:crypto';
import { DriverCommandSchema, DriverStartSchema } from './protocol.js';

const FINAL = new Set(['finished', 'released', 'error']);

const fail = (error, extra = {}) => ({ ok: false, error, ...extra });

function sameKey(a, b) {
  const x = Buffer.from(String(a ?? ''));
  const y = Buffer.from(String(b ?? ''));
  return x.length === y.length && x.length > 0 && crypto.timingSafeEqual(x, y);
}

export class DriverSeat {
  constructor({ cfg, io, db, logger, live, room }) {
    this.cfg = cfg;
    this.io = io;
    this.db = db;
    this.log = logger;
    this.live = live; // LiveChannel: proc, current, network
    this.room = room;
    this.enabled = Boolean(cfg.DRIVER_MODE);
    this.seat = null; // { socketId, key, from, to, since, runId, vid, tripId, tokens, refillAt, timers }
    this.state = null; // latest driver message of the current drive
    this.tripByVid = new Map(); // vehicle id -> trip row id, until the simulation's final report
  }

  // ------------------------------------------------------------------ public view
  public() {
    const s = this.seat;
    return {
      enabled: this.enabled,
      available: this.enabled && Boolean(this.live.proc) && !this.live.sawFinalStatus,
      busy: Boolean(s),
      vid: s?.vid ?? null,
      since: s ? new Date(s.since).toISOString() : null,
      idle_ms: this.cfg.DRIVER_IDLE_MS,
      max_ms: this.cfg.DRIVER_MAX_MS,
    };
  }

  _broadcast() {
    this.io.to(this.room).emit('driver_lock', this.public());
  }

  _tell(socketId, payload) {
    if (socketId) this.io.to(socketId).emit('driver_seat', payload);
  }

  // ------------------------------------------------------------------ socket handlers
  attach(socket) {
    // Acknowledgement callbacks are optional; a missing or extra argument is tolerated.
    const on = (name, fn) => socket.on(name, (...args) => {
      const cb = args.find((a) => typeof a === 'function') ?? (() => {});
      const body = typeof args[0] === 'function' ? undefined : args[0];
      try {
        cb(fn(body));
      } catch (err) {
        this.log.error(`driver ${name} failed:`, err.message);
        cb(fail('Internal error'));
      }
    });
    on('driver:claim', (body) => this.claim(socket, body));
    on('driver:cmd', (body) => this.command(socket, body));
    on('driver:release', () => this.release(socket));
    on('driver:resume', (key) => this.resume(socket, key));
    socket.on('disconnect', () => this._onDisconnect(socket));
  }

  claim(socket, body) {
    if (!this.enabled) return fail('Driving is switched off by the admin');
    const live = this.live;
    if (!live.proc || !live.current || live.sawFinalStatus) return fail('No live run right now. Try again when the next run starts.');
    if (this.seat) {
      return fail(this.seat.socketId === socket.id ? 'You are already driving' : 'Someone else is driving. You can follow their car until the seat is free.');
    }
    const parsed = DriverStartSchema.safeParse(body ?? {});
    if (!parsed.success) return fail('Pick a start road and a destination road', { issues: parsed.error.issues.map((i) => i.message) });
    const { from, to } = parsed.data;
    const edges = new Set((live.network?.edges ?? []).map((e) => e.id));
    if (edges.size && (!edges.has(from) || !edges.has(to))) return fail('Unknown road');

    const cmd = { cmd: 'drive_start', from, to };
    if (!this.cfg.USE_MOCK) cmd.step_delay = this.cfg.DRIVER_STEP_DELAY;
    if (!live.proc.send(cmd)) return fail('The simulation is not accepting commands');

    const now = Date.now();
    const runId = live.current.run_id;
    this.seat = {
      socketId: socket.id, key: crypto.randomBytes(16).toString('hex'), from, to, since: now, runId, vid: null,
      tripId: this.db.insertDriverTrip(runId, from, to), tokens: this._burst(), refillAt: now, timers: {},
    };
    this.state = null;
    this.seat.timers.max = setTimeout(() => this.end('time_limit'), this.cfg.DRIVER_MAX_MS);
    this._touch();
    this.log.info(`Driver seat taken in ${runId}: ${from} -> ${to}`);
    this._broadcast();
    return { ok: true, key: this.seat.key, idle_ms: this.cfg.DRIVER_IDLE_MS, max_ms: this.cfg.DRIVER_MAX_MS };
  }

  _burst() {
    return Math.max(1, Math.round(this.cfg.DRIVER_CMDS_PER_S * 2));
  }

  command(socket, body) {
    const seat = this.seat;
    if (!seat || seat.socketId !== socket.id) return fail('You are not driving');
    const parsed = DriverCommandSchema.safeParse(body ?? {});
    if (!parsed.success) return fail('Invalid command', { issues: parsed.error.issues.map((i) => i.message) });

    // Token bucket: DRIVER_CMDS_PER_S on average, short bursts up to twice that
    const now = Date.now();
    const rate = this.cfg.DRIVER_CMDS_PER_S;
    seat.tokens = Math.min(this._burst(), seat.tokens + ((now - seat.refillAt) / 1000) * rate);
    seat.refillAt = now;
    if (seat.tokens < 1) return fail('Too many commands: slow down', { retry_ms: Math.ceil(((1 - seat.tokens) / rate) * 1000) });
    seat.tokens -= 1;

    const { action, speed_kmh: kmh } = parsed.data;
    const cmd = { cmd: 'drive', mode: action };
    if (action === 'ease') cmd.speed = Math.round(((kmh ?? 30) / 3.6) * 100) / 100;
    if (!this.live.proc?.send(cmd)) return fail('The simulation is not accepting commands');
    this._touch();
    return { ok: true };
  }

  release(socket) {
    if (!this.seat || this.seat.socketId !== socket.id) return fail('You are not driving');
    this.end('released');
    return { ok: true };
  }

  resume(socket, key) {
    const seat = this.seat;
    if (!seat || !sameKey(key, seat.key)) return fail('Your drive has ended');
    clearTimeout(seat.timers.resume);
    seat.socketId = socket.id;
    return { ok: true, key: seat.key, vid: seat.vid, idle_ms: this.cfg.DRIVER_IDLE_MS, max_ms: this.cfg.DRIVER_MAX_MS };
  }

  _onDisconnect(socket) {
    const seat = this.seat;
    if (!seat || seat.socketId !== socket.id) return;
    seat.socketId = null;
    if (this.cfg.DRIVER_RESUME_MS > 0) {
      seat.timers.resume = setTimeout(() => this.end('left'), this.cfg.DRIVER_RESUME_MS);
    } else {
      this.end('left');
    }
  }

  _touch() {
    const seat = this.seat;
    if (!seat) return;
    clearTimeout(seat.timers.idle);
    seat.timers.idle = setTimeout(() => this.end('idle'), this.cfg.DRIVER_IDLE_MS);
  }

  // ------------------------------------------------------------------ ending a drive
  /**
   * Free the seat. reason: released | idle | left | admin | time_limit | run_ended | finished | error.
   * Unless the simulation ended the drive itself, it is told to hand the car back (drive_end) and
   * its final message later adds the trip report to the stored trip.
   */
  end(reason, { fromSim = false, message = null } = {}) {
    const seat = this.seat;
    if (!seat) return false;
    for (const t of Object.values(seat.timers)) clearTimeout(t);
    this.seat = null;
    const sameRun = this.live.current?.run_id === seat.runId;
    if (!fromSim && sameRun && this.live.proc) this.live.proc.send({ cmd: 'drive_end', reason });
    const status = reason === 'finished' ? 'finished' : reason === 'error' ? 'error' : 'released';
    this.db.endDriverTrip(seat.tripId, { status, reason, vid: seat.vid });
    if (fromSim || !sameRun || !this.live.proc) this.tripByVid.delete(seat.vid);
    this._tell(seat.socketId, { mine: false, reason, message });
    this.log.info(`Driver seat freed (${reason})`);
    this._broadcast();
    return true;
  }

  /** A "driver" message from the simulation. */
  onSimMessage(msg) {
    const seat = this.seat;
    if (seat && !seat.vid && msg.vid && msg.run_id === seat.runId) {
      seat.vid = msg.vid;
      this.tripByVid.set(msg.vid, seat.tripId);
      this._broadcast();
    }
    if (FINAL.has(msg.status)) {
      const tripId = msg.vid ? this.tripByVid.get(msg.vid) : seat?.tripId;
      if (tripId) {
        this.db.endDriverTrip(tripId, { status: msg.status, reason: msg.status === 'finished' ? 'finished' : null,
          vid: msg.vid || null, report: msg.report ?? null });
        this.tripByVid.delete(msg.vid);
      }
      // The simulation ended the current drive (arrived, failed to start, or the run ended)
      if (seat && (seat.vid ? seat.vid === msg.vid : !msg.vid || msg.status === 'error')) {
        const reason = msg.status === 'finished' ? 'finished' : msg.status === 'error' ? 'error' : 'run_ended';
        this.end(reason, { fromSim: true, message: msg.message ?? null });
      }
      this.state = null;
    } else {
      this.state = msg;
    }
    this.io.to(this.room).emit('driver', msg);
  }

  /** The live run stopped or was replaced: any drive in it is over. */
  onRunEnded() {
    if (this.seat) this.end('run_ended', { fromSim: true });
    this.tripByVid.clear();
    this.state = null;
    this._broadcast();
  }

  setEnabled(enabled) {
    this.enabled = Boolean(enabled);
    if (!this.enabled && this.seat) this.end('admin');
    this._broadcast();
    return this.public();
  }

  close() {
    if (this.seat) for (const t of Object.values(this.seat.timers)) clearTimeout(t);
  }
}
