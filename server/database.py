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
    guild_id     TEXT    NOT NULL,
    channel_type TEXT    NOT NULL DEFAULT 'briefing',
    channel_id   TEXT    NOT NULL,
    set_at       INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_type)
);

CREATE TABLE IF NOT EXISTS news_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guid         TEXT    UNIQUE NOT NULL,
    title        TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    published_at INTEGER NOT NULL,
    fetched_at   INTEGER NOT NULL,
    cluster_id   TEXT,
    hot_score    REAL    NOT NULL DEFAULT 0,
    is_hot       INTEGER NOT NULL DEFAULT 0,
    summary      TEXT,
    headline     TEXT,
    direction    TEXT,
    stock_tags   TEXT,
    sources_json TEXT
);

CREATE TABLE IF NOT EXISTS news_clusters (
    cluster_id   TEXT    PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    updated_at   INTEGER NOT NULL,
    item_count   INTEGER NOT NULL DEFAULT 1,
    source_count INTEGER NOT NULL DEFAULT 1,
    hot_score    REAL    NOT NULL DEFAULT 0,
    is_hot       INTEGER NOT NULL DEFAULT 0,
    refined_at   INTEGER
);

CREATE TABLE IF NOT EXISTS news_messages (
    cluster_id   TEXT    NOT NULL,
    channel_id   TEXT    NOT NULL,
    message_id   INTEGER NOT NULL,
    PRIMARY KEY (cluster_id, channel_id)
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
        # news_channels 스키마 마이그레이션: guild_id 단일 PK → (guild_id, channel_type) 복합 PK
        try:
            pk_cols = [r[5] for r in conn.execute("PRAGMA table_info(news_channels)").fetchall() if r[5] > 0]
            if len(pk_cols) < 2:  # 복합 PK가 아닌 경우 재생성
                conn.execute("""
                    CREATE TABLE news_channels_new (
                        guild_id     TEXT    NOT NULL,
                        channel_type TEXT    NOT NULL DEFAULT 'briefing',
                        channel_id   TEXT    NOT NULL,
                        set_at       INTEGER NOT NULL,
                        PRIMARY KEY (guild_id, channel_type)
                    )
                """)
                conn.execute("""
                    INSERT INTO news_channels_new (guild_id, channel_type, channel_id, set_at)
                    SELECT guild_id, 'briefing', channel_id, set_at FROM news_channels
                """)
                conn.execute("DROP TABLE news_channels")
                conn.execute("ALTER TABLE news_channels_new RENAME TO news_channels")
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

def set_news_channel(guild_id: str, channel_id: str, channel_type: str = "briefing") -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO news_channels (guild_id, channel_type, channel_id, set_at)
               VALUES (?,?,?,?)
               ON CONFLICT(guild_id, channel_type) DO UPDATE SET
                 channel_id=excluded.channel_id,
                 set_at=excluded.set_at""",
            (guild_id, channel_type, channel_id, int(time.time())),
        )


def get_news_channels_by_type(channel_type: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news_channels WHERE channel_type = ?", (channel_type,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_news_channels() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM news_channels").fetchall()
        return [dict(r) for r in rows]


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


# ── news_items ────────────────────────────────────────────────────────────────

def upsert_news_item(item: dict) -> bool:
    """새 기사 저장. 이미 존재하면 False, 새로 삽입하면 True."""
    try:
        with _conn() as conn:
            conn.execute(
                """INSERT INTO news_items
                   (guid, title, url, source, published_at, fetched_at)
                   VALUES (:guid, :title, :url, :source, :published_at, :fetched_at)""",
                item,
            )
        return True
    except sqlite3.IntegrityError:
        return False


def update_news_cluster(guid: str, cluster_id: str, hot_score: float, is_hot: bool) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE news_items SET cluster_id=?, hot_score=?, is_hot=? WHERE guid=?",
            (cluster_id, hot_score, 1 if is_hot else 0, guid),
        )


def update_news_refined(guid: str, summary: str, headline: str, direction: str,
                        stock_tags: str, sources_json: str) -> None:
    with _conn() as conn:
        conn.execute(
            """UPDATE news_items
               SET summary=?, headline=?, direction=?, stock_tags=?, sources_json=?
               WHERE guid=?""",
            (summary, headline, direction, stock_tags, sources_json, guid),
        )


def get_recent_news_items(limit: int = 200, since: int | None = None) -> list[dict]:
    with _conn() as conn:
        if since is not None:
            rows = conn.execute(
                "SELECT * FROM news_items WHERE fetched_at >= ? ORDER BY fetched_at DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM news_items ORDER BY fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_hot_news(limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM news_items
               WHERE is_hot = 1 AND summary IS NOT NULL
               ORDER BY fetched_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_hot_news_by_score(threshold: float, limit: int = 20) -> list[dict]:
    """현재 임계치 기준으로 hot_score >= threshold 인 뉴스 반환."""
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM news_items
               WHERE hot_score >= ? AND summary IS NOT NULL
               ORDER BY fetched_at DESC LIMIT ?""",
            (threshold, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_hot_news_time() -> int | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT fetched_at FROM news_items WHERE is_hot=1 ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        return row["fetched_at"] if row else None


# ── news_clusters ─────────────────────────────────────────────────────────────

def upsert_cluster(cluster_id: str, item_count: int, source_count: int,
                   hot_score: float, is_hot: bool) -> None:
    now = int(time.time())
    with _conn() as conn:
        conn.execute(
            """INSERT INTO news_clusters
               (cluster_id, created_at, updated_at, item_count, source_count, hot_score, is_hot)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(cluster_id) DO UPDATE SET
                 updated_at=excluded.updated_at,
                 item_count=excluded.item_count,
                 source_count=excluded.source_count,
                 hot_score=excluded.hot_score,
                 is_hot=excluded.is_hot""",
            (cluster_id, now, now, item_count, source_count, hot_score, 1 if is_hot else 0),
        )


def mark_cluster_refined(cluster_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE news_clusters SET refined_at=? WHERE cluster_id=?",
            (int(time.time()), cluster_id),
        )


def purge_old_news(days: int = 7) -> int:
    cutoff = int(time.time()) - 3600 * 24 * days
    with _conn() as conn:
        conn.execute("DELETE FROM news_items WHERE fetched_at < ?", (cutoff,))
        conn.execute(
            """DELETE FROM news_clusters WHERE cluster_id NOT IN (
               SELECT DISTINCT cluster_id FROM news_items WHERE cluster_id IS NOT NULL)"""
        )
        conn.execute(
            """DELETE FROM news_messages WHERE cluster_id NOT IN (
               SELECT DISTINCT cluster_id FROM news_items WHERE cluster_id IS NOT NULL)"""
        )
        deleted = conn.execute("SELECT changes()").fetchone()[0]
    return deleted


def get_unrefined_hot_clusters() -> list[dict]:
    since = int(time.time()) - 3600 * 24
    with _conn() as conn:
        rows = conn.execute(
            """SELECT nc.* FROM news_clusters nc
               WHERE nc.is_hot=1 AND nc.refined_at IS NULL
                 AND EXISTS (
                   SELECT 1 FROM news_items ni
                   WHERE ni.cluster_id = nc.cluster_id
                     AND ni.fetched_at >= ?
                 )
               ORDER BY nc.hot_score DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_cluster_items(cluster_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news_items WHERE cluster_id=? ORDER BY published_at",
            (cluster_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── news_messages ────────────────────────────────────────────────────────────

def save_message_id(cluster_id: str, channel_id: str, message_id: int) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO news_messages (cluster_id, channel_id, message_id)
               VALUES (?,?,?)
               ON CONFLICT(cluster_id, channel_id) DO UPDATE SET message_id=excluded.message_id""",
            (cluster_id, channel_id, message_id),
        )


def get_message_id(cluster_id: str, channel_id: str) -> int | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT message_id FROM news_messages WHERE cluster_id=? AND channel_id=?",
            (cluster_id, channel_id),
        ).fetchone()
        return row["message_id"] if row else None
