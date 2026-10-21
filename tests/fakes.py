"""A fake Docling engine for the tests. It returns a hand-made DoclingDocument (and the clusters Docling would have
assembled) and can also fail pages, hang, crash or raise. It runs in the runner's child process like the real
engine, so the module must be importable there (tests/ is on sys.path, which a spawned child inherits).

`document()` builds a small document shaped like Docling 2.140.0 output (see kb_intake.engines.docling):
item boxes BOTTOMLEFT, cell boxes TOPLEFT, and, in the label stage, the table's PDF cells as extra text items
that match no cluster.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from kb_intake.engines.base import EngineInfo, RawResult
from kb_intake.engines.docling import DoclingEngine

W, H = 595.0, 842.0


def bl(x0, y0, x1, y1):
    """A TOPLEFT box as Docling writes item boxes (BOTTOMLEFT)."""
    return {"l": x0, "t": H - y0, "r": x1, "b": H - y1, "coord_origin": "BOTTOMLEFT"}


def tl(x0, y0, x1, y1):
    return {"l": x0, "t": y0, "r": x1, "b": y1, "coord_origin": "TOPLEFT"}


def document(pages: int = 2, stage: str = "label", table_page: int = 1, table_box=(72, 200, 400, 300),
             grid=(2, 2), pictures=(), skip_pages=()) -> tuple[dict, dict]:
    """(DoclingDocument dict, clusters). Each page has a heading at y 68-84 and a text at y 100-140; `table_page`
    has a table in `table_box` with a rows x cols `grid`; `pictures` are (page, box, [[class, score], ...])."""
    texts, tables, pics, children, clusters = [], [], [], [], {}

    def add_text(label, page, box, cluster=True):
        ref = f"#/texts/{len(texts)}"
        texts.append({"self_ref": ref, "label": label, "content_layer": "body", "text": "docling text (ignored)",
                      "prov": [{"page_no": page, "bbox": bl(*box)}], "children": [],
                      "parent": {"$ref": "#/body"}})
        children.append({"$ref": ref})
        if cluster:
            clusters.setdefault(str(page), []).append([*box, label, 0.9])

    for p in range(1, pages + 1):
        if p in skip_pages:
            continue
        add_text("section_header", p, (72, 68, 300, 84))
        add_text("text", p, (72, 100, 500, 140))
        if p == table_page:
            x0, y0, x1, y1 = table_box
            rows, cols = grid
            xs = [x0 + (x1 - x0) * i / cols for i in range(cols + 1)]
            ys = [y0 + (y1 - y0) * i / rows for i in range(rows + 1)]
            ref = f"#/tables/{len(tables)}"
            item = {"self_ref": ref, "label": "table", "content_layer": "body", "children": [],
                    "parent": {"$ref": "#/body"}, "prov": [{"page_no": p, "bbox": bl(*table_box)}]}
            if stage == "extract":
                item["data"] = {"num_rows": rows, "num_cols": cols, "table_cells": [
                    {"start_row_offset_idx": r, "end_row_offset_idx": r + 1, "start_col_offset_idx": c,
                     "end_col_offset_idx": c + 1, "row_span": 1, "col_span": 1, "text": "docling cell (ignored)",
                     "column_header": r == 0, "bbox": tl(xs[c] + 1, ys[r] + 1, xs[c + 1] - 1, ys[r + 1] - 1)}
                    for r in range(rows) for c in range(cols)]}
            tables.append(item)
            children.append({"$ref": ref})
            clusters.setdefault(str(p), []).append([*table_box, "table", 0.95])
            if stage == "label":            # Docling 2.140 emits the table's PDF cells as unclustered text items
                add_text("text", p, (xs[0] + 3, ys[0] + 3, xs[1] - 3, ys[1] - 3), cluster=False)
    for page, box, classes in pictures:
        ref = f"#/pictures/{len(pics)}"
        pics.append({"self_ref": ref, "label": "picture", "content_layer": "body", "children": [],
                     "parent": {"$ref": "#/body"}, "prov": [{"page_no": page, "bbox": bl(*box)}],
                     "meta": {"classification": {"predictions": [
                         {"class_name": c, "confidence": s} for c, s in classes]}}})
        children.append({"$ref": ref})
        clusters.setdefault(str(page), []).append([*box, "picture", 0.8])
    doc = {"body": {"children": children}, "furniture": {"children": []}, "texts": texts, "tables": tables,
           "pictures": pics, "groups": [],
           "pages": {str(p): {"size": {"width": W, "height": H}, "page_no": p} for p in range(1, pages + 1)}}
    return doc, clusters


@dataclass(frozen=True)
class FakeEngine(DoclingEngine):
    behaviour: str = "ok"     # ok | failed_page | hang | crash | silent_exit | raise | not_ready | unreadable
    doc: str = ""             # json of [document, clusters]
    calls: str = ""           # a file that gets one line per convert() call

    def info(self) -> EngineInfo:
        ready = self.behaviour != "not_ready"
        return EngineInfo(name=self.name, version="fake-1",
                          models=({"repo_id": "fake/layout", "revision": "0" * 40, "role": "layout"},),
                          params={"stage": self.stage, "behaviour": self.behaviour}, ready=ready,
                          problems=() if ready else ("weights missing",))

    def convert(self, pdf: Path) -> RawResult:
        if self.calls:
            with open(self.calls, "a") as f:
                f.write(self.behaviour + "\n")
        if self.behaviour == "hang":
            time.sleep(600)
        if self.behaviour == "crash":
            os._exit(3)
        if self.behaviour == "silent_exit":
            os._exit(0)
        if self.behaviour == "raise":
            raise RuntimeError("model blew up")
        if self.behaviour == "unreadable":
            return RawResult("success", b"{not json")
        doc, clusters = json.loads(self.doc)
        if self.behaviour == "failed_page":
            return RawResult("partial_success", json.dumps(doc).encode(), failed_pages=[2],
                             errors=["page 2: Page failed to process."], clusters=clusters)
        return RawResult("success", json.dumps(doc).encode(), clusters=clusters)


def fake_engine(tmp_path: Path, behaviour: str = "ok", stage: str = "label", **doc_kw) -> FakeEngine:
    from kb_intake.config import DoclingParams
    return FakeEngine(models_dir=tmp_path / "models", params=DoclingParams(), stage=stage, behaviour=behaviour,
                      doc=json.dumps(document(stage=stage, **doc_kw)), calls=str(tmp_path / "calls.txt"))
