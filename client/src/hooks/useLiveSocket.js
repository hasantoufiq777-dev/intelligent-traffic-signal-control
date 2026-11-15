// One shared connection to the gateway's live channel.
// Messages land in mutable buffers; React state is committed at most every COMMIT_MS (about 2 renders/s),
// so a busy stream never re-renders the page on every message.
import { createContext, createElement, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { io } from 'socket.io-client';
import { API_BASE } from '../api.js';
import { useAuth } from './useAuth.js';

const COMMIT_MS = 500;
const MAX_STATES = 240; // the gateway sends the last 120 simulated seconds; keep some slack
const MAX_EVENTS = 200;

const LiveContext = createContext(null);

function emptyBuffers() {
  return {
    nowPlaying: null,
    status: null,
    latest: null,
    states: [],
    events: [],
    result: null,
    network: null,
    vehicles: null, // {t, rows: [[id, x, y, angle, flags], ...]}
    followed: null, // detail object of the followed car
    viewers: null,
    driverSeat: null, // { enabled, available, busy, vid, since, idle_ms, max_ms }
    driver: null, // latest driver message of the current drive (anyone's)
    myDrive: null, // { vid, ended: {reason, message}, report } for this viewer's own drive
  };
}

const ACK_TIMEOUT_MS = 5000;
const CAPACITY_RETRY_MS = 15_000; // the platform is full: try again this often

export function LiveProvider({ children }) {
  const buf = useRef(emptyBuffers());
  const dirty = useRef(true);
  const followId = useRef(null);
  const socketRef = useRef(null);
  const driveKey = useRef(null); // seat key of this viewer's drive (reclaims the seat after a reconnect)
  const [snap, setSnap] = useState(() => ({ ...emptyBuffers(), connection: 'connecting', followId: null }));
  const [connection, setConnection] = useState('connecting');
  const [connectionNote, setConnectionNote] = useState(null);
  const { refresh: refreshAuth } = useAuth();

  useEffect(() => {
    // The session cookie authenticates the socket (sent with the handshake)
    const socket = io(API_BASE || undefined, { transports: ['websocket', 'polling'], reconnectionDelayMax: 10_000,
      withCredentials: true });
    socketRef.current = socket;
    const b = buf.current;
    const touch = () => { dirty.current = true; };
    let capacityTimer = null;

    socket.on('connect', () => {
      setConnection('connected');
      setConnectionNote(null);
      if (followId.current) socket.emit('follow', followId.current); // re-subscribe after a reconnect
      if (driveKey.current) {
        socket.emit('driver:resume', driveKey.current, (res) => {
          if (!res?.ok) {
            driveKey.current = null;
            if (b.myDrive) b.myDrive = { ...b.myDrive, active: false, ended: { reason: 'left', message: res?.error } };
            touch();
          }
        });
      }
    });
    const atCapacity = (info) => {
      setConnection('capacity');
      setConnectionNote(info?.message || 'The platform is at its connection limit. Trying again shortly.');
      clearTimeout(capacityTimer);
      capacityTimer = setTimeout(() => socket.connect(), CAPACITY_RETRY_MS);
    };
    socket.on('disconnect', (reason) => {
      if (reason === 'io server disconnect') {
        // The gateway closed the connection: the session ended (signed out elsewhere, disabled,
        // password changed) or the connection limit was hit. Ask who we are; the app follows.
        setConnection((c) => (c === 'capacity' ? c : 'offline'));
        refreshAuth();
      } else {
        setConnection('reconnecting');
      }
    });
    socket.io.on('reconnect_attempt', () => setConnection('reconnecting'));
    socket.on('connect_error', (err) => {
      const code = err?.data?.code;
      if (code === 'capacity') atCapacity(err.data);
      else if (code === 'unauthenticated') {
        setConnection('offline');
        refreshAuth(); // the session is gone: the app shows the sign-in page
      } else setConnection('offline');
    });
    socket.on('capacity', atCapacity);

    socket.on('init', (m) => {
      b.nowPlaying = m.now_playing;
      b.status = m.status;
      b.latest = m.latest;
      b.states = (m.states || []).slice(-MAX_STATES);
      b.events = (m.events || []).slice(-MAX_EVENTS);
      b.result = m.result;
      b.network = m.network;
      b.vehicles = m.vehicles;
      b.viewers = m.now_playing?.viewers ?? null;
      b.driverSeat = m.driver?.seat ?? m.now_playing?.driver ?? null;
      b.driver = m.driver?.state ?? null;
      touch();
    });
    socket.on('now_playing', (m) => {
      if (m?.current?.run_id && m.current.run_id !== b.nowPlaying?.current?.run_id) {
        // A new run started: drop the previous run's history
        b.states = [];
        b.events = [];
        b.latest = null;
        b.result = null;
        b.network = null;
        b.vehicles = null;
        b.followed = null;
      }
      b.nowPlaying = m;
      b.viewers = m.viewers ?? b.viewers;
      if (m.driver) b.driverSeat = m.driver;
      touch();
    });
    socket.on('state', (m) => {
      b.latest = m;
      b.states.push(m);
      if (b.states.length > MAX_STATES) b.states.splice(0, b.states.length - MAX_STATES);
      touch();
    });
    socket.on('event', (m) => {
      b.events.push(m);
      if (b.events.length > MAX_EVENTS) b.events.splice(0, b.events.length - MAX_EVENTS);
      touch();
    });
    socket.on('status', (m) => { b.status = m; touch(); });
    socket.on('result', (m) => { b.result = m; touch(); });
    socket.on('network', (m) => { b.network = m; touch(); });
    socket.on('vehicles', (m) => { b.vehicles = m; touch(); });
    socket.on('vehicle', (m) => {
      if (m && m.id === followId.current) { b.followed = m; touch(); }
    });
    socket.on('viewers', (m) => { b.viewers = m.count; touch(); });

    // One-driver mode
    socket.on('driver_lock', (m) => { b.driverSeat = m; touch(); });
    socket.on('driver', (m) => {
      const final = ['finished', 'released', 'error'].includes(m.status);
      const mine = b.myDrive && (b.myDrive.vid ? m.vid === b.myDrive.vid : b.myDrive.active);
      if (mine) {
        if (!b.myDrive.vid && m.vid) b.myDrive = { ...b.myDrive, vid: m.vid };
        if (final) b.myDrive = { ...b.myDrive, active: false, report: m.report ?? b.myDrive.report, last: m };
        else b.myDrive = { ...b.myDrive, last: m };
      }
      b.driver = final ? null : m;
      touch();
    });
    socket.on('driver_seat', (m) => {
      // This viewer's drive ended (handed back, idle, left, admin, run ended, finished, error)
      if (!m.mine && b.myDrive) {
        driveKey.current = null;
        b.myDrive = { ...b.myDrive, active: false, ended: { reason: m.reason, message: m.message } };
        touch();
      }
    });

    const timer = setInterval(() => {
      if (!dirty.current) return;
      dirty.current = false;
      const c = buf.current;
      setSnap({
        ...c,
        states: c.states.slice(),
        events: c.events.slice(),
        followId: followId.current,
      });
    }, COMMIT_MS);

    return () => {
      clearInterval(timer);
      clearTimeout(capacityTimer);
      socket.close();
    };
  }, [refreshAuth]);

  const follow = useCallback((id) => {
    followId.current = id || null;
    buf.current.followed = null;
    dirty.current = true;
    const s = socketRef.current;
    if (!s) return;
    if (id) s.emit('follow', id);
    else s.emit('unfollow');
  }, []);

  /** Emit with an acknowledgement; resolves {ok:false,error} on timeout or when offline. */
  const ask = useCallback((name, body, timeoutMs = ACK_TIMEOUT_MS) => new Promise((resolve) => {
    const s = socketRef.current;
    if (!s?.connected) {
      resolve({ ok: false, error: 'Not connected to the gateway' });
      return;
    }
    s.timeout(timeoutMs).emit(name, body, (err, res) => resolve(err ? { ok: false, error: 'No answer from the gateway' } : res));
  }), []);

  /** Route explorer: A* alternatives from the running simulation (the gateway waits up to 5 s). */
  const queryRoutes = useCallback((from, to, vclass) => ask('routes', { from, to, vclass }, 8000), [ask]);

  const startDrive = useCallback(async (from, to, kind) => {
    const res = await ask('driver:claim', kind ? { from, to, kind } : { from, to });
    if (res?.ok) {
      driveKey.current = res.key;
      buf.current.myDrive = { active: true, vid: null, from, to, since: Date.now(), idleMs: res.idle_ms,
        maxMs: res.max_ms, report: null, ended: null, last: null };
      dirty.current = true;
    }
    return res;
  }, [ask]);

  const driveCommand = useCallback((action, speedKmh) => (
    ask('driver:cmd', action === 'ease' ? { action, speed_kmh: speedKmh } : { action })
  ), [ask]);

  const endDrive = useCallback(() => ask('driver:release'), [ask]);

  const clearDrive = useCallback(() => {
    buf.current.myDrive = null;
    dirty.current = true;
  }, []);

  const value = { ...snap, connection, connectionNote, follow, startDrive, driveCommand, endDrive, clearDrive, queryRoutes };
  return createElement(LiveContext.Provider, { value }, children);
}

export function useLive() {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error('useLive must be used inside <LiveProvider>');
  return ctx;
}

/** Name used in the project brief; same hook. */
export const useLiveSocket = useLive;
