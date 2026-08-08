// shareware service worker.
//
// Exists to make the app installable (Chrome requires a worker with a fetch
// handler), and gets offline shell loading as a side effect. It is deliberately
// small: a worker is the one piece of this app that can outlive a bad deploy.
//
// ONE POLICY, network-first: the network answer always wins when it is
// available, and the cache is only ever consulted after a fetch has failed.
// This is the whole point. A cache-first worker on a static host is how you
// ship an index.html your users cannot escape without clearing site data --
// see the deployment gotchas in DEPLOY.md. Nothing here can serve stale code
// to somebody who is online.
//
// Explicitly NOT intercepted -- these never reach respondWith, so they behave
// exactly as if no worker were installed:
//   * anything that is not a GET   -- writes must never be cached or replayed
//   * the Supabase origin          -- auth tokens and ledger rows. A cached
//                                     balance is a WRONG balance, and this app
//                                     derives every balance on read for exactly
//                                     that reason.
//   * icons and the manifest       -- tiny, and already handled by HTTP caching

const VERSION = 'shareware-v1';
const SHELL = new URL('./', self.location).href;
const BOOT = 'https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2';

self.addEventListener('install', (e) => {
  // Only the shell is precached. BOOT is third-party: putting it in addAll()
  // would mean a jsdelivr blip fails the whole install and leaves no worker at
  // all. It gets cached opportunistically on first successful fetch instead.
  e.waitUntil(
    caches.open(VERSION)
      .then((c) => c.add(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)),
      ))
      .then(() => self.clients.claim()),
  );
});

// Try the network; remember what came back; fall back to the last good copy
// only if the network could not answer at all.
async function networkFirst(req, key) {
  try {
    const res = await fetch(req);
    if (res && res.ok) {
      const copy = res.clone();
      caches.open(VERSION).then((c) => c.put(key, copy));
    }
    return res;
  } catch (err) {
    const hit = await caches.match(key);
    if (hit) return hit;
    throw err;
  }
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  if (req.mode === 'navigate') return e.respondWith(networkFirst(req, SHELL));
  if (req.url === BOOT) return e.respondWith(networkFirst(req, BOOT));
  // everything else, Supabase included, goes straight to the network
});
