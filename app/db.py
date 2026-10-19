"""SQLite persistence: each operation owns a short, closed connection."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

PERSONAS = [
    {"id": "alex", "name": "Alex Morgan", "label": "Active · Web · USD"},
    {"id": "blair", "name": "Blair Chen", "label": "Active · App Store · EUR"},
    {"id": "casey", "name": "Casey Rivera", "label": "Canceled · Google Play · USD"},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=3.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self, *, write=False):
        conn = self.connect()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, persona TEXT UNIQUE NOT NULL, name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id TEXT PRIMARY KEY, user_id TEXT UNIQUE NOT NULL REFERENCES users(id),
                plan TEXT NOT NULL, status TEXT NOT NULL, channel TEXT NOT NULL,
                renewal_date TEXT, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                amount_cents INTEGER NOT NULL, currency TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
                expires_at REAL NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                category TEXT NOT NULL, description TEXT NOT NULL,
                transaction_id TEXT REFERENCES transactions(id), status TEXT NOT NULL,
                created_at TEXT NOT NULL, escalation_reason TEXT NOT NULL,
                event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                UNIQUE(user_id, event_id)
            );
            CREATE TABLE IF NOT EXISTS responses (
                id TEXT PRIMARY KEY, session_hash TEXT,
                receipt_hash TEXT NOT NULL, delivery_status TEXT NOT NULL,
                created_at TEXT NOT NULL, delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_transactions_user ON transactions(user_id);
            CREATE INDEX IF NOT EXISTS ix_tickets_user ON tickets(user_id);
            """)
            renewal = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
            for i, persona in enumerate(PERSONAS):
                user_id = f"usr_{persona['id']}"
                conn.execute(
                    "INSERT OR IGNORE INTO users VALUES (?, ?, ?)", (user_id, persona["id"], persona["name"])
                )
                conn.execute(
                    "INSERT OR IGNORE INTO subscriptions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"sub_{persona['id']}",
                        user_id,
                        ["Monthly", "Annual", "Monthly"][i],
                        ["active", "active", "canceled"][i],
                        ["web", "app_store", "google_play"][i],
                        renewal if i != 2 else None,
                        [1299, 7999, 999][i],
                        ["USD", "EUR", "USD"][i],
                    ),
                )
                for offset in (1, 2):
                    date = (datetime.now(timezone.utc) - timedelta(days=30 * offset)).isoformat()
                    conn.execute(
                        "INSERT OR IGNORE INTO transactions VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            f"tx_{persona['id']}_{offset:03d}",
                            user_id,
                            [1299, 7999, 999][i],
                            ["USD", "EUR", "USD"][i],
                            "paid",
                            date,
                        ),
                    )

    def healthcheck(self):
        with self.transaction() as conn:
            conn.execute("SELECT 1").fetchone()
