# Stage L: LLM (MiniMax) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional, off-by-default LLM help (document summary, chart and picture descriptions, experimental chart-to-table) through MiniMax's OpenAI-compatible endpoint, cached for reproducibility, kept out of the core files, never blocking the flow.

**Architecture:** A stdlib HTTP client (`llm_client`) and a content-addressed cache (`llm_cache`) under `cache_dir/llm`, used by `llm.py` (prompts, inputs, outputs). The extract step runs `describe` / `chart_table` on regions whose workflow handler is `describe`; the summary runs after labelling in a background thread (server) or at the end of `kb-intake label` (CLI). Outputs go to `llm/llm.jsonl` in the work dir and `llm.jsonl` in the package; failures become `llm_failed` gaps.

**Tech Stack:** Python stdlib `urllib.request` (no new dependency, `uv.lock` untouched), PyMuPDF for region crops, JSON Schema.

**Spec:** `docs/intake-ui-llm-design-v1.md` §2, §4 (authoritative). Facts checked before coding (PR #6 description): MiniMax `POST {base_url}/chat/completions`, Bearer key, `image_url.url` accepts a Base64 data URL, ≤ 10 MB per image; `temperature` / `top_p` / seed have no effect and M3 has no `response_format`, so reproducibility comes only from the cache and JSON is validated by us. Decisions: per-job `describe` / `chart_table` switches in `workflow.json` (`llm` block); `summary` follows config only; Docling's picture description / chart extraction are not used.

## Global Constraints

- Off by default. A call that sends data out happens only when `config.public_llm_info(cfg)["enabled"]` (config `llm.enabled` and the key's env var set) **and** the feature is on (config `llm.features`, then the workflow's `llm` block for describe / chart_table).
- The key is read from the env var named by `llm.api_key_env` at call time; never written to any file, log, event, gap, response or exception message.
- `regions.jsonl`, `tables.jsonl` and the other core files are not changed by LLM output (the `describe` handler's region row is the `text` handler's row with `handler: "describe"`).
- `llm.jsonl` rows carry no time; durations, tokens and cache hits go to `manifest.run.llm`. Rows are sorted: summary first, then by region reading order, then kind (`describe` before `chart_table`).
- Cache key = sha256 of `provider \0 model \0 prompt_version \0 input_sha256`; a re-run with the same inputs makes no network call and yields byte-identical `llm.jsonl`.
- A failure (timeout, HTTP error, quota, refusal, unparseable answer) records gap `llm_failed` and the step continues. Contract v2 only gains fields.
- With the LLM off, packages are identical to stage S output (no `llm.jsonl`; `manifest.llm = {"used": false}` is the only addition).
- English everywhere; no new third-party dependency.

## Review Focus

1. The key leaking: an exception from urllib containing the request headers, a `repr` of the config, an error body echoed into a gap detail. → Task 2 `test_errors_never_carry_the_key`; Task 4 `test_failure_gap_has_no_key`.
2. The model answers with `<think>…</think>` reasoning, Markdown fences around JSON, or empty content → description text without the reasoning; chart table parsed from inside a fence; empty → `llm_failed`. → Task 2 `test_reasoning_is_stripped`, Task 3 `test_chart_table_json_in_fence` / `test_empty_answer_fails`.
3. A huge region (full-page picture) or a tiny one (1×1 pt) → image kept ≤ 2000 px on the long side and well under 10 MB; a degenerate box still renders. → Task 3 `test_crop_limits`.
4. LLM enabled but the network is down or the endpoint returns 5xx for every call → extraction finishes in bounded time with one `llm_failed` gap per region, not a hung step. → Task 4 `test_endpoint_down_degrades_with_gaps` (local server returning 503).
5. Workflow approved with `describe` while the LLM is disabled (or the key disappears between approval and extraction) → regions extracted like `text`, no call, no gap, `manifest.llm.used = false`. → Task 4 `test_describe_without_llm_is_text`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kb_intake/llm_client.py` (new, **delegated**) | `chat(...)`: one OpenAI-compatible chat completion over urllib; retries; error mapping; reasoning stripped |
| `src/kb_intake/llm_cache.py` (new, **delegated**) | `Cache(dir)`: `get(key)`, `put(key, value)` atomic JSON files |
| `src/kb_intake/llm.py` (new) | prompts (versioned), inputs + `input_sha256`, region crops, summary / describe / chart_table, rows and gaps, progress |
| `src/kb_intake/vocab.py`, `workflow.py`, `contract.py`, `extract.py`, `review.py`, `label.py`, `deliver.py`, `queue.py`, `search.py`, `cli.py`, `service/app.py`, `service/runner_thread.py`, `service/api_models.py` (modify) | wiring |
| `contract/v2/llm.schema.json` (new); `gap`, `workflow`, `manifest` schemas (modify, additive) | contract |
| `src/kb_intake/workflows/default.yaml` | unchanged |

---

### Task 1: Contract and vocabulary (main session; shared state)

**Files:** `contract/v2/llm.schema.json` (new), `contract/v2/{gap,workflow,manifest}.schema.json`, `src/kb_intake/{vocab,workflow,contract}.py`; tests `tests/test_workflow.py`, `tests/test_contract_llm.py` (new)

**Interfaces (produced):**
- `vocab.HANDLERS` gains `"describe"`; `vocab.DESCRIBE_TYPES = ("chart", "diagram", "picture")` (the only types that may use it); `vocab.GAP_CODES` gains `"llm_failed"`, `GAP_LABELS["llm_failed"] = "LLM calls that failed"`; `vocab.LLM_KINDS = ("summary", "describe", "chart_table")`.
- `workflow.KEYS` gains optional `"llm"`: `{"describe": bool, "chart_table": bool}` (both keys optional; unknown keys refused). `parse` returns it only when present; `dump` writes it after `exclude_pages` only when present. `workflow.llm_switch(wf, feature, default: bool) -> bool`.
- `workflow.validate` refuses `describe` on a type outside `DESCRIBE_TYPES`.
- `llm.schema.json` row: `kind` (enum LLM_KINDS), `region_id` (string|null; null iff summary), `provider`, `model`, `prompt_version`, `input_sha256` (64 hex), `output` (`{"text": str}` or `{"table": {"title": str|null, "columns": [str], "rows": [[str|number|null]], "units": str|null, "notes": str|null}}`), `weight: "low"`; all required, no extra keys.
- `manifest.schema.json`: optional `llm`: `{"used": bool, "provider"?, "model"?, "remote"?, "counts"?: {"summary", "describe", "chart_table", "failed"}}`.
- `contract.ROWS` / `OPTIONAL_FILES` gain `llm.jsonl` → kind `llm`; the check also verifies every `region_id` of `llm.jsonl` exists in `regions.jsonl`.

- [ ] Step 1: failing tests: workflow with `chart: describe` parses and dumps round-trip; `table: describe` refused; `llm: {describe: false}` round-trips, `llm: {ocr: true}` refused; an `llm.jsonl` row of each kind validates, a row with `at` or without `weight` does not; a package with `llm.jsonl` naming an unknown region fails `contract.check`.
- [ ] Step 2: run → FAIL. Step 3: implement. Step 4: full gate → PASS. Step 5: commit `feat(contract): describe handler, llm.jsonl, llm_failed, workflow llm switches (v2 additive)`.

### Task 2: HTTP client and cache (**delegated** to one subagent in its own worktree; Tallyback task)

**Files:** `src/kb_intake/llm_client.py`, `src/kb_intake/llm_cache.py`, `tests/test_llm_client.py`, `tests/test_llm_cache.py` — nothing else.

**Interfaces (produced):**
- `llm_client.LlmError(Exception)` with `.code: str` in `{"timeout", "network", "http_<status>", "quota", "rate_limit", "refused", "auth", "bad_response", "empty"}` and a message that never contains the key or request headers.
- `llm_client.Completion` (frozen dataclass): `text: str` (content with every `<think>…</think>` block removed and stripped), `usage: dict` (the response's `usage` or `{}`), `model: str`.
- `llm_client.chat(base_url: str, model: str, key: str, content: list[dict], *, max_tokens: int = 2048, timeout: float = 120.0, retries: int = 2, opener=None) -> Completion` — POST `{base_url.rstrip('/')}/chat/completions` with JSON `{"model", "messages": [{"role": "user", "content": content}], "max_completion_tokens": max_tokens}`, headers `Authorization: Bearer <key>`, `Content-Type: application/json`. Retries (backoff 1 s, then 4 s) on timeout, connection errors, HTTP 429 / 500 / 502 / 503 / 504 and MiniMax `base_resp.status_code` 1002 / 1001; no retry on other errors. Maps `base_resp.status_code` 1008 / 2056 → `quota`, 1002 / 2045 / 1041 → `rate_limit`, 1026 / 1027 → `refused`, 1004 / 2049 → `auth`, 1001 → `timeout`; empty content → `empty`. `opener` is for tests only (defaults to `urllib.request.urlopen`).
- `llm_cache.Cache(root: Path)`: `get(key: str) -> dict | None`, `put(key: str, value: dict) -> None` (file `root/key[:2]/key.json`, written to a temp file then renamed; a corrupt file reads as a miss). `llm_cache.cache_key(provider: str, model: str, prompt_version: str, input_sha256: str) -> str`.

- [ ] Tests run against a real local HTTP server (`http.server` on 127.0.0.1 in a thread) scripted per test: success; `<think>` stripped; 503 twice then 200 (3 requests seen); 503 always → `LlmError("http_503")` after 3 requests; `base_resp` 1008 → `quota`, no retry; slow server → `timeout`; `test_errors_never_carry_the_key` (key `sk-SENTINEL` absent from `str(e)`, `repr(e)` and `e.__cause__` chain text for every error kind); request body and headers as specified. Cache: round trip; corrupt file is a miss; concurrent `put` of the same key from 8 threads leaves one valid file.
- [ ] Gate: `uv run pytest -q`, `uv run lint-imports`, CJK grep — all pass; one commit on the worktree branch.

### Task 3: Prompts, inputs and outputs (`llm.py`, main session)

**Interfaces (produced):**
- `PROMPTS = {"summary": ("summary-1", <text>), "describe": ("describe-1", <text>), "chart_table": ("chart-table-1", <text>)}` (texts in English; describe asks for type, title, axes and units, main content in ≤ 120 words, no interpretation beyond what is visible; chart_table asks for one JSON object with the table keys of the schema and nothing else).
- `crop_png(page: pymupdf.Page, bbox) -> bytes`: render the box at 144 dpi, scaled down so the long side ≤ 2000 px, minimum 1 px; PNG.
- `summary_input(job_dir: Path, decl: dict) -> str`: declared title / issuer / period, then for each page `p{n}: ` + the first 300 characters of its text-layer words; stops at 24,000 characters.
- `input_sha256(kind, text: str, png: bytes | None) -> str`: sha256 over a canonical JSON of `{kind, text, image_sha256}`.
- `parse_table(text: str) -> dict` (raises `ValueError`): the first `{...}` object in the text (fences allowed), checked against the table schema; numbers kept as numbers.
- `Runner(cfg, jobs, job_id)`: `enabled(feature, wf) -> bool`; `summary() -> row | gap`; `describe(region, page) -> list[row | gap]` (describe, then chart_table when enabled and `region["type"] == "chart"`); each call goes through the cache; counters `calls`, `cached`, `failed`, `seconds`, `tokens`.
- Rows and gaps follow the schema; gap `{"code": "llm_failed", "page_no", "region_id", "kind", "reason": LlmError.code, "detail": "<kind> failed: <code>"}`.

- [ ] Tests (with a fake `chat` injected — the HTTP path is Task 2's): `test_crop_limits` (full page and 1×1 box), `test_input_hash_changes_with_text_or_image`, `test_chart_table_json_in_fence`, `test_chart_table_rejects_bad_shape`, `test_empty_answer_fails`, `test_cache_hit_makes_no_call` (second run: 0 calls, identical rows), `test_failure_gap_has_no_key`.

### Task 4: Wiring into the steps and the package (main session)

- `extract._run`: after `finish_tables`, when `Runner.enabled("describe", wf)`, run `describe` for regions with `handler == "describe"` and `status == "extracted"` in reading order, 4 at a time; write `llm/llm.jsonl` (work dir; the summary row, when present, is kept) and add `llm_failed` gaps to `extract/gaps.jsonl`; progress events `llm` started/done with counts. `extract_region` treats `describe` like `text` (row `handler: "describe"`).
- `review.apply`: a region re-extracted under `describe` gets its LLM rows recomputed (cache first); rows of regions that left `describe` are dropped.
- `label`: after `finish_label`, the summary runs when enabled: in `serve`, `StepRunner` starts it on a separate daemon thread after a successful label (not holding the step slot; status file `llm/summary.status` = `pending` | `done` | `failed`); in the CLI, `kb-intake label` runs it before returning. `workflow.suggest` proposes `describe` for chart and diagram only when the LLM is enabled with `describe` on.
- `deliver`: copies `llm/llm.jsonl` to the package when it has rows; `manifest.llm` = `{"used": false}` or `{"used": true, "provider", "model", "remote", "counts"}`; `manifest.run.llm` = `{"calls", "cached", "failed", "seconds", "tokens"}`.
- API: `/summary` reads the summary row / status; `/llm` lists rows (filters `kind`, `region_id`) and progress; `RegionRow.llm` lists the kinds a region has; the search index adds `llm` text rows (source `llm`); the queue adds one `llm_check` item per `describe` row (`item_id` `llm:describe:{region_id}`, group label "Chart descriptions to check").
- Tests: `test_describe_without_llm_is_text`, `test_endpoint_down_degrades_with_gaps` (real local server returning 503; with `retries=0` injected through config for speed), `test_llm_jsonl_in_package_validates`, `test_second_run_hits_cache_and_package_is_identical` (two work dirs, one cache dir, local fake server counting requests: second run 0 requests; packages equal except the exempt fields), `test_summary_runs_after_label_without_blocking` (service: the job reaches `planned` while the summary is pending), `test_queue_has_llm_checks_and_ack_resolves_them`.
- Snapshot `--write` (the shapes were frozen in S; only descriptions may change — a shape change is reported in the PR).

### Task 5: Acceptance

- [ ] Without a key (now): OCBC copy, LLM configured but no key, and LLM disabled: full flow through the CLI; package passes `kb-intake check`; no `llm.jsonl`; `manifest.llm.used = false`; `/summary` `off`; zero outgoing connections (run under `sandbox-exec` denying network, as in the v2 offline test).
- [ ] With a key (when Peter provides `INTAKE_LLM_API_KEY`): report the command and expected cost first. OCBC: summary rows = 1; describe rows = regions with handler `describe` (9 charts + 1 diagram with the suggested workflow); `llm.jsonl` passes the contract check; a second run from a fresh work dir with the same cache: 0 requests, package byte-identical except the exempt fields; chart_table experimentally on for one run, reported separately.
