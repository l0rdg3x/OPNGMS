# OPNGMS Frontend

React web console for OPNGMS: session login, app shell with tenant switcher, device management
(list, detail, onboarding, test-connection, delete), and the monitoring dashboard (fleet overview,
per-device health charts, alert list).

## Stack
Vite + React + TypeScript, Mantine (UI) + Mantine Charts, React Router, TanStack Query, typed API
client (openapi-fetch) generated from OpenAPI, Vitest + Testing Library + MSW tests. UI strings live
behind a lightweight i18n layer (`src/i18n/`, English by default, ready for additional languages).

## Development
1. `npm install`
2. Generate the API client from the backend (requires the backend venv): `npm run gen:api`
3. Start the backend on :8000 (`cd ../backend && .venv/bin/uvicorn app.main:app`)
4. `npm run dev` → http://localhost:5173 (the dev server proxies `/api` → :8000)

Auth uses an httpOnly cookie: the frontend derives its state from `GET /api/me` and sends the cookie
with `credentials: 'include'`; the CSRF header `X-OPNGMS-CSRF` is added automatically on mutations.

## Test and build
- Test: `npm run test`
- Production build: `npm run build` (type-check + bundle into `dist/`)
- Lint: `npm run lint`

## Regenerating the API client
After backend API changes: `npm run gen:api` (re-exports `openapi.json` + `src/api/schema.d.ts`).
If the types diverge from the backend, it means a regeneration is due.

## i18n
UI text is keyed in `src/i18n/en.ts` and accessed via the `useT()` hook. To add a language, create a
new dictionary file with the same shape, register it in `src/i18n/index.ts`, and select the locale in
the `I18nProvider`.
