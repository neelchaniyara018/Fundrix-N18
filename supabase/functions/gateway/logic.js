// Pure validation helpers (no I/O) – shared by the gateway and unit-tested with `node --test`.

/** Indian mobile numbers only. Returns "+91XXXXXXXXXX" or null. */
export function normalizePhone(raw) {
  if (typeof raw !== "string") return null;
  let d = raw.replace(/[\s\-().]/g, "");
  if (d.startsWith("+91")) d = d.slice(3);
  else if (d.startsWith("0091")) d = d.slice(4);
  else if (d.startsWith("91") && d.length === 12) d = d.slice(2);
  else if (d.startsWith("0") && d.length === 11) d = d.slice(1);
  return /^[6-9]\d{9}$/.test(d) ? "+91" + d : null;
}

export function normalizeEmail(raw) {
  if (typeof raw !== "string") return null;
  const e = raw.trim().toLowerCase();
  return e.length <= 254 && /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/.test(e) ? e : null;
}

/** 8-72 chars (72 = bcrypt byte limit), at least one letter and one digit. Returns error string or null. */
export function passwordProblem(p) {
  if (typeof p !== "string") return "Password is required.";
  if (p.length < 8) return "Use at least 8 characters.";
  if (new TextEncoder().encode(p).length > 72) return "Use at most 72 characters.";
  if (!/[A-Za-z]/.test(p) || !/\d/.test(p)) return "Include at least one letter and one number.";
  return null;
}

/** Login identifier: contains "@" => email, otherwise phone. */
export function parseIdentifier(raw) {
  if (typeof raw !== "string" || !raw.trim()) return null;
  if (raw.includes("@")) { const email = normalizeEmail(raw); return email ? { kind: "email", email } : null; }
  const phone = normalizePhone(raw);
  return phone ? { kind: "phone", phone } : null;
}
