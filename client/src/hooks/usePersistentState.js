// Page state that survives moving between pages and reloading this browser tab.
import { useEffect, useState } from 'react';

const PREFIX = 'itsc-ui:';

// Like useState, but the value is kept for this tab under `key`. Storage may be unavailable: then it
// behaves like plain useState.
export function usePersistentState(key, initial) {
  const fullKey = PREFIX + key;
  const [value, setValue] = useState(() => {
    try {
      const raw = sessionStorage.getItem(fullKey);
      if (raw !== null) return JSON.parse(raw);
    } catch {
      /* fall back to the initial value */
    }
    return typeof initial === 'function' ? initial() : initial;
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(fullKey, JSON.stringify(value));
    } catch {
      /* convenience only */
    }
  }, [fullKey, value]);
  return [value, setValue];
}

// Forget this tab's page state, so the next user who signs in here starts from the defaults.
export function clearPersistentState() {
  try {
    for (const key of Object.keys(sessionStorage)) if (key.startsWith(PREFIX)) sessionStorage.removeItem(key);
  } catch {
    /* nothing to clear */
  }
}
