import os
import tempfile


_whisper_model = None
_VOICE_LANGUAGES = {"en-IN": "en", "hi-IN": "hi", "kn-IN": "kn"}
_VOICE_AUDIO_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/x-m4a": ".m4a",
}


def transcribe_local_audio(content: bytes, language: str, content_type: str) -> str:
    global _whisper_model
    if language not in _VOICE_LANGUAGES:
        raise ValueError("Choose English (India), Hindi, or Kannada for voice input.")
    if not content or len(content) > 16 * 1024 * 1024:
        raise ValueError("Voice recordings must be between 1 byte and 16 MB.")
    media_type = content_type.split(";", 1)[0].strip().lower()
    suffix = _VOICE_AUDIO_TYPES.get(media_type)
    if suffix is None:
        raise ValueError("Unsupported recording format. Use WebM, Ogg, WAV, MP4, or MP3 audio.")
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise RuntimeError(
            "Local Whisper is not installed. Install it with "
            "`python -m pip install -r requirements-voice.txt` and restart the app."
        ) from error

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as audio_file:
            audio_file.write(content)
            temporary_path = audio_file.name
        if _whisper_model is None:
            model_name = os.environ.get("WHISPER_MODEL", "tiny")
            _whisper_model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = _whisper_model.transcribe(
            temporary_path,
            language=_VOICE_LANGUAGES[language],
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
