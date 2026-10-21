# kb-intake

Scans files, labels their structure with [Docling](https://github.com/docling-project/docling) and writes
**intake packages**: versioned directories (contract v1) holding the text layer, word coordinates, page kinds,
layout regions with types, tables with cells, and heading candidates. Any consumer that reads the contract can use
the packages; kb-intake never calls a consumer.

- Docling is the core: regions, table structure and region types come from Docling (pinned version, pinned weights,
  run offline). PyMuPDF `find_tables` is a secondary extractor for cross-checks, and the fallback when Docling fails.
- No generative model is called. Every rule after Docling is deterministic, and the same file with the same
  configuration gives byte-identical packages (outside `manifest.json` → `run`).
- Runs natively on macOS arm64 and Linux x86_64. No Docker, no database, no external service.

Design: [docs/intake-design-v1.md](docs/intake-design-v1.md), [docs/layout-design-v1.md](docs/layout-design-v1.md),
[docs/docling-eval-v1.md](docs/docling-eval-v1.md).

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

Then download the model weights once (about 510 MB, the Hugging Face revisions pinned in
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

## Scan files

```bash
kb-intake scan report.pdf --decl report.yaml --out ./packages       # one file
kb-intake scan ./inbox   --decl inbox.yaml  --out ./packages       # every PDF in a directory
kb-intake check ./packages/<doc_id>@<sha12>                         # contract check of a package (dir or zip)
```

- One JSON line per file on stdout: `ok`, `skipped_same_sha` (already packaged, under any doc_id),
  `skipped_no_declaration` (its declaration lacks fields; listed with the missing fields) or `failed`. A failing
  file does not stop the scan; the exit code is 1 when any file failed.
- Files are recognised by content (`%PDF-`), not by extension; hidden files are ignored; `--recursive` also scans
  subdirectories; `--replace` rebuilds packages that already exist.
- `--out` defaults to `$INTAKE_PACKAGES_DIR`, else `./packages`.

### Web UI and HTTP API

```bash
kb-intake serve --data ./intake-data --port 8700        # http://127.0.0.1:8700/
```

One process: the API of intake-design §3.2 and a static web UI (no build step). Submit uploaded PDFs or a directory
on the same machine with a declaration; follow every step of every file (detection, text layer, find_tables,
Docling with cache hits and engine time, post-processing and checks, package); review page by page (the page image
with Docling's region boxes and types, table cell boxes coloured by `bbox_source`, find_tables grid and secondary
candidates, words and heading candidates, and the page's gaps); list, check and download packages as zip; edit the
configuration (validated before it is saved) and test the engine.

Jobs run one at a time in a background thread of the server (no queue: a second submission while one runs gets
409). State lives in the data directory: `intake.db` (SQLite: jobs, step events, results), `packages/`, `uploads/`,
`intake.yaml`, and `cache/` unless the configuration sets `cache_dir`.

- The server listens on 127.0.0.1; another address needs `--host` (there is no authentication, and the server says
  so when it starts on a non-loopback address).
- Directories on the server machine can only be listed and scanned inside `scan_roots` (configuration; empty by
  default, which leaves uploads only). Paths are resolved (symlinks, `..`) before the check; a path outside every
  root is refused, and a file of an allowed directory that links outside the roots is refused on its own.

### Declarations

For one file, a plain mapping:

```yaml
doc_id: acme_ar_2025_en          # file-name safe: ^[a-z0-9][a-z0-9._-]*$ (names the package)
issuer_id: acme
doc_type: annual_report          # [a-z][a-z0-9_]*
period_end: 2025-12-31
lang: en                         # ISO 639-1, optional: detected when omitted
source_url: https://example.com/ar2025.pdf
completeness: {full: true, sections: null}
# optional: translation_of, publisher, published_at
```

For a directory, defaults plus per-file overrides (keyed by the path relative to the directory). A field of an
override replaces the default as a whole; `doc_id` defaults to the slug of the file name.

```yaml
defaults:
  issuer_id: acme
  doc_type: annual_report
  source_url: https://example.com/reports
  completeness: {full: true, sections: null}
files:
  AR 2025.pdf:    {period_end: 2025-12-31}                    # doc_id "ar-2025", lang detected
  AR 2025 FR.pdf: {doc_id: acme_ar_2025_fr, period_end: 2025-12-31, lang: fr}
```

kb-intake only checks the format. A declared `lang` that differs from the detected one is recorded as a
`lang_mismatch` gap; a consumer with its own naming rules re-checks `doc_id` when it imports a package.
Language detection only recognises Chinese and English; for other languages declare `lang`.

### Configuration

Optional `intake.yaml` (`--config` or `$INTAKE_CONFIG`); every key has a default, unknown keys are refused, and
`INTAKE_MODELS_DIR` / `INTAKE_CACHE_DIR` override the file. See [intake.example.yaml](intake.example.yaml).

## The package (contract v1)

```
{doc_id}@{sha256[:12]}/
  manifest.json   contract version, declaration, detection, flow; every step with tool, version and parameters
                  (Docling version, model revisions, device, dependency versions, cache key); `run` holds the only
                  values that vary between runs (timestamps, host, durations, cache hit)
  source.pdf      the original file
  working.pdf     the PDF with a text layer (for a native PDF, the original)
  pages.jsonl     page_no, page_label, kind (native|scanned|mixed|blank), text, size, image regions
  layout.jsonl    words per page: [x0, y0, x1, y1, text, source, conf]
  regions.jsonl   layout regions: bbox, type (layout-design §2), type_source (model|native|fused), reading order
  tables.jsonl    tables: pages, bboxes, cells {r, c, rs, cs, text, value, nil, bbox, bbox_source, grid_bbox?,
                  is_header},
                  header_rows, markdown, extract_method (docling|find_tables|merged), role, checks, context_above
  headings.jsonl  heading candidates: text, level, numbering, sources, furniture (running headers, "(continued)")
  docling.json    the lossless DoclingDocument
  gaps.jsonl      what could not be read or did not match (table_text_unaccounted, table_cross_mismatch,
                  table_missed, table_header_suspect, extractor_degraded, lang_mismatch, ...; codes and their
                  fields are listed in contract/v1/gap.schema.json)
  checks.json     the package's own contract check
```

Coordinates are PDF points with a TOPLEFT origin, at full precision. A cell's `bbox` is the box of its text, clipped
to the cell; `bbox_source` says how it was found (`text`: the words centred in the cell, `words`: words overlapping
the cell, `cell`: no text found, the cell's box). An empty cell has `bbox` null. Schemas: [contract/v1/](contract/v1/).

When Docling cannot be used (weights missing, timeout, crash, a post-processing error) the file still gets a
package: the tables come from `find_tables` alone and the document has one `extractor_degraded` gap. Pages Docling
could not convert fall back the same way, page by page.

Heading levels: without a font profile they are inferred from the document (font x size distribution and
numbering). A profile maps fonts to levels for one document series (see
[tests/regression/profiles/](tests/regression/profiles/) for an example); list profile directories under
`headings.font_profiles`.

## Development

```bash
uv sync
uv run pytest -q                          # unit and pipeline tests (fake engine; no weights needed)
uv run lint-imports
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o constraints.txt   # after uv lock
```

CI checks that `constraints.txt` matches `uv.lock`. Upgrading Docling, PyMuPDF or any inference dependency goes
through the regression gate in [tests/regression/](tests/regression/) (24 reference tables, determinism).

Rules for contributors and agents: [AGENTS.md](AGENTS.md).
