# Zero Trust for AI Agents — MVP

In-process Python library that gates every AI agent action through a
declarative policy and writes an append-only audit trail. A FastAPI
service with Jinja2 chat UI is the demo runtime; the analyst agent
uses LangChain tool calling (ChatOpenAI), the ZTA library intercepts each call,
and a SQLite database is the tool surface.

## Status

- Design spec: [`docs/superpowers/specs/2026-06-18-zta-mvp-design.md`](docs/superpowers/specs/2026-06-18-zta-mvp-design.md)
- Implementation plans: [`docs/superpowers/plans/2026-06-18-implementation-index.md`](docs/superpowers/plans/2026-06-18-implementation-index.md)

## Quickstart

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Seed the demo database (5 customers, 10 orders)
python examples/seed_db.py

# 3. Set your OpenAI key
export ZTA_OPENAI_API_KEY=sk-...
export ZTA_DB_PATH=./data.db

# 4. Run the server
uvicorn app:app --reload

# 5. Open http://localhost:8000
```

## Try the demo

1. **Chat:** "show all customers" — ChatOpenAI calls `db_query("SELECT * FROM customers")`; policy allows; LLM returns a summary.
2. **Chat:** "delete all customers" — ChatOpenAI calls `db_write("DELETE FROM customers")`; policy denies; LLM acknowledges it cannot.
3. **Visit `/audit`** — see the allow/deny events with the hash chain banner. The page polls `/api/audit` every 3s.
4. **Visit `/policy`** — see the rendered policy rules and the raw YAML.

## Architecture (TL;DR)

```
Browser (Jinja2)
  -> FastAPI (app.py)
    -> LangChain ChatOpenAI (tool calling)
      -> zta.runtime.session
        -> Policy.decide (F3) -> Audit.append (F4)
        -> Tool call (db_query, db_write, echo) via ToolRegistry (F5)
        -> Identity (F2) signs per agent
```

## Development

```bash
ruff check . && ruff format --check .
mypy zta tests
pytest --cov=zta
```

CI runs all three on every push. See `docs/superpowers/plans/` for
feature-by-feature plans.

## License

Apache-2.0.
