# Zero Trust for AI Agents — Implementation Plan

> **For agentic workers:** This is a HIGH-LEVEL plan by design. Each module is self-contained with a clear contract, so a cheaper/smaller model can implement one module at a time without needing the full picture. Do NOT expand into line-by-line code; fill in each module's contract.

**Goal:** Build a Zero Trust control plane for AI agent deployments — identity, authn, authz, tool gating, isolation, observability, memory protection, and defensive operations — mapped to the Foundation and Enterprise tiers of Anthropic's "Zero Trust for AI Agents" framework.

**Architecture:** A modular **control plane** (Python services) plus a **policy-enforcing agent runtime wrapper**. Each control is an independent service/module exposing a small HTTP/gRPC or in-process API. An `AgentRuntime` wraps any agent framework call and routes every action (tool call, memory write, credential use) through the control plane for verification + logging. Policy is data-driven (declarative rules), not hard-coded.

**Tech Stack:**
- Language: **Python 3.11+** (best support in smaller models; rich ecosystem)
- Services: **FastAPI** + **uvicorn**; inter-service via HTTP/JSON (simple for cheap models)
- Auth: **OAuth 2.0 / OIDC** tokens (short-lived, minutes); **mTLS** for service-to-service at Enterprise
- Storage: **PostgreSQL** (metadata, audit), **Redis** (token cache, rate limits), **S3-compatible** (immutable audit log, signed configs)
- Isolation: **Docker** containers (Foundation/Enterprise); optional **gVisor / microVM** (Advanced)
- Observability: **OpenTelemetry** SDK + **Jaeger/Tempo**; logs to append-only store
- Secrets: **HashiCorp Vault** (or cloud managed identity) — never in code/config
- Policy: **OPA / Rego** (Enterprise) or in-process Python rule engine (Foundation)
- Crypto: `cryptography` lib (X.509 certs, signing, hashing); hardware binding optional via PKCS#11/TPM

**Source framework:** Anthropic eBook "Zero Trust for AI Agents" (05182026) — 3 tiers (Foundation / Enterprise / Advanced). This plan targets **Foundation + Enterprise**. Advanced capabilities are noted as stretch goals.

---

## Assumptions & Scope

1. We are building the **Zero Trust control plane + runtime wrapper**, not a new LLM or agent framework. It wraps existing agents (e.g. Claude, OpenAI, LangGraph, MCP clients).
2. Target tier maturity: **Foundation (mandatory) + Enterprise (target)**. Advanced items are marked `(Advanced)` and can be deferred.
3. One agent = one identity = one scoped credential set. Multi-agent systems get per-agent identities (no shared service accounts).
4. Regulated-industry compliance (HIPAA/FINRA/GDPR/FedRAMP/EU AI Act) is supported via the audit + governance modules, but specific compliance packs are out of scope for v1.
5. The plan is **modular and parallelizable**: modules listed under the same phase can be built concurrently by different workers because their contracts are fixed up front.

---

## Architecture Overview

```
                         ┌──────────────────────────────────────────┐
                         │           Agent Application              │
                         │  (LangGraph / Claude / MCP client / ...) │
                         └───────────────────┬──────────────────────┘
                                             │ all actions go through
                                             ▼
                         ┌──────────────────────────────────────────┐
                         │           AgentRuntime (SDK)             │
                         │  pre-hooks + post-hooks around every     │
                         │  tool call, memory op, credential use    │
                         └───────────────────┬──────────────────────┘
                                             │ verifies + logs
             ┌───────────────────────────────┼───────────────────────────────┐
             ▼                               ▼                               ▼
   ┌──────────────────┐         ┌────────────────────┐         ┌────────────────────┐
   │ IdentitySvc      │         │ PolicyEngine        │         │ ToolGateway         │
   │ (agent IDs,      │         │ (RBAC/ABAC, JIT,    │         │ (allow-list, param  │
   │  cert lifecycle) │         │  deny-by-default)   │         │  validation, sandbox)│
   └──────────────────┘         └────────────────────┘         └────────────────────┘
             │                               │                               │
             ▼                               ▼                               ▼
   ┌──────────────────┐         ┌────────────────────┐         ┌────────────────────┐
   │ TokenSvc         │         │ MemoryGuard         │         │ AuditSvc            │
   │ (OAuth2, mTLS,   │         │ (isolation,         │         │ (immutable logs,    │
   │  refresh, revoc) │         │  integrity, TTL)    │         │  tracing, provenance)│
   └──────────────────┘         └────────────────────┘         └────────────────────┘
                                             │
   ┌──────────────────┐         ┌────────────────────┐         ┌────────────────────┐
   │ InputOutputFilter│         │ BehaviorMonitor     │         │ ConfigIntegrity     │
   │ (sanitization,   │         │ (baselines,         │         │ (signed configs,    │
   │  PII, output)    │         │  anomaly, drift)    │         │  rollback, attestation)│
   └──────────────────┘         └────────────────────┘         └────────────────────┘
                                             │
                         ┌───────────────────┴──────────────────────┐
                         │  DefensiveOps (SOAR triage, alerting)    │
                         │  Governance (policies, approvals)        │
                         └──────────────────────────────────────────┘
```

**Data flow per agent action:**
1. Agent wants to call tool `T` with args `A`, or write memory `M`, or use credential `C`.
2. `AgentRuntime` intercepts → calls `IdentitySvc.WhoAmI` → `PolicyEngine.Decide` → `ToolGateway` (or `MemoryGuard`, `TokenSvc`).
3. On allow: execute in sandbox, emit OpenTelemetry span + audit event.
4. `InputOutputFilter` validates inputs before and outputs after.
5. `BehaviorMonitor` scores the action against baseline; on anomaly → `DefensiveOps` (alert / contain / revoke).
6. All events land in `AuditSvc` (append-only, integrity-tagged).

---

## Module Contracts (the spec each module must satisfy)

Each module below is **one unit of work** for an implementer. Contract = responsibility + public interface + data model + dependencies + acceptance criteria + tier.

### M1 — Agent Identity Service (`identity/`)

**Responsibility:** Issue and lifecycle-manage cryptographically rooted agent identities. Foundation = unique IDs + key material; Enterprise = X.509 certs with rotation/revocation.

**Public API (FastAPI):**
- `POST /agents` → register agent, return `{agent_id, public_key, cert?}`
- `GET /agents/{id}` → identity record
- `POST /agents/{id}/rotate` → rotate key/cert
- `POST /agents/{id}/revoke` → revoke identity (CRL/OCSP update)
- `GET /agents/{id}/attest` `(Advanced)` → remote attestation challenge

**Data model (Postgres `identity` schema):** `agent_id (uuid, pk)`, `name`, `public_key`, `cert_pem`, `status {active|revoked}`, `created_at`, `retired_at`, `parent_agent_id` (for sub-agents).

**Dependencies:** `cryptography` (X.509), internal CA (Foundation: self-signed root; Enterprise: managed CA or Vault PKI).

**Acceptance criteria:**
- Two agents get distinct `agent_id` + distinct key material.
- A revoked agent's cert fails verification in TokenSvc/ToolGateway within 1 minute.
- Every identity op emits an audit event.
- IDs appear in all logs and access requests (verify in AuditSvc).

**Tier:** Foundation (unique crypto IDs) → Enterprise (X.509 + lifecycle) → Advanced (HSM/TPM + attestation).

---

### M2 — Token Service (`token/`)

**Responsibility:** Issue short-lived OAuth2 tokens bound to an agent identity; auto-refresh; revoke. No static API keys accepted anywhere downstream.

**Public API:**
- `POST /token` (client_credentials grant, mTLS or signed JWT assertion) → `{access_token, expires_in ≤ 900s}`
- `POST /token/revoke` → revoke by `jti` or `agent_id`
- `POST /token/introspect` → return `{active, agent_id, scope, exp}`

**Data model:** issued tokens in Redis (jti → claims, TTL = exp); revocation list in Postgres.

**Dependencies:** IdentitySvc (verify caller), `authlib` / `python-jose`, Redis.

**Acceptance criteria:**
- Token TTL ≤ 15 minutes, refresh happens without human intervention.
- A revoked token's `introspect` returns `active=false` within 10 seconds.
- Endpoint rejects static API keys and shared passwords (no password grant).
- All token issuance/revocation is logged with `agent_id`.

**Tier:** Foundation (short-lived tokens + refresh) → Enterprise (mTLS + cert pinning) → Advanced (hardware-bound issuance).

---

### M3 — Policy Engine (`policy/`)

**Responsibility:** Deny-by-default authorization. RBAC (Foundation) → ABAC with context (Enterprise) → continuous authorization (Advanced). Enforces **Least Agency**: per-tool, per-action, per-resource scoping + JIT elevation.

**Public API:**
- `POST /decide` body `{agent_id, action, resource, context{time, location, risk, sensitivity}}` → `{decision: allow|deny|step_up, obligations:[...], expires_at?}`
- `POST /policies` (admin) → upload/validate policy document
- `GET /policies/{agent_id}` → effective policy

**Policy document format (declarative):** YAML/JSON, e.g.
```yaml
agent: customer-service-bot
allow:
  - tool: crm.read
    resources: ["customers:*"]
    when: { hours: "09-17", risk: "<high" }
  - tool: email.draft
    require_approval: true
deny: ["*"]   # deny-by-default
```

**Data model:** policies table (versioned), decision log (every decision, even denies).

**Dependencies:** IdentitySvc, TokenSvc (for `step_up`), OPA sidecar (Enterprise) or in-process rules (Foundation).

**Acceptance criteria:**
- An action not explicitly allowed is denied (deny-by-default proven by tests).
- ABAC: same agent allowed at 10:00, denied at 23:00 when policy restricts hours.
- JIT: an elevated scope expires automatically after task completion or timeout (verify expiry fires).
- Every decision is logged with full context + policy version.

**Tier:** Foundation (RBAC deny-by-default) → Enterprise (ABAC context-aware) → Advanced (continuous, real-time re-eval).

---

### M4 — Tool Gateway (`toolgateway/`)

**Responsibility:** Single chokepoint for all tool/MCP invocations. Allow-listing, capability restrictions, parameter validation, sandboxed execution, rate limits/circuit breakers, approval escalation for high-risk tools.

**Public API:**
- `POST /invoke` body `{agent_id, tool, args, request_id}` → tool result or `pending_approval` or `denied`
- `GET /tools/{agent_id}` → allowed tools + capabilities
- `POST /tools/approve/{request_id}` → human approval for escalated call

**Controls:**
- Allow-list per agent (deny unlisted).
- Capability subset per tool (e.g. email tool = read only by default; `send` requires step-up).
- Parameter schema validation (JSON Schema) on both agent-side and tool-side.
- Sandbox: run tool in Docker container with restricted caps, limited mounts, egress allow-list.
- Rate limit + spend cap per agent per tool; circuit breaker on threshold breach.

**Data model:** tool registry (name, schema, capabilities, sandbox profile), invocation log, approval queue.

**Dependencies:** PolicyEngine (decision), TokenSvc (tool-side auth), Docker SDK, InputOutputFilter (validate args + results).

**Acceptance criteria:**
- Unlisted tool invocation is denied and logged.
- Tool called with out-of-schema args is rejected before execution.
- `email.send` without approval returns `pending_approval`; after `/approve` it executes.
- A tool run in sandbox cannot write outside its mount or reach blocked egress.
- Rate limit triggers circuit breaker after N calls/min (logged).

**Tier:** Foundation (allow-list + param validation + sandbox) → Enterprise (capability RBAC + circuit breakers + ABAC) → Advanced (microVM + attested tools).

---

### M5 — Agent Runtime SDK (`runtime/`)

**Responsibility:** The thin wrapper every agent app imports. Wraps tool calls, memory ops, and credential access with pre/post hooks that call the control plane. Generates request IDs and propagates them. Emits OpenTelemetry spans.

**Public interface (Python package `zta.runtime`):**
- `class AgentRuntime(agent_id, credential_ref)`: constructor loads identity from IdentitySvc.
- `runtime.call_tool(tool, args)` → goes through ToolGateway.
- `runtime.memory_write(key, value)` → goes through MemoryGuard.
- `runtime.get_credential(scope)` → goes through TokenSvc (JIT).
- `runtime.context` → current request_id, traceparent.
- Hook registry: `register_pre_hook(action_type, fn)`, `register_post_hook(...)`.

**Dependencies:** IdentitySvc, TokenSvc, PolicyEngine, ToolGateway, MemoryGuard, AuditSvc clients (thin HTTP).

**Acceptance criteria:**
- A sample agent using the SDK cannot bypass any control plane check (no raw HTTP to tools).
- Every action carries a propagated `request_id` visible in audit logs.
- Adding a new tool requires only a registry entry, not SDK changes.
- SDK has <50ms overhead per action in the happy path.

**Tier:** Cross-cutting; implements Foundation + Enterprise controls via composition.

---

### M6 — Audit Service (`audit/`)

**Responsibility:** Comprehensive, immutable, integrity-verifiable audit trail + distributed tracing + provenance chains. Foundation = logs with timestamps + context; Enterprise = append-only + integrity tags; Advanced = real-time SIEM streaming + full provenance replay.

**Public API:**
- `POST /events` (internal) → append event (idempotent by event_id)
- `GET /events?agent_id=...&request_id=...&from=...&to=...` → query
- `GET /events/{id}/verify` → verify integrity hash chain
- `POST /export` (SIEM streaming config) `(Advanced)`

**Event schema:** `{event_id, ts, agent_id, request_id, traceparent, action, resource, decision, context, prev_hash, this_hash}`. Hash chain = `SHA256(prev_hash || canonical(event))`.

**Storage:** append-only (Postgres with `REVOKE UPDATE/DELETE` + S3 mirror); Redis for hot queries.

**Dependencies:** OpenTelemetry SDK for span emission; S3 client.

**Acceptance criteria:**
- Cannot `UPDATE` or `DELETE` an event (DB permissions enforce).
- Tampering with one event breaks the hash chain at `/verify`.
- A request_id query returns the full ordered action sequence for an agent run.
- Events searchable within 5 seconds of emission.

**Tier:** Foundation (logs + request IDs) → Enterprise (immutable + integrity) → Advanced (SIEM stream + provenance replay).

---

### M7 — Input/Output Filter (`iofilter/`)

**Responsibility:** Validate inputs to agents and filter outputs from agents. Foundation = schema/length + PII/credential pattern scan; Enterprise = known attack-pattern matching + encoded payload detection; Advanced = constitutional classifiers + spotlighting.

**Public API:**
- `POST /input/check` body `{text, source: user|external|tool}` → `{ok, redacted, flags:[...]}`
- `POST /output/check` body `{text, channel}` → `{ok, redacted, blocked_reason?}`
- Spotlighting wrapper: `POST /input/spotlight` → returns untrusted content delimited for the LLM.

**Patterns:** regex set for PII (email, phone, SSN, card), credentials (`AKIA...`, `ghp_...`, `-----BEGIN PRIVATE KEY-----`), known injection markers (`ignore previous instructions`, base64/hex payloads above threshold).

**Dependencies:** regex lib; `(Enterprise)` small classifier or rule pack; `(Advanced)` external classifier API.

**Acceptance criteria:**
- A fake AWS key in output is redacted and logged.
- A base64-encoded injection payload is flagged at Enterprise.
- Spotlighted content is wrapped so downstream prompt clearly marks it untrusted.
- Latency < 50ms per check at Foundation.

**Tier:** Foundation (schema + PII) → Enterprise (attack patterns + encoded payloads) → Advanced (classifiers + spotlighting).

---

### M8 — Memory Guard (`memory/`)

**Responsibility:** Protect agent memory/context from poisoning and cross-session leakage. Session isolation, integrity validation on retrieval, retention TTLs, rollback/quarantine on detection.

**Public API:**
- `POST /memory/{agent_id}/{session_id}` write `{key, value, source, ttl?}` → stores with hash + source tag
- `GET /memory/{agent_id}/{session_id}/{key}` → returns value only if integrity check passes; else quarantines + alerts
- `POST /memory/rollback/{agent_id}` → restore to versioned known-good
- `DELETE /memory/{agent_id}/purge` → admin purge

**Data model:** memory items with `{agent_id, session_id, key, value, source, hash, created_at, expires_at, version}`. Sessions strictly partitioned by `session_id` (no cross-session reads without explicit policy).

**Dependencies:** AuditSvc (log every read/write/quarantine), BehaviorMonitor (flag drift).

**Acceptance criteria:**
- Session A cannot read session B's memory (enforced, tested).
- A modified memory value fails hash check → quarantined + alert fired.
- Expired memory is not returned and is purged by background job.
- Rollback restores a prior version within 30 seconds.

**Tier:** Foundation (session isolation + TTL) → Enterprise (integrity validation + source tagging) → Advanced (provenance + auto-quarantine on drift).

---

### M9 — Behavior Monitor (`behavior/`)

**Responsibility:** Establish baselines of normal agent behavior and detect anomalies/drift. Foundation = documented expected patterns + threshold alerts; Enterprise = statistical baselines + tunable anomaly detection; Advanced = ML + contextual + drift detection.

**Public API:**
- `POST /baseline/{agent_id}` (admin or auto-learned) → store baseline
- `POST /observe` body `{agent_id, action, metrics}` → `{score, anomaly: bool, severity}`
- `GET /drift/{agent_id}` → drift report over time window

**Metrics tracked per agent:** tool usage frequency, call rate, data volume accessed, error rate, output size distribution, decision distribution.

**Dependencies:** AuditSvc (stream of events), Redis (counters), `(Enterprise)` simple stats (z-score, EWMA).

**Acceptance criteria:**
- An agent making 100x normal API calls in a minute triggers a `high` severity anomaly.
- Gradual drift over 7 days is flagged (Enterprise).
- Every anomaly emits an event to DefensiveOps.
- Baseline can be exported/restored (recovery point).

**Tier:** Foundation (thresholds + manual baseline) → Enterprise (statistical + auto-learn) → Advanced (ML + contextual + drift).

---

### M10 — Configuration Integrity (`config/`)

**Responsibility:** Treat agent configs (system prompts, tool allow-lists, permission rules, MCP server lists) as versioned + signed artifacts. Reject unsigned/invalid configs at deploy. Immutable images at Advanced.

**Public API:**
- `POST /configs` (CI) → upload + sign config, returns `{version, sig}`
- `GET /configs/{agent_id}/latest` → returns latest signed config
- `POST /configs/verify` → verify signature + hash before deploy
- `POST /configs/rollback/{agent_id}/{version}` → redeploy prior signed version

**Data model:** configs table `{agent_id, version, content_hash, sig, content, created_by, created_at}`; git-backed mirror.

**Dependencies:** `cryptography` (signing), git, CI hook.

**Acceptance criteria:**
- A config with tampered content fails `/verify`.
- Rollback restores the exact prior version (hash match).
- All config changes attributed to a committer in audit log.
- Deploy pipeline refuses unsigned configs (CI gate).

**Tier:** Foundation (version control + review) → Enterprise (signed + deploy verification) → Advanced (immutable images + attestation).

---

### M11 — Defensive Ops / Agentic SOAR (`defops/`)

**Responsibility:** Put a model at the front of the alert queue. Automated first-pass triage of every anomaly with read-only SIEM access; structured disposition before a human sees it. Containment actions (session terminate, credential revoke) automated only for high-confidence; high-impact requires human approval. Humans stay on containment/disclosure/comms decisions.

**Public API:**
- `POST /alerts` (from BehaviorMonitor/AuditSvc) → enqueues triage
- `GET /alerts/{id}/disposition` → structured `{summary, likely_cause, recommended_actions, confidence}`
- `POST /alerts/{id}/contain` → execute containment playbook (auto for high-confidence, else requires approval)
- `GET /coverage` → MITRE ATT&CK technique coverage map

**Containment actions available:** terminate session (AgentRuntime), revoke credential (TokenSvc), block tool (ToolGateway), isolate sandbox (ToolGateway).

**Dependencies:** BehaviorMonitor, AuditSvc, an LLM client (read-only triage), TokenSvc, ToolGateway, AgentRuntime.

**Acceptance criteria:**
- Every alert gets a structured disposition before human review.
- High-confidence auto-containment revokes a credential within 60 seconds.
- High-impact actions (taking a service offline) require human approval (enforced).
- All defensive agent actions are themselves logged in AuditSvc (Zero Trust applies to defenders too).

**Tier:** Foundation (alert + model-drafted triage) → Enterprise (auto-containment for high-confidence) → Advanced (orchestrated SOAR playbooks + graduated escalation).

---

### M12 — Governance (`governance/`)

**Responsibility:** Documented acceptable-use + incident-response policies (Foundation), cross-functional approval workflow for new agent deployments (Enterprise), automated policy checks in CI (Advanced). Track Shadow AI.

**Public API:**
- `POST /policies/governance` (admin) → upload acceptable-use + IR policy doc
- `POST /deployments/request` → request approval for a new agent deployment
- `GET /deployments/{id}/status` → approval state
- `POST /deployments/{id}/approve` → stakeholder sign-off (multi-role)

**Data model:** governance policies (versioned docs), deployment requests (agent spec, approvals, risk assessment), approval records.

**Dependencies:** ConfigIntegrity (deploy uses signed configs), AuditSvc.

**Acceptance criteria:**
- A new agent cannot deploy without required stakeholder approvals (enforced by CI gate).
- Every approval decision is logged with role + rationale.
- Policy docs are versioned and reviewable.
- Shadow AI: an agent not in the registry is blocked by ToolGateway (cross-module check).

**Tier:** Foundation (documented policies + IR) → Enterprise (formal governance + approvals) → Advanced (automated CI compliance checks).

---

### M13 — Sandbox Runtime (`sandbox/`)

**Responsibility:** Container/microVM execution environment for agent tool calls and untrusted-input processing. Restricted capabilities, limited mounts, egress allow-list, syscall filtering.

**Public API (used by ToolGateway):**
- `POST /sandbox/run` body `{image, cmd, env, mounts, egress_allow, timeout}` → `{exit_code, stdout, stderr, logs}`
- `GET /sandbox/capabilities` → list available sandbox profiles

**Profiles:** `strict` (no network, read-only fs), `web` (egress allow-list only), `data` (specific DB creds injected, no internet).

**Dependencies:** Docker SDK (Foundation/Enterprise), gVisor runtime (Advanced), Firecracker/microVM (Advanced).

**Acceptance criteria:**
- A sandboxed job cannot reach a host on the deny-list.
- A sandboxed job cannot write outside its declared mount.
- Job is killed at timeout; resources reclaimed.
- Per-job resource cap (CPU/mem) enforced.

**Tier:** Foundation (Docker restricted caps) → Enterprise (gVisor + mount/egress limits) → Advanced (SEV/TDX microVM + attestation).

---

### M14 — Shared Lib + Clients (`common/`)

**Responsibility:** Cross-cutting library imported by every module: typed clients for each service, OpenTelemetry setup, audit event builder, error types, config loader (pulls signed configs from M10), request-id propagation.

**Public interface (Python package `zta.common`):**
- `clients.IdentityClient`, `TokenClient`, `PolicyClient`, `ToolClient`, `AuditClient`, `MemoryClient`, `BehaviorClient`
- `telemetry.setup(service_name)` → returns tracer + meter
- `audit.event(...)` → builds + sends event with hash chain helper
- `config.load(agent_id)` → fetches + verifies signed config

**Acceptance criteria:**
- All services use `zta.common` for tracing; a single trace spans an agent action across services.
- A module cannot start with an unsigned/invalid config (fail-closed).
- Clients retry with backoff + circuit breaker.

**Tier:** Cross-cutting (enables every tier).

---

## Implementation Phases (ordered, dependency-aware)

Phases are the build order. Within a phase, modules are independent and can be built in parallel by separate workers. Each phase produces something demoable.

### Phase 0 — Foundations (1 worker, sequential)
- Repo scaffold (Python workspace, `pyproject.toml`, FastAPI app factory, Docker compose for Postgres/Redis).
- M14 `common/` skeleton: config loader stub, telemetry setup, audit event builder, base client class.
- CI: lint (ruff), typecheck (mypy), test (pytest), OpenSSF Scorecard wired in.

### Phase 1 — Identity + Audit (parallel: M1, M6)
The spine everything else needs. Build these two first because every other module emits audit events and verifies identity.
- **M1 IdentitySvc** — crypto IDs, then X.509.
- **M6 AuditSvc** — append-only + hash chain.
- Integration: IdentitySvc emits audit events; AuditSvc records them with `agent_id`.

### Phase 2 — Auth + Policy (parallel: M2, M3)
- **M2 TokenSvc** — short-lived tokens, refresh, revocation; depends on M1.
- **M3 PolicyEngine** — deny-by-default RBAC, then ABAC; depends on M1.
- Integration: TokenSvc mints tokens with scopes from PolicyEngine decisions.

### Phase 3 — Runtime + Tools (parallel: M5, M4, M13)
- **M5 AgentRuntime SDK** — wraps actions, calls control plane; depends on M1/M2/M3/M6.
- **M13 Sandbox Runtime** — Docker profiles; standalone.
- **M4 ToolGateway** — allow-list, param validation, sandbox handoff, approval queue; depends on M3/M13 + M7.
- Integration: a sample agent uses M5 to call a tool through M4, sandboxed by M13, all logged in M6.

### Phase 4 — Memory + Filtering (parallel: M8, M7)
- **M8 MemoryGuard** — session isolation, integrity, TTL, rollback; depends on M6/M9.
- **M7 InputOutputFilter** — schema, PII, attack patterns, spotlighting; depends on M6.
- Integration: agent memory writes/reads go through M8; agent inputs/outputs go through M7.

### Phase 5 — Behavior + Config (parallel: M9, M10)
- **M9 BehaviorMonitor** — baselines, threshold + statistical anomalies; depends on M6.
- **M10 ConfigIntegrity** — signed versioned configs, CI gate; depends on M6.
- Integration: all modules load signed configs via M10 (M14 wired in); M9 consumes AuditSvc stream.

### Phase 6 — Defensive Ops + Governance (parallel: M11, M12)
- **M11 DefensiveOps** — alert triage + containment; depends on M9/M6/M2/M4/M5.
- **M12 Governance** — approval workflow + policy docs; depends on M10/M6.
- Integration: M9 anomalies → M11 triage → auto-containment via M2/M4; new deploys need M12 approval.

### Phase 7 — Hardening + Tier Advance
- Add Enterprise-tier depth to each module (mTLS, ABAC context, immutable audit, statistical anomaly, signed configs).
- Add the "impossible vs tedious" design review: replace any friction-only control (rate limits alone, SMS MFA, non-standard ports) with hard barriers (hardware-bound creds, expiring tokens, non-existent network paths).
- MITRE ATT&CK coverage map in M11; tabletop for 5 simultaneous incidents.

### Phase 8 (Stretch, Advanced) — Hardware binding + agentic SOAR
- HSM/TPM-backed identity (M1), attested issuance (M2), continuous authorization (M3), microVM (M13), ML behavior (M9), SOAR playbooks (M11).

---

## Testing Strategy (high level)

- **Unit tests per module** against its contract's acceptance criteria (pytest). Each module's tests live in `tests/<module>/`.
- **Contract tests**: every public API endpoint has a test that asserts the schema + a happy path + a deny/fail path. Shared fixtures in `tests/conftest.py`.
- **Integration tests** (per phase): the "Integration:" line under each phase is a test scenario. Run with `docker compose up` + `pytest tests/integration/phaseN/`.
- **Security tests** (cross-cutting):
  - Deny-by-default: assert every unlisted action is denied.
  - Tamper test: flip a bit in an audit event → hash chain breaks.
  - Sandbox escape test: sandboxed job cannot reach blocked egress or write outside mount.
  - Prompt-injection test: indirect injection payload in external content is flagged by M7 and blocked from influencing behavior via spotlighting.
  - Credential test: a static API key anywhere in the codebase fails CI (grep + pre-commit).
- **Coverage target**: 80% per module; security-critical modules (Policy, ToolGateway, Audit, Memory) 90%.
- **Property tests** where useful (hypothesis): hash chain integrity under random event streams.

---

## Tier Coverage Matrix (verifies spec coverage)

| PDF capability | Module | Foundation | Enterprise | Advanced |
|---|---|---|---|---|
| Agent identity verification | M1 | crypto IDs | X.509 + lifecycle | HSM + attestation |
| Service authentication | M2 | short-lived tokens | mTLS + pinning | hardware-bound |
| Permission models | M3 | RBAC deny-default | ABAC context | continuous authz |
| Privilege scoping | M3 | static least-priv | dynamic adjust | JIT/JEA auto-expire |
| Resource boundaries | M13 + M4 | identity + net seg | sandboxed exec | HW isolation |
| Action logging | M6 | comprehensive logs | immutable + integrity | SIEM stream |
| Traceability | M5 + M6 | request IDs | distributed tracing | full provenance |
| Behavioral monitoring | M9 | manual baseline | statistical | ML + drift |
| Anomaly detection | M9 | threshold alerts | statistical tunable | ML contextual |
| Automated response | M11 | triage + alert | auto-containment | SOAR playbooks |
| Input sanitization | M7 | schema + length | attack patterns | classifiers + spotlight |
| Output filtering | M7 | PII patterns | semantic analysis | human-in-loop high-risk |
| Configuration integrity | M10 | version control | signed + verify | immutable + attestation |
| Recovery | M10 + M8 | documented rollback | auto rollback | self-healing |
| AI governance | M12 | documented policies | formal governance | automated CI checks |
| Supply chain | CI + M10 | OpenSSF Scorecard | signed models/tools | runtime attestation |
| Memory/context poisoning | M8 | session isolation | integrity validation | provenance + auto-quarantine |

**Spec coverage self-check:** every Part III capability and every Part IV phase (1–8) maps to at least one module. Part V (defensive ops at autonomous speed) maps to M11 + the Phase 7 tabletop/coverage work. Part II threats are mitigated by the modules listed (prompt injection → M7+M4; tool misuse → M4; identity abuse → M1/M2/M3; memory poisoning → M8; supply chain → CI/M10).

---

## Notes for the Implementing Model (cheaper/smaller)

1. **Build one module per session.** Don't try to do the whole plan at once. Read the module's contract, write its tests first (against acceptance criteria), then implement, then run tests.
2. **Use the contracts as the source of truth.** If a contract says `POST /decide` returns `{decision, obligations, expires_at}`, that's the exact shape — don't invent extra fields or rename them, because other modules depend on them.
3. **Prefer well-known libraries** listed in each module's Dependencies. Don't pull in exotic packages.
4. **Never hard-code secrets or use static API keys.** Every credential flows through TokenSvc.
5. **Deny-by-default everywhere.** When unsure, deny and log.
6. **Emit an audit event for every state change**, via `zta.common.audit.event(...)`.
7. **If a step feels ambiguous**, implement the Foundation tier behavior and leave an Enterprise/Advanced `TODO` tagged with the tier name. Do not skip Foundation controls.
8. **Run `ruff`, `mypy`, `pytest` before declaring a module done.** CI enforces all three.
