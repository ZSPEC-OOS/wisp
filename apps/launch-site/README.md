# WISP Launch Site (Vercel-ready)

This folder contains a polished, accessible launch site framework for WISP.

## Included framework capabilities

- Accessible and responsive launch page with clear product sections.
- Login and registration UI framework.
- Admin login framework account:
  - Email: `admin@wisp.local`
  - Password: `wispadmin`
- Plan-based usage credits (Starter/Pro/Scale).
- Per-user credit deduction simulation.
- Unlimited admin usage mode.
- Firestore configuration placeholders for secure setup.
- Security headers via `vercel.json`.

## Local preview

```bash
cd apps/launch-site
python -m http.server 4173
```

Open `http://localhost:4173`.

## Deploy to Vercel

1. Import this repository in Vercel.
2. Set the project root directory to `apps/launch-site`.
3. Add Firebase environment values (when wiring production auth).
4. Deploy.

## Important production notes

This is a framework/starter implementation. For production security:

- Never keep admin credentials in client code.
- Use Firebase Authentication and custom claims for admin role.
- Hash and salt API keys server-side.
- Enforce usage limits server-side (not only in the browser).
- Add Stripe/webhook billing synchronization for real credits.
