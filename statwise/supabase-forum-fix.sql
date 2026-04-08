-- ============================================================
-- StatWise Forum Fix — Run this in your Supabase SQL Editor:
-- https://supabase.com/dashboard/project/pdrcyuzfdqjnsltqqxvr/sql/new
-- ============================================================
-- This creates a SECURITY DEFINER function that bypasses RLS
-- for forum posts. It verifies the user is authenticated, then
-- inserts the message with their real user ID.
-- ============================================================

CREATE OR REPLACE FUNCTION public.send_forum_message(p_message TEXT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  IF auth.uid() IS NULL THEN
    RAISE EXCEPTION 'Not authenticated';
  END IF;

  INSERT INTO forum_messages (user_id, message, created_at)
  VALUES (auth.uid(), p_message, NOW());
END;
$$;

-- Allow authenticated users to call this function
GRANT EXECUTE ON FUNCTION public.send_forum_message(TEXT) TO authenticated;
