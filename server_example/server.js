// server.js - Simple upload proxy for Supabase Storage
// Usage: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables, then `npm install` and `npm start`

import express from 'express';
import multer from 'multer';
import { createClient } from '@supabase/supabase-js';

const app = express();
const upload = multer({ storage: multer.memoryStorage() });
const PORT = process.env.PORT || 3001;

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
const BUCKET = process.env.SUPABASE_BUCKET || 'profile-pictures';

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
  console.error('Please set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables.');
  process.exit(1);
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false }
});

app.get('/', (req, res) => res.send('StatWise upload proxy running'));

// POST /upload - accepts multipart/form-data with `file` and optional `userId` fields
app.post('/upload', upload.single('file'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded' });

    const userId = req.body.userId || 'anon';
    const originalName = req.file.originalname || 'upload';
    const ext = originalName.split('.').pop() || 'bin';
    const filename = `${userId}-${Date.now()}.${ext}`;

    // Upload buffer to Supabase Storage using service role key
    const { data, error } = await supabase.storage
      .from(BUCKET)
      .upload(filename, req.file.buffer, {
        contentType: req.file.mimetype,
        upsert: true
      });

    if (error) {
      console.error('Supabase upload error:', error);
      return res.status(500).json({ error: error.message || 'Upload failed' });
    }

    // Make public URL (works if bucket is public)
    const { data: urlData } = supabase.storage.from(BUCKET).getPublicUrl(filename);

    return res.json({ fileKey: data?.path || filename, publicUrl: urlData?.publicUrl || null });
  } catch (err) {
    console.error('Upload proxy error:', err);
    return res.status(500).json({ error: String(err) });
  }
});

app.listen(PORT, () => {
  console.log(`Upload proxy listening on port ${PORT}`);
});
