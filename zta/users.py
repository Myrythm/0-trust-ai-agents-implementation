"""User store backed by SQLite, with stdlib password hashing.

Passwords are stored as `pbkdf2$<iterations>$<salt_b64>$<hash_b64>` using
`hashlib.pbkdf2_hmac` — no third-party crypto dependency. The `users` table
lives in the same SQLite database used by the demo tools.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return (
        f"pbkdf2${_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"
    )


def verify_hash(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        iterations = int(iter_s)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(digest, expected)


@dataclass
class User:
    username: str
    role: str
    password_hash: str
    created_at: str


class UserStore:
    """CRUD over a `users` table; one row per account."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username      TEXT PRIMARY KEY,
                    role          TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
                """
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def create_user(self, username: str, password: str, role: str) -> User:
        user = User(
            username=username,
            role=role,
            password_hash=hash_password(password),
            created_at=datetime.now(UTC).isoformat(),
        )
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO users (username, role, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (user.username, user.role, user.password_hash, user.created_at),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"user already exists: {username}") from exc
        return user

    def get_user(self, username: str) -> User | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT username, role, password_hash, created_at FROM users WHERE username = ?",
                (username,),
            ).fetchone()
        return self._row_to_user(row) if row is not None else None

    def verify_password(self, username: str, password: str) -> bool:
        user = self.get_user(username)
        if user is None:
            return False
        return verify_hash(password, user.password_hash)

    def list_users(self) -> list[User]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT username, role, password_hash, created_at FROM users ORDER BY username"
            ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def delete_user(self, username: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM users WHERE username = ?", (username,))

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> User:
        return User(
            username=row["username"],
            role=row["role"],
            password_hash=row["password_hash"],
            created_at=row["created_at"],
        )
