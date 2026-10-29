/**
 * StatWise payment verification poller (plan 5.1).
 *
 * Runs in GitHub Actions every 3 minutes (`.github/workflows/verify-payments.yml`)
 * with the service-role key and the Flutterwave v3 secret key. The flow:
 *
 *   1. rows past the 24 h TTL get ONE FINAL Flutterwave verification BEFORE
 *      they are expired — a late-settled bank transfer is credited instead of
 *      silently thrown away, and a row only expires when Flutterwave says
 *      definitively "no transaction for this ref" (audit M-24: the old code
 *      expired first and never asked Flutterwave at all);
 *   2. for each fresh pending event: Flutterwave
 *      `GET /v3/transactions/verify_by_reference?tx_ref=…`;
 *   3. adjudicate (`adjudicateVerification` in lib/paymentCore.ts — the same
 *      pure function unit-tested in CI): credit / fail / keep waiting;
 *   4. credit atomically through the `apply_payment_verification` RPC
 *      (ledger insert + tier grant + event flip in ONE transaction,
 *      service-role-only — clients cannot execute it, and a DB trigger
 *      blocks any direct tier write with `auth.uid() is not null`).
 *
 * Queue-flooding defence (audit H-4) — a burst of abandoned checkouts used
 * to fill the whole run so genuine purchases were never looked at and aged
 * out at 24 h unverified:
 *   * every run processes at most MAX_FRESH_PER_RUN fresh rows and
 *     MAX_STALE_PER_RUN expiry candidates (the rest are picked up FIFO next
 *     run — the queue drains at a bounded rate instead of O(queue));
 *   * at most MAX_PER_USER_PER_RUN rows per account per run, so one user's
 *     burst cannot occupy a whole run;
 *   * listing is `order=expires_at.asc` — `payment_events.expires_at` is
 *     `created_at + 24 h` (section 13 of statwise/supabase-audit-hardening.sql)
 *     — i.e. rows CLOSEST TO EXPIRY FIRST, with an automatic fallback to the
 *     equivalent `created_at.asc` order (identical: constant offset) while
 *     that migration is not applied yet, so the poller never depends on it;
 *   * anything that ages past the TTL while waiting behind the cap still
 *     gets its final verification in step 1 before anything is expired;
 *   * and the DB side caps the queue at the source too: section 14 of the
 *     same migration allows at most 3 open pending checkouts per account
 *     (mirrors MAX_PENDING_CHECKOUTS in app/(tabs)/subscriptions.tsx).
 *
 * Everything is idempotent: re-running a completed event returns `already`,
 * the ledger insert is `ON CONFLICT (transaction_id) DO NOTHING`, and the
 * workflow has a single concurrency group.
 *
 *   npx tsc statwise/lib/paymentCore.ts statwise/scripts/verify-payments.ts …
 *   node …/verify-payments.js            # writes
 *   node …/verify-payments.js --dry-run  # decisions only, no writes
 *                                         # (Flutterwave GETs still happen)
 *
 * Exit codes: 0 = processed (incl. "nothing to do" / table not migrated yet,
 * which prints a ::warning:: annotation), 1 = configuration or infrastructure
 * failure (missing secrets, rejected key, Supabase errors) so the run goes
 * red and GitHub emails the maintainer.
 */
import {
  EVENT_TTL_MS,
  FlwVerification,
  PaymentEventRow,
  adjudicateVerification,
} from '../lib/paymentCore';

// Read env/argv off globalThis so this file compiles both under the app's
// tsconfig (no @types/node) and under the workflow's bare `tsc` invocation.
const g = globalThis as {
  process?: {
    env?: Record<string, string | undefined>;
    argv?: string[];
    exitCode?: number;
  };
};
const env = (k: string): string => g.process?.env?.[k] ?? '';
const DRY = (g.process?.argv ?? []).includes('--dry-run');
const setFail = (): void => {
  if (g.process) g.process.exitCode = 1;
};

const FLW_VERIFY_URL = 'https://api.flutterwave.com/v3/transactions/verify_by_reference';

// ── run bounds (H-4) ───────────────────────────────────────────────────────
/** Rows pulled per listing pass; the run then trims these to its caps. */
const FETCH_LIMIT = 100;
/** Fresh rows adjudicated per run. */
const MAX_FRESH_PER_RUN = 25;
/** Expiry candidates given their final verification per run. */
const MAX_STALE_PER_RUN = 25;
/** Rows per account per run — one account cannot eat a whole run. */
const MAX_PER_USER_PER_RUN = 5;
/** Older than this a Flutterwave transaction can no longer settle. */
const HARD_EXPIRE_MS = 7 * 24 * 60 * 60 * 1000;

// ── transport guards ───────────────────────────────────────────────────────
const FLW_TIMEOUT_MS = 20_000;
const SUPA_TIMEOUT_MS = 20_000;
/** Supabase/Flutterwave JSON is KiB-scale; anything MB-scale is an error page. */
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;

const log = (msg: string): void => { console.log(msg); };
const warn = (msg: string): void => { console.log(`::warning::${msg}`); };
const fail = (msg: string): void => { console.log(`::error::${msg}`); };

/** AbortSignal.timeout where available, controller+timer otherwise. */
function timeoutSignal(ms: number): AbortSignal | undefined {
  const AS = typeof AbortSignal !== 'undefined' ? (AbortSignal as unknown as {
    timeout?: (n: number) => AbortSignal;
  }) : undefined;
  if (AS && typeof AS.timeout === 'function') return AS.timeout(ms);
  if (typeof AbortController !== 'undefined') {
    const c = new AbortController();
    setTimeout(() => c.abort(), ms);
    return c.signal;
  }
  return undefined;
}

/** Read a response body with a hard size cap (null = oversized/unreadable). */
async function readBodyCapped(res: Response): Promise<string | null> {
  const declared = Number(res.headers.get('content-length') ?? '0');
  if (Number.isFinite(declared) && declared > MAX_RESPONSE_BYTES) return null;
  if (!res.body || typeof res.body.getReader !== 'function') {
    const text = await res.text();
    return text.length > MAX_RESPONSE_BYTES ? null : text;
  }
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (; ;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (!value) continue;
    total += value.byteLength;
    if (total > MAX_RESPONSE_BYTES) {
      try { await reader.cancel(); } catch { /* already closed */ }
      return null;
    }
    chunks.push(value);
  }
  const merged = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { merged.set(c, off); off += c.byteLength; }
  return new TextDecoder().decode(merged);
}

type SupaResult = { status: number; body: any };

/** fetch + timeout + size-capped JSON parse. Oversized → `{ __oversized }`. */
async function fetchJson(
  url: string,
  init: RequestInit = {},
  timeoutMs: number = SUPA_TIMEOUT_MS,
): Promise<SupaResult> {
  const res = await fetch(url, { ...init, signal: timeoutSignal(timeoutMs) });
  const text = await readBodyCapped(res);
  if (text === null) return { status: res.status, body: { __oversized: true } };
  let body: any = null;
  try { body = JSON.parse(text); } catch { /* empty body */ }
  return { status: res.status, body };
}

async function supa(
  base: string,
  key: string,
  path: string,
  init: RequestInit = {},
): Promise<SupaResult> {
  return fetchJson(`${base}${path}`, {
    method: init.method ?? 'GET',
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      ...(init.headers as Record<string, string> | undefined),
    },
    body: init.body,
  });
}

async function verifyFlw(txRef: string, key: string): Promise<FlwVerification> {
  let res: SupaResult;
  try {
    res = await fetchJson(
      `${FLW_VERIFY_URL}?tx_ref=${encodeURIComponent(txRef)}`,
      { headers: { Authorization: `Bearer ${key}` } },
      FLW_TIMEOUT_MS,
    );
  } catch (e) {
    return { kind: 'transport_error', message: e instanceof Error ? e.message : String(e) };
  }
  if (res.body && res.body.__oversized) {
    return { kind: 'transport_error', message: 'oversized response body' };
  }
  const body = res.body;
  if (res.status === 401 || res.status === 403) {
    // Bad/dead secret key: throw so the run exits 1 instead of silently
    // waiting on every event forever.
    throw new Error(`Flutterwave rejected FLW_SECRET_KEY (HTTP ${res.status})`);
  }
  if (res.status >= 200 && res.status < 300 && body?.status === 'success' && body.data) {
    const d = body.data;
    return {
      kind: 'transaction',
      id: Number(d.id),
      tx_ref: String(d.tx_ref ?? ''),
      status: String(d.status ?? ''),
      amount: Number(d.amount),
      currency: String(d.currency ?? ''),
    };
  }
  if (res.status === 400 && /no transaction/i.test(String(body?.message ?? ''))) {
    return { kind: 'not_found' };
  }
  return {
    kind: 'transport_error',
    message: `HTTP ${res.status}: ${String(body?.message ?? 'unexpected response')}`,
  };
}

/** A `payment_events` row as PostgREST returns it, with its queue timestamp. */
type EventRow = PaymentEventRow & { created_at?: string | null };

const ageMs = (ev: EventRow): number => {
  const t = Date.parse(ev.created_at ?? '');
  return Number.isFinite(t) ? Date.now() - t : Number.NaN;
};

/** Apply the terminal state to an event row (shared by both passes). */
async function patchEvent(
  base: string,
  key: string,
  ev: EventRow,
  body: Record<string, unknown>,
): Promise<{ ok: boolean; detail: string }> {
  const r = await supa(
    base, key,
    `/rest/v1/payment_events?id=eq.${ev.id}`,
    { method: 'PATCH', headers: { Prefer: 'return=minimal' }, body: JSON.stringify(body) },
  );
  const ok = r.status === 200 || r.status === 204;
  return { ok, detail: `HTTP ${r.status} ${JSON.stringify(r.body)}` };
}

/** Credit through the service-role-only RPC (idempotent). */
async function creditEvent(
  base: string,
  key: string,
  ev: EventRow,
  adj: Extract<ReturnType<typeof adjudicateVerification>, { action: 'credit' }>,
): Promise<boolean> {
  const r = await supa(
    base, key,
    '/rest/v1/rpc/apply_payment_verification',
    {
      method: 'POST',
      body: JSON.stringify({
        p_event_id: ev.id,
        p_flw_id: adj.flwId,
        p_paid_amount: adj.paidAmount,
        p_currency: 'NGN',
        p_tier: adj.plan.tier,
        p_period: adj.plan.period,
      }),
    },
  );
  if (r.status !== 200) {
    if (r.body?.code === 'PGRST2028') {
      fail(
        'apply_payment_verification RPC missing — paste section 5 of ' +
        'statwise/supabase-pending-migrations.sql into the Supabase SQL editor',
      );
    } else {
      fail(`credit failed for ${ev.tx_ref}: HTTP ${r.status} ${JSON.stringify(r.body)}`);
    }
    setFail();
    return false;
  }
  log(`credit ${ev.tx_ref} → ${adj.plan.tier} (₦${adj.paidAmount}) · ${JSON.stringify(r.body)}`);
  return true;
}

/**
 * List pending events for one age window, CLOSEST TO EXPIRY FIRST.
 *
 * Prefers `order=expires_at.asc`; if the server rejects that column
 * (payment_events.expires_at is added by section 13 of
 * statwise/supabase-audit-hardening.sql and may not be applied yet —
 * PostgREST answers with an error naming the missing column), warn once
 * and fall back to `created_at.asc`, which orders identically because
 * expires_at is the constant offset `created_at + 24 h`.
 */
async function listPendingEvents(
  base: string,
  key: string,
  window: string, // `created_at=lt.…` (stale) or `created_at=gte.…` (fresh)
): Promise<SupaResult> {
  const path = (order: string): string =>
    `/rest/v1/payment_events?select=*&status=eq.pending&${window}` +
    `&order=${order}&limit=${FETCH_LIMIT}`;
  const r = await supa(base, key, path('expires_at.asc'));
  if (r.status === 200) return r;
  if (JSON.stringify(r.body ?? {}).includes('expires_at')) {
    warn(
      'payment_events.expires_at missing — paste section 13 of ' +
      'statwise/supabase-audit-hardening.sql; ordering by created_at',
    );
    return supa(base, key, path('created_at.asc'));
  }
  return r;
}

async function main(): Promise<void> {
  const base = env('SUPABASE_URL');
  const key = env('SUPABASE_SERVICE_ROLE_KEY') || env('SUPABASE_SERVICE_ROLE')
    || env('SUPABASE_SERVICE_KEY');
  const flwKey = env('FLW_SECRET_KEY');
  if (!base || !key) {
    fail('SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured as secrets');
    setFail();
    return;
  }
  if (!flwKey) {
    fail('FLW_SECRET_KEY secret is not set — repo Settings → Secrets and variables → Actions');
    setFail();
    return;
  }

  const mode = DRY ? '[dry-run] ' : '';
  const cutoff = new Date(Date.now() - EVENT_TTL_MS).toISOString();

  let credits = 0;
  let failed = 0;
  let waiting = 0;
  let expired = 0;

  // ── 1. rows past the 24 h TTL: FINAL verification, THEN expire ──────────
  // M-24: the old code expired these wholesale without ever asking
  // Flutterwave, so a bank transfer that settled near the deadline (or while
  // the queue was backed up) was thrown away unpaid.  Each row now gets one
  // last look: credit if it paid, fail if it is terminally dead, and only
  // expire when Flutterwave says there is NO transaction for this ref.
  const stale = await listPendingEvents(base, key, `created_at=lt.${cutoff}`);
  if (stale.status === 404 || stale.body?.code === 'PGRST201') {
    warn(
      'payment_events table not found — paste section 5 of ' +
      'statwise/supabase-pending-migrations.sql into the Supabase SQL editor',
    );
    return;
  }
  if (stale.status !== 200) {
    fail(`listing stale events failed: HTTP ${stale.status} ${JSON.stringify(stale.body)}`);
    setFail();
    return;
  }
  const staleRows: EventRow[] = ((stale.body ?? []) as EventRow[])
    .slice(0, MAX_STALE_PER_RUN);
  if (staleRows.length > 0 && DRY) {
    log(`${mode}${staleRows.length} stale pending event(s) to re-verify before expiry`);
  }
  for (const ev of staleRows) {
    const flw = await verifyFlw(ev.tx_ref, flwKey);
    const adj = adjudicateVerification(ev, flw);
    const age = ageMs(ev);
    const hard = Number.isFinite(age) && age > HARD_EXPIRE_MS;

    if (adj.action === 'credit') {
      if (DRY) {
        log(`${mode}would LATE-CREDIT ${ev.tx_ref} → ${adj.plan.tier} (₦${adj.paidAmount})`);
        credits += 1;
        continue;
      }
      log(`late-credit ${ev.tx_ref}: settled after the 24h TTL — verifying before expiry`);
      if (await creditEvent(base, key, ev, adj)) credits += 1;
      else failed += 1;
      continue;
    }

    if (adj.action === 'fail') {
      failed += 1;
      if (DRY) {
        log(`${mode}would FAIL  ${ev.tx_ref}: ${adj.reason}`);
        continue;
      }
      const r = await patchEvent(base, key, ev, {
        status: 'failed', last_error: adj.reason.slice(0, 500),
      });
      if (!r.ok) { fail(`marking ${ev.tx_ref} failed: ${r.detail}`); setFail(); continue; }
      log(`FAIL  ${ev.tx_ref}: ${adj.reason}`);
      continue;
    }

    // leave_pending at TTL — decide whether "pending" is still defensible.
    if (flw.kind === 'not_found') {
      // Definitive: 24 h after queueing, Flutterwave has no transaction for
      // this ref — no one ever attempted a payment.  This is the ONLY
      // condition that auto-expires a row.
      expired += 1;
      if (DRY) { log(`${mode}would expire ${ev.tx_ref}: no transaction at TTL`); continue; }
      const r = await patchEvent(base, key, ev, {
        status: 'expired', last_error: 'no Flutterwave transaction within 24h',
      });
      if (!r.ok) { fail(`expiring ${ev.tx_ref} failed: ${r.detail}`); setFail(); continue; }
      log(`expire ${ev.tx_ref}: no transaction at TTL`);
      continue;
    }
    if (hard) {
      // Row exists but has not settled for 7 days (or we could not reach
      // Flutterwave for that long).  Never silently drop it: expire WITH a
      // loud annotation so the maintainer can reconcile by hand.
      expired += 1;
      warn(
        `expiring ${ev.tx_ref} after ${Math.round(age / 86_400_000)}d — ` +
        `last state: ${flw.kind === 'transport_error' ? `FLW unreachable (${flw.message})` : 'transaction exists but unsettled'}`,
      );
      if (DRY) { log(`${mode}would hard-expire ${ev.tx_ref}`); continue; }
      const r = await patchEvent(base, key, ev, {
        status: 'expired',
        last_error: flw.kind === 'transport_error'
          ? `could not confirm with Flutterwave: ${flw.message}`.slice(0, 500)
          : 'transaction still unsettled after 7d',
      });
      if (!r.ok) { fail(`expiring ${ev.tx_ref} failed: ${r.detail}`); setFail(); continue; }
      continue;
    }
    // A real transaction exists but has not settled yet — keep it alive; it
    // is re-verified by this pass on the next run (this is the
    // late-settled-bank-transfer path that used to be expired unverified).
    waiting += 1;
    log(`hold  ${ev.tx_ref}: past TTL but ${flw.kind} — keep verifying`);
  }

  // ── 2. fresh pending events (closest to expiry first) ──────────────────
  const fresh = await listPendingEvents(base, key, `created_at=gte.${cutoff}`);
  if (fresh.status !== 200) {
    fail(`listing pending events failed: HTTP ${fresh.status} ${JSON.stringify(fresh.body)}`);
    setFail();
    return;
  }
  const listed = (fresh.body ?? []) as EventRow[];

  // Trim to the per-run caps: FIFO (expires_at.asc ≡ created_at.asc) with
  // the per-user cap applied first so one account's burst can never consume
  // a whole run.
  const perUser = new Map<string, number>();
  const events: EventRow[] = [];
  for (const ev of listed) {
    const n = perUser.get(ev.user_id) ?? 0;
    if (n >= MAX_PER_USER_PER_RUN) continue;
    perUser.set(ev.user_id, n + 1);
    events.push(ev);
    if (events.length >= MAX_FRESH_PER_RUN) break;
  }
  if (listed.length > events.length) {
    log(
      `${mode}processing ${events.length} of ${listed.length} fresh pending event(s) ` +
      `(cap ${MAX_FRESH_PER_RUN}/run, ${MAX_PER_USER_PER_RUN}/user — the rest run next tick)`,
    );
  }

  // ── 3./4. adjudicate each one ───────────────────────────────────────────
  for (const ev of events) {
    const flw = await verifyFlw(ev.tx_ref, flwKey);
    const adj = adjudicateVerification(ev, flw);

    if (adj.action === 'leave_pending') {
      waiting += 1;
      log(`wait  ${ev.tx_ref}: ${adj.reason}`);
      continue;
    }

    if (adj.action === 'fail') {
      failed += 1;
      if (DRY) {
        log(`${mode}would FAIL  ${ev.tx_ref}: ${adj.reason}`);
        continue;
      }
      const r = await patchEvent(base, key, ev, {
        status: 'failed', last_error: adj.reason.slice(0, 500),
      });
      if (!r.ok) { fail(`marking ${ev.tx_ref} failed: ${r.detail}`); setFail(); continue; }
      log(`FAIL  ${ev.tx_ref}: ${adj.reason}`);
      continue;
    }

    // credit
    if (DRY) {
      log(`${mode}would CREDIT ${ev.tx_ref} → ${adj.plan.tier} (₦${adj.paidAmount}, ${adj.plan.period})`);
      credits += 1;
      continue;
    }
    if (await creditEvent(base, key, ev, adj)) credits += 1;
  }

  log(
    `${mode}done: ${staleRows.length} past-TTL re-verified (${expired} expired), ` +
    `${events.length} fresh processed → ${credits} credited, ${failed} failed, ` +
    `${waiting} waiting`,
  );
}

main().catch((e) => {
  fail(e instanceof Error ? e.message : String(e));
  setFail();
});
