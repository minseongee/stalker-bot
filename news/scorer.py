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
    cluster_id:  str
    titles:      list[str]   # 클러스터 내 모든 기사 제목
    source_count: int        # 보도 매체 수
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


register_scorer("source_count", _score_source_count)
register_scorer("keywords",     _score_keywords)


# ── 공개 API ──────────────────────────────────────────────────────────────────

def compute_hot_score(sig: ClusterSignal) -> float:
    score = sum(fn(sig) for _, fn in _SCORE_PLUGINS)
    return min(round(score, 1), 100.0)


def is_hot(score: float) -> bool:
    return score >= HOT_SCORE_THRESHOLD
