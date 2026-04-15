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
};

// ── Persistence ───────────────────────────────────────────
const save = () => {
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

  // Theme
  document.documentElement.setAttribute('data-theme', state.darkMode ? 'dark' : 'light');

  // Status bar
  el.statusBar.classList.toggle('hidden', !authed);
  if (authed) {
    el.planDisplay.textContent = plan.label;
    el.creditsDisplay.textContent = creditsText;
    el.apikeyDisplay.textContent = state.apiKey ?? '—';
  }

  // Account section: form vs dashboard
  el.authGate.classList.toggle('hidden', authed);
  el.authDash.classList.toggle('hidden', !authed);
  if (authed) {
    el.dashEmail.textContent = state.user.email;
    el.dashPlan.textContent = plan.label;
    el.apikeyLarge.textContent = state.apiKey ?? '—';
    el.dashCredits.textContent = creditsText;
  }

  // Nav sign-in link
  el.signInLink.textContent = authed
    ? state.user.email.split('@')[0]
    : 'Sign in';

  // Plan selection buttons
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
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const result = doLogin(email, password);
    el.authMsg.textContent = result.ok
      ? `Account created — ${result.message}`
      : result.message;
    el.authMsg.style.color = result.ok ? 'var(--success)' : 'var(--danger)';
    if (result.ok) el.loginForm.reset();
    render();
  });

  el.logoutBtn.addEventListener('click', () => {
    Object.assign(state, { user: null, role: 'guest', creditsRemaining: 0, apiKey: null });
    el.authMsg.textContent = 'Signed out.';
    el.authMsg.style.color = 'var(--muted)';
    render();
  });

  el.copyKeyBtn?.addEventListener('click', () => {
    if (state.apiKey) copyToClipboard(state.apiKey, el.copyKeyBtn);
  });

  el.copyKeyBtnDash?.addEventListener('click', () => {
    if (state.apiKey) copyToClipboard(state.apiKey, el.copyKeyBtnDash);
  });

  render();
};

bootstrap();
