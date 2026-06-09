"""핫뉴스 판별 — 점수 항목을 플러그인처럼 추가 가능한 구조."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import os

from .config import (
    HOT_KEYWORDS,
    SOURCE_COUNT_CAP,
    SOURCE_COUNT_WEIGHT,
    STOCK_PRICE_BONUS,
    STOCK_PRICE_KEYWORDS,
)


@dataclass
class ClusterSignal:
    cluster_id:   str
    titles:       list[str]        # 클러스터 내 모든 기사 제목
    source_count: int              # 보도 매체 수
    sources:      list[str] = field(default_factory=list)  # 매체명 목록
    # 향후 시세 신호 등을 여기에 추가


ScoreFn = Callable[[ClusterSignal], float]

_SCORE_PLUGINS: list[tuple[str, ScoreFn]] = []


def register_scorer(name: str, fn: ScoreFn) -> None:
    """새 점수 항목을 런타임에 등록."""
    _SCORE_PLUGINS.append((name, fn))


# ── 기본 점수 항목 ─────────────────────────────────────────────────────────────

def _score_source_count(sig: ClusterSignal) -> float:
    return min(sig.source_count * SOURCE_COUNT_WEIGHT, SOURCE_COUNT_CAP)


def _score_keywords(sig: ClusterSignal) -> float:
    combined = " ".join(sig.titles)
    total = 0.0
    seen: set[str] = set()
    for kw, weight in HOT_KEYWORDS:
        if kw not in seen and kw in combined:
            total += weight
            seen.add(kw)
    return min(total, 70.0)


def _score_dart(sig: ClusterSignal) -> float:
    """DART 공시 포함 클러스터 — 중요 공시 키워드에 따라 차등 보너스."""
    if "DART" not in sig.sources:
        return 0.0
    combined = " ".join(sig.titles)
    # 기본 15점 + 중요도에 따라 추가 보너스 (상향)
    dart_keywords = [
        ("상장폐지", 25.0),
        ("영업정지", 22.0),
        ("관리종목", 20.0),
        ("횡령",    22.0),
        ("배임",    22.0),
        ("합병",    20.0),
        ("분할",    18.0),
        ("유상증자", 18.0),
        ("무상증자", 12.0),
        ("자기주식", 12.0),
        ("영업양수", 15.0),
        ("영업양도", 15.0),
        ("공개매수", 20.0),
        ("감사의견", 18.0),
        ("최대주주", 12.0),
        ("대표이사",  5.0),  # 단순 변경은 낮게
        ("실적",     8.0),
        ("배당",     6.0),
    ]
    bonus = 15.0  # 중요 키워드 없는 공시의 기본 점수
    seen: set[str] = set()
    for kw, w in dart_keywords:
        if kw not in seen and kw in combined:
            bonus += w
            seen.add(kw)
    return min(bonus, 60.0)


def _score_stock_price(sig: ClusterSignal) -> float:
    """주가 움직임 키워드가 2개 이상 등장하면 보너스 추가."""
    combined = " ".join(sig.titles)
    hit = sum(1 for kw in STOCK_PRICE_KEYWORDS if kw in combined)
    return STOCK_PRICE_BONUS if hit >= 2 else 0.0


register_scorer("source_count", _score_source_count)
register_scorer("keywords",     _score_keywords)
register_scorer("dart",         _score_dart)
register_scorer("stock_price",  _score_stock_price)


# ── 공개 API ──────────────────────────────────────────────────────────────────

def compute_hot_score(sig: ClusterSignal) -> float:
    score = sum(fn(sig) for _, fn in _SCORE_PLUGINS)
    return min(round(score, 1), 100.0)


def is_hot(score: float) -> bool:
    threshold = float(os.getenv("HOT_SCORE_THRESHOLD", "70"))
    return score >= threshold
