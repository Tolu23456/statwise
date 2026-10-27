/**
 * Client telemetry (plan step 4.4 — "error tracking", "performance
 * monitoring").
 *
 * Zero new dependencies: events are rows in a `client_events` Supabase
 * table (supabase-telemetry-migration.sql), read only by operators — the
 * metrics workflow (metrics-daily.yml) turns them into the dashboard and
 * alerts. Until that migration is run, every capture is a swallowed no-op:
 * telemetry must never break the app.
 *
 * This module is deliberately pure (no react-native/supabase imports):
 *   - payload construction with hard caps (a broken error handler must not
 *     ship megabytes or control characters anywhere),
 *   - the slow-query predicate over Resource Timing entries,
 *   - a per-session rate budget (so a error-storm loop can't flood the
 *     table from a single tab).
 * The side-effecting half — `setEventSink` + `installWebTelemetry` — is
 * wired in app/_layout.tsx and guarded so importing this file on Node is
 * safe (same pattern as lib/onboarding.ts / lib/bookingGuard.ts).
 */
import { sanitizeUserText, isValidEmail } from './validation';
import { checkRateLimit, type RateRule } from './rateLimit';

export const TELEMETRY = {
  /** Hard cap for `message` after sanitization. */
  maxMessageLen: 500,
  /** Hard cap for the serialized `context` JSON. */
  maxContextBytes: 2048,
  /** A Supabase REST call slower than this is worth recording. */
  slowQueryThresholdMs: 1000,
  /** Per page-load budget: an error storm reports, then stops reporting. */
  budget: { max: 30, windowMs: 60 * 60_000 } satisfies RateRule,
} as const;

export const TELEMETRY_KINDS = [
  'render_error',   // React ErrorBoundary caught a render crash
  'react_error',    // minified React invariant (e.g. the #418 hydration
                    // mismatch the 4.2 load measurement surfaced)
  'uncaught',       // window error / unhandledrejection
  'slow_query',     // Supabase REST call over the threshold
  'custom',         // anything a screen wants to record explicitly
] as const;

export type EventKind = (typeof TELEMETRY_KINDS)[number];

export type TelemetryEvent = {
  kind: EventKind;
  message: string;
  context: Record<string, unknown> | null;
  path: string | null;
  session_id: string;
  app_version: string;
};

export const APP_VERSION = '1.0.0';

/** Unique id per page load — groups events from one session together. */
export function makeSessionId(): string {
  const rand = Math.random().toString(36).slice(2, 10);
  return `${Date.now().toString(36)}-${rand}`;
}

/**
 * Build a row-safe event, or null when the input is unusable. Never throws:
 * JSON that can't be stringified degrades to a note, unknown kinds are
 * dropped (they would fail the table's CHECK anyway).
 */
export function buildEvent(
  input: {
    kind: string;
    message: unknown;
    context?: unknown;
    path?: unknown;
    sessionId: string;
  },
  appVersion: string = APP_VERSION,
): TelemetryEvent | null {
  if (!(TELEMETRY_KINDS as readonly string[]).includes(input.kind)) return null;
  if (!input.sessionId) return null;

  const raw = input.message == null ? '' : String(input.message);
  const message = sanitizeUserText(raw, TELEMETRY.maxMessageLen) || '(no message)';

  let context: Record<string, unknown> | null = null;
  if (input.context !== undefined && input.context !== null) {
    let serialized: string;
    try {
      serialized = JSON.stringify(input.context);
    } catch {
      serialized = '{"note":"context not serializable"}';
    }
    if (serialized.length > TELEMETRY.maxContextBytes) {
      context = {
        truncated: true,
        preview: serialized.slice(0, TELEMETRY.maxContextBytes - 64),
      };
    } else {
      try {
        const parsed = JSON.parse(serialized);
        // Record only — arrays and primitives get wrapped so the column's
        // shape stays uniform for the metrics queries.
        context =
          parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? (parsed as Record<string, unknown>)
            : { value: parsed };
      } catch {
        context = { preview: serialized };
      }
    }
  }

  const path =
    typeof input.path === 'string' && input.path.length > 0
      ? sanitizeUserText(input.path, 200)
      : null;

  return {
    kind: input.kind as EventKind,
    message,
    context,
    path,
    session_id: input.sessionId,
    app_version: appVersion,
  };
}

// ─── transport (registered by the app; null = no-op, as before migration) ──

type EventSink = (events: TelemetryEvent[]) => Promise<unknown>;

let sink: EventSink | null = null;
let sessionId = '';

/** app/_layout.tsx points this at the Supabase insert once at startup. */
export function setEventSink(next: EventSink | null): void {
  sink = next;
}

export function getSessionId(): string {
  if (!sessionId) sessionId = makeSessionId();
  return sessionId;
}

/**
 * Capture one event. Returns true only when a row was handed to the sink —
 * false for invalid input, rate-budget exhaustion, missing sink, or a sink
 * that threw (upload failure is never allowed to surface to the UI).
 */
export function captureEvent(input: {
  kind: string;
  message: unknown;
  context?: unknown;
  path?: unknown;
}): boolean {
  const event = buildEvent({ ...input, sessionId: getSessionId() });
  if (!event) return false;
  if (!sink) return false;
  // Budget is charged per attempt so a failing sink can't retry-storm either.
  const verdict = checkRateLimit('telemetry:session', TELEMETRY.budget);
  if (!verdict.allowed) return false;
  try {
    void sink([event]);
    return true;
  } catch {
    return false;
  }
}

// ─── slow-query predicate (pure — unit-tested) ──────────────────────────────

const SUPABASE_PATHS = ['/rest/v1/', '/auth/v1/', '/realtime/v1/'];

/**
 * True for a *Supabase API* resource that took at least the threshold.
 * Static assets, fonts and third-party URLs never qualify — this measures
 * our data layer, not the network at large. `url` is matched on path only
 * (query strings may contain filters; they must not leak into events).
 */
export function isSlowResource(url: string, durationMs: number): boolean {
  if (!Number.isFinite(durationMs) || durationMs < TELEMETRY.slowQueryThresholdMs) return false;
  if (typeof url !== 'string' || url.length === 0) return false;
  if (url.startsWith('data:') || url.startsWith('blob:')) return false;
  let pathname = url;
  try {
    pathname = new URL(url).pathname;
  } catch {
    // Relative or malformed URL: use it raw for the path-contains match.
  }
  return SUPABASE_PATHS.some(p => pathname.includes(p));
}

/** Path (never the full URL — query strings can carry user data). */
export function pathOf(url: string): string {
  try {
    return new URL(url).pathname;
  } catch {
    return url.split('?')[0].slice(0, 200);
  }
}

// ─── web installation (called from _layout on web only) ─────────────────────

type Cleanup = () => void;

/**
 * Install global capture: uncaught errors, unhandled rejections, React
 * invariant errors surfaced through console.error (this is how the #418
 * hydration mismatch becomes a tracked, alertable metric), and Supabase
 * slow calls via Resource Timing. Safe no-op outside a browser.
 */
export function installWebTelemetry(): Cleanup {
  if (typeof window === 'undefined' || typeof window.addEventListener !== 'function') {
    return () => {};
  }

  const onError = (event: ErrorEvent) => {
    captureEvent({
      kind: 'uncaught',
      message: event.message || 'window error',
      context: {
        filename: event.filename ? pathOf(event.filename) : undefined,
        line: event.lineno,
      },
      path: window.location?.pathname,
    });
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    const reason: any = event.reason;
    captureEvent({
      kind: 'uncaught',
      message: reason?.message ?? String(reason),
      context: { stack: typeof reason?.stack === 'string' ? reason.stack.slice(0, 800) : undefined },
      path: window.location?.pathname,
    });
  };
  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection as EventListener);

  // React throws render/hydration problems through console.error, not as
  // exceptions — intercept just enough to notice them, always delegating.
  const originalError = console.error;
  const consoleWrapper = (...args: unknown[]) => {
    try {
      const first = args[0];
      if (typeof first === 'string' && /Minified React error #\d+/.test(first)) {
        captureEvent({
          kind: 'react_error',
          message: first.slice(0, TELEMETRY.maxMessageLen),
          context: { argCount: args.length },
          path: window.location?.pathname,
        });
      }
    } catch {
      /* instrumentation must never break logging */
    }
    originalError.apply(console, args);
  };
  console.error = consoleWrapper as typeof console.error;

  // Slow Supabase calls: Resource Timing, no per-request wrapping needed.
  let observer: PerformanceObserver | null = null;
  try {
    if (typeof PerformanceObserver !== 'undefined') {
      observer = new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (isSlowResource(entry.name, entry.duration)) {
            captureEvent({
              kind: 'slow_query',
              message: `${Math.round(entry.duration)}ms ${pathOf(entry.name)}`,
              context: { ms: Math.round(entry.duration), path: pathOf(entry.name) },
              path: window.location?.pathname,
            });
          }
        }
      });
      observer.observe({ entryTypes: ['resource'] });
    }
  } catch {
    observer = null;
  }

  return () => {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection as EventListener);
    console.error = originalError;
    try {
      observer?.disconnect();
    } catch {
      /* ignore */
    }
  };
}
