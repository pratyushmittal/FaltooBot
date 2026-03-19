import io
from typing import Any

DEFAULT_AUDIO_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_AUDIO_MAX_SECONDS = 420
NON_LATIN_SCRIPTS = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0x0900, 0x097F),
)
SCRIPT_NORMALIZATION_PROMPT = (
    "Convert this transcript to English letters only. Do not translate the meaning. "
    "For Hindi, Urdu, and Hinglish, produce phonetic transliteration in Roman script. "
    "Never output Urdu or Devanagari script. Return only the converted text."
)


class AudioError(RuntimeError):
    pass


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


def audio_message(event: Any) -> Any | None:
    message = getattr(event, "Message", None)
    if message is None or not hasattr(message, "HasField"):
        return None
    return message.audioMessage if message.HasField("audioMessage") else None


def audio_suffix(mimetype: str) -> str:
    return MIME_SUFFIXES.get(mimetype.lower(), ".ogg")


def contains_non_latin_script(text: str) -> bool:
    for char in text:
        codepoint = ord(char)
        if any(start <= codepoint <= end for start, end in NON_LATIN_SCRIPTS):
            return True
    return False


async def normalize_transcript_script(
    openai_client: Any,
    transcript: str,
    *,
    model: str,
) -> str:
    response = await openai_client.responses.create(
        model=model,
        input=transcript,
        instructions=SCRIPT_NORMALIZATION_PROMPT,
        store=False,
    )
    text = getattr(response, "output_text", "")
    return text.strip() if isinstance(text, str) else transcript


async def transcribe_audio(
    openai_client: Any,
    audio_bytes: bytes,
    *,
    mimetype: str,
    prompt: str,
    model: str = DEFAULT_AUDIO_TRANSCRIPTION_MODEL,
) -> str:
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = f"voice-note{audio_suffix(mimetype)}"  # type: ignore[attr-defined]
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
    openai_client: Any,
    *,
    transcription_prompt: str,
    model: str = DEFAULT_AUDIO_TRANSCRIPTION_MODEL,
    normalization_model: str,
    max_seconds: int = DEFAULT_AUDIO_MAX_SECONDS,
) -> str:
    message = audio_message(event)
    if message is None:
        raise AudioError("No audio found in this message.")
    seconds = int(getattr(message, "seconds", 0) or 0)
    if seconds > max_seconds:
        raise AudioError(f"Voice note is too long. Keep it under {max_seconds} seconds.")
    blob = await client.download_any(event.Message)
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        raise AudioError("I couldn't download that voice note.")
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
    if contains_non_latin_script(transcript):
        normalized = (
            await normalize_transcript_script(
                openai_client,
                transcript,
                model=normalization_model,
            )
        ).strip()
        if normalized:
            return normalized
    return transcript
