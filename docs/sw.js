/* Minervini Tracker service worker — network-first with offline fallback.
   The site changes weekly; always prefer fresh files, keep the last good copy
   so the app still opens (with last week's data) when offline. */
var CACHE = "minervini-v3";
var SHELL = ["./", "index.html", "app.css?v=2", "app.js?v=2", "manifest.webmanifest",
             "icons/icon-192.png", "icons/icon-512.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(SHELL); })
    .then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  if (e.request.method !== "GET") return;
  e.respondWith(
    fetch(e.request).then(function (resp) {
      if (resp.ok && new URL(e.request.url).origin === self.location.origin) {
        var copy = resp.clone();
        caches.open(CACHE).then(function (c) { c.put(e.request, copy); });
      }
      return resp;
    }).catch(function () { return caches.match(e.request); })
  );
});
