// Service worker of the installable app (PWA).
// - Makes the dashboard installable and opens it without the browser frame.
// - Keeps the app shell (page, scripts, styles, icons) so it starts fast and shows an offline page
//   when there is no connection.
// - Never stores live data: /api and /socket.io always go to the network, so the app shows the
//   real simulation or nothing.
const VERSION = 'itsc-app-v1';
const SHELL = ['/', '/offline.html', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png'];

// Install: keep the app shell
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

// Activate: remove the stores of older versions
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Stores a copy of a good response for later.
function keep(request, response) {
  if (response && response.ok && response.type === 'basic') {
    const copy = response.clone();
    caches.open(VERSION).then((cache) => cache.put(request, copy));
  }
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/socket.io/')) return; // live data: network only

  // Pages: the newest from the network; offline, the stored page, or the offline notice
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((res) => keep('/', res))
        .catch(() => caches.match('/').then((page) => page || caches.match('/offline.html'))),
    );
    return;
  }

  // Built files have a content hash in their name and never change: stored copy first
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request).then((res) => keep(request, res))));
    return;
  }

  // Icons and the manifest: stored copy at once, refreshed in the background
  event.respondWith(
    caches.match(request).then((hit) => {
      const fresh = fetch(request).then((res) => keep(request, res)).catch(() => hit);
      return hit || fresh;
    }),
  );
});
