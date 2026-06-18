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
    return tmp_path / "keys"


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
    pub = serialization.load_der_public_key(raw)
    assert isinstance(pub, Ed25519PublicKey)


def test_sign_returns_bytes(key_dir: Path) -> None:
    a = Identity.load_or_create("agent-a", key_dir)
    sig = a.sign(b"hello")
    assert isinstance(sig, bytes)
    assert len(sig) == 64


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
    assert Identity.verify("ghost", b"x", b"\x00" * 64, key_dir) is False


def test_load_raises_on_missing_key(key_dir: Path) -> None:
    with pytest.raises(IdentityError):
        Identity.load("ghost", key_dir)


def test_identity_error_is_zta_error() -> None:
    assert issubclass(IdentityError, ZTAError)
