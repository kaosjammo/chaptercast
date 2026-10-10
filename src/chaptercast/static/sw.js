"use strict";

// Bump this version whenever the shell changes. Never add private/API/audio URLs.
const SHELL_CACHE = "chaptercast-shell-v3";
const SHELL_PATHS = ["/", "/static/style.css", "/static/app.js",
  "/static/media-session.js", "/static/manifest.webmanifest",
  "/static/icons/icon-192.png", "/static/icons/icon-512.png", "/static/icons/icon-maskable-512.png"];

async function publicShell(path) {
  // Construct a fresh request: no cookies, tokens, original headers or redirects.
  const request = new Request(new URL(path, self.location.origin), {
    credentials: "omit", cache: "no-store", redirect: "error",
  });
  const response = await fetch(request);
  if (!response.ok || response.status !== 200 || response.redirected) throw new Error("Shell unavailable");
  return response;
}
self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    for (const path of SHELL_PATHS) await cache.put(path, await publicShell(path));
    await self.skipWaiting();
  })());
});
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith("chaptercast-shell-") && name !== SHELL_CACHE) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});
self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin || url.search ||
      !SHELL_PATHS.includes(url.pathname) || request.headers.has("Authorization") ||
      request.headers.has("Range")) return;
  // Use the installed, versioned shell as a coherent unit. No runtime cache growth.
  event.respondWith((async () => {
    const cached = await (await caches.open(SHELL_CACHE)).match(url.pathname);
    return cached || publicShell(url.pathname);
  })());
});
