# Stage S: service freeze, search, clusters, review queue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the v2 service a complete, typed and frozen HTTP API (existing endpoints unchanged plus the new ones of the design §3), with FTS5 search, deterministic clusters, a review queue and the `ack` review action.

**Architecture:** Three new pure modules (`search`, `clusters`, `queue`) read the job's work files and are wired into `service/app.py`. Pydantic response models in `service/api_models.py` make the OpenAPI document describe every body; the document is snapshotted to `contract/api/openapi-v1.json` and a test fails on any drift. Request bodies stay hand-parsed, so status codes and error bodies of existing endpoints do not change; their schemas are declared through `openapi_extra`.

**Tech Stack:** Python 3.12, FastAPI 0.128 / Starlette 0.50 (FileResponse has HTTP Range), SQLite FTS5 (state.db), pydantic v2, pytest.

**Spec:** `docs/intake-ui-llm-design-v1.md` §3 (authoritative), on top of `docs/intake-design-v2.md`. Decisions taken with Peter on 2026-10-21 (PR description of `intake/ui-llm` records them): per-job LLM switches live in `workflow.json` (stage L); the prototype's "wrong" buttons are `ack` with `verdict: "wrong"`; the queue keeps a `seen` state stored only in `state.db`.

## Global Constraints

- Everything in code, comments, tests and UI text is English (CI greps for CJK in `src tests scripts contract`).
- Existing endpoints keep their paths, status codes, error bodies and response fields; changes are additive only.
- Contract v2 only gains fields and enum values (`contract/v2/*.schema.json`); a changed field means v2 → v3, which is out of scope.
- Delivered files do not change except where the design adds to them (`ack` rows in `review.jsonl`, `resolved.action = "ack"` in `gaps.jsonl`).
- Search uses SQLite FTS5 inside `work_dir/state.db`; no other service. Clusters are deterministic; no vector model.
- `kb_intake` must not import `kb_intake.service` (import-linter); new core modules live in `src/kb_intake/`, endpoints in `service/app.py`.
- Search response < 200 ms on the OCBC job (276 pages, 4,159 regions, 202 tables).
- No generative model is called in stage S.

## Review Focus

1. A search string with FTS5 syntax (`"`, `*`, `-`, `(`, `AND`, `NEAR`, a lone `'`) or non-Latin text must be treated as literal words, never a 500. → Task 4 test `test_search_treats_query_syntax_as_text`.
2. Search, facets, clusters or queue on a job that is not labelled yet (status `uploaded`, label running or failed) → 409 "not labelled yet", as `/pages/{n}` does today, never a 500. → Task 4 `test_regions_before_label_is_409`, Task 5, Task 6.
3. A review applied from the CLI on the same work dir (the server did not see it) must show up in the next search: the index notices that the work files changed. → Task 4 `test_index_follows_work_files_changed_elsewhere`.
4. A malformed or foreign `cursor`, `limit` out of range, an unknown `type`/`status`/`gap` value → 422 with a message, never a 500 or a silent empty page. → Task 4 `test_regions_rejects_bad_params`.
5. `ack` of an unknown item, of an item already resolved, or together with a reclassify that removes the same item in one call → 400 and nothing applied (all-or-nothing as today). → Task 7 `test_ack_refusals_apply_nothing`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kb_intake/search.py` (new) | Region rows of a job (label or current stage), text by box for label regions, FTS5 index kept in step with the work files, filtering, facets, cursor paging |
| `src/kb_intake/clusters.py` (new) | Deterministic groups: repeated text, same shape, same table header, same gap |
| `src/kb_intake/queue.py` (new) | Review queue items (gaps, weak continuation hints, LLM checks), their status, item ids |
| `src/kb_intake/review.py` (modify) | `ack` op, its validation and resolution |
| `src/kb_intake/deliver.py` (modify) | `check_review` accepts `ack` rows |
| `src/kb_intake/jobs.py` (modify) | `queue_seen` table, `mark_seen`, `seen` |
| `src/kb_intake/config.py` (modify) | `llm` section (read only; used by stage L), `public_llm_info()` |
| `src/kb_intake/service/api_models.py` (new) | Pydantic response models and request-body schemas |
| `src/kb_intake/service/openapi_snapshot.py` (new) | `python -m kb_intake.service.openapi_snapshot [--write]` |
| `src/kb_intake/service/app.py` (modify) | New endpoints, response models, additive fields |
| `contract/v2/review.schema.json`, `gap.schema.json` (modify) | `ack`, `target`, `verdict`; `resolved.action` gains `ack` |
| `contract/api/openapi-v1.json` (new) | The frozen API |
| `docs/api-v1.md` (new) | Human-readable endpoint list (generated table + notes on search, clusters, queue) |
| `tests/test_api_compat.py` + `tests/data/api_v2_responses.json` (new) | Existing endpoints answer as before |
| `tests/test_openapi.py`, `tests/test_search.py`, `tests/test_clusters.py`, `tests/test_queue.py` (new); `tests/test_review.py`, `tests/test_service.py` (extend) | |

---

### Task 1: Pin the current responses of the existing endpoints

**Files:**
- Create: `tests/test_api_compat.py`, `tests/data/api_v2_responses.json`

**Interfaces:**
- Produces: `masked(obj) -> obj` in `tests/test_api_compat.py` — replaces values of keys `job_id, id, created_at, updated_at, at, approved_at, confirmed_at, dir, sha256, package, delivery_dir, models_dir, version, run, duration_s, seconds` and any string matching the job-id pattern `\d{8}T\d{6}-[0-9a-f]{6}` with `"<masked>"`, recursively; keeps key sets and value types.

- [ ] **Step 1:** Write `test_existing_endpoints_answer_as_before(svc)`: drive the fake flow (upload → label → approve → review one reclassify and one exclude → confirm → deliver) and record, for each of the 19 existing endpoints, `{"status": code, "body": masked(json)}` (for `/pages/{n}/image` record status and content-type only; for `/` the redirect location). Compare with `tests/data/api_v2_responses.json`; when the env var `KB_INTAKE_WRITE_GOLDEN=1` is set, write the file instead.
- [ ] **Step 2:** Generate the golden on the unchanged code: `KB_INTAKE_WRITE_GOLDEN=1 uv run pytest tests/test_api_compat.py -q`, then run without the env var. Expected: PASS.
- [ ] **Step 3:** Commit `test(api): pin the v2 responses of the existing endpoints`.

### Task 2: Response models and the OpenAPI snapshot

**Files:**
- Create: `src/kb_intake/service/api_models.py`, `src/kb_intake/service/openapi_snapshot.py`, `tests/test_openapi.py`, `contract/api/openapi-v1.json`
- Modify: `src/kb_intake/service/app.py` (add `response_model=` / `responses=` / `openapi_extra=` to every route)

**Interfaces:**
- Produces: `openapi_snapshot.document(app: FastAPI) -> dict` — `app.openapi()` with `info.version` removed, serialised by `dump(doc) -> str` as `json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"`; `main(argv)` checks (exit 1 on drift, printing a unified diff) or writes with `--write`.
- Produces in `api_models.py` (pydantic v2, `model_config = ConfigDict(extra="allow")` on records that carry work-only fields): `Region`, `Table`, `Cell`, `Gap` (mirror `contract/v2` fields; `Gap.stage` optional), `JobSummary`, `JobDetail`, `Event`, `WorkflowView`, `PageView`, `ReviewResult`, `Started`, `CreateJobResult`, `PackagesView`, `EnginesView`, `DirectoriesView`, `Health`, `ErrorBody` (`detail: str | dict | list`, `error: str | None`). Request schemas: `CreateJobJson`, `ApproveBody`, `ReviewBody` (`ops: list[ReclassifyOp | ExcludeOp | AckOp]`), `ConfirmBody`.

- [ ] **Step 1:** Write `tests/test_openapi.py::test_openapi_matches_snapshot` — `document(create_app(cfg))` serialised equals `contract/api/openapi-v1.json`; and `test_every_operation_documents_its_body` — every 2xx JSON response in the document has a schema other than `{}`, and every POST/PUT with a body has `requestBody`.
- [ ] **Step 2:** Run it. Expected: FAIL (file missing; bodies undocumented).
- [ ] **Step 3:** Add the models and route annotations. Request bodies keep being parsed by `body_of` / `need_name`; declare them with `openapi_extra={"requestBody": {"content": {"application/json": {"schema": <Model>.model_json_schema(ref_template=...)}}, "required": True}}` (multipart for `POST /v1/jobs` too). Error responses: `responses={404: {"model": ErrorBody}, 409: ..., 422: ...}` as each route can return them.
- [ ] **Step 4:** `uv run python -m kb_intake.service.openapi_snapshot --write`, then `uv run pytest tests/test_openapi.py tests/test_api_compat.py tests/test_service.py -q`. Expected: PASS (Task 1's golden unchanged proves the models did not alter responses).
- [ ] **Step 5:** Commit `feat(api): typed responses and the OpenAPI snapshot`.

Every later task that adds or changes an endpoint ends with `--write` and commits the snapshot together with the change.

### Task 3: `GET /v1/jobs/{id}/source.pdf`

**Files:** Modify `src/kb_intake/service/app.py`; Test `tests/test_service.py`

- [ ] **Step 1:** Tests: `test_source_pdf_whole` (200, `application/pdf`, bytes equal `job.source`, `accept-ranges: bytes`); `test_source_pdf_range` (`Range: bytes=0-99` → 206, 100 bytes, `content-range: bytes 0-99/<size>`); `test_source_pdf_bad_range` (`bytes=999999999-` → 416); unknown job → 404.
- [ ] **Step 2:** Run → FAIL. **Step 3:** `FileResponse(job.source, media_type="application/pdf", headers={"ETag": f'"{job.sha256}"', "Cache-Control": "private, max-age=0, must-revalidate"})`. **Step 4:** Run → PASS; snapshot `--write`. **Step 5:** Commit `feat(api): source.pdf with HTTP Range`.

### Task 4: Search index, `GET /regions`, `GET /facets`

**Files:** Create `src/kb_intake/search.py`, `tests/test_search.py`; Modify `src/kb_intake/service/app.py`, `tests/test_service.py`

**Interfaces:**
- Produces `search.Rows` (dataclass): `stage: "label" | "current"`, `regions: list[dict]` (each = the region record plus `text` — the region's text, or for label regions and regions whose text is null, the text by box from the text layer — plus `gaps: list[str]` sorted unique open-or-resolved gap codes of the region, plus `llm: list[str]` kinds), `tables: list[dict]`.
- Produces `search.load_rows(jobs: Jobs, job_id: str) -> Rows` — `KeyError` when the job has no `label/regions.jsonl`. Label stage before `POST_EXTRACT` statuses, current after. Label-region text by box is computed once with `textlayer.words(page)` + `words_in` + `text_of` and cached in `jobs/<id>/search/label_text.jsonl` (`{region_id, text}` rows); delivered files are not touched.
- Produces `search.Index(db_path: Path)` with `ensure(job_id: str, rows: Rows, signature: str) -> None` (rebuilds the job's FTS rows when `signature` differs from the stored one; table `search_meta(job_id primary key, signature)`), `match(job_id: str, q: str) -> dict[str, Match]` (region_id → best `Match(source: "region"|"cell"|"llm", snippet: str, bbox: list[float], table_id: str|None, r: int|None, c: int|None)`; priority region > cell > llm, then lowest rowid).
- Produces `search.signature(job_dir: Path) -> str` — sha256 of `(relative path, size, mtime_ns)` for `label/regions.jsonl`, `extract/regions.jsonl`, `extract/tables.jsonl`, `review/resolved.jsonl`, `extract/gaps.jsonl`, `review/gaps.jsonl`, `llm/llm.jsonl` that exist.
- Produces `search.query(rows: Rows, matches: dict | None, *, types, statuses, page, gaps, min_score, max_score, cursor: int | None, limit: int) -> dict` → `{"total", "items", "next_cursor"}` in `reading_order`; and `search.facets(rows, matches, **same filters) -> dict` → `{"total", "type": {}, "status": {}, "gap": {}}` where each dimension is counted with all filters except its own.
- FTS table: `create virtual table if not exists search_fts using fts5(job_id unindexed, region_id unindexed, source unindexed, ref unindexed, text, tokenize = "unicode61 remove_diacritics 2")`; `ref` = JSON `{"table_id", "r", "c", "bbox"}` for cells, `{"bbox"}` otherwise. One row per region text, per non-empty cell, per LLM text (stage L fills the last; the source is read if `llm/llm.jsonl` exists).
- Query building: split `q` on whitespace; each token becomes `"<token with " doubled>"*`; tokens joined with spaces (implicit AND). Empty after stripping → no text filter.
- Item shape (`api_models.RegionRow`): `region_id, page_no, bbox, type, subtype, score, label_source, layer, reading_order, status, handler, table_id, font, text_preview (first 200 chars of text or null), gaps, llm, match (Match | null)`. `status`/`handler` are null before extraction.
- Endpoint params: `q: str = ""`, `type: list[str] = Query([])`, `status: list[str] = Query([])`, `page: int | None`, `gap: list[str] = Query([])`, `min_score`, `max_score: float | None`, `cursor: str | None` (base64url of the decimal `reading_order` of the last item), `limit: int = 100` (1..500). Unknown enum values → 422.
- Startup: `create_app` checks FTS5 (`create virtual table temp.t using fts5(x)`) and raises `RuntimeError("SQLite without FTS5: ...")` if absent.

- [ ] **Step 1: Write failing tests** in `tests/test_search.py` on the `jobflow.in_review(tmp_path)` job and on a labelled-only job:
  - `test_label_rows_have_text_by_box` — every label row whose box holds text-layer words has non-empty `text`; the delivered files are byte-identical before and after `load_rows`.
  - `test_search_finds_region_and_cell_text` — a word only in a table cell is found with `match.source == "cell"`, `match.table_id`, `r`, `c`, `bbox`; a word in body text with `source == "region"`.
  - `test_results_in_reading_order_and_paged` — `limit=2` pages through all hits; concatenation equals the unpaged list; `next_cursor` is null on the last page.
  - `test_filters_combine` — `type=table&q=<word>` returns only tables containing it; `facets` for the same filters counts `type` without the type filter and `status` with it.
  - `test_search_treats_query_syntax_as_text` — for q in `['"', '*', '-x', '(a', 'AND', 'NEAR(', "'", '数字']` the call returns 200 and a list (Review Focus 1).
  - `test_index_follows_work_files_changed_elsewhere` — apply a review with `review.apply` directly (not through the app), then `/regions?q=` reflects the new type (Review Focus 3).
  - In `tests/test_service.py`: `test_regions_before_label_is_409` and `test_regions_rejects_bad_params` (`cursor=zz`, `limit=0`, `limit=501`, `type=nope` → 422) (Review Focus 2, 4).
- [ ] **Step 2:** `uv run pytest tests/test_search.py -q` → FAIL.
- [ ] **Step 3:** Implement `search.py` and the two endpoints (`409` when `load_rows` raises `KeyError`, like `/pages/{n}`).
- [ ] **Step 4:** Run → PASS; full suite; snapshot `--write`.
- [ ] **Step 5:** Commit `feat(search): FTS5 search, filters and facets over a job's regions`.

### Task 5: Clusters, `GET /clusters`

**Files:** Create `src/kb_intake/clusters.py`, `tests/test_clusters.py`; Modify `app.py`

**Interfaces:**
- Consumes `search.load_rows`.
- Produces `clusters.normalise(text: str) -> str`: NFKC → `casefold()` → every run of `\d` becomes `#` → runs of whitespace become one space → strip.
- Produces `clusters.build(rows: search.Rows, pages: dict[int, dict], gaps: list[dict], top: float = 0.12, bottom: float = 0.88) -> list[dict]`, each `{"cluster_id", "kind", "label", "basis", "n", "pages", "members"}`, members sorted by `reading_order`, clusters sorted by `(kind order, -n, label)`; only `n >= 2`. Regions with status `excluded` or `absorbed` are left out.
  - `repeated_text`: regions of every type except `table, picture, chart, diagram, icon, logo, signature, stamp, formula`, with non-empty normalised text, whose vertical centre / page height is `< top` (band `top`) or `> bottom` (band `bottom`). Key `(type, normalised text, band, font.name, round(font.size))`; also `len(set(pages)) >= 2`. `label` = the original text of the first member (≤ 80 chars); `basis` = `{"type", "text", "band", "font": {"name", "size"}}`.
  - `same_shape`: types `icon, logo, signature, stamp, picture, chart, diagram`. Key `(type, subtype, round(2*log2(w)), round(2*log2(h)))` with `w, h` the bbox width and height in PDF points (minimum 1). `label` = `"<Type plural> about <median w>×<median h> pt"`; `basis` = `{"type", "subtype", "size_bucket": [bw, bh]}`.
  - `same_table_header`: table regions with a table; key `(n_cols, " | ".join(normalise(cell text or "") for the r == 0 cells ordered by c))`, skipped when every first-row cell is empty. `label` = `'Tables headed "<first-row text>"'`; `basis` = `{"n_cols", "first_row"}`.
  - `same_gap`: unresolved gaps with a `region_id`, grouped by `code`. `label` = `queue.GROUP_LABELS[code]` (Task 6); `basis` = `{"code"}`.
  - `cluster_id` = `f"{kind}:{sha1(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]}"`.
- Endpoint `GET /v1/jobs/{id}/clusters?kind=` (`kind` optional, one of the four) → `{"clusters": [...]}`; 409 before label.

- [ ] **Step 1:** Tests: `test_normalise` (`"Page 12 of  276"` → `"page # of #"`; full-width digits normalised); `test_repeated_header_grouped_across_pages` (same text in the top band on 3 pages → one cluster n=3; the same text mid-page is not a member; a different font size splits it); `test_single_page_repeat_is_not_a_cluster`; `test_same_shape_buckets` (29×29 and 30×30 icons together; 29×29 and 64×31 apart); `test_same_table_header` (two 5-column tables with first row `GROUP | BANK` and `Group | Bank` → one cluster; a 6-column one apart); `test_cluster_ids_are_stable` (two builds from the same rows give identical output).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; snapshot `--write`. **Step 5:** Commit `feat(clusters): deterministic similar-region groups`.

### Task 6: Review queue, `GET /queue`, `POST /queue/seen`

**Files:** Create `src/kb_intake/queue.py`, `tests/test_queue.py`; Modify `src/kb_intake/jobs.py`, `app.py`

**Interfaces:**
- Produces `queue.GROUP_LABELS: dict[str, str]` — `table_suspected: "Suspected missed tables"`, `low_confidence: "Low confidence"`, `table_words_unassigned: "Unassigned values in tables"`, `table_unmatched: "Tables not matched between stages"`, `no_text_layer: "No text layer"`, `unhandled: "Unhandled regions"`, `lang_mismatch: "Language differs from the declaration"`, `extractor_degraded: "Extractor degraded"`, `llm_failed: "LLM calls that failed"`, `continued_hint: "Continuation hints (weak)"`, `llm_check: "Chart descriptions to check"`. Group order = this order.
- Produces `queue.item_id(target: dict) -> str`: `gap:{code}:{page_no or 0}:{region_id or table_id or "-"}`; `hint:{region_id}` (the table's region — table ids are renumbered by review); `llm:{code}:{region_id}` where `code` is the LLM kind.
- Produces `queue.items(jobs: Jobs, job_id: str) -> list[dict]`, each `{"item_id", "group", "kind", "code", "page_no", "region_id", "table_id", "bbox", "detail", "status", "resolution", "target"}`:
  - one per gap of `review.all_gaps` (post-extract only; the queue is empty before extraction);
  - one per table whose `continued_hint.reason == "same_first_row"` (`detail` = `"May continue table <hint table_id> (same first row)"`);
  - one per `describe` LLM output (stage L; none while `llm/llm.jsonl` is absent).
  - `status`: `resolved` if the gap has `resolved` or an ack op targets the item; else `seen` if in `jobs.seen(job_id)`; else `open`. `resolution` = the gap's `resolved` or `{"action": "ack", "review_seq"}`.
- Produces `queue.view(items) -> dict` → `{"total", "done", "groups": [{"key", "label", "total", "open", "seen", "resolved", "items"}]}` (`done` = resolved count; empty groups omitted).
- `jobs.py`: schema gains `create table if not exists queue_seen (job_id text not null, item_id text not null, at text not null, primary key (job_id, item_id))`; `Jobs.mark_seen(job_id: str, item_ids: list[str]) -> int` (insert or ignore; returns rows added); `Jobs.seen(job_id: str) -> set[str]`.
- Endpoints: `GET /v1/jobs/{id}/queue` → `queue.view(...)` (409 before label; empty groups before extraction). `POST /v1/jobs/{id}/queue/seen` body `{"item_ids": [str]}` (JSON, ≤ 1000 ids, unknown ids → 422) → `{"updated": n}`; allowed in any state from `in_review` on.

- [ ] **Step 1:** Tests: `test_queue_groups_gaps_and_weak_hints`; `test_item_ids_survive_review_renumbering` (reclassify another region to table → the hint item keeps its id); `test_seen_is_work_state_only` (mark seen → status seen; the delivered package has no trace of it); `test_resolved_after_reclassify_and_ack`; `test_queue_before_extract_is_empty`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; snapshot `--write`. **Step 5:** Commit `feat(queue): review inbox with open/seen/resolved items`.

### Task 7: The `ack` review action, `GET /gaps` with `resolved`, `GET /review`

**Files:** Modify `contract/v2/review.schema.json`, `contract/v2/gap.schema.json`, `src/kb_intake/review.py`, `src/kb_intake/deliver.py`, `src/kb_intake/service/app.py`; Test `tests/test_review.py`, `tests/test_deliver.py`, `tests/test_service.py`

**Interfaces:**
- Consumes `queue.items(jobs, job_id) -> list[dict]` and `queue.item_id` (Task 6).
- Contract (additive): review `action` enum gains `"ack"`; new optional properties `target` (`{"kind": "gap"|"hint"|"llm", "code": str|null, "page_no": int|null, "region_id": str|null, "table_id": str|null}`, `additionalProperties: false`) and `verdict` (`"ok"|"wrong"`); `from`/`to` stay required and are null on ack rows. gap `resolved.action` enum gains `"ack"`.
- Op: `{"action": "ack", "item_id": str, "verdict"?: "ok"|"wrong" (default "ok"), "note"?: str}`. Row: `{"action": "ack", "region_id": item.region_id, "from": null, "to": null, "by", "at", "note", "target": item.target, "verdict"}`.
- Validation (inside the existing all-or-nothing `_validate`): `item_id` must be an open or seen item of `queue.items`; not twice in one call; refused if another op in the same call reclassifies or excludes the item's region.
- Effect: a `gap` target adds `{"code", "page_no", "region_id", "resolved": {"action": "ack", "review_seq": seq}}` to `review/resolved.jsonl`; `hint` and `llm` targets change no work file (their status comes from the recorded ops). A call with only `ack` ops does not open `extract.Context`.
- `deliver.check_review`: an `ack` row with a `gap` target requires that gap in the package's `gaps.jsonl` with `resolved.action == "ack"`; other ack rows need no package change.
- `GET /v1/jobs/{id}/gaps`: each gap also gets `resolved` from `review.all_gaps` when present (additive; `stage` kept).
- `GET /v1/jobs/{id}/review` → `{"ops": [rows in seq order]}` (from `jobs.ops`).

- [ ] **Step 1:** Tests: `test_ack_resolves_gap_and_records_target` (gap → resolved action ack, review row has target and verdict "ok"); `test_ack_wrong_verdict_on_hint_changes_no_file` (tables.jsonl unchanged, `continued_hint` kept); `test_ack_refusals_apply_nothing` (unknown item, already resolved, twice in a call, together with an exclude of the same region → `ReviewError`, no file or op changes) (Review Focus 5); `test_delivery_accepts_ack_rows` (package passes `kb-intake check`, `review.jsonl` row validates); `test_gaps_endpoint_has_resolved`; `test_review_endpoint_lists_ops`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Full suite → PASS (Task 1 golden: `/gaps` gains `resolved` only on resolved gaps — update the golden with `KB_INTAKE_WRITE_GOLDEN=1` and check the diff is only that addition). Snapshot `--write`. **Step 5:** Commit `feat(review): ack action (contract v2 additive), resolved gaps in /gaps, /review`.

### Task 8: LLM placeholders in the API (`/summary`, `/llm`, `llm` fields)

**Files:** Modify `src/kb_intake/config.py`, `app.py`; Test `tests/test_config.py`, `tests/test_service.py`

**Interfaces:**
- Produces `config.LlmConfig` (frozen dataclass): `enabled: bool = False`, `provider: str = "openai_compatible"`, `base_url: str = "https://api.minimax.io/v1"`, `model: str = "MiniMax-M3"`, `api_key_env: str = "INTAKE_LLM_API_KEY"`, `remote: bool = True`, `features: dict = {"summary": True, "describe": True, "chart_table": False}`, `cache_dir: Path` (default `~/.kb-intake/llm-cache`). `Config.llm: LlmConfig`; YAML key `llm:` with the design §4.1 keys; unknown keys → `ConfigError`.
- Produces `config.public_llm_info(cfg) -> dict` → `{"enabled", "remote", "provider", "model", "base_url", "features", "key_present": bool}` (never the key; `enabled` is false when the key is missing).
- Endpoints (shapes frozen now, filled by stage L): `GET /v1/jobs/{id}/summary` → `{"status": "off"|"pending"|"done"|"failed", "provider", "model", "remote", "text"}` (`off` in stage S); `GET /v1/jobs/{id}/llm?kind=&region_id=` → `{"enabled", "remote", "progress": {"summary"|"describe"|"chart_table": {"total", "done", "cached", "failed"}}, "items": []}`.
- Additive fields: `GET /workflow` gains `llm` (= `public_llm_info`); `GET /v1/health` and `GET /v1/engines` gain `llm` (same). Task 1's golden is updated for these additions only.

- [ ] **Step 1:** Tests: `test_llm_config_defaults_off`, `test_llm_config_rejects_unknown_key`, `test_llm_info_never_contains_the_key` (set the env var to a sentinel, assert it is absent from every endpoint's JSON), `test_summary_and_llm_are_off_without_config`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS; golden diff is only the `llm` additions; snapshot `--write`. **Step 5:** Commit `feat(api): LLM endpoints and fields (off until stage L)`.

### Task 9: Freeze and measure

**Files:** Create `docs/api-v1.md`; Modify `README.md` (one paragraph: the API is frozen, how to regenerate the snapshot)

- [ ] **Step 1:** Prove the snapshot gate: change one response field name locally, run `uv run pytest tests/test_openapi.py -q` → FAIL with a diff naming the field; revert. Record the output for the PR report.
- [ ] **Step 2:** Write `docs/api-v1.md`: endpoint table (method, path, purpose, body, response model name), search/cluster/queue rules as implemented.
- [ ] **Step 3:** OCBC measurements on a **copy** of `/Users/peterxu/workspace/intake-data/acceptance-v2/ui/work` (never the original): first-search index build time; `q=overdue` hits (expect p106 among them); `q=overdue&type=text`; p50/p95 latency of 50 `/regions` calls with `q` (target < 200 ms); cluster counts per kind and the top 5 of each; queue group counts. Record commands and numbers.
- [ ] **Step 4:** `uv run lint-imports && uv run pytest -q` → all pass; CJK grep clean.
- [ ] **Step 5:** Commit `docs(api): v1 endpoint list; README note on the frozen API`; push; write the stage S report in the PR.
