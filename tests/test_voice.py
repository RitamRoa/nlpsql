import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

import core.voice as voice


def test_local_transcription_uses_selected_language_and_cleans_up_audio(monkeypatch):
    observed = {}

    class Model:
        def transcribe(self, path, *, language, vad_filter):
            with open(path, "rb") as audio_file:
                content = audio_file.read()
            observed.update(
                path=path,
                content=content,
                language=language,
                vad_filter=vad_filter,
            )
            return iter([SimpleNamespace(text="  नमस्ते "), SimpleNamespace(text="दुनिया")]), None

    fake_whisper = ModuleType("faster_whisper")
    fake_whisper.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_whisper)
    monkeypatch.setattr(voice, "_whisper_model", Model())

    assert voice.transcribe_local_audio(b"sample audio", "hi-IN", "audio/webm;codecs=opus") == "नमस्ते दुनिया"
    assert observed["content"] == b"sample audio"
    assert observed["language"] == "hi"
    assert observed["vad_filter"] is True
    assert not os.path.exists(observed["path"])


@pytest.mark.parametrize(
    ("content", "language", "content_type", "message"),
    [
        (b"audio", "fr-FR", "audio/webm", "Choose English"),
        (b"", "en-IN", "audio/webm", "between 1 byte"),
        (b"audio", "en-IN", "application/octet-stream", "Unsupported recording format"),
    ],
)
def test_local_transcription_rejects_invalid_input(content, language, content_type, message):
    with pytest.raises(ValueError, match=message):
        voice.transcribe_local_audio(content, language, content_type)


def test_local_transcription_reports_missing_optional_dependency(monkeypatch):
    monkeypatch.setattr(voice, "_whisper_model", None)
    monkeypatch.setitem(sys.modules, "faster_whisper", None)

    with pytest.raises(RuntimeError, match="requirements-voice.txt"):
        voice.transcribe_local_audio(b"audio", "en-IN", "audio/wav")
