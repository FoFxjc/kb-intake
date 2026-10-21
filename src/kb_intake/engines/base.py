"""The two engine interfaces (intake-design-v2 §2). Plain Protocols; no entry points, no plugin registry.

A layout engine is used in two halves so that the caller, not the engine, owns caching and fault isolation:
- `convert(pdf)` runs the model. It is slow, may hang or crash, and runs in a child process with a hard timeout
  (kb_intake.runner). Its output (`RawResult.data`, the lossless DoclingDocument, plus `clusters`) is what the cache
  stores and the package keeps under raw/.
- `label_items` / `extract_tables` (kb_intake.engines.docling) turn that output into plain dicts. They are pure
  functions of the stored output, so a cached result gives byte-identical packages.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class EngineInfo:
    name: str
    version: str
    models: tuple[dict, ...]                # repo_id, revision, role
    params: dict[str, Any]                  # everything that shapes the output (part of the cache key)
    ready: bool
    problems: tuple[str, ...] = ()          # why not ready: weights missing or not matching, versions off, ...


@dataclass
class RawResult:
    status: str                             # success | partial_success | failure
    data: bytes | None                      # the engine's own lossless output
    failed_pages: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    page_confidence: dict[str, Any] = field(default_factory=dict)
    # layout clusters that made it into the document, per page: [x0, y0, x1, y1, label, confidence], TOPLEFT points.
    # DoclingDocument items carry no confidence; it is joined back by page and box (engines.docling.label_items).
    clusters: dict[str, list] = field(default_factory=dict)


class LayoutEngine(Protocol):
    name: str
    stage: str                              # "label" or "extract"

    def info(self) -> EngineInfo: ...

    def convert(self, pdf: Path) -> RawResult: ...


class OcrEngine(Protocol):
    """Scanned pages and regions (the `ocr` handler, intake-design-v2 §8). Only the interface exists so far."""
    name: str

    def info(self) -> EngineInfo: ...

    def recognize(self, image: bytes, page_no: int, dpi: int, lang: str | None) -> list[list]:
        """Words of one page image: [x0, y0, x1, y1, text, "ocr", confidence] in PDF points, TOPLEFT."""
        ...
