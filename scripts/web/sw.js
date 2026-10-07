// A service worker because an installed app wants one, and nothing more: it caches
// nothing and answers nothing itself, so the page is always the machine's live one. It
// also clears any cache an earlier version of this file kept.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", () => {});
