import os

# ── RSS 피드 설정 ─────────────────────────────────────────────────────────────
RSS_FEEDS = [
    {"source": "연합뉴스",    "url": "https://www.yna.co.kr/rss/economy.xml"},
    {"source": "한국경제",    "url": "https://www.hankyung.com/feed/economy"},
    {"source": "한경 증권",   "url": "https://www.hankyung.com/feed/finance"},
    {"source": "한경 IT",     "url": "https://www.hankyung.com/feed/it"},
    {"source": "한경 국제",   "url": "https://www.hankyung.com/feed/international"},
    {"source": "매일경제",    "url": "https://www.mk.co.kr/rss/40300001/"},
    {"source": "매경 증권",   "url": "https://www.mk.co.kr/rss/50200011/"},
    {"source": "매경 산업",   "url": "https://www.mk.co.kr/rss/50400012/"},
    {"source": "매경 글로벌", "url": "https://www.mk.co.kr/rss/30100041/"},
]

# ── DART 전자공시 API ─────────────────────────────────────────────────────────
DART_API_KEY: str | None = os.getenv("DART_API_KEY")
DART_ENDPOINT = "https://opendart.fss.or.kr/api/list.json"

# ── 폴링 주기 ─────────────────────────────────────────────────────────────────
POLL_INTERVAL: int = int(os.getenv("NEWS_POLL_INTERVAL", "60"))  # 초

# ── 클러스터링 ─────────────────────────────────────────────────────────────────
CLUSTER_SIMILARITY_THRESHOLD: float = 0.35   # 자카드 유사도 임계값
CLUSTER_WINDOW_SECONDS: int = 3600 * 6       # 같은 클러스터로 묶을 최대 시간 간격

# ── 핫뉴스 판별 ───────────────────────────────────────────────────────────────
HOT_SCORE_THRESHOLD: float  = float(os.getenv("HOT_SCORE_THRESHOLD",  "70"))
HOT_EMPHASIS_THRESHOLD: float = float(os.getenv("HOT_EMPHASIS_THRESHOLD", "60"))

# 주가 영향 키워드 (hot_score에 가산)
HOT_KEYWORDS: list[tuple[str, float]] = [
    # 주가·시세 직접 관련
    ("주가",      15.0),
    ("급등",      18.0),
    ("급락",      20.0),
    ("폭등",      20.0),
    ("폭락",      25.0),
    ("상한가",    22.0),
    ("하한가",    25.0),
    ("목표주가",  12.0),
    ("시가총액",  10.0),
    ("증시",       8.0),
    # 기업 이벤트
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
    # 거시경제
    ("금리",      10.0),
    ("기준금리",  12.0),
    ("환율",      10.0),
    ("코스피",    10.0),
    ("코스닥",    10.0),
]

# 주가 움직임 키워드 — 2개 이상 동시 등장 시 보너스 판정에 사용
STOCK_PRICE_KEYWORDS: list[str] = [
    "주가", "급등", "급락", "폭등", "폭락", "상한가", "하한가",
    "코스피", "코스닥", "증시", "시가총액", "목표주가",
]
STOCK_PRICE_BONUS: float = 15.0   # 2개 이상 등장 시 추가 보너스

# 매체 수에 따른 가산 (클러스터 소스 수 × 이 값)
SOURCE_COUNT_WEIGHT: float = 10.0
SOURCE_COUNT_CAP: float = 30.0   # 최대 가산 한도
