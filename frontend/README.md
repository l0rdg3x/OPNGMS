# OPNGMS Frontend

Console web React per OPNGMS (Fase 1 · Milestone D): login a sessione, app shell con tenant
switcher, e gestione device (lista, dettaglio, onboarding, test-connection, elimina).

## Stack
Vite + React + TypeScript, Mantine (UI), React Router, TanStack Query, client API tipizzato
(openapi-fetch) generato da OpenAPI, test Vitest + Testing Library + MSW.

## Sviluppo
1. `npm install`
2. Genera il client API dal backend (richiede il venv del backend): `npm run gen:api`
3. Avvia il backend su :8000 (`cd ../backend && .venv/bin/uvicorn app.main:app`)
4. `npm run dev` → http://localhost:5173 (il dev server fa da proxy `/api` → :8000)

L'auth è a cookie httpOnly: il frontend deriva lo stato da `GET /api/me` e invia il cookie con
`credentials: 'include'`; l'header CSRF `X-OPNGMS-CSRF` è aggiunto automaticamente sulle mutazioni.

## Test e build
- Test: `npm run test`
- Build di produzione: `npm run build` (type-check + bundle in `dist/`)

## Rigenerare il client API
Dopo modifiche all'API backend: `npm run gen:api` (riesporta `openapi.json` + `src/api/schema.d.ts`).
Se i tipi divergono dal backend, è perché va rigenerato.

## Scope (Milestone D)
Focalizzato: login + shell + gestione device. L'UI org-admin (tenant/utenti/membership) e le UI di
edit/rotate-secret sono follow-up.
