# Zero Trust MVP for AI Agents — Design Spec

**Date:** 2026-06-18
**Status:** Approved (implicit via brainstorm dialogue; awaiting explicit user sign-off on this written spec)
**Supersedes:** None
**Replaces scope of:** `docs/superpowers/plans/2026-06-18-zero-trust-ai-agents.md` (the 539-line, 14-module, 8-phase plan). The original plan is kept for reference only; the MVP is the source of truth for what we build.

---

## 1. Goal

A working, end-to-end demonstrable **minimum viable Zero Trust control for AI agents**, delivered as a small Python project that runs locally with one command. The MVP proves the core idea (intercept every agent action, decide allow/deny, audit) with a real LLM (OpenAI), a real interface (web UI), and real policy + audit artifacts on disk. It is intentionally small, self-contained, and free of infrastructure dependencies (no Docker, no Postgres, no Redis, no microservices).

Non-goals (deferred to post-MVP):
- 14-module full control plane from the original plan
- Multi-agent orchestration
- Service-to-service mTLS, OPA, Vault, gVisor, microVMs
- Regulated-industry compliance packs
- Multi-language SDK
- Production hardening (rate limits, multi-tenant isolation, observability backends)

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser (http://localhost:8000)                         │
│                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   Chat       │  │   Trace      │  │   Audit      │    │
│  │  (send msg)  │  │  (tool calls │  │  (JSONL feed │    │
│  │              │  │   allow/deny)│  │   w/ hash)   │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
└─────────┼──────────────────┼──────────────────┼──────────┘
          │ SSE             │ inline render    │ JS poll
          ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI service (app.py) + Jinja2 templates             │
│                                                          │
│  GET  /              → render chat.html                  │
│  POST /chat          → JSON {reply, trace, messages}     │
│  POST /chat/stream   → SSE {token|trace|end|error}       │
│  GET  /audit         → render audit.html                 │
│  GET  /api/audit     → JSON (for live polling)           │
│  GET  /policy        → render policy.html (read-only)    │
│                                                          │
│  LangGraph StateGraph (zta.agent_graph)                  │
│    call_model → zta_tools → call_model → ...             │
└─────────────────┬────────────────────────────────────────┘
                  │ in-process
        ┌─────────┴────────────────────────┐
        │  zta (Python library)            │
        │   identity  — Ed25519 keypair    │
        │   policy    — YAML rule engine   │
        │   audit     — JSONL hash chain   │
        │   tools     — @tool decorator    │
        │   runtime   — session() + Agent  │
        │   agent_graph — LangGraph graph  │
        └──────────────────────────────────┘
                  │
       audit.jsonl  policy.yaml  ~/.zta/keys/  data.db
       (append-only) (YAML)      (Ed25519)     (SQLite)
```

All in-process. No HTTP inter-service, no Docker, no DB server. SQLite is from the stdlib (`sqlite3`).

---

## 3. Components and Contracts

### 3.1 `zta.identity` — Agent Identity

**Responsibility:** Issue and verify cryptographically rooted agent identities. Ed25519 keypair per agent.

**Public API:**
```python
class Identity:
    @classmethod
    def load_or_create(cls, agent_id: str, key_dir: Path) -> "Identity": ...
    def sign(self, payload: bytes) -> bytes: ...
    @classmethod
    def verify(cls, agent_id: str, payload: bytes, signature: bytes, key_dir: Path) -> bool: ...
    @property
    def public_key_b64(self) -> str: ...
```

**Storage:** `~/.zta/keys/{agent_id}.pem` (private), public key derived from private.
**Failure mode:** Missing key file → `IdentityError`. Bad signature → returns `False` (no exception).

### 3.2 `zta.policy` — Policy Engine

**Responsibility:** Allow/deny decisions based on declarative YAML rules. Deny-by-default.

**Public API:**
```python
class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    PENDING_APPROVAL = "pending_approval"

class Policy:
    @classmethod
    def load(cls, path: Path) -> "Policy": ...
    def decide(self, *, agent_id: str, tool: str, args: dict) -> Decision: ...
    def reason(self) -> str: ...  # human-readable for trace + audit
```

**Policy file format (YAML):**
```yaml
agent: analyst-bot
default: deny
rules:
  - tool: db_query
    when: "args.sql.strip().lower().startswith('select')"
    decision: allow
  - tool: db_query
    decision: deny
    reason: "only SELECT allowed on db_query"
  - tool: db_write
    decision: deny
    reason: "db_write is disabled for analyst-bot"
```

**Failure mode:** YAML parse error → `PolicyError` at load. Rule with no `when` matches all calls to that tool.

**`when` expression safety:** The `when` field is a Python expression evaluated with `eval(when, {"__builtins__": {}}, {"args": args})`. No builtins, no attribute access on arbitrary objects (only on `args` and its values). If the expression raises, the rule is skipped and the error is logged; the next rule is tried. This keeps the rule DSL expressive enough for MVP without making the policy file a code-execution vector beyond what it already is.

### 3.3 `zta.audit` — Audit Trail

**Responsibility:** Append-only event log with SHA-256 hash chain. Genesis hash = 64 zero hex.

**Public API:**
```python
class AuditEvent(BaseModel):
    event_id: str
    ts: str
    agent_id: str
    request_id: str
    action: str       # e.g. "tool.invoke:db_query"
    resource: str     # e.g. "sqlite://data.db"
    decision: str     # "allow" | "deny" | "pending_approval"
    reason: str
    prev_hash: str
    this_hash: str

class Audit:
    def __init__(self, path: Path) -> None: ...
    def append(self, *, agent_id: str, request_id: str, action: str, resource: str, decision: str, reason: str) -> AuditEvent: ...
    def read_all(self) -> list[AuditEvent]: ...
    def verify_chain(self) -> bool: ...
```

**Storage:** `audit.jsonl` (one JSON object per line). Append-only enforced at the API surface (no `update` or `delete` methods).

### 3.4 `zta.tools` — Tool Decorator and Registry

**Responsibility:** Wrap user functions as gated tools. The decorator does NOT enforce policy itself; it is a marker that the runtime reads to know which functions are tools and what their names are.

**Public API:**
```python
def tool(name: str | None = None) -> Callable: ...
class ToolRegistry:
    def register(self, fn: Callable) -> None: ...
    def get(self, name: str) -> Callable: ...
    def list(self) -> list[str]: ...
```

### 3.5 `zta.runtime` — Agent + Session

**Responsibility:** The thin API that the application uses. `zta.session(agent=...)` returns a context manager that handles: identity, policy load, audit setup, and tool invocation gating.

**Public API:**
```python
class Agent:
    def tool(self, name: str, **args) -> ToolResult: ...
    @property
    def trace(self) -> list[TraceEntry]: ...  # populated by tool() calls

class ToolResult:
    ok: bool
    value: Any | None
    error: str | None

@contextmanager
def session(*, agent: str, policy: Path, audit: Path, key_dir: Path) -> Iterator[Agent]: ...
```

**`Agent.tool()` flow:**
1. Generate `request_id` (or inherit from context)
2. `policy.decide(agent_id, tool, args)` → `Decision`
3. If `allow`: invoke the tool function, capture result, audit
4. If `deny`: do NOT invoke the tool, audit, return `ToolResult(ok=False, error=reason)`
5. If `pending_approval`: (not implemented in MVP; behaves like deny with reason)
6. Append to `agent.trace` for UI display

### 3.6 `zta.agent_graph` — LangGraph Workflow

**Responsibility:** Build the agent workflow as a compiled LangGraph `StateGraph`. The graph replaces the manual ReAct loop and makes the model/tool/model orchestration explicit.

**Public API:**
```python
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    trace: Annotated[list[TraceEntry], operator.add]

def build_zta_graph(model: BaseChatModel, agent: Agent, tools: Sequence[BaseTool]) -> CompiledGraph: ...
```

**Graph nodes:**
- `call_model` — binds native LangChain tools to the chat model and invokes it.
- `zta_tools` — for each `AIMessage.tool_call`, calls `agent.tool(name, **args)` so policy enforcement and audit happen before execution.

**Edges:**
- `START → call_model`
- `call_model → zta_tools` if the model emitted `tool_calls`, otherwise `END`
- `zta_tools → call_model`

The graph emits `zta_trace` custom events during streaming so the UI can render allow/deny decisions in real time.

### 3.7 `app.py` — FastAPI + LangGraph + Jinja2

**Responsibility:** Wires it all together. One file.

**Endpoints:**
| Method | Path            | Purpose                                              |
|--------|-----------------|------------------------------------------------------|
| GET    | `/`             | Chat page (Jinja2)                                   |
| POST   | `/chat`         | Run one user message through the graph; return JSON  |
| POST   | `/chat/stream`  | Stream the graph run as SSE (token + trace events)   |
| GET    | `/audit`        | Audit page (renders all events)                      |
| GET    | `/api/audit`    | JSON of all events (for JS polling)                  |
| GET    | `/policy`       | Policy page (renders policy.yaml)                    |

**Chat flow (non-streaming `/chat`):**
1. Receive `{"messages": [{"role": "user", "content": "..."}]}`
2. Convert dict messages to `BaseMessage`s
3. Build and invoke the LangGraph graph
4. Return JSON with final assistant message, full message list, and `agent.trace`

**Chat flow (streaming `/chat/stream`):**
1. Receive `{"messages": [...]}`
2. Build the LangGraph graph
3. Run `graph.astream_events(..., version="v2")`
4. Emit SSE events:
   - `token` — each model token chunk
   - `trace` — each allow/deny/error tool decision
   - `end` — graph finished
   - `error` — setup or runtime error

**Tools registered (in `app.py`):**
- `db_query(sql: str)` — SELECT against `data.db` (SQLite, read-only)
- `db_write(sql: str)` — INSERT/UPDATE/DELETE against `data.db`
- `echo(message: str)` — echo helper

The actual SQLite operations live in `app.py` (not in `zta.tools`) because they are app-specific. `zta.tools` only provides the decorator + registry.

### 3.8 Templates and Static

- `templates/base.html` — shared layout, navigation
- `templates/chat.html` — message list, input form, inline trace panel (JS-driven)
- `templates/audit.html` — table of events with hash chain
- `templates/policy.html` — pretty-printed policy.yaml
- `static/style.css` — minimal CSS (no framework)
- `static/app.js` — chat streaming handler + audit polling every 3s

---

## 4. Data Flow (one chat turn)

### Non-streaming `/chat`

```
User submits message "show top 5 customers"
        │
        ▼
POST /chat { messages: [...] }
        │
        ▼
LangGraph StateGraph
   call_model node → OpenAI Chat Completions with tools [db_query, db_write]
        │
        ▼ (OpenAI picks tool_call)
zta_tools node receives AIMessage(tool_calls=[...])
        │
        ▼
agent.tool("db_query", sql="SELECT * FROM customers LIMIT 5")
        │
        │ policy.decide(agent_id="analyst-bot", tool="db_query",
        │               args={"sql": "SELECT * FROM customers LIMIT 5"})
        │  → ALLOW (matches first rule)
        │
        │ execute db_query → returns rows
        │
        │ audit.append(action="tool.invoke:db_query", decision="allow",
        │             reason="matches rule: db_query + SELECT prefix")
        │
        ▼
ToolMessage(content=rows) appended to state
        │
        ▼
call_model node (second invocation)
        │
        ▼ (no more tool calls → returns content)
"Here are the top 5 customers: ..."
        │
        ▼
JSONResponse {reply, trace, messages}
```

### Streaming `/chat/stream`

The same graph is executed with `graph.astream_events(...)`. The server emits:

- `token` events as the model produces the assistant response
- `trace` events as each tool call is evaluated by policy
- `end` when the graph reaches `END`

The JavaScript chat UI renders each event as it arrives, so the user sees tokens and trace entries appear live.

---

## 5. Error Handling

| Condition                       | Behavior                                            |
|---------------------------------|-----------------------------------------------------|
| Missing OpenAI API key          | `/chat` returns 500; `/chat/stream` emits an SSE `error` event |
| `policy.yaml` not found         | `PolicyError` → `/chat` returns 500 with explanation |
| `policy.yaml` parse error       | `PolicyError` → `/chat` returns 500                |
| `~/.zta/keys/{agent}.pem` missing | auto-create on first `session()` call            |
| Tool raises exception           | `ToolResult(ok=False, error=str(exc))`, audit as `error` decision |
| `audit.jsonl` tampered          | `verify_chain()` returns False; UI shows a banner   |
| OpenAI API call fails           | `/chat` returns 500; `/chat/stream` emits an SSE `error` event |
| SQLite file missing             | `db_query` returns `ToolResult(ok=False, error="...")` |
| Rule regex error                | rule skipped, logged, decision falls through        |

Deny-by-default: a missing or empty `policy.yaml` means every tool call is denied.

---

## 6. Testing Strategy

- **Unit tests per module** (pytest, no network).
  - `tests/test_identity.py` — sign/verify roundtrip, bad signature, missing key
  - `tests/test_policy.py` — allow/deny, deny-by-default, rule matching, malformed YAML
  - `tests/test_audit.py` — append + hash chain, tamper detection, genesis hash
  - `tests/test_tools.py` — decorator + registry
  - `tests/test_runtime.py` — `session()` happy path, deny path, trace population, audit on every call
  - `tests/test_agent_graph.py` — LangGraph graph allow/deny/multi-tool/trace paths
- **Contract tests** for `app.py` using `TestClient` (FastAPI) + fake chat models for deterministic graph tests.
  - `tests/test_app.py` — chat flow; allow/deny tool; audit endpoint; streaming SSE endpoint
- **Coverage target:** 80% overall. Library modules (`zta/`) 90%.
- **Property test (optional, MVP):** `hypothesis` test on `audit` that random event streams produce a valid chain.

---

## 7. File Structure

```
0trust-ai-agents/
├── docs/
│   └── superpowers/
│       ├── plans/
│       │   └── 2026-06-18-zero-trust-ai-agents.md   # original 14-module plan (reference)
│       └── specs/
│           └── 2026-06-18-zta-mvp-design.md         # this file
├── zta/
│   ├── __init__.py
│   ├── identity.py
│   ├── policy.py
│   ├── audit.py
│   ├── tools.py
│   ├── runtime.py
│   └── agent_graph.py
├── tests/
│   ├── conftest.py
│   ├── test_identity.py
│   ├── test_policy.py
│   ├── test_audit.py
│   ├── test_tools.py
│   ├── test_runtime.py
│   ├── test_agent_graph.py
│   └── test_app.py
├── examples/
│   └── seed_db.py
├── templates/
│   ├── base.html
│   ├── chat.html
│   ├── audit.html
│   └── policy.html
├── static/
│   ├── style.css
│   └── app.js
├── app.py
├── policy.yaml
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
└── .github/
    └── workflows/
        └── ci.yml
```

---

## 8. Dependencies

Runtime: `pyyaml`, `cryptography`, `pydantic`, `fastapi`, `uvicorn[standard]`, `jinja2`, `openai`, `httpx`.
Dev: `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `ruff`, `mypy`.
Stdlib: `sqlite3`, `pathlib`, `hashlib`, `hmac`, `base64`, `contextlib`, `json`, `uuid`.

8 runtime + 6 dev. No infrastructure deps.

---

## 9. Demo Flow (acceptance)

After `pip install -e ".[dev]" && python examples/seed_db.py && python app.py`:

1. Open `http://localhost:8000` → chat page renders.
2. Send "show top 5 customers" → response includes a tool-call row: `✓ db_query (allow)`, then a natural-language answer.
3. Send "delete all orders older than 2020" → response includes `✗ db_write (deny: db_write is disabled)`, then the assistant acknowledges it cannot perform the action.
4. Open `http://localhost:8000/audit` → see both events with valid hash chain; `verify_chain()` returns True.
5. Open `http://localhost:8000/policy` → see rendered policy.yaml.

If all five pass, the MVP is demoable.

---

## 10. Out of Scope (deferred)

Everything from the original 14-module plan not listed above, including: Sandbox (M13), MemoryGuard (M8), BehaviorMonitor (M9), DefensiveOps (M11), Governance (M12), I/O Filter (M7), TokenSvc (M2 — separate from Identity), Config Integrity (M10), input/output sanitization, mTLS, OPA, Vault, OTel/Jaeger, Postgres, Redis, Docker, multi-agent, multi-language SDK.

These may return in a post-MVP v2.
