// The one shared live simulation: process lifecycle, broadcast to all viewers, history, playlist loop.
import crypto from 'node:crypto';
import { SimProcess } from './simProcess.js';
import { buildSimArgs, VehicleIdSchema } from './protocol.js';

const ROOM = 'live';
const VEHICLES_OFFSET_MS = 150; // keep the map frame out of the state broadcast's tick

export class LiveChannel {
  constructor({ cfg, io, db, logger, playlist }) {
    this.cfg = cfg;
    this.io = io;
    this.db = db;
    this.log = logger;
    this.playlist = playlist ?? [];
    this.nextIndex = 0;
    this.paused = !cfg.LIVE_LOOP;
    // Show live runs in the SUMO-GUI window (toggled from the dashboard; never with the mock)
    this.gui = Boolean(cfg.SIM_GUI) && !cfg.USE_MOCK;
    this.closing = false;

    this.proc = null;
    this.current = null; // { run_id, entry, source, started_at, status }
    this.lastStatus = null;
    this.lastResult = null;
    this.latestState = null;
    this.states = []; // last HISTORY_SECONDS of simulated time
    this.events = []; // last EVENT_HISTORY events
    this.lastSampleT = -Infinity;
    this.sawFinalStatus = false;
    this.network = null; // road geometry of the current run
    this.latestVehicles = null; // latest full vehicles message from Python
    this.vehicleIndex = new Map(); // vehicle id -> detail object (latest)

    this._lastStateEmit = 0;
    this._lastVehiclesEmit = 0;
    this._pendingVehicles = null;
    this._vehiclesTimer = null;
    this._pendingState = null;
    this._stateTimer = null;
    this._loopTimer = null;
    this._viewerTimer = null;
    this._lastViewerCount = -1;

    this.stats = { started_at: Date.now(), lines_in: 0, bad_lines: 0, states_in: 0, states_broadcast: 0,
      events_broadcast: 0, bytes_broadcast: 0, runs_started: 0 };
  }

  // ------------------------------------------------------------------ viewers
  attach() {
    this.io.on('connection', (socket) => {
      socket.join(ROOM);
      socket.emit('init', this.snapshot());
      // Viewers are read-only: nothing they send is acted on.
      socket.on('disconnect', () => this._scheduleViewerBroadcast());
      // Following a car is a subscription (detail stream for one vehicle), not control.
      socket.on('follow', (id) => this._follow(socket, id));
      socket.on('unfollow', () => this._follow(socket, null));
      this._scheduleViewerBroadcast();
    });
  }

  viewers() {
    return this.io.of('/').sockets.size;
  }

  _scheduleViewerBroadcast() {
    if (this._viewerTimer || this.closing) return;
    this._viewerTimer = setTimeout(() => {
      this._viewerTimer = null;
      const n = this.viewers();
      if (n !== this._lastViewerCount) {
        this._lastViewerCount = n;
        this.io.to(ROOM).emit('viewers', { count: n });
      }
    }, this.cfg.VIEWER_BROADCAST_MS);
  }

  nowPlaying() {
    const loop = this.cfg.LIVE_LOOP && !this.paused;
    return {
      running: Boolean(this.proc),
      current: this.current,
      next: loop && this.playlist.length ? this.playlist[this.nextIndex % this.playlist.length] : null,
      loop,
      viewers: this.viewers(),
      mock: this.cfg.USE_MOCK,
      gui: this.gui,
    };
  }

  _follow(socket, id) {
    const prev = socket.data.follow;
    if (prev) socket.leave(`veh:${prev}`);
    socket.data.follow = null;
    if (id === null || id === undefined) return;
    const parsed = VehicleIdSchema.safeParse(id);
    if (!parsed.success) return;
    socket.data.follow = parsed.data;
    socket.join(`veh:${parsed.data}`);
    const detail = this.vehicleIndex.get(parsed.data);
    // gone: true = the car is not (or no longer) in the simulation; false = no vehicle data yet
    socket.emit('vehicle', detail ?? { id: parsed.data, gone: this.vehicleIndex.size > 0 });
  }

  /** Map rows for every viewer: [id, x, y, angle, flags]. */
  _mapRows(msg) {
    const f = msg.fields ?? [];
    const ix = ['id', 'x', 'y', 'angle', 'flags'].map((k) => f.indexOf(k));
    if (ix.some((i) => i < 0)) return [];
    return (msg.rows ?? []).map((r) => ix.map((i) => r[i]));
  }

  snapshot() {
    return {
      network: this.network,
      vehicles: this.latestVehicles ? { t: this.latestVehicles.t, rows: this._mapRows(this.latestVehicles) } : null,
      now_playing: this.nowPlaying(),
      status: this.lastStatus,
      latest: this.latestState,
      states: this.states,
      events: this.events,
      result: this.lastResult,
      history_seconds: this.cfg.HISTORY_SECONDS,
    };
  }

  // ------------------------------------------------------------------ lifecycle
  /** Start a run (stopping any current one). source: 'admin' | 'playlist'. */
  async start(entry, source = 'admin') {
    clearTimeout(this._loopTimer);
    this._loopTimer = null;
    if (this.proc) await this._stopProcess();
    if (this.closing) return null;
    if (source === 'admin') this.paused = false; // admin start resumes the loop afterwards (if enabled)

    this._dropPendingState(); // never deliver a state of the previous run after this one starts
    const runId = `live-${Date.now().toString(36)}-${crypto.randomBytes(3).toString('hex')}`;
    this.current = { run_id: runId, entry, source, started_at: new Date().toISOString(), status: 'running' };
    this.states = [];
    this.events = [];
    this.latestState = null;
    this.lastStatus = null;
    this.lastResult = null;
    this.lastSampleT = -Infinity;
    this.sawFinalStatus = false;
    this.network = null;
    this.latestVehicles = null;
    this.vehicleIndex = new Map();
    clearTimeout(this._vehiclesTimer);
    this._vehiclesTimer = null;
    this._pendingVehicles = null;
    this.db.insertRun({ id: runId, scenario: entry.scenario, controller: entry.controller, nav: entry.nav,
      seed: entry.seed, penetration: entry.penetration, loss: entry.loss, relay: entry.relay,
      eco_fraction: entry.eco_fraction ?? 0, ev_routing: entry.ev_routing ?? 'off', source });
    this.stats.runs_started += 1;

    const args = buildSimArgs(entry, runId, this.cfg, { gui: this.gui });
    this.log.info(`Starting live run ${runId} (${source}): ${this.cfg.PYTHON_BIN} ${args.join(' ')}`);
    const proc = new SimProcess({ pythonBin: this.cfg.PYTHON_BIN, args, cwd: this.cfg.PROJECT_ROOT, logger: this.log });
    proc.gui = this.gui;
    this.proc = proc;
    proc.on('message', (msg) => this._onMessage(proc, msg));
    proc.on('bad_line', (line) => {
      this.stats.bad_lines += 1;
      this.log.warn('Ignoring non-protocol line from simulation:', line.slice(0, 200));
    });
    proc.on('stderr', (line) => this.log.debug('[sim]', line));
    proc.on('exit', (info) => this._onExit(proc, info));
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());
    return runId;
  }

  /**
   * Turn the SUMO-GUI window on or off. SUMO cannot add a window to a running simulation, so a
   * running live run is restarted (same entry) with or without the window.
   */
  async setGui(enabled) {
    if (this.cfg.USE_MOCK) throw new Error('SUMO-GUI is not available with the mock simulation');
    this.gui = Boolean(enabled);
    let restarted = false;
    if (this.proc && this.current && Boolean(this.proc.gui) !== this.gui) {
      await this.start(this.current.entry, this.current.source);
      restarted = true;
    }
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());
    return { enabled: this.gui, restarted };
  }

  /** Admin stop: stop the run and pause the playlist until the next admin start. */
  async stop() {
    this.paused = true;
    clearTimeout(this._loopTimer);
    this._loopTimer = null;
    const had = Boolean(this.proc);
    if (had) await this._stopProcess();
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());
    return had;
  }

  async _stopProcess() {
    const proc = this.proc;
    if (!proc) return;
    proc.manualStop = true;
    await proc.stop();
  }

  /** Start the playlist (LIVE_LOOP). */
  startLoop() {
    if (!this.cfg.LIVE_LOOP || !this.playlist.length) return;
    this.paused = false;
    if (!this.proc) this._startNextFromPlaylist();
  }

  _startNextFromPlaylist() {
    if (this.closing || this.paused || !this.playlist.length) return;
    const entry = this.playlist[this.nextIndex % this.playlist.length];
    this.nextIndex = (this.nextIndex + 1) % this.playlist.length;
    this.start(entry, 'playlist').catch((err) => this.log.error('Playlist start failed:', err.message));
  }

  // ------------------------------------------------------------------ messages
  _onMessage(proc, msg) {
    if (proc !== this.proc || !this.current) return; // late line from a replaced process
    this.stats.lines_in += 1;
    const runId = this.current.run_id;
    switch (msg.type) {
      case 'state':
        this.stats.states_in += 1;
        this._recordState(runId, msg);
        this._broadcastState(msg);
        break;
      case 'event': {
        const ev = { type: 'event', run_id: runId, t: msg.t, kind: msg.kind, tls: msg.tls, detail: msg.detail };
        this.events.push(ev);
        if (this.events.length > this.cfg.EVENT_HISTORY) this.events.splice(0, this.events.length - this.cfg.EVENT_HISTORY);
        this.db.saveEvent(runId, ev);
        this._emitReliable('event', ev);
        this.stats.events_broadcast += 1;
        break;
      }
      case 'status':
        this.lastStatus = msg;
        if (['finished', 'stopped', 'error'].includes(msg.status)) this.sawFinalStatus = true;
        this._emitReliable('status', msg);
        break;
      case 'network':
        this.network = { ...msg, run_id: runId };
        this._emitReliable('network', this.network);
        break;
      case 'vehicles':
        this._onVehicles(msg);
        break;
      case 'result':
        this.lastResult = msg;
        this.db.saveResult(runId, msg.metrics);
        this._emitReliable('result', msg);
        break;
      default:
        break;
    }
  }

  _recordState(runId, msg) {
    this.latestState = msg;
    this.states.push(msg);
    const t = Number(msg.t) || 0;
    const minT = t - this.cfg.HISTORY_SECONDS;
    let drop = 0;
    while (drop < this.states.length && (Number(this.states[drop].t) || 0) <= minT) drop += 1;
    if (drop) this.states.splice(0, drop);
    if (t - this.lastSampleT >= this.cfg.SAMPLE_EVERY_SIM_S) {
      this.lastSampleT = t;
      this.db.saveSample(runId, msg);
    }
  }

  /** At most one "state" per STATE_INTERVAL_MS, always the latest; volatile (droppable for slow clients). */
  _broadcastState(msg) {
    const wait = this._lastStateEmit + this.cfg.STATE_INTERVAL_MS - Date.now();
    if (wait <= 0) {
      this._dropPendingState(); // a late timer must not send an older state right after this one
      this._emitState(msg);
    } else {
      this._pendingState = msg;
      if (!this._stateTimer) {
        this._stateTimer = setTimeout(() => {
          this._stateTimer = null;
          const pending = this._pendingState;
          this._pendingState = null;
          if (pending) this._emitState(pending);
        }, wait);
      }
    }
  }

  _emitState(msg) {
    this._lastStateEmit = Date.now();
    const payload = { ...msg, sent_at: this._lastStateEmit }; // sent_at: lets clients measure latency
    this.io.to(ROOM).volatile.emit('state', payload);
    this.stats.states_broadcast += 1;
    this.stats.bytes_broadcast += Buffer.byteLength(JSON.stringify(payload)) * this.viewers();
  }

  _onVehicles(msg) {
    this.latestVehicles = msg;
    const fields = msg.fields ?? [];
    const index = new Map();
    for (const row of msg.rows ?? []) {
      const obj = { t: msg.t };
      fields.forEach((k, i) => { obj[k] = row[i]; });
      index.set(String(obj.id), obj);
    }
    this.vehicleIndex = index;
    // Same throttle as states: at most one map frame per STATE_INTERVAL_MS, always the latest.
    // Never in the same tick as the state broadcast: a volatile message is dropped while the
    // socket is still writing the previous one, so the frame goes out VEHICLES_OFFSET_MS later.
    this._pendingVehicles = msg;
    if (this._vehiclesTimer) return;
    const wait = Math.max(VEHICLES_OFFSET_MS, this._lastVehiclesEmit + this.cfg.STATE_INTERVAL_MS - Date.now());
    this._vehiclesTimer = setTimeout(() => {
      this._vehiclesTimer = null;
      const p = this._pendingVehicles;
      this._pendingVehicles = null;
      if (p) this._emitVehicles(p);
    }, wait);
  }

  _emitVehicles(msg) {
    this._lastVehiclesEmit = Date.now();
    const payload = { run_id: this.current?.run_id, t: msg.t, rows: this._mapRows(msg), sent_at: this._lastVehiclesEmit };
    this.io.to(ROOM).volatile.emit('vehicles', payload);
    this.stats.bytes_broadcast += Buffer.byteLength(JSON.stringify(payload)) * this.viewers();
    // Detail rows only to the viewers following each car. Reliable (not volatile): they are small,
    // go only to followers, and would otherwise be dropped right after the volatile map frame.
    for (const [room, members] of this.io.of('/').adapter.rooms) {
      if (!room.startsWith('veh:') || !members.size) continue;
      const id = room.slice(4);
      const detail = this.vehicleIndex.get(id) ?? { id, t: msg.t, gone: true };
      this.io.to(room).emit('vehicle', detail);
    }
  }

  _emitReliable(name, payload) {
    this.io.to(ROOM).emit(name, payload);
  }

  _dropPendingState() {
    clearTimeout(this._stateTimer);
    this._stateTimer = null;
    this._pendingState = null;
  }

  _onExit(proc, { code, signal, stopped }) {
    if (proc !== this.proc) return;
    // A pending final state is still sent by its timer, keeping the at-most-once-per-interval rule.
    this.proc = null;
    const run = this.current;
    let status;
    if (stopped) status = 'stopped';
    else if (code === 0 && this.lastResult) status = 'finished';
    else status = 'error';
    const message = stopped ? 'Stopped by admin' : status === 'error'
      ? `Simulation exited with code ${code}${signal ? ` (${signal})` : ''}${this.lastStatus?.message ? `: ${this.lastStatus.message}` : ''}`
      : 'Simulation completed';
    if (run) {
      run.status = status;
      this.db.endRun(run.run_id, status, message);
      // Windows kills cannot be caught by Python, so report the final status ourselves if needed
      if (!this.sawFinalStatus) {
        this.lastStatus = { type: 'status', run_id: run.run_id, status, message };
        this._emitReliable('status', this.lastStatus);
      }
    }
    if (proc.gui && !stopped && this.lastStatus?.status === 'stopped') {
      // Python reports "stopped" when the SUMO window was closed: the viewer is done with the GUI
      this.gui = false;
      this.log.info('SUMO-GUI window was closed; continuing without the window');
    }
    this.log.info(`Live run ${run?.run_id} ended: ${status} (code ${code})`);
    this.io.to(ROOM).emit('now_playing', this.nowPlaying());

    if (!this.closing && !this.paused && this.cfg.LIVE_LOOP && !proc.manualStop) {
      const delay = status === 'error' ? this.cfg.CRASH_RETRY_MS : 1500;
      this._loopTimer = setTimeout(() => this._startNextFromPlaylist(), delay);
    }
  }

  async close() {
    this.closing = true;
    clearTimeout(this._loopTimer);
    clearTimeout(this._stateTimer);
    clearTimeout(this._viewerTimer);
    clearTimeout(this._vehiclesTimer);
    if (this.proc) await this._stopProcess();
  }
}
