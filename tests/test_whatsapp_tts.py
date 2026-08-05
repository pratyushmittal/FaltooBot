from types import SimpleNamespace

import pytest

from faltoobot.whatsapp import audio


@pytest.mark.anyio
async def test_synthesize_speech_uses_openai_opus_tts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    closed = False

    class Response:
        async def aread(self) -> bytes:
            return b"opus voice note"

    class Speech:
        async def create(self, **kwargs: object) -> Response:
            calls.append(kwargs)
            return Response()

    class Client:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "env-key"
            self.audio = SimpleNamespace(speech=Speech())

        async def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(audio, "AsyncOpenAI", Client)

    result = await audio.synthesize_speech(
        "Chalo ek pyari si Hindi kahani sunte hain!",
        openai_api_key="env-key",
    )

    assert result == b"opus voice note"
    assert calls == [
        {
            "model": "gpt-4o-mini-tts",
            "voice": "coral",
            "input": "Chalo ek pyari si Hindi kahani sunte hain!",
            "instructions": audio.TTS_INSTRUCTIONS,
            "response_format": "opus",
        }
    ]
    assert closed is True


@pytest.mark.anyio
async def test_synthesize_speech_requires_public_api_key() -> None:
    with pytest.raises(audio.AudioError, match="API key"):
        await audio.synthesize_speech("Hello", openai_api_key="")
