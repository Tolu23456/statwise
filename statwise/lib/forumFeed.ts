/**
 * Forum feed — background loading, local cache, background updates.
 *
 * User report (2026-10-27): messages only loaded when the forum screen was
 * open, nothing was cached locally, and updates only happened while the
 * user sat on the page. This module inverts that:
 *
 *   hydrate   AsyncStorage cache read once at startup → the forum opens
 *             instantly (even offline) with the last conversation;
 *   refresh   the real fetch, run by the app-wide watcher (started from
 *             app/_layout.tsx) — at startup and every 60s while active,
 *             NOT when the user opens the forum page;
 *   realtime  one app-wide postgres_changes channel so new messages land
 *             in the cache no matter which tab is showing;
 *   persist   every confirmed merge is written back to AsyncStorage
 *             (confirmed rows only, capped by FORUM_CACHE_CAP).
 *
 * The screen subscribes via subscribeForumMessages() and never fetches on
 * its own except pull-to-refresh / send-reconciliation.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { AppState } from 'react-native';
import { supabase, ForumMessage } from './supabase';
import { cacheableForumRows, mergeForumRows } from './forumFeedCore';

const CACHE_KEY = 'statwise.forum.messages.v1';
const POLL_MS = 60_000;

type Listener = (rows: ForumMessage[]) => void;

let rows: ForumMessage[] = [];
let hydrated = false;
let refreshInFlight: Promise<{ ok: boolean; error: unknown }> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;
let channel: ReturnType<typeof supabase.channel> | null = null;
let visibilityHandler: (() => void) | null = null;
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener(rows);
}

function setRows(next: ForumMessage[]): void {
  rows = next;
  emit();
  void persist();
}

async function persist(): Promise<void> {
  try {
    if (!hydrated) return; // never clobber the cache with a pre-hydrate state
    await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(cacheableForumRows(rows)));
  } catch {
    // storage full/unavailable — in-memory feed still works
  }
}

/** Read the cached conversation once (idempotent). Safe before any UI exists. */
export async function hydrateForumCache(): Promise<void> {
  if (hydrated) return;
  hydrated = true;
  try {
    const raw = await AsyncStorage.getItem(CACHE_KEY);
    if (raw) {
      const cached = JSON.parse(raw);
      if (Array.isArray(cached) && cached.length > 0 && !rows.length) {
        rows = mergeForumRows(rows, cached as ForumMessage[]);
        emit();
      }
    }
  } catch {
    // corrupt cache — start fresh
  }
}

/** Current rows (sync snapshot for initial state / imperative reads). */
export function getForumMessages(): ForumMessage[] {
  return rows;
}

/**
 * Subscribe to feed updates. The listener fires immediately with the
 * current snapshot (so a screen mounted before hydration gets the real
 * value as soon as hydration lands), then on every change.
 */
export function subscribeForumMessages(listener: Listener): () => void {
  listeners.add(listener);
  listener(rows);
  return () => listeners.delete(listener);
}

/** Add/replace rows (realtime delivery, send reconciliation). */
export function upsertForumMessages(incoming: ForumMessage[]): void {
  if (!incoming.length) return;
  setRows(mergeForumRows(rows, incoming));
}

/** Drop a rejected optimistic bubble by id. */
export function removeOptimisticForumMessage(id: string): void {
  setRows(rows.filter(r => r.id !== id));
}

async function fetchRows(): Promise<ForumMessage[]> {
  let { data, error } = await supabase
    .from('forum_messages')
    .select('*, user_profiles(username, display_name, avatar_url, current_tier)')
    .order('created_at', { ascending: true })
    .limit(100);

  if (error) {
    const msg: string = error?.message ?? '';
    // Profiles join may be missing on older schemas — fall back to the bare
    // table (the screen renders names from row fields when absent).
    if (msg.includes('user_profiles') || msg.includes('relation') || msg.includes('schema') || msg.includes('join')) {
      const res = await supabase
        .from('forum_messages')
        .select('*')
        .order('created_at', { ascending: true })
        .limit(100);
      if (res.error) throw res.error;
      data = res.data;
      error = null;
    } else {
      throw error;
    }
  }
  return (data ?? []) as ForumMessage[];
}

/**
 * Fetch + merge + persist. Deduplicated: concurrent callers share one
 * in-flight request (screen mount + watcher tick can collide). Never
 * throws — the caller decides how loudly to report `error`.
 */
export function refreshForumMessages(): Promise<{ ok: boolean; error: unknown }> {
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const fetched = await fetchRows();
      setRows(mergeForumRows(rows, fetched));
      return { ok: true, error: null };
    } catch (error) {
      return { ok: false, error };
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

function appIsVisible(): boolean {
  if (AppState.currentState !== 'active') return false;
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return false;
  return true;
}

/**
 * Start the app-wide feed watcher (idempotent). Returns a stop function —
 * the root layout holds it for the session lifetime and drops it on
 * hot reload, which tears down the timer and the realtime channel.
 */
export function startForumFeed(): () => void {
  if (pollTimer || channel) return stopForumFeed;

  void hydrateForumCache().then(() => refreshForumMessages());

  channel = supabase
    .channel('forum-feed-watcher')
    .on(
      'postgres_changes',
      { event: 'INSERT', schema: 'public', table: 'forum_messages' },
      payload => upsertForumMessages([payload.new as ForumMessage]),
    )
    .subscribe();

  pollTimer = setInterval(() => {
    if (appIsVisible()) void refreshForumMessages();
  }, POLL_MS);

  // Coming back to the tab refreshes immediately instead of waiting out
  // the interval (same idea as the home feed's refetch-on-focus).
  if (typeof document !== 'undefined') {
    visibilityHandler = () => {
      if (appIsVisible()) void refreshForumMessages();
    };
    document.addEventListener('visibilitychange', visibilityHandler);
  }

  return stopForumFeed;
}

function stopForumFeed(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  if (visibilityHandler) {
    document.removeEventListener('visibilitychange', visibilityHandler);
    visibilityHandler = null;
  }
  if (channel) {
    void supabase.removeChannel(channel);
    channel = null;
  }
}
