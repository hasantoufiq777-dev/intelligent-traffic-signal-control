import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { useLive } from '../hooks/useLiveSocket.js';

/**
 * "Open in SUMO-GUI": shows the live run in SUMO's own window on the computer running the gateway.
 * Shown only when the gateway says it is available for this browser (same computer, real simulation).
 */
export default function SumoGuiButton() {
  const { nowPlaying, connection } = useLive();
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const enabled = Boolean(nowPlaying?.gui);

  useEffect(() => {
    if (connection !== 'connected') return;
    api.sumoGui().then((r) => setAvailable(Boolean(r.available))).catch(() => setAvailable(false));
  }, [connection]);

  if (!available) return null;

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      await api.setSumoGui(!enabled);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={`btn${enabled ? '' : ' primary'}`}
        onClick={toggle}
        disabled={busy}
        title={enabled
          ? 'Close the SUMO window (the current run restarts without it)'
          : 'Show the live run in SUMO-GUI on this computer. The current run restarts from the beginning in the SUMO window.'}
      >
        {busy ? 'Working…' : enabled ? 'Close SUMO-GUI' : 'Open in SUMO-GUI'}
      </button>
      {error && <span className="chip bad" role="alert">{error}</span>}
    </>
  );
}
