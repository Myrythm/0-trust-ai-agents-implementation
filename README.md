# Zero Trust for AI Agents

A Zero Trust control plane for AI agents that gates every tool invocation through declarative policies and records the outcome in a tamper-evident audit trail. The chat runtime is built on a LangGraph `StateGraph` that streams the model's response and live ZTA decisions to the browser via Server-Sent Events.

- **Policy-first:** every tool call passes through a YAML policy engine before execution.
- **Auditable:** every decision (allow, deny, error, pending) is written to a SHA-256 hash-chained JSONL log.
- **Streaming UI:** token-level model output and live tool traces appear in the browser in real time.
- **Portable:** single Python process, SQLite for the demo DB, no Docker, no infrastructure.

## Features

- ✅ **LangGraph workflow** — explicit `call_model` / `zta_tools` graph nodes instead of a hand-rolled ReAct loop.
- ✅ **Native LangChain tools** — `db_query`, `db_write`, `echo` registered as LangChain `BaseTool`s.
- ✅ **Token-level streaming** — `POST /chat/stream` emits `token`, `trace`, `end`, and `error` SSE events.
- ✅ **Cryptographic agent identity** — Ed25519 keypair per agent, stored as 0600-permission PEM.
- ✅ **Hash-chained audit log** — `audit.jsonl` with SHA-256 `prev_hash` → `this_hash` chain; tampering is detectable.
- ✅ **Deny-by-default policy** — missing rules, missing `when` matches, or rule errors fall through to deny.
- ✅ **Self-contained** — SQLite from the stdlib, no external DB, no Docker, no cloud.

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Seed the demo database (5 customers, 10 orders)
python examples/seed_db.py

# 3. Configure environment variables
export ZTA_OPENAI_API_KEY=sk-...
export ZTA_DB_PATH=./data.db

# 4. Run the server
uvicorn app:app --reload

# 5. Open the application
http://localhost:8000
```

A `.env.example` template is provided; copy it to `.env` and the app picks up the values via `python-dotenv` without needing `export`.

## Try the Demo

1. **Chat:** `show all customers`
   The assistant response streams in token by token. ChatOpenAI calls `db_query("SELECT * FROM customers")`; the policy engine allows the action and the trace entry appears live; the agent returns a summarized response.

2. **Chat:** `delete all customers`
   ChatOpenAI calls `db_write("DELETE FROM customers")`; the policy engine denies the action and the deny trace entry appears live; the agent explains that the operation is not permitted.

3. **Visit `/audit`**
   Review allow/deny decisions recorded in the append-only audit log. The page polls `/api/audit` every 3 seconds and shows chain validity.

4. **Visit `/policy`**
   Inspect the rendered policy rules and the underlying YAML configuration.

## Architecture (TL;DR)

```text
Browser (Jinja2 + SSE)
  -> FastAPI (app.py)
    -> LangGraph StateGraph (zta.agent_graph)
      -> call_model node (ChatOpenAI with native tools)
      -> zta_tools node
        -> agent.tool(...) -> Policy.decide -> Audit.append
        -> Tool execution via ToolRegistry
    -> Identity attaches agent identity
```

### Core Zero Trust Flow

```text
User Request
      |
      v
AI Agent
      |
      v
Identity Layer
      |
      v
Policy Engine
      |
   Allow?
   /    \
 Yes     No
  |       |
  v       v
Tool    Deny
Call    Action
  |
  v
Audit Trail
```

Every tool invocation is evaluated before execution and recorded after the decision is made, regardless of whether the action is allowed or denied.

## LangGraph Workflow

The chat runtime is a compiled `StateGraph` defined in `zta/agent_graph.py`. The graph makes the model/tool/model orchestration explicit and the Zero Trust enforcement a first-class graph node.

```text
                START
                  |
                  v
            +-----------+
            | call_model|  <-- ChatOpenAI.bind_tools(tools)
            +-----+-----+      returns AIMessage
                  |
       tool_calls?|---no---> END
                  |yes
                  v
          +-------------+
          |  zta_tools  |  <-- for each tool_call:
          +------+------+      agent.tool(name, **args)
                 |                -> policy.decide() -> audit.append()
                 |                -> execute tool (if allowed)
                 v
        (loop back to call_model)
```

**State schema** (`AgentState`):

```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    trace: Annotated[list[TraceEntry], operator.add]
```

**Node responsibilities:**

- `call_model` — binds the LangChain tools to the chat model and invokes it with the current message list.
- `zta_tools` — iterates `AIMessage.tool_calls`, routes each through `agent.tool(name, **args)` so policy and audit run before execution, and dispatches a `zta_trace` custom event for streaming UIs.

## Project Structure

```
0-trust-ai-agents/
├── app.py                      # FastAPI service: /, /chat, /chat/stream, /audit, /policy
├── policy.yaml                 # Declarative policy (deny-by-default)
├── pyproject.toml              # hatchling build + ruff/mypy/pytest config
├── zta/
│   ├── __init__.py
│   ├── identity.py             # Ed25519 keypair load/create/sign/verify
│   ├── policy.py               # YAML rule engine with `when` expression
│   ├── audit.py                # Append-only JSONL log with SHA-256 hash chain
│   ├── tools.py                # @tool decorator + ToolRegistry
│   ├── runtime.py              # session() + Agent.tool() (the Zero Trust surface)
│   └── agent_graph.py          # LangGraph StateGraph builder
├── tests/
│   ├── test_identity.py
│   ├── test_policy.py
│   ├── test_audit.py
│   ├── test_tools.py
│   ├── test_runtime.py
│   ├── test_agent_graph.py
│   └── test_app.py
├── examples/
│   └── seed_db.py              # Creates demo customers + orders tables
├── templates/
│   ├── base.html
│   ├── chat.html               # JS-driven streaming chat UI
│   ├── audit.html              # Audit table with chain-validity banner
│   └── policy.html             # Pretty-printed policy.yaml
├── static/
│   ├── style.css
│   └── app.js                  # Chat SSE handler + audit polling
├── docs/
│   └── superpowers/
│       ├── specs/2026-06-18-zta-mvp-design.md
│       └── plans/2026-06-18-implementation-index.md
└── .github/workflows/ci.yml    # ruff + mypy + pytest
```

## Configuration

All configuration is read from environment variables (or a local `.env` loaded by `python-dotenv`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZTA_OPENAI_API_KEY` | _(required)_ | OpenAI API key used by `ChatOpenAI` |
| `ZTA_OPENAI_MODEL` | `gpt-4o-mini` | OpenAI chat model name |
| `ZTA_DB_PATH` | `./data.db` | SQLite path used by `db_query` / `db_write` |
| `ZTA_POLICY_PATH` | `./policy.yaml` | Policy file path |
| `ZTA_AUDIT_PATH` | `./audit.jsonl` | Append-only audit log path |
| `ZTA_KEY_DIR` | `./.zta/keys` | Directory for Ed25519 agent keypairs |
| `ZTA_ENV` | `dev` | Environment label |
| `ZTA_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## API Reference

| Method | Path | Purpose | Returns |
|--------|------|---------|---------|
| GET    | `/`             | Chat page (Jinja2) | HTML |
| POST   | `/chat`         | Run a message through the graph | JSON `{reply, trace, messages}` |
| POST   | `/chat/stream`  | Stream the graph run as SSE | SSE event stream |
| GET    | `/audit`        | Audit page | HTML |
| GET    | `/api/audit`    | All audit events + chain validity | JSON `{events, chain_valid}` |
| GET    | `/policy`       | Policy page | HTML |

### `POST /chat` request

```json
{
  "messages": [
    {"role": "user", "content": "show all customers"}
  ]
}
```

### `POST /chat/stream` SSE events

| Event   | When | Data |
|---------|------|------|
| `token` | each model token chunk | `{"content": "partial text"}` |
| `trace` | each allow/deny/error tool decision | `{"tool": "...", "decision": "allow\|deny\|error", "args": {...}, "reason": "...", "ok": true\|false, ...}` |
| `end`   | graph reached `END` | `{"done": true}` |
| `error` | setup or runtime error | `{"message": "..."}` |

Example raw SSE chunk:

```text
event: token
data: {"content": "Here"}

event: token
data: {"content": " are"}

event: trace
data: {"tool": "db_query", "decision": "allow", "args": {"sql": "SELECT 1"}, "reason": "SELECT is allowed", "ok": true, ...}

event: end
data: {"done": true}
```

## Library API (`zta/`)

The library is importable independently of the FastAPI demo. Minimal usage:

```python
from pathlib import Path
from zta.runtime import session

with session(
    agent="analyst-bot",
    policy=Path("policy.yaml"),
    audit=Path("audit.jsonl"),
    key_dir=Path("./.zta/keys"),
) as agent:
    result = agent.tool("db_query", sql="SELECT 1")
    # result.ok, result.value, result.error
    # agent.trace populated with TraceEntry objects
```

| Module | Purpose |
|--------|---------|
| `zta.identity`  | Ed25519 keypair load/create/sign/verify per agent |
| `zta.policy`    | YAML rule engine; `decide(agent_id, tool, args)` → `Decision` |
| `zta.audit`     | Append-only JSONL log with SHA-256 hash chain |
| `zta.tools`     | `@tool` decorator + `ToolRegistry` (supports raw callables and `BaseTool`s) |
| `zta.runtime`   | `session()` + `Agent.tool()` — the Zero Trust surface |
| `zta.agent_graph` | `build_zta_graph(model, agent, tools)` — compiled LangGraph app |

## Policy Format

```yaml
agent: analyst-bot        # optional: only applies to this agent_id
default: deny             # deny-by-default; allow is coerced to deny
rules:
  - tool: db_query
    when: "args.sql.strip().lower().split()[0] in ('select', 'with')"
    decision: allow
    reason: "SELECT/WITH queries are allowed for analyst-bot"
  - tool: db_query
    decision: deny
    reason: "non-SELECT on db_query is not allowed"
  - tool: db_write
    decision: deny
    reason: "db_write is disabled for analyst-bot"
```

- Rules are evaluated in order; the first whose `tool` matches and whose `when` returns truthy wins.
- `when` is a Python expression evaluated with `builtins` stripped and only `args` in scope. An expression that raises is skipped and logged; the next rule is tried.
- `default: allow` is silently coerced to `deny` to preserve the deny-by-default invariant.

## Development

```bash
ruff check . && ruff format --check .
mypy zta tests app.py
pytest --cov=zta
```

CI runs linting, type checking, and tests on every push (see `.github/workflows/ci.yml`).

Coverage target: 80% overall; library modules 90%.

## Testing

```bash
pytest                                    # full suite
pytest tests/test_agent_graph.py -v       # graph allow/deny/multi-tool paths
pytest tests/test_app.py -k stream -v     # streaming endpoint SSE parsing
pytest --cov=zta --cov-report=term-missing
```

- `test_agent_graph.py` uses a deterministic `FakeChatModel` to exercise graph paths without any network.
- `test_app.py` uses `FakeStreamingChatModel` + `monkeypatch` for SSE tests; the non-streaming `/chat` tests use `respx` to mock OpenAI HTTP.
- The hash chain integrity is tested by tampering with audit lines and asserting `verify_chain()` returns `False`.

## Goals

- Demonstrate Zero Trust concepts for AI agents.
- Enforce explicit authorization on every tool invocation.
- Maintain a tamper-evident audit trail of agent activity.
- Provide a minimal, understandable reference implementation.
- Serve as a foundation for future integrations with external tools and identity providers.

## Out of Scope (deferred to post-MVP)

- Multi-agent orchestration
- Service-to-service mTLS, OPA, Vault, gVisor, microVMs
- Regulated-industry compliance packs
- Multi-language SDK
- Production hardening (rate limits, multi-tenant isolation, observability backends)

## License

Apache-2.0
