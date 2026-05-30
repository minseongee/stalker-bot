"""핫뉴스 판별 — 점수 항목을 플러그인처럼 추가 가능한 구조."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .config import (
    HOT_KEYWORDS,
    HOT_SCORE_THRESHOLD,
    SOURCE_COUNT_CAP,
    SOURCE_COUNT_WEIGHT,
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
    """DART 공시가 포함된 클러스터는 기본 50점 보너스."""
    if "DART" not in sig.sources:
        return 0.0
    combined = " ".join(sig.titles)
    # 중요 공시 키워드별 추가 보너스
    dart_keywords = [
        ("유상증자", 20.0), ("무상증자", 15.0), ("자기주식", 15.0),
        ("합병", 25.0), ("분할", 20.0), ("영업양수", 20.0), ("영업양도", 20.0),
        ("최대주주", 15.0), ("대표이사", 10.0), ("감사의견", 20.0),
        ("상장폐지", 30.0), ("관리종목", 25.0), ("횡령", 30.0), ("배임", 30.0),
        ("실적", 10.0), ("배당", 10.0),
    ]
    bonus = 50.0
    seen: set[str] = set()
    for kw, w in dart_keywords:
        if kw not in seen and kw in combined:
            bonus += w
            seen.add(kw)
    return min(bonus, 100.0)


register_scorer("source_count", _score_source_count)
register_scorer("keywords",     _score_keywords)
register_scorer("dart",         _score_dart)


# ── 공개 API ──────────────────────────────────────────────────────────────────

def compute_hot_score(sig: ClusterSignal) -> float:
    score = sum(fn(sig) for _, fn in _SCORE_PLUGINS)
    return min(round(score, 1), 100.0)


def is_hot(score: float) -> bool:
    return score >= HOT_SCORE_THRESHOLD
