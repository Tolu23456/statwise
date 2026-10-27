-- Push notification subscriptions (plan step 3.2).
--
-- Run this in the Supabase SQL editor. Until it exists, ai/notify.py and
-- the app's push registration detect the missing table and no-op with a
-- clear message — nothing breaks.
--
-- Web push subscriptions are per-browser: one row per device. The endpoint
-- is unique — re-subscribing the same browser updates the keys.

create table if not exists push_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  endpoint    text not null unique,
  p256dh      text not null,
  auth        text not null,
  user_agent  text,
  created_at  timestamptz default now(),
  last_seen_at timestamptz default now()
);

create index if not exists push_subscriptions_user_id_idx
  on push_subscriptions (user_id);

alter table push_subscriptions enable row level security;

-- Users manage only their own subscriptions.
drop policy if exists "Users can view their own push subscriptions" on push_subscriptions;
create policy "Users can view their own push subscriptions"
  on push_subscriptions for select
  using (auth.uid() = user_id);

drop policy if exists "Users can insert their own push subscriptions" on push_subscriptions;
create policy "Users can insert their own push subscriptions"
  on push_subscriptions for insert
  with check (auth.uid() = user_id);

drop policy if exists "Users can update their own push subscriptions" on push_subscriptions;
create policy "Users can update their own push subscriptions"
  on push_subscriptions for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "Users can delete their own push subscriptions" on push_subscriptions;
create policy "Users can delete their own push subscriptions"
  on push_subscriptions for delete
  using (auth.uid() = user_id);

-- The notification sender (service role) reads every row — the default
-- service_role bypass above covers that; no extra policy needed.
