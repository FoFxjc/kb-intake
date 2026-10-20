# kb-intake

Turns any file into a standard **intake package** that the knowledge base (FoFxjc/kb) can read. It only cares
about file formats, not about knowledge: receiving a file and its declaration, scanning and classifying pages,
taking text and word coordinates, finding table geometry, printed page labels. Everything that needs a type
configuration or a domain pack stays in kb. The two repositories never import each other (D49); they meet only
through packages that follow `contract/v1/`.

Design: [docs/intake-design-v1.md](docs/intake-design-v1.md) (main design), [docs/layout-design-v1.md](docs/layout-design-v1.md)
(regions), [docs/docling-eval-v1.md](docs/docling-eval-v1.md) (Docling evaluation).

## Status

| Flow | Input | State |
|---|---|---|
| A | native PDF | text layer, words, page kinds, page labels, tables with `find_tables` (step 2). Docling extraction comes in step 3b |
| B–F | scans, PPTX, DOCX, XLSX, images | not yet |

Web UI, Docker image and OCR are later steps (intake-design §6).

## Install and run

```bash
uv sync
export KB_PACKAGES_DIR=~/workspace/intake-packages     # where packages are written; kb imports from here

uv run kb-intake run report.pdf --decl report.yaml      # writes $KB_PACKAGES_DIR/<doc_id>@<sha256[:12]>/
uv run kb-intake check $KB_PACKAGES_DIR/<doc_id>@<sha12>   # validates a package directory or zip against contract v1
```

A declaration is a YAML mapping:

```yaml
doc_id: ocbc-group_ar_2025_en      # <issuer>_<type>_<year>_<lang>; kb recomputes and checks it at import
issuer_id: ocbc-group
doc_type: annual_report
period_end: "2025-12-31"
lang: en                           # en | zh
source_url: https://www.ocbc.com/group/investors/annual-report-and-agm
completeness: {full: true, sections: null}
# optional: translation_of, publisher, published_at
```

The same doc_id with the same file is idempotent; a different file for an existing doc_id is refused unless
`--replace` is given.

## The package (contract v1)

```
{doc_id}@{sha256[:12]}/
  manifest.json   contract version, declaration, detection, flow, tools and versions, file hashes; `run` holds
                  the only non-deterministic values (timestamps, host)
  source.pdf      the original file
  working.pdf     the PDF with a text layer (for a native PDF, the original)
  pages.jsonl     page_no, page_label, kind, text, text_source, width, height, image regions
  layout.jsonl    words per page: [x0, y0, x1, y1, text, source, conf], full precision, TOPLEFT origin, PDF points
  tables.jsonl    table candidates: bboxes, cells with r/c/rs/cs/text/value/nil/bbox, markdown, extract_method
  regions.jsonl   layout regions (empty until step 3b)
  headings.jsonl  heading candidates (empty until step 3b)
  gaps.jsonl      what could not be read or did not match
  checks.json     the package's own contract check
```

Schemas: [contract/v1/](contract/v1/). Rebuilding the same file with the same configuration gives byte-identical
files except `manifest.json` → `run`.

## Development

```bash
uv run pytest -q
uv run lint-imports          # kb-intake does not import kb
```

Rules for contributors and agents: [AGENTS.md](AGENTS.md).
