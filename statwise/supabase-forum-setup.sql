-- ============================================================
-- StatWise: Forum Messages RLS Setup (Corrected)
-- ============================================================

-- 1. Create the table (safe to run if it already exists)
CREATE TABLE IF NOT EXISTS public.forum_messages (
  id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     UUID        NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  username    TEXT,
  message     TEXT        NOT NULL CHECK (char_length(message) BETWEEN 1 AND 500),
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Enable Realtime
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'forum_messages'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.forum_messages;
  END IF;
END $$;

-- 3. Enable RLS
ALTER TABLE public.forum_messages ENABLE ROW LEVEL SECURITY;

-- 4. Drop old policies
DROP POLICY IF EXISTS "Anyone can read messages"              ON public.forum_messages;
DROP POLICY IF EXISTS "Authenticated users can post messages" ON public.forum_messages;
DROP POLICY IF EXISTS "Users can delete their own messages"   ON public.forum_messages;
DROP POLICY IF EXISTS "Users can view all forum messages"    ON public.forum_messages;
DROP POLICY IF EXISTS "Authenticated users can send forum messages" ON public.forum_messages;
DROP POLICY IF EXISTS "Users can update their own forum messages" ON public.forum_messages;

-- 5. Policies
CREATE POLICY "Anyone can read messages"
  ON public.forum_messages FOR SELECT TO anon, authenticated USING (true);

CREATE POLICY "Authenticated users can post messages"
  ON public.forum_messages FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own messages"
  ON public.forum_messages FOR UPDATE TO authenticated
  USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own messages"
  ON public.forum_messages FOR DELETE TO authenticated USING (auth.uid() = user_id);

-- 6. Function
CREATE OR REPLACE FUNCTION public.send_forum_message(p_message TEXT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_username TEXT;
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;
  SELECT COALESCE(display_name, username, email) INTO v_username
  FROM public.user_profiles WHERE id = auth.uid() LIMIT 1;
  INSERT INTO forum_messages (user_id, username, message, created_at)
  VALUES (auth.uid(), COALESCE(v_username, 'User'), p_message, NOW());
END;
$$;
GRANT EXECUTE ON FUNCTION public.send_forum_message(TEXT) TO authenticated;
