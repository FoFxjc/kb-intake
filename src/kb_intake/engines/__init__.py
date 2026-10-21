"""Engines by name. Adding an engine means adding a class here; there is no plugin mechanism."""

from kb_intake.engines.base import EngineInfo, LayoutEngine, OcrEngine, RawResult
from kb_intake.engines.docling import DoclingEngine

__all__ = ["DoclingEngine", "EngineInfo", "LayoutEngine", "OcrEngine", "RawResult"]
