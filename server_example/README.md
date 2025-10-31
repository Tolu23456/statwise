# StatWise Upload Proxy (example)

This small example shows how to implement a server-side upload proxy that accepts file uploads from the browser and forwards them to Supabase Storage using a service-role key.

Why: The browser SDK doesn't expose byte-level upload progress when uploading directly to Supabase Storage. Using a server proxy lets the browser upload to your server (XHR/fetch) and track progress, while the server securely uploads to Supabase.

Files:
- `server.js` - Express server that accepts `POST /upload` multipart/form-data and uploads buffer to Supabase Storage.
- `package.json` - minimal deps and start script.

Setup:
1. Create a new project folder and copy these files, or use the `server_example` folder.
2. Install dependencies:

```bash
cd server_example
npm install
```

3. Set environment variables (do NOT expose the service role key in client code):

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="<SERVICE_ROLE_KEY_FROM_SUPABASE>"
export SUPABASE_BUCKET="profile-pictures" # optional
```

4. Start the server:

```bash
npm start
```

Client integration (browser):
- Use XHR or fetch to POST a multipart/form-data to `http://your-server:3001/upload` with the file under the `file` key and optionally `userId` as form field. XHR supports progress events for uploads.

Example (brief):

```javascript
const form = new FormData();
form.append('file', fileInput.files[0]);
form.append('userId', currentUser.id);

const xhr = new XMLHttpRequest();
xhr.open('POST', 'https://your-server.example.com/upload');

xhr.upload.onprogress = (ev) => {
  if (ev.lengthComputable) {
    const percent = Math.round((ev.loaded / ev.total) * 100);
    console.log('Upload progress', percent);
  }
};

xhr.onload = () => {
  const res = JSON.parse(xhr.responseText);
  console.log('Upload complete, server response:', res);
};

xhr.send(form);
```

Security notes:
- Keep `SUPABASE_SERVICE_ROLE_KEY` secret on the server only.
- Validate/authenticate incoming requests to the proxy in production (e.g., check a JWT from Supabase Auth).
- Consider rate limits and virus scanning if accepting arbitrary uploads.
