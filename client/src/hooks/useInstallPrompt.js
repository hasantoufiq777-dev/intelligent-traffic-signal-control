// "Install app": offers the browser's install dialog when the browser allows it (Chrome, Edge,
// Android). Safari on iPhone has no such dialog: there the user taps Share, then "Add to Home Screen".
import { useEffect, useState } from 'react';

// The browser fires this once per page; keep it so a component mounted later can still use it.
let deferred = null;
const listeners = new Set();
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault(); // show our own button instead of the browser's mini bar
    deferred = e;
    listeners.forEach((fn) => fn());
  });
  window.addEventListener('appinstalled', () => {
    deferred = null;
    listeners.forEach((fn) => fn());
  });
}

// Whether the dashboard already runs as an installed app.
export function isInstalledApp() {
  try {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  } catch {
    return false;
  }
}

// { canInstall, install }: install() opens the browser's dialog and resolves to true if accepted.
export function useInstallPrompt() {
  const [, rerender] = useState(0);
  useEffect(() => {
    const fn = () => rerender((n) => n + 1);
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  const install = async () => {
    if (!deferred) return false;
    const e = deferred;
    deferred = null;
    e.prompt();
    const choice = await e.userChoice.catch(() => ({ outcome: 'dismissed' }));
    listeners.forEach((fn) => fn());
    return choice.outcome === 'accepted';
  };
  return { canInstall: Boolean(deferred) && !isInstalledApp(), install };
}

// Registers the service worker (only in the built app: the dev server reloads files itself).
export function registerServiceWorker() {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* the dashboard works without it, only not installable/offline */
    });
  });
}
