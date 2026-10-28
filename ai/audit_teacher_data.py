#!/usr/bin/env python3
"""Date-truncate the clean season files for the AUDIT's Monte Carlo teacher.

The 100k-holdout audit (ai/audit_100k.py) holds out the most recent rows
by date (boundary 2019-12-20 on the current corpus).  A Monte Carlo teacher
distilled into that audit must never have SEEN any row after the audit
split, so ai/sim/run.py is pointed at a copy of ai/data/clean/*_matches.csv
with every row after AUDIT_CUTOFF_DATE removed.  The lab's own
--holdout-days split then happens entirely INSIDE the audit's TRAIN
window, and ai/sim/distill.py's freshness guard accepts the resulting
recipe (teacher split_at < audit split_at) legitimately — nothing is
bypassed, the teacher simply predates the holdout.

The output directory holds ONLY truncated CSVs (gitignored build products;
the merged recipe — ai/data/sim_audit/worlds.json — is what gets committed).
ai/sim/** is read-only for this pipeline: only the input files are copied.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLEAN_GLOB = os.path.join(BASE_DIR, "data", "clean", "????_matches.csv")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cutoff", default=os.environ.get(
        "AUDIT_CUTOFF_DATE", "2019-12-20"),
        help="keep rows with date <= this (inclusive)")
    ap.add_argument("--out-dir", default="simdata")
    ap.add_argument("--glob", default=CLEAN_GLOB)
    args = ap.parse_args()

    cutoff = pd.Timestamp(args.cutoff)
    paths = sorted(glob.glob(args.glob))
    if not paths:
        print(f"::error::no season files matching {args.glob}")
        return 1
    os.makedirs(args.out_dir, exist_ok=True)

    kept_total = 0
    max_date = None
    for p in paths:
        df = pd.read_csv(p, low_memory=False)
        if "date" not in df.columns:
            continue
        d = pd.to_datetime(df["date"], errors="coerce")
        keep = (d <= cutoff).fillna(False)
        out = df[keep]
        kept_total += len(out)
        if len(out):
            m = d[keep].max()
            max_date = m if max_date is None or m > max_date else max_date
        out.to_csv(os.path.join(args.out_dir, os.path.basename(p)), index=False)
    print(f"teacher data: kept {kept_total:,} rows <= {cutoff.date()} "
          f"(max date {max_date.date() if max_date is not None else 'n/a'}) "
          f"in {args.out_dir}/ from {len(paths)} files")
    if max_date is None or max_date <= cutoff - pd.Timedelta(days=30):
        print("::warning::truncated frame ends suspiciously early — the "
              "teacher would be fit on far less history than the audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
