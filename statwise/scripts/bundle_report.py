#!/usr/bin/env python3
"""Production bundle size report (plan step 4.2 — "Analyze bundle").

Takes an `expo export -p web` output directory and prints, in plain text:

  * total + gzipped size, file counts
  * a per-type breakdown (js / ttf / png / html / other)
  * the 20 largest files
  * an explicit font inventory — this is the tree-shaking receipt: the app
    renders 4 Inter weights and Ionicons only, so anything else in here
    is weight we ship but never draw.

Stdlib only — runs anywhere (CI runner or local).

Run:  python3 statwise/scripts/bundle_report.py statwise/dist
"""
from __future__ import annotations

import gzip
import os
import sys
from collections import defaultdict

TOP_N = 20


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else "dist"
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    files: list[tuple[str, int]] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                files.append((os.path.relpath(path, root), os.path.getsize(path)))
            except OSError:
                continue

    if not files:
        print(f"error: no files under {root}", file=sys.stderr)
        return 2

    total = sum(size for _, size in files)
    gz_total = 0
    for rel, size in files:
        # Text compresses; binary (fonts/images) barely does. Gzipping every
        # file keeps the "what would the wire actually carry" number honest.
        with open(os.path.join(root, rel), "rb") as fh:
            data = fh.read()
        gz_total += len(gzip.compress(data, 6)) if size > 0 else 0

    by_type: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # count, bytes
    for rel, size in files:
        ext = os.path.splitext(rel)[1].lower().lstrip(".") or "noext"
        by_type[ext][0] += 1
        by_type[ext][1] += size

    print(f"== bundle report: {root} ==")
    print(f"files          : {len(files)}")
    print(f"total size     : {human(total)}")
    print(f"gzip'd total   : {human(gz_total)}  (simulated, level 6)")
    print()
    print("-- by type --")
    for ext, (count, size) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
        print(f"  {ext:<8} {count:>4} file(s)  {human(size):>10}")

    print()
    print(f"-- top {TOP_N} files --")
    for rel, size in sorted(files, key=lambda kv: -kv[1])[:TOP_N]:
        print(f"  {human(size):>10}  {rel}")

    # Tree-shaking receipt: font inventory.
    fonts = sorted((r, s) for r, s in files if r.lower().endswith(".ttf"))
    print()
    print(f"-- font inventory ({len(fonts)} ttf, {human(sum(s for _, s in fonts))}) --")
    inter = [r for r, _ in fonts if "google-fonts" in r or "Inter" in r]
    icons = [r for r, _ in fonts if "vector-icons" in r]
    print(f"  Inter faces   : {len(inter)} (app renders 4)")
    for r in inter:
        print(f"    {r}")
    print(f"  icon fonts    : {len(icons)} (app renders Ionicons only)")
    for r in icons:
        print(f"    {r}")
    other = [r for r, _ in fonts if r not in inter and r not in icons]
    if other:
        print(f"  other fonts   : {len(other)}")
        for r in other:
            print(f"    {r}")

    # The headline number the plan boxes care about.
    js = [s for r, s in files if r.endswith(".js")]
    print()
    print(f"js bundle      : {human(sum(js))} across {len(js)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
