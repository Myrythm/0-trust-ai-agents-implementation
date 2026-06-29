# Design: User Roles & RBAC for the ZTA Control Plane

**Date:** 2026-06-29
**Status:** Approved (design); pending implementation plan
**Topic:** Multiple authenticated users with role-based access to the agent

## Context

Today the ZTA control plane has **no concept of a human user**. There is no login; anyone who opens the app can chat with the single hardcoded agent (`analyst-bot`), view the audit log, and view the policy. The only "identity" is the agent's Ed25519 keypair — that is the *agent's* identity, not the *user's*. Policy decisions are keyed by `agent_id`, and the word "role" in the codebase refers only to chat-message roles (`user`/`assistant`/`tool`).

We want to upgrade the project so that **multiple users authenticate, each with a role that grants specific access** — both to the agent's tools and to the app's pages. This makes the Zero Trust story complete: every action is attributable to a *who* (user) in addition to a *what* (tool) and a *which* (agent).

**Goal:** add authentication + role-based access control (RBAC) with three roles (admin / analyst / viewer), an admin-managed user list, and a readable "who-can-do-what" view — without breaking the existing policy engine or the self-contained, dependency-light ethos.

## Decisions (from brainstorming)

- A role controls **both** which tools the agent may run for that user **and** which pages the user can reach.
- Authentication is **username + password stored in SQLite**, with a signed session cookie. No external SSO.
- Three roles: **admin / analyst / viewer**.
- **Admin can manage users in-app** (create / list / delete) via an admin-only page.
- RBAC is expressed as a **separate permission matrix** (Approach B), chosen for governance/visibility — admins must be able to see "who can do what" at a glance — and for clean separation between human RBAC and agent tool-safety policy.

## Revision (2026-06-29): Chinook domain roles + table-level scoping

After swapping the demo data to the **Chinook** media-store database, the generic `admin/analyst/viewer` roles were replaced with **domain-themed, data-scoped** roles. This section is authoritative; where older text below still says admin/analyst/viewer, read it as manager/sales/catalog.

**Roles and scope:**

| Role | Pages | Write | Readable tables | Story |
|---|---|---|---|---|
| **manager** | all | ✅ `db_write` | **all** (incl. `Employee`) | full control + sensitive HR data |
| **sales** | chat, audit | ❌ | catalog + `Customer`, `Invoice`, `InvoiceLine` — **denied `Employee`** | analyze sales, HR off-limits |
| **catalog** | chat | ❌ | catalog only (`Artist, Album, Track, Genre, MediaType, Playlist, PlaylistTrack`) | public catalog; no customer/HR data |

**New dimension — table scope.** `roles.yaml` gains a `tables` key per role: `"*"` (all) or an explicit list. `zta/rbac.py` gains `KNOWN_TABLES`, `Permissions.table_readable(role, table)` and `tables_allowed(role)` (None = all), with load-time validation of table names.

**Enforcement (engine-level, not SQL-parsing).** The `db_query` tool is built **per request**, bound to the current role's readable tables, installing a SQLite `set_authorizer` callback that returns `SQLITE_DENY` for any `SQLITE_READ` on an out-of-scope table. The resulting access error is caught and recorded as a clean **authorization deny** (`rbac: role 'catalog' not permitted to read table 'Employee'`) in the trace + audit — not a generic error. This is a separate task that lands **after** the auth wiring (it needs the logged-in role at request time).

Table scope is part of the RBAC layer (Layer 1), refined from "which tools" to "which tables," enforced by the DB engine at execution. `policy.yaml` (SELECT-only) remains role-agnostic.

## Authorization model: two layers, composed with AND

The central design idea. RBAC and the existing policy engine answer **different questions** and are combined so they can never contradict each other:

| Layer | Question | Source | Owns |
|---|---|---|---|
| **1. RBAC (who)** | "Is this *role* allowed to touch this tool / page at all?" | `roles.yaml` (new matrix) | role → allowed pages + tools |
| **2. Policy (what)** | "Given the role may use it, is this *specific call* safe?" | `policy.yaml` (existing engine, unchanged) | per-tool call-content safety (e.g. `db_query` must be SELECT/WITH) |

**Precedence:** a tool call must pass **both** layers. Layer 1 is checked first; if the role is not permitted, the call is denied and audited immediately. Only if Layer 1 allows does `policy.decide()` run as it does today. Deny-by-default at both layers.

Because each layer owns a distinct concern (RBAC narrows by *who*; policy narrows by *what*), the result is a logical AND — they compose rather than conflict. Crucially, **`policy.yaml` stays role-agnostic**: no `when: "role == ..."` strings. Human RBAC lives entirely in `roles.yaml`.

Enforcement lives at the **runtime surface** (`Agent.tool`), not in `app.py`, so every tool call passes through one gate regardless of caller — consistent with the Zero Trust principle already in the codebase.

### Tool-call flow

```
zta_tools node → agent.tool(name, args)
   │
   ├─ Layer 1: permissions.tool_allowed(role, name)?
   │      └─ no  → deny + audit  ("rbac: role 'viewer' not permitted to use 'db_query'")
   │      └─ yes ↓
   ├─ Layer 2: policy.decide(agent_id, tool, args)
   │      └─ deny/pending → record as today
   │      └─ allow ↓
   └─ execute tool → record allow
                     (every audit event now carries `user`)
```

## Components

### New files

| File | Responsibility |
|---|---|
| `zta/rbac.py` | `Role` enum (`admin`/`analyst`/`viewer`); `Permissions` loaded from `roles.yaml`; `tool_allowed(role, tool)`, `page_allowed(role, page)`, `as_table()`. Validates at load time that every role name and referenced tool/page is known — typos fail loudly, not silently. |
| `zta/users.py` | `UserStore` over SQLite (`users` table). `User` (username, role, password_hash, created_at); `create_user`, `get_user`, `verify_password`, `list_users`, `delete_user`. Passwords hashed with **argon2id** via `argon2-cffi` (self-describing `$argon2id$...` encoding). |
| `zta/webauth.py` | Signed session-cookie helpers using stdlib `hmac` (~20 lines): `sign(payload) -> str`, `verify(cookie) -> payload | None`. Stateless; no server-side session store. |
| `roles.yaml` | The RBAC matrix (single source of truth for "who can do what"). |
| `templates/login.html`, `templates/users.html`, `templates/roles.html` | Login page; admin user-management page; read-only matrix view. |
| `examples/seed_users.py` | Seed default admin/analyst/viewer accounts (idempotent). |
| `tests/test_rbac.py`, `tests/test_users.py`, `tests/test_auth.py` | Unit + route-guard tests. |

### Changed files

| File | Change |
|---|---|
| `zta/runtime.py` | `Agent` gains `user`, `role`, `permissions`. `session()` accepts them. `Agent.tool()` runs **Layer 1 RBAC** before `policy.decide()`; `audit.append()` is called with `user`. New deny path for RBAC denials (reused `_record_deny` with an `rbac:` reason). |
| `zta/audit.py` | Add `user: str = ""` field to `AuditEvent`; `append()` gains a `user` kwarg. (Hash now covers `user`; existing `audit.jsonl` must be regenerated — demo data, acceptable.) |
| `app.py` | Add signed-cookie session support via `zta/webauth.py`; `AppConfig` gains `roles_path` and `secret_key`. New routes `/login`, `/logout`, `/users` (GET/POST/delete), `/roles`. `get_current_user` + `require_page(...)` dependencies on all routes. Per-request agent session built with the logged-in user's role + permissions; `user` threaded into audit. |
| `templates/base.html` | Sidebar shows `username · role` + Logout; nav links filtered by `page_allowed(role)`. |
| `zta/policy.py` | **Unchanged** — stays role-agnostic (the separation-of-concerns win from Approach B). |

## Data model & configuration

### `roles.yaml`

```yaml
roles:
  manager:
    pages: [chat, audit, policy, users, roles]
    tools: [echo, db_query, db_write]
    tables: "*"                       # all tables, incl. Employee
  sales:
    pages: [chat, audit]
    tools: [echo, db_query]
    tables: [Artist, Album, Track, Genre, MediaType, Playlist, PlaylistTrack,
             Customer, Invoice, InvoiceLine]   # no Employee
  catalog:
    pages: [chat]
    tools: [echo, db_query]
    tables: [Artist, Album, Track, Genre, MediaType, Playlist, PlaylistTrack]
```

### `users` table (in the existing `ZTA_DB_PATH` SQLite DB)

```sql
CREATE TABLE users (
  username      TEXT PRIMARY KEY,
  role          TEXT NOT NULL,          -- admin|analyst|viewer
  password_hash TEXT NOT NULL,          -- $argon2id$v=19$m=...,t=...,p=...$salt$hash
  created_at    TEXT NOT NULL
);
```

### Session cookie

Signed JSON payload `{"username": ..., "role": ...}` + HMAC-SHA256 signature using `ZTA_SECRET_KEY`. Stateless, single-process friendly. Tampered or unsigned cookies are rejected.

### Audit

`AuditEvent` gains `user`. Every decision now records both **who** (user) and **which agent** (agent_id), strengthening the audit trail.

### New environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ZTA_SECRET_KEY` | _(required)_ | HMAC key for signing session cookies. |
| `ZTA_ROLES_PATH` | `./roles.yaml` | RBAC matrix path. |

## Auth flow & routing

```
GET  /login  → login form (username, password)
POST /login  → UserStore.verify_password()
                 ├─ valid → set signed session cookie → redirect /
                 └─ invalid → re-render form + error (audit: login_failed)
GET  /logout → clear cookie → redirect /login
```

**Dependencies on every route:**
- `get_current_user` — read & verify cookie; missing/invalid → redirect `/login` (HTML routes) or `401` (`/chat/stream`, `/api/audit`).
- `require_page("<page>")` — `permissions.page_allowed(role, page)`; failure → `403`.

**Route guard map:**

| Route | manager | sales | catalog |
|---|:--:|:--:|:--:|
| `/login`, `/logout` | public | public | public |
| `/`, `/chat`, `/chat/stream` | ✅ | ✅ | ✅ (table scope enforced in db_query) |
| `/audit`, `/api/audit` | ✅ | ✅ | ⛔ 403 |
| `/policy` | ✅ | ⛔ | ⛔ |
| `/users` | ✅ | ⛔ | ⛔ |
| `/roles` | ✅ | ⛔ | ⛔ |

## UI

- **`base.html`** — sidebar bottom shows `👤 username · role` + Logout; nav links filtered per `page_allowed(role)` (viewer sees only Chat; analyst sees Chat + Audit; admin sees all). Reuses the existing app-shell + light/dark theme.
- **`login.html`** — centered login card, themed.
- **`users.html`** (admin) — table of users (username, role, created) + "add user" form (username, password, role) + delete buttons. Cannot delete self.
- **`roles.html`** (admin) — renders `roles.yaml` as a read-only role × {pages, tools} table. This is the "who-can-do-what" grid that motivated Approach B.
- **Viewer chat behavior** — viewer can chat; if the model attempts a tool, Layer 1 denies it and an inline deny tool-card appears (`rbac: role 'viewer' not permitted to use 'db_query'`), consistent with existing policy-deny UX.

## Testing

- `test_rbac.py` — `tool_allowed`/`page_allowed` per role; a `roles.yaml` referencing an unknown role/tool/page fails at load.
- `test_users.py` — hash ≠ plaintext; `verify_password` correct/incorrect; create/get/list/delete; duplicate username rejected.
- `test_auth.py` — login success sets cookie; login failure; tampered/forged cookie rejected; logout clears cookie; route guard per role (matrix above → 200/403/redirect).
- Extend `test_runtime.py` — `Agent.tool` with viewer role → Layer-1 deny (never reaches policy); analyst `db_query` SELECT → allow; analyst `db_write` → Layer-1 deny; admin `db_write` → passes Layer 1 then policy.
- Extend `test_audit.py` — events include `user`; chain still verifies with the new field.
- Extend `test_app.py` — e2e per role over HTTP (viewer tool-blocked, analyst read-only, admin full); protected pages redirect when logged out.

## Documentation

- README gains a "Users & Roles" section (default accounts, the matrix, how to edit `roles.yaml`).
- `.env.example` gains `ZTA_SECRET_KEY`, `ZTA_ROLES_PATH`.
- CLAUDE.md updated (auth, RBAC, the two-layer model).

## Out of scope (this version)

- In-app editing of `roles.yaml` (role definitions are edited as a file; only *users* are managed in-app).
- Password reset / email flows, account lockout, rate limiting.
- Per-user (as opposed to per-role) permissions.
- Multi-agent or multi-tenant isolation.

## Migration notes

- Adding `user` to `AuditEvent` changes the hash of new events; the existing `audit.jsonl` will fail `verify_chain()` against the new schema. Since it is generated demo data, the upgrade regenerates it (delete/rotate on first run).
- `ZTA_SECRET_KEY` becomes required; document it and fail fast with a clear error if missing.
