# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zero Trust AI Agents (`zta`) is a Python library and FastAPI demo that enforces cryptographic identity, deny-by-default policy decisions, and hash-chained audit logging on every tool call an LLM agent makes. It uses LangGraph to wire the agent loop explicitly.

## Development Setup

```bash
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
cp .env.example .env                   # fill in ZTA_OPENAI_API_KEY (+ ZTA_SECRET_KEY for stable sessions)
python examples/seed_db.py             # build data.db from the Chinook dataset (downloads pinned SQL)
python examples/seed_users.py          # create default users: manager / sales / catalog
```

## Common Commands

```bash
# Run the app
uvicorn app:app --reload               # http://localhost:8000

# Lint
ruff check .
ruff format --check .

# Type check
mypy zta tests app.py

# Tests
pytest                                 # full suite
pytest tests/test_agent_graph.py -v   # single file
pytest -k "stream" -v                 # filter by name
pytest --cov=zta --cov-report=term-missing
```

## Architecture

### Core library — `zta/`

Focused modules with a strict dependency order:

| Module | Role |
|---|---|
| `errors.py` | Exception hierarchy (`IdentityError`, `PolicyError`, `AuditError`, `ToolError`, `TokenError`, `RbacError`) |
| `identity.py` | Ed25519 keypair lifecycle — `load_or_create`, `sign`, `verify`. Keys stored in `.zta/keys/{agent_id}.pem` |
| `policy.py` | YAML deny-by-default rule engine — `decide(agent, tool, context)` evaluates `when` expressions in a sandboxed `eval` scope. **Role-agnostic** |
| `rbac.py` | RBAC matrix from `roles.yaml` — `Role` enum + `Permissions` (`tool_allowed`, `page_allowed`, `table_readable`, `tables_allowed`); load-time validation |
| `users.py` | SQLite user store (`UserStore`) + argon2id password hashing (`argon2-cffi`) |
| `webauth.py` | Stateless HMAC-signed session cookies (`sign_session` / `verify_session`), stdlib only |
| `audit.py` | Append-only JSONL log with SHA-256 hash chaining; events carry the acting `user` |
| `tools.py` | `@tool` decorator + `ToolRegistry` for wrapping functions and LangChain `BaseTool` instances |
| `runtime.py` | `session()` / `Agent` — Layer-1 RBAC (`tool_allowed`) runs before policy; tool `RbacError` → deny; audit attributed to `user` |
| `agent_graph.py` | LangGraph `StateGraph` with two nodes: `call_model` (ChatOpenAI with bound tools) and `zta_tools` (policy-gated dispatch) |

### FastAPI demo — `app.py`

Tools (`_echo`, `_db_write`, and a per-request scoped `db_query` from `_make_db_query`) plus:
- `/login`, `/logout` — auth (signed session cookie)
- `/chat` — JSON, full response (auth required)
- `/chat/stream` — SSE, token-by-token streaming via LangGraph `astream_events` (auth required)
- `/audit` and `/api/audit` — HTML and JSON views of the hash-chained log (manager/sales)
- `/policy` — rendered policy (manager)
- `/users`, `/roles` — admin pages (manager)

SSE event types emitted: `token`, `trace`, `error`, `end`. The per-request agent is built with the logged-in user's role + permissions.

### Request flow

```
Browser → /chat/stream
  → LangGraph StateGraph.astream_events()
    → call_model: ChatOpenAI.bind_tools()
    → zta_tools: policy.decide() → audit.append() → execute tool
  → SSE events → Browser renders token/trace in real-time
```

### Policy

`policy.yaml` is evaluated at startup. Structure:

```yaml
agent: analyst-bot
default: deny
rules:
  - tool: db_query
    allow: true
    when: "context.get('rows', 100) <= 100"
```

`when` expressions are evaluated with `eval()` in a restricted scope with only `context` available.

### Auth & RBAC (two-layer authorization)

The app requires login (signed session cookie; users in SQLite, argon2id hashes). Authorization composes two layers with AND:

1. **RBAC** (`roles.yaml`, `zta/rbac.py`) — *who*: does the role get this page / tool / table? Roles: `manager`, `sales`, `catalog`.
2. **Policy** (`policy.yaml`, `zta/policy.py`) — *what*: is the specific call safe (e.g. SELECT-only)? Stays role-agnostic.

Enforcement points:
- **Pages** — FastAPI route guards (`_current_user` + `page_allowed`); unauthenticated → redirect `/login` (HTML) or `401` (API); wrong role → `403`.
- **Tools** — `Agent.tool` runs `permissions.tool_allowed(role, name)` before `policy.decide()`.
- **Tables** — `db_query` is built per request scoped to the role's `tables_allowed`; a SQLite `set_authorizer` denies out-of-scope `SQLITE_READ` at the engine level, surfaced as `RbacError` → recorded as a deny.

Admin-only pages: `/users` (manage accounts) and `/roles` (view the matrix). Edit `roles.yaml` to change permissions. Keep `policy.yaml` / `zta/policy.py` role-agnostic.

## Testing Approach

Tests use `FakeChatModel` / `FakeStreamingChatModel` (deterministic, no OpenAI calls) and `respx` for HTTP mocking. `conftest.py` auto-sets `ZTA_ENV=test` and `ZTA_LOG_LEVEL=WARNING` for all tests.

Coverage minimum: 70% (`fail_under=70` in `pyproject.toml`), target ~80%.

## Environment Variables

| Variable | Purpose |
|---|---|
| `ZTA_OPENAI_API_KEY` | Required — passed to `ChatOpenAI` |
| `ZTA_OPENAI_MODEL` | Default: `gpt-4o-mini` |
| `ZTA_POLICY_PATH` | Path to `policy.yaml` |
| `ZTA_AUDIT_PATH` | Path to `audit.jsonl` |
| `ZTA_KEY_DIR` | Directory for Ed25519 PEM files (default `.zta/`) |
| `ZTA_DB_PATH` | Path to SQLite database (Chinook demo data **and** the `users` table) |
| `ZTA_SECRET_KEY` | Signs session cookies; ephemeral if unset (sessions reset on restart) |
| `ZTA_ROLES_PATH` | Path to `roles.yaml` (RBAC matrix) |
| `ZTA_ENV` | Environment label (`dev`, `test`, `prod`) |
| `ZTA_LOG_LEVEL` | Log verbosity |

## CI

Three sequential jobs on push/PR to `main`: **lint** (ruff) → **typecheck** (mypy strict) → **test** (pytest, uploads `coverage.xml` artifact). Python 3.11 required.
