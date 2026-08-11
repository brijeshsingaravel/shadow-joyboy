"""Indic multilingual stack — the India-edge language layer (AI4Bharat, IIT Madras).

Backend finalized (all permissive, all from AI4Bharat): **IndicTrans2** (MIT) for translation
across the 22 scheduled Indian languages, **IndicConformer** (MIT) for ASR, and **Indic
Parler-TTS** (Apache-2.0) for TTS. `IndicService` validates the language pair against the
scheduled set + English, routes to an injectable `IndicBackend`, and audits every call — so
the model calls are governed and unsupported languages fail fast. The backend is injectable →
pure/deterministic here; the heavy HF model loading is a thin adapter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# The 22 scheduled languages of India (ISO codes), per IndicTrans2 / IndicConformer.
SCHEDULED: dict[str, str] = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "ks": "Kashmiri",
    "kok": "Konkani",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}
SUPPORTED: frozenset[str] = frozenset(SCHEDULED) | {"en"}


@dataclass
class IndicResult:
    ok: bool
    output: Any = None  # translated text / transcript / audio bytes
    error: str | None = None


@runtime_checkable
class IndicBackend(Protocol):
    async def translate(self, text: str, src: str, tgt: str) -> str: ...
    async def transcribe(self, audio: bytes, lang: str) -> str: ...
    async def synthesize(self, text: str, lang: str) -> bytes: ...


@dataclass
class IndicService:
    backend: IndicBackend
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    @staticmethod
    def _unsupported(*langs: str) -> str | None:
        bad = [lang for lang in langs if lang not in SUPPORTED]
        return f"unsupported language(s): {bad}" if bad else None

    async def translate(self, text: str, *, src: str, tgt: str) -> IndicResult:
        err = self._unsupported(src, tgt)
        if err:
            return IndicResult(False, error=err)
        self._audit({"op": "translate", "src": src, "tgt": tgt})
        return IndicResult(True, await self.backend.translate(text, src, tgt))

    async def transcribe(self, audio: bytes, *, lang: str) -> IndicResult:
        err = self._unsupported(lang)
        if err:
            return IndicResult(False, error=err)
        self._audit({"op": "transcribe", "lang": lang})
        return IndicResult(True, await self.backend.transcribe(audio, lang))

    async def synthesize(self, text: str, *, lang: str) -> IndicResult:
        err = self._unsupported(lang)
        if err:
            return IndicResult(False, error=err)
        self._audit({"op": "synthesize", "lang": lang})
        return IndicResult(True, await self.backend.synthesize(text, lang))


# ISO 639 (the SUPPORTED codes) -> IndicTrans2 FLORES tag
_FLORES: dict[str, str] = {
    "en": "eng_Latn",
    "as": "asm_Beng",
    "bn": "ben_Beng",
    "brx": "brx_Deva",
    "doi": "doi_Deva",
    "gu": "guj_Gujr",
    "hi": "hin_Deva",
    "kn": "kan_Knda",
    "ks": "kas_Arab",
    "kok": "gom_Deva",
    "mai": "mai_Deva",
    "ml": "mal_Mlym",
    "mni": "mni_Mtei",
    "mr": "mar_Deva",
    "ne": "npi_Deva",
    "or": "ory_Orya",
    "pa": "pan_Guru",
    "sa": "san_Deva",
    "sat": "sat_Olck",
    "sd": "snd_Arab",
    "ta": "tam_Taml",
    "te": "tel_Telu",
    "ur": "urd_Arab",
}
_SPECIALS = frozenset({"<s>", "<pad>", "</s>", "<unk>"})


class _IndicTranslator:
    """Live IndicTrans2 over CTranslate2 (no transformers → no version conflict): SentencePiece
    tokenize → CT2 translate → indic-nlp transliterate to the native script. en↔indic direct;
    indic→indic pivots via English."""

    def __init__(
        self, en_indic_dir: str, indic_en_dir: str, sp_en: str, sp_indic: str, device: str
    ) -> None:
        import ctranslate2 as _ctranslate2  # type: ignore[reportMissingTypeStubs]
        import sentencepiece as _spm  # type: ignore[reportMissingTypeStubs]

        ctranslate2: Any = _ctranslate2
        spm: Any = _spm
        self._en_indic = ctranslate2.Translator(en_indic_dir, device=device)
        self._indic_en = ctranslate2.Translator(indic_en_dir, device=device)
        # IndicTrans2 has a SP model per side; roles swap by direction.
        self._sp_en = spm.SentencePieceProcessor(model_file=sp_en)  # English side (model.SRC)
        self._sp_indic = spm.SentencePieceProcessor(model_file=sp_indic)  # Indic side (model.TGT)

    @staticmethod
    def _translit(text: str, src_iso: str, tgt_iso: str) -> str:
        if src_iso == tgt_iso:
            return text
        try:
            from indicnlp.transliterate.unicode_transliterate import (  # type: ignore[reportMissingTypeStubs]
                UnicodeIndicTransliterator as _UnicodeIndicTransliterator,
            )

            transliterator: Any = _UnicodeIndicTransliterator
            return str(transliterator.transliterate(text, src_iso, tgt_iso))
        except Exception:
            return text

    def _run(
        self, translator: Any, text: str, src_flores: str, tgt_flores: str, enc: Any, dec: Any
    ) -> str:
        tokens = [src_flores, tgt_flores, *enc.encode(text, out_type=str), "</s>"]
        result = translator.translate_batch([tokens], beam_size=5, max_decoding_length=256)
        tags = set(_FLORES.values())
        out = [t for t in result[0].hypotheses[0] if t not in _SPECIALS and t not in tags]
        return dec.decode(out)

    def __call__(self, text: str, src: str, tgt: str) -> str:
        if src == "en" and tgt != "en":  # encode EN, decode Indic
            raw = self._run(
                self._en_indic, text, _FLORES["en"], _FLORES[tgt], self._sp_en, self._sp_indic
            )
            return self._translit(raw, "hi", tgt)  # CT2 emits Devanagari -> native script
        if tgt == "en" and src != "en":  # -> Devanagari, encode Indic, decode EN
            deva = self._translit(text, src, "hi")
            return self._run(
                self._indic_en, deva, _FLORES[src], _FLORES["en"], self._sp_indic, self._sp_en
            )
        if src != "en" and tgt != "en":  # indic -> indic: pivot via English
            return self.__call__(self.__call__(text, src, "en"), "en", tgt)
        return text  # en -> en


class AI4BharatBackend:
    """Adapter over AI4Bharat — IndicTrans2 (translate, **native driver wired s33**), IndicConformer
    (ASR) + Indic Parler-TTS (TTS) injectable. `load()` builds the live CTranslate2 translator from
    the en-indic + indic-en CT2 weights (MIT)."""

    def __init__(self, *, translator: Any = None, asr: Any = None, tts: Any = None) -> None:
        self._translator, self._asr, self._tts = translator, asr, tts

    @classmethod
    def load(
        cls, *, models_dir: str, device: str = "cuda", asr: Any = None, tts: Any = None
    ) -> AI4BharatBackend:  # pragma: no cover - needs the heavy deps
        import os

        try:
            import ctranslate2  # noqa: F401  # type: ignore[reportMissingTypeStubs]
            import sentencepiece  # noqa: F401  # type: ignore[reportMissingTypeStubs]
        except ImportError as exc:
            raise ImportError(
                "the Indic stack is not installed — install the `indic` extra (ctranslate2 + "
                "sentencepiece + indic-nlp-library) + the IndicTrans2 CT2 weights"
            ) from exc

        engine = _IndicTranslator(
            en_indic_dir=os.path.join(
                models_dir, "ct2-en-indic", "en-indic-200m-ct2", "ctranslate2_model"
            ),
            indic_en_dir=os.path.join(
                models_dir, "ct2-indic-en", "indic-en-200m-ct2", "ctranslate2_model"
            ),
            sp_en=os.path.join(models_dir, "indictrans2-en-indic", "model.SRC"),
            sp_indic=os.path.join(models_dir, "indictrans2-en-indic", "model.TGT"),
            device=device,
        )

        async def _translate(text: str, src: str, tgt: str) -> str:
            return await asyncio.to_thread(engine, text, src, tgt)

        return cls(translator=_translate, asr=asr, tts=tts)

    async def translate(self, text: str, src: str, tgt: str) -> str:
        return await self._translator(text, src, tgt)

    async def transcribe(self, audio: bytes, lang: str) -> str:
        if self._asr is None:
            raise RuntimeError("Indic ASR (IndicConformer) not wired — inject an asr runtime")
        return await self._asr(audio, lang)

    async def synthesize(self, text: str, lang: str) -> bytes:
        if self._tts is None:
            raise RuntimeError("Indic TTS (Parler) not wired — inject a tts runtime")
        return await self._tts(text, lang)
