"""Contract v2 additions of stage L (intake-ui-llm-design-v1 §4.4): llm.jsonl rows, llm_failed gaps, manifest.llm."""

import pytest

from kb_intake import contract

SHA = "0" * 64
ROW = {"kind": "describe", "region_id": "d:p0001:r001", "provider": "openai_compatible", "model": "MiniMax-M3",
       "prompt_version": "describe-1", "input_sha256": SHA, "output": {"text": "A bar chart."}, "weight": "low"}
TABLE = {"title": None, "columns": ["Year", "Value"], "rows": [["2024", 1.5], ["2025", None]], "units": "SGD m",
         "notes": "estimated from the bars"}


@pytest.mark.parametrize("row", [
    ROW,
    {**ROW, "kind": "summary", "region_id": None, "prompt_version": "summary-1"},
    {**ROW, "kind": "chart_table", "output": {"table": TABLE}},
])
def test_llm_rows_validate(row):
    assert contract.schema_errors("llm", row, "row") == []


@pytest.mark.parametrize("row", [
    {**ROW, "at": "2026-10-21T00:00:00Z"},                 # no time in the file
    {k: v for k, v in ROW.items() if k != "weight"},
    {**ROW, "weight": "high"},
    {**ROW, "input_sha256": "abc"},
    {**ROW, "output": {"text": "x", "table": TABLE}},
    {**ROW, "kind": "chart_table", "output": {"table": {**TABLE, "rows": [[{"x": 1}]]}}},
    {**ROW, "kind": "summary"},                             # a summary has no region
    {**ROW, "region_id": None},                             # a description has one
])
def test_bad_llm_rows_are_refused(row):
    assert contract.schema_errors("llm", row, "row") != []


def test_llm_failed_gap_and_manifest_llm():
    gap = {"code": "llm_failed", "page_no": 3, "region_id": "d:p0003:r002", "kind": "describe", "reason": "timeout",
           "detail": "describe failed: timeout"}
    assert contract.schema_errors("gap", gap, "gap") == []
    m = contract.schema_errors("manifest", {"llm": {"used": True, "provider": "openai_compatible", "model": "M",
                                                    "remote": True, "counts": {"summary": 1, "describe": 2,
                                                                               "chart_table": 0, "failed": 1}}}, "m")
    assert not [e for e in m if "llm" in e]
    assert [e for e in contract.schema_errors("manifest", {"llm": {"used": "yes"}}, "m") if "llm" in e]


def test_llm_failed_needs_kind_and_reason_and_used_manifest_needs_its_fields():
    assert contract.schema_errors("gap", {"code": "llm_failed", "page_no": None, "detail": "x"}, "g") != []
    assert contract.schema_errors("gap", {"code": "low_confidence", "page_no": 1, "detail": "x", "kind": "describe"},
                                  "g") != []
    assert [e for e in contract.schema_errors("manifest", {"llm": {"used": True}}, "m") if "llm" in e]
    assert not [e for e in contract.schema_errors("manifest", {"llm": {"used": False}}, "m") if "llm" in e]
