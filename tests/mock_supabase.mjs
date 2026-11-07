// Local stand-in for Supabase used by the browser tests:  node tests/mock_supabase.mjs <port>
// Runs the REAL gateway handler in front of a fake GoTrue (/auth/v1/*) and PostgREST (/rest/v1/profiles).
// GoTrue behaviour is modelled from its documented API, NOT verified against a live project.
import http from "node:http";
import crypto from "node:crypto";
import { handler } from "../supabase/functions/gateway/handler.js";

const port = +process.argv[2] || 54321, BASE = `http://127.0.0.1:${port}`;
const env = { SUPABASE_URL: BASE, SUPABASE_ANON_KEY: "anon-key", SUPABASE_SERVICE_ROLE_KEY: "service-key", ALLOWED_ORIGIN: "*" };
let db = { screens: [], watchlist: [] };
let users = new Map(), profiles = [], sessions = new Map(), outbox = new Map(), resendAt = new Map(), codeTtlMs = 600000;
const rid = () => crypto.randomUUID(), tok = () => crypto.randomBytes(16).toString("hex");
const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*", "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS" };
const send = (res, status, body) => { res.writeHead(status, { ...cors, "Content-Type": "application/json" }); res.end(body === undefined ? "" : JSON.stringify(body)); };
const err = (res, status, code, msg) => send(res, status, { code: status, error_code: code, msg });
const accessTokens = new Map();
const issue = u => { const s = { access_token: "at_" + tok(), refresh_token: "rt_" + tok(), token_type: "bearer", expires_in: 3600, user: { id: u.id, email: u.email, user_metadata: u.meta } }; sessions.set(s.refresh_token, u.id); accessTokens.set(s.access_token, u.id); return s; };
const newCode = email => { const c = String(crypto.randomInt(0, 1e6)).padStart(6, "0"); const u = users.get(email); u.code = c; u.codeAt = Date.now(); outbox.set(email, c); return c; };
function confirm(u) {                                   // mirrors supabase/schema.sql trigger
  u.confirmed = true; let p = u.meta.phone || null;
  if (p && !/^\+91[6-9]\d{9}$/.test(p)) p = null;
  if (p && profiles.some(x => x.phone === p)) p = null;
  profiles.push({ id: u.id, email: u.email, phone: p });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, BASE); const chunks = []; for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString(); const path = url.pathname;
  if (req.method === "OPTIONS") { res.writeHead(204, cors); return res.end(); }
  if (path === "/functions/v1/gateway") {
    const r = await handler(new Request(BASE + path, { method: req.method, headers: req.headers, body: ["GET", "HEAD"].includes(req.method) ? undefined : raw }), env, fetch);
    res.writeHead(r.status, Object.fromEntries(r.headers)); return res.end(await r.text());
  }
  if (path === "/__mock/otp") return send(res, 200, { code: outbox.get((url.searchParams.get("type") === "recovery" ? "recovery:" : "") + url.searchParams.get("email")) || null });
  if (path === "/__mock/expire") { codeTtlMs = +url.searchParams.get("ms"); return send(res, 200, {}); }
  if (path === "/__mock/reset") { db = { screens: [], watchlist: [] }; users = new Map(); profiles = []; sessions = new Map(); outbox = new Map(); resendAt = new Map(); codeTtlMs = 600000; return send(res, 200, {}); }
  if (path === "/__mock/state") return send(res, 200, { profiles, users: [...users.values()].map(u => ({ email: u.email, confirmed: u.confirmed })) });
  if (path === "/__mock/db") return send(res, 200, db);
  if (path.startsWith("/rest/v1/") && path !== "/rest/v1/profiles") {                  // PostgREST subset used by the app (RLS emulated by the bearer token)
    const bearer = (req.headers.authorization || "").replace("Bearer ", ""), uid = accessTokens.get(bearer);
    if (req.headers.apikey !== "anon-key") return err(res, 401, "no_api_key", "No API key found in request");
    let b = {}; try { b = raw ? JSON.parse(raw) : {}; } catch { return send(res, 400, { message: "bad json" }); }
    const table = path.slice(9), prefer = req.headers.prefer || "", eq = k => { const m = /^eq\.(.*)$/.exec(url.searchParams.get(k) || ""); return m ? m[1] : null; };
    const rep = prefer.includes("return=representation");
    if (table === "rpc/get_shared_screen") {
      const s = db.screens.find(x => x.id === b.p_id);
      return send(res, 200, s ? [{ id: s.id, name: s.name, note: s.note, config: s.config }] : []);
    }
    if (!uid) return send(res, 401, { message: "JWT expired or missing" });
    if (table === "screens") {
      const mine = db.screens.filter(x => x.user_id === uid);
      if (req.method === "GET") { let rows = mine; if (eq("id")) rows = rows.filter(x => x.id === eq("id")); rows = [...rows].sort((a, c) => c.updated_at.localeCompare(a.updated_at)); return send(res, 200, rows.map(({ user_id, ...r }) => r)); }
      if (req.method === "POST") {
        if (typeof b.name !== "string" || b.name.length < 1 || b.name.length > 80) return send(res, 400, { message: 'new row for relation "screens" violates check constraint "screens_name_check"' });
        if (!b.config || typeof b.config !== "object") return send(res, 400, { message: "null value in column config" });
        const now = new Date().toISOString(), row = { id: crypto.randomUUID(), user_id: uid, name: b.name, note: b.note || "", config: b.config, created_at: now, updated_at: now };
        db.screens.push(row); const { user_id, ...pub } = row; return send(res, 201, rep ? [pub] : undefined);
      }
      if (req.method === "PATCH") {
        const row = mine.find(x => x.id === eq("id")); if (!row) return rep ? send(res, 200, []) : send(res, 204);
        Object.assign(row, b); const { user_id, ...pub } = row; return rep ? send(res, 200, [pub]) : send(res, 204);
      }
      if (req.method === "DELETE") { db.screens = db.screens.filter(x => !(x.user_id === uid && x.id === eq("id"))); return send(res, 204); }
    }
    if (table === "watchlist") {
      if (req.method === "GET") return send(res, 200, db.watchlist.filter(x => x.user_id === uid).map(x => ({ fund_id: x.fund_id })));
      if (req.method === "POST") {
        const items = Array.isArray(b) ? b : [b];
        if (items.some(i => !Number.isInteger(i.fund_id) || i.fund_id <= 0)) return send(res, 400, { message: "violates check constraint" });
        for (const i of items) if (!db.watchlist.some(x => x.user_id === uid && x.fund_id === i.fund_id)) db.watchlist.push({ user_id: uid, fund_id: i.fund_id });
        return send(res, 201);
      }
      if (req.method === "DELETE") { const f = Number(eq("fund_id")); db.watchlist = db.watchlist.filter(x => !(x.user_id === uid && x.fund_id === f)); return send(res, 204); }
    }
    return send(res, 404, { message: "not found" });
  }
  if (path === "/rest/v1/profiles") {
    if (req.headers.authorization !== "Bearer service-key") return err(res, 401, "bad_jwt", "no");
    const m = /^eq\.(.*)$/.exec(url.searchParams.get("phone") || "");
    return send(res, 200, profiles.filter(p => !m || p.phone === m[1]).slice(0, 1));
  }
  if (!path.startsWith("/auth/v1/")) return send(res, 404, {});
  if (req.headers.apikey !== "anon-key") return err(res, 401, "no_authorization", "No API key");
  let b = {}; try { b = raw ? JSON.parse(raw) : {}; } catch { return err(res, 400, "bad_json", "bad json"); }
  const route = path.slice(9);
  if (route === "signup") {
    if (typeof b.password !== "string" || b.password.length < 6) return err(res, 422, "weak_password", "weak");
    const ex = users.get(b.email);
    if (ex && ex.confirmed) return send(res, 200, { id: rid(), email: b.email, identities: [] });         // obfuscated, like GoTrue
    const u = { id: rid(), email: b.email, password: b.password, meta: b.data || {}, confirmed: false }; users.set(b.email, u); newCode(b.email);
    return send(res, 200, { id: u.id, email: u.email, identities: [{}] });
  }
  if (route === "recover") {                                                   // like GoTrue: 200 whether or not the user exists
    const u = users.get(b.email); if (u && u.confirmed) { if (Date.now() - (resendAt.get("r" + b.email) || 0) < 1500) return err(res, 429, "over_email_send_rate_limit", "rate limited");
      resendAt.set("r" + b.email, Date.now()); u.rcode = String(crypto.randomInt(0, 1e6)).padStart(6, "0"); u.rcodeAt = Date.now(); outbox.set("recovery:" + b.email, u.rcode); }
    return send(res, 200, {});
  }
  if (route === "user" && req.method === "PUT") {
    const id = accessTokens.get((req.headers.authorization || "").replace("Bearer ", "")); const u = id && [...users.values()].find(x => x.id === id);
    if (!u) return err(res, 401, "bad_jwt", "invalid token");
    if (typeof b.password !== "string" || b.password.length < 6) return err(res, 422, "weak_password", "weak");
    if (b.password === u.password) return err(res, 422, "same_password", "New password should be different from the old password.");
    u.password = b.password; return send(res, 200, { id: u.id, email: u.email });
  }
  if (route === "verify" && b.type === "recovery") {
    const u = users.get(b.email);
    if (!u || !u.rcode || u.rcode !== String(b.token) || Date.now() - u.rcodeAt > codeTtlMs) return err(res, 403, "otp_expired", "Token has expired or is invalid");
    u.rcode = null; return send(res, 200, issue(u));
  }
  if (route === "verify") {
    const u = users.get(b.email);
    if (!u || b.type !== "signup" || u.code !== String(b.token) || Date.now() - u.codeAt > codeTtlMs) return err(res, 403, "otp_expired", "Token has expired or is invalid");
    if (!u.confirmed) confirm(u); u.code = null; return send(res, 200, issue(u));
  }
  if (route === "resend") {
    const u = users.get(b.email); if (!u || u.confirmed) return send(res, 200, {});
    if (Date.now() - (resendAt.get(b.email) || 0) < 1500) return err(res, 429, "over_email_send_rate_limit", "rate limited");
    resendAt.set(b.email, Date.now()); newCode(b.email); return send(res, 200, {});
  }
  if (route === "token") {
    const gt = url.searchParams.get("grant_type");
    if (gt === "password") {
      const u = users.get(b.email);
      if (!u || u.password !== b.password) return err(res, 400, "invalid_credentials", "Invalid login credentials");
      if (!u.confirmed) return err(res, 400, "email_not_confirmed", "Email not confirmed");
      return send(res, 200, issue(u));
    }
    if (gt === "refresh_token") {
      const id = sessions.get(b.refresh_token); const u = id && [...users.values()].find(x => x.id === id);
      if (!u) return err(res, 400, "refresh_token_not_found", "Invalid Refresh Token"); sessions.delete(b.refresh_token); return send(res, 200, issue(u));
    }
  }
  if (route === "logout") return send(res, 204);
  return send(res, 404, {});
});
server.listen(port, "127.0.0.1", () => console.log("mock supabase on", port));
