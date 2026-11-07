// Public settings (safe to commit: the anon key is designed to be public; Row Level Security protects data).
// Leave supabaseUrl empty to run without accounts. NEVER put the service_role key here.
window.FUNDRIX_CONFIG = {
  supabaseUrl: "",        // e.g. "https://abcd1234.supabase.co"
  anonKey: "",            // Project Settings -> API -> anon public key
  turnstileSiteKey: "",   // optional: Cloudflare Turnstile site key (free CAPTCHA) for the sign-up form
};
