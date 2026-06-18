# Zero Trust for AI Agents — MVP

An implementation of Zero Trust Architecture (ZTA) for AI agents that gates every agent action through declarative policies and records the outcome in an append-only audit trail. A FastAPI service with a Jinja2-based chat interface serves as the demonstration runtime. The analyst agent uses LangChain tool calling with ChatOpenAI, while the Zero Trust layer intercepts and evaluates each tool invocation before execution. A SQLite database acts as the tool surface, enabling policy enforcement, identity attribution, and auditable agent interactions.

The project demonstrates how Zero Trust principles can be applied to LLM agents by enforcing least-privilege access, explicit authorization, and full auditability of tool usage.

## Status

* Design spec: [`docs/superpowers/specs/2026-06-18-zta-mvp-design.md`](docs/superpowers/specs/2026-06-18-zta-mvp-design.md)
* Implementation plans: [`docs/superpowers/plans/2026-06-18-implementation-index.md`](docs/superpowers/plans/2026-06-18-implementation-index.md)

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

## Try the Demo

1. **Chat:** "show all customers"
   ChatOpenAI calls `db_query("SELECT * FROM customers")`; the policy engine allows the action; the agent returns a summarized response.

2. **Chat:** "delete all customers"
   ChatOpenAI calls `db_write("DELETE FROM customers")`; the policy engine denies the action; the agent explains that the operation is not permitted.

3. **Visit `/audit`**
   Review allow/deny decisions recorded in the append-only audit log. The page polls `/api/audit` every three seconds.

4. **Visit `/policy`**
   Inspect the rendered policy rules and the underlying YAML configuration.

## Architecture (TL;DR)

```text
Browser (Jinja2)
  -> FastAPI (app.py)
    -> LangChain ChatOpenAI (tool calling)
      -> zta.runtime.session
        -> Policy.decide (F3) -> Audit.append (F4)
        -> Tool call (db_query, db_write, echo) via ToolRegistry (F5)
        -> Identity (F2) attaches agent identity
```

## Core Zero Trust Flow

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

## Development

```bash
ruff check . && ruff format --check .
mypy zta tests
pytest --cov=zta
```

CI runs linting, type checking, and tests on every push.

## Goals

* Demonstrate Zero Trust concepts for AI agents.
* Enforce explicit authorization on every tool invocation.
* Maintain a tamper-evident audit trail of agent activity.
* Provide a minimal, understandable reference implementation.
* Serve as a foundation for future integrations with external tools and identity providers.

## License

Apache-2.0
