# kb-intake

The scan layer in front of a knowledge base. It reads a PDF, labels every region with
[Docling](https://github.com/docling-project/docling) (what it is, where it is), counts what it found, proposes a
**workflow** for extracting it, and — after a person approves the workflow, reviews the result and confirms it —
writes the original file plus JSONL to a delivery folder. Consumers read the delivery folder; kb-intake never calls
or notifies them.

- **Describes, does not interpret:** what is on a page, where, and the original text. Number parsing, units,
  continued-table merging, heading levels and table business types belong to consumers.
- **Text from the text layer:** Docling gives structure only (region types, boxes, table rows and columns); every
  text is read from the PDF's own text layer by box.
- **Two human gates:** approve the workflow, confirm the result. Nothing unconfirmed reaches the delivery folder.
- **Local and reproducible:** one `./setup.sh`, no Docker, no database server (state is one SQLite file); pinned
  versions and model weights, run offline; the same file, workflow and review operations give the same package.

Design: [docs/intake-design-v2.md](docs/intake-design-v2.md) (authoritative), [docs/layout-design-v1.md](docs/layout-design-v1.md) §2
(region types), [docs/intake-design-v1.md](docs/intake-design-v1.md) §2 and §9.3 (boundaries, stability rules).

## Install

Four steps, described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) (with the model file list, every setting, what
each check means, measured resource use and known limits):

1. Download the source zip of a release and unpack it.
2. Put the model files (three Hugging Face models at pinned commits, 521 MB) into `./models`, in any layout.
3. `cp .env.example .env` and fill it in (paths; optionally the LLM endpoint, model and key; a pip mirror, proxy, CA).
4. `./setup.sh`: makes a Python 3.12 environment (conda or venv), installs with `pip install -c constraints.txt .`,
   puts the models in place, then runs `kb-intake doctor --smoke`.

Then `./kb-intake serve` opens http://127.0.0.1:8700/. Nothing goes to the network after installation unless the
optional LLM is switched on: Hugging Face is offline for every command, and the weights are read from the local
directory only and refused when they do not match the lock.

## Use

```
uploaded -> labeled -> planned -> approved -> extracted -> in_review -> confirmed -> delivered
            label + stats       (gate 1)      extract       review      (gate 2)     package written
```

### Command line

```bash
kb-intake label   report.pdf --decl report.yaml       # label, count, suggest a workflow; prints the job id
kb-intake approve <job> --by NAME [--workflow wf.yaml] # gate 1: approve the suggested (or an edited) workflow
kb-intake extract <job>                               # extract every region by the workflow
kb-intake review  <job> --apply ops.yaml --by NAME    # reclassify or exclude regions
kb-intake confirm <job> --by NAME                     # gate 2: write <delivery_dir>/<doc_id>@<sha12>/
kb-intake check   <delivery_dir>/<doc_id>@<sha12>     # contract v2 check of a package (dir or zip)
kb-intake jobs | status <job>
```

A declaration (`--decl`) gives `doc_id`, `issuer_id`, `doc_type`, `period_end`, `source_url`, `completeness`
(required) and optionally `lang`, `translation_of`, `publisher`, `published_at`. Review operations are a YAML list:

```yaml
- {action: reclassify, region_id: "ocbc-group_ar_2025_en:p0106:r010", to: table, note: NPAs by Period Overdue}
- {action: exclude, region_id: "ocbc-group_ar_2025_en:p0005:r004"}
```

Extracted text cannot be edited: content comes from the file; a problem with it is a gap for the consumer.

stdout carries one JSON object per line; errors and progress go to stderr as JSON. Exit codes: 0 ok, 1 a step
failed, 2 bad input (unknown job, wrong state, invalid declaration, workflow or ops).

### Optional LLM help

Off by default. To use it, set `INTAKE_LLM_ENABLED=true`, `INTAKE_LLM_BASE_URL`, `INTAKE_LLM_MODEL` and
`INTAKE_LLM_API_KEY` in `.env` (any OpenAI-compatible endpoint; MiniMax when no URL is given). Data is then sent
to that host: the declaration and the start of every page's text for the summary (up to 24,000 characters per
document), and the image and text of each region set to `describe`. The outputs are kept apart in `llm.jsonl` and
marked low weight. `.env` is gitignored; never commit a key. Details: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) §3.

### Web UI and HTTP API

```bash
kb-intake serve                    # http://127.0.0.1:8700/
```

Six views: upload, labels and statistics (with the page viewer), workflow (gate 1), extraction progress, review
(page image with region and cell boxes, reclassify / exclude, gate 2), delivery (packages and their contract
check). The HTTP API under `/v1/` does the same, plus search, similar-region groups and a review queue; it is
frozen (`contract/api/openapi-v1.json`, described in `docs/api-v1.md`) and only gains endpoints and fields. To change
it on purpose, run `uv run python -m kb_intake.service.openapi_snapshot --write` and explain why in the PR. The
server listens on 127.0.0.1 only unless `--host` says otherwise, and reads server-side files only under `scan_roots`.

### Workflow

A YAML mapping of every region type to a handler (`text`, `table_structure`, `record`, `discard`, `unhandled`,
`ocr`), plus `exclude_pages`. The shipped template is
[src/kb_intake/workflows/default.yaml](src/kb_intake/workflows/default.yaml); the label step suggests it with notes
(scanned pages, suspected tables, low-confidence regions).

### Configuration

Optional `intake.yaml` (`--config` or `$INTAKE_CONFIG`): directories (`models_dir`, `cache_dir`, `work_dir`,
`delivery_dir`), `low_confidence`, Docling parameters, `scan_roots`. Every key has a default and unknown keys are
refused; `INTAKE_*_DIR` variables override the directories. See [intake.example.yaml](intake.example.yaml).

## The package (contract v2)

```
{doc_id}@{sha256[:12]}/
  source.pdf          the original, byte for byte
  manifest.json       declaration and detection, statistics, engines (Docling and model revisions, PyMuPDF, device,
                      parameters per stage), the approved workflow, who confirmed and when, every file's sha256;
                      `run` holds timings, cache hits and host
  pages.jsonl         one page per line: page_no, page_label (printed), size, kind, text, excluded
  regions.jsonl       one region per line: region_id, page_no, bbox, type, subtype, score, label_source
                      (model/user/native), layer, reading_order, text, font, status, handler, table_id
  tables.jsonl        one table per line: cells {r, c, rs, cs, text, bbox, bbox_source, is_header}, markdown,
                      extract_method (docling/find_tables), continued_hint {table_id, reason}, checks
  layout.jsonl        words per page: [x0, y0, x1, y1, text]
  gaps.jsonl          what could not be read or looks wrong (table_suspected, low_confidence, table_unmatched,
                      table_words_unassigned, unhandled, no_text_layer, lang_mismatch, extractor_degraded)
  workflow.json       the approved workflow, who approved it and when
  review.jsonl        every review operation and the confirmation
  raw/docling.json, raw/docling.extract.json   Docling's own output of both stages (audit only)
```

Coordinates are PDF points with a TOPLEFT origin, at full precision. A package appears in the delivery folder
complete or not at all (built under `.tmp-…` and renamed). Two deliveries of the same file, workflow and review
operations are byte-identical except `manifest.run` and the times of the human actions. Schemas:
[contract/v2/](contract/v2/).

When Docling cannot be used (timeout, crash, failed pages) the job still goes on: regions come from the text layer
and `find_tables`, and the stage records one `extractor_degraded` gap.

## Development

```bash
uv sync
uv run pytest -q                          # unit and end-to-end tests (fake engine; no weights needed)
uv run lint-imports
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o constraints.txt   # after uv lock
```

CI checks that `constraints.txt` matches `uv.lock`. Upgrading Docling, PyMuPDF or any inference dependency goes
through the regression gate in [tests/regression/](tests/regression/) (24 reference tables, determinism,
offline run).

Rules for contributors and agents: [AGENTS.md](AGENTS.md).
