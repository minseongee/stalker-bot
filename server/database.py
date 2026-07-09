import json
import os
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

CREATE INDEX IF NOT EXISTS idx_news_items_fetched_at  ON news_items (fetched_at);
CREATE INDEX IF NOT EXISTS idx_news_items_cluster_id  ON news_items (cluster_id);
CREATE INDEX IF NOT EXISTS idx_news_items_hot         ON news_items (is_hot, fetched_at);
CREATE INDEX IF NOT EXISTS idx_news_clusters_hot      ON news_clusters (is_hot, refined_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id      TEXT PRIMARY KEY,
    username     TEXT NOT NULL,
    global_name  TEXT NOT NULL,
    avatar       TEXT,
    updated_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    author_id   TEXT    NOT NULL,
    author_name TEXT    NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hot_news_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT    NOT NULL,
    stock_codes TEXT    NOT NULL,
    headline    TEXT    NOT NULL,
    direction   TEXT    NOT NULL DEFAULT 'neutral',
    cluster_id  TEXT,
    fired_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hot_news_alerts_user ON hot_news_alerts (user_id, fired_at);

CREATE TABLE IF NOT EXISTS stock_digests (
    code         TEXT    NOT NULL,
    window_key   TEXT    NOT NULL,  -- 예: '2026-07-09-16' (날짜-시각, 재실행 시 idempotent 갱신용)
    name         TEXT    NOT NULL,
    counts_json  TEXT    NOT NULL,  -- {"positive":N, "negative":N, "neutral":N}
    net_stance   TEXT    NOT NULL DEFAULT 'neutral',
    net_reason   TEXT,
    body_json    TEXT    NOT NULL DEFAULT '[]',  -- key_issues 리스트
    sources_json TEXT    NOT NULL DEFAULT '[]',
    created_at   INTEGER NOT NULL,
    PRIMARY KEY (code, window_key)
);

CREATE INDEX IF NOT EXISTS idx_stock_digests_code ON stock_digests (code, created_at);
"""


def init_db() -> None:
    with _conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # 동시 읽기 성능 향상
        conn.executescript(_SCHEMA)
        for sql in [
            "ALTER TABLE channels ADD COLUMN channel_type TEXT NOT NULL DEFAULT 'normal'",
            "ALTER TABLE channels ADD COLUMN alert_enabled INTEGER NOT NULL DEFAULT 1",
        ]:
            try:
                conn.execute(sql)
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    print(f"[DB] 마이그레이션 경고: {e}")
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
                print("[DB] news_channels 복합 PK 마이그레이션 완료")
        except Exception as e:
            print(f"[DB] news_channels 마이그레이션 실패: {e}")


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


def get_users_watching(stock_codes: list[str]) -> dict[str, list[str]]:
    """주어진 종목 코드 목록 중 하나라도 관심 종목에 등록한 유저 반환.
    반환: {user_id: [매칭된 종목코드, ...]}
    """
    if not stock_codes:
        return {}
    placeholders = ",".join("?" * len(stock_codes))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT user_id, stock_code FROM watchlist WHERE stock_code IN ({placeholders})",
            stock_codes,
        ).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["user_id"], []).append(r["stock_code"])
    return result


def get_all_watchlists() -> dict[str, list[str]]:
    """전 유저의 관심종목 매핑 반환: {user_id: [stock_code, ...]}. 다이제스트 팬아웃에 사용."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT user_id, stock_code FROM watchlist ORDER BY user_id, added_at"
        ).fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["user_id"], []).append(r["stock_code"])
    return result


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


def get_hot_news_for_codes_since(codes: list[str], since_ts: int) -> list[dict]:
    """codes 중 하나라도 stock_tags에 포함된, since_ts 이후 정제된 핫뉴스 반환.
    관심종목별 다이제스트 집계에 사용 (news_items.stock_tags는 "코드:이름" JSON 배열)."""
    if not codes:
        return []
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM news_items
               WHERE summary IS NOT NULL AND stock_tags IS NOT NULL AND fetched_at >= ?
               ORDER BY fetched_at ASC""",
            (since_ts,),
        ).fetchall()
    code_set = set(codes)
    result: list[dict] = []
    for r in rows:
        try:
            tags = json.loads(r["stock_tags"])
        except Exception:
            continue
        tag_codes = {t.split(":")[0] for t in tags if ":" in t}
        if tag_codes & code_set:
            result.append(dict(r))
    return result


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

def get_broadcast_cluster_ids() -> set[str]:
    """봇 시작 시 이미 전송된 cluster_id 목록 반환 — 재시작 후 재전송 방지."""
    with _conn() as conn:
        rows = conn.execute("SELECT DISTINCT cluster_id FROM news_messages").fetchall()
        return {r["cluster_id"] for r in rows}


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


# ── settings ──────────────────────────────────────────────────────────────────

_SETTING_DEFAULTS = {
    "HOT_SCORE_THRESHOLD":   "35",
    "HOT_EMPHASIS_THRESHOLD": "60",
    "NEWS_POLL_INTERVAL":    "60",
}

def get_live_setting(key: str) -> str:
    """DB 우선, 없으면 env, 없으면 기본값."""
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
    return os.getenv(key, _SETTING_DEFAULTS.get(key, ""))

def get_all_settings() -> dict:
    defaults = {k: os.getenv(k, v) for k, v in _SETTING_DEFAULTS.items()}
    with _conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for r in rows:
            defaults[r["key"]] = r["value"]
    return defaults

def set_setting(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            (key, value),
        )


# ── stock_digests ────────────────────────────────────────────────────────────

def upsert_stock_digest(window_key: str, card: dict) -> None:
    """종목별 종합 다이제스트 카드 저장. (code, window_key) 복합키라 같은 주기 재실행 시 idempotent."""
    with _conn() as conn:
        conn.execute(
            """INSERT INTO stock_digests
               (code, window_key, name, counts_json, net_stance, net_reason,
                body_json, sources_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(code, window_key) DO UPDATE SET
                 name=excluded.name,
                 counts_json=excluded.counts_json,
                 net_stance=excluded.net_stance,
                 net_reason=excluded.net_reason,
                 body_json=excluded.body_json,
                 sources_json=excluded.sources_json,
                 created_at=excluded.created_at""",
            (
                card["code"], window_key, card["name"],
                json.dumps(card.get("counts", {}), ensure_ascii=False),
                card.get("net_stance", "neutral"),
                card.get("net_reason", ""),
                json.dumps(card.get("key_issues", []), ensure_ascii=False),
                json.dumps(card.get("sources", []), ensure_ascii=False),
                int(time.time()),
            ),
        )


def get_latest_stock_digests(codes: list[str]) -> list[dict]:
    """codes 각각의 가장 최근 다이제스트 카드 반환 (아직 생성 안 된 종목은 제외).
    window_key와 무관하게 최신 1건만 — 웹 대시보드가 언제 열어도 가장 최근 종합을 보여주기 위함."""
    if not codes:
        return []
    placeholders = ",".join("?" * len(codes))
    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM stock_digests
                WHERE code IN ({placeholders})
                ORDER BY code, created_at DESC""",
            codes,
        ).fetchall()
    seen: set[str] = set()
    result: list[dict] = []
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        result.append({
            "code":       r["code"],
            "name":       r["name"],
            "counts":     json.loads(r["counts_json"]),
            "net_stance": r["net_stance"],
            "net_reason": r["net_reason"],
            "key_issues": json.loads(r["body_json"]),
            "sources":    json.loads(r["sources_json"]),
            "created_at": r["created_at"],
        })
    return result


# ── admin stats ───────────────────────────────────────────────────────────────

def get_admin_stats() -> dict:
    import time as _time
    now = int(_time.time())
    today_start     = now - (now % 86400)
    yesterday_start = today_start - 86400

    with _conn() as conn:
        total_news     = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
        hot_news       = conn.execute("SELECT COUNT(*) FROM news_items WHERE is_hot=1").fetchone()[0]
        today_news     = conn.execute("SELECT COUNT(*) FROM news_items WHERE fetched_at>=?", (today_start,)).fetchone()[0]
        yesterday_news = conn.execute(
            "SELECT COUNT(*) FROM news_items WHERE fetched_at>=? AND fetched_at<?",
            (yesterday_start, today_start),
        ).fetchone()[0]
        total_users    = conn.execute("SELECT COUNT(DISTINCT user_id) FROM channels").fetchone()[0]
        total_channels = conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        total_alerts   = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        source_rows    = conn.execute(
            "SELECT source, COUNT(*) cnt FROM news_items GROUP BY source ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        daily_rows     = conn.execute(
            """SELECT date(fetched_at, 'unixepoch', 'localtime') day, COUNT(*) cnt
               FROM news_items
               WHERE fetched_at >= ?
               GROUP BY day ORDER BY day""",
            (now - 86400 * 7,),
        ).fetchall()

    return {
        "total_news":        total_news,
        "hot_news":          hot_news,
        "today_news":        today_news,
        "yesterday_news":    yesterday_news,
        "total_users":       total_users,
        "total_channels":    total_channels,
        "total_alerts_fired": total_alerts,
        "news_by_source":    [{"source": r["source"], "count": r["cnt"]} for r in source_rows],
        "news_daily":        [{"day": r["day"], "count": r["cnt"]} for r in daily_rows],
    }


# ── admin user management ─────────────────────────────────────────────────────

def get_users_summary() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT
                c.user_id,
                COUNT(*)            AS channel_count,
                SUM(c.alert_enabled) AS alert_on_count,
                MAX(c.created_at)   AS last_seen
            FROM channels c
            GROUP BY c.user_id
            ORDER BY last_seen DESC
        """).fetchall()
        return [dict(r) for r in rows]

def get_user_detail(user_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM alerts a WHERE a.channel_id=c.id) AS fired_count
            FROM channels c
            WHERE c.user_id=?
            ORDER BY c.created_at DESC
        """, (user_id,)).fetchall()
        return [dict(r) for r in rows]


# ── user_profiles ─────────────────────────────────────────────────────────────

def upsert_user_profile(user_id: str, username: str, global_name: str, avatar: str | None) -> None:
    with _conn() as conn:
        conn.execute(
            """INSERT INTO user_profiles (user_id, username, global_name, avatar, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=excluded.username, global_name=excluded.global_name,
                 avatar=excluded.avatar, updated_at=excluded.updated_at""",
            (user_id, username, global_name, avatar, int(time.time())),
        )

def get_user_profile(user_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


# ── alert history ─────────────────────────────────────────────────────────────

def get_user_alerts(user_id: str) -> list[dict]:
    with _conn() as conn:
        channel_rows = conn.execute("""
            SELECT a.fired_at, a.side, c.stock_code, c.channel_type,
                   'channel' AS alert_type, NULL AS headline,
                   NULL AS direction, NULL AS stock_codes
            FROM alerts a
            JOIN channels c ON a.channel_id = c.id
            WHERE c.user_id = ?
        """, (user_id,)).fetchall()

        hot_rows = conn.execute("""
            SELECT fired_at, NULL AS side, NULL AS stock_code, NULL AS channel_type,
                   'hot_news' AS alert_type, headline, direction, stock_codes
            FROM hot_news_alerts
            WHERE user_id = ?
        """, (user_id,)).fetchall()

    combined = [dict(r) for r in channel_rows] + [dict(r) for r in hot_rows]
    combined.sort(key=lambda r: r["fired_at"], reverse=True)
    return combined


def save_hot_news_alert(user_id: str, stock_codes: list[str],
                        headline: str, direction: str, cluster_id: str | None = None) -> None:
    import json
    now = int(time.time())
    with _conn() as conn:
        conn.execute(
            """INSERT INTO hot_news_alerts (user_id, stock_codes, headline, direction, cluster_id, fired_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, json.dumps(stock_codes, ensure_ascii=False), headline, direction, cluster_id, now),
        )


# ── announcements ─────────────────────────────────────────────────────────────

def get_announcements(limit: int = 20) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM announcements ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_announcement(title: str, content: str, author_id: str, author_name: str) -> int:
    now = int(time.time())
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO announcements (title, content, author_id, author_name, created_at) VALUES (?,?,?,?,?)",
            (title, content, author_id, author_name, now),
        )
        return cur.lastrowid


def delete_announcement(announcement_id: int) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
