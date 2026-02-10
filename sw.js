const CACHE_NAME = "trivial-endgames-v1";

// Rules:
// /data/manifest.json : network-first (or bypass cache)
// /data/*.txt : cache-first (versioned URLs)

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Cache-first for manifest and versioned data files
  if (url.pathname.includes("/data/") && (url.pathname.endsWith(".txt") || url.pathname.endsWith(".json"))) {
    event.respondWith(
      caches.open(CACHE_NAME).then((cache) => {
        return cache.match(event.request).then((response) => {
          return response || fetch(event.request).then((networkResponse) => {
            cache.put(event.request, networkResponse.clone());
            return networkResponse;
          });
        });
      })
    );
    return;
  }

  // Default: Network only (or add more rules for assets if needed)
});
