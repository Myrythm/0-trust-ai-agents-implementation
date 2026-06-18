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
    def load_or_create(cls, agent_id: str, key_dir: Path) -> Identity:
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
    def load(cls, agent_id: str, key_dir: Path) -> Identity:
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
            private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
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
    def _load_from_path(cls, agent_id: str, key_path: Path) -> Identity:
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise IdentityError(f"key file is not Ed25519: agent_id={agent_id!r} path={key_path}")
        return cls(agent_id=agent_id, _private_key=private_key)
