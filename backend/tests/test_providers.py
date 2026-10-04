import json

import httpx
import httpx2
import pytest
import respx

from app import config
from app.ai import providers
from app.ai.providers import OllamaProvider, OpenAIProvider, ProviderError, get_provider, validate_vectors


OLLAMA = "http://ollama.test"
# additionalProperties: false is required by OpenAI when strict is true
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


async def collect(stream) -> str:
    return "".join([part async for part in stream])


def test_validate_vectors_rejects_bad_embeddings():
    assert validate_vectors([[0.1, 0.2]], 1) == [[0.1, 0.2]]

    for vectors, count in [([[0.1]], 2), ([[0.1, 0.2], [0.3]], 2), ([[]], 1), ([[float("nan")]], 1)]:
        with pytest.raises(ProviderError):
            validate_vectors(vectors, count)


@respx.mock
async def test_ollama_embed_sends_every_text_and_refuses_truncation():
    route = respx.post(f"{OLLAMA}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    )

    vectors = await OllamaProvider(base_url=OLLAMA, embedding_model="embed-model").embed(["a", "b"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert json.loads(route.calls.last.request.content) == {"model": "embed-model", "input": ["a", "b"], "truncate": False}


@respx.mock
async def test_ollama_embed_turns_http_errors_into_provider_errors():
    respx.post(f"{OLLAMA}/api/embed").mock(return_value=httpx.Response(500))

    with pytest.raises(ProviderError):
        await OllamaProvider(base_url=OLLAMA).embed(["a"])


@respx.mock
async def test_ollama_chat_streams_pieces_with_the_schema_and_fixed_settings():
    lines = [
        {"message": {"content": '{"answer": '}, "done": False},
        {"message": {"content": '"hi"}'}, "done": False},
        {"message": {"content": ""}, "done": True},
    ]
    route = respx.post(f"{OLLAMA}/api/chat").mock(
        return_value=httpx.Response(200, content="\n".join(json.dumps(line) for line in lines).encode())
    )

    provider = OllamaProvider(base_url=OLLAMA, chat_model="small-model")
    pieces = [piece async for piece in provider.chat([{"role": "user", "content": "q"}], schema=SCHEMA, model="other-model")]

    assert pieces == ['{"answer": ', '"hi"}']
    payload = json.loads(route.calls.last.request.content)
    assert payload["model"] == "other-model"  # the evaluation switches models per request
    assert payload["format"] == SCHEMA
    assert payload["think"] is False
    assert payload["options"] == {"num_ctx": config.CONTEXT_TOKENS, "num_predict": config.MAX_OUTPUT_TOKENS, "temperature": 0}


@respx.mock
async def test_ollama_chat_reports_errors_sent_inside_the_stream():
    respx.post(f"{OLLAMA}/api/chat").mock(return_value=httpx.Response(200, content=b'{"error": "model not found"}\n'))

    # The user sees a safe message; the details only go to the server log
    with pytest.raises(ProviderError, match="returned an error"):
        await collect(OllamaProvider(base_url=OLLAMA).chat([{"role": "user", "content": "q"}]))


async def test_the_network_is_blocked_when_a_mock_is_missing():
    # Without the http_client mock this would reach the real OpenAI API
    provider = OpenAIProvider(api_key="sk-test")

    with pytest.raises(Exception) as error:
        await provider.embed(["a"])

    # A real 401 from OpenAI would also raise, so the guard itself must be the
    # error or somewhere in its chain of causes
    causes = []
    current = error.value
    while current is not None:
        causes.append(str(current))
        current = current.__cause__ or current.__context__
    assert any("must not use the network" in cause for cause in causes)

    with pytest.raises(RuntimeError, match="must not use the network"):
        httpx.get("https://api.github.com")


def test_openai_needs_an_api_key():
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider(api_key="")


# The openai SDK uses httpx2, not httpx, so respx can't see its requests.
# A MockTransport is passed in instead, and it records every request it receives
async def test_openai_embed_and_chat_use_the_configured_models():
    requests = []
    events = [
        {"id": "1", "object": "chat.completion.chunk", "created": 0, "model": "chat-model",
         "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
        for piece in ['{"answer": "', 'hi"}']
    ]
    chat_body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/embeddings"):
            return httpx2.Response(200, json={
                "object": "list",
                "model": "embed-model",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.5, 0.5]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            })
        return httpx2.Response(200, content=chat_body.encode(), headers={"content-type": "text/event-stream"})

    provider = OpenAIProvider(
        api_key="sk-test",
        embedding_model="embed-model",
        chat_model="chat-model",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )

    assert await provider.embed(["a"]) == [[0.5, 0.5]]
    assert await collect(provider.chat([{"role": "user", "content": "q"}], schema=SCHEMA)) == '{"answer": "hi"}'
    payload = json.loads(requests[-1].content)
    assert payload["model"] == "chat-model"
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA
    assert all(request.url.host == "api.openai.com" for request in requests)


def test_get_provider_follows_the_configuration(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "ollama")
    assert isinstance(get_provider(), OllamaProvider)

    monkeypatch.setattr(config, "AI_PROVIDER", "something-else")
    with pytest.raises(ProviderError):
        providers.get_provider()


async def test_local_ollama_is_blocked_too_when_a_mock_is_missing():
    with pytest.raises(RuntimeError, match="must not use the network"):
        await OllamaProvider(base_url="http://localhost:11434").embed(["a"])
