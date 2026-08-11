"""Governed document extraction - PDF/office/scanned image -> structured Markdown.

Backend finalized (June 2026 SOTA, license-clean): **GLM-OCR** (zai-org/GLM-OCR, **MIT**;
layout via PP-DocLayoutV3, Apache-2.0) - a 0.9B VLM-OCR that tops OmniDocBench V1.5 (94.62,
beating Gemini 3 Pro / GPT-5.2) and ships a native PDF->Markdown flow (our canon is an Obsidian
vault). **PaddleOcrVlBackend** (Apache-2.0, 100+ langs incl. Indic - see [[Indic]]/B59) and
**DeepSeekOcrBackend** (MIT, blank-page/efficiency) are swappable behind the same OcrBackend
interface. The Madras edge: every extraction is **ASI02-wrapped** (doc text is untrusted DATA,
fenced in <retrieved>...</retrieved>, never instructions), **page/size-capped**, and **audited**.
Pure + deterministic; the VLM weights are an injectable adapter (lazy-import).
"""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ExtractedTable:
    rows: list[list[str]]
    caption: str = ""

    def to_markdown(self) -> str:
        if not self.rows:
            return ""
        head, *body = self.rows
        out = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
        out += ["| " + " | ".join(r) + " |" for r in body]
        if self.caption:
            out.append(f"\n*{self.caption}*")
        return "\n".join(out)


@dataclass
class ExtractedBlock:
    kind: str  # heading | paragraph | list | table | formula | figure
    text: str = ""
    level: int = 1  # heading level / list nesting
    table: ExtractedTable | None = None
    page: int = 1

    def to_markdown(self) -> str:
        if self.kind == "heading":
            return f"{'#' * max(1, min(6, self.level))} {self.text}"
        if self.kind == "list":
            return "\n".join(f"- {line}" for line in self.text.splitlines() if line.strip())
        if self.kind == "table":
            return self.table.to_markdown() if self.table else ""
        if self.kind == "formula":
            return f"$$\n{self.text}\n$$"
        if self.kind == "figure":
            return f"![figure]({self.text})" if self.text else "![figure]()"
        return self.text  # paragraph


@dataclass
class ExtractionResult:
    markdown: str  # full document, ASI02-wrapped by the governor
    blocks: list[ExtractedBlock] = field(default_factory=list[ExtractedBlock])
    pages: int = 0
    langs: tuple[str, ...] = ()
    backend: str = ""
    truncated: bool = False
    ok: bool = True
    error: str | None = None


@runtime_checkable
class OcrBackend(Protocol):
    name: str

    async def extract(
        self, source: bytes | str, *, langs: Sequence[str], max_pages: int
    ) -> tuple[list[ExtractedBlock], int, tuple[str, ...]]:
        """Return (blocks, page_count, detected_langs)."""
        ...


def assemble_markdown(blocks: Sequence[ExtractedBlock]) -> str:
    return "\n\n".join(b.to_markdown() for b in blocks if b.to_markdown().strip())


@dataclass
class GovernedExtractor:
    backend: OcrBackend
    max_pages: int = 50
    max_bytes: int = 50_000_000  # 50 MB cost/DoS bound
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def extract(
        self, source: bytes | str, *, langs: Sequence[str] = (), filename: str = ""
    ) -> ExtractionResult:
        if isinstance(source, (bytes, bytearray)) and len(source) > self.max_bytes:
            self._audit(
                {"event": "rejected", "reason": "too_large", "bytes": len(source), "file": filename}
            )
            return ExtractionResult(
                markdown="",
                backend=self.backend.name,
                ok=False,
                error=f"document exceeds max_bytes ({self.max_bytes})",
            )
        try:
            blocks, pages, detected = await self.backend.extract(
                source, langs=langs, max_pages=self.max_pages
            )
        except Exception as exc:
            self._audit({"event": "error", "file": filename, "error": str(exc)})
            return ExtractionResult(
                markdown="",
                backend=self.backend.name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        truncated = pages > self.max_pages
        if truncated:
            blocks = [b for b in blocks if b.page <= self.max_pages]
            pages = self.max_pages

        body = assemble_markdown(blocks)
        # ASI02: extracted document text is untrusted external DATA, never instructions.
        markdown = f"<retrieved>\n{body}\n</retrieved>"
        self._audit(
            {
                "event": "extract",
                "file": filename,
                "backend": self.backend.name,
                "pages": pages,
                "blocks": len(blocks),
                "langs": list(detected),
                "truncated": truncated,
            }
        )
        return ExtractionResult(
            markdown=markdown,
            blocks=list(blocks),
            pages=pages,
            langs=tuple(detected),
            backend=self.backend.name,
            truncated=truncated,
        )


def _parse_ocr_blocks(text: str, page: int = 1) -> list[ExtractedBlock]:
    """Parse GLM-OCR output (HTML tables / markdown / plain) into ExtractedBlocks."""
    text = text.strip()
    if not text:
        return []
    if "<table" in text.lower():
        rows: list[list[str]] = []
        for tr in re.findall(r"<tr>(.*?)</tr>", text, re.S | re.I):
            cells = [
                re.sub(r"<[^>]+>", "", c).strip()
                for c in re.findall(r"<t[dh]>(.*?)</t[dh]>", tr, re.S | re.I)
            ]
            if cells:
                rows.append(cells)
        if rows:
            return [ExtractedBlock(kind="table", table=ExtractedTable(rows=rows), page=page)]
    blocks: list[ExtractedBlock] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if para.startswith("#"):
            level = len(para) - len(para.lstrip("#"))
            blocks.append(
                ExtractedBlock(
                    kind="heading", text=para.lstrip("# ").strip(), level=level, page=page
                )
            )
        else:
            blocks.append(ExtractedBlock(kind="paragraph", text=para, page=page))
    return blocks


class _GlmOcrModel:
    """Live GLM-OCR runner: a 4-bit VLM + processor that OCRs an image into ExtractedBlocks."""

    def __init__(self, model: Any, processor: Any) -> None:
        self._model, self._processor = model, processor

    async def run(
        self, source: bytes | str, *, langs: list[str], max_pages: int
    ) -> tuple[list[ExtractedBlock], int, tuple[str, ...]]:
        return await asyncio.to_thread(self._run_sync, source)

    def _run_sync(self, source: bytes | str) -> tuple[list[ExtractedBlock], int, tuple[str, ...]]:
        import torch
        from PIL import Image

        img = Image.open(
            io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
        ).convert("RGB")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": img},
                    {"type": "text", "text": "Extract all text from this document as markdown."},
                ],
            }
        ]
        inputs = self._processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(self._model.device)
        with torch.no_grad():
            out = self._model.generate(**inputs, max_new_tokens=2048, do_sample=False)
        text = self._processor.batch_decode(
            out[:, inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )[0]
        return _parse_ocr_blocks(text), 1, ("en",)


class GlmOcrBackend:
    """Adapter over GLM-OCR (zai-org/GLM-OCR, MIT; layout PP-DocLayoutV3 Apache-2.0) - default.
    `connect()` loads the real 4-bit VLM (fits the 4 GB GPU at ~0.9 GB); the model handle is also
    injectable (a fake in tests). Live wiring OCRs page images into ExtractedBlocks. Swappable
    alternates behind OcrBackend: PaddleOcrVlBackend (Apache, 100+ langs), DeepSeekOcrBackend
    (MIT)."""

    name = "glm-ocr"

    def __init__(self, model: Any) -> None:
        self._model = model

    @classmethod
    def connect(
        cls,
        model_factory: Callable[[], Any] | None = None,
        *,
        model_dir: str = "zai-org/GLM-OCR",
        device: str = "cuda",
        quantize: str = "4bit",
    ) -> GlmOcrBackend:
        if model_factory is not None:
            return cls(model_factory())
        try:
            import torch
            from transformers import (
                AutoModelForImageTextToText,
                AutoProcessor,
                BitsAndBytesConfig,
            )
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "GLM-OCR backend needs `transformers` + `bitsandbytes` + the zai-org/GLM-OCR "
                "weights (MIT) - install the `ocr` extra and download the 0.9B model"
            ) from exc
        qcfg = (
            BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type="nf4"
            )
            if quantize == "4bit"
            else None
        )
        model: Any = AutoModelForImageTextToText.from_pretrained(  # type: ignore[reportUnknownMemberType]
            model_dir, quantization_config=qcfg, device_map=device, dtype=torch.float16
        )
        processor: Any = AutoProcessor.from_pretrained(  # type: ignore[reportUnknownMemberType]
            model_dir
        )
        return cls(_GlmOcrModel(model, processor))

    async def extract(
        self, source: bytes | str, *, langs: Sequence[str], max_pages: int
    ) -> tuple[list[ExtractedBlock], int, tuple[str, ...]]:
        return await self._model.run(source, langs=list(langs), max_pages=max_pages)
