"""Shared helpers for the CLI and service tests: a synthetic PDF shaped like tests/fakes.document and a
declaration. Every step (label, extract, review, deliver) is the real one; Docling is tests/fakes.fake_engine."""

import sys
import types

import pymupdf
import yaml
from fakes import H, W

import kb_intake
from kb_intake import jobs as jobs_mod
from kb_intake import package
from kb_intake.package import read_jsonl

DOC = "demo-ar-2025-en"
DECL = {"doc_id": DOC, "issuer_id": "demo", "doc_type": "annual_report", "period_end": "2025-12-31",
        "source_url": "https://example.com/demo.pdf", "completeness": {"full": True}, "lang": "en"}
BODY = ("The Group reported a net profit for the year and the board is pleased with the results of this year. "
        "It is the view of the board that the bank is in a strong position for the next year.")
TABLE_BOX = (72, 200, 400, 300)


def build_pdf(path, pages: int = 2):
    """Each page: a heading at y 67-84 and body text in 100-140; page 1 has a ruled 2 x 2 table in TABLE_BOX (the
    boxes of fakes.document)."""
    doc = pymupdf.open()
    for n in range(1, pages + 1):
        p = doc.new_page(width=W, height=H)
        p.insert_text((72, 80), f"Section {n}", fontsize=12, fontname="hebo")
        p.insert_textbox(pymupdf.Rect(72, 100, 500, 140), BODY, fontsize=9)
        if n == 1:
            x0, y0, x1, y1 = TABLE_BOX
            xs, ys = [x0, (x0 + x1) / 2, x1], [y0, (y0 + y1) / 2, y1]
            for r in range(2):
                for c in range(2):
                    p.draw_rect(pymupdf.Rect(xs[c], ys[r], xs[c + 1], ys[r + 1]), color=(0, 0, 0), width=0.8)
                    p.insert_text((xs[c] + 5, ys[r] + 30), f"cell {r}{c}", fontsize=10)
    doc.save(path)
    doc.close()
    return path


def decl_yaml(**over) -> str:
    return yaml.safe_dump({**DECL, **over}, sort_keys=False)


class ReviewError(ValueError):
    pass
