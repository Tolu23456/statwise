-- ============================================================
-- StatWise Forum Fix — Run this in your Supabase SQL Editor
-- ============================================================

-- Drop policies that might conflict
DROP POLICY IF EXISTS "Users can send forum messages" ON public.forum_messages;
DROP POLICY IF EXISTS "Authenticated users can send forum messages" ON public.forum_messages;
DROP POLICY IF EXISTS "Authenticated users can post messages" ON public.forum_messages;
DROP POLICY IF EXISTS "Users can update their own forum messages" ON public.forum_messages;

-- Make username nullable
ALTER TABLE public.forum_messages ALTER COLUMN username DROP NOT NULL;

-- Ensure updated_at exists
ALTER TABLE public.forum_messages ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Create/replace function to send messages
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
  FROM public.user_profiles
  WHERE id = auth.uid()
  LIMIT 1;

  INSERT INTO forum_messages (user_id, username, message, created_at)
  VALUES (auth.uid(), COALESCE(v_username, 'User'), p_message, NOW());
END;
$$;

GRANT EXECUTE ON FUNCTION public.send_forum_message(TEXT) TO authenticated;

-- Ensure insert policy exists
DROP POLICY IF EXISTS "Authenticated users can insert forum messages" ON public.forum_messages;
CREATE POLICY "Authenticated users can insert forum messages"
  ON public.forum_messages
  FOR INSERT
  TO authenticated
  WITH CHECK (auth.uid() = user_id);
