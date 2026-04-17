'use strict';

const state = {
  authed:   false,
  apiKey:   '',
  darkMode: true,
};

// ── DOM refs ──────────────────────────────────────────────
const $ = id => document.getElementById(id);

const el = {
  themeToggle:    $('theme-toggle'),
  statusBar:      $('status-bar'),
  apikeyDisplay:  $('apikey-display'),
  copyKeyBtn:     $('copy-key-btn'),
  logoutBtn:      $('logout-btn'),
  loginForm:      $('login-form'),
  authMsg:        $('auth-message'),
  authGate:       $('auth-gate'),
  authDash:       $('auth-dash'),
  apikeyLarge:    $('apikey-large'),
  copyKeyBtnDash: $('copy-key-btn-dash'),
  logoutBtnDash:  $('logout-btn-dash'),
  signInLink:     $('signin-link'),
};

// ── Persistence ───────────────────────────────────────────
const save = () => {
  try {
    localStorage.setItem('wisp-authed', state.authed ? '1' : '');
    // Never persist the key to localStorage — fetch it fresh each session
  } catch {}
};

const load = () => {
  try {
    // Restore theme only; auth state requires a fresh PIN each session for security
    const theme = localStorage.getItem('wisp-theme');
    if (theme !== null) state.darkMode = theme === 'dark';
  } catch {}
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
  document.documentElement.setAttribute('data-theme', state.darkMode ? 'dark' : 'light');

  el.statusBar.classList.toggle('hidden', !state.authed);
  if (state.authed && state.apiKey) {
    el.apikeyDisplay.textContent = state.apiKey;
  }

  el.authGate.classList.toggle('hidden', state.authed);
  el.authDash.classList.toggle('hidden', !state.authed);
  if (state.authed && state.apiKey) {
    el.apikeyLarge.textContent = state.apiKey;
  }

  el.signInLink.textContent = state.authed ? 'Dashboard' : 'Sign in';

  save();
};

// ── Auth ──────────────────────────────────────────────────
const doLogin = async pin => {
  try {
    const res = await fetch('/auth/unlock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin }),
    });

    if (res.status === 429) {
      return { ok: false, message: 'Too many attempts. Try again in 60 seconds.' };
    }
    if (res.status === 503) {
      return { ok: false, message: 'Auth not configured on server.' };
    }
    if (!res.ok) {
      return { ok: false, message: 'Incorrect PIN.' };
    }

    const data = await res.json();
    state.authed = true;
    state.apiKey = data.api_key;
    return { ok: true };
  } catch {
    return { ok: false, message: 'Network error — please try again.' };
  }
};

const doLogout = () => {
  state.authed = false;
  state.apiKey = '';
  el.authMsg.textContent = '';
  render();
};

// ── Bootstrap ─────────────────────────────────────────────
const bootstrap = () => {
  load();

  el.themeToggle.addEventListener('click', () => {
    state.darkMode = !state.darkMode;
    try { localStorage.setItem('wisp-theme', state.darkMode ? 'dark' : 'light'); } catch {}
    render();
  });

  el.loginForm.addEventListener('submit', async e => {
    e.preventDefault();
    const submitBtn = e.currentTarget.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Checking…';

    const fd  = new FormData(e.currentTarget);
    const pin = String(fd.get('pin') ?? '').trim();
    const result = await doLogin(pin);

    submitBtn.disabled = false;
    submitBtn.textContent = 'Unlock';

    if (result.ok) {
      e.currentTarget.reset();
      el.authMsg.textContent = '';
    } else {
      el.authMsg.textContent = result.message;
      el.authMsg.style.color = 'var(--danger)';
    }
    render();
  });

  el.logoutBtn.addEventListener('click', doLogout);
  el.logoutBtnDash.addEventListener('click', doLogout);

  el.copyKeyBtn?.addEventListener('click', () => {
    copyToClipboard(state.apiKey, el.copyKeyBtn);
  });

  el.copyKeyBtnDash?.addEventListener('click', () => {
    copyToClipboard(state.apiKey, el.copyKeyBtnDash);
  });

  render();
};

bootstrap();
