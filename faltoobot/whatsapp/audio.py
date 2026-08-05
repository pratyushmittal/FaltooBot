import io
from typing import Any

from openai import AsyncOpenAI

DEFAULT_AUDIO_MAX_SECONDS = 420
AUDIO_MODEL = "gpt-4o-transcribe"
MIME_SUFFIXES = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp3": ".mp3",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
}


class AudioError(RuntimeError):
    pass


def get_audio(event: Any) -> Any | None:
    message = getattr(event, "Message", None)
    if message is None or not hasattr(message, "HasField"):
        return None
    return message.audioMessage if message.HasField("audioMessage") else None


def format_voice_note_transcript(transcript: str) -> str:
    return (
        "The user sent a voice note. "
        "The following text is a transcription of that voice note:\n\n"
        f"{transcript}"
    )


async def transcribe_audio(
    openai_client: Any,
    audio_bytes: bytes,
    *,
    mimetype: str,
    prompt: str,
    model: str = AUDIO_MODEL,
) -> str:
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = f"voice-note{MIME_SUFFIXES.get(mimetype.lower(), '.ogg')}"
    response = await openai_client.audio.transcriptions.create(
        file=audio_file,
        model=model,
        prompt=prompt,
        response_format="text",
    )
    return response if isinstance(response, str) else str(getattr(response, "text", ""))


async def audio_prompt(  # noqa: PLR0913
    client: Any,
    event: Any,
    *,
    openai_api_key: str,
    transcription_prompt: str,
    model: str = AUDIO_MODEL,
    max_seconds: int = DEFAULT_AUDIO_MAX_SECONDS,
) -> str:
    message = get_audio(event)
    if message is None:
        raise AudioError("No audio found in this message.")
    if int(getattr(message, "seconds", 0) or 0) > max_seconds:
        raise AudioError(
            f"Voice note is too long. Keep it under {max_seconds} seconds."
        )
    if not openai_api_key.strip():
        # comment: voice transcription uses the public API key, not Codex OAuth.
        raise AudioError("OpenAI API key is required to transcribe voice notes.")

    blob = await client.download_any(event.Message)
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise AudioError("I couldn't download that voice note.")

    openai_client = AsyncOpenAI(api_key=openai_api_key)
    try:
        transcript = (
            await transcribe_audio(
                openai_client,
                bytes(blob),
                mimetype=str(getattr(message, "mimetype", "") or "audio/ogg"),
                prompt=transcription_prompt,
                model=model,
            )
        ).strip()
        if not transcript:
            raise AudioError("I couldn't transcribe that voice note.")
        return format_voice_note_transcript(transcript)
    finally:
        await openai_client.close()


TTS_MODEL = "gpt-4o-mini-tts"
TTS_VOICE = "coral"
TTS_MAX_CHARS = 4096
TTS_INSTRUCTIONS = (
    "Speak warmly, naturally, and briefly for a young child. "
    "Pronounce simple Hindi and Hinglish clearly without exaggerating an accent."
)


async def synthesize_speech(
    text: str,
    *,
    openai_api_key: str,
    model: str = TTS_MODEL,
    voice: str = TTS_VOICE,
) -> bytes:
    """Generate WhatsApp-compatible Opus speech with the public OpenAI API."""
    cleaned = text.strip()
    if not openai_api_key.strip():
        raise AudioError("OpenAI API key is required for voice replies.")
    if not cleaned or len(cleaned) > TTS_MAX_CHARS:
        raise AudioError("Voice reply text is empty or too long.")

    openai_client = AsyncOpenAI(api_key=openai_api_key)
    try:
        response = await openai_client.audio.speech.create(
            model=model,
            voice=voice,
            input=cleaned,
            instructions=TTS_INSTRUCTIONS,
            response_format="opus",
        )
        audio_bytes = await response.aread()
        if not audio_bytes:
            raise AudioError("OpenAI returned an empty voice reply.")
        return audio_bytes
    finally:
        await openai_client.close()
