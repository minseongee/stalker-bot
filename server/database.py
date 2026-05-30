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

CREATE TABLE IF NOT EXISTS watchlist (
    user_id    TEXT    NOT NULL,
    stock_code TEXT    NOT NULL,
    added_at   INTEGER NOT NULL,
    PRIMARY KEY (user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL,
    side       TEXT    NOT NULL,  -- 'upper' | 'lower'
    fired_at   INTEGER NOT NULL,
    UNIQUE(channel_id, side)      -- 같은 채널·방향 중복 알림 방지
);

CREATE TABLE IF NOT EXISTS news_channels (
    guild_id   TEXT    PRIMARY KEY,
    channel_id TEXT    NOT NULL,
    message_id TEXT,              -- 마지막으로 전송한 메시지 ID (편집용)
    set_at     INTEGER NOT NULL
);
"""


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        try:
            conn.execute("ALTER TABLE channels ADD COLUMN channel_type TEXT NOT NULL DEFAULT 'normal'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE channels ADD COLUMN alert_enabled INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass


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


def validate_token(token: str) -> str | None:
    """토큰 유효성 검사 후 user_id 반환. 토큰은 소모하지 않음."""
    now = int(time.time())
    with _conn() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM tokens WHERE token = ?", (token,)
        ).fetchone()
        if row is None or row["expires_at"] < now:
            return None
        return row["user_id"]


def consume_token(token: str) -> str | None:
    """토큰 검증 후 user_id 반환 + 토큰 삭제 (1회 소모)."""
    user_id = validate_token(token)
    if user_id is None:
        return None
    with _conn() as conn:
        conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
    return user_id


# ── channels ─────────────────────────────────────────────────────────────────

def save_channel(
    user_id: str,
    stock_code: str,
    p1_ts: float,
    p1_price: float,
    p2_ts: float,
    p2_price: float,
    offset_y: float,
    channel_type: str = 'normal',
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO channels
               (user_id, stock_code, p1_ts, p1_price, p2_ts, p2_price, offset_y, channel_type, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user_id, stock_code, p1_ts, p1_price, p2_ts, p2_price, offset_y, channel_type, int(time.time())),
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


def update_channel_coords(
    channel_id: int,
    user_id: str,
    p1_ts: float, p1_price: float,
    p2_ts: float, p2_price: float,
    offset_y: float,
) -> dict | None:
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE channels
               SET p1_ts=?, p1_price=?, p2_ts=?, p2_price=?, offset_y=?
               WHERE id=? AND user_id=?""",
            (p1_ts, p1_price, p2_ts, p2_price, offset_y, channel_id, user_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
        return dict(row) if row else None


def update_channel_alert(channel_id: int, user_id: str, enabled: bool) -> dict | None:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE channels SET alert_enabled=? WHERE id=? AND user_id=?",
            (1 if enabled else 0, channel_id, user_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
        return dict(row) if row else None


def delete_channel(channel_id: int, user_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM channels WHERE id = ? AND user_id = ?",
            (channel_id, user_id),
        )
        return cur.rowcount > 0


# ── news_channels ────────────────────────────────────────────────────────────

def set_news_channel(guild_id: str, channel_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO news_channels (guild_id, channel_id, message_id, set_at)
               VALUES (?,?,NULL,?)
               ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id,
                                                    message_id=NULL,
                                                    set_at=excluded.set_at""",
            (guild_id, channel_id, int(time.time())),
        )


def get_all_news_channels() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM news_channels").fetchall()
        return [dict(r) for r in rows]


def update_news_message_id(guild_id: str, message_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE news_channels SET message_id=? WHERE guild_id=?",
            (message_id, guild_id),
        )


# ── watchlist ────────────────────────────────────────────────────────────────

def get_watchlist(user_id: str) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT stock_code FROM watchlist WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        ).fetchall()
        return [r["stock_code"] for r in rows]


def add_to_watchlist(user_id: str, stock_code: str) -> bool:
    """이미 있으면 False, 새로 추가하면 True."""
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO watchlist (user_id, stock_code, added_at) VALUES (?,?,?)",
                (user_id, stock_code, int(time.time())),
            )
        return True
    except Exception:
        return False


def remove_from_watchlist(user_id: str, stock_code: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND stock_code = ?",
            (user_id, stock_code),
        )
        return cur.rowcount > 0


# ── alerts ───────────────────────────────────────────────────────────────────

ALERT_COOLDOWN = 3600  # 1시간


def already_alerted(channel_id: int, side: str) -> bool:
    """마지막 알림이 쿨타임(1시간) 이내면 True."""
    now = int(time.time())
    with _conn() as conn:
        row = conn.execute(
            "SELECT fired_at FROM alerts WHERE channel_id = ? AND side = ?",
            (channel_id, side),
        ).fetchone()
        if row is None:
            return False
        return (now - row["fired_at"]) < ALERT_COOLDOWN


def record_alert(channel_id: int, side: str) -> None:
    """알림 기록 (같은 채널·방향이면 타임스탬프 갱신)."""
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO alerts (channel_id, side, fired_at) VALUES (?,?,?)",
            (channel_id, side, int(time.time())),
        )
