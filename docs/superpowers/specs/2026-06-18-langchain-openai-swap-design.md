# Replace OpenAI SDK with LangChain — Design Spec

**Date:** 2026-06-18
**Status:** Approved (via brainstorm dialogue)
**Supersedes scope of:** the OpenAI client wiring in `app.py` introduced by F8 (`docs/superpowers/plans/2026-06-18-f08-openai-integration.md`). F8's plan is kept for reference; this spec is the source of truth for the SDK swap.

---

## 1. Goal

Replace the bare `openai` SDK usage in the demo runtime (`app.py`) with
`langchain-openai`'s `ChatOpenAI`, while keeping the existing manual
tool-calling loop and the ZTA runtime integration unchanged. The demo
remains a single-file monolith (`app.py`); no new modules, no
`AgentExecutor`, no LangChain agents.

Non-goals:
- No changes to the `zta/` library (identity, policy, audit, tools, runtime).
- No LangChain `AgentExecutor` / `create_tool_calling_agent` abstraction.
- No rename of `ZTA_OPENAI_API_KEY` / `ZTA_OPENAI_MODEL` env vars.
- No new files or modules.
- No multi-model / multi-provider abstraction layer.

---

## 2. Architecture (unchanged shape, swapped client)

```
Browser (Jinja2)
  -> FastAPI (app.py)
     -> ChatOpenAI.bind_tools(_TOOL_SCHEMAS).invoke(messages)   [was: OpenAI client]
       -> zta.runtime.session
         -> Policy.decide (F3) -> Audit.append (F4)
         -> Tool call (db_query, db_write, echo) via ToolRegistry (F5)
         -> Identity (F2) signs per agent
```

Only the boxed layer changes: the OpenAI SDK client is replaced by a
LangChain `ChatOpenAI` model. Everything below (ZTA runtime, policy,
audit, tools, identity) is untouched.

---

## 3. Dependency change

`pyproject.toml`:

```diff
-  "openai>=1.30",
+  "langchain-openai>=1.0,<2.0",
```

Pinned to the `1.x` line (latest is `1.3.2` at time of writing, per
`pip index versions` and Context7). `langchain-openai` transitively
pulls `langchain-core` (latest `1.4.7`) and `openai` (the underlying
SDK remains a transitive dependency; we simply stop importing it
directly in `app.py`). `httpx` stays pinned for tests.

---

## 4. Changes to `app.py`

### 4.1 Import

```diff
-from openai import OpenAI
+from langchain_openai import ChatOpenAI
```

### 4.2 Client factory

`_get_openai_client() -> OpenAI` becomes `_get_chat_model() -> ChatOpenAI`:

```python
def _get_chat_model() -> ChatOpenAI:
    api_key = os.environ.get("ZTA_OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500, detail="ZTA_OPENAI_API_KEY environment variable is not set"
        )
    model = os.environ.get("ZTA_OPENAI_MODEL", "gpt-4o-mini")
    return ChatOpenAI(model=model, api_key=api_key)
```

Env var names and the 500-on-missing-key behavior are preserved.

### 4.3 Tool schema

`_TOOL_SCHEMAS` is **unchanged**. LangChain's `ChatOpenAI.bind_tools`
accepts the OpenAI-format dict schema (`{"type":"function","function":{...}}`)
directly, so no rewrite is needed.

### 4.4 Chat loop

`_run_chat_loop` keeps its shape (manual loop, `MAX_TOOL_ITERATIONS=10`,
ZTA `session(...)`, `agent.tool(...)` per call). The only changes are
the call site and the response mapping:

```python
chat = _get_chat_model().bind_tools(_TOOL_SCHEMAS)
with session(...) as agent:
    agent.registry.register(_echo, name="echo")
    agent.registry.register(_db_query, name="db_query")
    agent.registry.register(_db_write, name="db_write")
    for _ in range(MAX_TOOL_ITERATIONS):
        ai = chat.invoke(messages)                       # -> AIMessage
        if not ai.tool_calls:
            messages.append({"role": "assistant", "content": ai.content or ""})
            return messages, ai.content or "", [t.__dict__ for t in agent.trace]
        messages.append(
            {
                "role": "assistant",
                "content": ai.content,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                    for tc in ai.tool_calls
                ],
            }
        )
        for tc in ai.tool_calls:
            result = agent.tool(tc["name"], **tc["args"])   # args already a parsed dict
            tool_content = str(result.value) if result.ok else (result.error or "")
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_content})
raise HTTPException(status_code=500, detail=f"too many tool iterations (>{MAX_TOOL_ITERATIONS})")
```

Simplifications enabled by LangChain (verified via Context7 docs for
`langchain-openai` 1.x):

1. `ai.tool_calls[i]["args"]` is **already a parsed dict** — drop the
   `json.loads(tc.function.arguments)` step the bare SDK required.
2. `chat.invoke(messages)` accepts the **dict-format message list**
   (`[{"role":...,"content":...}]`) as-is — no conversion to
   `HumanMessage`/`AIMessage` objects needed.
3. `bind_tools` accepts the existing OpenAI dict schema — no schema rewrite.

Tool results are still appended as `{"role":"tool","tool_call_id":...,
"content":...}` dicts, which LangChain accepts on the next `invoke`.

### 4.5 Module docstring

Update the F8 sentence to reflect LangChain rather than the bare OpenAI
SDK.

---

## 5. Changes to `tests/test_app.py`

`respx` mocking of `https://api.openai.com/v1/chat/completions` is
**kept** — `ChatOpenAI` hits the same endpoint, so the existing
`_openai_completion` helper and `respx.mock(base_url=...)` setup still
work. Adjustments are limited to:

- The helper and assertions already assert on the JSON `reply` and
  `trace` shape returned by `/chat`, which is unchanged. No test
  rewrite expected unless the response mapping surfaces a divergence.
- Update the module docstring / test names that reference "OpenAI"
  where they describe the integration layer (cosmetic).

If a test fails due to LangChain serializing messages differently on the
second `invoke` (e.g. echoing `tool_calls` back), the fix is local to
the message-dict construction in `_run_chat_loop` and the corresponding
mocked response sequence — no test architecture change.

---

## 6. Changes to `README.md` and `.env.example`

Wording only:

- `README.md`: "uses OpenAI function calling" -> "uses LangChain tool
  calling (`ChatOpenAI`)"; the architecture TL;DR line
  `-> OpenAI Chat Completions (function calling)` becomes
  `-> LangChain ChatOpenAI (tool calling)`. Quickstart env var names
  are unchanged.
- `.env.example`: the `# --- OpenAI (used by F8+) ---` comment becomes
  `# --- LangChain / OpenAI (used by F8+) ---`. Var names unchanged.

---

## 7. Verification

```bash
ruff check . && ruff format --check .
mypy zta tests
pytest --cov=zta
```

All three must be green. CI runs all three on push; the PR is not ready
for review until they pass locally.

---

## 8. Risk & rollback

- **Risk:** LangChain may serialize the assistant message's
  `tool_calls` differently than the bare SDK when echoed back, causing
  the second `invoke` to 400. Mitigation: the message-dict construction
  in 4.4 explicitly rebuilds the OpenAI-style `tool_calls` shape, which
  is what the OpenAI endpoint expects and what `respx` already mocks.
- **Rollback:** single revert of the branch; no schema migrations, no
  data format changes, no `zta/` library impact.
