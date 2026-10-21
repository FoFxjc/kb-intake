"""The Docling engine (intake-design-v2 §2): Docling 2.140.0 standard PDF pipeline, OCR off, pinned weights read from
the local models directory. One engine instance per stage:

- label:   layout + picture classifier (DocumentFigureClassifier-v2.5, pinned revision), no table structure.
- extract: layout + TableFormer (accurate, cell matching), no classifier.

Facts this module relies on (checked on Docling 2.140.0, OCBC AR 2025 en):
- Failed pages: Docling puts every failed or skipped page back into `document.pages`, so they cannot be read from
  there. They are the expected pages that are not in `ConversionResult.pages`, plus the pages named by the errors.
- DoclingDocument items carry no layout confidence. `convert` keeps the box, label and confidence of every cluster
  that was assembled into the document; `label_items` joins them back by page and box (exact to 0.01 pt).
- With table structure off, Docling also emits each PDF cell inside a table cluster as a separate top-level text item
  that matches no cluster. Those are not regions: `label_items` drops and counts them.
- Item boxes are BOTTOMLEFT, cluster and table-cell boxes TOPLEFT; everything here is returned TOPLEFT.
"""

import json
import os
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from kb_intake import deps, models
from kb_intake.config import DoclingParams
from kb_intake.engines.base import EngineInfo, RawResult

MATCH_TOL = 0.01


def offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"         # never download at run time (v1 §9.3.2)
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def classifier_pin() -> dict:
    return next(m for m in models.pins() if m["role"] == "picture_classifier")


@dataclass(frozen=True)
class DoclingEngine:
    models_dir: Path
    params: DoclingParams
    stage: str = "label"
    name: str = "docling"

    def info(self) -> EngineInfo:
        problems: list[str] = []
        try:
            models.verify(self.models_dir)
        except models.ModelsError as e:
            problems.append(str(e))
        problems += deps.problems()
        return EngineInfo(name=self.name, version=version("docling"), models=tuple(models.pins()),
                          params=self.params.engine_params(self.stage), ready=not problems,
                          problems=tuple(problems))

    def options(self):
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.picture_classification_options import DocumentPictureClassifierOptions
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TableStructureOptions

        p = self.params
        common = dict(do_ocr=False, artifacts_path=str(self.models_dir), document_timeout=p.document_timeout,
                      accelerator_options=AcceleratorOptions(device=AcceleratorDevice(p.device),
                                                             num_threads=p.num_threads))
        if self.stage == "label":
            pc = DocumentPictureClassifierOptions.from_preset("document_figure_classifier_v2")
            pin = classifier_pin()
            if pc.repo_id != pin["repo_id"]:
                raise RuntimeError(f"Docling's picture classifier is {pc.repo_id}, the lock pins {pin['repo_id']}")
            pc.model_spec.revision = pin["revision"]          # the preset says "main"
            return PdfPipelineOptions(**common, do_table_structure=False, do_picture_classification=True,
                                      picture_classification_options=pc)
        return PdfPipelineOptions(
            **common, do_table_structure=True, do_picture_classification=False,
            table_structure_options=TableStructureOptions(
                mode=TableFormerMode.ACCURATE if p.table_mode == "accurate" else TableFormerMode.FAST,
                do_cell_matching=True))

    def convert(self, pdf: Path) -> RawResult:
        offline()
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter, PdfFormatOption

        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=self.options())})
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
                         page_confidence=conf, clusters=clusters_of(res))


def failed_pages(res) -> list[int]:
    """Pages of a ConversionResult that Docling did not complete (see the module docstring)."""
    expected = set(range(1, res.input.page_count + 1))
    done = {p.page_no for p in res.pages}
    named = {e.page_no for e in res.errors if getattr(e, "page_no", None)}
    return sorted((expected - done) | (named & expected))


def clusters_of(res) -> dict[str, list]:
    out = {}
    for p in res.pages:
        if p.assembled is None:
            continue
        out[str(p.page_no)] = [[e.cluster.bbox.l, e.cluster.bbox.t, e.cluster.bbox.r, e.cluster.bbox.b,
                                e.cluster.label.value, e.cluster.confidence] for e in p.assembled.elements]
    return out


def _error(e) -> str:
    msg = getattr(e, "error_message", None) or str(e)
    page = getattr(e, "page_no", None)
    return (f"page {page}: " if page else "") + msg[:300]


# ---------- DoclingDocument -> plain dicts ----------

def topleft(bbox: dict, page_h: float) -> list[float]:
    if bbox.get("coord_origin") == "BOTTOMLEFT":
        return [bbox["l"], page_h - bbox["t"], bbox["r"], page_h - bbox["b"]]
    return [bbox["l"], bbox["t"], bbox["r"], bbox["b"]]


def walk(doc: dict):
    """Items in reading order: body first, then furniture. Groups (lists, etc.) are entered; the children of
    pictures and tables (text inside them) are not separate items."""
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
                continue
            yield item
            if not item["self_ref"].startswith(("#/pictures/", "#/tables/")):
                yield from visit(item)
    yield from visit(doc["body"])
    yield from visit(doc.get("furniture") or {})


def _heights(doc: dict) -> dict[int, float]:
    return {int(k): v["size"]["height"] for k, v in doc["pages"].items()}


def _same(a, b) -> bool:
    return all(abs(x - y) <= MATCH_TOL for x, y in zip(a, b))


def _centre_in(box, c) -> bool:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return c[0] - 1 <= cx <= c[2] + 1 and c[1] - 1 <= cy <= c[3] + 1


def classes_of(item: dict) -> list[list]:
    """[[class_name, score], ...] of a picture, best first (empty when the classifier did not run on it)."""
    meta = (item.get("meta") or {}).get("classification") or {}
    preds = meta.get("predictions") or []
    return [[p.get("class_name"), p.get("confidence")] for p in preds]


def label_items(data: bytes, clusters: dict[str, list]) -> dict:
    """The label-stage items of a DoclingDocument, in reading order:

        {"items": [{"engine_ref", "page_no", "bbox", "label", "content_layer", "score", "classes"}],
         "dropped_table_cells": int, "unscored": int, "page_sizes": {page_no: [width, height]}}

    `score` is the confidence of the cluster with the same page and box (None when none matches); `classes` is the
    classifier's ranking for pictures, else None. Text items that match no cluster and lie in a table cluster are
    the table's PDF cells: dropped and counted."""
    doc = json.loads(data)
    heights = _heights(doc)
    items, dropped, unscored = [], 0, 0
    for item in walk(doc):
        if not item.get("prov"):
            continue
        prov = item["prov"][0]
        page_no = prov["page_no"]
        box = topleft(prov["bbox"], heights[page_no])
        page_clusters = clusters.get(str(page_no)) or []
        match = next((c for c in page_clusters if _same(c[:4], box)), None)
        if match is None and item["self_ref"].startswith("#/texts/") and any(
                c[4] == "table" and _centre_in(box, c[:4]) for c in page_clusters):
            dropped += 1
            continue
        unscored += match is None
        is_picture = item["self_ref"].startswith("#/pictures/")
        items.append({"engine_ref": item["self_ref"], "page_no": page_no, "bbox": box,
                      "label": item.get("label", "unknown"), "content_layer": item.get("content_layer", "body"),
                      "score": match[5] if match else None, "classes": classes_of(item) if is_picture else None})
    sizes = {int(k): [v["size"]["width"], v["size"]["height"]] for k, v in doc["pages"].items()}
    return {"items": items, "dropped_table_cells": dropped, "unscored": unscored, "page_sizes": sizes}


def extract_tables(data: bytes) -> list[dict]:
    """The extract-stage tables in reading order: {"engine_ref", "page_no", "bbox", "n_rows", "n_cols", "cells"},
    cells {"r", "c", "rs", "cs", "bbox", "is_header"} with Docling's cell box (TOPLEFT). Docling's cell text is not
    kept: cell text is read from the text layer (kb_intake.textlayer, in kb_intake.extract)."""
    doc = json.loads(data)
    heights = _heights(doc)
    tables = []
    for item in walk(doc):
        if item.get("label") not in ("table", "document_index") or not item.get("prov") or "data" not in item:
            continue
        prov = item["prov"][0]
        page_no, d = prov["page_no"], item["data"]
        cells, seen = [], set()
        for c in d["table_cells"]:
            key = (c["start_row_offset_idx"], c["start_col_offset_idx"])
            if key in seen:
                continue
            seen.add(key)
            cells.append({"r": key[0], "c": key[1], "rs": max(1, c.get("row_span") or 1),
                          "cs": max(1, c.get("col_span") or 1),
                          "bbox": topleft(c["bbox"], heights[page_no]) if c.get("bbox") else None,
                          "is_header": bool(c.get("column_header"))})
        tables.append({"engine_ref": item["self_ref"], "page_no": page_no,
                       "bbox": topleft(prov["bbox"], heights[page_no]),
                       "n_rows": d["num_rows"], "n_cols": d["num_cols"], "cells": cells})
    return tables
