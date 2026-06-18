# Modern UI (terminal/security-tool) — Design Spec

**Date:** 2026-06-18
**Status:** Approved (via brainstorm dialogue)

---

## 1. Goal

Refresh the ZTA demo frontend to a modern, dark, terminal/security-tool aesthetic without using AI-slop patterns (no purple-blue gradients, no glassmorphism, no excessive rounded-2xl, no emoji icons). Keep the stack vanilla (CSS + JS, no framework, no new deps) to match the existing pattern and stay anti-slop.

Non-goals:
- No theme toggle / light mode (dark-first only).
- No JS framework, no Tailwind, no new dependencies.
- No `app.py` / `zta/` library changes (routes, data shapes, APIs unchanged).
- No new pages or routes.

---

## 2. Design tokens

Dark-first palette (GitHub-dark inspired, proven legible for data-dense security UIs):

```
--bg:        #0d1117   (canvas)
--panel:     #161b22   (cards, header, table head)
--border:    #21262d   (1px tegas, no glow)
--border-2:  #30363d   (hover/active)
--fg:        #e6edf3
--muted:     #7d8590
--faint:     #484f58
--accent:    #2f81f7   (links, primary button, user msg border)
--allow:     #3fb950
--deny:      #f85149
--pending:   #d29922
--error:     #db6d28
```

Fonts: `ui-monospace, "SF Mono", Menlo, monospace` for data (tool names, SQL, YAML, hashes, request IDs, timestamps); `system-ui, -apple-system, sans-serif` for body copy. Radius 4px (not 2xl). Borders 1px solid, no shadows except optional 0 1px 0 inset for header depth. No gradients anywhere.

---

## 3. Layout

- **Header:** sticky top, `--panel` bg, bottom border. Left: monospace logo `ZTA//` with a small status dot (green = chain valid on audit page, neutral elsewhere). Right: nav Chat / Audit / Policy, active link gets accent underline + accent text.
- **Main:** max-width 960px (up from 800 for data density), centered, `--bg` canvas, `--fg` text.
- **Sections:** panels are `--panel` bg with 1px `--border`, radius 4px, padding 1rem-1.25rem. No card shadows.

---

## 4. Components

### 4.1 Chat (`chat.html`)
- Input: full-width `<textarea>` in a panel, monospace, `--bg` fill, `--border`. Button "Send" right-aligned, `--accent` bg, mono uppercase 0.75rem, radius 4px.
- Messages: each in a panel row, 4px left border. User = `--accent`, assistant = `--muted`. Role label uppercase 0.7rem mono `--muted`. Content in body font, preserves whitespace via `white-space: pre-wrap`.
- Trace: compact rows, mono. Badge decision + `code.tool` + `reason`. allow row gets subtle green left tint, deny row red tint.

### 4.2 Audit (`audit.html`)
- Banner: full-width, 4px left border + tinted bg. ok = green, error = red. Mono text, uppercase verdict.
- Table: data-dense, `--panel` header row uppercase 0.7rem `--muted`, rows 0.85rem. Mono columns: ts, action (code), request_id, hash (truncated 12ch + ellipsis). Decision badge. Reason truncated with ellipsis. Row hover = `--bg` subtle.
- Polling JS unchanged in logic; row HTML template updated to match new classes.

### 4.3 Policy (`policy.html`)
- Scope banner (ok style).
- Default line: sentence + badge.
- Rules: ordered list, each `<li>` panel-ish (border-bottom), `code.tool` mono + badge + `when` chip (mono, `--bg` fill, 1px border). Reason below in `--muted`.
- Raw YAML: `<pre>` `--panel` or deeper `#010409` bg, mono, 0.85rem, horizontal scroll. Keep as-is content-wise.

---

## 5. Files

```
static/style.css              # full rewrite (dark tokens, components)
static/app.js                 # minor: row template matches new classes; polling logic unchanged
templates/base.html           # header structure (logo, nav, status dot), dark meta, mono font link
templates/chat.html           # markup tweaks (panels, whitespace-pre-wrap)
templates/audit.html          # markup tweaks (banner, table classes)
templates/policy.html         # markup tweaks (panels, chips)
```

No `app.py` changes. No `zta/` changes. No new files.

---

## 6. Verification

- `ruff check . && ruff format --check .` (no Python touched, should stay green)
- `mypy zta tests` (unchanged)
- `pytest --cov=zta` (existing tests assert on HTTP 200 + substring presence like "ZTA Chat" — keep those substrings present so tests stay green; verify by running suite)
- Manual: load `/`, `/audit`, `/policy` in a browser, confirm dark theme renders, no console errors, audit polling still updates.

---

## 7. Risk

- Test `test_index_returns_service_metadata` asserts `"ZTA Chat" in resp.text` — keep the `ZTA Chat` string in `chat.html` `<h1>` (can restyle, not rename).
- Audit banner text `"CHAIN TAMPERED"` and `"Chain valid"` asserted in tests — keep exact strings.
- Policy page asserts presence of agent scope / default badge / rules — keep the data, restyle freely.
