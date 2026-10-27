#!/usr/bin/env python3
"""StatWise daily metrics report (plan step 4.4: dashboard + alerts).

Read-only against Supabase (service role key — the only role that can read
`client_events`, and the role the backend already uses). Emits Markdown:

  * prediction volume last 24 h (total, by tier, AI Picks share)
  * settled accuracy (mirrors ai/settle_predictions.py exactly:
    a settled pick is correct iff actual_result == prediction)
  * booking-code pipeline health (attempts / successes / failures)
  * client telemetry last 24 h by kind (errors + React invariants + slow
    Supabase calls) — table created by supabase-telemetry-migration.sql
  * alert evaluation

Modes:
  default            report only — always exit 0, print the Markdown
  --enforce-alerts   exit 1 when a critical condition holds (CI mode):
                       - zero predictions published in the last 24 h
                         (the pipeline died silently)
                       - >= 25 client error events in the last 24 h
                         (the app is actively breaking for users)
  --prune            delete client_events older than 90 days (retention;
                     the migration comment documents this intent — the
                     workflow does it because pg_cron may not be enabled)

Output goes to stdout and, when present, appends to $GITHUB_STEP_SUMMARY.
Stdlib only — no pip install anywhere in CI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

# ─── configuration (mirrors statwise/lib/telemetry.ts constants) ────────────

SLOW_KINDS = ("render_error", "react_error", "uncaught")
ERROR_KINDS = SLOW_KINDS  # alias kept readable at call sites
ALERT_ERROR_EVENTS_24H = 25
ALERT_MIN_PREDICTIONS_24H = 1
CLIENT_EVENT_RETENTION_DAYS = 90
FETCH_CAP = 10000  # rows per query — enough for a day of events, bounded

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://fwpriiugfjhtoymcbmso.supabase.co").rstrip("/")
SERVICE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE")
    or ""
)


def _load_dotenv() -> None:
    """Local convenience only: CI injects env vars, the SQL editor is manual."""
    global SERVICE_KEY
    if SERVICE_KEY:
        return
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__)) + "/../.."):
        path = os.path.join(os.path.abspath(base), ".env")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key == "SUPABASE_SERVICE_KEY" and not SERVICE_KEY:
                    SERVICE_KEY = value
                elif key == "SUPABASE_URL" and "SUPABASE_URL" not in os.environ:
                    pass  # the hardcoded default is the canonical project
        if SERVICE_KEY:
            return


# ─── REST helper (service role — bypasses RLS, reads everything it needs) ───

def rest(method: str, path: str, body: Any = None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        SUPABASE_URL + path,
        data=data,
        method=method,
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode(errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        status = exc.code
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw[:400]


def select(path: str) -> list[dict] | None:
    """GET rows. None = the request itself failed (missing table/column);
    [] = succeeded with zero rows. Callers must treat those differently —
    an empty window is a signal, an errored one is not."""
    status, body = rest("GET", path)
    if status >= 400:
        return None
    return body if isinstance(body, list) else None


def table_missing(path: str) -> bool:
    status, body = rest("GET", path)
    text = json.dumps(body) if not isinstance(body, str) else body
    return status >= 400 and ("does not exist" in text or "PGRST205" in text or "schema cache" in text)


def iso(hours_ago: float) -> str:
    # Z-suffix form: PostgREST accepts it and, unlike "+00:00", it needs no
    # URL-encoding ('+' decodes to a space inside a query string).
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "n/a"


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


# ─── report sections ────────────────────────────────────────────────────────

def section_predictions() -> tuple[str, int, list[str]]:
    alerts: list[str] = []
    fresh = select(f"/rest/v1/predictions?select=tier,stats&created_at=gte.{iso(24)}&limit={FETCH_CAP}")

    if fresh is None:
        # The 24h window query itself failed (no created_at column / table
        # missing) — degrade to the full inventory for context, and say the
        # pipeline alert cannot be evaluated rather than pretending it passed.
        rows = select(f"/rest/v1/predictions?select=tier,stats&limit={FETCH_CAP}") or []
        inventory_note = " (created_at unavailable — full inventory shown)"
        fresh_count: int | None = None
    else:
        rows = fresh
        inventory_note = ""
        fresh_count = len(fresh)

    total = len(rows)
    ai_picks = sum(1 for r in rows if (r.get("stats") or {}).get("is_ai_pick") is True)
    by_tier: dict[str, int] = {}
    for r in rows:
        tier = r.get("tier") or "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1

    lines = ["## Predictions (last 24 h)", ""]
    if fresh_count is None:
        lines.append("- Window count: **unavailable** (24h query failed — "
                     "predictions table/columns missing?)")
    else:
        lines.append(f"- Total published{inventory_note}: **{fresh_count}**")
    lines.append(f"- AI Picks (stats.is_ai_pick): **{ai_picks}** of {total} known rows "
                 f"({pct(ai_picks, total)})")
    if by_tier:
        lines.append("- By tier: " + ", ".join(f"{k} {v}" for k, v in sorted(by_tier.items())))
    lines.append("")
    return "\n".join(lines), -1 if fresh_count is None else fresh_count, alerts


def section_settled_accuracy() -> str:
    rows = select(
        f"/rest/v1/predictions?select=prediction,actual_result,tier&status=eq.completed"
        f"&settled_at=gte.{iso(24 * 30)}&limit={FETCH_CAP}"
    )
    lines = ["## Settled accuracy (last 30 days)", ""]
    if rows is None:
        lines.append("_Query failed — predictions table/columns unavailable._")
        lines.append("")
        return "\n".join(lines)
    if not rows:
        lines.append("_No settled predictions in the window (or the settle workflow has not run)._")
        lines.append("")
        return "\n".join(lines)

    def accuracy(subset: list[dict]) -> str:
        if not subset:
            return "n/a (0 settled)"
        correct = sum(1 for r in subset if r.get("actual_result") == r.get("prediction"))
        return f"{pct(correct, len(subset))} ({correct}/{len(subset)})"

    lines.append(f"- Settled picks: **{len(rows)}**")
    lines.append(f"- Accuracy (actual_result == prediction): **{accuracy(rows)}**")
    by_tier: dict[str, list[dict]] = {}
    for r in rows:
        by_tier.setdefault(r.get("tier") or "unknown", []).append(r)
    for tier in sorted(by_tier):
        lines.append(f"- {tier}: {accuracy(by_tier[tier])}")
    lines.append("")
    return "\n".join(lines)


def section_booking() -> str:
    rows = select("/rest/v1/app_settings?setting_key=in.(booking_code,booking_code_stats)") or []
    stats = next((r for r in rows if r.get("setting_key") == "booking_code_stats"), None)
    code = next((r for r in rows if r.get("setting_key") == "booking_code"), None)

    lines = ["## Booking code pipeline (SportyBet NG)", ""]
    payload = (stats or {}).get("setting_value") or {}
    if stats:
        lines.append(
            f"- Generations: **{payload.get('attempts', 0)}** attempted, "
            f"**{payload.get('successes', 0)}** succeeded, "
            f"**{payload.get('failures', 0)}** failed"
        )
        if payload.get("last_error"):
            lines.append(f"- Last error: `{str(payload.get('last_error'))[:200]}` "
                         f"at {payload.get('last_error_at') or 'unknown time'}")
        if payload.get("last_success_at"):
            lines.append(f"- Last success: {payload['last_success_at']}")
    else:
        lines.append("_No booking_code_stats row yet (written by ai/sportybet_booking.py)._")

    if code and code.get("updated_at"):
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(
            code["updated_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        code_payload = code.get("setting_value") or {}
        lines.append(f"- Published code `{code_payload.get('code', '?')}` age: **{age_h:.1f} h**")
    else:
        lines.append("- No booking code currently published.")
    lines.append("")
    return "\n".join(lines)


def section_telemetry() -> tuple[str, int]:
    """Returns (markdown, error_event_count_24h)."""
    lines = ["## Client telemetry (last 24 h)", ""]
    if table_missing("/rest/v1/client_events?select=kind&limit=0"):
        lines.append("_`client_events` table missing — run "
                     "`statwise/supabase-telemetry-migration.sql`; until then the app's "
                     "telemetry is a silent no-op._")
        lines.append("")
        return "\n".join(lines), 0

    rows = select(f"/rest/v1/client_events?select=kind&created_at=gte.{iso(24)}&limit={FETCH_CAP}") or []
    by_kind: dict[str, int] = {}
    for r in rows:
        kind = r.get("kind") or "unknown"
        by_kind[kind] = by_kind.get(kind, 0) + 1

    errors = sum(by_kind.get(k, 0) for k in ERROR_KINDS)
    lines.append(f"- Events: **{len(rows)}** total, **{errors}** error events")
    for kind in sorted(by_kind):
        lines.append(f"  - {kind}: {by_kind[kind]}")

    slow = select(
        f"/rest/v1/client_events?select=context&kind=eq.slow_query"
        f"&created_at=gte.{iso(24)}&limit={FETCH_CAP}"
    ) or []
    durations = []
    for r in slow:
        ms = (r.get("context") or {}).get("ms")
        if isinstance(ms, (int, float)):
            durations.append(int(ms))
    if durations:
        lines.append(
            f"- Slow Supabase calls (≥1 s): **{len(durations)}** — "
            f"p50 {percentile(durations, 0.50)} ms, "
            f"p95 {percentile(durations, 0.95)} ms, "
            f"max {max(durations)} ms"
        )
    else:
        lines.append("- Slow Supabase calls (≥1 s): 0")
    lines.append("")
    return "\n".join(lines), errors


def section_experiments() -> str:
    """30-day A/B funnel: exposures → clicks (CTR) → conversions, per
    experiment and variant (plan step 4.5)."""
    lines = ["## Experiments & funnels (last 30 days)", ""]
    if table_missing("/rest/v1/client_events?select=kind&limit=0"):
        lines.append("_`client_events` table missing — run "
                     "`statwise/supabase-telemetry-migration.sql`._")
        lines.append("")
        return "\n".join(lines)

    rows = select(
        f"/rest/v1/client_events?select=message,context&kind=eq.custom"
        f"&message=in.(experiment_exposure,experiment_click,conversion)"
        f"&created_at=gte.{iso(24 * 30)}&limit={FETCH_CAP}"
    ) or []

    # experiment -> variant -> counters
    funnel: dict[str, dict[str, dict[str, int]]] = {}
    conversions_by_name: dict[str, int] = {}
    for row in rows:
        msg = row.get("message")
        ctx = row.get("context") or {}
        if msg == "conversion":
            name = str(ctx.get("name") or "unnamed")
            conversions_by_name[name] = conversions_by_name.get(name, 0) + 1
            # Variant-attributed conversions feed the experiment funnel.
            exp, var = ctx.get("experiment"), ctx.get("variant")
            if exp and var:
                funnel.setdefault(str(exp), {}).setdefault(str(var), {})["conversions"] = \
                    funnel[str(exp)][str(var)].get("conversions", 0) + 1
            continue
        exp, var = ctx.get("experiment"), ctx.get("variant")
        if not exp or not var:
            continue
        arm = funnel.setdefault(str(exp), {}).setdefault(str(var), {})
        key = "exposures" if msg == "experiment_exposure" else "clicks"
        arm[key] = arm.get(key, 0) + 1

    if not funnel:
        lines.append("_No experiment events yet — experiments ship dormant "
                     "(enrollment 0 in experiments.json); enable one to start "
                     "collecting._")
        lines.append("")
    else:
        for exp in sorted(funnel):
            lines.append(f"### `{exp}`")
            for var in sorted(funnel[exp]):
                arm = funnel[exp][var]
                exposures = arm.get("exposures", 0)
                clicks = arm.get("clicks", 0)
                convs = arm.get("conversions", 0)
                lines.append(
                    f"- {var}: {exposures} exposures, CTR {pct(clicks, exposures)}, "
                    f"conversions {convs} ({pct(convs, exposures)})"
                )
            lines.append("")

    if conversions_by_name:
        lines.append("### Conversions (all traffic)")
        for name in sorted(conversions_by_name):
            lines.append(f"- {name}: {conversions_by_name[name]}")
        lines.append("")
    return "\n".join(lines)


def evaluate_alerts(prediction_total: int, error_events: int) -> list[str]:
    """prediction_total: -1 means the window could not be evaluated (the
    section already says so) — only a definitive 0 alerts."""
    alerts: list[str] = []
    if prediction_total == 0:
        alerts.append(
            f"PREDICTION PIPELINE DOWN: {prediction_total} predictions published in the "
            f"last 24h (expected >= {ALERT_MIN_PREDICTIONS_24H})."
        )
    if error_events >= ALERT_ERROR_EVENTS_24H:
        alerts.append(
            f"CLIENT ERRORS HIGH: {error_events} error events in the last 24h "
            f"(threshold {ALERT_ERROR_EVENTS_24H}). Check client_events rows for the "
            f"dominant kind/message."
        )
    return alerts


def prune_old_events() -> str:
    cutoff = iso(CLIENT_EVENT_RETENTION_DAYS * 24)
    status, body = rest("DELETE", f"/rest/v1/client_events?created_at=lt.{urllib.parse.quote(cutoff)}")
    if status >= 400:
        return f"- Retention prune: skipped ({str(body)[:120]})"
    return f"- Retention prune: client_events older than {CLIENT_EVENT_RETENTION_DAYS} days removed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enforce-alerts", action="store_true",
                        help="exit 1 on critical conditions (CI mode)")
    parser.add_argument("--prune", action="store_true",
                        help="delete client_events older than the retention window")
    args = parser.parse_args()

    _load_dotenv()
    if not SERVICE_KEY:
        print("ERROR: SUPABASE_SERVICE_KEY (service role) is not set.", file=sys.stderr)
        return 2 if args.enforce_alerts else 0

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pred_md, prediction_total, section_alerts = section_predictions()
    parts = [
        f"# StatWise daily metrics — {generated}",
        "",
        pred_md,
        section_settled_accuracy(),
        section_booking(),
    ]
    tel_md, error_events = section_telemetry()
    parts.append(tel_md)
    parts.append(section_experiments())

    alerts = section_alerts + evaluate_alerts(prediction_total, error_events)
    parts.append("## Alerts")
    parts.append("")
    if alerts:
        for alert in alerts:
            parts.append(f"- :rotating_light: **{alert}**")
    else:
        parts.append("- All clear.")
    if args.prune:
        parts.append(prune_old_events())
    parts.append("")

    report = "\n".join(parts)
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf8") as fh:
            fh.write(report + "\n")

    if args.enforce_alerts and alerts:
        print(f"\n{len(alerts)} alert condition(s) — failing the run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
