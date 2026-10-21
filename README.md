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
- **Local and reproducible:** `uv tool install`, no Docker, no database server (state is one SQLite file); pinned
  versions and model weights, run offline; the same file, workflow and review operations give the same package.

Design: [docs/intake-design-v2.md](docs/intake-design-v2.md) (authoritative), [docs/layout-design-v1.md](docs/layout-design-v1.md) §2
(region types), [docs/intake-design-v1.md](docs/intake-design-v1.md) §2 and §9.3 (boundaries, stability rules).

## Install

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/) **0.10.2 or newer** (the version this is tested with;
`uv tool install --torch-backend` is needed on Linux).

`uv tool install` does not read the project's lock file, so the pinned versions of the inference libraries (torch,
transformers, pillow, numpy, torchvision, ...) come from `constraints.txt`, generated from `uv.lock`:

```bash
curl -fsSLO https://raw.githubusercontent.com/FoFxjc/kb-intake/main/constraints.txt

# macOS (arm64)
uv tool install --python 3.12 --constraints constraints.txt git+https://github.com/FoFxjc/kb-intake

# Linux (x86_64): the CPU build of torch (packages are built on CPU; avoids the multi-GB CUDA wheels)
uv tool install --python 3.12 --constraints constraints.txt --torch-backend cpu git+https://github.com/FoFxjc/kb-intake
```

Then download the model weights once (about 520 MB: Docling's layout model, TableFormer and the picture
classifier, the Hugging Face revisions pinned in
`src/kb_intake/models.lock.json`, every file checked against its sha256):

```bash
kb-intake models download          # into ~/.kb-intake/models, or $INTAKE_MODELS_DIR
kb-intake engines                  # versions, weights, dependency check; "ok": true when ready
```

Without network access on the target machine, run `models download --to DIR` where Hugging Face is reachable,
copy `DIR` over, point `INTAKE_MODELS_DIR` at it and run `kb-intake models verify`.

After this, nothing goes to the network: Hugging Face is switched to offline mode for every command except
`models download`, the weights are read from the local directory only, and files that do not match the lock are
refused.

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
