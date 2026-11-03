// One shared connection to the gateway's live channel.
// Messages land in mutable buffers; React state is committed at most every COMMIT_MS (about 2 renders/s),
// so a busy stream never re-renders the page on every message.
import { createContext, createElement, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { io } from 'socket.io-client';
import { API_BASE } from '../api.js';

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
  };
}

export function LiveProvider({ children }) {
  const buf = useRef(emptyBuffers());
  const dirty = useRef(true);
  const followId = useRef(null);
  const socketRef = useRef(null);
  const [snap, setSnap] = useState(() => ({ ...emptyBuffers(), connection: 'connecting', followId: null }));
  const [connection, setConnection] = useState('connecting');

  useEffect(() => {
    const socket = io(API_BASE || undefined, { transports: ['websocket', 'polling'], reconnectionDelayMax: 10_000 });
    socketRef.current = socket;
    const b = buf.current;
    const touch = () => { dirty.current = true; };

    socket.on('connect', () => {
      setConnection('connected');
      if (followId.current) socket.emit('follow', followId.current); // re-subscribe after a reconnect
    });
    socket.on('disconnect', () => setConnection('reconnecting'));
    socket.io.on('reconnect_attempt', () => setConnection('reconnecting'));
    socket.on('connect_error', () => setConnection('offline'));

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
      socket.close();
    };
  }, []);

  const follow = useCallback((id) => {
    followId.current = id || null;
    buf.current.followed = null;
    dirty.current = true;
    const s = socketRef.current;
    if (!s) return;
    if (id) s.emit('follow', id);
    else s.emit('unfollow');
  }, []);

  const value = { ...snap, connection, follow };
  return createElement(LiveContext.Provider, { value }, children);
}

export function useLive() {
  const ctx = useContext(LiveContext);
  if (!ctx) throw new Error('useLive must be used inside <LiveProvider>');
  return ctx;
}

/** Name used in the project brief; same hook. */
export const useLiveSocket = useLive;
