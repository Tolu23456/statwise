/* Web push service worker (plan step 3.2).
 *
 * Registered by statwise/lib/push.ts. Receives Web Push messages from
 * ai/notify.py and shows them as system notifications; clicking one opens
 * the app.
 *
 * Keep this file dependency-free — it runs outside the bundle.
 */

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : '' };
  }
  const title = data.title || 'StatWise';
  const options = {
    body: data.body || '',
    icon: '/assets/images/icon.png',
    badge: '/assets/images/icon.png',
    data: { url: data.url || '/' },
    // Replaces a previous notification of the same tag (e.g. an updated
    // live score) instead of stacking duplicates.
    tag: data.tag || 'statwise',
    renotify: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      // Focus an already-open app window when possible.
      for (const client of list) {
        if ('focus' in client) return client.focus();
      }
      return self.clients.openWindow(url);
    }),
  );
});
