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
          │ HTML form       │ inline render    │ JS poll
          ▼                  ▼                  ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI service (app.py) + Jinja2 templates             │
│                                                          │
│  GET  /              → render chat.html                  │
│  POST /chat          → OpenAI + zta.session → trace      │
│  GET  /audit         → render audit.html                 │
│  GET  /api/audit     → JSON (for live polling)           │
│  GET  /policy        → render policy.html (read-only)    │
│                                                          │
│  zta.session() wraps every OpenAI function call         │
└─────────────────┬────────────────────────────────────────┘
                  │ in-process
        ┌─────────┴────────────────────────┐
        │  zta (Python library)            │
        │   identity  — Ed25519 keypair    │
        │   policy    — YAML rule engine   │
        │   audit     — JSONL hash chain   │
        │   tools     — @tool decorator    │
        │   runtime   — session() + Agent  │
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

### 3.6 `app.py` — FastAPI + OpenAI + Jinja2

**Responsibility:** Wires it all together. One file.

**Endpoints:**
| Method | Path         | Purpose                                       |
|--------|--------------|-----------------------------------------------|
| GET    | `/`          | Chat page (Jinja2)                            |
| POST   | `/chat`      | Run one user message through OpenAI + zta     |
| GET    | `/audit`     | Audit page (renders all events)               |
| GET    | `/api/audit` | JSON of all events (for JS polling)           |
| GET    | `/policy`    | Policy page (renders policy.yaml)             |

**Chat flow:**
1. Receive `{"messages": [{"role": "user", "content": "..."}]}`
2. OpenAI Chat Completions call with function-calling schema for `db_query`, `db_write`
3. If OpenAI returns a function call:
   a. `with zta.session(...) as agent: agent.tool(name, **args)`
   b. Append result to messages
   c. Call OpenAI again so it can produce a final answer (or another tool call)
4. Loop until OpenAI returns no function call
5. Render `chat.html` with: final assistant message + `agent.trace` (inline tool call list)

**Tools registered (in `app.py`):**
- `db_query(sql: str)` — SELECT against `data.db` (SQLite, read-only)
- `db_write(sql: str)` — INSERT/UPDATE/DELETE against `data.db`

The actual SQLite operations live in `app.py` (not in `zta.tools`) because they are app-specific. `zta.tools` only provides the decorator + registry.

### 3.7 Templates and Static

- `templates/base.html` — shared layout, navigation
- `templates/chat.html` — message list, input form, inline trace panel
- `templates/audit.html` — table of events with hash chain
- `templates/policy.html` — pretty-printed policy.yaml
- `static/style.css` — minimal CSS (no framework)
- `static/app.js` — polls `/api/audit` every 3s when on audit page

---

## 4. Data Flow (one chat turn)

```
User submits message "show top 5 customers"
        │
        ▼
POST /chat { messages: [...] }
        │
        ▼
OpenAI Chat Completions
   tools: [db_query, db_write]
        │
        ▼ (OpenAI picks tool_call)
{ name: "db_query", args: { sql: "SELECT * FROM customers LIMIT 5" } }
        │
        ▼
with zta.session(agent="analyst-bot", policy=Path("policy.yaml"),
                 audit=Path("audit.jsonl"), key_dir=Path("~/.zta/keys")) as agent:
    result = agent.tool("db_query", sql="SELECT * FROM customers LIMIT 5")
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
ToolResult(ok=True, value=[{...}, {...}, ...])
        │
        ▼ (appended as tool message to OpenAI)
OpenAI Chat Completions (second call)
        │
        ▼ (no more tool calls → returns content)
"Here are the top 5 customers: ..."
        │
        ▼
Render chat.html with messages + agent.trace
```

---

## 5. Error Handling

| Condition                       | Behavior                                            |
|---------------------------------|-----------------------------------------------------|
| Missing OpenAI API key          | `app.py` startup fails with clear message           |
| `policy.yaml` not found         | `PolicyError` → `/chat` returns 500 with explanation |
| `policy.yaml` parse error       | `PolicyError` → `/chat` returns 500                |
| `~/.zta/keys/{agent}.pem` missing | auto-create on first `session()` call            |
| Tool raises exception           | `ToolResult(ok=False, error=str(exc))`, audit as `error` decision |
| `audit.jsonl` tampered          | `verify_chain()` returns False; UI shows a banner   |
| OpenAI API call fails           | 500 with OpenAI error message                       |
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
- **Contract tests** for `app.py` using `TestClient` (FastAPI) + `respx` for OpenAI mocking.
  - `tests/test_app.py` — chat flow with mocked OpenAI; allow tool; deny tool; audit endpoint
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
│   └── runtime.py
├── tests/
│   ├── conftest.py
│   ├── test_identity.py
│   ├── test_policy.py
│   ├── test_audit.py
│   ├── test_tools.py
│   ├── test_runtime.py
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
