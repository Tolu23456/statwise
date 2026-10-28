/**
 * StatWise payment verification poller (plan 5.1).
 *
 * Runs in GitHub Actions every 3 minutes (`.github/workflows/verify-payments.yml`)
 * with the service-role key and the Flutterwave v3 secret key. The flow:
 *
 *   1. expire `payment_events` rows still pending after 24 h (abandoned
 *      checkout sessions);
 *   2. for each fresh pending event: Flutterwave
 *      `GET /v3/transactions/verify_by_reference?tx_ref=…`;
 *   3. adjudicate (`adjudicateVerification` in lib/paymentCore.ts — the same
 *      pure function unit-tested in CI): credit / fail / keep waiting;
 *   4. credit atomically through the `apply_payment_verification` RPC
 *      (ledger insert + tier grant + event flip in ONE transaction,
 *      service-role-only — clients cannot execute it, and a DB trigger
 *      blocks any direct tier write with `auth.uid() is not null`).
 *
 * Everything is idempotent: re-running a completed event returns `already`,
 * the ledger insert is `ON CONFLICT (transaction_id) DO NOTHING`, and the
 * workflow has a single concurrency group.
 *
 *   node scripts/verify-payments.js            # writes
 *   node scripts/verify-payments.js --dry-run  # decisions only, no writes
 *                                             # (Flutterwave GETs still happen)
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
  process?: { env?: Record<string, string | undefined>; argv?: string[] };
};
const env = (k: string): string => g.process?.env?.[k] ?? '';
const DRY = (g.process?.argv ?? []).includes('--dry-run');

const FLW_VERIFY_URL = 'https://api.flutterwave.com/v3/transactions/verify_by_reference';

const log = (msg: string): void => { console.log(msg); };
const warn = (msg: string): void => { console.log(`::warning::${msg}`); };
const fail = (msg: string): void => { console.log(`::error::${msg}`); };

type SupaResult = { status: number; body: any };

async function supa(
  base: string,
  key: string,
  path: string,
  init: RequestInit = {},
): Promise<SupaResult> {
  const res = await fetch(`${base}${path}`, {
    method: init.method ?? 'GET',
    headers: {
      apikey: key,
      Authorization: `Bearer ${key}`,
      'Content-Type': 'application/json',
      ...(init.headers as Record<string, string> | undefined),
    },
    body: init.body,
  });
  let body: any = null;
  try { body = await res.json(); } catch { /* empty body */ }
  return { status: res.status, body };
}

async function verifyFlw(txRef: string, key: string): Promise<FlwVerification> {
  let res: Response;
  try {
    res = await fetch(`${FLW_VERIFY_URL}?tx_ref=${encodeURIComponent(txRef)}`, {
      headers: { Authorization: `Bearer ${key}` },
    });
  } catch (e) {
    return { kind: 'transport_error', message: e instanceof Error ? e.message : String(e) };
  }
  let body: any = null;
  try { body = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 || res.status === 403) {
    // Bad/dead secret key: throw so the run exits 1 instead of silently
    // waiting on every event forever.
    throw new Error(`Flutterwave rejected FLW_SECRET_KEY (HTTP ${res.status})`);
  }
  if (res.ok && body?.status === 'success' && body.data) {
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
    message: `HTTP ${res.status}: ${String(body?.message ?? res.statusText)}`,
  };
}

async function main(): Promise<void> {
  const base = env('SUPABASE_URL');
  const key = env('SUPABASE_SERVICE_ROLE_KEY') || env('SUPABASE_SERVICE_ROLE')
    || env('SUPABASE_SERVICE_KEY');
  const flwKey = env('FLW_SECRET_KEY');
  if (!base || !key) {
    fail('SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured as secrets');
    process.exitCode = 1;
    return;
  }
  if (!flwKey) {
    fail('FLW_SECRET_KEY secret is not set — repo Settings → Secrets and variables → Actions');
    process.exitCode = 1;
    return;
  }

  const mode = DRY ? '[dry-run] ' : '';
  const cutoff = new Date(Date.now() - EVENT_TTL_MS).toISOString();

  // ── 1. expire stale pendings (abandoned checkout sessions) ──────────────
  const stale = await supa(
    base, key,
    `/rest/v1/payment_events?select=id&status=eq.pending&created_at=lt.${cutoff}&limit=500`,
  );
  if (stale.status === 404 || stale.body?.code === 'PGRST201') {
    warn(
      'payment_events table not found — paste section 5 of ' +
      'statwise/supabase-pending-migrations.sql into the Supabase SQL editor',
    );
    return;
  }
  if (stale.status !== 200) {
    fail(`listing stale events failed: HTTP ${stale.status} ${JSON.stringify(stale.body)}`);
    process.exitCode = 1;
    return;
  }
  const staleIds: string[] = (stale.body ?? []).map((r: any) => String(r.id));
  if (staleIds.length > 0) {
    if (DRY) {
      log(`${mode}would expire ${staleIds.length} stale pending event(s)`);
    } else {
      const r = await supa(
        base, key,
        `/rest/v1/payment_events?id=in.(${staleIds.join(',')})`,
        {
          method: 'PATCH',
          headers: { Prefer: 'return=representation' },
          body: JSON.stringify({ status: 'expired' }),
        },
      );
      if (r.status !== 200) {
        fail(`expiring stale events failed: HTTP ${r.status} ${JSON.stringify(r.body)}`);
        process.exitCode = 1;
        return;
      }
      const n = Array.isArray(r.body) ? r.body.length : staleIds.length;
      log(`expired ${n} stale pending event(s)`);
    }
  }

  // ── 2. fresh pending events ─────────────────────────────────────────────
  const fresh = await supa(
    base, key,
    `/rest/v1/payment_events?select=*&status=eq.pending&created_at=gte.${cutoff}` +
    `&order=created_at.asc&limit=100`,
  );
  if (fresh.status !== 200) {
    fail(`listing pending events failed: HTTP ${fresh.status} ${JSON.stringify(fresh.body)}`);
    process.exitCode = 1;
    return;
  }
  const events: PaymentEventRow[] = (fresh.body ?? []) as PaymentEventRow[];
  if (events.length === 0) {
    log(`${mode}0 pending payment events`);
    return;
  }

  // ── 3./4. adjudicate each one ───────────────────────────────────────────
  let credits = 0;
  let failed = 0;
  let waiting = 0;
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
      const r = await supa(
        base, key,
        `/rest/v1/payment_events?id=eq.${ev.id}`,
        {
          method: 'PATCH',
          headers: { Prefer: 'return=minimal' },
          body: JSON.stringify({ status: 'failed', last_error: adj.reason.slice(0, 500) }),
        },
      );
      if (r.status !== 200 && r.status !== 204) {
        fail(`marking ${ev.tx_ref} failed: HTTP ${r.status} ${JSON.stringify(r.body)}`);
        process.exitCode = 1;
        continue;
      }
      log(`FAIL  ${ev.tx_ref}: ${adj.reason}`);
      continue;
    }

    // credit
    if (DRY) {
      log(`${mode}would CREDIT ${ev.tx_ref} → ${adj.plan.tier} (₦${adj.paidAmount}, ${adj.plan.period})`);
      credits += 1;
      continue;
    }
    const r = await supa(
      base, key,
      `/rest/v1/rpc/apply_payment_verification`,
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
      process.exitCode = 1;
      continue;
    }
    credits += 1;
    log(`credit ${ev.tx_ref} → ${adj.plan.tier} (₦${adj.paidAmount}) · ${JSON.stringify(r.body)}`);
  }

  log(
    `${mode}done: ${events.length} pending → ` +
    `${credits} credited, ${failed} failed, ${waiting} waiting`,
  );
}

main().catch((e) => {
  fail(e instanceof Error ? e.message : String(e));
  process.exitCode = 1;
});
