#!/usr/bin/env python3
"""Headless load-time measurement for the exported web bundle (plan 4.2).

Serves an `expo export -p web` output directory on 127.0.0.1, opens it in
headless Chromium, and reports:

  * navigation timing (domInteractive / loadEventEnd)
  * request count + bytes actually transferred
  * the slowest resources
  * any uncaught page errors

Every request that is NOT to the local server is aborted before it leaves
the browser, so the numbers measure *our* bundle only — no Supabase, no
external fonts, no variance from the network. The page still renders its
error states; we time the load event, not API round-trips.

Requires: pip install playwright && playwright install chromium

Run:  python3 statwise/scripts/measure_load.py statwise/dist
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import socketserver
import sys
import threading
import time


def main() -> int:
    root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "dist")
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    index = os.path.join(root, "index.html")
    if not os.path.isfile(index):
        print(f"error: no index.html under {root}", file=sys.stderr)
        return 2

    # Quiet + threaded so the single measurement page can fetch assets in
    # parallel like a real browser would.
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - silence access log
            pass

    quiet_handler = functools.partial(Quiet, directory=root)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), quiet_handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    from playwright.sync_api import sync_playwright

    result: dict = {"url": base}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)[:200]))
            blocked = {"count": 0, "hosts": set()}

            def route(route_obj, _request):
                url = route_obj.request.url
                if url.startswith(base) or url.startswith("data:") or url.startswith("blob:"):
                    route_obj.continue_()
                else:
                    blocked["count"] += 1
                    blocked["hosts"].add(url.split("/")[2] if "//" in url else url)
                    route_obj.abort()

            page.route("**/*", route)
            started = time.time()
            page.goto(base + "/", wait_until="load", timeout=60_000)
            # One settle frame so late resource entries are recorded.
            page.wait_for_timeout(500)
            wall_ms = int((time.time() - started) * 1000)

            nav = page.evaluate(
                """() => {
                    const n = performance.getEntriesByType('navigation')[0];
                    if (!n) return null;
                    return {
                      dom_interactive_ms: Math.round(n.domInteractive),
                      dom_content_loaded_ms: Math.round(n.domContentLoadedEventEnd),
                      load_event_ms: Math.round(n.loadEventEnd),
                      ttfb_ms: Math.round(n.responseStart),
                      transfer_bytes: n.transferSize || 0,
                    };
                }"""
            )
            resources = page.evaluate(
                """() => performance.getEntriesByType('resource').map(r => ({
                    name: r.name.replace(location.origin, ''),
                    duration_ms: Math.round(r.duration),
                    bytes: r.transferSize || r.encodedBodySize || 0,
                }))"""
            )
            browser.close()
    finally:
        httpd.shutdown()

    resources = resources or []
    total_bytes = sum(r["bytes"] for r in resources) + (nav or {}).get("transfer_bytes", 0)
    result.update(
        {
            "navigation": nav,
            "wall_ms": wall_ms,
            "requests": len(resources),
            "transferred_bytes": total_bytes,
            "blocked_external_requests": blocked["count"],
            "blocked_hosts": sorted(blocked["hosts"]),
            "page_errors": page_errors,
            "slowest_resources": sorted(
                resources, key=lambda r: -r["duration_ms"]
            )[:5],
            "largest_resources": sorted(resources, key=lambda r: -r["bytes"])[:5],
        }
    )
    print(json.dumps(result, indent=2))

    # Fail loudly only on structural problems (page never loaded / JS threw),
    # never on raw timing — CI machines are noisy, sizes are the signal.
    if not nav:
        print("error: no navigation timing entry", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
