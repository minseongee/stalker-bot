import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "stalker.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT    PRIMARY KEY,
    user_id    TEXT    NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT    NOT NULL,
    stock_code TEXT    NOT NULL,
    p1_ts      REAL    NOT NULL,
    p1_price   REAL    NOT NULL,
    p2_ts      REAL    NOT NULL,
    p2_price   REAL    NOT NULL,
    offset_y   REAL    NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── tokens ──────────────────────────────────────────────────────────────────

def create_token(token: str, user_id: str, ttl_seconds: int = 600) -> None:
    now = int(time.time())
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (token, user_id, now, now + ttl_seconds),
        )


def consume_token(token: str) -> str | None:
    """토큰 검증 후 user_id 반환. 만료됐거나 없으면 None."""
    now = int(time.time())
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM tokens WHERE token = ?", (token,)
        ).fetchone()
        if row is None or row["expires_at"] < now:
            return None
        conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
        return row["user_id"]


# ── channels ─────────────────────────────────────────────────────────────────

def save_channel(
    user_id: str,
    stock_code: str,
    p1_ts: float,
    p1_price: float,
    p2_ts: float,
    p2_price: float,
    offset_y: float,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO channels
               (user_id, stock_code, p1_ts, p1_price, p2_ts, p2_price, offset_y, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, stock_code, p1_ts, p1_price, p2_ts, p2_price, offset_y, int(time.time())),
        )
        return cur.lastrowid


def get_channels(user_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM channels WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_channels() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM channels").fetchall()
        return [dict(r) for r in rows]


def delete_channel(channel_id: int, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM channels WHERE id = ? AND user_id = ?",
            (channel_id, user_id),
        )
        return cur.rowcount > 0
