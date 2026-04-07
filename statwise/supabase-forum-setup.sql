-- ============================================================
-- StatWise: Forum Messages RLS Setup
-- Run this in your Supabase SQL editor:
-- https://supabase.com/dashboard/project/pdrcyuzfdqjnsltqqxvr/sql/new
-- ============================================================

-- 1. Create the table (safe to run if it already exists)
CREATE TABLE IF NOT EXISTS public.forum_messages (
  id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     UUID        REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL,
  message     TEXT        NOT NULL CHECK (char_length(message) BETWEEN 1 AND 500),
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Enable Realtime (allows live message updates)
ALTER PUBLICATION supabase_realtime ADD TABLE public.forum_messages;

-- 3. Enable RLS
ALTER TABLE public.forum_messages ENABLE ROW LEVEL SECURITY;

-- 4. DROP old policies if they exist (clean slate)
DROP POLICY IF EXISTS "Anyone can read messages"              ON public.forum_messages;
DROP POLICY IF EXISTS "Authenticated users can post messages" ON public.forum_messages;
DROP POLICY IF EXISTS "Users can delete their own messages"   ON public.forum_messages;

-- 5. SELECT — anyone (including logged-out users) can read messages
CREATE POLICY "Anyone can read messages"
  ON public.forum_messages
  FOR SELECT
  TO anon, authenticated
  USING (true);

-- 6. INSERT — logged-in users can post, but only as themselves
CREATE POLICY "Authenticated users can post messages"
  ON public.forum_messages
  FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);

-- 7. DELETE — users can only delete their own messages
CREATE POLICY "Users can delete their own messages"
  ON public.forum_messages
  FOR DELETE
  TO authenticated
  USING (auth.uid() = user_id);

-- Done! ✓
