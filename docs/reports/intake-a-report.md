# Stage A report: splitting the pre-ingest part out of kb (D49, steps 0–3)

Date: 2026-10-20. Every number below comes from a command that was actually run; logs live in
`kb/data/baseline/logs/` (local, not committed, since kb's `data/` is git-ignored).

## 1. What was done, step by step

| Step | What | PR |
|---|---|---|
| 0 | Baseline: rebuilt the 6 reports on kb `main` after #26/#29/#30/#31 were merged; compared with the pre-merge outputs; reloaded Doris and facts; ran the 31-question eval; archived everything and tagged `pre-intake-baseline` (= kb 08993d8) | tag only |
| 1 | kb-intake skeleton: history moved with `git filter-repo` from a temporary clone of kb at the tag, merged into the current `main` (`--allow-unrelated-histories`, no force push); contract v1 schemas; `kb-intake run` / `kb-intake check` | [kb-intake#1](https://github.com/FoFxjc/kb-intake/pull/1) |
| 2 | Flow A (native PDF, `find_tables` only) for the 6 OCBC reports; item-by-item comparison with the baseline | [kb-intake#2](https://github.com/FoFxjc/kb-intake/pull/2) (stacked on #1) |
| 3 | kb reads packages: `pipeline/import_package.py`, the table adapter for `structure`, moved code deleted, admin "upload" replaced by "import package"; D49, README, architecture, api-contract | [kb#32](https://github.com/FoFxjc/kb/pull/32) |

None of the PRs is merged; they wait for Peter.

### Decisions applied (Peter, 2026-10-20)
- Decision numbers: D49 for the split, D50 for the Docling extraction layer (checked free on every kb branch first).
- `document.path` points to `data/packages/{doc_id}@{sha12}/working.pdf`. Step 3 acceptance is "4 JSONL types byte-identical except `document.path`".
- The declaration carries `doc_id`. kb-intake only checks its format and names the package with it. At import, kb recomputes the doc_id with its profile, including the reserved-abbreviation check, and refuses a mismatch.
- `structure` (kb #30) stays in kb. `tables_of()` was split by "does it need the type configuration": find tables, cells and cell boxes moved to kb-intake. `structure` now reads `tables.jsonl` (every cell has a bbox) plus `layout.jsonl` (full-precision words).

### Calls I made where the design left room (please check)
- **Declarations live in kb** (`kb/ref/declarations/*.yaml`, exported from kb's `document` table). kb owns them, and the zh `completeness.note` is Chinese text that must reach the document JSONL byte for byte. kb-intake only reads them.
- **`tables.jsonl` keeps every `find_tables` candidate**, including the 74 that kb drops (markdown shorter than 3 lines). kb's `structure` needs the boxes of *all* tables on a page ("do not reach above another table"), and the table ids keep their positional numbering. kb's adapter applies kb's existing filter.
- **Extra fields beyond intake-design §4:**
  - `tables.jsonl`: `n_rows`, `n_cols`;
  - `pages.jsonl`: `chars`, `image_regions`, `images_discarded` (the kb #29 per-page artifact);
  - `layout.jsonl`: `width`, `height`.

  All are additive and inside contract v1.
- **No `detect` stage row in kb any more.** The admin API and the V5 board show a "package detection" column, built from the manifest by `registry.intake_stage()`. `ingest_run` holds only tree / build / load.
- **Probe scripts keep working.** `p3_generic_probe` and `ocr_testset` build packages through kb-intake's *command line* (`scripts/intake_cli.py`, `KB_INTAKE_CMD`). That is a subprocess, not an import.

## 2. Acceptance

### Step 0: baseline (kb main @ 08993d8)
| Item | Result |
|---|---|
| pytest | 392 passed |
| Rebuild vs pre-merge outputs | `jsonl/*` 24 files, `artifacts/layout` 6, `artifacts/pages` 10, `t3` 29: **0 differences**. node_concept 731 rows: identical except the `tagged_at` timestamp |
| observation / fact / fact_version / fact_obs_link | 408 / 89 / 89 / 408, identical to pre-merge |
| 31-question eval | with year 21/21, without year 17/21, cross-year 8/10 (x02, v05 fail); held-out legacy 3/3, new 4/16 |
| Time | rebuild 9 min 31 s, detect 12 s, Doris reload 31 s, facts 0.8 s, eval 12 s |

The archive with `sha256sums.txt` (45 files) is in `kb/data/baseline/pre-intake-baseline/`.

### Step 1: `kb-intake check` runs
| Item | Result |
|---|---|
| pytest (kb-intake) | 35 passed |
| import-linter | "kb-intake does not import kb" KEPT |
| `kb-intake check` on the 6 packages | 6/6 ok, 0 errors |

### Step 2: packages match the baseline item by item
`scripts/compare_baseline.py` reports **0 differences in every item for all 6 documents**:

| Item compared | Count |
|---|---|
| document fields (page_count, sha256, has_text_layer, page_label_source) | 4 × 6 |
| page text, page label, page kind (with chars, image regions, discarded) | 1,472 pages each |
| page size | 1,472 |
| words (rounded to 0.1 pt like kb's layout artifact, order kept) | 442,691 words on 1,472 pages |
| table ids, markdown and bbox | 1,077 tables (of 1,151 candidates in the packages) |

| Document | Pages | Build time | Tables (all / kept by kb) | Cells |
|---|---|---|---|---|
| 2023 en | 296 | 116 s | 216 / 202 | 18,090 |
| 2023 zh | 220 | 51 s | 180 / 166 | 15,714 |
| 2024 en | 284 | 114 s | 216 / 197 | 18,140 |
| 2024 zh | 200 | 49 s | 167 / 159 | 15,722 |
| 2025 en | 276 | 106 s | 208 / 195 | 17,201 |
| 2025 zh | 196 | 49 s | 164 / 158 | 14,784 |

Determinism: a fresh rebuild of 2025 zh into another directory is byte-identical in all 10 files, except `manifest.json → run`.

The baseline has no cell-level data, so cells and cell boxes were proven in step 3: kb computed `structure` from them and got the baseline bytes.

### Step 3: kb rebuilt from packages
| Item | Standard | Result |
|---|---|---|
| node / page / table_block JSONL | byte-identical | **18/18 identical** (including `structure`) |
| document JSONL | byte-identical except `path` | **6/6**; `path` changed from `data/raw/{doc_id}.pdf` to `data/packages/{doc_id}@{sha12}/working.pdf` |
| node_concept | 731 rows, identical without `tagged_at` | **identical** |
| observation / fact / fact_version / fact_obs_link | 408 / 89 (83 agreed, 6 single) / 89 / 408, identical CSV | **identical** |
| 31-question eval | 21/21, 17/21, 8/10, failures only x02 and v05, per question identical to baseline JSON | **identical**: 33 single-point and 14 cross-year entries (held-out included) and the config all match |
| held-out terms | legacy 3/3, new 4/16 | **identical** |
| pytest | both repos pass | kb 383 passed, kb-intake 35 passed |
| no imports across repos | import-linter on both sides | both KEPT; kb's `pyproject.toml` has no kb-intake dependency |
| contract copy | kb copy = kb-intake `contract/v1/` | 8/8 identical to source commit e57132a and to the kb-intake working tree |

Rebuild time from packages: 1 min 32 s (baseline 9 min 31 s, because `find_tables` no longer runs in kb).

Commands:
```bash
# kb-intake
uv run kb-intake run ../kb/data/raw/<doc>.pdf --decl ../kb/ref/declarations/<doc>.yaml --out $KB_PACKAGES_DIR
uv run kb-intake check $KB_PACKAGES_DIR/*@*
uv run python scripts/compare_baseline.py --packages $KB_PACKAGES_DIR --baseline ../kb/data/baseline/pre-intake-baseline
# kb
uv run python -m pipeline.import_package --all
uv run python -m scripts.p3_rebuild_check
uv run python -m pipeline.loader --recreate && uv run python -m pipeline.facts
uv run python -m scripts.eval recall --inprocess --label intake-step3
uv run python -m scripts.intake_baseline_check --eval data/p2/evals/eval-recall-20261020T183332.json
uv run python -m scripts.check_intake_contract --intake ../kb-intake
```

## 3. Delegations

| Who | What | Constraints | How it was verified | Conclusion |
|---|---|---|---|---|
| subagent `reviewer-intake` | Independent review of kb-intake #1 and #2 | Read only; runs only into a scratch directory; did not write the code | Main session re-checked every finding against the code. B1 confirmed: `t.replace(" ", "")` in `parse_value`. After the fix and a rebuild, 0 cells carry a value parsed from text with inner whitespace. Tests added for S2–S4 | APPROVE WITH FIXES → fixed in 51f290e |
| subagent `reviewer-kb` | Independent review of kb #32 | Read only; no DB writes (SELECT only); did not write the code | Main session read the cited lines for S1–S5; all confirmed and fixed with tests. S6 evidence gaps closed: `ingest.run` end to end, fresh import log, DB-path tests | APPROVE WITH FIXES → fixed in kb 70aca09 |

Both are in Tallyback (topic "D49 kb-intake split" in kb's ledger, task `tsk_01a1592a-1672…`): claim, evidence, main-session verification evidence, verdict (supported, final) and settlement (accept). `tallyback validate` shows no conflicts.

Main findings and fixes:
- **kb-intake B1 (blocker).** `parse_value` deleted spaces, so "2023 2024" became 20232024. The reviewer found 46 such cells. Fixed: a cell has a value only if it is exactly one number.
- **kb-intake S2–S4.**
  - stdout was polluted by PyMuPDF's layout recommendation; bad input gave tracebacks.
  - Manifest file names could point outside the package.
  - One bad table failed the whole package; it now becomes an `extractor_degraded` gap.
- **kb-intake S1/S5.** find_tables span semantics documented (rs = cs = 1, covered positions have no cell). Optional `cells[].page_no` added, and `docling_confidence` may be a number, ahead of 3b.
- **kb S1.** A missing package left `ingest_run` stuck at `pending`. Fixed.
- **kb S2.** Import validation now checks:
  - that the files list is complete;
  - that the source hash matches;
  - page numbering;
  - `checks.json`.

  Bad JSON gives a `PackageError` (400), not a 500.
- **kb S3.** A same-name package with different content is now refused.
- **kb S4.** Probe packages no longer go into the shared `KB_PACKAGES_DIR`.
- **kb S5.** The README now starts the service with `KB_PACKAGES_DIR`.

Not taken up (nits, listed for the record):
- the event loop blocks while a package is copied;
- `source.pdf` duplicates `working.pdf` for native PDFs;
- the doc_id pattern in the contract mirrors kb's naming;
- kb-intake's `--replace` path was kept.

### Re-verification after the fixes
| Item | Result |
|---|---|
| kb-intake pytest | 41 passed |
| 6 packages rebuilt; `kb-intake check` | 6/6 ok |
| `compare_baseline.py` | 0 differences in every item |
| kb: re-import, then all 6 through `ingest.run` (the real entry point) | all `loaded`; tree 8–13 s, build 2–9 s, load 2–4 s per document |
| kb JSONL | 24/24 identical (document except `path`) |
| node_concept / observation / fact / fact_version / fact_obs_link | identical (731 / 408 / 89 / 89 / 408) |
| 31-question eval after `loader --recreate` | 33 + 14 entries identical, summary identical |
| kb pytest | 391 passed |

## 4. Side checks
- **Pre-existing, not caused by the split: reloading Doris document by document changes rankings.** After the 6 documents went through `ingest.run` (each one deletes its rows and reloads them), the eval summary was still identical (21/21, 17/21, 8/10; held-out 3/3, 4/16). But 29 of the 47 per-question entries had a different top-5 order, for example q02 ranks 3–5. The JSONL fed to Doris was byte-identical. After `loader --recreate` the eval was identical again, entry by entry. The admin "trigger ingest" path behaves like this today, before D49 as well. Not fixed here; reported for Peter (§5).
- **The four synthetic-scan page-classification files have identical hashes, and that is expected.** Every page is one full-page image with 0 native characters. The artifact carries no doc_id. The 2025 en and zh scans use the same page sizes. Not a kb #29 bug.

## 5. Open items and things for Peter
- **Decision for Peter: Doris ranking depends on how the data was loaded.**
  - The baseline and this acceptance use `loader --recreate`; per-document reloads (admin "trigger ingest") change top-5 orders without changing hit counts.
  - Either the eval procedure should always reload with `--recreate`, or the load path should be made order-stable. I did not change either.
- **The demo service must be restarted** after kb#32 is merged; I did not touch the two uvicorn processes on port 8100. The new code reads packages through `document.path`, and the import endpoint needs `KB_PACKAGES_DIR`.
  - Doris and Postgres already hold the rebuilt data. Compared with before, the only difference is `document.path`.
  - Until the restart, the old process still renders pages from `data/raw` (those files are still there). It also still uses the old "upload PDF" endpoint.
- **`ui/dist` was rebuilt**, and the service serves it as static files, so the change is already visible: the V5 "detection" column is renamed to "package detection" and shows the package name.
- **`contract/intake-v1/SOURCE.md` records commit e57132a on branch `intake/step1-skeleton`.** After kb-intake#1 is merged, it should name the commit on `main`.
- **Not done in this round:**
  - zip upload in kb's import endpoint (it takes a package name under `KB_PACKAGES_DIR`; `kb-intake check` does read zips);
  - intake's SQLite state (`intake-data/intake.db`, §3) and the `intake.yaml` config file. Neither is needed by the CLI flow in stage A.
- **Original files:** `kb/data/raw/` is still on disk and was only read. kb no longer reads it, apart from `scripts/t1_download.py`, the downloader.
