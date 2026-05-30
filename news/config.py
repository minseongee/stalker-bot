import os

# ── RSS 피드 설정 ─────────────────────────────────────────────────────────────
RSS_FEEDS = [
    {"source": "연합뉴스",  "url": "https://www.yonhapnewstv.co.kr/feed/"},
    {"source": "한국경제",  "url": "https://www.hankyung.com/feed/economy"},
    {"source": "매일경제",  "url": "https://www.mk.co.kr/rss/40300001/"},
]

# ── DART 전자공시 API ─────────────────────────────────────────────────────────
DART_API_KEY: str | None = os.getenv("DART_API_KEY")
DART_ENDPOINT = "https://opendart.fss.or.kr/api/list.json"

# ── 폴링 주기 ─────────────────────────────────────────────────────────────────
POLL_INTERVAL: int = int(os.getenv("NEWS_POLL_INTERVAL", "60"))  # 초

# ── 클러스터링 ─────────────────────────────────────────────────────────────────
CLUSTER_SIMILARITY_THRESHOLD: float = 0.25   # 자카드 유사도 임계값
CLUSTER_WINDOW_SECONDS: int = 3600 * 6       # 같은 클러스터로 묶을 최대 시간 간격

# ── 핫뉴스 판별 ───────────────────────────────────────────────────────────────
HOT_SCORE_THRESHOLD: float = float(os.getenv("HOT_SCORE_THRESHOLD", "70"))

# 주가 영향 키워드 (hot_score에 가산)
HOT_KEYWORDS: list[tuple[str, float]] = [
    ("실적",     15.0),
    ("영업이익",  15.0),
    ("순이익",    12.0),
    ("인수합병",  20.0),
    ("M&A",      20.0),
    ("유상증자",  20.0),
    ("무상증자",  15.0),
    ("수주",      18.0),
    ("신약승인",  22.0),
    ("임상",      15.0),
    ("상장폐지",  25.0),
    ("관리종목",  20.0),
    ("공시",      10.0),
    ("배당",      12.0),
    ("자사주",    12.0),
    ("매각",      15.0),
    ("파산",      25.0),
    ("부도",      25.0),
    ("리콜",      18.0),
    ("과징금",    15.0),
    ("제재",      15.0),
    ("금리",      10.0),
    ("기준금리",  12.0),
    ("환율",      8.0),
    ("코스피",    5.0),
    ("코스닥",    5.0),
]

# 매체 수에 따른 가산 (클러스터 소스 수 × 이 값)
SOURCE_COUNT_WEIGHT: float = 10.0
SOURCE_COUNT_CAP: float = 30.0   # 최대 가산 한도
