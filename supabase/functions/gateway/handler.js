// Auth gateway: the only place that (a) enforces unique phone numbers, (b) resolves phone -> email, (c) checks CAPTCHA.
// Runs as a Supabase Edge Function (see index.ts); written as plain JS with injected env/fetch so it can be tested in Node.
import { normalizePhone, normalizeEmail, passwordProblem, parseIdentifier } from "./logic.js";

const J = (status, body, cors) => new Response(JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } });

function corsFor(env, req) {
  const allowed = env.ALLOWED_ORIGIN || "*";                 // set ALLOWED_ORIGIN=https://your-site.pages.dev in production
  return { "Access-Control-Allow-Origin": allowed, "Access-Control-Allow-Headers": "content-type, authorization, apikey, x-client-info", "Access-Control-Allow-Methods": "POST, OPTIONS", "Vary": "Origin" };
}

async function turnstileOk(env, token, ip, f) {
  if (!env.TURNSTILE_SECRET) return true;                    // CAPTCHA optional until you configure it
  if (typeof token !== "string" || !token) return false;
  const form = new URLSearchParams({ secret: env.TURNSTILE_SECRET, response: token });
  if (ip) form.set("remoteip", ip);
  try {
    const r = await f("https://challenges.cloudflare.com/turnstile/v0/siteverify", { method: "POST", body: form });
    return !!(await r.json()).success;
  } catch { return false; }
}

const svcHeaders = env => ({ apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: "Bearer " + env.SUPABASE_SERVICE_ROLE_KEY });

async function profileBy(env, f, column, value, select) {
  const url = `${env.SUPABASE_URL}/rest/v1/profiles?${column}=eq.${encodeURIComponent(value)}&select=${select}&limit=1`;
  const r = await f(url, { headers: svcHeaders(env) });
  if (!r.ok) throw new Error("profiles lookup failed: " + r.status);
  return (await r.json())[0] || null;
}

async function gotrue(env, f, path, body, { method = "POST", bearer } = {}) {
  const headers = { apikey: env.SUPABASE_ANON_KEY, "Content-Type": "application/json" };
  if (bearer) headers.Authorization = "Bearer " + bearer;
  const r = await f(`${env.SUPABASE_URL}/auth/v1/${path}`, { method, headers, body: JSON.stringify(body) });
  let data = {}; try { data = await r.json(); } catch { /* empty body */ }
  return { status: r.status, data };
}

export async function handler(req, env, f = fetch) {
  const cors = corsFor(env, req);
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
  if (req.method !== "POST") return J(405, { error: "method_not_allowed" }, cors);
  let body;
  try { body = await req.json(); } catch { return J(400, { error: "bad_request" }, cors); }
  if (!body || typeof body !== "object") return J(400, { error: "bad_request" }, cors);
  try {
    if (body.action === "signup") return await signup(body, req, env, f, cors);
    if (body.action === "login") return await login(body, env, f, cors);
    if (body.action === "forgot") return await forgot(body, req, env, f, cors);
    if (body.action === "reset") return await reset(body, env, f, cors);
    return J(400, { error: "unknown_action" }, cors);
  } catch (e) {
    console.error("gateway error", e && e.message);
    return J(502, { error: "upstream" }, cors);
  }
}

async function signup(b, req, env, f, cors) {
  const phone = normalizePhone(b.phone), email = normalizeEmail(b.email), pwErr = passwordProblem(b.password), fields = {};
  if (!phone) fields.phone = "Enter a valid 10-digit Indian mobile number.";
  if (!email) fields.email = "Enter a valid email address.";
  if (pwErr) fields.password = pwErr;
  if (Object.keys(fields).length) return J(422, { error: "validation", fields }, cors);
  if (!(await turnstileOk(env, b.captcha, req.headers.get("cf-connecting-ip"), f))) return J(403, { error: "captcha_failed" }, cors);
  if (await profileBy(env, f, "phone", phone, "id")) return J(409, { error: "phone_taken" }, cors);
  const r = await gotrue(env, f, "signup", { email, password: b.password, data: { phone } });
  if (r.status === 200) return J(200, { ok: true, email }, cors);          // GoTrue answers 200 for already-registered emails too (no enumeration)
  const code = r.data.error_code || r.data.code;
  if (code === "weak_password") return J(422, { error: "validation", fields: { password: "That password is too weak." } }, cors);
  if (r.status === 429 || String(code).includes("rate_limit")) return J(429, { error: "rate_limited" }, cors);
  return J(r.status >= 500 ? 502 : 400, { error: "signup_failed" }, cors);
}

async function login(b, env, f, cors) {
  const id = parseIdentifier(b.identifier);
  if (!id || typeof b.password !== "string" || !b.password) return J(401, { error: "invalid_credentials" }, cors);   // same answer as a wrong password
  let email = id.email;
  if (id.kind === "phone") {
    const p = await profileBy(env, f, "phone", id.phone, "email");
    if (!p) return J(401, { error: "invalid_credentials" }, cors);
    email = p.email;
  }
  const r = await gotrue(env, f, "token?grant_type=password", { email, password: b.password });
  if (r.status === 200) return J(200, { ok: true, session: r.data }, cors);
  const code = r.data.error_code || r.data.code;
  if (code === "email_not_confirmed") return J(403, { error: "email_not_confirmed", email }, cors);   // only reachable with the correct password
  if (r.status === 429 || String(code).includes("rate_limit")) return J(429, { error: "rate_limited" }, cors);
  if (r.status >= 500) return J(502, { error: "upstream" }, cors);
  return J(401, { error: "invalid_credentials" }, cors);
}

/** Email for an identifier (email as-is; phone via profiles). null when no such account. */
async function emailFor(id, env, f) {
  if (id.kind === "email") return id.email;
  const p = await profileBy(env, f, "phone", id.phone, "email");
  return p ? p.email : null;
}

/** Step 1 of password reset: e-mail a 6-digit code. Always answers 200 for well-formed input, so it cannot be used to find out who is registered. */
async function forgot(b, req, env, f, cors) {
  const id = parseIdentifier(b.identifier);
  if (!id) return J(422, { error: "validation", fields: { identifier: "Enter the email or 10-digit mobile number you signed up with." } }, cors);
  if (!(await turnstileOk(env, b.captcha, req.headers.get("cf-connecting-ip"), f))) return J(403, { error: "captcha_failed" }, cors);
  const email = await emailFor(id, env, f);
  if (email) {
    const r = await gotrue(env, f, "recover", { email });
    if (r.status === 429 || String(r.data.error_code || "").includes("rate_limit")) return J(429, { error: "rate_limited" }, cors);
  }
  return J(200, { ok: true }, cors);
}

/** Step 2: check the code, set the new password, return a session (the user is logged in afterwards). */
async function reset(b, env, f, cors) {
  const id = parseIdentifier(b.identifier), pwErr = passwordProblem(b.password), fields = {};
  if (!id) fields.identifier = "Enter the email or mobile number you used.";
  if (typeof b.code !== "string" || !/^\d{6}$/.test(b.code)) fields.code = "Enter the 6-digit code.";
  if (pwErr) fields.password = pwErr;
  if (Object.keys(fields).length) return J(422, { error: "validation", fields }, cors);
  const email = await emailFor(id, env, f);
  if (!email) return J(400, { error: "invalid_code" }, cors);                       // same answer as a wrong code
  const v = await gotrue(env, f, "verify", { type: "recovery", email, token: b.code });
  if (v.status === 429) return J(429, { error: "rate_limited" }, cors);
  if (v.status >= 500) return J(502, { error: "upstream" }, cors);
  if (v.status !== 200 || !v.data.access_token) return J(400, { error: "invalid_code" }, cors);
  const u = await gotrue(env, f, "user", { password: b.password }, { method: "PUT", bearer: v.data.access_token });
  if (u.status === 200) return J(200, { ok: true, session: v.data }, cors);
  const code = u.data.error_code || u.data.code;
  if (code === "same_password") return J(422, { error: "validation", fields: { password: "Choose a password different from your current one." } }, cors);
  if (code === "weak_password") return J(422, { error: "validation", fields: { password: "That password is too weak." } }, cors);
  return J(502, { error: "upstream" }, cors);
}
