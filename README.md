# Zero Trust for AI Agents — MVP

In-process Python library that gates every AI agent action through a
declarative policy and writes an append-only audit trail. A FastAPI
service with Jinja2 chat UI is the demo runtime; the analyst agent
uses OpenAI function calling, the ZTA library intercepts each call,
and a SQLite database is the tool surface.

## Status

- Design spec: [`docs/superpowers/specs/2026-06-18-zta-mvp-design.md`](docs/superpowers/specs/2026-06-18-zta-mvp-design.md)
- Implementation plans: [`docs/superpowers/plans/2026-06-18-implementation-index.md`](docs/superpowers/plans/2026-06-18-implementation-index.md)

## Quickstart (after all 12 features land)

```bash
pip install -e ".[dev]"
python examples/seed_db.py
export ZTA_OPENAI_API_KEY=sk-...
python app.py
# open http://localhost:8000
```

## License

Apache-2.0.
