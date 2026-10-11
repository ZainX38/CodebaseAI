# ---------------------- Reciprocal Rank Fusion -------------------------------
# Vector search returns cosine similarities (0-1), BM25 returns unbounded scores and
# the symbol lookup returns its own points, so the scores can't be added together.
# RRF only uses each result's *position*: 1 / (k + rank) from every list it appears in.
# A chunk that is near the top of several lists beats one that is first in only one.
# k = 60 is the value from the original RRF paper; it stops rank 1 from dominating
RRF_K = 60


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}

    for ranking in rankings:
        # A list could contain the same chunk twice; only its best position counts
        for rank, item_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1 / (k + rank)
            first_seen.setdefault(item_id, len(first_seen))

    # Ties keep the order in which chunks were first seen, so the result is reproducible
    return sorted(scores.items(), key=lambda item: (-item[1], first_seen[item[0]]))
