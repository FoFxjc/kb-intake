"""Workflows (intake-design-v2 §3): the template, parsing and validation, stable dumping, suggestions from stats."""

import pytest

from kb_intake import vocab, workflow
from kb_intake.workflow import WorkflowError


def test_default_template_covers_every_region_type_with_the_design_handlers():
    wf = workflow.load_template()
    assert wf["name"] == "default" and wf["version"] == 1 and wf["exclude_pages"] == []
    assert list(wf["handlers"]) == list(vocab.REGION_TYPES)
    assert wf["handlers"]["table"] == "table_structure" and wf["handlers"]["chart"] == "text"
    assert wf["handlers"]["picture"] == "discard" and wf["handlers"]["signature"] == "record"
    assert wf["handlers"]["unknown"] == "unhandled"
    assert workflow.handler_for(wf, "heading") == "text"


def test_unknown_template_is_refused():
    with pytest.raises(WorkflowError):
        workflow.load_template("../config")
    with pytest.raises(WorkflowError):
        workflow.load_template("nope")


@pytest.mark.parametrize("bad", [
    "just a string",
    "name: x\nversion: 1\nhandlers: {}\nexclude_pages: []\n",                       # every type missing
    "- 1\n- 2\n",                                                                    # not a mapping
    "name: [unclosed\n",                                                             # not YAML
])
def test_parse_refuses_malformed_text(bad):
    with pytest.raises(WorkflowError):
        workflow.parse(bad)


def test_workflow_parse_refuses_bad_input():
    base = workflow.load_template()

    def variant(**changes):
        wf = {**base, "handlers": dict(base["handlers"]), "exclude_pages": list(base["exclude_pages"])}
        for k, v in changes.items():
            if k.startswith("h_"):
                if v is None:
                    del wf["handlers"][k[2:]]
                else:
                    wf["handlers"][k[2:]] = v
            else:
                wf[k] = v
        return workflow.dump(wf)

    unknown_type = variant(h_paragraph="text")
    missing_type = variant(h_caption=None)
    unknown_handler = variant(h_text="summarise")
    structure_on_text = variant(h_text="table_structure")
    for bad in (unknown_type, missing_type, unknown_handler, structure_on_text):
        with pytest.raises(WorkflowError):
            workflow.parse(bad)
    for pages in (["2"], [1.5], [0], [True], [3, 3], "1, 2"):
        with pytest.raises(WorkflowError):
            workflow.parse(variant(exclude_pages=pages))
    with pytest.raises(WorkflowError):
        workflow.parse(variant(version="one"))
    with pytest.raises(WorkflowError):
        workflow.parse(variant(name=""))
    with pytest.raises(WorkflowError):
        workflow.parse(workflow.dump(base) + "extra_key: 1\n")
    with pytest.raises(WorkflowError):
        workflow.handler_for(base, "paragraph")


def test_parse_accepts_changed_handlers_and_sorts_exclude_pages():
    base = workflow.load_template()
    wf = {**base, "handlers": {**base["handlers"], "picture": "record", "unknown": "ocr"}, "exclude_pages": [5, 1]}
    out = workflow.parse(workflow.dump(wf))
    assert out["handlers"]["picture"] == "record" and out["handlers"]["unknown"] == "ocr"
    assert out["exclude_pages"] == [1, 5]


def test_handlers_may_be_given_in_any_order():
    base = workflow.load_template()
    lines = ["name: mine", "version: 2", "handlers:"]
    lines += [f"  {t}: {base['handlers'][t]}" for t in reversed(vocab.REGION_TYPES)]
    lines.append("exclude_pages: [3]")
    wf = workflow.parse("\n".join(lines) + "\n")
    assert list(wf["handlers"]) == list(vocab.REGION_TYPES) and wf["name"] == "mine" and wf["version"] == 2


def test_dump_parse_round_trip_is_stable():
    base = workflow.load_template()
    wf = {**base, "exclude_pages": [9, 2], "handlers": {**base["handlers"], "chart": "record"}}
    once = workflow.dump(workflow.parse(workflow.dump(wf)))
    twice = workflow.dump(workflow.parse(once))
    assert once == twice and workflow.parse(once) == workflow.parse(twice)
    assert once.endswith("\n") and "exclude_pages:" in once


def _stats(**kw) -> dict:
    s = {"pages": 10, "pages_by_kind": {"native": 10, "scanned": 0, "mixed": 0, "blank": 0}, "regions": 50,
         "regions_by_type": {"text": 50}, "low_confidence": 0, "table_suspected": 0, "estimated_extract_seconds": 11}
    s.update(kw)
    return s


def test_suggest_returns_the_default_without_notes_for_a_clean_document():
    wf, notes = workflow.suggest(_stats())
    assert wf == workflow.load_template() and notes == []


def test_suggest_notes_scanned_pages_suspected_tables_and_low_confidence():
    wf, notes = workflow.suggest(_stats(pages_by_kind={"native": 7, "scanned": 2, "mixed": 1, "blank": 0},
                                        table_suspected=3, low_confidence=4, regions_by_type={"unknown": 2}))
    joined = "\n".join(notes)
    assert "OCR is not implemented" in joined and "3 suspected" in joined and "4 low-confidence" in joined
    assert "unknown" in joined
    workflow.parse(workflow.dump(wf))
    assert notes == workflow.suggest(_stats(pages_by_kind={"native": 7, "scanned": 2, "mixed": 1, "blank": 0},
                                            table_suspected=3, low_confidence=4,
                                            regions_by_type={"unknown": 2}))[1]


# ---------- describe and the llm switches (intake-ui-llm-design-v1 §4) ----------

def _with(**handlers) -> dict:
    wf = workflow.load_template()
    return {**wf, "handlers": {**wf["handlers"], **handlers}}


def test_describe_applies_to_charts_diagrams_and_pictures_only():
    wf = _with(chart="describe", diagram="describe", picture="describe")
    assert workflow.parse(workflow.dump(wf)) == wf
    for t in ("table", "text", "logo"):
        with pytest.raises(workflow.WorkflowError, match="describe"):
            workflow.validate(_with(**{t: "describe"}))


def test_llm_switches_round_trip_and_are_optional():
    wf = {**workflow.load_template(), "llm": {"describe": False, "chart_table": True}}
    text = workflow.dump(wf)
    assert text.endswith("llm: {describe: false, chart_table: true}\n")
    assert workflow.parse(text) == wf
    assert "llm" not in workflow.parse(workflow.dump(workflow.load_template()))
    assert workflow.llm_switch(wf, "describe", True) is False
    assert workflow.llm_switch(workflow.load_template(), "describe", True) is True
    for bad in ({"ocr": True}, {"describe": "yes"}, []):
        with pytest.raises(workflow.WorkflowError):
            workflow.validate({**workflow.load_template(), "llm": bad})
