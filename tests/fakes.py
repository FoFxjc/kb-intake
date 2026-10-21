"""A fake layout engine for the pipeline tests: Docling's interpret() on a hand-made DoclingDocument, with a convert()
that can succeed, fail pages, hang, crash or raise. It runs in the pipeline's child process like the real engine, so
the module must be importable there (tests/ is on sys.path, which a spawned child inherits)."""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from kb_intake.engines.base import EngineInfo, RawResult
from kb_intake.engines.docling import DoclingEngine

H = 842.0


def _box(x0, y0, x1, y1):
    return {"l": x0, "t": H - y0, "r": x1, "b": H - y1, "coord_origin": "BOTTOMLEFT"}


def document(pages: int, table_page: int, cells: list[list[str]], xs: list[float], ys: list[float],
             skip_pages=()) -> dict:
    """A DoclingDocument with one text item per page and one table (first row = column headers)."""
    texts, children = [], []
    for p in range(1, pages + 1):
        if p in skip_pages:
            continue
        ref = f"#/texts/{len(texts)}"
        texts.append({"self_ref": ref, "label": "text", "content_layer": "body", "text": f"Annual report page {p}",
                      "prov": [{"page_no": p, "bbox": _box(72, 68, 300, 84)}], "children": []})
        children.append({"$ref": ref})
    table_cells = []
    for r, row in enumerate(cells):
        for c, text in enumerate(row):
            table_cells.append({"start_row_offset_idx": r, "end_row_offset_idx": r + 1, "start_col_offset_idx": c,
                                "end_col_offset_idx": c + 1, "row_span": 1, "col_span": 1, "text": text,
                                "column_header": r == 0, "bbox": _box(xs[c] + 2, ys[r] + 2, xs[c + 1] - 2, ys[r + 1] - 2)})
    tables = []
    if table_page not in skip_pages:
        tables.append({"self_ref": "#/tables/0", "label": "table", "content_layer": "body", "children": [],
                       "prov": [{"page_no": table_page, "bbox": _box(xs[0], ys[0], xs[-1], ys[-1])}],
                       "data": {"table_cells": table_cells, "num_rows": len(cells), "num_cols": len(cells[0])}})
        children.append({"$ref": "#/tables/0"})
    return {"body": {"children": children}, "furniture": {"children": []}, "texts": texts, "tables": tables,
            "pictures": [], "groups": [],
            "pages": {str(p): {"size": {"width": 595.0, "height": H}, "page_no": p} for p in range(1, pages + 1)}}


@dataclass(frozen=True)
class FakeEngine(DoclingEngine):
    mode: str = "ok"                      # ok | failed_page | hang | crash | raise | not_ready
    doc: str = ""                         # json of document()
    calls: str = ""                       # a file that gets one line per convert() call
    name: str = "docling"

    def info(self) -> EngineInfo:
        ready = self.mode != "not_ready"
        return EngineInfo(name=self.name, version="fake-1", models=({"repo_id": "fake/layout", "revision": "0" * 40,
                                                                     "role": "layout"},),
                          params={"mode": self.mode}, ready=ready, problems=() if ready else ("weights missing",))

    def convert(self, pdf: Path) -> RawResult:
        if self.calls:
            with open(self.calls, "a") as f:
                f.write(self.mode + "\n")
        if self.mode == "hang":
            time.sleep(600)
        if self.mode == "crash":
            os._exit(3)
        if self.mode == "raise":
            raise RuntimeError("model blew up")
        doc = json.loads(self.doc)
        if self.mode == "failed_page":
            return RawResult("partial_success", json.dumps(doc).encode(), failed_pages=[2],
                             errors=["page 2: Page failed to process."])
        return RawResult("success", json.dumps(doc).encode(), page_confidence={"1": {"grade": "excellent"}})
