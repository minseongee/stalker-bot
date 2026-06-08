"""제목 기반 자카드 유사도 클러스터링."""
import re
import uuid

from .config import CLUSTER_SIMILARITY_THRESHOLD, CLUSTER_WINDOW_SECONDS

_STOP = frozenset([
    "이", "가", "을", "를", "은", "는", "의", "에", "에서", "로", "으로",
    "와", "과", "도", "만", "에게", "한", "하는", "하고", "하여", "하면",
    "대한", "관련", "위해", "통해", "따라", "위한", "대해", "으로서",
])


def _normalize(title: str) -> set[str]:
    title = re.sub(r"(\d),(\d)", r"\1\2", title)  # 8,800 → 8800
    title = re.sub(r"[^\w\s]", " ", title)
    tokens = title.split()
    return {t for t in tokens if len(t) > 1 and t not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def assign_clusters(items: list[dict]) -> list[dict]:
    """
    items: DB에서 가져온 기사 dicts (published_at, title, guid, cluster_id 필드 필요).
    이미 cluster_id가 할당된 기사는 기존 ID를 보존하고,
    새 기사(cluster_id 없음)만 기존 클러스터에 매칭하거나 새 클러스터를 생성한다.
    """
    cluster_map: dict[str, dict] = {}  # cluster_id → {id, tokens, latest_ts, items}

    # 1) 기존 cluster_id로 클러스터 씨드 초기화
    for item in items:
        cid = item.get("cluster_id")
        if not cid:
            continue
        if cid not in cluster_map:
            cluster_map[cid] = {
                "id":        cid,
                "tokens":    set(),
                "latest_ts": 0,
                "items":     [],
            }
        cluster_map[cid]["tokens"]    |= _normalize(item["title"])
        cluster_map[cid]["latest_ts"]  = max(cluster_map[cid]["latest_ts"], item["published_at"])
        cluster_map[cid]["items"].append(item["guid"])

    clusters = list(cluster_map.values())

    # 2) 새 기사만 기존/신규 클러스터에 할당
    for item in sorted(items, key=lambda x: x["published_at"]):
        if item.get("cluster_id"):
            continue  # 이미 배정된 기사는 건너뜀

        tokens = _normalize(item["title"])
        best_id = None
        best_score = 0.0

        for cl in clusters:
            if item["published_at"] - cl["latest_ts"] > CLUSTER_WINDOW_SECONDS:
                continue
            score = _jaccard(tokens, cl["tokens"])
            if score > best_score:
                best_score = score
                best_id = cl["id"]

        if best_score >= CLUSTER_SIMILARITY_THRESHOLD and best_id is not None:
            for cl in clusters:
                if cl["id"] == best_id:
                    cl["tokens"]   |= tokens
                    cl["latest_ts"] = max(cl["latest_ts"], item["published_at"])
                    cl["items"].append(item["guid"])
                    break
            item["cluster_id"] = best_id
        else:
            new_id = str(uuid.uuid4())
            clusters.append({
                "id":        new_id,
                "tokens":    tokens.copy(),
                "latest_ts": item["published_at"],
                "items":     [item["guid"]],
            })
            item["cluster_id"] = new_id

    return items


def build_cluster_meta(items: list[dict]) -> dict[str, dict]:
    """cluster_id → {item_count, source_count, guids} 집계."""
    meta: dict[str, dict] = {}
    for item in items:
        cid = item.get("cluster_id")
        if not cid:
            continue
        if cid not in meta:
            meta[cid] = {"item_count": 0, "sources": set(), "guids": []}
        meta[cid]["item_count"] += 1
        meta[cid]["sources"].add(item["source"])
        meta[cid]["guids"].append(item["guid"])
    for v in meta.values():
        v["source_count"] = len(v["sources"])
    return meta
