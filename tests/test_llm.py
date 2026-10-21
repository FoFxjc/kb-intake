"""LLM uses (kb_intake.llm, intake-ui-llm-design-v1 §4): inputs, hashing, the chart table, caching and failures.
The HTTP path is tested in test_llm_client.py; here `chat` is a recording stand-in."""

import dataclasses

import pymupdf
import pytest

from kb_intake import config, contract, llm
from kb_intake.llm_client import Completion, LlmError

KEY = "sk-SENTINEL-77"
ENV = {"INTAKE_LLM_API_KEY": KEY}


def enabled_cfg(tmp_path, **features) -> config.Config:
    cfg = config.Config(models_dir=tmp_path / "m", cache_dir=tmp_path / "c", work_dir=tmp_path / "w",
                        delivery_dir=tmp_path / "d")
    llm_cfg = dataclasses.replace(cfg.llm, enabled=True, features={**cfg.llm.features, **features})
    return dataclasses.replace(cfg, llm=llm_cfg)


class Chat:
    """Records calls; answers from `answers` (a list, one per call; an exception instance is raised)."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, base_url, model, key, content, max_tokens=2048, timeout=120.0):
        self.calls.append({"base_url": base_url, "model": model, "key": key, "content": content})
        a = self.answers.pop(0) if self.answers else "A bar chart of revenue by year."
        if isinstance(a, Exception):
            raise a
        return Completion(text=a, usage={"total_tokens": 10}, model=model)


class DictCache:
    def __init__(self):
        self.d = {}

    def get(self, key):
        return self.d.get(key)

    def put(self, key, value):
        self.d[key] = value


@pytest.fixture
def page():
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((80, 120), "Revenue 2025", fontsize=12)
    yield p
    doc.close()


REGION = {"region_id": "d:p0001:r003", "page_no": 1, "bbox": [60, 100, 300, 260], "type": "chart",
          "text": "Revenue 2025"}


def test_crop_limits(page):
    full = pymupdf.Pixmap(llm.crop_png(page, [0, 0, 595, 842]))
    assert max(full.width, full.height) <= llm.CROP_MAX_SIDE
    tiny = pymupdf.Pixmap(llm.crop_png(page, [10, 10, 10.5, 10.2]))
    assert tiny.width >= 1 and tiny.height >= 1
    assert len(llm.crop_png(page, [0, 0, 595, 842])) < 10 * 1024 * 1024


def test_input_hash_changes_with_text_or_image():
    a = llm.input_sha256("describe", "t", b"png")
    assert a == llm.input_sha256("describe", "t", b"png")
    assert len({a, llm.input_sha256("describe", "u", b"png"), llm.input_sha256("describe", "t", b"gif"),
                llm.input_sha256("chart_table", "t", b"png")}) == 4


def test_chart_table_json_in_fence():
    answer = 'Here:\n```json\n{"title": "Revenue", "columns": ["Year", "SGD m"], "rows": [["2024", 1.5], ' \
             '["2025", null]], "units": "SGD m", "notes": "read from the bars"}\n```'
    t = llm.parse_table(answer)
    assert t["rows"] == [["2024", 1.5], ["2025", None]] and t["units"] == "SGD m"


@pytest.mark.parametrize("answer", ["no json here", '{"columns": ["a"]}', '{"title": null, "columns": [1], '
                                    '"rows": [], "units": null, "notes": null}',
                                    '{"title": null, "columns": ["a"], "rows": [[true]], "units": null, '
                                    '"notes": null}'])
def test_chart_table_rejects_bad_shape(answer):
    with pytest.raises(llm.TableError):
        llm.parse_table(answer)


def test_describe_and_chart_table_rows(tmp_path, page):
    chat = Chat("A bar chart of revenue.", '{"title": null, "columns": ["Year", "Value"], "rows": [["2025", 3]], '
                                           '"units": null, "notes": "estimated"}')
    r = llm.Runner(enabled_cfg(tmp_path, chart_table=True), chat=chat, cache=DictCache(), env=ENV)
    rows = r.run_many(r.describe_inputs(REGION, page, wf=None))
    assert [x["kind"] for x in rows] == ["describe", "chart_table"]
    for x in rows:
        assert contract.schema_errors("llm", x, "row") == []
    assert rows[0]["output"] == {"text": "A bar chart of revenue."} and rows[1]["output"]["table"]["rows"] == [["2025", 3]]
    first = chat.calls[0]
    assert first["key"] == KEY and first["model"] == "MiniMax-M3"
    assert first["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Revenue 2025" in first["content"][1]["text"]


def test_chart_table_only_for_charts_and_when_switched_on(tmp_path, page):
    r = llm.Runner(enabled_cfg(tmp_path), chat=Chat(), cache=DictCache(), env=ENV)
    assert [c[0] for c in r.describe_inputs(REGION, page, wf=None)] == ["describe"]              # off by default
    r = llm.Runner(enabled_cfg(tmp_path, chart_table=True), chat=Chat(), cache=DictCache(), env=ENV)
    assert [c[0] for c in r.describe_inputs({**REGION, "type": "picture"}, page, None)] == ["describe"]
    wf_off = {"llm": {"chart_table": False}}
    assert [c[0] for c in r.describe_inputs(REGION, page, wf_off)] == ["describe"]


def test_enabled_needs_config_key_feature_and_workflow(tmp_path):
    cfg = enabled_cfg(tmp_path)
    assert llm.Runner(cfg, chat=Chat(), cache=DictCache(), env={}).enabled("describe") is False       # no key
    r = llm.Runner(cfg, chat=Chat(), cache=DictCache(), env=ENV)
    assert r.enabled("describe") and r.enabled("summary") and not r.enabled("chart_table")
    assert not r.enabled("describe", {"llm": {"describe": False}})
    off = dataclasses.replace(cfg, llm=dataclasses.replace(cfg.llm, enabled=False))
    assert llm.Runner(off, chat=Chat(), cache=DictCache(), env=ENV).enabled("summary") is False


def test_cache_hit_makes_no_call(tmp_path, page):
    cache, chat = DictCache(), Chat()
    r = llm.Runner(enabled_cfg(tmp_path), chat=chat, cache=cache, env=ENV)
    first = r.run_many(r.describe_inputs(REGION, page, None))
    again = llm.Runner(enabled_cfg(tmp_path), chat=Chat(RuntimeError("must not be called")), cache=cache, env=ENV)
    second = again.run_many(again.describe_inputs(REGION, page, None))
    assert second == first and again.stats["calls"] == 0 and again.stats["cached"] == 1 and len(chat.calls) == 1


def test_failures_become_gaps_and_are_not_cached(tmp_path, page):
    cache = DictCache()
    r = llm.Runner(enabled_cfg(tmp_path, chart_table=True), chat=Chat(LlmError("timeout", "the call timed out"),
                                                                       "no table here"), cache=cache, env=ENV)
    gaps = r.run_many(r.describe_inputs(REGION, page, None))
    assert [(g["code"], g["kind"], g["reason"]) for g in gaps] == \
        [("llm_failed", "describe", "timeout"), ("llm_failed", "chart_table", "invalid_table")]
    for g in gaps:
        assert contract.schema_errors("gap", g, "gap") == []
    assert r.stats["failed"] == 2 and len(cache.d) == 1               # the unparseable answer is cached, the error not


def test_empty_answer_fails(tmp_path, page):
    r = llm.Runner(enabled_cfg(tmp_path), chat=Chat("   "), cache=DictCache(), env=ENV)
    [gap] = r.run_many(r.describe_inputs(REGION, page, None))
    assert gap["code"] == "llm_failed" and gap["reason"] == "empty"


def test_failure_gap_has_no_key(tmp_path, page):
    r = llm.Runner(enabled_cfg(tmp_path), chat=Chat(LlmError("auth", "authentication failed")), cache=DictCache(),
                   env=ENV)
    [gap] = r.run_many(r.describe_inputs(REGION, page, None))
    assert KEY not in repr(gap)


def test_summary_input_and_row(tmp_path):
    job = tmp_path / "job"
    (job / "label").mkdir(parents=True)
    words = [[0, 0, 1, 1, w] for w in ("Annual", "Report", "2025")]
    (job / "label" / "layout.jsonl").write_text(
        "\n".join(f'{{"page_no": {n}, "words": {__import__("json").dumps(words if n != 2 else [])}}}'
                  for n in (1, 2, 3)) + "\n")
    text = llm.summary_input(job, {"doc_id": "x", "lang": "en"})
    assert text.startswith("doc_id: x\nlang: en") and "p1: Annual Report 2025" in text and "p2:" not in text
    r = llm.Runner(enabled_cfg(tmp_path), chat=Chat("It is an annual report."), cache=DictCache(), env=ENV)
    row = r.summary(job, {"doc_id": "x"})
    assert row["kind"] == "summary" and row["region_id"] is None and contract.schema_errors("llm", row, "r") == []


def test_rows_sort_summary_first_then_reading_order():
    rows = [{"kind": "chart_table", "region_id": "b"}, {"kind": "describe", "region_id": "b"},
            {"kind": "describe", "region_id": "a"}, {"kind": "summary", "region_id": None}]
    assert [(r["kind"], r["region_id"]) for r in llm.sort_rows(rows, {"a": 1, "b": 0})] == \
        [("summary", None), ("describe", "b"), ("chart_table", "b"), ("describe", "a")]

