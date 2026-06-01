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
    items: DB에서 가져온 기사 dicts (published_at, title, guid 필드 필요).
    각 item에 cluster_id를 할당해 반환.
    """
    clusters: list[dict] = []  # [{id, tokens, latest_ts}]

    for item in sorted(items, key=lambda x: x["published_at"]):
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
                    cl["tokens"] |= tokens
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
