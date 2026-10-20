// Per-car comparison with HBEFA4 for the current run, refreshed while the page is visible.
import { useEffect, useState } from 'react';
import { api } from '../api.js';

const POLL_MS = 4000;

export function useEcoCars(enabled = true) {
  const [data, setData] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      api.ecoCars()
        .then((d) => { if (alive) { setData(d); setFailed(false); } })
        .catch(() => { if (alive) setFailed(true); });
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [enabled]);

  return { data, failed };
}
