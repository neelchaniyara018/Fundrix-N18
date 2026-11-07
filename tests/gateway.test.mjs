// Run:  node --test tests/gateway.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { normalizePhone, normalizeEmail, passwordProblem, parseIdentifier } from "../supabase/functions/gateway/logic.js";
import { handler } from "../supabase/functions/gateway/handler.js";

test("normalizePhone accepts common Indian formats", () => {
  for (const x of ["9876543210", "+919876543210", "+91 98765 43210", "919876543210", "09876543210", "0091-98765-43210", "(98765) 43210", " 9876543210 "])
    assert.equal(normalizePhone(x), "+919876543210", x);
  assert.equal(normalizePhone("9123456789"), "+919123456789");     // starts with 91 but is a 10-digit number
});
test("normalizePhone rejects bad input", () => {
  for (const x of ["", "12345", "5876543210", "98765432101", "+14155550123", "98765abcde", null, undefined, 9876543210, "+91", "9876543210; drop table"])
    assert.equal(normalizePhone(x), null, String(x));
});
test("normalizeEmail", () => {
  assert.equal(normalizeEmail("  Foo@Example.COM "), "foo@example.com");
  for (const x of ["a", "a@b", "a b@c.com", "@x.com", "a@@x.com", null, "a".repeat(250) + "@x.com"]) assert.equal(normalizeEmail(x), null, String(x));
});
test("passwordProblem", () => {
  assert.equal(passwordProblem("abcdefg1"), null);
  assert.ok(passwordProblem("short1")); assert.ok(passwordProblem("onlyletters")); assert.ok(passwordProblem("12345678"));
  assert.ok(passwordProblem("a1".repeat(40))); assert.ok(passwordProblem(undefined));
  assert.equal(passwordProblem("pässwörd1"), null);
});
test("parseIdentifier", () => {
  assert.deepEqual(parseIdentifier("A@b.co"), { kind: "email", email: "a@b.co" });
  assert.deepEqual(parseIdentifier("98765 43210"), { kind: "phone", phone: "+919876543210" });
  assert.equal(parseIdentifier("nonsense"), null); assert.equal(parseIdentifier(""), null); assert.equal(parseIdentifier(5), null);
});

// ---- handler with a fake Supabase
const ENV = { SUPABASE_URL: "http://sb", SUPABASE_ANON_KEY: "anon", SUPABASE_SERVICE_ROLE_KEY: "svc" };
function fake({ profile = null, auth = { status: 200, data: {} }, profilesStatus = 200 } = {}) {
  const calls = [];
  const f = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const u = String(url);
    if (u.includes("/rest/v1/profiles")) return new Response(JSON.stringify(profilesStatus === 200 ? (profile ? [profile] : []) : { message: "x" }), { status: profilesStatus });
    if (u.includes("/auth/v1/")) return new Response(JSON.stringify(auth.data), { status: auth.status });
    if (u.includes("siteverify")) return new Response(JSON.stringify({ success: init.body.get("response") === "good" }), { status: 200 });
    throw new Error("unexpected " + u);
  };
  return { f, calls };
}
const call = (body, env = ENV, f, method = "POST", raw) => handler(new Request("http://x/gateway", { method, headers: { "content-type": "application/json" }, body: method === "POST" ? (raw ?? JSON.stringify(body)) : undefined }), env, f);
const good = { action: "signup", phone: "98765 43210", email: "A@B.com", password: "secret123" };

test("CORS preflight and method guard", async () => {
  const r = await call(null, { ...ENV, ALLOWED_ORIGIN: "https://app.pages.dev" }, fake().f, "OPTIONS");
  assert.equal(r.status, 204); assert.equal(r.headers.get("access-control-allow-origin"), "https://app.pages.dev");
  assert.equal((await call(null, ENV, fake().f, "GET")).status, 405);
});
test("malformed JSON and unknown action", async () => {
  assert.equal((await call(null, ENV, fake().f, "POST", "{nope")).status, 400);
  assert.equal((await call(null, ENV, fake().f, "POST", "null")).status, 400);
  assert.equal((await call({ action: "x" }, ENV, fake().f)).status, 400);
});
test("signup validation returns per-field errors and never touches Supabase", async () => {
  const { f, calls } = fake();
  const r = await call({ action: "signup", phone: "123", email: "bad", password: "x" }, ENV, f);
  const j = await r.json(); assert.equal(r.status, 422); assert.deepEqual(Object.keys(j.fields).sort(), ["email", "password", "phone"]); assert.equal(calls.length, 0);
});
test("signup success sends normalized phone/email to GoTrue (phone in user metadata)", async () => {
  const { f, calls } = fake(); const r = await call(good, ENV, f); const j = await r.json();
  assert.equal(r.status, 200); assert.deepEqual(j, { ok: true, email: "a@b.com" });
  const g = calls.find(c => c.url.endsWith("/auth/v1/signup")); const body = JSON.parse(g.init.body);
  assert.equal(body.email, "a@b.com"); assert.deepEqual(body.data, { phone: "+919876543210" }); assert.equal(g.init.headers.apikey, "anon");
  const p = calls.find(c => c.url.includes("/rest/v1/profiles")); assert.ok(p.url.includes("phone=eq.%2B919876543210")); assert.equal(p.init.headers.apikey, "svc");
});
test("signup: phone already registered -> 409 and no account is created", async () => {
  const { f, calls } = fake({ profile: { id: "u1" } }); const r = await call(good, ENV, f);
  assert.equal(r.status, 409); assert.equal((await r.json()).error, "phone_taken"); assert.ok(!calls.some(c => c.url.includes("/auth/v1/signup")));
});
test("signup: weak password / rate limit / upstream failure mapping", async () => {
  assert.equal((await call(good, ENV, fake({ auth: { status: 422, data: { error_code: "weak_password" } } }).f)).status, 422);
  assert.equal((await call(good, ENV, fake({ auth: { status: 429, data: { error_code: "over_email_send_rate_limit" } } }).f)).status, 429);
  assert.equal((await call(good, ENV, fake({ auth: { status: 500, data: {} } }).f)).status, 502);
  assert.equal((await call(good, ENV, fake({ profilesStatus: 500 }).f)).status, 502);
});
test("signup: CAPTCHA enforced only when TURNSTILE_SECRET is set", async () => {
  const env = { ...ENV, TURNSTILE_SECRET: "s" };
  assert.equal((await call(good, env, fake().f)).status, 403);
  assert.equal((await call({ ...good, captcha: "bad" }, env, fake().f)).status, 403);
  assert.equal((await call({ ...good, captcha: "good" }, env, fake().f)).status, 200);
});
test("login by email forwards credentials to GoTrue and returns the session", async () => {
  const { f, calls } = fake({ auth: { status: 200, data: { access_token: "t", user: { id: "1" } } } });
  const r = await call({ action: "login", identifier: " Me@X.com ", password: "pw" }, ENV, f); const j = await r.json();
  assert.equal(r.status, 200); assert.equal(j.session.access_token, "t");
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { email: "me@x.com", password: "pw" });
  assert.ok(!calls.some(c => c.url.includes("/rest/v1/profiles")));
});
test("login by phone resolves the email server-side", async () => {
  const { f, calls } = fake({ profile: { email: "me@x.com" }, auth: { status: 200, data: { access_token: "t" } } });
  const r = await call({ action: "login", identifier: "+91 98765-43210", password: "pw" }, ENV, f);
  assert.equal(r.status, 200); assert.equal(JSON.parse(calls.at(-1).init.body).email, "me@x.com");
});
test("login: unknown phone, unparsable id and wrong password are indistinguishable", async () => {
  const unknown = await call({ action: "login", identifier: "9876543210", password: "pw" }, ENV, fake({ profile: null }).f);
  const garbage = await call({ action: "login", identifier: "zzz", password: "pw" }, ENV, fake().f);
  const wrong = await call({ action: "login", identifier: "a@b.com", password: "pw" }, ENV, fake({ auth: { status: 400, data: { error_code: "invalid_credentials" } } }).f);
  const bodies = await Promise.all([unknown, garbage, wrong].map(async r => [r.status, await r.text()]));
  for (const b of bodies) assert.deepEqual(b, [401, '{"error":"invalid_credentials"}']);
});
test("login: unconfirmed email / rate limit / upstream", async () => {
  const unc = await call({ action: "login", identifier: "a@b.com", password: "pw" }, ENV, fake({ auth: { status: 400, data: { error_code: "email_not_confirmed" } } }).f);
  assert.equal(unc.status, 403); assert.deepEqual(await unc.json(), { error: "email_not_confirmed", email: "a@b.com" });
  assert.equal((await call({ action: "login", identifier: "a@b.com", password: "pw" }, ENV, fake({ auth: { status: 429, data: {} } }).f)).status, 429);
  assert.equal((await call({ action: "login", identifier: "a@b.com", password: "pw" }, ENV, fake({ auth: { status: 503, data: {} } }).f)).status, 502);
});
test("responses are never cached", async () => {
  assert.equal((await call({ action: "x" }, ENV, fake().f)).headers.get("cache-control"), "no-store");
});

// ---- forgot / reset password
function fake2({ profile = null, recover = { status: 200, data: {} }, verify = { status: 200, data: { access_token: "rt_tok", user: { id: "1" } } }, user = { status: 200, data: {} } } = {}) {
  const calls = [];
  const f = async (url, init = {}) => {
    const u = String(url); calls.push({ url: u, init });
    if (u.includes("/rest/v1/profiles")) return new Response(JSON.stringify(profile ? [profile] : []), { status: 200 });
    const r = u.endsWith("/auth/v1/recover") ? recover : u.endsWith("/auth/v1/verify") ? verify : u.endsWith("/auth/v1/user") ? user : null;
    if (!r) throw new Error("unexpected " + u);
    return new Response(JSON.stringify(r.data), { status: r.status });
  };
  return { f, calls };
}
test("forgot: sends a recovery code for an email", async () => {
  const { f, calls } = fake2(); const r = await call({ action: "forgot", identifier: " A@B.com " }, ENV, f);
  assert.equal(r.status, 200); assert.deepEqual(await r.json(), { ok: true });
  assert.deepEqual(JSON.parse(calls.find(c => c.url.endsWith("/recover")).init.body), { email: "a@b.com" });
});
test("forgot: by phone resolves the email server-side", async () => {
  const { f, calls } = fake2({ profile: { email: "me@x.com" } }); const r = await call({ action: "forgot", identifier: "98765 43210" }, ENV, f);
  assert.equal(r.status, 200); assert.equal(JSON.parse(calls.find(c => c.url.endsWith("/recover")).init.body).email, "me@x.com");
});
test("forgot: unknown phone answers exactly like a known one and sends nothing", async () => {
  const known = fake2({ profile: { email: "me@x.com" } }), unknown = fake2({ profile: null });
  const a = await call({ action: "forgot", identifier: "9876543210" }, ENV, known.f), b = await call({ action: "forgot", identifier: "9876543210" }, ENV, unknown.f);
  assert.equal(a.status, b.status); assert.equal(await a.text(), await b.text()); assert.ok(!unknown.calls.some(c => c.url.endsWith("/recover")));
});
test("forgot: malformed identifier -> 422; rate limit -> 429; captcha enforced when configured", async () => {
  assert.equal((await call({ action: "forgot", identifier: "nonsense" }, ENV, fake2().f)).status, 422);
  assert.equal((await call({ action: "forgot", identifier: "a@b.com" }, ENV, fake2({ recover: { status: 429, data: { error_code: "over_email_send_rate_limit" } } }).f)).status, 429);
  const env = { ...ENV, TURNSTILE_SECRET: "s" };
  assert.equal((await call({ action: "forgot", identifier: "a@b.com" }, env, fake2().f)).status, 403);
});
const rs = { action: "reset", identifier: "a@b.com", code: "123456", password: "newpass99" };
test("reset: verifies the code, then sets the password with the recovery token", async () => {
  const { f, calls } = fake2(); const r = await call(rs, ENV, f); const j = await r.json();
  assert.equal(r.status, 200); assert.equal(j.session.access_token, "rt_tok");
  assert.deepEqual(JSON.parse(calls.find(c => c.url.endsWith("/verify")).init.body), { type: "recovery", email: "a@b.com", token: "123456" });
  const put = calls.find(c => c.url.endsWith("/auth/v1/user")); assert.equal(put.init.method, "PUT"); assert.equal(put.init.headers.Authorization, "Bearer rt_tok");
  assert.deepEqual(JSON.parse(put.init.body), { password: "newpass99" });
});
test("reset: by phone", async () => {
  const { f, calls } = fake2({ profile: { email: "me@x.com" } }); assert.equal((await call({ ...rs, identifier: "9876543210" }, ENV, f)).status, 200);
  assert.equal(JSON.parse(calls.find(c => c.url.endsWith("/verify")).init.body).email, "me@x.com");
});
test("reset: validation happens before any network call", async () => {
  const { f, calls } = fake2(); const r = await call({ action: "reset", identifier: "zz", code: "12", password: "x" }, ENV, f);
  assert.equal(r.status, 422); assert.deepEqual(Object.keys((await r.json()).fields).sort(), ["code", "identifier", "password"]); assert.equal(calls.length, 0);
});
test("reset: wrong code / unknown phone are the same 400 and never reach the password step", async () => {
  const bad = fake2({ verify: { status: 403, data: { error_code: "otp_expired" } } }), unk = fake2({ profile: null });
  const a = await call(rs, ENV, bad.f), b = await call({ ...rs, identifier: "9876543210" }, ENV, unk.f);
  assert.equal(a.status, 400); assert.equal(b.status, 400); assert.equal(await a.text(), await b.text());
  assert.ok(!bad.calls.some(c => c.url.endsWith("/auth/v1/user")));
});
test("reset: same / weak password and upstream errors are mapped", async () => {
  const same = await call(rs, ENV, fake2({ user: { status: 422, data: { error_code: "same_password" } } }).f);
  assert.equal(same.status, 422); assert.ok((await same.json()).fields.password.includes("different"));
  assert.equal((await call(rs, ENV, fake2({ user: { status: 422, data: { error_code: "weak_password" } } }).f)).status, 422);
  assert.equal((await call(rs, ENV, fake2({ user: { status: 500, data: {} } }).f)).status, 502);
  assert.equal((await call(rs, ENV, fake2({ verify: { status: 429, data: {} } }).f)).status, 429);
  assert.equal((await call(rs, ENV, fake2({ verify: { status: 500, data: {} } }).f)).status, 502);
});
