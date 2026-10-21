"""The two engine interfaces (intake-design §3.1). Plain Protocols, chosen by name in the configuration
(`ENGINES` in kb_intake.engines); no entry points, no plugin registry.

A layout engine is used in two halves so that the pipeline, not the engine, owns caching and fault isolation:
- `convert(pdf)` runs the model. It is slow, may hang or crash, and runs in a child process with a hard timeout.
  Its output (`RawResult.data`, e.g. the lossless DoclingDocument) is what the cache stores and the package keeps.
- `interpret(data, doc_id)` maps that output to contract shapes (region types of layout-design §2, tables with cells).
  It is a pure function of `data`, so a cached result gives byte-identical packages.
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


@dataclass
class LayoutResult:
    """Contract-shaped, before post-processing. Regions and tables carry `engine_ref` (the engine's own id of the
    item) so that post-processing can link tables, regions and gaps."""
    regions: list[dict]
    tables: list[dict]


class LayoutEngine(Protocol):
    name: str

    def info(self) -> EngineInfo: ...

    def convert(self, pdf: Path) -> RawResult: ...

    def interpret(self, data: bytes, doc_id: str) -> LayoutResult: ...


class OcrEngine(Protocol):
    """Flow B (scanned pages, step F). Only the interface exists so far."""
    name: str

    def info(self) -> EngineInfo: ...

    def recognize(self, image: bytes, page_no: int, dpi: int, lang: str | None) -> list[list]:
        """Words of one page image: [x0, y0, x1, y1, text, "ocr", confidence] in PDF points, TOPLEFT."""
        ...
