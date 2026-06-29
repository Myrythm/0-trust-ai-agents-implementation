# RBAC & Users — Delivery Tracker

Plan: `docs/superpowers/plans/2026-06-29-rbac-users.md`
Spec: `docs/superpowers/specs/2026-06-29-rbac-users-design.md`

Workflow per task: GitHub issue → develop → test → push `feature/<slug>` → open PR → **user reviews & merges** → mark done here.

| # | Task | Branch | Issue | PR | Merged |
|---|------|--------|-------|----|:------:|
| 1 | RBAC permission matrix (`zta/rbac.py` + `roles.yaml`) | `feature/rbac-matrix` | — | — | ☐ |
| 2 | User store (`zta/users.py`) | `feature/user-store` | — | — | ☐ |
| 3 | Signed session cookie (`zta/webauth.py`) | `feature/webauth-cookie` | — | — | ☐ |
| 4 | Audit user attribution (`zta/audit.py`) | `feature/audit-user` | — | — | ☐ |
| 5 | Runtime RBAC enforcement (`zta/runtime.py`) | `feature/runtime-rbac` | — | — | ☐ |
| 6 | Auth & route guards (`app.py`, login, seed) | `feature/auth-guards` | — | — | ☐ |
| 7 | Admin pages `/users` & `/roles` | `feature/admin-pages` | — | — | ☐ |
| 8 | Documentation & config | `feature/rbac-docs` | — | — | ☐ |

_Fill in Issue/PR links as each is opened. Tick "Merged" only after the user merges._
