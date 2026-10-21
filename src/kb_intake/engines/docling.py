"""The Docling layout engine (intake-design §9): Docling 2.140.0 standard PDF pipeline, OCR off, TableFormer, with the
pinned weights from the local models directory.

- `convert` runs in a child process (kb_intake.runner): Hugging Face is put offline before Docling is imported, and
  the weights are only read from `models_dir`.
- Failed pages: Docling 2.140 puts every failed or skipped page back into `document.pages` (to keep page numbering),
  so they cannot be read from there. They are the expected pages that are not in `ConversionResult.pages` (only
  pages that completed stay there), plus the pages named by the errors.
- `interpret` maps Docling labels to the region types of layout-design §2; Docling's own label names do not reach
  the contract. Coordinates become TOPLEFT.
"""

import json
import os
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from kb_intake import deps, models
from kb_intake.config import DoclingParams
from kb_intake.engines.base import EngineInfo, LayoutResult, RawResult

# Docling labels -> layout-design §2 region types
REGION_TYPE = {
    "title": "title", "section_header": "heading", "text": "text", "paragraph": "text", "reference": "text",
    "list_item": "list", "caption": "caption", "footnote": "footnote", "page_header": "page_header",
    "page_footer": "page_footer", "formula": "formula", "code": "text", "document_index": "toc", "table": "table",
    "picture": "picture", "chart": "chart",
}


def offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"         # never download at run time (§9.3.2)
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


@dataclass(frozen=True)
class DoclingEngine:
    models_dir: Path
    params: DoclingParams
    name: str = "docling"

    def info(self) -> EngineInfo:
        problems: list[str] = []
        try:
            models.verify(self.models_dir)
        except models.ModelsError as e:
            problems.append(str(e))
        problems += deps.problems()
        return EngineInfo(name=self.name, version=version("docling"), models=tuple(models.pins()),
                          params=self.params.engine_params(), ready=not problems, problems=tuple(problems))

    def convert(self, pdf: Path) -> RawResult:
        offline()
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        p = self.params
        opts = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
            table_structure_options=TableStructureOptions(
                mode=TableFormerMode.ACCURATE if p.table_mode == "accurate" else TableFormerMode.FAST,
                do_cell_matching=True),
            accelerator_options=AcceleratorOptions(device=AcceleratorDevice(p.device), num_threads=p.num_threads),
            artifacts_path=str(self.models_dir),
            document_timeout=p.document_timeout,
        )
        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        res = conv.convert(str(pdf), raises_on_error=False)
        data = json.dumps(res.document.export_to_dict(), ensure_ascii=False).encode()
        conf = {}
        for page_no, c in (res.confidence.pages or {}).items():
            d = c.model_dump()
            score = d.get("layout_score")
            conf[str(page_no)] = {"grade": str(d.get("mean_grade")).split(".")[-1].lower(),
                                  "layout_score": None if score != score else score}       # NaN -> None
        return RawResult(status=str(res.status).split(".")[-1].lower(), data=data,
                         failed_pages=failed_pages(res), errors=[_error(e) for e in res.errors],
                         page_confidence=conf)

    def interpret(self, data: bytes, doc_id: str) -> LayoutResult:
        doc = json.loads(data)
        return LayoutResult(regions=_regions(doc), tables=_tables(doc))


def failed_pages(res) -> list[int]:
    """Pages of a ConversionResult that Docling did not complete (see the module docstring)."""
    expected = set(range(1, res.input.page_count + 1))
    done = {p.page_no for p in res.pages}
    named = {e.page_no for e in res.errors if getattr(e, "page_no", None)}
    return sorted((expected - done) | (named & expected))


def _error(e) -> str:
    msg = getattr(e, "error_message", None) or str(e)
    page = getattr(e, "page_no", None)
    return (f"page {page}: " if page else "") + msg[:300]


# ---------- DoclingDocument -> contract shapes ----------

def topleft(bbox: dict, page_h: float) -> list[float]:
    if bbox.get("coord_origin") == "BOTTOMLEFT":
        return [bbox["l"], page_h - bbox["t"], bbox["r"], page_h - bbox["b"]]
    return [bbox["l"], bbox["t"], bbox["r"], bbox["b"]]


def walk(doc: dict):
    """Items in reading order: body first, then furniture; groups (lists, etc.) are entered."""
    index = {}
    for kind in ("texts", "tables", "pictures", "groups", "key_value_items", "form_items"):
        for item in doc.get(kind) or []:
            index[item["self_ref"]] = item

    def visit(node):
        for ref in node.get("children") or []:
            item = index.get(ref["$ref"])
            if item is None:
                continue
            if item["self_ref"].startswith("#/groups/"):
                yield from visit(item)
            else:
                yield item
                yield from visit(item)
    yield from visit(doc["body"])
    yield from visit(doc.get("furniture") or {})


def _heights(doc: dict) -> dict[int, float]:
    return {int(k): v["size"]["height"] for k, v in doc["pages"].items()}


def _regions(doc: dict) -> list[dict]:
    """Regions in reading order with page size ratio (for the picture/diagram rule in post-processing)."""
    heights = _heights(doc)
    out = []
    for item in walk(doc):
        if not item.get("prov"):
            continue
        prov = item["prov"][0]
        page_no = prov["page_no"]
        size = doc["pages"][str(page_no)]["size"]
        out.append({"page_no": page_no, "bbox": topleft(prov["bbox"], heights[page_no]),
                    "type": REGION_TYPE.get(item.get("label", "unknown"), "unknown"),
                    "engine_ref": item["self_ref"], "content_layer": item.get("content_layer", "body"),
                    "page_area": size["width"] * size["height"]})
    return out


def _tables(doc: dict) -> list[dict]:
    """Tables in reading order. Cells keep spans and Docling's cell box (`engine_bbox`); text is as Docling read it.
    Post-processing cleans the text, re-reads headers from the text layer and sets the contract's cell bbox."""
    heights = _heights(doc)
    tables = []
    for item in walk(doc):
        if item.get("label") not in ("table", "document_index") or not item.get("prov") or "data" not in item:
            continue
        prov = item["prov"][0]
        page_no, data = prov["page_no"], item["data"]
        cells, seen = [], set()
        for c in data["table_cells"]:
            key = (c["start_row_offset_idx"], c["start_col_offset_idx"])
            if key in seen:
                continue
            seen.add(key)
            cells.append({"r": key[0], "c": key[1], "rs": max(1, c.get("row_span") or 1),
                          "cs": max(1, c.get("col_span") or 1), "text": c.get("text"),
                          "engine_bbox": topleft(c["bbox"], heights[page_no]) if c.get("bbox") else None,
                          "is_header": bool(c.get("column_header")), "page_no": page_no})
        tables.append({"engine_ref": item["self_ref"], "pages": [page_no],
                       "bboxes": [topleft(prov["bbox"], heights[page_no])],
                       "n_rows": data["num_rows"], "n_cols": data["num_cols"], "cells": cells})
    return tables
