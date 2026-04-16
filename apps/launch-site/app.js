'use strict';

const PLANS = {
  starter: { label: 'Starter', credits: 500 },
  pro:     { label: 'Pro',     credits: 15_000 },
  scale:   { label: 'Scale',   credits: 75_000 },
};

const ADMIN = { email: 'admin@wisp.local', password: 'wispadmin' };

const state = {
  user: null,
  role: 'guest',
  plan: 'starter',
  creditsRemaining: 0,
  apiKey: null,
  darkMode: true,
};

// ── DOM refs ──────────────────────────────────────────────
const $ = id => document.getElementById(id);

const el = {
  themeToggle:    $('theme-toggle'),
  statusBar:      $('status-bar'),
  planDisplay:    $('plan-display'),
  creditsDisplay: $('credits-display'),
  apikeyDisplay:  $('apikey-display'),
  copyKeyBtn:     $('copy-key-btn'),
  logoutBtn:      $('logout-btn'),
  loginForm:      $('login-form'),
  registerBtn:    $('register-btn'),
  authMsg:        $('auth-message'),
  authGate:       $('auth-gate'),
  authDash:       $('auth-dash'),
  dashEmail:      $('dash-email'),
  dashPlan:       $('dash-plan'),
  apikeyLarge:    $('apikey-large'),
  copyKeyBtnDash: $('copy-key-btn-dash'),
  dashCredits:    $('dash-credits'),
  signInLink:     $('signin-link'),
  planMsg:        $('plan-msg'),
  planSelects:    document.querySelectorAll('.plan-select'),
  googleBtn:      $('google-btn'),
  authDivider:    $('auth-divider'),
};

// ── Persistence ───────────────────────────────────────────
// Firebase handles its own auth persistence; only save local/admin sessions.
const save = () => {
  if (state.user?._firebase) return;
  try { localStorage.setItem('wisp-state', JSON.stringify(state)); } catch {}
};

const load = () => {
  try {
    const raw = localStorage.getItem('wisp-state');
    if (raw) Object.assign(state, JSON.parse(raw));
  } catch {
    localStorage.removeItem('wisp-state');
  }
};

// ── Clipboard ─────────────────────────────────────────────
const copyToClipboard = async (text, btn) => {
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  } catch {
    btn.textContent = 'Failed';
  }
};

// ── Render ────────────────────────────────────────────────
const render = () => {
  const plan = PLANS[state.plan] ?? PLANS.starter;
  const authed = !!state.user;
  const isAdmin = state.role === 'admin';
  const creditsText = isAdmin
    ? 'Unlimited'
    : `${state.creditsRemaining.toLocaleString()} / ${plan.credits.toLocaleString()}`;

  document.documentElement.setAttribute('data-theme', state.darkMode ? 'dark' : 'light');

  el.statusBar.classList.toggle('hidden', !authed);
  if (authed) {
    el.planDisplay.textContent = plan.label;
    el.creditsDisplay.textContent = creditsText;
    el.apikeyDisplay.textContent = state.apiKey ?? '—';
  }

  el.authGate.classList.toggle('hidden', authed);
  el.authDash.classList.toggle('hidden', !authed);
  if (authed) {
    el.dashEmail.textContent = state.user.displayName || state.user.email;
    el.dashPlan.textContent = plan.label;
    el.apikeyLarge.textContent = state.apiKey ?? '—';
    el.dashCredits.textContent = creditsText;
  }

  const shortName = state.user
    ? (state.user.displayName?.split(' ')[0] ?? state.user.email.split('@')[0])
    : 'Sign in';
  el.signInLink.textContent = authed ? shortName : 'Sign in';

  el.planSelects.forEach(btn => {
    const selected = btn.dataset.plan === state.plan;
    btn.setAttribute('aria-pressed', String(selected));
    btn.textContent = selected
      ? 'Current plan'
      : `Select ${PLANS[btn.dataset.plan]?.label ?? ''}`;
    btn.classList.toggle('btn-primary', selected);
    btn.classList.toggle('btn-outline', !selected);
  });

  save();
};

// ── Firebase / Google Sign-In ─────────────────────────────
let fbAuth     = null;
let fbSignOut  = null;

const GOOGLE_ICON_SVG = `<svg aria-hidden="true" width="18" height="18" viewBox="0 0 18 18" style="flex-shrink:0">
  <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908C18.658 12.008 17.64 10.81 17.64 9.2Z" fill="#4285F4"/>
  <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853"/>
  <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z" fill="#FBBC05"/>
  <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 6.29C4.672 4.163 6.656 3.58 9 3.58Z" fill="#EA4335"/>
</svg> Sign in with Google`;

const initFirebase = async () => {
  try {
    const { firebaseConfig } = await import('./firebase.config.js');
    if (!firebaseConfig?.apiKey || firebaseConfig.apiKey.startsWith('REPLACE_')) return;

    const [{ initializeApp }, { getAuth, GoogleAuthProvider, signInWithPopup, onAuthStateChanged, signOut }] =
      await Promise.all([
        import('https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js'),
        import('https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js'),
      ]);

    const app      = initializeApp(firebaseConfig);
    fbAuth         = getAuth(app);
    fbSignOut      = signOut;
    const provider = new GoogleAuthProvider();

    onAuthStateChanged(fbAuth, fbUser => {
      if (fbUser) {
        const plan  = PLANS[state.plan] ?? PLANS.starter;
        const uid24 = fbUser.uid.replace(/[^a-zA-Z0-9]/g, '').slice(0, 24).padEnd(24, '0');
        Object.assign(state, {
          user:             { email: fbUser.email, displayName: fbUser.displayName, _firebase: true },
          role:             'user',
          creditsRemaining: plan.credits,
          apiKey:           `wsp_${uid24}`,
        });
      } else if (state.user?._firebase) {
        Object.assign(state, { user: null, role: 'guest', creditsRemaining: 0, apiKey: null });
      }
      render();
    });

    // Reveal and wire up the Google button
    el.googleBtn.innerHTML = GOOGLE_ICON_SVG;
    el.googleBtn.disabled  = false;
    el.googleBtn.classList.remove('hidden');
    el.authDivider.classList.remove('hidden');

    el.googleBtn.addEventListener('click', async () => {
      el.googleBtn.disabled = true;
      el.googleBtn.textContent = 'Signing in…';
      try {
        await signInWithPopup(fbAuth, provider);
        el.authMsg.textContent = '';
      } catch (e) {
        el.authMsg.textContent =
          e.code === 'auth/popup-closed-by-user' ? 'Sign-in cancelled.'                        :
          e.code === 'auth/popup-blocked'        ? 'Allow popups for this site and try again.' :
                                                   'Sign-in failed. Please try again.';
        el.authMsg.style.color = 'var(--danger)';
      } finally {
        el.googleBtn.disabled  = false;
        el.googleBtn.innerHTML = GOOGLE_ICON_SVG;
      }
    });
  } catch {
    // firebase.config.js absent or config placeholder — Google Sign-In unavailable
  }
};

// ── Auth ──────────────────────────────────────────────────
const doLogin = (email, password) => {
  if (email === ADMIN.email && password === ADMIN.password) {
    Object.assign(state, {
      user: { email },
      role: 'admin',
      creditsRemaining: 0,
      apiKey: 'wsp_admin_unlimited',
    });
    return { ok: true, message: 'Admin signed in — unlimited credits enabled.' };
  }
  if (!email.includes('@') || password.length < 6) {
    return { ok: false, message: 'Enter a valid email and a password of at least 6 characters.' };
  }
  const plan = PLANS[state.plan] ?? PLANS.starter;
  Object.assign(state, {
    user: { email },
    role: 'user',
    creditsRemaining: plan.credits,
    apiKey: `wsp_${crypto.randomUUID().replace(/-/g, '').slice(0, 24)}`,
  });
  return {
    ok: true,
    message: `Signed in. ${plan.credits.toLocaleString()} credits allocated on the ${plan.label} plan.`,
  };
};

const doLogout = async () => {
  if (fbAuth && fbSignOut && state.user?._firebase) {
    try { await fbSignOut(fbAuth); } catch {}
    // onAuthStateChanged will reset state and re-render
  } else {
    Object.assign(state, { user: null, role: 'guest', creditsRemaining: 0, apiKey: null });
    el.authMsg.textContent = 'Signed out.';
    el.authMsg.style.color = 'var(--muted)';
    render();
  }
};

// ── Plan selection ────────────────────────────────────────
const selectPlan = name => {
  if (!PLANS[name]) return;
  state.plan = name;
  if (state.user && state.role !== 'admin') {
    state.creditsRemaining = PLANS[name].credits;
  }
  el.planMsg.textContent = `${PLANS[name].label} plan selected.`;
  render();
};

// ── Bootstrap ─────────────────────────────────────────────
const bootstrap = () => {
  load();
  initFirebase(); // async, non-blocking

  el.themeToggle.addEventListener('click', () => {
    state.darkMode = !state.darkMode;
    render();
  });

  el.planSelects.forEach(btn => {
    btn.addEventListener('click', () => selectPlan(btn.dataset.plan));
  });

  el.loginForm.addEventListener('submit', e => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const result = doLogin(
      String(fd.get('email') ?? '').trim(),
      String(fd.get('password') ?? ''),
    );
    el.authMsg.textContent = result.message;
    el.authMsg.style.color = result.ok ? 'var(--success)' : 'var(--danger)';
    if (result.ok) e.currentTarget.reset();
    render();
  });

  el.registerBtn.addEventListener('click', () => {
    const email    = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const result   = doLogin(email, password);
    el.authMsg.textContent = result.ok ? `Account created — ${result.message}` : result.message;
    el.authMsg.style.color = result.ok ? 'var(--success)' : 'var(--danger)';
    if (result.ok) el.loginForm.reset();
    render();
  });

  el.logoutBtn.addEventListener('click', doLogout);

  el.copyKeyBtn?.addEventListener('click', () => {
    if (state.apiKey) copyToClipboard(state.apiKey, el.copyKeyBtn);
  });

  el.copyKeyBtnDash?.addEventListener('click', () => {
    if (state.apiKey) copyToClipboard(state.apiKey, el.copyKeyBtnDash);
  });

  render();
};

bootstrap();
