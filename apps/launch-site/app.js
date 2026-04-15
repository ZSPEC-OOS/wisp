const PLAN_LIBRARY = {
  starter: { label: 'Starter', credits: 500 },
  pro: { label: 'Pro', credits: 15000 },
  scale: { label: 'Scale', credits: 75000 },
};

const ADMIN = {
  email: 'admin@wisp.local',
  password: 'wispadmin',
};

const state = {
  user: null,
  role: 'guest',
  plan: 'starter',
  creditsRemaining: 0,
};

const el = {
  authStatus: document.getElementById('auth-status'),
  roleStatus: document.getElementById('role-status'),
  planStatus: document.getElementById('plan-status'),
  creditsStatus: document.getElementById('credits-status'),
  authMsg: document.getElementById('auth-message'),
  usageMsg: document.getElementById('usage-message'),
  loginForm: document.getElementById('login-form'),
  registerBtn: document.getElementById('register-btn'),
  logoutBtn: document.getElementById('logout-btn'),
  usageForm: document.getElementById('usage-form'),
  usageAmount: document.getElementById('usage-amount'),
  year: document.getElementById('year'),
  apiPlaceholder: document.getElementById('api-placeholder'),
};

const persistState = () => {
  localStorage.setItem('wisp-launch-state', JSON.stringify(state));
};

const loadState = () => {
  try {
    const raw = localStorage.getItem('wisp-launch-state');
    if (!raw) return;
    const parsed = JSON.parse(raw);
    Object.assign(state, parsed);
  } catch {
    localStorage.removeItem('wisp-launch-state');
  }
};

const render = () => {
  const plan = PLAN_LIBRARY[state.plan] ?? PLAN_LIBRARY.starter;
  const maxCredits = state.role === 'admin' ? 'Unlimited' : plan.credits;

  el.authStatus.textContent = state.user ? `Signed in as ${state.user.email}` : 'Not signed in';
  el.roleStatus.textContent = state.role[0].toUpperCase() + state.role.slice(1);
  el.planStatus.textContent = plan.label;
  el.creditsStatus.textContent =
    state.role === 'admin' ? 'Unlimited / Unlimited' : `${state.creditsRemaining} / ${maxCredits}`;

  const payload = {
    firebase: {
      apiKey: 'process.env.NEXT_PUBLIC_FIREBASE_API_KEY',
      authDomain: 'process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN',
      projectId: 'process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID',
    },
    firestoreCollections: ['users', 'subscriptions', 'apiKeys', 'usageEvents'],
    authFramework: ['email/password', 'admin role claim', 'session expiry policy'],
    apiSecurity: ['key hashing', 'per-key scope', 'usage throttles'],
  };

  el.apiPlaceholder.textContent = JSON.stringify(payload, null, 2);
  persistState();
};

const login = ({ email, password }) => {
  if (email === ADMIN.email && password === ADMIN.password) {
    state.user = { email, uid: 'admin' };
    state.role = 'admin';
    state.creditsRemaining = Number.MAX_SAFE_INTEGER;
    return { ok: true, message: 'Admin login successful. Unlimited usage is enabled.' };
  }

  if (!email || password.length < 6) {
    return { ok: false, message: 'Use a valid email and password (minimum 6 characters).' };
  }

  const plan = PLAN_LIBRARY[state.plan] ?? PLAN_LIBRARY.starter;
  state.user = { email, uid: crypto.randomUUID() };
  state.role = 'user';
  state.creditsRemaining = plan.credits;
  return { ok: true, message: `Welcome ${email}. ${plan.credits} credits allocated.` };
};

const register = () => {
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const res = login({ email, password });
  el.authMsg.textContent = res.ok
    ? `${res.message} (Framework mode: replace this stub with Firebase createUserWithEmailAndPassword.)`
    : res.message;
  render();
};

const consumeCredits = (amount) => {
  if (!state.user) {
    return { ok: false, message: 'Please sign in before consuming credits.' };
  }

  if (state.role === 'admin') {
    return { ok: true, message: `Admin usage approved for ${amount} credits. No deduction applied.` };
  }

  if (amount > state.creditsRemaining) {
    return {
      ok: false,
      message: `Credit limit reached. Requested ${amount}, but only ${state.creditsRemaining} remain.`,
    };
  }

  state.creditsRemaining -= amount;
  return {
    ok: true,
    message: `${amount} credits consumed. ${state.creditsRemaining} credits remain in this billing cycle.`,
  };
};

const selectPlan = (planName) => {
  if (!PLAN_LIBRARY[planName]) return;
  state.plan = planName;

  if (state.role !== 'admin' && state.user) {
    state.creditsRemaining = PLAN_LIBRARY[planName].credits;
  }

  render();
};

const bootstrap = () => {
  loadState();
  el.year.textContent = new Date().getFullYear().toString();

  document.querySelectorAll('.plan-btn').forEach((button) => {
    button.addEventListener('click', () => {
      selectPlan(button.dataset.plan);
      el.usageMsg.textContent = `${PLAN_LIBRARY[button.dataset.plan].label} plan selected.`;
    });
  });

  el.loginForm.addEventListener('submit', (event) => {
    event.preventDefault();

    const form = new FormData(event.currentTarget);
    const result = login({
      email: String(form.get('email') || '').trim(),
      password: String(form.get('password') || ''),
    });

    el.authMsg.textContent = result.message;
    render();
  });

  el.registerBtn.addEventListener('click', register);

  el.logoutBtn.addEventListener('click', () => {
    state.user = null;
    state.role = 'guest';
    state.creditsRemaining = 0;
    el.authMsg.textContent = 'Signed out.';
    render();
  });

  el.usageForm.addEventListener('submit', (event) => {
    event.preventDefault();

    const amount = Number(el.usageAmount.value);
    if (!Number.isFinite(amount) || amount < 1) {
      el.usageMsg.textContent = 'Enter a valid credit amount above 0.';
      return;
    }

    const result = consumeCredits(amount);
    el.usageMsg.textContent = result.message;
    render();
  });

  render();
};

bootstrap();
