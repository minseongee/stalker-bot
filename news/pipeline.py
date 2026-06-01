"""전체 뉴스 파이프라인 — 수집 → 클러스터링 → 스코어링 → 정제."""
import asyncio
import json
import time
import traceback
from typing import Callable

from server.database import (
    get_cluster_items,
    get_recent_news_items,
    mark_cluster_refined,
    purge_old_news,
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


_REFINE_SEMAPHORE = asyncio.Semaphore(3)  # 동시 GPT 호출 최대 3개


async def _refine_one(cid: str, hot_score: float, items_in_cluster: list[dict]) -> dict | None:
    """단일 클러스터 GPT 정제 — 세마포어로 동시 호출 제한."""
    async with _REFINE_SEMAPHORE:
        refined = await refine_cluster(cid, items_in_cluster)
    if refined is None:
        return None
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
    fetched_at = max((item.get("fetched_at") or 0) for item in items_in_cluster)
    mark_cluster_refined(cid)
    return {
        "cluster_id": cid,
        "fetched_at": fetched_at,
        "hot_score":  hot_score,
        **refined,
    }


async def _refine_and_broadcast(hot_clusters: dict[str, float]) -> None:
    """미정제 핫 클러스터를 GPT로 정제하고 콜백으로 전송."""
    # DB에 is_hot=1인 미정제 클러스터도 포함 (수동 업데이트 등)
    from server.database import get_unrefined_hot_clusters
    extra_rows = {r["cluster_id"]: r["hot_score"]
                  for r in get_unrefined_hot_clusters()
                  if r["cluster_id"] not in hot_clusters}
    all_clusters = {**hot_clusters, **extra_rows}

    newly_refined: list[dict] = []
    gpt_tasks: list[tuple[str, float, list[dict]]] = []  # GPT 정제 필요한 클러스터
    for cid, hot_score in all_clusters.items():
        items_in_cluster = get_cluster_items(cid)
        has_summary   = [i for i in items_in_cluster if i.get("summary")]
        needs_summary = [i for i in items_in_cluster if not i.get("summary")]

        if has_summary and needs_summary:
            # 재클러스터링으로 새 기사가 편입된 경우 — GPT 재호출 없이 기존 요약 복사
            donor = has_summary[0]
            sources_json = json.dumps(
                [{"source": i["source"], "url": i["url"], "title": i["title"]} for i in items_in_cluster],
                ensure_ascii=False,
            )
            for item in needs_summary:
                update_news_refined(
                    item["guid"],
                    donor["summary"],
                    donor["headline"],
                    donor["direction"],
                    donor.get("stock_tags") or "[]",
                    sources_json,
                )
            print(f"[Pipeline] 클러스터 {cid[:8]}… 편입 기사 {len(needs_summary)}건 요약 복사")
            fetched_at = max((i.get("fetched_at") or 0) for i in items_in_cluster)
            mark_cluster_refined(cid)
            newly_refined.append({
                "cluster_id": cid,
                "fetched_at": fetched_at,
                "hot_score":  hot_score,
                "is_update":  True,
                "headline":   donor["headline"],
                "summary":    donor["summary"],
                "direction":  donor["direction"],
                "stock_tags": json.loads(donor.get("stock_tags") or "[]"),
                "sources":    json.loads(sources_json),
            })
            continue

        if has_summary:
            # 모두 이미 처리됨 — refined_at만 기록
            mark_cluster_refined(cid)
            continue

        gpt_tasks.append((cid, hot_score, items_in_cluster))

    # GPT 정제 병렬 실행
    if gpt_tasks:
        results = await asyncio.gather(
            *[_refine_one(cid, score, items) for cid, score, items in gpt_tasks],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, dict):
                newly_refined.append(r)
            elif isinstance(r, Exception):
                print(f"[Pipeline] GPT 정제 오류: {r}")

    if newly_refined and _hot_callbacks:
        for fn in _hot_callbacks:
            try:
                fn(newly_refined)
            except Exception as e:
                print(f"[Pipeline] hot callback 오류: {e}")


async def _run_once() -> None:
    # 1) 수집
    new_items = await collect_all()
    if not new_items:
        await _refine_and_broadcast({})
        return

    inserted: list[dict] = []
    for item in new_items:
        if upsert_news_item(item):
            inserted.append(item)

    from datetime import datetime, timezone, timedelta
    from .config import RSS_FEEDS
    kst = datetime.now(timezone(timedelta(hours=9))).strftime("%H:%M:%S")
    per_source = {}
    for item in new_items:
        per_source[item["source"]] = per_source.get(item["source"], 0) + 1
    rss_str = " | ".join(f"{f['source']} {per_source.get(f['source'], 0)}건" for f in RSS_FEEDS)
    dart_str = f"DART {per_source.get('DART', 0)}건"
    print(f"[{kst}] [뉴스수집] 총 {len(new_items)}건 (신규 {len(inserted)}건) — {rss_str} | {dart_str}")

    # 2) 클러스터링 (최근 6시간 기사 전체 대상으로 재계산)
    # 신규 기사 없어도 임계치 변경 등을 반영하기 위해 항상 재계산
    since = int(time.time()) - 3600 * 6
    recent = get_recent_news_items(limit=500, since=since)
    if not recent:
        await _refine_and_broadcast({})
        return

    clustered = assign_clusters(recent)
    cluster_meta = build_cluster_meta(clustered)

    # 3) 스코어링 + DB 업데이트
    for item in clustered:
        update_news_cluster(
            item["guid"],
            item["cluster_id"],
            0.0,
            False,
        )

    hot_clusters: dict[str, float] = {}
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
            hot_clusters[cid] = score

    # 4) 핫 클러스터 정제 + 미처리 핫 클러스터 병합 처리
    await _refine_and_broadcast(hot_clusters)


_PURGE_INTERVAL = 3600 * 24  # 하루 한 번 정리

async def run_loop() -> None:
    """독립 asyncio 루프 — main.py 또는 bot에서 asyncio.create_task()로 실행."""
    print(f"[Pipeline] 뉴스 수집 루프 시작 (주기: {POLL_INTERVAL}초)")
    last_purge = 0.0
    while True:
        try:
            await _run_once()
        except Exception as e:
            print(f"[Pipeline] 루프 오류: {e}")
            traceback.print_exc()

        now = time.time()
        if now - last_purge >= _PURGE_INTERVAL:
            try:
                deleted = purge_old_news(days=7)
                print(f"[Pipeline] 7일 이전 뉴스 정리: {deleted}건 삭제")
            except Exception as e:
                print(f"[Pipeline] 정리 오류: {e}")
            last_purge = now

        await asyncio.sleep(POLL_INTERVAL)
