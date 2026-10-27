/**
 * Favourites ⇄ server sync (user_favorites table, section 4 of the pending
 * migration runner).
 *
 * Why the table exists: favourites were AsyncStorage-only, so the notify
 * cron had NO way to know which user bookmarked which match — the
 * "your match is starting soon" alert targets exactly those users.
 *
 * Contract (everything is best-effort and silent):
 *   - AsyncStorage stays the instant UI source; the server copy is a
 *     follower, never a gate — no screen waits on this;
 *   - reconcileFavorites(local) runs once per app start (after hydrate):
 *     add = local \ server (upsert DO NOTHING), remove = server \ local;
 *   - syncFavoriteToggle(id, isNowFavorite) fires per bookmark tap;
 *   - while the user_favorites table does not exist (migration not run
 *     yet), every call no-ops and stops trying for the session — no
 *     errors, no retries, no user-visible behaviour change.
 */
import { supabase } from './supabase';
import { favoriteSyncPlan } from './favorites';

let tableMissing = false;
let currentUser: { id: string } | null = null;
let sessionLoaded = false;

/** Error shapes: PostgREST "relation ... does not exist" and Postg missing-entity. */
function isMissingTable(error: unknown): boolean {
  const e = error as { code?: string; message?: string } | null | undefined;
  const msg = String(e?.message ?? '');
  return (
    e?.code === '42P01' ||
    e?.code === 'PGRST205' ||
    msg.includes('does not exist') ||
    msg.includes('schema cache')
  );
}

async function resolveUser(): Promise<{ id: string } | null> {
  if (tableMissing) return null;
  if (!sessionLoaded) {
    sessionLoaded = true;
    try {
      const { data } = await supabase.auth.getSession();
      currentUser = (data.session?.user as { id: string } | undefined) ?? null;
    } catch {
      // auth not ready — next call retries once the session exists
      sessionLoaded = false;
    }
  }
  if (!currentUser) {
    try {
      const { data } = await supabase.auth.getUser();
      currentUser = (data.user as { id: string } | undefined) ?? null;
    } catch {
      /* signed out / offline — nothing to sync */
    }
  }
  return currentUser;
}

function noteIfMissingTable(error: unknown): void {
  if (isMissingTable(error)) tableMissing = true; // stop trying this session
}

/**
 * One-shot add for a bookmark tap. Fire-and-forget: the caller does not
 * await or handle errors — the device list already updated optimistically.
 */
export async function syncFavoriteToggle(
  predictionId: string,
  isNowFavorite: boolean,
): Promise<void> {
  if (!predictionId) return;
  const user = await resolveUser();
  if (!user) return;
  try {
    if (isNowFavorite) {
      const { error } = await supabase
        .from('user_favorites')
        .upsert(
          { user_id: user.id, prediction_id: predictionId },
          { onConflict: 'user_id,prediction_id', ignoreDuplicates: true },
        );
      if (error) noteIfMissingTable(error);
    } else {
      const { error } = await supabase
        .from('user_favorites')
        .delete()
        .eq('user_id', user.id)
        .eq('prediction_id', predictionId);
      if (error) noteIfMissingTable(error);
    }
  } catch {
    // network/RLS — the next toggle or reconcile will retry
  }
}

/**
 * Full reconcile after the device list hydrates (app start). Brings the
 * server copy in line with the device in both directions and writes the
 * additions back to AsyncStorage so both sides are byte-identical after.
 */
export async function reconcileFavorites(local: string[]): Promise<{ added: number; removed: number }> {
  const none = { added: 0, removed: 0 };
  const user = await resolveUser();
  if (!user) return none;
  try {
    const { data: rows, error } = await supabase
      .from('user_favorites')
      .select('prediction_id')
      .eq('user_id', user.id);
    if (error) {
      noteIfMissingTable(error);
      return none;
    }
    const server = (rows ?? []).map(r => String((r as { prediction_id: string }).prediction_id));
    const plan = favoriteSyncPlan(local, server);
    if (plan.toAdd.length > 0) {
      const { error: addErr } = await supabase.from('user_favorites').upsert(
        plan.toAdd.map(prediction_id => ({ user_id: user.id, prediction_id })),
        { onConflict: 'user_id,prediction_id', ignoreDuplicates: true },
      );
      if (addErr) noteIfMissingTable(addErr);
    }
    if (plan.toRemove.length > 0) {
      const { error: rmErr } = await supabase
        .from('user_favorites')
        .delete()
        .eq('user_id', user.id)
        .in('prediction_id', plan.toRemove);
      if (rmErr) noteIfMissingTable(rmErr);
    }
    return { added: plan.toAdd.length, removed: plan.toRemove.length };
  } catch {
    return none;
  }
}
