// sw.js

const CACHE_NAME = 'statwise-offline-v1';
const OFFLINE_URL = './Offline/offline.html';

// A list of all the assets needed for the offline page to work correctly.
const OFFLINE_ASSETS = [
    OFFLINE_URL,
    './Offline/offline.css',
    './Assets/Fonts/Optimistic_Text_A_Md.ttf'
];

/**
 * 1. Install the service worker and cache the offline assets.
 */
self.addEventListener('install', (event) => {
    event.waitUntil(
        (async () => {
            const cache = await caches.open(CACHE_NAME);
            console.log('[Service Worker] Caching offline assets');
            await cache.addAll(OFFLINE_ASSETS);
        })()
    );
    // Force the waiting service worker to become the active service worker.
    self.skipWaiting();
});

/**
 * 2. Clean up old caches on activation.
 */
self.addEventListener('activate', (event) => {
    event.waitUntil(
        (async () => {
            // Delete old caches that are not in use.
            const cacheNames = await caches.keys();
            await Promise.all(
                cacheNames.filter(name => name !== CACHE_NAME).map(name => caches.delete(name))
            );
        })()
    );
    // Tell the active service worker to take control of the page immediately.
    self.clients.claim();
});

/**
 * 3. Intercept fetch requests and serve the offline page if a navigation request fails.
 */
self.addEventListener('fetch', (event) => {
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).catch(() => caches.match(OFFLINE_URL))
        );
    }
});