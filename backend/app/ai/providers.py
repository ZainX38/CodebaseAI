from collections.abc import AsyncIterator
import json
import logging
import math
from typing import Any, Protocol

import httpx
from openai import AsyncOpenAI, OpenAIError

from app import config


logger = logging.getLogger(__name__)

# Raised for anything that goes wrong while talking to a model, so callers can
# show one clear message instead of handling httpx and openai errors separately
class ProviderError(Exception):
    pass


Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}


# ---------------------- The interface every provider follows -----------------
# The rest of the app only uses embed() and chat(), so switching between Ollama
# and OpenAI is a configuration change, not a code change
class AIProvider(Protocol):
    name: str
    embedding_model: str
    chat_model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    # Yields the answer piece by piece as the model writes it. Joining all the
    # pieces gives the full answer, so the same method works with and without streaming
    def chat(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = config.MAX_OUTPUT_TOKENS,
    ) -> AsyncIterator[str]: ...


# Every vector must be the same length and contain only real numbers.
# A NaN would silently break cosine similarity for every search afterwards
def validate_vectors(vectors: list[list[float]], expected_count: int) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise ProviderError(f"Expected {expected_count} embeddings but got {len(vectors)}")

    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) > 1 or 0 in dimensions:
        raise ProviderError("Embeddings have inconsistent dimensions")

    for vector in vectors:
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            raise ProviderError("Embedding contains an invalid value")

    return vectors


# ---------------------- Ollama (local, default) ------------------------------
class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str = config.OLLAMA_URL,
        embedding_model: str = config.OLLAMA_EMBEDDING_MODEL,
        chat_model: str = config.OLLAMA_CHAT_MODEL,
    ):
        self.base_url = base_url
        self.embedding_model = embedding_model
        self.chat_model = chat_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            async with httpx.AsyncClient(timeout=config.AI_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self.base_url}/api/embed",
                    # truncate=False makes Ollama fail instead of silently cutting a chunk
                    json={"model": self.embedding_model, "input": texts, "truncate": False},
                )
                response.raise_for_status()
        except httpx.HTTPError as error:
            # The details (URL, status) go to the server log; the user gets a clear hint instead
            logger.warning("Ollama embedding request failed: %s", error)
            raise ProviderError("Embedding failed. Is Ollama running with the embedding model installed?") from error

        try:
            vectors = response.json()["embeddings"]
        except (ValueError, KeyError) as error:
            raise ProviderError("Ollama returned an invalid embedding response") from error

        return validate_vectors(vectors, len(texts))

    async def chat(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = config.MAX_OUTPUT_TOKENS,
    ) -> AsyncIterator[str]:
        payload = {
            "model": model or self.chat_model,
            "messages": messages,
            "stream": True,
            "think": False,  # qwen3.5 would otherwise spend the output budget "thinking"
            "options": {"num_ctx": config.CONTEXT_TOKENS, "num_predict": max_tokens, "temperature": 0},
        }
        if schema:
            # Ollama's structured output: the answer is forced to match this JSON schema
            payload["format"] = schema

        try:
            async with httpx.AsyncClient(timeout=config.AI_TIMEOUT_SECONDS) as client:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if not response.is_success:
                        await response.aread()
                        logger.warning("Ollama chat request failed with status %s", response.status_code)
                        raise ProviderError("The answer model failed. Is Ollama running with the model installed?")

                    # Ollama streams one JSON object per line: {"message": {"content": "..."}, "done": false}
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        part = json.loads(line)
                        if part.get("error"):
                            logger.warning("Ollama chat error: %s", part["error"])
                            raise ProviderError("The answer model returned an error")
                        content = part.get("message", {}).get("content", "")
                        if content:
                            yield content
        except httpx.HTTPError as error:
            logger.warning("Ollama chat request failed: %s", error)
            raise ProviderError("The answer model failed. Is Ollama running with the model installed?") from error
        except json.JSONDecodeError as error:
            raise ProviderError("Ollama returned an invalid streaming response") from error


# ---------------------- OpenAI (optional) ------------------------------------
class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str = config.OPENAI_API_KEY,
        embedding_model: str = config.OPENAI_EMBEDDING_MODEL,
        chat_model: str = config.OPENAI_CHAT_MODEL,
        http_client: Any = None,
    ):
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is not set")
        # max_retries=0: a failed request is reported instead of being silently repeated (and paid for).
        # http_client lets tests replace the network with a fake transport
        self.client = AsyncOpenAI(
            api_key=api_key,
            timeout=config.AI_TIMEOUT_SECONDS,
            max_retries=0,
            http_client=http_client,
        )
        self.embedding_model = embedding_model
        self.chat_model = chat_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = await self.client.embeddings.create(
                model=self.embedding_model,
                input=texts,
                encoding_format="float",
            )
        except OpenAIError as error:
            # Only the error type is logged: OpenAI messages can contain part of the API key
            logger.warning("OpenAI embedding request failed: %s", type(error).__name__)
            raise ProviderError("The OpenAI embedding request failed") from error

        return validate_vectors([item.embedding for item in response.data], len(texts))

    async def chat(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        max_tokens: int = config.MAX_OUTPUT_TOKENS,
    ) -> AsyncIterator[str]:
        options: dict[str, Any] = {}
        if schema:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": schema, "strict": True},
            }

        try:
            stream = await self.client.chat.completions.create(
                model=model or self.chat_model,
                messages=messages,
                max_completion_tokens=max_tokens,
                stream=True,
                **options,
            )
            async for event in stream:
                if event.choices and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
        except OpenAIError as error:
            logger.warning("OpenAI chat request failed: %s", type(error).__name__)
            raise ProviderError("The OpenAI answer request failed") from error


# ---------------------- Picks the provider from the configuration ------------
def get_provider() -> AIProvider:
    if config.AI_PROVIDER == "ollama":
        return OllamaProvider()
    if config.AI_PROVIDER == "openai":
        return OpenAIProvider()
    raise ProviderError(f"Unknown AI_PROVIDER: {config.AI_PROVIDER}")
