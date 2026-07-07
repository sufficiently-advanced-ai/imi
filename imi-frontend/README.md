# imi-frontend

The Next.js web UI for [imi](../README.md). In a normal deployment you don't run it directly —
the app container serves it behind nginx on port 8080 (see `deployment/supervisord.conf`), and
`./dev-hot.sh` at the repo root gives you a hot-reloading dev stack for both backend and
frontend.

## Standalone development

```bash
npm install
npm run dev        # dev server on :3000 — needs the backend running for /api
npx jest           # tests
npm run build      # production build (server mode — no static export)
```

## Configuration

Three `NEXT_PUBLIC_*` vars, defaulted in `next.config.ts` and read in `lib/config.ts` /
`middleware.ts`:

| Var | Default | Effect |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `/api` | backend API base — change only when running frontend and backend on separate origins |
| `NEXT_PUBLIC_AUTH_MODE` | `none` | must mirror the backend `AUTH_MODE` |
| `NEXT_PUBLIC_BASE_PATH` | `''` | sub-path mounting behind a reverse proxy |

All product terminology, navigation, and entity labels come from the **backend** at runtime
(`GET /api/domain/config`, driven by the active domain schema's `ui:` block) — you do not
rebuild the frontend to rebrand it for a domain. See
[`docs/customization/domain-schemas.md`](../docs/customization/domain-schemas.md).

Gotcha worth knowing: client-side API paths must **not** start with `/api/` in code — the
base path handling adds the prefix, and doubling it (`/api/api/...`) is a recurring trap.
Use the helpers in `lib/api/`.
