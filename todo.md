# RBAC & Users — Delivery Tracker

Plan: `docs/superpowers/plans/2026-06-29-rbac-users.md`
Spec: `docs/superpowers/specs/2026-06-29-rbac-users-design.md`

Workflow per task: GitHub issue → develop → test → push `feature/<slug>` → open PR → **user reviews & merges** → mark done here.

| # | Task | Issue | PR | Merged |
|---|------|-------|----|:------:|
| 1 | RBAC permission matrix (`zta/rbac.py` + `roles.yaml`) | #28 | #29 | ✅ |
| 2 | User store (`zta/users.py`) | #30 | #31 | ✅ |
| — | Chinook DB swap (`examples/seed_db.py`) | #32 | #33 | ✅ |
| — | Domain roles + table scoping (manager/sales/catalog) | #34 | #35 | ✅ |
| — | argon2id password hashing (`argon2-cffi`) | #38 | #39 | ✅ |
| 3 | Signed session cookie (`zta/webauth.py`) | #36 | #37 | ✅ |
| 4 | Audit user attribution (`zta/audit.py`) | #40 | #41 | ✅ |
| 5 | Runtime RBAC enforcement (`zta/runtime.py`) | #42 | #43 | ✅ |
| 6 | Auth & route guards (`app.py`, login, seed) | #44 | #45 | ✅ |
| 7 | Table-scoped `db_query` (SQLite authorizer) | #46 | — | ☐ |
| 8 | Admin pages `/users` & `/roles` | — | — | ☐ |
| 9 | Documentation & config | — | — | ☐ |

_Tick "Merged" only after the user merges. Seed default accounts: manager/sales/catalog._
