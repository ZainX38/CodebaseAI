import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal

from app.indexing.chunking import Chunk
from app.indexing.jobs import get_embedding_text
from app.indexing.store import IndexStore
from app.retrieval.bm25 import BM25Index
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.rerank import Candidate, rerank
from app.retrieval.symbols import NAMED_DIRECTLY, SymbolMatch, SymbolTable, find_named_symbols


# Fixed before any evaluation run
VECTOR_CANDIDATES = 20
KEYWORD_CANDIDATES = 20
RERANK_POOL = 15
FINAL_RESULTS = 6
# Keeps one request from making BM25 and the symbol lookup do a huge amount of work
MAX_QUESTION_CHARS = 1000


# A problem with the question itself (too long), whose message is safe to show the user.
# A dedicated type, so an unexpected internal ValueError is never shown by mistake
class QuestionError(ValueError):
    pass

RetrievalMode = Literal["vector", "hybrid"]


# ---------------------- Everything retrieval needs for one namespace ---------
# Chunks and files come from SQLite. The BM25 index is built in memory from the same
# text that was embedded (path + symbol + code), so both searches see the same thing
@dataclass
class RepositoryIndex:
    namespace: str
    chunks: list[Chunk]
    files: dict[str, str]
    bm25: BM25Index
    symbols: SymbolTable

    def __post_init__(self):
        self.chunks_by_id = {chunk.id: chunk for chunk in self.chunks}

    @classmethod
    def build(cls, namespace: str, chunks: list[Chunk], files: dict[str, str]) -> "RepositoryIndex":
        bm25 = BM25Index({chunk.id: get_embedding_text(chunk) for chunk in chunks})
        return cls(namespace, chunks, files, bm25, SymbolTable.build(chunks, files))


# A few recently used repositories stay in memory, so BM25 isn't rebuilt for every question.
# A namespace never changes once it is ready (it is one exact commit), so the cache is never stale
_repository_cache: OrderedDict[str, RepositoryIndex] = OrderedDict()
REPOSITORY_CACHE_SIZE = 8


async def load_repository_index(store: IndexStore, namespace: str) -> RepositoryIndex:
    if namespace in _repository_cache:
        _repository_cache.move_to_end(namespace)
        return _repository_cache[namespace]

    def build() -> RepositoryIndex:
        return RepositoryIndex.build(namespace, store.get_chunks(namespace), store.get_files(namespace))

    repository = await asyncio.to_thread(build)
    # An empty index means the namespace isn't ready (or was deleted); it is never cached
    if not repository.chunks:
        return repository
    _repository_cache[namespace] = repository
    if len(_repository_cache) > REPOSITORY_CACHE_SIZE:
        _repository_cache.popitem(last=False)
    return repository


# Everything that was found, kept for the evaluation and for debugging
@dataclass
class RetrievalResult:
    mode: RetrievalMode
    candidates: list[Candidate]
    vector_hits: list[tuple[str, float]]
    keyword_hits: list[tuple[str, float]]
    symbol_matches: list[SymbolMatch]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "vector_hits": self.vector_hits,
            "keyword_hits": self.keyword_hits,
            "symbol_matches": [match.__dict__ for match in self.symbol_matches],
            "candidates": [
                {
                    "chunk_id": candidate.chunk.id,
                    "path": candidate.chunk.path,
                    "start_line": candidate.chunk.start_line,
                    "end_line": candidate.chunk.end_line,
                    "symbol": candidate.chunk.symbol,
                    "fused_score": candidate.fused_score,
                    "final_score": candidate.final_score,
                    "vector_rank": candidate.vector_rank,
                    "keyword_rank": candidate.keyword_rank,
                    "symbol_score": candidate.symbol_score,
                }
                for candidate in self.candidates
            ],
        }


# The symbol lookup returns functions; every chunk of each function becomes one ranked
# list for RRF (a long function split into windows keeps all of its windows)
def get_symbol_ranking(matches: list[SymbolMatch], table: SymbolTable) -> tuple[list[str], dict[str, int]]:
    ranking, scores = [], {}
    for match in matches:
        for chunk in table.chunks_by_symbol.get((match.symbol, match.path), []):
            if chunk.id not in scores:
                ranking.append(chunk.id)
                scores[chunk.id] = match.score
    return ranking, scores


# ---------------------- Retrieval -------------------------------------------
# "vector": cosine similarity only (the evaluation baseline)
# "hybrid": vector + BM25 + exact symbol lookup, fused with RRF, then reranked
async def retrieve(
    question: str,
    repository: RepositoryIndex,
    provider: Any,
    vector_store: Any,
    mode: RetrievalMode = "hybrid",
    top_k: int = FINAL_RESULTS,
) -> RetrievalResult:
    if len(question) > MAX_QUESTION_CHARS:
        raise QuestionError(f"Questions can be at most {MAX_QUESTION_CHARS} characters")

    [query_vector] = await provider.embed([question])
    vector_hits = await asyncio.to_thread(vector_store.query, repository.namespace, query_vector, VECTOR_CANDIDATES)
    # Pinecone could still hold vectors from an older chunking of the same commit.
    # Anything without a matching chunk in SQLite is ignored
    vector_hits = [(chunk_id, score) for chunk_id, score in vector_hits if chunk_id in repository.chunks_by_id]
    vector_ids = [chunk_id for chunk_id, _ in vector_hits]

    if mode == "vector":
        candidates = [
            Candidate(repository.chunks_by_id[chunk_id], fused_score=score, vector_rank=rank, final_score=score)
            for rank, (chunk_id, score) in enumerate(vector_hits[:top_k], start=1)
        ]
        return RetrievalResult(mode, candidates, vector_hits, [], [])

    # BM25 and the symbol lookup are CPU work over every chunk, so they run in a thread
    # instead of blocking other requests (e.g. status polling) on the event loop
    keyword_hits, symbol_matches, (symbol_ids, symbol_scores) = await asyncio.to_thread(
        search_keywords_and_symbols, question, repository
    )
    keyword_ids = [chunk_id for chunk_id, _ in keyword_hits]

    vector_ranks = {chunk_id: rank for rank, chunk_id in enumerate(vector_ids, start=1)}
    keyword_ranks = {chunk_id: rank for rank, chunk_id in enumerate(keyword_ids, start=1)}

    fused = reciprocal_rank_fusion([vector_ids, keyword_ids, symbol_ids])
    fused_scores = dict(fused)

    # A chunk only found by the symbol lookup scores at most 1/61 in RRF, so it could
    # miss the top 15 if vector and BM25 agree on other chunks. Functions named in the
    # question are always added to the pool, so the reranker can see them
    pool_ids = [chunk_id for chunk_id, _ in fused[:RERANK_POOL]]
    pool_ids += [
        chunk_id for chunk_id in symbol_ids
        if symbol_scores[chunk_id] >= NAMED_DIRECTLY and chunk_id not in pool_ids
    ]
    pool = [
        Candidate(
            repository.chunks_by_id[chunk_id],
            fused_score=fused_scores[chunk_id],
            vector_rank=vector_ranks.get(chunk_id),
            keyword_rank=keyword_ranks.get(chunk_id),
            symbol_score=symbol_scores.get(chunk_id, 0),
        )
        for chunk_id in pool_ids
    ]

    candidates = rerank(pool, question)[:top_k]
    return RetrievalResult(mode, candidates, vector_hits, keyword_hits, symbol_matches)


def search_keywords_and_symbols(question: str, repository: RepositoryIndex):
    keyword_hits = repository.bm25.search(question, KEYWORD_CANDIDATES)
    symbol_matches = find_named_symbols(question, repository.symbols, repository.files)
    return keyword_hits, symbol_matches, get_symbol_ranking(symbol_matches, repository.symbols)
