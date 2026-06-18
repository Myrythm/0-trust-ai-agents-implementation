# Feature 2: `zta.identity` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `zta/identity.py` — Ed25519 keypair per agent, persisted as PEM in `key_dir`, with sign/verify API and deny-by-default identity error handling.

**Architecture:** A single `Identity` class wrapping `cryptography`'s `Ed25519PrivateKey`/`Ed25519PublicKey`. Storage = one PEM file per agent under `key_dir/{agent_id}.pem` (private key; public key derived). Permissions 0600. `load_or_create` is the primary entry; `load` is strict. `verify` is a classmethod and returns `False` for unknown agents and bad signatures (no exception) so callers can treat both as "deny".

**Tech Stack:** `cryptography` (Ed25519), `pathlib`, `base64`, `dataclasses`, stdlib only beyond the package deps already pinned in F1.

---

## File Structure

Files created in this feature:

```
zta/
├── errors.py              # already declared in spec; create here
└── identity.py            # the module

tests/
└── test_identity.py       # 10 tests covering contract
```

The spec section 3.1 references `IdentityError`. F2 introduces `zta/errors.py` with the full error hierarchy (F6 may extend; other modules just import what's there).

---

## Contract (per spec section 3.1)

```python
class IdentityError(ZTAError): ...

@dataclass
class Identity:
    agent_id: str
    _private_key: Ed25519PrivateKey  # internal
    _public_key: Ed25519PublicKey    # derived; cached

    @classmethod
    def load_or_create(cls, agent_id: str, key_dir: Path) -> "Identity": ...
    @classmethod
    def load(cls, agent_id: str, key_dir: Path) -> "Identity": ...  # raises IdentityError on miss
    def sign(self, payload: bytes) -> bytes: ...
    @classmethod
    def verify(cls, agent_id: str, payload: bytes, signature: bytes, key_dir: Path) -> bool: ...
    @property
    def public_key_b64(self) -> str: ...  # base64(SPKI)
```

**Failure modes (locked by spec):**
- `load` on missing key file → `IdentityError`
- `load_or_create` on missing key file → creates it, returns new Identity
- `sign` on un-loaded Identity (internal misuse) → `IdentityError` (defensive)
- `verify` on missing key file → `False` (no exception)
- `verify` on bad signature → `False` (no exception)
- `key_dir` not existing on `load_or_create` → created automatically
- key file permissions must be `0o600` (security property — tested)

---

## Tasks

### Task 1: Write the failing test file

**Files:**
- Create: `tests/test_identity.py`

- [ ] **Step 1: Create the test file with 10 tests**

```python
"""Tests for zta.identity — Ed25519 agent keypair persistence + sign/verify."""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from zta.errors import IdentityError, ZTAError
from zta.identity import Identity


@pytest.fixture
def key_dir(tmp_path: Path) -> Path:
    d = tmp_path / "keys"
    return d


def test_load_or_create_creates_key_dir_if_missing(key_dir: Path) -> None:
    assert not key_dir.exists()
    Identity.load_or_create("agent-a", key_dir)
    assert key_dir.is_dir()


def test_load_or_create_creates_pem_file(key_dir: Path) -> None:
    Identity.load_or_create("agent-a", key_dir)
    assert (key_dir / "agent-a.pem").is_file()


def test_load_or_create_key_file_has_0600_permissions(key_dir: Path) -> None:
    Identity.load_or_create("agent-a", key_dir)
    mode = stat.S_IMODE(os.stat(key_dir / "agent-a.pem").st_mode)
    assert mode == 0o600


def test_load_or_create_reuses_existing_key(key_dir: Path) -> None:
    a1 = Identity.load_or_create("agent-a", key_dir)
    a2 = Identity.load_or_create("agent-a", key_dir)
    assert a1.public_key_b64 == a2.public_key_b64


def test_load_or_create_different_agents_have_different_keys(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    b = Identity.load_or_create("agent-b", key_dir)
    assert a.public_key_b64 != b.public_key_b64


def test_public_key_b64_is_valid_ed25519_spki(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    raw = base64.b64decode(a.public_key_b64)
    Ed25519PublicKey.from_public_bytes(raw[12:])  # SPKI header for Ed25519 is 12 bytes
    # from_public_bytes is not what from_public_key_data expects; use the proper loader:
    pub = serialization.load_der_public_key(raw)
    assert isinstance(pub, Ed25519PublicKey)


def test_sign_returns_bytes(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    sig = a.sign(b"hello")
    assert isinstance(sig, bytes)
    assert len(sig) == 64  # Ed25519 signature length


def test_sign_then_verify_succeeds(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    payload = b"the quick brown fox"
    sig = a.sign(payload)
    assert Identity.verify("agent-a", payload, sig, key_dir) is True


def test_verify_rejects_tampered_payload(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    sig = a.sign(b"original")
    assert Identity.verify("agent-a", b"tampered", sig, key_dir) is False


def test_verify_rejects_bad_signature(key_dir: Path) -> None:
    Identity.load_or_create("agent-a", key_dir)
    assert Identity.verify("agent-a", b"x", b"\x00" * 64, key_dir) is False


def test_verify_unknown_agent_returns_false(key_dir: Path) -> None:
    # No identity loaded; verify should return False (deny-by-default)
    assert Identity.verify("ghost", b"x", b"\x00" * 64, key_dir) is False


def test_load_raises_on_missing_key(key_dir: Path) -> None:
    with pytest.raises(IdentityError):
        Identity.load("ghost", key_dir)


def test_identity_error_is_zta_error() -> None:
    assert issubclass(IdentityError, ZTAError)
```

- [ ] **Step 2: Run the test to verify it fails (no implementation yet)**

Run: `.venv/bin/pytest tests/test_identity.py -v`
Expected: collection error (`ModuleNotFoundError: No module named 'zta.identity'`).

---

### Task 2: Create `zta/errors.py`

**Files:**
- Create: `zta/errors.py`

- [ ] **Step 1: Create the error hierarchy**

```python
"""Error types for the ZTA MVP.

ZTA's policy is deny-by-default. `ZTAError` is the base for every
control-plane error so callers can catch them uniformly. Library modules
extend this hierarchy; they do not invent new base classes.
"""

from __future__ import annotations


class ZTAError(Exception):
    """Base class for all ZTA control plane errors."""


class IdentityError(ZTAError):
    """Identity issuance, lookup, rotation, or revocation failed."""


class TokenError(ZTAError):
    """Token issuance, refresh, introspection, or revocation failed."""


class PolicyError(ZTAError):
    """Policy file could not be loaded or parsed."""


class AuditError(ZTAError):
    """Audit append or integrity verification failed."""


class ToolError(ZTAError):
    """Tool registry or invocation failed."""
```

- [ ] **Step 2: Re-export errors from `zta/__init__.py`**

Modify `zta/__init__.py` to add:

```python
"""Zero Trust control plane for AI agents (MVP)."""

from __future__ import annotations

from zta.errors import (
    AuditError,
    IdentityError,
    PolicyError,
    TokenError,
    ToolError,
    ZTAError,
)

__version__ = "0.1.0"

__all__ = [
    "AuditError",
    "IdentityError",
    "PolicyError",
    "TokenError",
    "ToolError",
    "ZTAError",
    "__version__",
]
```

---

### Task 3: Implement `zta/identity.py`

**Files:**
- Create: `zta/identity.py`

- [ ] **Step 1: Write the implementation**

```python
"""Agent identity: Ed25519 keypair per agent, persisted as PEM.

The key file is the canonical source of identity. Re-running
`load_or_create` for an existing agent returns an `Identity` bound to
the same key (same public key bytes). `verify` is a classmethod that
looks up the agent's public key on demand and returns False if the
agent is unknown or the signature does not match — never raises. Use
`load` when you specifically need the strict "must exist" behaviour.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from zta.errors import IdentityError

_KEY_FILE_MODE = 0o600
_PUBKEY_HEADER_LEN = 12  # SPKI header for Ed25519 is 12 bytes


@dataclass
class Identity:
    """An agent's Ed25519 keypair (private key held; public derived)."""

    agent_id: str
    _private_key: Ed25519PrivateKey

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._private_key.public_key()

    @property
    def public_key_b64(self) -> str:
        raw = self.public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(raw).decode("ascii")

    def sign(self, payload: bytes) -> bytes:
        return self._private_key.sign(payload)

    @classmethod
    def load_or_create(cls, agent_id: str, key_dir: Path) -> "Identity":
        """Return the Identity for `agent_id`, creating the key file if absent.

        Creates `key_dir` (with parent dirs) if it does not exist.
        Permissions on the key file are 0o600.
        """
        key_dir.mkdir(parents=True, exist_ok=True)
        key_path = key_dir / f"{agent_id}.pem"
        if key_path.exists():
            return cls._load_from_path(agent_id, key_path)
        private_key = Ed25519PrivateKey.generate()
        pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_path.write_bytes(pem)
        os.chmod(key_path, _KEY_FILE_MODE)
        return cls(agent_id=agent_id, _private_key=private_key)

    @classmethod
    def load(cls, agent_id: str, key_dir: Path) -> "Identity":
        """Strict load: raise IdentityError if the key file does not exist."""
        key_path = key_dir / f"{agent_id}.pem"
        if not key_path.exists():
            raise IdentityError(f"identity not found: agent_id={agent_id!r} key_dir={key_dir}")
        return cls._load_from_path(agent_id, key_path)

    @classmethod
    def verify(
        cls,
        agent_id: str,
        payload: bytes,
        signature: bytes,
        key_dir: Path,
    ) -> bool:
        """Verify `signature` over `payload` against the agent's public key.

        Returns False (never raises) for: unknown agent, unreadable key,
        or signature mismatch. This is the deny-by-default surface.
        """
        key_path = key_dir / f"{agent_id}.pem"
        if not key_path.exists():
            return False
        try:
            private_key = serialization.load_pem_private_key(
                key_path.read_bytes(), password=None
            )
        except Exception:
            return False
        if not isinstance(private_key, Ed25519PrivateKey):
            return False
        try:
            private_key.public_key().verify(signature, payload)
        except Exception:
            return False
        return True

    @classmethod
    def _load_from_path(cls, agent_id: str, key_path: Path) -> "Identity":
        private_key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=None
        )
        if not isinstance(private_key, Ed25519PrivateKey):
            raise IdentityError(
                f"key file is not Ed25519: agent_id={agent_id!r} path={key_path}"
            )
        return cls(agent_id=agent_id, _private_key=private_key)
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_identity.py -v`
Expected: 13 tests pass.

If `test_public_key_b64_is_valid_ed25519_spki` is awkward (it does two things), keep the SPKI-shape check only; remove the redundant `from_public_bytes` attempt that has a misleading comment. Replace its body with:

```python
def test_public_key_b64_is_valid_ed25519_spki(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    raw = base64.b64decode(a.public_key_b64)
    pub = serialization.load_der_public_key(raw)
    assert isinstance(pub, Ed25519PublicKey)
```

(Use this version — the version with the 12-byte slice in step 1 above should be removed.)

---

### Task 4: Full verification

**Files:** none

- [ ] **Step 1: Lint**

Run: `.venv/bin/ruff check .`
Expected: clean.

- [ ] **Step 2: Format check**

Run: `.venv/bin/ruff format --check .`
Expected: clean.

- [ ] **Step 3: Typecheck (strict)**

Run: `.venv/bin/mypy zta tests`
Expected: 0 issues.

- [ ] **Step 4: Full test suite with coverage**

Run: `.venv/bin/pytest --cov=zta -v`
Expected: all tests pass; library coverage ≥ 90%.

- [ ] **Step 5: Smoke — interact in Python**

Run:
```bash
.venv/bin/python -c "
from pathlib import Path
from zta.identity import Identity
import tempfile

with tempfile.TemporaryDirectory() as d:
    idn = Identity.load_or_create('demo', Path(d))
    print('pubkey:', idn.public_key_b64[:24] + '...')
    sig = idn.sign(b'hello')
    print('sig len:', len(sig))
    print('verify ok:', Identity.verify('demo', b'hello', sig, Path(d)))
    print('verify tampered:', Identity.verify('demo', b'goodbye', sig, Path(d)))
    print('verify unknown:', Identity.verify('ghost', b'hello', sig, Path(d)))
"
```

Expected output (numbers and `True`/`False` exact; pubkey prefix will vary):
```
pubkey: MCowBQYDK2VwAyEA...
sig len: 64
verify ok: True
verify tampered: False
verify unknown: False
```

---

### Task 5: Commit and push

- [ ] **Step 1: Stage**

Run: `git add zta/errors.py zta/identity.py zta/__init__.py tests/test_identity.py`

- [ ] **Step 2: Commit**

Run:
```bash
git commit -m "feat(f02): zta.identity - Ed25519 keypair, sign/verify, file mode 0600

- zta/errors.py: ZTAError hierarchy (IdentityError, TokenError, PolicyError,
  AuditError, ToolError) re-exported from zta package root
- zta/identity.py: Identity class with Ed25519 keypair, persisted as PEM
  in {key_dir}/{agent_id}.pem with mode 0600 enforced
- load_or_create: idempotent, creates key_dir + file on first call
- load: strict, raises IdentityError on miss
- verify (classmethod): returns False for unknown agent, bad signature,
  or malformed key file (never raises) — deny-by-default surface
- 13 unit tests covering all contract edges (incl. permissions, roundtrip,
  tamper detection, unknown agent, strict load)"
```

- [ ] **Step 3: Push and open PR**

Run:
```bash
git push -u origin feature/f02-zta-identity
```

After push, the PR description should reference this plan file. The PR body template:

```markdown
Implements docs/superpowers/plans/2026-06-18-f02-zta-identity.md.

## What
- zta/errors.py: ZTAError hierarchy (IdentityError + 4 others for future modules)
- zta/identity.py: Ed25519 keypair Identity with load_or_create, load, sign, verify
- zta/__init__.py: re-exports error types
- 13 unit tests covering the full contract (incl. 0600 file permissions, deny-by-default verify)

## Verification
- ruff check: clean
- ruff format --check: clean
- mypy zta tests (strict): 0 issues
- pytest --cov=zta: 13 passed, library coverage ≥ 90%
- Manual smoke: sign/verify roundtrip + tamper detection + unknown-agent deny

## Spec
docs/superpowers/specs/2026-06-18-zta-mvp-design.md section 3.1
```

I will create the PR via the GitHub API (the previous PAT is still configured for this session); if it has been rotated, I'll ask for a new one.

---

## Self-Review

**Spec coverage:** Spec section 3.1 lists `load_or_create`, `sign`, `verify` (classmethod), `public_key_b64`. All present. Spec mentions `key_dir: Path` — handled. Spec mentions `IdentityError` — defined in errors.py.
**Placeholders:** None. All code shown; all commands exact.
**Type consistency:** `Identity` dataclass with `agent_id: str` and `_private_key: Ed25519PrivateKey`. `public_key_b64` returns `str`. `sign(payload: bytes) -> bytes`. `verify(...) -> bool`. All consistent with spec.
**Security:** key file mode 0o600 enforced via `os.chmod` after write. `verify` never raises (caller can't accidentally let an exception leak through "deny" path).
