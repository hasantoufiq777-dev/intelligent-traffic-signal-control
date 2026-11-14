function describe(entry) {
  if (!entry) return null;
  const parts = [entry.scenario, entry.controller];
  if (entry.nav && entry.nav !== 'none') parts.push(`nav ${entry.nav}`);
  if (entry.eco_fraction > 0) parts.push(`eco ${Math.round(entry.eco_fraction * 100)}%`);
  return parts.join(' · ');
}

export function ConnectionBadge({ connection }) {
  const map = {
    connected: ['conn-ok', 'Live'],
    connecting: ['conn-wait', 'Connecting…'],
    reconnecting: ['conn-wait', 'Reconnecting…'],
    offline: ['conn-bad', 'Offline'],
    capacity: ['conn-bad', 'Platform full'],
  };
  const [cls, text] = map[connection] || map.connecting;
  return (
    <span className="chip" role="status"><span className={`dot ${cls}`} aria-hidden="true" />{text}</span>
  );
}

// What the gateway says the simulation is doing; the page never shows "running" on its own guess
const LIFECYCLE = {
  STARTING: 'Starting…', RUNNING: 'Now playing', STOPPING: 'Stopping…', STOPPED: 'Idle',
  FAILED: 'Simulation failed', RECOVERING: 'Recovering…',
};

export default function NowPlaying({ nowPlaying, latest, viewers, status }) {
  const cur = nowPlaying?.current;
  const entry = cur?.entry;
  const running = nowPlaying?.running;
  const state = nowPlaying?.lifecycle;
  const failed = state === 'FAILED' || state === 'RECOVERING';
  // Pause and speed as confirmed by the simulation (not as requested)
  const paused = running && state === 'RUNNING' && nowPlaying?.playback?.paused;
  const speed = nowPlaying?.playback?.speed ?? 1;
  return (
    <div className="banner" role="region" aria-label="Now playing">
      <span className={`chip${failed ? ' bad' : paused ? ' warn' : ''}`} role="status">
        {paused ? 'Paused' : LIFECYCLE[state] ?? (running ? 'Now playing' : 'Idle')}
      </span>
      <span className="label">{entry?.label || describe(entry) || 'No simulation running'}</span>
      {entry?.label && <span className="muted small">{describe(entry)}</span>}
      {latest?.t != null && running && <span className="chip num">t = {latest.t} s</span>}
      {running && speed !== 1 && <span className="chip num">{speed}× speed</span>}
      {!running && status?.status && <span className="chip">last run: {status.status}</span>}
      <span style={{ marginLeft: 'auto' }} className="row">
        {nowPlaying?.next && <span className="small muted">Next: {nowPlaying.next.label || describe(nowPlaying.next)}</span>}
        {viewers != null && <span className="chip num">{viewers} watching</span>}
      </span>
    </div>
  );
}
