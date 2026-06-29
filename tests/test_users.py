"""Tests for the SQLite-backed user store and password hashing."""

from __future__ import annotations

from pathlib import Path

import pytest
from zta.users import User, UserStore, hash_password, verify_hash


def test_hash_roundtrip() -> None:
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert h.startswith("pbkdf2$")
    assert verify_hash("s3cret", h) is True
    assert verify_hash("wrong", h) is False


def test_hash_is_salted() -> None:
    assert hash_password("same") != hash_password("same")


def test_verify_hash_rejects_garbage() -> None:
    assert verify_hash("x", "not-a-hash") is False
    assert verify_hash("x", "") is False


def test_create_and_get(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    user = store.create_user("alice", "pw", "admin")
    assert isinstance(user, User)
    assert user.username == "alice"
    assert user.role == "admin"
    got = store.get_user("alice")
    assert got is not None
    assert got.role == "admin"
    assert store.get_user("nobody") is None


def test_verify_password(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.create_user("bob", "hunter2", "analyst")
    assert store.verify_password("bob", "hunter2") is True
    assert store.verify_password("bob", "nope") is False
    assert store.verify_password("ghost", "hunter2") is False


def test_duplicate_rejected(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.create_user("carol", "pw", "viewer")
    with pytest.raises(ValueError, match="already exists"):
        store.create_user("carol", "pw2", "admin")


def test_list_and_delete(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.create_user("a", "pw", "admin")
    store.create_user("b", "pw", "viewer")
    assert {u.username for u in store.list_users()} == {"a", "b"}
    store.delete_user("b")
    assert {u.username for u in store.list_users()} == {"a"}


def test_delete_missing_is_noop(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "data.db")
    store.delete_user("ghost")  # must not raise


def test_persists_across_instances(tmp_path: Path) -> None:
    db = tmp_path / "data.db"
    UserStore(db).create_user("d", "pw", "admin")
    assert UserStore(db).get_user("d") is not None
