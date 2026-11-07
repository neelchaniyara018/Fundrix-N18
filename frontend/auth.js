'use strict';
/* Accounts: sign up (phone + email + password, 6-digit code by email), log in with email OR phone + password.
   Talks to (a) our Supabase Edge Function "gateway" for signup/login and (b) Supabase Auth for OTP verify/resend/refresh.
   Loaded after app.js and uses its helpers ($, esc, toast). */
const Auth = (() => {
  const CFG = window.FUNDRIX_CONFIG || {};
  const enabled = !!(CFG.supabaseUrl && CFG.anonKey);
  const KEY = 'fx_session', PEND = 'fx_pending';
  let session = null, tsToken = null;
  try { session = JSON.parse(localStorage.getItem(KEY)); } catch { session = null; }

  /* ----- validation (mirrors supabase/functions/gateway/logic.js; the server re-checks everything) */
  function normPhone(raw) {
    let d = String(raw || '').replace(/[\s\-().]/g, '');
    if (d.startsWith('+91')) d = d.slice(3); else if (d.startsWith('0091')) d = d.slice(4);
    else if (d.startsWith('91') && d.length === 12) d = d.slice(2); else if (d.startsWith('0') && d.length === 11) d = d.slice(1);
    return /^[6-9]\d{9}$/.test(d) ? '+91' + d : null;
  }
  const normEmail = raw => { const e = String(raw || '').trim().toLowerCase(); return e.length <= 254 && /^[^\s@]{1,64}@[^\s@]+\.[^\s@]{2,}$/.test(e) ? e : null; };
  function pwProblem(p) {
    if (!p || p.length < 8) return 'Use at least 8 characters.';
    if (new TextEncoder().encode(p).length > 72) return 'Use at most 72 characters.';
    if (!/[A-Za-z]/.test(p) || !/\d/.test(p)) return 'Include at least one letter and one number.';
    return '';
  }

  /* ----- session storage */
  function setSession(s) {
    const before = session && session.user && session.user.id;
    session = s ? { access_token: s.access_token, refresh_token: s.refresh_token, expires_at: s.expires_at || Math.floor(Date.now() / 1000) + (s.expires_in || 3600),
      user: { id: s.user?.id, email: s.user?.email, phone: s.user?.user_metadata?.phone || null } } : null;
    try { session ? localStorage.setItem(KEY, JSON.stringify(session)) : localStorage.removeItem(KEY); } catch { /* private mode */ }
    renderNav();
    const after = session && session.user && session.user.id;
    if (before !== after) window.dispatchEvent(new Event('fx-auth'));            // login or logout (not a silent token refresh)
  }
  const user = () => session && session.user;
  const pending = () => { try { return JSON.parse(sessionStorage.getItem(PEND)); } catch { return null; } };
  const setPending = v => { try { v ? sessionStorage.setItem(PEND, JSON.stringify(v)) : sessionStorage.removeItem(PEND); } catch { /* ignore */ } };

  /* ----- network */
  async function post(url, body, headers = {}) {
    const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), 15000);
    try {
      const r = await fetch(CFG.supabaseUrl + url, { method: 'POST', signal: ctl.signal, headers: { 'Content-Type': 'application/json', apikey: CFG.anonKey, ...headers }, body: JSON.stringify(body) });
      let data = {}; try { data = await r.json(); } catch { /* empty */ }
      return { ok: r.ok, status: r.status, data };
    } catch { return { ok: false, status: 0, data: { error: 'network' } }; } finally { clearTimeout(t); }
  }
  const gateway = body => post('/functions/v1/gateway', body);

  /** Valid access token (refreshes shortly before expiry); null when logged out. For future per-user API calls. */
  async function token() {
    if (!session) return null;
    if (session.expires_at - 60 > Date.now() / 1000) return session.access_token;
    const r = await post('/auth/v1/token?grant_type=refresh_token', { refresh_token: session.refresh_token });
    if (r.ok) { setSession(r.data); return session.access_token; }
    if (r.status >= 400 && r.status < 500) setSession(null);           // refresh token rejected -> logged out
    return null;
  }
  async function logout() {
    const tk = session && session.access_token; setSession(null);
    if (tk) post('/auth/v1/logout', {}, { Authorization: 'Bearer ' + tk });     // best effort
    toast('Logged out'); location.hash = '#/';
  }

  /* ----- nav */
  function renderNav() {
    const el = $('#navauth'); if (!el) return;
    if (!enabled) { el.innerHTML = ''; return; }
    const u = user();
    el.innerHTML = u ? `<a class="btn sm ghost" href="#/account" title="Your account">👤 ${esc(u.phone ? u.phone.replace(/^(\+91)(\d{2})\d{6}(\d{2})$/, '$1 $2•••••• $3') : u.email)}</a><button class="btn sm" id="logoutBtn">Log out</button>`
      : `<a class="btn sm" href="#/login">Log in</a><a class="btn sm primary" href="#/signup">Sign up</a>`;
  }
  document.addEventListener('click', e => { if (e.target.closest('#logoutBtn')) logout(); });

  /* ----- pages */
  const field = (id, label, extra = '', hint = '') => `<div class="fld"><label for="${id}">${label}</label>${extra}<div class="err" id="${id}-err" role="alert"></div>${hint ? `<div class="muted hint">${hint}</div>` : ''}</div>`;
  const pwInput = (id, ac) => `<div class="pw"><input class="field" id="${id}" type="password" autocomplete="${ac}" maxlength="72" aria-describedby="${id}-err"><button type="button" class="link" data-show="${id}" aria-label="Show password">Show</button></div>`;
  const setErr = (id, msg) => { const e = $('#' + id + '-err'); if (e) e.textContent = msg || ''; const i = $('#' + id); if (i) i.setAttribute('aria-invalid', msg ? 'true' : 'false'); };
  const busy = (btn, on, label) => { btn.disabled = on; btn.textContent = on ? 'Please wait…' : label; };
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-show]'); if (!b) return; const i = $('#' + b.dataset.show); const show = i.type === 'password';
    i.type = show ? 'text' : 'password'; b.textContent = show ? 'Hide' : 'Show'; b.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });
  const shell = inner => {
    $('#app').innerHTML = `<div class="auth-grid">
      <aside class="auth-hero" aria-hidden="true"><svg viewBox="0 0 32 32" class="hero-mark"><circle cx="16" cy="16" r="14" fill="currentColor" opacity=".18"/><path d="M16 16V2a14 14 0 0 1 12 7z" fill="var(--gold)"/></svg>
        <h2>Find the right mutual fund, faster.</h2>
        <ul><li>Screen funds with 50+ filters</li><li>Build your own custom filters</li><li>Compare up to 4 funds side by side</li><li>Export results to CSV</li></ul>
        <p class="hero-note">Fundrix N18 · mutual fund screener</p></aside>
      <div class="panel auth-card">${inner}</div></div>`;
  };

  function privacyPage() {
    shell(`<h1>Privacy notice</h1><p class="muted" style="font-size:12px">Template text: have it reviewed (for example against India's DPDP Act) before you launch.</p>
      <p><b>What we store:</b> your email address, your mobile number, and a hashed password (we never see or store the plain password).</p>
      <p><b>Why:</b> to create and secure your account and let you log in with email or phone. We do not sell your data and do not send marketing messages.</p>
      <p><b>Your phone number</b> is not verified by SMS. It is used only as a login identifier.</p>
      <p><b>Your rights:</b> you can ask us to correct or delete your account data by contacting the site owner.</p><a class="btn" href="javascript:history.back()">Back</a>`);
  }

  function page(r) {
    if (r === 'privacy') return privacyPage();
    if (!enabled) return shell('<h1>Accounts are not enabled</h1><p class="muted">This site has no Supabase settings yet. Fill in <code>frontend/config.js</code> to turn on sign-up and login.</p><a class="btn" href="#/">Back to screener</a>');
    if (user() && ['login', 'signup', 'verify', 'forgot'].includes(r)) { location.hash = '#/'; return; }
    if (r === 'login') return loginPage();
    if (r === 'signup') return signupPage();
    if (r === 'verify') return verifyPage();
    if (r === 'forgot') return forgotPage();
    if (r === 'account') return user() ? accountPage() : (location.hash = '#/login');
  }

  function loginPage() {
    const note = pending() && pending().note;
    shell(`<h1>Log in</h1>${note ? `<p class="ok" role="status">${esc(note)}</p>` : ''}
      <form id="fLogin" novalidate>
        ${field('lid', 'Email or phone number', '<input class="field" id="lid" autocomplete="username" autocapitalize="none" spellcheck="false" aria-describedby="lid-err">')}
        ${field('lpw', 'Password', pwInput('lpw', 'current-password'))}
        <div class="forgot"><a href="#/forgot">Forgot password?</a></div>
        <div class="err" id="lform-err" role="alert"></div>
        <button class="btn primary wide" type="submit" id="lbtn">Log in</button>
      </form>
      <p class="muted">New to Fundrix N18? <a href="#/signup"><b>Create an account</b></a></p>`);
    $('#lid').focus();
    $('#fLogin').onsubmit = async ev => {
      ev.preventDefault(); const id = $('#lid').value.trim(), pw = $('#lpw').value, btn = $('#lbtn'); setErr('lform', '');
      setErr('lid', id ? '' : 'Enter your email or phone number.'); setErr('lpw', pw ? '' : 'Enter your password.'); if (!id || !pw) return;
      busy(btn, true); const r = await gateway({ action: 'login', identifier: id, password: pw }); busy(btn, false, 'Log in');
      if (r.ok && r.data.session) { setSession(r.data.session); setPending(null); toast('Welcome back'); location.hash = '#/'; return; }
      const er = r.data.error;
      if (er === 'email_not_confirmed') { setPending({ email: r.data.email, note: 'Please verify your email first. We sent you a code.' }); post('/auth/v1/resend', { type: 'signup', email: r.data.email }); location.hash = '#/verify'; return; }
      setErr('lform', er === 'invalid_credentials' ? 'Wrong email/phone or password. If you have not verified your email yet, log in with your email address to get a new code.'
        : er === 'rate_limited' ? 'Too many attempts. Please wait a few minutes and try again.' : er === 'network' ? 'Cannot reach the server. Check your connection.' : 'Something went wrong. Please try again.');
    };
  }

  function signupPage() {
    shell(`<h1>Create your account</h1>
      <form id="fSign" novalidate>
        ${field('sph', 'Mobile number', '<div class="phone"><span>+91</span><input class="field" id="sph" inputmode="numeric" autocomplete="tel-national" maxlength="16" aria-describedby="sph-err"></div>', 'Indian mobile numbers only. Used to log in; it is not verified by SMS yet.')}
        ${field('sem', 'Email', '<input class="field" id="sem" type="email" autocomplete="email" autocapitalize="none" spellcheck="false" aria-describedby="sem-err">', 'We email you a 6-digit code to verify it.')}
        ${field('spw', 'Password', pwInput('spw', 'new-password'), 'At least 8 characters, with a letter and a number.')}
        ${field('spw2', 'Confirm password', pwInput('spw2', 'new-password'))}
        <div id="ts"></div>
        <label class="consent"><input type="checkbox" id="sconsent"><span>I agree to the <a href="#/privacy">privacy notice</a> and consent to my phone number and email being stored to run my account.</span></label>
        <div class="err" id="sconsent-err" role="alert"></div><div class="err" id="sform-err" role="alert"></div>
        <button class="btn primary wide" type="submit" id="sbtn">Create account</button>
      </form>
      <p class="muted">Already have an account? <a href="#/login"><b>Log in</b></a></p>`);
    $('#sph').focus(); tsToken = null;
    if (CFG.turnstileSiteKey) loadTurnstile();
    $('#fSign').onsubmit = async ev => {
      ev.preventDefault(); setErr('sform', '');
      const ph = normPhone($('#sph').value), em = normEmail($('#sem').value), pw = $('#spw').value, pw2 = $('#spw2').value, ok = $('#sconsent').checked;
      setErr('sph', ph ? '' : 'Enter a valid 10-digit Indian mobile number.'); setErr('sem', em ? '' : 'Enter a valid email address.');
      setErr('spw', pwProblem(pw)); setErr('spw2', pw === pw2 ? '' : 'Passwords do not match.'); setErr('sconsent', ok ? '' : 'Please tick the box to continue.');
      if (!ph || !em || pwProblem(pw) || pw !== pw2 || !ok) return;
      if (CFG.turnstileSiteKey && !tsToken) return setErr('sform', 'Please complete the security check.');
      const btn = $('#sbtn'); busy(btn, true); const r = await gateway({ action: 'signup', phone: ph, email: em, password: pw, captcha: tsToken }); busy(btn, false, 'Create account');
      if (r.ok) { setPending({ email: em, note: '' }); location.hash = '#/verify'; return; }
      const er = r.data.error;
      if (er === 'validation') { const f = r.data.fields || {}; ['phone:sph', 'email:sem', 'password:spw'].forEach(p => { const [k, id] = p.split(':'); if (f[k]) setErr(id, f[k]); }); }
      else if (er === 'phone_taken') setErr('sph', 'This phone number is already registered. Try logging in instead.');
      else if (er === 'captcha_failed') { setErr('sform', 'Security check failed. Please try again.'); if (window.turnstile) window.turnstile.reset(); tsToken = null; }
      else if (er === 'rate_limited') setErr('sform', 'Too many sign-up emails right now. Please wait a few minutes.');
      else setErr('sform', er === 'network' ? 'Cannot reach the server. Check your connection.' : 'Could not create the account. Please try again.');
    };
  }
  function loadTurnstile() {
    const go = () => window.turnstile.render('#ts', { sitekey: CFG.turnstileSiteKey, callback: t => { tsToken = t; }, 'expired-callback': () => { tsToken = null; } });
    if (window.turnstile) return go();
    const s = document.createElement('script'); s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; s.async = true; s.onload = go; document.head.appendChild(s);
  }

  const RESET = 'fx_reset';
  const resetState = () => { try { return JSON.parse(sessionStorage.getItem(RESET)); } catch { return null; } };
  const setResetState = v => { try { v ? sessionStorage.setItem(RESET, JSON.stringify(v)) : sessionStorage.removeItem(RESET); } catch { /* ignore */ } };
  const tsReset = () => { tsToken = null; if (window.turnstile) try { window.turnstile.reset(); } catch { /* widget not rendered */ } };

  /** Forgot password: step 1 asks for email/phone, step 2 takes the emailed code + new password. */
  function forgotPage() {
    const st = resetState();
    if (!st) {
      shell(`<h1>Forgot your password?</h1><p class="muted">Enter the email or mobile number you signed up with. We will email a 6-digit code to the account's email address.</p>
        <form id="fForgot" novalidate>
          ${field('fid', 'Email or phone number', '<input class="field" id="fid" autocomplete="username" autocapitalize="none" spellcheck="false" aria-describedby="fid-err">')}
          <div id="ts"></div><div class="err" id="fform-err" role="alert"></div>
          <button class="btn primary wide" type="submit" id="fbtn">Send code</button>
        </form><p class="muted"><a href="#/login">← Back to log in</a></p>`);
      $('#fid').focus(); tsToken = null; if (CFG.turnstileSiteKey) loadTurnstile();
      $('#fForgot').onsubmit = async ev => {
        ev.preventDefault(); setErr('fform', ''); const id = $('#fid').value.trim();
        if (!id) return setErr('fid', 'Enter your email or phone number.'); setErr('fid', '');
        if (CFG.turnstileSiteKey && !tsToken) return setErr('fform', 'Please complete the security check.');
        const btn = $('#fbtn'); busy(btn, true); const r = await gateway({ action: 'forgot', identifier: id, captcha: tsToken }); busy(btn, false, 'Send code'); tsReset();
        if (r.ok) { setResetState({ identifier: id }); forgotPage(); return; }
        const er = r.data.error;
        if (er === 'validation') setErr('fid', (r.data.fields || {}).identifier || 'Check what you entered.');
        else if (er === 'captcha_failed') setErr('fform', 'Security check failed. Please try again.');
        else if (er === 'rate_limited') setErr('fform', 'Too many requests. Please wait a few minutes and try again.');
        else setErr('fform', er === 'network' ? 'Cannot reach the server. Check your connection.' : 'Something went wrong. Please try again.');
      };
      return;
    }
    shell(`<h1>Set a new password</h1><p class="ok" role="status">If an account exists for <b>${esc(st.identifier)}</b>, we have emailed it a 6-digit code. It can take a minute; check spam too.</p>
      <form id="fReset" novalidate>
        ${field('rcode', '6-digit code', '<input class="field code" id="rcode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]*" aria-describedby="rcode-err">')}
        ${field('rpw', 'New password', pwInput('rpw', 'new-password'), 'At least 8 characters, with a letter and a number.')}
        ${field('rpw2', 'Confirm new password', pwInput('rpw2', 'new-password'))}
        <div id="ts"></div><div class="err" id="rform-err" role="alert"></div>
        <button class="btn primary wide" type="submit" id="rbtn">Update password</button>
      </form>
      <p class="muted">No code? <button class="link" id="rresend" type="button">Resend code</button> · <a href="#/forgot" id="rother">Use a different account</a></p>`);
    $('#rcode').focus(); tsToken = null; if (CFG.turnstileSiteKey) loadTurnstile();
    $('#rcode').oninput = e => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6); };
    $('#rother').onclick = ev => { ev.preventDefault(); setResetState(null); forgotPage(); };    // same hash, so re-render by hand
    $('#fReset').onsubmit = async ev => {
      ev.preventDefault(); setErr('rform', ''); const code = $('#rcode').value, pw = $('#rpw').value, pw2 = $('#rpw2').value;
      setErr('rcode', /^\d{6}$/.test(code) ? '' : 'Enter the 6-digit code.'); setErr('rpw', pwProblem(pw)); setErr('rpw2', pw === pw2 ? '' : 'Passwords do not match.');
      if (!/^\d{6}$/.test(code) || pwProblem(pw) || pw !== pw2) return;
      const btn = $('#rbtn'); busy(btn, true); const r = await gateway({ action: 'reset', identifier: st.identifier, code, password: pw }); busy(btn, false, 'Update password');
      if (r.ok && r.data.session) { setResetState(null); setPending(null); setSession(r.data.session); toast('Password updated. You are logged in.'); location.hash = '#/'; return; }
      const er = r.data.error;
      if (er === 'invalid_code') setErr('rcode', 'That code is wrong or has expired. Check the latest email or resend a code.');
      else if (er === 'validation') { const f = r.data.fields || {}; if (f.code) setErr('rcode', f.code); if (f.password) setErr('rpw', f.password); if (f.identifier) setErr('rform', f.identifier); }
      else if (er === 'rate_limited') setErr('rform', 'Too many attempts. Please wait a few minutes and try again.');
      else setErr('rform', er === 'network' ? 'Cannot reach the server. Check your connection.' : 'Something went wrong. Please try again.');
    };
    const rb = $('#rresend'); let left = 0, timer = null;
    const cool = sec => { clearInterval(timer); left = sec; rb.disabled = true; const tick = () => { rb.textContent = left > 0 ? `Resend in ${left}s` : 'Resend code'; if (left-- <= 0) { rb.disabled = false; clearInterval(timer); } }; tick(); timer = setInterval(tick, 1000); };
    rb.onclick = async () => {
      setErr('rform', ''); if (CFG.turnstileSiteKey && !tsToken) return setErr('rform', 'Please complete the security check, then resend.');
      const r = await gateway({ action: 'forgot', identifier: st.identifier, captcha: tsToken }); tsReset();
      if (r.ok) { toast('If the account exists, a new code is on its way'); cool(30); }
      else if (r.data.error === 'rate_limited') { setErr('rform', 'Please wait a little before requesting another code.'); cool(30); }
      else setErr('rform', 'Could not send a new code. Please try again.');
    };
    cool(30);
  }

  let cooldownTimer = null;
  function verifyPage() {
    const p = pending(), email = p && p.email;
    shell(`<h1>Verify your email</h1>${p && p.note ? `<p class="ok" role="status">${esc(p.note)}</p>` : ''}
      <p>${email ? `We sent a 6-digit code to <b>${esc(email)}</b>. It can take a minute; check spam too.` : 'Enter the email you signed up with and the 6-digit code we sent.'}</p>
      <form id="fVer" novalidate>
        ${email ? '' : field('vem', 'Email', '<input class="field" id="vem" type="email" autocomplete="email" aria-describedby="vem-err">')}
        ${field('vcode', '6-digit code', '<input class="field code" id="vcode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]*" aria-describedby="vcode-err">')}
        <div class="err" id="vform-err" role="alert"></div>
        <button class="btn primary wide" type="submit" id="vbtn">Verify and continue</button>
      </form>
      <p class="muted">Did not get it? <button class="link" id="resend" type="button">Resend code</button> · <a href="#/signup">Use a different email</a></p>`);
    (email ? $('#vcode') : $('#vem')).focus();
    $('#vcode').oninput = e => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 6); };
    const getEmail = () => email || normEmail($('#vem') && $('#vem').value);
    $('#fVer').onsubmit = async ev => {
      ev.preventDefault(); setErr('vform', ''); const em = getEmail(), code = $('#vcode').value;
      if (!email) setErr('vem', em ? '' : 'Enter a valid email address.');
      setErr('vcode', /^\d{6}$/.test(code) ? '' : 'Enter the 6-digit code.'); if (!em || !/^\d{6}$/.test(code)) return;
      const btn = $('#vbtn'); busy(btn, true); const r = await post('/auth/v1/verify', { type: 'signup', email: em, token: code }); busy(btn, false, 'Verify and continue');
      if (r.ok && r.data.access_token) { setSession(r.data); setPending(null); toast('Email verified. You are logged in.'); location.hash = '#/'; return; }
      setErr('vform', r.status === 0 ? 'Cannot reach the server. Check your connection.' : r.status === 429 ? 'Too many attempts. Please wait and try again.' : 'That code is wrong or has expired. Check the latest email or resend a new code.');
    };
    const rb = $('#resend'); const startCooldown = s => { clearInterval(cooldownTimer); let left = s; rb.disabled = true; const tick = () => { rb.textContent = left > 0 ? `Resend in ${left}s` : 'Resend code'; if (left-- <= 0) { rb.disabled = false; clearInterval(cooldownTimer); } }; tick(); cooldownTimer = setInterval(tick, 1000); };
    rb.onclick = async () => {
      const em = getEmail(); if (!em) return setErr('vem', 'Enter a valid email address.'); setErr('vform', '');
      const r = await post('/auth/v1/resend', { type: 'signup', email: em });
      if (r.status === 429) { setErr('vform', 'Please wait a little before requesting another code.'); startCooldown(30); return; }
      if (!r.ok) return setErr('vform', 'Could not send a new code. Please try again.');
      toast('New code sent'); startCooldown(30);
    };
    startCooldown(email ? 30 : 0);
  }

  function accountPage() {
    const u = user();
    shell(`<h1>Your account</h1><div class="kv"><div>Email</div><div>${esc(u.email)}</div><div>Mobile</div><div>${u.phone ? esc(u.phone) : '<span class="muted">not set</span>'}</div></div>
      <p class="muted" style="font-size:12px">Your email is verified. Your phone number is stored for login but is not verified by SMS.</p>
      <button class="btn" id="logoutBtn">Log out</button>`);
  }

  // keep the session fresh at start-up; a rejected refresh token logs the user out
  if (enabled && session) token();
  renderNav();
  return { enabled, page, token, user, logout, _norm: { normPhone, normEmail, pwProblem } };
})();
