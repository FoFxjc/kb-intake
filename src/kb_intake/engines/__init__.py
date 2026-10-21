"""Engines by name (intake-design §3.1). Adding an engine means adding a class here; there is no plugin mechanism."""

from kb_intake.engines.base import EngineInfo, LayoutEngine, LayoutResult, OcrEngine, RawResult
from kb_intake.engines.docling import DoclingEngine

LAYOUT = {"docling": DoclingEngine}

__all__ = ["LAYOUT", "DoclingEngine", "EngineInfo", "LayoutEngine", "LayoutResult", "OcrEngine", "RawResult"]
