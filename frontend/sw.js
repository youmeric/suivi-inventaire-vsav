/* Service worker : réseau d'abord (jamais de version périmée), cache seulement en secours
   hors connexion pour ouvrir l'application. Les données (/api) ne sont jamais mises en cache :
   une date de péremption affichée doit toujours venir du serveur. */
const CACHE = "inventaire-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith((async () => {
    try {
      const resp = await fetch(req);
      if (resp.ok) {
        const copy = resp.clone();
        caches.open(CACHE).then((cache) => cache.put(req, copy));
      }
      return resp;
    } catch (err) {
      const cached = await caches.match(req);
      if (cached) return cached;
      if (req.mode === "navigate") {
        const home = await caches.match("/");
        if (home) return home;
      }
      throw err;
    }
  })());
});
