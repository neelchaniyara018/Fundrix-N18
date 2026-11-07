# Accounts setup (Supabase, free tier)

Sign-up collects **phone + email + password**; a **6-digit code is emailed** to verify the email. Log in with **email or phone** + password.
SMS OTP is intentionally not used (it is never free). The phone number is stored and unique, but NOT verified.

## One-time setup
1. **Create a project** at supabase.com (free plan). Note the *Project URL* and the *anon public* key (Project Settings -> API).
2. **Database:** SQL Editor -> paste `supabase/schema.sql` -> Run. (Creates `profiles` + triggers + Row Level Security.)
3. **Auth settings** (Authentication section):
   * Email provider enabled, **Confirm email = ON**.
   * **Minimum password length = 8** (the app enforces 8 + a letter + a number; the server must not be weaker).
   * **Email templates -> Confirm signup:** the body must contain the code, e.g. `Your Fundrix N18 code: {{ .Token }}`. (If it only contains a link, users never see the code.)
   * **Email templates -> Reset Password:** also put the code in it, e.g. `Your Fundrix N18 password reset code: {{ .Token }}`. Forgot-password uses the same 6-digit-code flow.
   * **SMTP:** the built-in sender is for testing and heavily rate limited. Add a free SMTP provider (e.g. Brevo or Resend) under Authentication -> SMTP. Check that provider's current free limits.
4. **Deploy the gateway** (needs the Supabase CLI):
   ```bash
   supabase login && supabase link --project-ref <your-ref>
   supabase functions deploy gateway --no-verify-jwt
   supabase secrets set ALLOWED_ORIGIN=https://<your-site>.pages.dev        # exact origin of your site
   # optional CAPTCHA (free Cloudflare Turnstile): supabase secrets set TURNSTILE_SECRET=<secret>
   ```
   `SUPABASE_URL`, `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` are provided to Edge Functions automatically (confirm in your project's function settings). The service-role key must never appear in `frontend/`.
5. **Frontend:** edit `frontend/config.js` -> `supabaseUrl`, `anonKey` (and `turnstileSiteKey` if you enabled Turnstile). Deploy `frontend/` to Cloudflare Pages.

## How it works
* `gateway` (Edge Function): `signup` validates, checks CAPTCHA, rejects an already-registered phone, then calls Supabase Auth with the phone in user metadata. `login` accepts email or phone; for a phone it looks up the email **server-side**; unknown phone, malformed input and wrong password all return the identical `invalid_credentials`.
* The browser calls Supabase Auth directly only for: verify code, resend code, refresh token, logout.
* A DB trigger creates the `profiles` row **only after the email is confirmed**, so an unverified sign-up cannot block someone else's phone number. If two people confirm with the same phone at nearly the same time, the second keeps the account but without the phone.
* Phone login therefore works only after email verification; before that, log in with the email to receive a fresh code.

## Forgot password
`#/forgot`: the user enters their email **or phone**; the gateway finds the account's email server-side and Supabase emails a 6-digit code. The page always says "if an account exists, we have emailed a code", so it cannot be used to discover who is registered. Step 2 takes the code and a new password; on success the user is logged in. Reusing the old password is refused. Other existing sessions are *not* signed out automatically.

## First live test checklist (not verifiable offline)
1. Sign up -> the email arrives and shows a 6-digit code.  2. Wrong code is rejected; right code logs you in.  3. Row appears in `profiles` with `+91...`.
4. Log out, log in with phone, then with email.  5. Second sign-up with the same phone shows "already registered".  6. Open the site from another origin -> gateway refuses via CORS.  7. Forgot password with email, then with phone: the code arrives, the new password works, the old one does not.
The tests in `tests/` use a local mock of Supabase modelled on its documented API; if the real service differs (error codes, response shapes), the fix is small and localized to `handler.js` / `auth.js`.

## Not included yet
Changing email/phone, phone verification by SMS, and linking saved screens/watchlists to the account (they still use the anonymous browser id).
