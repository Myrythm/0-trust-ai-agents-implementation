"""Populate data.db with the Chinook sample database (a digital media store).

Chinook (https://github.com/lerocha/chinook-database, MIT) models artists,
albums, tracks, playlists, customers, invoices, and employees. We use the
official SQLite build, pinned to release v1.4.5.

Source resolution:
  - if ZTA_CHINOOK_SQL is set, read the SQL from that local path;
  - otherwise download the pinned Chinook_Sqlite.sql.

Idempotent: if the target DB already has a populated `Artist` table, this is a
no-op. Any legacy `customers`/`orders` demo tables are dropped; other tables
(e.g. the RBAC `users` table) are left untouched.

Usage: ZTA_DB_PATH=./data.db python examples/seed_db.py
"""

from __future__ import annotations

import os
import sqlite3
import urllib.request
from pathlib import Path

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "data.db"
DB_PATH = Path(os.environ.get("ZTA_DB_PATH", str(_DEFAULT_DB)))

CHINOOK_URL = (
    "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_Sqlite.sql"
)

_SUMMARY_TABLES = ("Artist", "Album", "Track", "Genre", "Customer", "Invoice", "Employee")
_LEGACY_TABLES = ("orders", "customers")


def _load_sql() -> str:
    """Return the Chinook SQL, from ZTA_CHINOOK_SQL if set, else the pinned URL."""
    override = os.environ.get("ZTA_CHINOOK_SQL")
    if override:
        return Path(override).read_text(encoding="utf-8-sig")
    with urllib.request.urlopen(CHINOOK_URL, timeout=120) as resp:  # noqa: S310 - pinned https URL
        return resp.read().decode("utf-8-sig")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        existing = _table_names(conn)
        for legacy in _LEGACY_TABLES:
            if legacy in existing:
                conn.execute(f"DROP TABLE {legacy}")
                conn.commit()

        if "Artist" in existing and conn.execute("SELECT COUNT(*) FROM Artist").fetchone()[0] > 0:
            print(f"Chinook already present in {DB_PATH}; nothing to do")
            return

        conn.executescript(_load_sql())
        conn.commit()

        after = _table_names(conn)
        for table in _SUMMARY_TABLES:
            if table in after:
                # table is from the hardcoded _SUMMARY_TABLES tuple, never user input
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
                print(f"  {table}: {count}")
        print(f"Seeded Chinook into {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
