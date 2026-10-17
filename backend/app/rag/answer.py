import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
import json
import re
import time
from typing import Any

from app import config
from app.ai.providers import ProviderError
from app.indexing.vector_store import VectorStoreError
from app.rag.citations import validate_citations
from app.rag.context import ContextMode, Excerpt, build_context, format_context, get_context_budget, new_fence
from app.rag.prompt import ANSWER_SCHEMA, SYSTEM_PROMPT, build_messages
from app.retrieval.retriever import QuestionError, RepositoryIndex, RetrievalMode, retrieve


# Half of a surrogate pair on its own can't be encoded as UTF-8, so it is removed
def remove_surrogates(text: str) -> str:
    return "".join(character for character in text if not 0xD800 <= ord(character) <= 0xDFFF)


def is_hex(text: str) -> bool:
    return len(text) == 4 and all(character in "0123456789abcdefABCDEF" for character in text)


# ---------------------- Reads the answer while it is still being written ----
# The model streams JSON like {"answer": "The function ...", "citations": [...]}.
# The citations can only be checked once the JSON is complete, but the answer text
# can be shown as it arrives. This pulls the "answer" string out piece by piece,
# decoding JSON escapes (newlines, quotes, é) even when they are split across pieces
class AnswerExtractor:
    KEY = '"answer"'
    KEY_AND_QUOTE = re.compile(r'"answer"\s*:\s*"')  # the key, a colon and the opening quote
    KEY_SO_FAR = re.compile(r'"answer"\s*(:\s*)?')  # the same, with the end not arrived yet

    def __init__(self):
        self.buffer = ""
        self.position: int | None = None  # where the answer string's text starts
        self.search_from = 0  # where to look for the key ("answer" found earlier wasn't the key)
        self.finished = False

    def feed(self, piece: str) -> str:
        self.buffer += piece
        if self.finished:
            return ""

        # The text "answer" can also appear somewhere else first, e.g. {"citations": ["answer"], ...},
        # so a match only counts if a colon and an opening quote follow it
        while self.position is None:
            key_index = self.buffer.find(self.KEY, self.search_from)
            if key_index == -1:
                return ""
            match = self.KEY_AND_QUOTE.match(self.buffer, key_index)
            if match:
                self.position = match.end()
            elif self.KEY_SO_FAR.fullmatch(self.buffer, key_index):
                return ""  # it could still be the key; wait for the next piece
            else:
                self.search_from = key_index + 1

        decoded = []
        while self.position < len(self.buffer):
            character = self.buffer[self.position]
            if character == '"':
                self.finished = True
                break
            if character != "\\":
                decoded.append(character)
                self.position += 1
                continue

            # An escape needs its next character (or 4 hex digits for \u) to be decoded
            is_unicode = self.buffer[self.position + 1 : self.position + 2] == "u"
            escape_end = self.position + (6 if is_unicode else 2)
            if escape_end > len(self.buffer):
                break
            # An emoji is written as two \u escapes (a "surrogate pair"). Decoding only the
            # first half gives a broken character, so it waits for the second half too
            hex_digits = self.buffer[self.position + 2 : escape_end]
            is_high_surrogate = is_unicode and is_hex(hex_digits) and 0xD800 <= int(hex_digits, 16) <= 0xDBFF
            if is_high_surrogate:
                if escape_end + 6 > len(self.buffer):
                    break
                if self.buffer[escape_end : escape_end + 2] == "\\u":
                    escape_end += 6
            try:
                text = json.loads(f'"{self.buffer[self.position:escape_end]}"')
                # A half of a surrogate pair on its own can't be sent as UTF-8, so it is dropped
                decoded.append("".join(character for character in text if not 0xD800 <= ord(character) <= 0xDFFF))
            except json.JSONDecodeError:
                pass  # a broken escape is skipped rather than stopping the stream
            self.position = escape_end

        return "".join(decoded)


# ---------------------- Everything about one answer -------------------------
# Kept complete for the evaluation: the exact messages sent, what was retrieved,
# the excerpts, the raw model output and how the citations were judged
@dataclass
class AnswerResult:
    question: str
    retrieval_mode: str
    context_mode: str
    model: str
    messages: list[dict[str, str]] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)
    excerpts: list[Excerpt] = field(default_factory=list)
    raw_response: str = ""
    answer: str = ""
    raw_citations: Any = None
    citations: list[Excerpt] = field(default_factory=list)
    rejected_citations: list[Any] = field(default_factory=list)
    normalised_citations: list[str] = field(default_factory=list)
    error: str | None = None
    seconds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["excerpts"] = [asdict(excerpt) for excerpt in self.excerpts]
        data["citations"] = [excerpt.id for excerpt in self.citations]
        return data


# Events produced while answering. The router turns them into SSE; the evaluation
# just waits for the last one, which carries the full AnswerResult
@dataclass
class AnswerEvent:
    kind: str  # "status", "token" or "result"
    data: Any


# ---------------------- The whole pipeline ----------------------------------
# retrieve --> context --> chat (structured JSON, streamed) --> validate citations
async def generate_answer(
    question: str,
    repository: RepositoryIndex,
    provider: Any,
    vector_store: Any,
    retrieval_mode: RetrievalMode = "hybrid",
    context_mode: ContextMode = "implementation",
    model: str | None = None,
) -> AsyncIterator[AnswerEvent]:
    result = AnswerResult(question, retrieval_mode, context_mode, model or provider.chat_model)
    started = time.perf_counter()

    try:
        yield AnswerEvent("status", "retrieving")
        retrieval = await retrieve(question, repository, provider, vector_store, mode=retrieval_mode)
        result.retrieval = retrieval.to_dict()
        result.seconds["retrieval"] = round(time.perf_counter() - started, 3)

        budget = get_context_budget(SYSTEM_PROMPT, question)
        # Context assembly can parse large files, so it runs off the event loop too
        result.excerpts = await asyncio.to_thread(
            build_context, [candidate.chunk for candidate in retrieval.candidates], repository, question, budget, context_mode
        )
        # A new random fence for every question, so file content can't fake an excerpt boundary
        result.messages = build_messages(question, format_context(result.excerpts, new_fence()))

        yield AnswerEvent("status", "generating")
        generation_started = time.perf_counter()
        extractor = AnswerExtractor()
        pieces = []
        async for piece in provider.chat(result.messages, schema=ANSWER_SCHEMA, model=model, max_tokens=config.MAX_OUTPUT_TOKENS):
            pieces.append(piece)
            text = extractor.feed(piece)
            if text:
                yield AnswerEvent("token", text)
        result.raw_response = "".join(pieces)
        result.seconds["generation"] = round(time.perf_counter() - generation_started, 3)

        yield AnswerEvent("status", "validating")
        parse_answer(result)
    except (ProviderError, QuestionError, VectorStoreError) as error:
        # These messages are written to be shown to the user
        result.error = str(error)
    finally:
        result.seconds["total"] = round(time.perf_counter() - started, 3)

    yield AnswerEvent("result", result)


# The JSON schema makes the shape very likely, but a model can still stop half way
# (output limit) or return something else, so the result is checked rather than trusted
def parse_answer(result: AnswerResult) -> None:
    try:
        parsed = json.loads(result.raw_response)
    except json.JSONDecodeError:
        result.error = "The model returned an answer that isn't valid JSON (it may have hit the output limit)"
        return

    if not isinstance(parsed, dict) or not isinstance(parsed.get("answer"), str):
        result.error = "The model's answer doesn't have the expected shape"
        return

    if not parsed["answer"].strip():
        result.error = "The model returned an empty answer"
        return

    # Kept exactly as streamed (not stripped), so the final answer matches the tokens
    result.answer = remove_surrogates(parsed["answer"])
    result.raw_citations = parsed.get("citations")
    check = validate_citations(result.raw_citations, result.excerpts)
    result.citations = check.valid
    result.rejected_citations = [remove_surrogates(item) if isinstance(item, str) else item for item in check.rejected]
    result.normalised_citations = check.normalised
