Title: Creating `profile-pictures` bucket and recommended policies for StatWise

Purpose

This file contains exact, safe, and minimal steps you can run in the Supabase Console (Storage UI or SQL editor) to create the `profile-pictures` bucket and set up an appropriate storage policy and RLS guidance so the client-side code in this repo can upload and read profile pictures.

Important notes
- Do NOT embed or expose service-role keys in frontend code. Use the Supabase Console or a secure server to run any commands that require elevated privileges.
- Browser clients should only use the anon/public API key and rely on RLS/storage policies to allow users to manage their own objects.

Quick UI steps (recommended)
1. Open Supabase Console for your project.
2. Go to "Storage" (left menu) -> "Buckets".
3. Click "New bucket".
   - Name: profile-pictures
   - Public: (optional) If you want the files to be publicly accessible via a public URL, toggle "Public" ON. If you prefer private buckets, keep it OFF and serve signed URLs from server or Supabase signed URL APIs.
   - Click Create.
4. (Optional) Under the Bucket `profile-pictures` -> Policies, add policy rules for object operations (below SQL shows how).

SQL snippets (Supabase SQL editor)

-- 1) Create bucket via SQL (alternative to UI)
-- This requires an elevated role (run in SQL editor as an admin)
SELECT storage.create_bucket('profile-pictures', true);

-- 2) Example storage policy allowing authenticated users to upload to their own folder
-- We'll store files under a folder named after the user: "<user_id>/<filename>". This policy allows users to create and manage objects within their own folder only.
-- Note: storage policies operate on "storage.objects" with a few helper functions.

-- Allow authenticated users to upload/insert objects into their own folder
CREATE POLICY "Users can insert their own files" ON storage.objects
  FOR INSERT WITH CHECK (
    auth.role() = 'authenticated' AND (
      -- file must be in the user's folder path
      (split_part(name, '/', 1) = auth.uid())
    )
  );

-- Allow authenticated users to select/list their own files
CREATE POLICY "Users can view their own files" ON storage.objects
  FOR SELECT USING (
    auth.role() = 'authenticated' AND (
      split_part(name, '/', 1) = auth.uid()
    )
  );

-- Allow authenticated users to update or delete their own files
CREATE POLICY "Users can update/delete their own files" ON storage.objects
  FOR UPDATE, DELETE USING (
    auth.role() = 'authenticated' AND (
      split_part(name, '/', 1) = auth.uid()
    )
  );

-- 3) If you made the bucket public, Supabase will serve files at a public URL automatically.
-- If private, generate signed URLs from server or from the client using Supabase signedUrl API (requires anon key but client signed URL call is limited).

Recommended object naming convention
- Use: `${user_id}/${user_id}-${Date.now()}.${ext}`
- This ensures each user only manages files in their top-level folder.

Client guidance (what the code should do)
- When uploading from the browser (using anon key): put objects under folder `currentUser.id/` so storage policies above allow it.
- Use `getPublicUrl` only for public buckets; for private buckets, use signed URLs.
- Do not try to create buckets from the client (service-role required).

Example minimal SQL you can run in Supabase SQL editor to set up recommended policies

-- Make sure storage extension functions are available (usually they are)
-- Then run:

-- Create bucket if you prefer SQL creation
-- SELECT storage.create_bucket('profile-pictures', true);

-- Storage policies that limit operations to the user's folder
DROP POLICY IF EXISTS "Users can insert their own files" ON storage.objects;
DROP POLICY IF EXISTS "Users can view their own files" ON storage.objects;
DROP POLICY IF EXISTS "Users can update/delete their own files" ON storage.objects;

CREATE POLICY "Users can insert their own files" ON storage.objects
  FOR INSERT WITH CHECK (
    auth.role() = 'authenticated' AND (split_part(name, '/', 1) = auth.uid())
  );

CREATE POLICY "Users can view their own files" ON storage.objects
  FOR SELECT USING (
    auth.role() = 'authenticated' AND (split_part(name, '/', 1) = auth.uid())
  );

CREATE POLICY "Users can update/delete their own files" ON storage.objects
  FOR UPDATE, DELETE USING (
    auth.role() = 'authenticated' AND (split_part(name, '/', 1) = auth.uid())
  );

Notes and caveats
- If your front-end still receives permission errors after applying these policies, check that your frontend uploads files with the object name that starts with the user's `auth.uid()` (string). If user IDs in your DB are UUIDs, auth.uid() should match the same UUID string.
- If you want other services (Edge Functions or servers) to access all files, use service-role key server-side; keep it secret.

If you want, I can now:
- A) Patch `main.js` to upload files under `currentUser.id/` path and adapt public URL extraction accordingly, or
- B) Produce a minimal SQL snippet to add RLS for `user_profiles` (we already updated earlier), or
- C) Harden the server_example proxy to validate Supabase JWTs before accepting uploads.

Which next action do you want? (reply with A, B, or C)
