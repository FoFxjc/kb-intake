# Deploying kb-intake

For whoever installs and runs kb-intake on a machine: requirements, what gets installed, three ways to install it,
configuration, operations and known limits. What kb-intake does and the package format are in the
[README](../README.md); the HTTP API is in [api-v1.md](api-v1.md).

Every number below was measured, on the machine and with the commit named next to it. Anything not measured says so.

## 1. Runtime environment

| | |
|---|---|
| Python | 3.12 exactly (`requires-python = "==3.12.*"`) |
| uv | 0.10.2 or newer (the version tested; `--torch-backend` is needed on Linux) |
| Platforms | macOS arm64 (tested: install, offline install, full runs), Linux x86_64 (CI runs the test suite on ubuntu-latest; a full Docling run on Linux has **not** been measured) |
| Node | not needed to run; only to change the web UI (§4.3) |
| Network | none after installation, unless the optional LLM is switched on (§5.2) |

### Measured on OCBC Group Annual Report 2025 (276 pages)

Apple M3 Pro, 11 cores, 18 GB RAM, macOS; commit b3da9d7; Docling on CPU with `num_threads: 4`; fresh cache; network
blocked (`sandbox-exec`, no outbound IP). Wall time and peak resident memory per CLI step (`/usr/bin/time -l`; Docling
runs in a child process, which is included):

| Step | Wall time | CPU (user + sys) | Peak memory |
|---|---|---|---|
| label (layout + picture classifier) | 119.5 s | 256.6 s | 3.0 GB |
| extract (layout + TableFormer) | 322.0 s | 595.5 s | 3.1 GB |
| confirm (write and check the package) | 6.3 s | | 0.1 GB |
| **total** | 447.8 s (7.5 min) | | |

With the Docling cache warm (the same file again), label takes about 21 s and extract about 4 s (PR #5 measurements).
UOB 2025 (fresh cache, PR #5): label 127.9 s, extract 223.9 s.

Disk, same run:

| What | Size |
|---|---|
| Model weights (`models_dir`) | 521 MB |
| Installed tool (Python + dependencies, macOS arm64) | 1.1 GB |
| Docling cache for this document (`cache_dir`) | 24 MB |
| Job work files (`work_dir`, one job) | 56 MB |
| Delivered package (`delivery_dir`) | 55 MB (the PDF itself is 16 MB) |

### Recommended

| | Minimum | Recommended | Why |
|---|---|---|---|
| CPU | 4 cores | 8 or more | Docling uses `num_threads` (default 4) plus a few housekeeping threads; above that it does not get faster |
| Memory | 8 GB | 16 GB | about 3.1 GB peak for a 276-page report, plus the server and the browser |
| Disk | 3 GB + documents | 10 GB + 2× the documents | weights, tool, then per document: cache + work files + package (each several times the PDF) |

These are derived from the one measured document above; a much longer or image-heavy PDF will need more.

## 2. Python dependencies

Direct dependencies (`pyproject.toml`), all pinned to exact versions:

| Package | Version | License | Used for |
|---|---|---|---|
| docling | 2.140.0 | MIT | layout, table structure, picture classification |
| docling-core | 2.103.0 | MIT | Docling's document model |
| docling-ibm-models | 4.0.3 | MIT | TableFormer and the layout model code |
| docling-parse | 7.22.3 | MIT | PDF parsing inside Docling |
| pymupdf | 1.28.2 | **AGPL-3.0** or Artifex commercial | text layer, page images, `find_tables` |
| fastapi | 0.128.0 | MIT | HTTP API |
| uvicorn | 0.40.0 | BSD-3-Clause | HTTP server |
| python-multipart | 0.0.20 | Apache-2.0 | file upload |
| huggingface-hub | 1.33.0 | Apache-2.0 | `models download` only |
| jsonschema | 4.23.0 | MIT | contract check |
| pyyaml | 6.0.3 | MIT | configuration, workflows |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause | version checks |

The inference libraries come in through Docling: torch 2.14.1, torchvision 0.29.1, transformers 5.20.2, numpy 2.5.4,
pillow 12.3.0, safetensors 0.8.0, opencv-python-headless 5.0.0.93, scipy 1.18.1, pandas 3.0.6, among others.

**PyMuPDF is AGPL-3.0.** Running kb-intake as a network service for others, or distributing it, brings AGPL
obligations unless you hold an Artifex commercial license. Check this before deploying beyond your own team.

### `constraints.txt` and `uv.lock`

`uv.lock` pins every package of the development environment. `uv tool install` does not read it, so the
installation pins come from `constraints.txt`, which is generated from the lock:

```bash
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o constraints.txt
```

Run this after every `uv lock`; CI fails when `constraints.txt` does not match `uv.lock`. The file also ships inside
the wheel: `kb-intake engines` compares the installed versions with it and reports `"ok": false`, with the
differences, when they do not match.

On Linux, add `--torch-backend cpu` to every `uv tool install` / `uv pip install`. Without it uv picks the CUDA build
of torch (several GB), which kb-intake does not use: formal packages are made on CPU.

## 3. Model dependencies

Three models, pinned to a Hugging Face commit in `src/kb_intake/models.lock.json` (shipped inside the package).
`kb-intake models download` fetches exactly these revisions and checks the sha256 of every file; at run time only
the local directory is read and files that do not match the lock are refused.

| Role | Hugging Face repo | Revision | Weight files | Size | License |
|---|---|---|---|---|---|
| Layout (region types and boxes) | `docling-project/docling-layout-heron` | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | `model.safetensors` | 172 MB | Apache-2.0 |
| Table structure (TableFormer, rows and columns) | `docling-project/docling-models` (tag v2.3.0) | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | `model_artifacts/tableformer/accurate/tableformer_accurate.safetensors`, `…/fast/tableformer_fast.safetensors` | 213 MB + 145 MB | CDLA-Permissive-2.0 |
| Picture classifier (chart, logo, icon, signature, …) | `docling-project/DocumentFigureClassifier-v2.5` | `f859dfbff5c9916cd996942d4b0db7fa25808220` | `model.safetensors` | 16 MB | MIT |

Each repo also has its `config.json`, `preprocessor_config.json` (or `tm_config.json`) and README; 521 MB in all.
The extract stage uses the accurate TableFormer (`docling.table_mode: accurate`); `fast` is there if configured.

Directory: `~/.kb-intake/models` by default; `models_dir` in `intake.yaml` or `INTAKE_MODELS_DIR` overrides it.
Layout: one folder per repo named `<org>--<name>`, plus a copy of `models.lock.json`.

```bash
kb-intake models download [--to DIR]   # needs huggingface.co
kb-intake models verify [--dir DIR]    # offline: every file against the lock
kb-intake engines                      # versions, weights, dependencies; "ok": true when ready
```

## 4. Installation

### 4.1 A machine with network access

```bash
curl -fsSLO https://raw.githubusercontent.com/FoFxjc/kb-intake/main/constraints.txt

# macOS arm64
uv tool install --python 3.12 --constraints constraints.txt git+https://github.com/FoFxjc/kb-intake
# Linux x86_64
uv tool install --python 3.12 --constraints constraints.txt --torch-backend cpu git+https://github.com/FoFxjc/kb-intake

kb-intake models download
kb-intake engines                      # "ok": true
kb-intake serve                        # http://127.0.0.1:8700/
```

The web UI is prebuilt inside the wheel; Node is not needed. How long the first install takes on a clean machine
has not been measured (the tested machine already had uv's package cache).

### 4.2 An isolated network (no internet)

Prepare a bundle on a machine **with** network access and the **same platform** (OS, CPU architecture) as the
target; wheels are platform-specific. Copy it over, install without network.

**On the connected machine**, from a checkout of the release:

```bash
B=kb-intake-bundle
mkdir -p $B/wheelhouse $B/bin
uv build --wheel --out-dir $B/wheelhouse                     # kb-intake itself (UI, contract and pins inside)
cp constraints.txt $B/
uvx --python 3.12 --from pip==25.2 pip wheel --no-deps -r constraints.txt -w $B/wheelhouse
#   Linux: add  --extra-index-url https://download.pytorch.org/whl/cpu  so torch is the CPU build
UV_PYTHON_INSTALL_DIR=$B/python uv python install 3.12        # a relocatable Python 3.12 for the target
cp "$(command -v uv)" $B/bin/                                 # uv is one static binary
kb-intake models download --to $B/models                      # any installed kb-intake will do
tar -czf $B.tar.gz $B
```

Measured on macOS arm64: wheelhouse 327 MB (108 wheels), models 521 MB, Python 50 MB, uv 42 MB.

**On the isolated machine**:

```bash
tar -xzf kb-intake-bundle.tar.gz && B=$PWD/kb-intake-bundle
export UV_PYTHON_INSTALL_DIR=$B/python UV_PYTHON_DOWNLOADS=never UV_OFFLINE=1
$B/bin/uv tool install --python 3.12 --no-index --find-links $B/wheelhouse \
    --constraints $B/constraints.txt kb-intake
export INTAKE_MODELS_DIR=$B/models                             # or copy it to ~/.kb-intake/models
kb-intake models verify
kb-intake engines                                              # "ok": true
```

Keep `$B/python` where it is: the installed tool's environment points at that interpreter. To put it elsewhere,
choose the final place for `UV_PYTHON_INSTALL_DIR` before installing.

**Tested** (2026-10-21, macOS arm64, commit b3da9d7): the install and everything after it ran inside
`sandbox-exec` with all outbound IP connections denied, with an empty uv cache and a new tool directory:

| Step | Result |
|---|---|
| Bundle | 849 MB as `.tar.gz`; unpacked into a new directory |
| `uv tool install` (no index, no network, empty uv cache) | 4.7 s; tool directory 1.1 GB |
| `models verify`, `engines` | `"ok": true` |
| OCBC 2025, fresh kb-intake cache: label / approve / extract / review (p106) / confirm | 157.3 s / 0.1 s / 303.4 s / 0.6 s / 6.2 s |
| `kb-intake check` of the package | ok, no errors |
| The 24 reference tables | 24/24, all 1,906 numeric cells identical |
| Same package as a development install with a warm cache (`same_packages.py`) | byte-identical except the exempt fields |
| `serve` | `/v1/health` ok, `/ui/` served, the delivered job listed |

Not tested: Linux, and a target machine that really is another computer (the walkthrough used the same Mac, with
the network denied to every step).

### 4.3 Development (with Node, to change the web UI)

```bash
git clone https://github.com/FoFxjc/kb-intake && cd kb-intake
uv sync --locked                       # Python 3.12 and the pinned environment in .venv
uv run pytest -q                       # fake engine; no weights needed
uv run lint-imports
cd web && nvm use && npm ci            # Node from web/.nvmrc
npm run build                          # writes src/kb_intake/service/static/ (committed)
npx playwright install chromium && npx playwright test   # starts its own fake-engine servers
```

Commit `src/kb_intake/service/static/` with the source change: CI rebuilds the UI and fails when the committed build
differs. The real-document browser walkthrough is `web/e2e/ocbc.spec.ts` (needs the models; not in CI).

## 5. Configuration

### 5.1 `intake.yaml`

Optional; found by `--config FILE`, else `$INTAKE_CONFIG`. Every key is optional and unknown keys are refused.
Precedence: command-line flag > `INTAKE_*` environment variable > `intake.yaml` > default. A template is
[intake.example.yaml](../intake.example.yaml).

| Key | Default | Meaning |
|---|---|---|
| `models_dir` | `~/.kb-intake/models` | pinned weights (`INTAKE_MODELS_DIR`) |
| `cache_dir` | `~/.kb-intake/cache` | Docling output keyed by source sha256 + versions + model revisions + parameters; LLM answers (`INTAKE_CACHE_DIR`) |
| `work_dir` | `~/.kb-intake/work` | jobs before delivery and `state.db` (`INTAKE_WORK_DIR`) |
| `delivery_dir` | `~/.kb-intake/delivery` | confirmed packages only (`INTAKE_DELIVERY_DIR`) |
| `low_confidence` | `0.5` | a layout score or picture-classifier top score below this gives a `low_confidence` gap |
| `docling.table_mode` | `accurate` | TableFormer mode in the extract stage: `accurate` or `fast` |
| `docling.device` | `cpu` | `cpu` for formal packages; `mps` only for debugging (recorded in the manifest) |
| `docling.num_threads` | `4` | Docling's threads |
| `docling.document_timeout` | `1800` | Docling's own time budget per document, seconds |
| `docling.wall_timeout` | `2100` | hard limit: the conversion process is killed after this many seconds |
| `scan_roots` | `[]` | `serve` only: directories the UI and API may list and read; empty = uploads only |
| `llm.enabled` | `false` | the optional LLM (§5.2) |
| `llm.provider` | `openai_compatible` | the only provider |
| `llm.base_url` | `https://api.minimax.io/v1` | must be `https`, except for a localhost endpoint |
| `llm.model` | `MiniMax-M3` | |
| `llm.api_key_env` | `INTAKE_LLM_API_KEY` | the name of the environment variable that holds the key; the key itself is never in this file |
| `llm.remote` | `true` | shown in the UI and the manifest; `false` is accepted only for a localhost `base_url` |
| `llm.features` | `{summary: true, describe: true, chart_table: false}` | which LLM uses are allowed; a job's workflow can switch `describe` and `chart_table` off |

Other environment variables: `INTAKE_CONFIG`. The CLI (and so `serve`) also reads `INTAKE_*` lines of a `.env` file in
the working directory for variables the environment does not set; `.env` is gitignored, `.env.example` is the
template.

### 5.2 The optional LLM (MiniMax)

Off by default. It is on only when **both** `llm.enabled: true` is set **and** the key's environment variable is set
in the environment of the `kb-intake` process:

```bash
export INTAKE_LLM_API_KEY=...          # from your secret store; or a .env line in the working directory
kb-intake serve --config intake.yaml   # with llm: {enabled: true}
curl -s http://127.0.0.1:8700/v1/health   # "llm": {"enabled": true, ...}; the key is never shown
```

With it on, **this data leaves the machine** for `llm.base_url`:

| Use | When | What is sent |
|---|---|---|
| Document summary (`features.summary`) | right after labelling, for every job | the declaration (doc_id, issuer_id, doc_type, period_end, lang) and the start of every page's text layer, up to 24,000 characters in all |
| Descriptions (`features.describe`) | during extraction, for regions whose approved handler is `describe` (charts, diagrams, pictures) | each region's image (rendered at 144 dpi, at most 2,000 px) and the text found in it |
| Chart to table (`features.chart_table`, experimental) | as descriptions, bar / line / pie charts only | the same image and text |

The upload form and the Workflow page say so before anything is sent. Answers are kept apart in `llm.jsonl`, marked
low weight, and cached by provider + model + prompt version + input hash (re-runs make no calls). A failed call is
an `llm_failed` gap; the flow goes on. The key is sent only in the `Authorization` header to `llm.base_url`;
redirects are refused, so it never goes to another host. It is not written to logs, the cache, the work files or the
package.

## 6. Operations

### 6.1 Directories

```
work_dir/
  state.db                 jobs, workflow versions, review operations (SQLite)
  uploads/                 files being received by the server (moved into the job when complete)
  jobs/<job_id>/
    source.pdf
    label/                 docling.json, regions, pages, layout (words), gaps, stats, run.json
    extract/               docling.extract.json, regions, tables, gaps, run.json
    review/                gaps added by review, resolved items, absorbed regions
    llm/                   llm.jsonl, gaps.jsonl, summary.json, run.json (only when the LLM is used)
cache_dir/
  docling/                 Docling output per (source, versions, model revisions, parameters)
  llm/                     LLM answers per (provider, model, prompt version, input hash)
delivery_dir/
  <doc_id>@<sha256[:12]>/  one confirmed package; nothing else is written here
```

Consumers read `delivery_dir` only. A package appears complete or not at all: it is built under
`delivery_dir/.tmp-<name>-<random>/`, checked against contract v2 and renamed. Delivered packages are never
replaced; a file that was delivered cannot start a new job.

### 6.2 Clearing the cache

`cache_dir` only saves time. It is safe to delete it, or any folder below it, while no job is running; the next
run recomputes (OCBC: about 7.5 minutes instead of seconds). Deleting `cache_dir/llm` makes the next LLM use
call the service again (and possibly answer differently). Do not delete `work_dir` while jobs are open: it holds
their state. Delivered jobs' work folders can be archived or removed; their packages are in `delivery_dir`.

### 6.3 Restart and recovery

One step runs at a time per server. When `serve` starts it:
- clears the running step of any job whose owning process is gone (the job keeps its status and `last_error` says
  "interrupted"); a step owned by a live process, for example a CLI run, is left alone;
- marks LLM summaries left `pending` as failed (reason `interrupted`).

Then **Retry** in the UI (`POST /v1/jobs/{id}/retry`, or the same CLI command again) runs the step again:
`uploaded` → label, `approved` → extract, `confirmed` → deliver. Work files are replaced atomically per file, and
delivery is idempotent (a package already renamed into place by an interrupted delivery is recognised and recorded).

If the server process is killed hard (SIGKILL) during a Docling run, the Docling child process may keep running until
it finishes or hits `wall_timeout`; stop it by hand before retrying to avoid two conversions at once.

### 6.4 Upgrading Docling, its dependencies or the models

Pinned versions and model revisions are part of the output contract: the same file must keep giving the same
package. An upgrade is a change like any other and must pass the regression gate before it is merged:

1. Change the pins (`pyproject.toml`, then `uv lock`) or `src/kb_intake/models.lock.json` (revision and sha256 of
   every file).
2. Regenerate `constraints.txt` (§2); `uv sync --locked`; `kb-intake models download`.
3. Run the gate in [tests/regression/](../tests/regression/): the 24 reference tables of OCBC 2025
   (`compare_snapshot.py`: every number identical), determinism (`same_packages.py`: two runs byte-identical except
   the exempt fields) and the offline run (`test_offline.py`).
4. Rebuild the OCBC and UOB packages and compare them with the previous release's; explain every difference in the PR.
5. Old cache entries stay valid for the old versions only (the key includes versions and revisions); delete them to
   save space.

### 6.5 Exposing the server

`serve` listens on 127.0.0.1 and has **no authentication**. It refuses requests whose `Host` header is not
localhost, which protects against DNS rebinding but is not access control. Do not run it with `--host 0.0.0.0` on a
shared network: a client that sends `Host: localhost` gets the whole API. For use by several people, put it behind a
reverse proxy that does authentication and TLS and forwards to 127.0.0.1. Uploads have no size limit; limit the body
size in the proxy.

## 7. Known limitations

- **No OCR.** Scanned pages and text inside images are not read: such pages get a `no_text_layer` gap, image regions
  keep no text. The `ocr` handler exists in the workflow vocabulary but is not implemented.
- **Cell text placement.** Text is read from the PDF text layer into Docling's cell boxes. Where Docling's table
  structure itself is wrong the text follows it; known cases on OCBC 2025: p131 "Changes in non-controlling
  interests" split over two rows, p183 the header "Liabilities for remaining coverage" merged into the "2024" cell.
  Numbers are not affected on the 24 reference tables (1,906 numeric cells identical).
- **Suspected tables from the picture classifier** (a table drawn as an image) need OCR to be read; reclassifying them
  as a table extracts nothing useful.
- **First install time on a clean machine** has not been measured; neither has a full Docling run on Linux.
- **The server is single-user**: no authentication, no upload size limit (§6.5).
- **Only one step runs at a time** per server; a 276-page report blocks others for about 7.5 minutes.
- **Killing the server hard during Docling** can leave the conversion process running (§6.3).
- **LLM cache key** is provider + model + prompt version + input hash (as designed); pointing the same model name at
  another endpoint reuses the cached answers.
- **An approved `describe` without the LLM** (switched off or key missing at extraction) extracts those regions as text,
  with no gap and `manifest.llm.used = false`.
- **A confirmed job with a summary still pending** is delivered without the summary.
- **Only PDF** files are accepted.
