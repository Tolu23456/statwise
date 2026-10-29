/**
 * Dependency-free static file server for the Expo web export.
 *
 * Used ONLY by Dockerfile.frontend: the image runs `expo export --platform
 * web` (a build step) and this script serves the resulting `dist/` folder.
 * It replaces the old `expo start --web` dev server, which shipped a
 * Metro/development runtime into the production image (audit M-32).
 *
 * Routing is tolerant of how Expo lays out a static export:
 *   /insights      -> dist/insights.html  OR  dist/insights/index.html
 *   /              -> dist/index.html
 *   /_something    -> exact file match
 *   anything else  -> dist/index.html (SPA fallback for client routes)
 *
 * Security: path traversal is blocked (resolved paths must stay under the
 * root), and only GET/HEAD are served. No dependencies, no eval, no shell.
 */
import { createServer } from 'node:http';
import { createReadStream, promises as fs } from 'node:fs';
import { extname, join, normalize, resolve, sep } from 'node:path';

const PORT = Number(process.env.PORT || 5000);
// Resolve the export dir relative to this script's location (/app/dist in the
// container). Overridable for local testing.
const ROOT = resolve(process.env.STATIC_ROOT || join(import.meta.dirname, '..', 'dist'));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.wasm': 'application/wasm',
};

async function isFile(p) {
  try {
    return (await fs.stat(p)).isFile();
  } catch {
    return false;
  }
}

/** First existing candidate wins; [] if none are files. */
async function candidates(pathname) {
  const rel = pathname.replace(/^\/+/, '');
  const base = join(ROOT, normalize(rel));
  // Path-traversal guard: the resolved path must remain under ROOT.
  if (base !== ROOT && !base.startsWith(ROOT + sep)) return [];
  const tries = pathname.endsWith('/')
    ? [join(base, 'index.html')]
    : [base, `${base}.html`, join(base, 'index.html')];
  const out = [];
  for (const c of tries) {
    if (await isFile(c)) out.push(c);
  }
  return out;
}

const server = createServer(async (req, res) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { Allow: 'GET, HEAD' });
    return res.end('Method Not Allowed');
  }
  let pathname = '/';
  try {
    pathname = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`).pathname;
  } catch {
    /* keep default '/' */
  }

  let file = (await candidates(pathname))[0];
  // SPA fallback: client-side routes that have no prerendered document.
  if (!file) file = (await candidates('/index.html'))[0];
  if (!file) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end('404 — not found');
  }

  const type = MIME[extname(file).toLowerCase()] || 'application/octet-stream';
  const headers = {
    'Content-Type': type,
    // Hashed asset names are immutable; HTML must revalidate so a redeploy
    // is picked up immediately.
    'Cache-Control': file.endsWith('.html')
      ? 'no-cache'
      : 'public, max-age=31536000, immutable',
  };
  if (req.method === 'HEAD') {
    res.writeHead(200, headers);
    return res.end();
  }
  const stream = createReadStream(file);
  stream.on('error', () => {
    if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('500 — read error');
  });
  stream.pipe(res.writeHead(200, headers));
});

server.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`statwise static server on :${PORT} (root ${ROOT})`);
});
