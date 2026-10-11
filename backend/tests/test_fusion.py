import pytest

from app.retrieval.fusion import RRF_K, reciprocal_rank_fusion


def test_rrf_adds_one_over_k_plus_rank_from_every_list():
    fused = dict(reciprocal_rank_fusion([["a", "b"], ["b", "c"]]))

    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))
    assert fused["c"] == pytest.approx(1 / (RRF_K + 2))


def test_a_chunk_found_by_several_searches_beats_one_found_once():
    vector = ["vector_only", "both"]
    keyword = ["keyword_only", "both"]

    assert reciprocal_rank_fusion([vector, keyword])[0][0] == "both"


def test_ties_keep_the_order_items_were_first_seen():
    assert [item for item, _ in reciprocal_rank_fusion([["a"], ["b"]])] == ["a", "b"]


def test_duplicates_in_one_list_only_count_once():
    fused = dict(reciprocal_rank_fusion([["a", "a", "b"]]))

    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 2))


def test_empty_lists():
    assert reciprocal_rank_fusion([[], []]) == []
