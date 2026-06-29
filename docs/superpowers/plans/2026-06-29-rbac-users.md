# User Roles & RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add authenticated users with three roles (admin/analyst/viewer) that gate both the agent's tools and the app's pages, with admin-managed users and a readable permission matrix.

**Architecture:** Two-layer authorization composed with AND — a new RBAC matrix (`roles.yaml`, "who may touch what") checked at the runtime surface before the existing, unchanged policy engine ("is this specific call safe"). Auth is username/password in SQLite with a stdlib HMAC-signed session cookie. Enforcement lives in `Agent.tool` (tools) and FastAPI route guards (pages).

**Tech Stack:** Python 3.11+, FastAPI, Starlette, Pydantic v2, SQLite (stdlib `sqlite3`), stdlib `hashlib`/`hmac`, PyYAML, pytest.

## Global Constraints

- Python `>=3.11`; type-checked with `mypy` (strict) and linted/formatted with `ruff` (line-length 100).
- **Minimal runtime dependencies** — session signing uses only the stdlib (`hmac`); the single auth dependency is **`argon2-cffi`** for argon2id password hashing (chosen over stdlib PBKDF2; see Revision). No others.
- `policy.yaml` and `zta/policy.py` stay **role-agnostic** — RBAC never leaks into the policy engine.
- Deny-by-default at both layers.
- Tests run via WSL Ubuntu venv: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest ...'`.
- Coverage target: 80% overall, 90% for library modules.
- Roles are exactly `manager`, `sales`, `catalog` (see Revision). Pages are exactly `chat`, `audit`, `policy`, `users`, `roles`. Known tools are `echo`, `db_query`, `db_write`. Known tables are the 11 Chinook tables.

## Revision (2026-06-29): Chinook domain roles + table scoping

The data was swapped to the Chinook media-store DB, so the role model changed. **This revision overrides role names and adds a table-scope dimension throughout the tasks below** — wherever a task still shows `admin`/`analyst`/`viewer`, substitute `manager`/`sales`/`catalog` and the scope below.

| Old role | New role | Pages | Write | Readable tables |
|---|---|---|---|---|
| admin | **manager** | all | ✅ | `"*"` (all, incl. `Employee`) |
| analyst | **sales** | chat, audit | ❌ | catalog + `Customer`, `Invoice`, `InvoiceLine` (no `Employee`) |
| viewer | **catalog** | chat | ❌ | catalog only (`Artist, Album, Track, Genre, MediaType, Playlist, PlaylistTrack`) |

Concretely this changes:
- **`zta/rbac.py`** — `Role` enum → `MANAGER`/`SALES`/`CATALOG`; add `KNOWN_TABLES`; `Permissions` gains `tables` (`"*"` or list), `table_readable(role, table)`, `tables_allowed(role)`; `RoleRow` gains `tables`; load-time validation of table names. (Done in PR for issue #34 — supersedes the merged Task 1 role names.)
- **`roles.yaml`** — new names + `tables` key.
- **seed defaults** (Task 6) — `manager/sales/catalog` (e.g. `manager/manager123`, `sales/sales123`, `catalog/catalog123`).
- **`/users`, `/roles`, `/policy` guards** (Tasks 6–7) — gated to `manager` (not `admin`).
- **test helpers / role args** — use `manager` where the old text used `admin`.
- **password hashing** — Task 2 shipped stdlib PBKDF2; this was later replaced by **argon2id** (`argon2-cffi`), the one allowed runtime dependency. `hash_password`/`verify_hash` use `argon2.PasswordHasher`; stored hashes are `$argon2id$...`. (Issue #38.)

**New task — Table-scoped `db_query` (after Task 6 auth wiring):** build the `db_query` tool **per request**, bound to the current role's readable tables, installing a SQLite `set_authorizer` callback that returns `SQLITE_DENY` for `SQLITE_READ` on out-of-scope tables. Catch the resulting access error and record a clean **authorization deny** (`rbac: role 'catalog' not permitted to read table 'Employee'`) in trace + audit. Tests: catalog querying `Customer`/`Employee` → deny; sales querying `Employee` → deny; manager → allowed; scoped `SELECT` within scope → allowed.

## Delivery Workflow (per task)

Each task below is one delivery unit. For every task:

1. `gh issue create` describing the task.
2. `git switch main && git pull && git switch -c feature/<task-slug>`.
3. Implement via the TDD steps.
4. Run the full test suite (WSL command above) + `ruff check .` + `mypy`.
5. `git push -u origin feature/<task-slug>` then `gh pr create` (link the issue).
6. **User reviews and merges** (never self-merge).
7. After merge, check the task off in `todo.md`.

---

## File Structure

**New files:**
- `zta/rbac.py` — `Role` enum, `Permissions` loader/validator, `tool_allowed`/`page_allowed`/`as_table`.
- `zta/users.py` — `User`, `UserStore` (SQLite CRUD), `hash_password`/`verify_hash`.
- `zta/webauth.py` — `sign_session`/`verify_session` (HMAC-signed cookie value).
- `roles.yaml` — the RBAC matrix.
- `templates/login.html`, `templates/users.html`, `templates/roles.html`.
- `examples/seed_users.py` — idempotent default-account seeder.
- `tests/test_rbac.py`, `tests/test_users.py`, `tests/test_webauth.py`, `tests/test_auth.py`.

**Modified files:**
- `zta/errors.py` — add `RbacError`.
- `zta/audit.py` — add `user` field + `append(user=...)`.
- `zta/runtime.py` — `Agent`/`session` gain `user`/`role`/`permissions`; Layer-1 check in `tool`.
- `app.py` — auth, route guards, admin pages, per-request role wiring, config.
- `templates/base.html` — current user + logout + role-filtered nav.
- `tests/test_app.py`, `tests/test_runtime.py`, `tests/test_audit.py` — extend/auth-adapt.
- `README.md`, `.env.example`, `CLAUDE.md` — docs.

---

## Task 1: RBAC permission matrix (`zta/rbac.py` + `roles.yaml`)

**Files:**
- Create: `zta/rbac.py`, `roles.yaml`, `tests/test_rbac.py`
- Modify: `zta/errors.py`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces:
  - `class Role(str, Enum)` with `ADMIN="admin"`, `ANALYST="analyst"`, `VIEWER="viewer"`.
  - `KNOWN_PAGES: frozenset[str]` = `{"chat","audit","policy","users","roles"}`.
  - `KNOWN_TOOLS: frozenset[str]` = `{"echo","db_query","db_write"}`.
  - `class Permissions` with `Permissions.load(path: Path) -> Permissions`, `tool_allowed(role: str, tool: str) -> bool`, `page_allowed(role: str, page: str) -> bool`, `roles() -> list[str]`, `as_table() -> list[dict[str, object]]` (each `{"role": str, "pages": list[str], "tools": list[str]}`).
  - `class RbacError(ZtaError)` in `zta/errors.py`.

- [ ] **Step 1: Add `RbacError` to `zta/errors.py`**

Append after the existing error classes (they subclass the package base error — match the existing pattern, e.g. if they subclass `ZtaError`):

```python
class RbacError(ZtaError):
    """Raised when the RBAC matrix is invalid or cannot be loaded."""
```

(If the base class is named differently in `errors.py`, subclass whatever the other errors subclass.)

- [ ] **Step 2: Write failing tests** `tests/test_rbac.py`

```python
from pathlib import Path

import pytest
from zta.errors import RbacError
from zta.rbac import KNOWN_PAGES, KNOWN_TOOLS, Permissions, Role


def write_roles(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "roles.yaml"
    p.write_text(body)
    return p


VALID = """
roles:
  admin:
    pages: [chat, audit, policy, users, roles]
    tools: [echo, db_query, db_write]
  analyst:
    pages: [chat, audit]
    tools: [echo, db_query]
  viewer:
    pages: [chat]
    tools: []
"""


def test_known_constants() -> None:
    assert KNOWN_PAGES == {"chat", "audit", "policy", "users", "roles"}
    assert KNOWN_TOOLS == {"echo", "db_query", "db_write"}
    assert Role.ADMIN.value == "admin"


def test_tool_allowed(tmp_path: Path) -> None:
    perms = Permissions.load(write_roles(tmp_path, VALID))
    assert perms.tool_allowed("admin", "db_write") is True
    assert perms.tool_allowed("analyst", "db_query") is True
    assert perms.tool_allowed("analyst", "db_write") is False
    assert perms.tool_allowed("viewer", "echo") is False


def test_page_allowed(tmp_path: Path) -> None:
    perms = Permissions.load(write_roles(tmp_path, VALID))
    assert perms.page_allowed("analyst", "audit") is True
    assert perms.page_allowed("analyst", "policy") is False
    assert perms.page_allowed("viewer", "chat") is True
    assert perms.page_allowed("viewer", "audit") is False


def test_unknown_role_or_value_rejected(tmp_path: Path) -> None:
    assert perms_unknown_role(tmp_path)
    assert perms_unknown_page(tmp_path)
    assert perms_unknown_tool(tmp_path)


def perms_unknown_role(tmp_path: Path) -> bool:
    bad = "roles:\n  superuser:\n    pages: [chat]\n    tools: []\n"
    with pytest.raises(RbacError):
        Permissions.load(write_roles(tmp_path, bad))
    return True


def perms_unknown_page(tmp_path: Path) -> bool:
    bad = "roles:\n  admin:\n    pages: [dashboard]\n    tools: []\n"
    with pytest.raises(RbacError):
        Permissions.load(write_roles(tmp_path, bad))
    return True


def perms_unknown_tool(tmp_path: Path) -> bool:
    bad = "roles:\n  admin:\n    pages: [chat]\n    tools: [rm_rf]\n"
    with pytest.raises(RbacError):
        Permissions.load(write_roles(tmp_path, bad))
    return True


def test_unknown_role_query_is_false(tmp_path: Path) -> None:
    perms = Permissions.load(write_roles(tmp_path, VALID))
    assert perms.tool_allowed("ghost", "echo") is False
    assert perms.page_allowed("ghost", "chat") is False


def test_as_table(tmp_path: Path) -> None:
    perms = Permissions.load(write_roles(tmp_path, VALID))
    table = perms.as_table()
    by_role = {row["role"]: row for row in table}
    assert set(by_role) == {"admin", "analyst", "viewer"}
    assert by_role["viewer"]["tools"] == []
    assert "db_query" in by_role["analyst"]["tools"]
```

- [ ] **Step 3: Run tests, verify they fail**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_rbac.py -q'`
Expected: FAIL (`ModuleNotFoundError: zta.rbac`).

- [ ] **Step 4: Implement `zta/rbac.py`**

```python
"""RBAC permission matrix: which role may use which pages and tools.

Loaded from a YAML file (`roles.yaml`). Validates at load time that
every role is a known Role and every referenced page/tool is known, so
typos fail loudly instead of silently denying. Unknown roles queried at
runtime simply return False (deny-by-default).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from zta.errors import RbacError


class Role(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


KNOWN_PAGES: frozenset[str] = frozenset({"chat", "audit", "policy", "users", "roles"})
KNOWN_TOOLS: frozenset[str] = frozenset({"echo", "db_query", "db_write"})
KNOWN_ROLES: frozenset[str] = frozenset(r.value for r in Role)


@dataclass
class Permissions:
    _pages: dict[str, set[str]]
    _tools: dict[str, set[str]]

    @classmethod
    def load(cls, path: Path) -> Permissions:
        if not path.exists():
            raise RbacError(f"roles file not found: {path}")
        try:
            raw = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise RbacError(f"invalid YAML in roles file {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise RbacError(f"roles file root must be a mapping: {path}")
        roles_raw = raw.get("roles", {})
        if not isinstance(roles_raw, dict):
            raise RbacError(f"'roles' must be a mapping: {path}")

        pages: dict[str, set[str]] = {}
        tools: dict[str, set[str]] = {}
        for role, spec in roles_raw.items():
            if role not in KNOWN_ROLES:
                raise RbacError(f"unknown role {role!r}; must be one of {sorted(KNOWN_ROLES)}")
            if not isinstance(spec, dict):
                raise RbacError(f"role {role!r} spec must be a mapping")
            role_pages = set(spec.get("pages", []) or [])
            role_tools = set(spec.get("tools", []) or [])
            unknown_pages = role_pages - KNOWN_PAGES
            if unknown_pages:
                raise RbacError(f"role {role!r} references unknown page(s): {sorted(unknown_pages)}")
            unknown_tools = role_tools - KNOWN_TOOLS
            if unknown_tools:
                raise RbacError(f"role {role!r} references unknown tool(s): {sorted(unknown_tools)}")
            pages[role] = role_pages
            tools[role] = role_tools
        return cls(_pages=pages, _tools=tools)

    def tool_allowed(self, role: str, tool: str) -> bool:
        return tool in self._tools.get(role, set())

    def page_allowed(self, role: str, page: str) -> bool:
        return page in self._pages.get(role, set())

    def roles(self) -> list[str]:
        return sorted(self._pages)

    def as_table(self) -> list[dict[str, object]]:
        return [
            {
                "role": role,
                "pages": sorted(self._pages.get(role, set())),
                "tools": sorted(self._tools.get(role, set())),
            }
            for role in self.roles()
        ]
```

- [ ] **Step 5: Create `roles.yaml`**

```yaml
roles:
  admin:
    pages: [chat, audit, policy, users, roles]
    tools: [echo, db_query, db_write]
  analyst:
    pages: [chat, audit]
    tools: [echo, db_query]
  viewer:
    pages: [chat]
    tools: []
```

- [ ] **Step 6: Run tests, verify pass**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_rbac.py -q'`
Expected: PASS.

- [ ] **Step 7: Lint + type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/ruff check zta/rbac.py tests/test_rbac.py && .venv/bin/mypy zta/rbac.py'`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add zta/rbac.py zta/errors.py roles.yaml tests/test_rbac.py
git commit -m "feat(rbac): permission matrix loader with load-time validation"
```

---

## Task 2: User store (`zta/users.py`)

**Files:**
- Create: `zta/users.py`, `tests/test_users.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `hash_password(password: str) -> str` (format `pbkdf2$<iterations>$<salt_b64>$<hash_b64>`).
  - `verify_hash(password: str, stored: str) -> bool`.
  - `@dataclass class User` with `username: str`, `role: str`, `password_hash: str`, `created_at: str`.
  - `class UserStore` with `__init__(db_path: Path)`, `create_user(username: str, password: str, role: str) -> User`, `get_user(username: str) -> User | None`, `verify_password(username: str, password: str) -> bool`, `list_users() -> list[User]`, `delete_user(username: str) -> None`.

- [ ] **Step 1: Write failing tests** `tests/test_users.py`

```python
from pathlib import Path

import pytest
from zta.users import User, UserStore, hash_password, verify_hash


def test_hash_roundtrip() -> None:
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert h.startswith("pbkdf2$")
    assert verify_hash("s3cret", h) is True
    assert verify_hash("wrong", h) is False


def test_create_and_get(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    user = store.create_user("alice", "pw", "admin")
    assert isinstance(user, User)
    assert user.username == "alice"
    assert user.role == "admin"
    got = store.get_user("alice")
    assert got is not None
    assert got.role == "admin"
    assert store.get_user("nobody") is None


def test_verify_password(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.create_user("bob", "hunter2", "analyst")
    assert store.verify_password("bob", "hunter2") is True
    assert store.verify_password("bob", "nope") is False
    assert store.verify_password("ghost", "hunter2") is False


def test_duplicate_rejected(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.create_user("carol", "pw", "viewer")
    with pytest.raises(ValueError):
        store.create_user("carol", "pw2", "admin")


def test_list_and_delete(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.create_user("a", "pw", "admin")
    store.create_user("b", "pw", "viewer")
    assert {u.username for u in store.list_users()} == {"a", "b"}
    store.delete_user("b")
    assert {u.username for u in store.list_users()} == {"a"}


def test_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "data.db"
    UserStore(db).create_user("d", "pw", "admin")
    assert UserStore(db).get_user("d") is not None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_users.py -q'`
Expected: FAIL (`ModuleNotFoundError: zta.users`).

- [ ] **Step 3: Implement `zta/users.py`**

```python
"""User store backed by SQLite, with stdlib password hashing.

Passwords are stored as `pbkdf2$<iterations>$<salt_b64>$<hash_b64>`
using `hashlib.pbkdf2_hmac`. No third-party crypto dependency.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return (
        f"pbkdf2${_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def verify_hash(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


@dataclass
class User:
    username: str
    role: str
    password_hash: str
    created_at: str


class UserStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username      TEXT PRIMARY KEY,
                    role          TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
                """
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def create_user(self, username: str, password: str, role: str) -> User:
        created_at = datetime.now(UTC).isoformat()
        user = User(
            username=username,
            role=role,
            password_hash=hash_password(password),
            created_at=created_at,
        )
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO users (username, role, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user.username, user.role, user.password_hash, user.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"user already exists: {username}") from exc
        return user

    def get_user(self, username: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT username, role, password_hash, created_at FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        if row is None:
            return None
        return User(
            username=row["username"],
            role=row["role"],
            password_hash=row["password_hash"],
            created_at=row["created_at"],
        )

    def verify_password(self, username: str, password: str) -> bool:
        user = self.get_user(username)
        if user is None:
            return False
        return verify_hash(password, user.password_hash)

    def list_users(self) -> list[User]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT username, role, password_hash, created_at FROM users ORDER BY username"
            ).fetchall()
        return [
            User(
                username=r["username"],
                role=r["role"],
                password_hash=r["password_hash"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def delete_user(self, username: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))
```

- [ ] **Step 4: Run tests, verify pass**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_users.py -q'`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/ruff check zta/users.py tests/test_users.py && .venv/bin/mypy zta/users.py'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add zta/users.py tests/test_users.py
git commit -m "feat(users): SQLite user store with pbkdf2 password hashing"
```

---

## Task 3: Signed session cookie (`zta/webauth.py`)

**Files:**
- Create: `zta/webauth.py`, `tests/test_webauth.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `sign_session(payload: dict[str, str], secret: str) -> str` — returns `"<b64url(json)>.<hex hmac>"`.
  - `verify_session(token: str, secret: str) -> dict[str, str] | None` — returns the payload if the signature is valid, else `None`.

- [ ] **Step 1: Write failing tests** `tests/test_webauth.py`

```python
from zta.webauth import sign_session, verify_session

SECRET = "test-secret-key"


def test_roundtrip() -> None:
    token = sign_session({"username": "alice", "role": "admin"}, SECRET)
    payload = verify_session(token, SECRET)
    assert payload == {"username": "alice", "role": "admin"}


def test_tampered_payload_rejected() -> None:
    token = sign_session({"username": "alice", "role": "viewer"}, SECRET)
    body, sig = token.split(".")
    import base64
    import json

    forged_body = base64.urlsafe_b64encode(
        json.dumps({"username": "alice", "role": "admin"}).encode()
    ).decode()
    forged = f"{forged_body}.{sig}"
    assert verify_session(forged, SECRET) is None


def test_wrong_secret_rejected() -> None:
    token = sign_session({"username": "a", "role": "admin"}, SECRET)
    assert verify_session(token, "other-secret") is None


def test_garbage_rejected() -> None:
    assert verify_session("not-a-token", SECRET) is None
    assert verify_session("", SECRET) is None
    assert verify_session("a.b.c", SECRET) is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_webauth.py -q'`
Expected: FAIL (`ModuleNotFoundError: zta.webauth`).

- [ ] **Step 3: Implement `zta/webauth.py`**

```python
"""Stateless signed session cookies using only the standard library.

A cookie value is `base64url(json(payload)) + "." + hex(hmac_sha256)`.
`verify_session` recomputes the HMAC and compares in constant time; any
mismatch, malformed token, or bad JSON returns None.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


def _sign(body_b64: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_session(payload: dict[str, str], secret: str) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body_b64 = base64.urlsafe_b64encode(body).decode("utf-8")
    return f"{body_b64}.{_sign(body_b64, secret)}"


def verify_session(token: str, secret: str) -> dict[str, str] | None:
    if not token or token.count(".") != 1:
        return None
    body_b64, sig = token.split(".")
    expected = _sign(body_b64, secret)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        raw = base64.urlsafe_b64decode(body_b64.encode("utf-8"))
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return {str(k): str(v) for k, v in payload.items()}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_webauth.py -q'`
Expected: PASS.

- [ ] **Step 5: Lint + type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/ruff check zta/webauth.py tests/test_webauth.py && .venv/bin/mypy zta/webauth.py'`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add zta/webauth.py tests/test_webauth.py
git commit -m "feat(webauth): stdlib HMAC-signed session cookies"
```

---

## Task 4: Audit user attribution (`zta/audit.py`)

**Files:**
- Modify: `zta/audit.py`
- Test: `tests/test_audit.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `AuditEvent` gains `user: str = ""`; `Audit.append(...)` gains a keyword-only `user: str = ""` parameter (placed before `prev_hash` is computed internally). The hash now covers `user`.

- [ ] **Step 1: Write failing test** — append to `tests/test_audit.py`

```python
def test_append_records_user(tmp_path) -> None:
    from zta.audit import Audit

    audit = Audit(tmp_path / "a.jsonl")
    event = audit.append(
        agent_id="bot",
        request_id="r1",
        action="tool:db_query",
        resource="tool:db_query",
        decision="allow",
        reason="ok",
        user="alice",
    )
    assert event.user == "alice"
    assert audit.verify_chain() is True
    reread = audit.read_all()[0]
    assert reread.user == "alice"


def test_user_defaults_empty(tmp_path) -> None:
    from zta.audit import Audit

    audit = Audit(tmp_path / "a.jsonl")
    event = audit.append(
        agent_id="bot",
        request_id="r1",
        action="x",
        resource="x",
        decision="allow",
        reason="ok",
    )
    assert event.user == ""
    assert audit.verify_chain() is True
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_audit.py -k user -q'`
Expected: FAIL (`TypeError: append() got an unexpected keyword argument 'user'`).

- [ ] **Step 3: Add `user` field to `AuditEvent`**

In `zta/audit.py`, add the field to the model (after `reason`, before `prev_hash`):

```python
    reason: str
    user: str = ""
    prev_hash: str = GENESIS_HASH
```

- [ ] **Step 4: Add `user` param to `Audit.append`**

Change the signature and event construction:

```python
    def append(
        self,
        *,
        agent_id: str,
        request_id: str,
        action: str,
        resource: str,
        decision: str,
        reason: str,
        user: str = "",
    ) -> AuditEvent:
        prev_hash = self._last_hash()
        event = AuditEvent(
            agent_id=agent_id,
            request_id=request_id,
            action=action,
            resource=resource,
            decision=decision,
            reason=reason,
            user=user,
            prev_hash=prev_hash,
        )
        event.this_hash = event.compute_this_hash()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
        return event
```

- [ ] **Step 5: Run full audit suite, verify pass**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_audit.py -q'`
Expected: PASS (existing chain tests still pass; `user` defaults to `""`).

- [ ] **Step 6: Lint + type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/ruff check zta/audit.py tests/test_audit.py && .venv/bin/mypy zta/audit.py'`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add zta/audit.py tests/test_audit.py
git commit -m "feat(audit): attribute events to the acting user"
```

---

## Task 5: Runtime RBAC enforcement (`zta/runtime.py`)

**Files:**
- Modify: `zta/runtime.py`
- Test: `tests/test_runtime.py` (extend)

**Interfaces:**
- Consumes: `zta.rbac.Permissions` (Task 1); `Audit.append(user=...)` (Task 4).
- Produces: `Agent` gains `user: str = ""`, `role: str = ""`, `permissions: Permissions | None = None`. `session(...)` gains keyword-only `user: str = ""`, `role: str = ""`, `permissions: Permissions | None = None`. `Agent.tool` performs the Layer-1 RBAC check before `policy.decide`; when `permissions is None` the RBAC layer is skipped (preserves existing library behavior); all audit writes pass `user=self.user`.

- [ ] **Step 1: Write failing tests** — append to `tests/test_runtime.py`

```python
def _perms(tmp_path):
    from zta.rbac import Permissions

    p = tmp_path / "roles.yaml"
    p.write_text(
        "roles:\n"
        "  admin:\n    pages: [chat]\n    tools: [echo, db_query, db_write]\n"
        "  analyst:\n    pages: [chat]\n    tools: [echo, db_query]\n"
        "  viewer:\n    pages: [chat]\n    tools: []\n"
    )
    return Permissions.load(p)


def _allow_all_policy(tmp_path):
    from zta.policy import Policy

    p = tmp_path / "policy.yaml"
    p.write_text("default: deny\nrules:\n  - tool: echo\n    decision: allow\n")
    return Policy.load(p)


def test_rbac_denies_viewer_tool(tmp_path) -> None:
    from zta.audit import Audit
    from zta.identity import Identity
    from zta.runtime import Agent
    from zta.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register_callable("echo", lambda message: f"echo: {message}")
    agent = Agent(
        agent_id="bot",
        policy=_allow_all_policy(tmp_path),
        audit=Audit(tmp_path / "a.jsonl"),
        identity=Identity.load_or_create("bot", tmp_path / "keys"),
        registry=reg,
        user="vic",
        role="viewer",
        permissions=_perms(tmp_path),
    )
    result = agent.tool("echo", message="hi")
    assert result.ok is False
    assert "rbac" in (result.error or "").lower()
    assert agent.trace[-1].decision == "deny"


def test_rbac_allows_then_policy_runs(tmp_path) -> None:
    from zta.audit import Audit
    from zta.identity import Identity
    from zta.runtime import Agent
    from zta.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register_callable("echo", lambda message: f"echo: {message}")
    agent = Agent(
        agent_id="bot",
        policy=_allow_all_policy(tmp_path),
        audit=Audit(tmp_path / "a.jsonl"),
        identity=Identity.load_or_create("bot", tmp_path / "keys"),
        registry=reg,
        user="amy",
        role="analyst",
        permissions=_perms(tmp_path),
    )
    result = agent.tool("echo", message="hi")
    assert result.ok is True
    assert result.value == "echo: hi"


def test_audit_records_user(tmp_path) -> None:
    from zta.audit import Audit
    from zta.identity import Identity
    from zta.runtime import Agent
    from zta.tools import ToolRegistry

    audit = Audit(tmp_path / "a.jsonl")
    reg = ToolRegistry()
    reg.register_callable("echo", lambda message: f"echo: {message}")
    agent = Agent(
        agent_id="bot",
        policy=_allow_all_policy(tmp_path),
        audit=audit,
        identity=Identity.load_or_create("bot", tmp_path / "keys"),
        registry=reg,
        user="amy",
        role="analyst",
        permissions=_perms(tmp_path),
    )
    agent.tool("echo", message="hi")
    assert audit.read_all()[-1].user == "amy"


def test_no_permissions_skips_rbac(tmp_path) -> None:
    from zta.audit import Audit
    from zta.identity import Identity
    from zta.runtime import Agent
    from zta.tools import ToolRegistry

    reg = ToolRegistry()
    reg.register_callable("echo", lambda message: f"echo: {message}")
    agent = Agent(
        agent_id="bot",
        policy=_allow_all_policy(tmp_path),
        audit=Audit(tmp_path / "a.jsonl"),
        identity=Identity.load_or_create("bot", tmp_path / "keys"),
        registry=reg,
    )
    result = agent.tool("echo", message="hi")
    assert result.ok is True
```

> Note: if `ToolRegistry` has no `register_callable`, use the registration method the existing `tests/test_runtime.py` already uses (check the top of that file) — keep the test consistent with current usage.

- [ ] **Step 2: Run tests, verify they fail**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_runtime.py -k "rbac or records_user or skips_rbac" -q'`
Expected: FAIL (`Agent.__init__() got an unexpected keyword argument 'permissions'`).

- [ ] **Step 3: Extend `Agent` dataclass fields**

In `zta/runtime.py`, add fields to `Agent` (after `registry`, keeping `trace` last):

```python
    registry: ToolRegistry
    user: str = ""
    role: str = ""
    permissions: "Permissions | None" = None
    trace: list[TraceEntry] = field(default_factory=list)
```

Add the import at the top:

```python
from zta.rbac import Permissions
```

- [ ] **Step 4: Add the Layer-1 RBAC check in `Agent.tool`**

At the very start of `tool()`, right after computing `request_id` and before `policy.decide`, insert:

```python
    def tool(self, name: str, **args: Any) -> ToolResult:
        request_id = uuid.uuid4().hex
        ts = datetime.now(UTC).isoformat()

        if self.permissions is not None and not self.permissions.tool_allowed(self.role, name):
            reason = f"rbac: role {self.role!r} not permitted to use {name!r}"
            return self._record_deny(
                request_id=request_id, ts=ts, name=name, args=args, reason=reason
            )

        decision = self.policy.decide(agent_id=self.agent_id, tool=name, args=args)
        reason = self.policy.reason()
        # ... (rest unchanged)
```

(Remove the now-duplicate `ts = datetime.now(UTC).isoformat()` that appeared later in the original body so `ts` is defined once.)

- [ ] **Step 5: Thread `user` into every audit write**

In each of `_record_allow`, `_record_deny`, `_record_pending`, `_record_error`, add `user=self.user` to the `self.audit.append(...)` call. Example for `_record_allow`:

```python
        self.audit.append(
            agent_id=self.agent_id,
            request_id=request_id,
            action=f"tool:{name}",
            resource=f"tool:{name}",
            decision="allow",
            reason=reason,
            user=self.user,
        )
```

Apply the identical `user=self.user` addition to `_record_deny`, `_record_pending`, and `_record_error`.

- [ ] **Step 6: Pass new params through `session()`**

Update the `session` context manager signature and the `Agent(...)` it yields:

```python
@contextmanager
def session(
    *,
    agent: str,
    policy: Path,
    audit: Path,
    key_dir: Path,
    user: str = "",
    role: str = "",
    permissions: Permissions | None = None,
) -> Iterator[Agent]:
    identity = Identity.load_or_create(agent, key_dir)
    loaded_policy = Policy.load(policy)
    audit_log = Audit(audit)
    yield Agent(
        agent_id=agent,
        policy=loaded_policy,
        audit=audit_log,
        identity=identity,
        registry=ToolRegistry(),
        user=user,
        role=role,
        permissions=permissions,
    )
```

- [ ] **Step 7: Run full runtime suite, verify pass**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_runtime.py -q'`
Expected: PASS (existing tests unaffected — they construct `Agent`/`session` without `permissions`, so RBAC is skipped).

- [ ] **Step 8: Lint + type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/ruff check zta/runtime.py tests/test_runtime.py && .venv/bin/mypy zta/runtime.py'`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
git add zta/runtime.py tests/test_runtime.py
git commit -m "feat(runtime): enforce role-based tool access before policy"
```

---

## Task 6: Authentication & route guards (`app.py`, login UI, seed)

This is the "flip" that makes the app require login. It bundles login/logout, per-route guards, per-request role wiring, the login page, the seeder, and the test updates so the suite stays green after merge.

**Files:**
- Modify: `app.py`, `templates/base.html`, `tests/test_app.py`
- Create: `templates/login.html`, `examples/seed_users.py`, `tests/test_auth.py`

**Interfaces:**
- Consumes: `Permissions` (Task 1), `UserStore`/`verify` (Task 2), `sign_session`/`verify_session` (Task 3), `session(user=,role=,permissions=)` (Task 5).
- Produces (within `app.py`):
  - `AppConfig` fields `roles_path: Path = Path("./roles.yaml")`, `secret_key: str = ""`, `users_db_path: Path` (default from `ZTA_DB_PATH` or `./data.db`).
  - module helper `_current_user(request: Request, cfg: AppConfig) -> dict[str, str] | None`.
  - module helper `_allowed_pages(perms: Permissions, role: str) -> list[str]`.
  - cookie name constant `SESSION_COOKIE = "zta_session"`.
  - routes `GET/POST /login`, `GET /logout`.
  - test helper `client_as(cfg: AppConfig, role: str = "admin") -> TestClient` (in `tests/test_app.py`).

- [ ] **Step 1: Extend `AppConfig` and resolve the secret key**

In `app.py`, update `AppConfig`:

```python
class AppConfig(BaseModel):
    agent_id: str = "analyst-bot"
    policy_path: Path = Field(default_factory=lambda: Path("./policy.yaml"))
    audit_path: Path = Field(default_factory=lambda: Path("./audit.jsonl"))
    key_dir: Path = Field(default_factory=lambda: Path("./.zta/keys"))
    roles_path: Path = Field(default_factory=lambda: Path("./roles.yaml"))
    users_db_path: Path = Field(
        default_factory=lambda: Path(os.environ.get("ZTA_DB_PATH", "./data.db"))
    )
    secret_key: str = Field(default_factory=lambda: os.environ.get("ZTA_SECRET_KEY", ""))
```

Add the cookie constant and imports near the top of `app.py`:

```python
import secrets

from fastapi.responses import RedirectResponse
from zta.rbac import KNOWN_PAGES, Permissions
from zta.users import UserStore
from zta.webauth import sign_session, verify_session

SESSION_COOKIE = "zta_session"
```

- [ ] **Step 2: Add auth helpers**

Add module-level helpers in `app.py` (above `create_app`):

```python
def _current_user(request: Request, cfg: AppConfig) -> dict[str, str] | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return verify_session(token, cfg.secret_key)


def _allowed_pages(perms: Permissions, role: str) -> list[str]:
    return [p for p in sorted(KNOWN_PAGES) if perms.page_allowed(role, p)]
```

- [ ] **Step 3: Resolve secret + load shared singletons in `create_app`**

At the top of `create_app`, after `cfg = config or AppConfig()`:

```python
    cfg = config or AppConfig()
    if not cfg.secret_key:
        cfg = cfg.model_copy(update={"secret_key": secrets.token_hex(32)})
        _log.warning("ZTA_SECRET_KEY not set; using an ephemeral key (sessions reset on restart)")
    perms = Permissions.load(cfg.roles_path)
    users = UserStore(cfg.users_db_path)
```

(`perms` and `users` are closed over by the route handlers below.)

- [ ] **Step 4: Add `/login` and `/logout` routes**

Inside `create_app`, add:

```python
    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> Response:
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login_submit(request: Request) -> Response:
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        user = users.get_user(username)
        if user is None or not users.verify_password(username, password):
            return templates.TemplateResponse(
                request, "login.html", {"error": "Invalid username or password"}, status_code=401
            )
        token = sign_session({"username": user.username, "role": user.role}, cfg.secret_key)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    async def logout() -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp
```

- [ ] **Step 5: Guard the chat page and thread role context**

Replace the `index` route:

```python
    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request,
            "chat.html",
            {
                "messages": [],
                "trace": [],
                "current_user": user,
                "allowed_pages": _allowed_pages(perms, user["role"]),
            },
        )
```

- [ ] **Step 6: Guard `/chat` and `/chat/stream` and pass role into the agent**

First change `_run_chat` and `_stream_chat` signatures to accept identity context and pass it through to `session(...)`:

```python
async def _stream_chat(
    messages: list[dict[str, object]],
    cfg: AppConfig,
    *,
    user: str,
    role: str,
    permissions: Permissions,
) -> AsyncIterator[str]:
    try:
        model = _get_chat_model()
    except HTTPException as exc:
        yield _sse_event("error", {"message": exc.detail})
        return
    with session(
        agent=cfg.agent_id,
        policy=cfg.policy_path,
        audit=cfg.audit_path,
        key_dir=cfg.key_dir,
        user=user,
        role=role,
        permissions=permissions,
    ) as agent:
        # ... body unchanged ...
```

Apply the same three keyword params (`user`, `role`, `permissions`) and the same `session(...)` additions to `_run_chat`.

Then guard the routes (login required; both roles may chat — tool gating happens in the runtime):

```python
    @app.post("/chat")
    async def chat(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            raise HTTPException(status_code=401, detail="login required")
        data = await request.json()
        req_messages = data.get("messages", [])
        if not req_messages:
            raise HTTPException(status_code=400, detail="messages must be non-empty")
        messages_in = [{"role": m["role"], "content": m["content"]} for m in req_messages]
        messages_out, reply, trace = await _run_chat(
            messages_in, cfg, user=user["username"], role=user["role"], permissions=perms
        )
        return JSONResponse({"reply": reply, "trace": trace, "messages": messages_out})

    @app.post("/chat/stream")
    async def chat_stream(request: Request) -> StreamingResponse:
        user = _current_user(request, cfg)
        if user is None:
            raise HTTPException(status_code=401, detail="login required")
        data = await request.json()
        req_messages = data.get("messages", [])
        if not req_messages:
            raise HTTPException(status_code=400, detail="messages must be non-empty")
        messages_in = [{"role": m["role"], "content": m["content"]} for m in req_messages]
        return StreamingResponse(
            _stream_chat(
                messages_in, cfg, user=user["username"], role=user["role"], permissions=perms
            ),
            media_type="text/event-stream",
        )
```

- [ ] **Step 7: Guard `/audit`, `/api/audit`, `/policy` by page permission**

For each, add the login + page-permission check at the top. `/api/audit` (API → 401/403); `/audit` and `/policy` (HTML → redirect/403):

```python
    @app.get("/api/audit")
    async def api_audit(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            raise HTTPException(status_code=401, detail="login required")
        if not perms.page_allowed(user["role"], "audit"):
            raise HTTPException(status_code=403, detail="forbidden")
        audit = Audit(cfg.audit_path)
        return JSONResponse(
            {
                "events": [e.model_dump(mode="json") for e in audit.read_all()],
                "chain_valid": audit.verify_chain(),
            }
        )
```

For `/audit` (HTML), insert after the signature:

```python
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "audit"):
            return HTMLResponse("403 Forbidden", status_code=403)
```

and add to its template context: `"current_user": user, "allowed_pages": _allowed_pages(perms, user["role"])`.

For `/policy` (HTML), insert the same block but with `"policy"` instead of `"audit"`, and add the same two context keys.

> Note: `api_audit` now takes `request: Request` and returns `Response` (was `dict`). Keep the `JSONResponse` wrapper shown above.

- [ ] **Step 8: Create `templates/login.html`**

```html
{% extends "base.html" %}
{% block title %}Sign in · ZTA{% endblock %}
{% block content %}
<div class="auth-card">
  <h1>Sign in</h1>
  {% if error %}<p class="auth-error">{{ error }}</p>{% endif %}
  <form method="post" action="/login" class="auth-form">
    <label>Username<input type="text" name="username" autofocus required></label>
    <label>Password<input type="password" name="password" required></label>
    <button type="submit">Sign in</button>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 9: Update `templates/base.html` for auth-aware nav**

Wrap the nav links so each is shown only if allowed, and show the current user + logout. Replace the `<nav>` block and add a user footer (use `current_user` / `allowed_pages`, which are passed by every authenticated page; guard with `{% if current_user %}`):

```html
        <nav>
          {% if not current_user or 'chat' in allowed_pages %}
          <a href="/" class="nav-link {% block nav_chat %}{% endblock %}">
            <span class="nav-ico">💬</span> <span class="nav-label">Chat</span>
          </a>
          {% endif %}
          {% if current_user and 'audit' in allowed_pages %}
          <a href="/audit" class="nav-link {% block nav_audit %}{% endblock %}">
            <span class="nav-ico">📜</span> <span class="nav-label">Audit</span>
          </a>
          {% endif %}
          {% if current_user and 'policy' in allowed_pages %}
          <a href="/policy" class="nav-link {% block nav_policy %}{% endblock %}">
            <span class="nav-ico">🛡️</span> <span class="nav-label">Policy</span>
          </a>
          {% endif %}
          {% if current_user and 'users' in allowed_pages %}
          <a href="/users" class="nav-link {% block nav_users %}{% endblock %}">
            <span class="nav-ico">👥</span> <span class="nav-label">Users</span>
          </a>
          {% endif %}
          {% if current_user and 'roles' in allowed_pages %}
          <a href="/roles" class="nav-link {% block nav_roles %}{% endblock %}">
            <span class="nav-ico">🗂️</span> <span class="nav-label">Roles</span>
          </a>
          {% endif %}
        </nav>
```

In `.sidebar-bottom`, above the theme toggle, add:

```html
        {% if current_user %}
        <div class="user-chip">
          <span class="user-ico">👤</span>
          <span class="user-meta"><b>{{ current_user.username }}</b><small>{{ current_user.role }}</small></span>
          <a href="/logout" class="logout-link" title="Sign out">⏻</a>
        </div>
        {% endif %}
```

Add minimal styles to `static/style.css` for `.auth-card`, `.auth-form`, `.auth-error`, `.user-chip` (reuse existing tokens; keep it short).

- [ ] **Step 10: Create `examples/seed_users.py`**

```python
"""Seed default ZTA users (idempotent). Run after seed_db.py.

Usage: ZTA_DB_PATH=./data.db python examples/seed_users.py
Default accounts (CHANGE in real use):
  admin   / admin123    (role: admin)
  analyst / analyst123  (role: analyst)
  viewer  / viewer123   (role: viewer)
"""

from __future__ import annotations

import os
from pathlib import Path

from zta.users import UserStore

DEFAULTS = [
    ("admin", "admin123", "admin"),
    ("analyst", "analyst123", "analyst"),
    ("viewer", "viewer123", "viewer"),
]


def main() -> None:
    db_path = Path(os.environ.get("ZTA_DB_PATH", "./data.db"))
    store = UserStore(db_path)
    for username, password, role in DEFAULTS:
        if store.get_user(username) is None:
            store.create_user(username, password, role)
            print(f"created {username} ({role})")
        else:
            print(f"skip {username} (exists)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 11: Add the test auth helper and update `make_config`**

In `tests/test_app.py`, update `make_config` to set a tmp users DB, roles path, and secret:

```python
def make_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        agent_id="bot",
        policy_path=write_policy(tmp_path, """..."""),  # unchanged body
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
        roles_path=Path("roles.yaml"),
        users_db_path=tmp_path / "users.db",
        secret_key="test-secret",
    )
```

Add a helper near the top of `tests/test_app.py`:

```python
def client_as(cfg: AppConfig, role: str = "admin") -> TestClient:
    """Build the app, seed a user with the given role, and log in."""
    from zta.users import UserStore

    UserStore(cfg.users_db_path).create_user(role, "pw", role)
    client = TestClient(create_app(cfg))
    resp = client.post("/login", data={"username": role, "password": "pw"}, follow_redirects=False)
    assert resp.status_code == 303
    return client
```

- [ ] **Step 12: Convert existing protected-route tests to authenticate**

In every existing `tests/test_app.py` test that hits a protected route, replace
`client = TestClient(create_app(cfg))` with `client = client_as(cfg)` (admin can reach everything, so existing assertions hold). The affected tests are:
`test_index_returns_service_metadata` (uses `create_app()` with no cfg → change to `client_as(make_config(tmp_path))` and add `tmp_path`), `test_chat_runs_echo_tool_through_zta`, `test_chat_deny_via_runtime_call`, `test_chat_audits_to_configured_path`, `test_api_audit_returns_events_and_chain_validity`, `test_audit_endpoint_returns_chain_status`, `test_policy_page_renders_html_with_yaml`, `test_policy_page_renders_html`, `test_policy_page_shows_default`, `test_policy_page_shows_agent_scope`, `test_policy_page_missing_file_returns_500`, `test_chat_openai_no_function_call_returns_content`, `test_chat_openai_tool_deny_propagates`, `test_index_renders_chat_html`, `test_chat_html_includes_base_layout`, `test_audit_page_renders_html`, `test_audit_page_shows_empty_state`, `test_audit_page_lists_events`, `test_api_audit_still_returns_json_after_f10`, `test_e2e_chat_db_query_allowed`, `test_e2e_chat_db_write_denied`, `test_chat_stream_returns_sse`, `test_chat_stream_emits_tokens_and_trace`, `test_chat_stream_missing_api_key_returns_error_event`.

For the two pure-validation tests that should stay unauthenticated, keep them but update expectations:
- `test_chat_empty_messages_returns_400` → now returns 401 without auth; change to use `client_as(make_config(tmp_path))` so it still exercises the 400 path (add `tmp_path` param).
- `test_chat_openai_missing_api_key_returns_500` → wrap with `client_as(...)` similarly.
- `test_chat_stream_empty_messages_returns_400` → `client_as(...)`.

> The `_db_query` e2e tests set `ZTA_DB_PATH` to a tmp file; `make_config`'s `users_db_path` also defaults from `ZTA_DB_PATH`. In `client_as` we pass `cfg.users_db_path` explicitly (the tmp `users.db`), so users and demo data can share or differ safely. Keep `users_db_path=tmp_path / "users.db"` distinct from the demo DB.

- [ ] **Step 13: Write `tests/test_auth.py` (guard behavior)**

```python
from pathlib import Path

from app import AppConfig, create_app
from fastapi.testclient import TestClient
from zta.users import UserStore


def cfg_for(tmp_path: Path) -> AppConfig:
    return AppConfig(
        agent_id="bot",
        policy_path=Path("policy.yaml"),
        audit_path=tmp_path / "a.jsonl",
        key_dir=tmp_path / "keys",
        roles_path=Path("roles.yaml"),
        users_db_path=tmp_path / "users.db",
        secret_key="test-secret",
    )


def login(client: TestClient, username: str, password: str) -> None:
    resp = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert resp.status_code == 303


def test_unauthenticated_html_redirects(tmp_path: Path) -> None:
    client = TestClient(create_app(cfg_for(tmp_path)))
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_unauthenticated_api_401(tmp_path: Path) -> None:
    client = TestClient(create_app(cfg_for(tmp_path)))
    assert client.get("/api/audit").status_code == 401


def test_bad_credentials_401(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    UserStore(cfg.users_db_path).create_user("admin", "pw", "admin")
    client = TestClient(create_app(cfg))
    resp = client.post(
        "/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False
    )
    assert resp.status_code == 401


def test_login_then_logout(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    UserStore(cfg.users_db_path).create_user("admin", "pw", "admin")
    client = TestClient(create_app(cfg))
    login(client, "admin", "pw")
    assert client.get("/", follow_redirects=False).status_code == 200
    client.get("/logout", follow_redirects=False)
    assert client.get("/", follow_redirects=False).status_code == 303


def test_viewer_forbidden_from_audit(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    UserStore(cfg.users_db_path).create_user("viewer", "pw", "viewer")
    client = TestClient(create_app(cfg))
    login(client, "viewer", "pw")
    assert client.get("/audit", follow_redirects=False).status_code == 403
    assert client.get("/api/audit").status_code == 403


def test_analyst_forbidden_from_policy(tmp_path: Path) -> None:
    cfg = cfg_for(tmp_path)
    UserStore(cfg.users_db_path).create_user("analyst", "pw", "analyst")
    client = TestClient(create_app(cfg))
    login(client, "analyst", "pw")
    assert client.get("/audit", follow_redirects=False).status_code == 200
    assert client.get("/policy", follow_redirects=False).status_code == 403


def test_forged_cookie_rejected(tmp_path: Path) -> None:
    client = TestClient(create_app(cfg_for(tmp_path)))
    client.cookies.set("zta_session", "forged.deadbeef")
    assert client.get("/", follow_redirects=False).status_code == 303
```

- [ ] **Step 14: Run the full suite, lint, type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy zta tests app.py'`
Expected: all PASS/clean. (`roles.yaml` from Task 1 must exist at repo root — the default `roles_path`.)

- [ ] **Step 15: Commit**

```bash
git add app.py templates/base.html templates/login.html static/style.css examples/seed_users.py tests/test_app.py tests/test_auth.py
git commit -m "feat(auth): require login, gate pages by role, attribute audit to user"
```

---

## Task 7: Admin pages — `/users` (manage) and `/roles` (matrix view)

**Files:**
- Modify: `app.py`, `tests/test_app.py`
- Create: `templates/users.html`, `templates/roles.html`

**Interfaces:**
- Consumes: `users` (`UserStore`), `perms` (`Permissions`), `_current_user`, `_allowed_pages` (Task 6); `Role` (Task 1).
- Produces: routes `GET /users`, `POST /users` (create), `POST /users/delete`, `GET /roles`.

- [ ] **Step 1: Write failing tests** — append to `tests/test_app.py` (uses `client_as` from Task 6)

```python
def test_users_page_admin_only(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    viewer_client = client_as(cfg, role="viewer")
    assert viewer_client.get("/users", follow_redirects=False).status_code == 403


def test_admin_can_create_and_delete_user(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = client_as(cfg, role="admin")
    resp = client.post(
        "/users",
        data={"username": "newbie", "password": "pw", "role": "analyst"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    listing = client.get("/users")
    assert "newbie" in listing.text
    client.post("/users/delete", data={"username": "newbie"}, follow_redirects=False)
    assert "newbie" not in client.get("/users").text


def test_admin_cannot_delete_self(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = client_as(cfg, role="admin")  # logged in as user "admin"
    client.post("/users/delete", data={"username": "admin"}, follow_redirects=False)
    assert "admin" in client.get("/users").text  # still present


def test_roles_page_shows_matrix(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = client_as(cfg, role="admin")
    body = client.get("/roles").text
    assert "analyst" in body
    assert "db_query" in body


def test_roles_page_forbidden_for_analyst(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    client = client_as(cfg, role="analyst")
    assert client.get("/roles", follow_redirects=False).status_code == 403
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_app.py -k "users or roles_page" -q'`
Expected: FAIL (404 for `/users` and `/roles`).

- [ ] **Step 3: Add the admin routes in `create_app`**

```python
    @app.get("/users", response_class=HTMLResponse)
    async def users_page(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "users"):
            return HTMLResponse("403 Forbidden", status_code=403)
        return templates.TemplateResponse(
            request,
            "users.html",
            {
                "current_user": user,
                "allowed_pages": _allowed_pages(perms, user["role"]),
                "users": users.list_users(),
                "roles": [r.value for r in Role],
            },
        )

    @app.post("/users")
    async def users_create(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "users"):
            return HTMLResponse("403 Forbidden", status_code=403)
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        role = str(form.get("role", ""))
        if username and password and role in {r.value for r in Role}:
            if users.get_user(username) is None:
                users.create_user(username, password, role)
        return RedirectResponse("/users", status_code=303)

    @app.post("/users/delete")
    async def users_delete(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "users"):
            return HTMLResponse("403 Forbidden", status_code=403)
        form = await request.form()
        target = str(form.get("username", ""))
        if target and target != user["username"]:  # cannot delete self
            users.delete_user(target)
        return RedirectResponse("/users", status_code=303)

    @app.get("/roles", response_class=HTMLResponse)
    async def roles_page(request: Request) -> Response:
        user = _current_user(request, cfg)
        if user is None:
            return RedirectResponse("/login", status_code=303)
        if not perms.page_allowed(user["role"], "roles"):
            return HTMLResponse("403 Forbidden", status_code=403)
        return templates.TemplateResponse(
            request,
            "roles.html",
            {
                "current_user": user,
                "allowed_pages": _allowed_pages(perms, user["role"]),
                "matrix": perms.as_table(),
            },
        )
```

Add `from zta.rbac import KNOWN_PAGES, Permissions, Role` (extend the Task 6 import to include `Role`).

- [ ] **Step 4: Create `templates/users.html`**

```html
{% extends "base.html" %}
{% block title %}Users · ZTA{% endblock %}
{% block nav_users %}active{% endblock %}
{% block content %}
<div class="page">
  <header class="page-head"><h1>Users</h1>
    <p class="page-sub">Create and remove accounts. Roles are defined in <code>roles.yaml</code>.</p>
  </header>

  <section class="card">
    <h2>Add user</h2>
    <form method="post" action="/users" class="inline-form">
      <input type="text" name="username" placeholder="username" required>
      <input type="password" name="password" placeholder="password" required>
      <select name="role">
        {% for r in roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
      </select>
      <button type="submit">Add</button>
    </form>
  </section>

  <div class="table-card">
    <table class="audit-table">
      <thead><tr><th>Username</th><th>Role</th><th>Created</th><th></th></tr></thead>
      <tbody>
        {% for u in users %}
        <tr>
          <td>{{ u.username }}</td>
          <td><span class="badge badge-allow">{{ u.role }}</span></td>
          <td class="ts">{{ u.created_at }}</td>
          <td>
            {% if u.username != current_user.username %}
            <form method="post" action="/users/delete" style="margin:0">
              <input type="hidden" name="username" value="{{ u.username }}">
              <button type="submit" class="link-danger">delete</button>
            </form>
            {% else %}<small>you</small>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Create `templates/roles.html`**

```html
{% extends "base.html" %}
{% block title %}Roles · ZTA{% endblock %}
{% block nav_roles %}active{% endblock %}
{% block content %}
<div class="page">
  <header class="page-head"><h1>Roles</h1>
    <p class="page-sub">Who can do what. Edit <code>roles.yaml</code> to change these.</p>
  </header>
  <div class="table-card">
    <table class="audit-table">
      <thead><tr><th>Role</th><th>Pages</th><th>Tools</th></tr></thead>
      <tbody>
        {% for row in matrix %}
        <tr>
          <td><span class="badge badge-allow">{{ row.role }}</span></td>
          <td>{% for p in row.pages %}<code>{{ p }}</code> {% endfor %}</td>
          <td>{% if row.tools %}{% for t in row.tools %}<code>{{ t }}</code> {% endfor %}{% else %}<small>none</small>{% endif %}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

Add small styles for `.inline-form` and `.link-danger` to `static/style.css` (reuse tokens; keep short).

- [ ] **Step 6: Run tests, lint, type-check**

Run: `wsl -d Ubuntu -e bash -lc 'cd /mnt/d/dev/0trust-ai-agents && .venv/bin/python -m pytest tests/test_app.py -q && .venv/bin/ruff check . && .venv/bin/mypy zta tests app.py'`
Expected: PASS/clean.

- [ ] **Step 7: Commit**

```bash
git add app.py templates/users.html templates/roles.html static/style.css tests/test_app.py
git commit -m "feat(admin): /users management and /roles matrix view"
```

---

## Task 8: Documentation & config

**Files:**
- Modify: `README.md`, `.env.example`, `CLAUDE.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `.env.example`**

Add:

```bash
# Secret key for signing session cookies (required for stable sessions).
ZTA_SECRET_KEY=change-me-to-a-long-random-string
# Path to the RBAC role matrix.
ZTA_ROLES_PATH=./roles.yaml
```

- [ ] **Step 2: Add a "Users & Roles" section to `README.md`**

Insert after the "Try the demo" section:

````markdown
## Users & roles

The app requires login. Seed the default accounts (after `seed_db.py`):

```bash
ZTA_DB_PATH=./data.db python examples/seed_users.py
```

| Username | Password | Role | Pages | Tools |
|---|---|---|---|---|
| `admin` | `admin123` | admin | all | echo, db_query, db_write |
| `analyst` | `analyst123` | analyst | chat, audit | echo, db_query |
| `viewer` | `viewer123` | viewer | chat | none |

Authorization is **two layers**: the RBAC matrix in `roles.yaml` decides which
role may use which pages/tools; the existing `policy.yaml` then decides whether a
specific call is safe. Edit `roles.yaml` to change role permissions; view the live
matrix at `/roles`. Manage users at `/users` (admin only).

Set `ZTA_SECRET_KEY` to a long random string in production (cookies are signed
with it). **Change the default passwords.**
````

- [ ] **Step 3: Update `CLAUDE.md`**

Add an "Auth & RBAC" subsection documenting: login required; `roles.yaml` matrix (Layer 1) + `policy.yaml` (Layer 2) composed with AND; users in SQLite (`zta/users.py`); signed cookies (`zta/webauth.py`); new env vars `ZTA_SECRET_KEY`, `ZTA_ROLES_PATH`; seed via `examples/seed_users.py`.

- [ ] **Step 4: Commit**

```bash
git add README.md .env.example CLAUDE.md
git commit -m "docs: document users, roles, and the two-layer authorization model"
```

---

## Self-Review

**Spec coverage:**
- Both tools + pages gated → Tasks 1 (matrix), 5 (tool enforcement), 6/7 (page guards). ✅
- Username/password in SQLite + signed cookie → Tasks 2, 3, 6. ✅
- admin/analyst/viewer → Task 1 `Role` + `roles.yaml`. ✅
- Admin manages users in-app → Task 7 `/users`. ✅
- Separate RBAC matrix (Approach B), policy stays role-agnostic → Task 1 + unchanged `policy.py`. ✅
- Two-layer AND, enforced at runtime surface → Task 5. ✅
- Audit attributes user → Task 4 + 5. ✅
- `/roles` matrix view → Task 7. ✅
- Seed defaults, docs, env vars → Tasks 6, 8. ✅
- Migration (regenerate `audit.jsonl`, `ZTA_SECRET_KEY`) → Task 4 note + Task 6 secret handling + Task 8 docs. ✅

**Type consistency:** `Permissions.tool_allowed/page_allowed(role, x) -> bool`, `as_table() -> list[dict]`, `Agent(user, role, permissions)`, `session(user=, role=, permissions=)`, `Audit.append(user=)`, `_current_user -> dict|None`, `sign_session/verify_session` — names/signatures consistent across Tasks 1–7. ✅

**Deviation from spec:** spec said `ZTA_SECRET_KEY` is "required, fail fast." To avoid an import-time crash for `app = create_app()` without the env, Task 6 instead falls back to an **ephemeral random key with a loud warning**. Stable sessions still require setting it. (Documented in Task 8.)

