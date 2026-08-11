"""On-device offline speech — ASR + TTS + VAD, fully offline (no Internet).

Backend finalized: **sherpa-onnx** (Apache-2.0) — the unified offline engine (STT/TTS/VAD/
diarization via ONNX, cross-platform: Android/iOS/Pi/RISC-V/x86), bundling **Whisper** (MIT)
for ASR and **Kokoro-82M** (Apache-2.0) for TTS. **Governance:** avoid Moonshine's non-English
models (non-commercial license) — Whisper+Kokoro keep it commercial-clean. `OfflineSpeech` wraps
an injectable `SpeechBackend`, audits every call, and is offline by construction (a local backend,
never the network). Pure/deterministic here; loading the ONNX models is the thin adapter.
"""

from __future__ import annotations

import asyncio
import io
import os
import wave
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


def wav_to_samples(audio: bytes) -> tuple[int, Any]:
    """Decode WAV bytes -> (sample_rate, float32 mono samples in [-1, 1])."""
    import numpy as np

    with wave.open(io.BytesIO(audio), "rb") as w:
        sr, width, nch = w.getframerate(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if nch > 1:
        data = data.reshape(-1, nch).mean(axis=1)
    return sr, data


def _samples_to_wav(samples: Any, sample_rate: int) -> bytes:
    """Encode float samples -> 16-bit mono WAV bytes."""
    import numpy as np

    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


@dataclass
class SpeechResult:
    ok: bool
    output: Any = None  # text (ASR) | audio bytes (TTS) | bool (VAD)
    error: str | None = None


@runtime_checkable
class SpeechBackend(Protocol):
    async def transcribe(self, audio: bytes, lang: str) -> str: ...
    async def synthesize(self, text: str, voice: str) -> bytes: ...
    async def detect_voice(self, audio: bytes) -> bool: ...


@dataclass
class OfflineSpeech:
    backend: SpeechBackend
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def transcribe(self, audio: bytes, *, lang: str = "en") -> SpeechResult:
        if not audio:
            return SpeechResult(False, error="empty audio")
        self._audit({"op": "transcribe", "lang": lang, "bytes": len(audio)})
        return SpeechResult(True, await self.backend.transcribe(audio, lang))

    async def synthesize(self, text: str, *, voice: str = "kokoro") -> SpeechResult:
        if not text.strip():
            return SpeechResult(False, error="empty text")
        self._audit({"op": "synthesize", "voice": voice, "chars": len(text)})
        return SpeechResult(True, await self.backend.synthesize(text, voice))

    async def detect_voice(self, audio: bytes) -> SpeechResult:
        self._audit({"op": "vad", "bytes": len(audio)})
        return SpeechResult(True, await self.backend.detect_voice(audio))


class SherpaBackend:
    """Adapter over sherpa-onnx (Apache-2.0). The recognizer / tts / vad are injected (or fakes
    in tests); `load()` lazy-imports the optional `sherpa_onnx` so importing this never requires
    it. Live wiring loads Whisper (ASR) + Kokoro-82M (TTS) + Silero VAD ONNX models."""

    def __init__(self, *, recognizer: Any = None, tts: Any = None, vad: Any = None) -> None:
        self._recognizer, self._tts, self._vad = recognizer, tts, vad

    @property
    def recognizer(self) -> Any:
        """Public accessor for the underlying sherpa-onnx recognizer (same-package consumers
        that need direct stream-level access, e.g. video_tools.py's audio-track transcription)."""
        return self._recognizer

    @classmethod
    def load(cls, *, models_dir: str, num_threads: int = 2) -> SherpaBackend:  # pragma: no cover
        """Wire the live offline-speech backend: Whisper (ASR) + Kokoro-82M (TTS) + Silero (VAD)
        ONNX models under `models_dir` (whisper-tiny.en/ , kokoro-en-v0_19/ , silero_vad.onnx)."""
        try:
            import numpy as np
            import sherpa_onnx as _sherpa_onnx  # type: ignore[reportMissingTypeStubs]

            sherpa_onnx: Any = _sherpa_onnx
        except ImportError as exc:
            raise ImportError(
                "sherpa-onnx is not installed — `pip install sherpa-onnx` (Apache-2.0) + the "
                "Whisper/Kokoro ONNX weights to wire the live offline-speech backend"
            ) from exc

        whisper, kokoro = (
            os.path.join(models_dir, "whisper-tiny.en"),
            os.path.join(models_dir, "kokoro-en-v0_19"),
        )
        recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
            encoder=os.path.join(whisper, "tiny.en-encoder.onnx"),
            decoder=os.path.join(whisper, "tiny.en-decoder.onnx"),
            tokens=os.path.join(whisper, "tiny.en-tokens.txt"),
            num_threads=num_threads,
        )
        tts = sherpa_onnx.OfflineTts(
            sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                        model=os.path.join(kokoro, "model.onnx"),
                        voices=os.path.join(kokoro, "voices.bin"),
                        tokens=os.path.join(kokoro, "tokens.txt"),
                        data_dir=os.path.join(kokoro, "espeak-ng-data"),
                    ),
                    num_threads=num_threads,
                ),
            )
        )
        vad_config = sherpa_onnx.VadModelConfig()
        vad_config.silero_vad.model = os.path.join(models_dir, "silero_vad.onnx")
        vad_config.sample_rate = 16000

        def _decode(audio: bytes, lang: str) -> str:
            sr, samples = wav_to_samples(audio)
            stream = recognizer.create_stream()
            stream.accept_waveform(sr, samples)
            recognizer.decode_stream(stream)
            return stream.result.text.strip()

        def _synth(text: str, voice: str) -> bytes:
            generated = tts.generate(text, sid=0, speed=1.0)
            return _samples_to_wav(generated.samples, generated.sample_rate)

        def _detect(audio: bytes) -> bool:
            sr, samples = wav_to_samples(audio)
            if sr != 16000:  # the VAD needs 16 kHz
                n = int(len(samples) * 16000 / sr)
                idx = np.clip((np.arange(n) * sr / 16000).astype(np.int64), 0, len(samples) - 1)
                samples = samples[idx]
            vad = sherpa_onnx.VoiceActivityDetector(vad_config, buffer_size_in_seconds=30)
            window = vad_config.silero_vad.window_size
            for i in range(0, len(samples) - window, window):
                vad.accept_waveform(samples[i : i + window].astype(np.float32))
            vad.flush()
            return not vad.empty()

        async def _atranscribe(audio: bytes, lang: str) -> str:
            return await asyncio.to_thread(_decode, audio, lang)

        async def _asynth(text: str, voice: str) -> bytes:
            return await asyncio.to_thread(_synth, text, voice)

        async def _avad(audio: bytes) -> bool:
            return await asyncio.to_thread(_detect, audio)

        return cls(recognizer=_atranscribe, tts=_asynth, vad=_avad)

    async def transcribe(self, audio: bytes, lang: str) -> str:
        return await self._recognizer(audio, lang)

    async def synthesize(self, text: str, voice: str) -> bytes:
        return await self._tts(text, voice)

    async def detect_voice(self, audio: bytes) -> bool:
        return await self._vad(audio)
