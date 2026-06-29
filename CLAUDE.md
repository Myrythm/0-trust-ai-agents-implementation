# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Zero Trust AI Agents (`zta`) is a Python library and FastAPI demo that enforces cryptographic identity, deny-by-default policy decisions, and hash-chained audit logging on every tool call an LLM agent makes. It uses LangGraph to wire the agent loop explicitly.

## Development Setup

```bash
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
cp .env.example .env                   # fill in ZTA_OPENAI_API_KEY
python examples/seed_db.py             # build data.db from the Chinook dataset (downloads pinned SQL)
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

Six focused modules with a strict dependency order:

| Module | Role |
|---|---|
| `errors.py` | Exception hierarchy (`IdentityError`, `PolicyError`, `AuditError`, `ToolError`, `TokenError`) |
| `identity.py` | Ed25519 keypair lifecycle — `load_or_create`, `sign`, `verify`. Keys stored in `.zta/keys/{agent_id}.pem` |
| `policy.py` | YAML deny-by-default rule engine — `decide(agent, tool, context)` evaluates `when` expressions in a sandboxed `eval` scope |
| `audit.py` | Append-only JSONL log with SHA-256 hash chaining for tamper-evidence |
| `tools.py` | `@tool` decorator + `ToolRegistry` for wrapping functions and LangChain `BaseTool` instances |
| `runtime.py` | `session()` context manager that wires identity + policy + audit into an `Agent` handle |
| `agent_graph.py` | LangGraph `StateGraph` with two nodes: `call_model` (ChatOpenAI with bound tools) and `zta_tools` (policy-gated dispatch) |

### FastAPI demo — `app.py`

Three tool registrations (`_echo`, `_db_query`, `_db_write`) and three response modes:
- `/chat` — JSON, full response
- `/chat/stream` — SSE, token-by-token streaming via LangGraph `astream_events`
- `/audit` and `/api/audit` — HTML and JSON views of the hash-chained log

SSE event types emitted: `token`, `trace`, `error`, `end`.

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
| `ZTA_DB_PATH` | Path to SQLite database |
| `ZTA_ENV` | Environment label (`dev`, `test`, `prod`) |
| `ZTA_LOG_LEVEL` | Log verbosity |

## CI

Three sequential jobs on push/PR to `main`: **lint** (ruff) → **typecheck** (mypy strict) → **test** (pytest, uploads `coverage.xml` artifact). Python 3.11 required.
