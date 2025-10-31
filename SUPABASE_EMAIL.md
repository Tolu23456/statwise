Supabase email (signup) not sending — troubleshooting and fixes

Summary

If users register in your app but never receive verification or welcome emails, this is almost always an SMTP / email-provider configuration issue in your Supabase project. Supabase Auth relies on an SMTP provider (or the managed provider configuration) to deliver transactional emails.

Quick checklist

1. Supabase SMTP configured
   - Go to your Supabase project > Settings > Email > SMTP settings.
   - Fill in SMTP host, port, username, password, and "From" email.
   - Save and test using the built-in test button (if available).

2. Auth email templates enabled
   - In Supabase Dashboard > Authentication > Templates / Email, ensure the templates for "Confirmation" and "Invite" are active and contain valid content.

3. Domain / DNS and provider
   - If using SendGrid, Mailgun, Postmark, etc., make sure you verified the sending domain or configured DKIM/SPF as required by the provider.

4. Check Supabase logs
   - In Supabase Dashboard > Logs, inspect auth/email delivery attempts and errors.
   - Look for authentication errors from the SMTP server (invalid credentials, TLS requirements, blocked port, etc.).

5. Test manually
   - From the Dashboard SMTP test (if provided) or using a small script with your SMTP credentials, send a test message.

Alternative: Use a webhook / Edge Function to send email

If you prefer not to configure SMTP in Supabase or want more control, use an auth webhook / Edge Function triggered on user creation to send emails via a transactional provider (SendGrid, Postmark, Mailgun).

Example: Node (Edge Function) using SendGrid

Note: this example requires you to deploy a serverless/edge function and store your SendGrid API key securely (not in frontend code).

// index.js (Edge Function)

```javascript
import fetch from 'node-fetch';
import sgMail from '@sendgrid/mail';

sgMail.setApiKey(process.env.SENDGRID_API_KEY);

export default async function handler(req, res) {
  // Supabase will POST events to this endpoint
  const event = await req.json();
  if (!event || !event.type) return res.status(400).send('No event');

  // Example event: { "type": "user.created", "user": {"email": "..."} }
  if (event.type === 'user.created') {
    const user = event.user;
    const msg = {
      to: user.email,
      from: process.env.FROM_EMAIL, // verified sender
      subject: 'Welcome to StatWise — Confirm your email',
      text: `Hi ${user.user_metadata?.display_name || ''},\n\nWelcome! Please verify your email by clicking the link in your Supabase confirmation email (or contact support).`,
      html: `<p>Hi ${user.user_metadata?.display_name || ''},</p><p>Welcome to StatWise! Please check your inbox for a verification email. If you don't see it, check spam or visit the help center.</p>`
    };

    try {
      await sgMail.send(msg);
      return res.status(200).send('Email sent');
    } catch (err) {
      console.error('SendGrid error', err);
      return res.status(500).send('Send failed');
    }
  }

  return res.status(200).send('Ignored');
}
```

How to wire this up

- Deploy the function to a secure runtime (Vercel, Fly, Supabase Edge Functions, etc.).
- Configure environment variables: SENDGRID_API_KEY and FROM_EMAIL.
- Add a Supabase Auth webhook (Project > Authentication > Webhooks / Settings) that posts `user.created` events to your function endpoint.

Important notes

- Never store secret keys in frontend files (e.g., `env.js`). Use server-side environment variables or Supabase secrets.
- If you configure SMTP in Supabase, you won't need the webhook approach for email delivery — Supabase will handle confirmation emails.

If you want, I can:
- Add a sample Edge Function project under `/examples/supabase-email-webhook/` with deployment notes (you must provide any required API keys or deploy the function yourself).
- Add a UI hint in the signup flow that surfaces a clearer message to the site admin when email delivery seems disabled.

Tell me which you'd prefer and I'll implement it.
