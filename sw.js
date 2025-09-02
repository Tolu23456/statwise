// sw.js

// Import and initialize the Firebase SDK
importScripts("https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.12.0/firebase-messaging-compat.js");

// Your web app's Firebase configuration
const CACHE_NAME = 'statwise-offline-v1';
const OFFLINE_URL = './Offline/offline.html';

// A list of all the assets needed for the offline page to work correctly.
const OFFLINE_ASSETS = [
    OFFLINE_URL,
    './Offline/offline.css',
    './Assets/Fonts/Optimistic_Text_A_Md.ttf'
];

const firebaseConfig = {
  apiKey: "AIzaSyDpPTmDw7RpxTo2AXf8ZDTq4AG46xKB16g",
  authDomain: "statwise-319a4.firebaseapp.com",
  databaseURL: "https://statwise-319a4-default-rtdb.firebaseio.com",
  projectId: "statwise-319a4",
  storageBucket: "statwise-319a4.firebasestorage.app",
  messagingSenderId: "416700134653",
  appId: "1:416700134653:web:f3a6f9766a2fafa8fdba94",
};

firebase.initializeApp(firebaseConfig);
const messaging = firebase.messaging();
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

/**
 * 4. Handle background push notifications.
 */
messaging.onBackgroundMessage((payload) => {
    console.log('[sw.js] Received background message ', payload);

    const notificationTitle = payload.notification.title;
    const notificationOptions = {
        body: payload.notification.body,
        icon: './Assets/Icons/icon-192.png' // Add an icon for notifications
    };

    self.registration.showNotification(notificationTitle, notificationOptions);
});