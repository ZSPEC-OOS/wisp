'use strict';

const PIN = '5522';
const API_KEY = 'wsp_admin_unlimited';

const state = {
  authed:   false,
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
  try { localStorage.setItem('wisp-authed', state.authed ? '1' : ''); } catch {}
};

const load = () => {
  try {
    state.authed   = !!localStorage.getItem('wisp-authed');
    const theme    = localStorage.getItem('wisp-theme');
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
  if (state.authed) {
    el.apikeyDisplay.textContent = API_KEY;
  }

  el.authGate.classList.toggle('hidden', state.authed);
  el.authDash.classList.toggle('hidden', !state.authed);
  if (state.authed) {
    el.apikeyLarge.textContent = API_KEY;
  }

  el.signInLink.textContent = state.authed ? 'Dashboard' : 'Sign in';

  save();
};

// ── Auth ──────────────────────────────────────────────────
const doLogin = pin => {
  if (pin === PIN) {
    state.authed = true;
    return { ok: true };
  }
  return { ok: false, message: 'Incorrect PIN.' };
};

const doLogout = () => {
  state.authed = false;
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

  el.loginForm.addEventListener('submit', e => {
    e.preventDefault();
    const fd  = new FormData(e.currentTarget);
    const pin = String(fd.get('pin') ?? '').trim();
    const result = doLogin(pin);
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
    copyToClipboard(API_KEY, el.copyKeyBtn);
  });

  el.copyKeyBtnDash?.addEventListener('click', () => {
    copyToClipboard(API_KEY, el.copyKeyBtnDash);
  });

  render();
};

bootstrap();
