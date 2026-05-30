"""전체 뉴스 파이프라인 — 수집 → 클러스터링 → 스코어링 → 정제."""
import asyncio
import json
import time
from typing import Callable

from server.database import (
    get_cluster_items,
    get_recent_news_items,
    update_news_cluster,
    update_news_refined,
    upsert_cluster,
    upsert_news_item,
)

from .clusterer import assign_clusters, build_cluster_meta
from .collector import collect_all
from .config import POLL_INTERVAL
from .refiner import refine_cluster
from .scorer import ClusterSignal, compute_hot_score, is_hot

# 핫뉴스 발생 시 호출될 콜백 목록 (외부에서 등록)
_hot_callbacks: list[Callable[[list[dict]], None]] = []


def register_hot_callback(fn: Callable[[list[dict]], None]) -> None:
    """핫뉴스가 새로 정제될 때마다 호출할 콜백 등록."""
    _hot_callbacks.append(fn)


async def _run_once() -> None:
    # 1) 수집
    new_items = await collect_all()
    if not new_items:
        return

    inserted: list[dict] = []
    for item in new_items:
        if upsert_news_item(item):
            inserted.append(item)

    from datetime import datetime, timezone, timedelta
    kst = datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M:%S")
    per_source = {}
    for item in new_items:
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
    rss_sources = ["연합뉴스", "한국경제", "매일경제"]
    rss_str = " | ".join(f"{s} {per_source.get(s, 0)}건" for s in rss_sources)
    dart_str = f"DART {per_source.get('DART', 0)}건"
    print(f"[{kst}] [뉴스수집] 총 {len(new_items)}건 (신규 {len(inserted)}건) — {rss_str} | {dart_str}")

    # 2) 클러스터링 (최근 6시간 기사 전체 대상으로 재계산)
    since = int(time.time()) - 3600 * 6
    recent = get_recent_news_items(limit=500, since=since)
    clustered = assign_clusters(recent)
    cluster_meta = build_cluster_meta(clustered)

    # 3) 스코어링 + DB 업데이트
    for item in clustered:
        update_news_cluster(
            item["guid"],
            item["cluster_id"],
            0.0,   # 클러스터 스코어는 아래서 계산
            False,
        )

    hot_cluster_ids: list[str] = []
    for cid, meta in cluster_meta.items():
        items_in_cluster = [i for i in clustered if i.get("cluster_id") == cid]
        titles = [i["title"] for i in items_in_cluster]
        sig = ClusterSignal(
            cluster_id=cid,
            titles=titles,
            source_count=meta["source_count"],
            sources=list(meta["sources"]),
        )
        score = compute_hot_score(sig)
        hot = is_hot(score)

        upsert_cluster(cid, meta["item_count"], meta["source_count"], score, hot)

        for guid in meta["guids"]:
            update_news_cluster(guid, cid, score, hot)

        if hot:
            hot_cluster_ids.append(cid)

    if not hot_cluster_ids:
        return

    # 4) 핫 클러스터 중 미정제 건만 GPT 정제
    # cluster_id는 매 사이클마다 새 UUID라서 news_clusters로는 중복 방지 불가.
    # 기사 레벨에서 summary 존재 여부로 판단한다.
    newly_refined: list[dict] = []
    for cid in hot_cluster_ids:
        items_in_cluster = get_cluster_items(cid)
        if any(item.get("summary") for item in items_in_cluster):
            continue  # 이미 정제된 기사가 있으면 GPT 재호출 안 함
        refined = await refine_cluster(cid, items_in_cluster)
        if refined is None:
            continue

        sources_json = json.dumps(refined["sources"], ensure_ascii=False)
        stock_tags   = json.dumps(refined["stock_tags"], ensure_ascii=False)

        for item in items_in_cluster:
            update_news_refined(
                item["guid"],
                refined["summary"],
                refined["headline"],
                refined["direction"],
                stock_tags,
                sources_json,
            )
        print(f"[Pipeline] 클러스터 {cid[:8]}… 정제 완료: {refined['headline']}")

        newly_refined.append({
            "cluster_id": cid,
            **refined,
        })

    if newly_refined and _hot_callbacks:
        for fn in _hot_callbacks:
            try:
                fn(newly_refined)
            except Exception as e:
                print(f"[Pipeline] hot callback 오류: {e}")


async def run_loop() -> None:
    """독립 asyncio 루프 — main.py 또는 bot에서 asyncio.create_task()로 실행."""
    print(f"[Pipeline] 뉴스 수집 루프 시작 (주기: {POLL_INTERVAL}초)")
    while True:
        try:
            await _run_once()
        except Exception as e:
            print(f"[Pipeline] 루프 오류: {e}")
        await asyncio.sleep(POLL_INTERVAL)
