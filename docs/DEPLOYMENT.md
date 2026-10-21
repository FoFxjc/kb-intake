# Deploying kb-intake

For whoever installs and runs kb-intake: recommended dependencies, online installation (including a company network
with a package mirror, a proxy or its own CA), a self-check after installing, configuration, operations and known
limits. What kb-intake does and the package format are in the [README](../README.md); the HTTP API is in
[api-v1.md](api-v1.md).

Every number below was measured, on the machine and commit named next to it. Anything not tested says so.

> **License notice: PyMuPDF is AGPL-3.0.** kb-intake depends on PyMuPDF (text layer, page images, `find_tables`),
> which is licensed under the GNU AGPL-3.0 or, alternatively, a commercial license from Artifex. Offering kb-intake
> to others over a network, or distributing it, brings AGPL obligations unless your organisation holds an Artifex
> commercial license. Have this checked before deploying beyond your own team.

## 1. Recommended dependencies

### 1.1 Runtime

| | |
|---|---|
| Python | **3.12** exactly (`requires-python = "==3.12.*"`); uv can provide it (`--python 3.12`) |
| uv | **0.10.2 or newer** (the version tested) |
| Platforms | macOS arm64: tested (installation and full runs). Linux x86_64: CI runs the test suite on ubuntu-latest, and the Linux dependency resolution was checked (§3.6); a full Docling run on Linux has **not** been measured |
| Node | not needed to run kb-intake; only to change the web UI (§4) |
| Network | needed to install and to download the models; none afterwards, unless the optional LLM is switched on (§6.2) |

### 1.2 Python packages

Direct dependencies (`pyproject.toml`), each pinned to one exact version:

| Package | Version | License | Used for |
|---|---|---|---|
| **pymupdf** | 1.28.2 | **AGPL-3.0** or Artifex commercial | text layer, page images, `find_tables` |
| docling | 2.140.0 | MIT | layout, table structure, picture classification |
| docling-core | 2.103.0 | MIT | Docling's document model |
| docling-ibm-models | 4.0.3 | MIT | TableFormer and the layout model code |
| docling-parse | 7.22.3 | MIT | PDF parsing inside Docling |
| fastapi | 0.128.0 | MIT | HTTP API |
| uvicorn | 0.40.0 | BSD-3-Clause | HTTP server |
| python-multipart | 0.0.20 | Apache-2.0 | file upload |
| huggingface-hub | 1.33.0 | Apache-2.0 | `kb-intake models download` only |
| jsonschema | 4.23.0 | MIT | contract check |
| pyyaml | 6.0.3 | MIT | configuration, workflows |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause | version checks |

The inference libraries come in through Docling, among them torch 2.14.1, torchvision 0.29.1, transformers 5.20.2,
numpy 2.5.4, pillow 12.3.0, safetensors 0.8.0, opencv-python-headless 5.0.0.93, scipy 1.18.1 and pandas 3.0.6.

### 1.3 `constraints.txt`: always install with it

`pyproject.toml` pins only the direct dependencies. The about 130 packages under them (torch, transformers, numpy,
pillow, …) would otherwise be resolved to whatever is newest on the day of installation. A different torch,
transformers or pillow can change model output, so the same PDF would no longer give the same package. Those
versions are part of the output contract and are pinned only through `constraints.txt`.

- `constraints.txt` is generated from `uv.lock` (the full, tested environment) with
  `uv export --no-dev --no-hashes --no-emit-project --format requirements-txt -o constraints.txt`. CI fails if it
  does not match `uv.lock`.
- `uv tool install` does not read `uv.lock`, so `--constraints constraints.txt` is what makes an installation get
  the tested versions. Take the file from the same release you install.
- The file also ships inside the wheel. `kb-intake engines` compares the installed versions with it and reports
  `"ok": false`, listing the differences, when they do not match (§5).

## 2. Models

Three models, pinned to a Hugging Face commit in `src/kb_intake/models.lock.json` (shipped inside the package).
`kb-intake models download` fetches exactly these revisions and checks every file's sha256. At run time only the
local directory is read, Hugging Face is switched to offline mode, and files that do not match the lock are refused.

| Role | Hugging Face repo | Revision | Weight files | Size | License |
|---|---|---|---|---|---|
| Layout (region types and boxes) | `docling-project/docling-layout-heron` | `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8` | `model.safetensors` | 172 MB | Apache-2.0 |
| Table structure (TableFormer: rows, columns) | `docling-project/docling-models` (tag v2.3.0) | `fc0f2d45e2218ea24bce5045f58a389aed16dc23` | `model_artifacts/tableformer/accurate/tableformer_accurate.safetensors`, `…/fast/tableformer_fast.safetensors` | 213 MB + 145 MB | CDLA-Permissive-2.0 |
| Picture classifier (chart, logo, icon, signature, …) | `docling-project/DocumentFigureClassifier-v2.5` | `f859dfbff5c9916cd996942d4b0db7fa25808220` | `model.safetensors` | 16 MB | MIT |

Each repo also has its `config.json`, `preprocessor_config.json` or `tm_config.json`, and a README: 521 MB in all.
The extract stage uses the accurate TableFormer (`docling.table_mode: accurate`).

The directory is `~/.kb-intake/models` by default; `models_dir` in `intake.yaml` or `INTAKE_MODELS_DIR` overrides it.
It holds one folder per repo, named `<org>--<name>`, plus a copy of `models.lock.json`.

If huggingface.co cannot be reached, see §3.5.

## 3. Installation (online)

### 3.1 Standard

```bash
# the constraints of the release you install (here: main)
curl -fsSLO https://raw.githubusercontent.com/FoFxjc/kb-intake/main/constraints.txt

# macOS arm64
uv tool install --python 3.12 --constraints constraints.txt git+https://github.com/FoFxjc/kb-intake
# Linux x86_64: the CPU build of torch (see §3.6)
uv tool install --python 3.12 --constraints constraints.txt --torch-backend cpu git+https://github.com/FoFxjc/kb-intake

kb-intake models download              # 521 MB from huggingface.co
```

Then run the self-check (§5). The web UI is prebuilt inside the package; Node is not needed. How long the first
installation takes on a clean machine has **not** been measured: the tested machine already had uv's package cache.

### 3.2 A company package mirror (PyPI proxy)

Point uv at the mirror instead of pypi.org. With no authentication, one environment variable is enough:

```bash
export UV_DEFAULT_INDEX=https://pypi.corp.example/simple
```

When the mirror needs credentials, name the index in uv's configuration file and keep the credentials in
environment variables. On macOS and Linux the user's file is `~/.config/uv/uv.toml`; `UV_CONFIG_FILE` can point
elsewhere.

```toml
# ~/.config/uv/uv.toml
[[index]]
name = "corp"
url = "https://pypi.corp.example/simple"
default = true                       # replaces pypi.org
```

```bash
export UV_INDEX_CORP_USERNAME=svc-kb-intake
export UV_INDEX_CORP_PASSWORD=...    # from your secret store
```

The variable names follow the index name (`corp` → `UV_INDEX_CORP_…`). Credentials embedded in the URL
(`https://user:token@pypi.corp.example/simple`) also work, but they end up in shell history.

`git+https://github.com/...` needs access to GitHub. If GitHub is not reachable, install from a wheel of the
release published on the mirror (`uv tool install ... kb-intake==0.3.0`), or from a local wheel file built with
`uv build --wheel` in a checkout.

### 3.3 An HTTP proxy

```bash
export HTTPS_PROXY=http://proxy.corp.example:8080
export HTTP_PROXY=http://proxy.corp.example:8080
export NO_PROXY=localhost,127.0.0.1,.corp.example     # internal hosts reached directly, e.g. the mirror
```

uv, `kb-intake models download` (huggingface_hub, which uses httpx) and the optional LLM client (Python's urllib)
all use `HTTPS_PROXY`; each was checked going through a local CONNECT proxy. `NO_PROXY` is the usual convention of
these clients and was not tested. If the proxy needs a login, put it in the URL:
`http://user:password@proxy:8080`.

### 3.4 A company CA (TLS inspection)

If the company re-signs TLS traffic with its own root CA, every client must trust that CA. Build a bundle that
contains the **public roots plus the company CA**. A file with only the company CA breaks every site that is not
re-signed. For example:

```bash
python3 -c "import certifi; print(certifi.where())"            # or the OS bundle, e.g. /etc/ssl/certs/ca-certificates.crt
cat "$(python3 -c 'import certifi; print(certifi.where())')" corp-root-ca.pem > ~/corp-bundle.pem
```

| Client | Used for | Setting |
|---|---|---|
| uv | installation | `SSL_CERT_FILE=~/corp-bundle.pem`, or `UV_NATIVE_TLS=1` (or `--native-tls`) to use the OS certificate store once the company CA is installed there (not tested) |
| huggingface_hub (httpx) | `kb-intake models download` | `SSL_CERT_FILE=~/corp-bundle.pem` |
| Python urllib | the optional LLM client | `SSL_CERT_FILE=~/corp-bundle.pem` (or `SSL_CERT_DIR`) |
| requests | not used by kb-intake itself; installed as a dependency of others | `REQUESTS_CA_BUNDLE=~/corp-bundle.pem` (requests ignores `SSL_CERT_FILE`) |

Set both variables to be safe:

```bash
export SSL_CERT_FILE=~/corp-bundle.pem
export REQUESTS_CA_BUNDLE=~/corp-bundle.pem
```

### 3.5 Hugging Face cannot be reached

`kb-intake models download` honours `HF_ENDPOINT`. With an internal Hugging Face mirror:

```bash
export HF_ENDPOINT=https://hf-mirror.corp.example
kb-intake models download
```

The mirror must serve the pinned commits (the revisions in §2), not only `main`. Every file is checked against the
lock, so a mirror that serves anything else is refused.

Without a mirror, run `kb-intake models download --to DIR` on any machine that can reach Hugging Face, copy `DIR`
over, and point `INTAKE_MODELS_DIR` at it (or copy it to `~/.kb-intake/models`). Then check it:

```bash
INTAKE_MODELS_DIR=/path/to/DIR kb-intake models verify      # "ok": true, see §5
```

### 3.6 Linux: the CPU build of torch

On Linux, the torch wheels on PyPI are the CUDA builds. They pull in triton and 15 `nvidia-*` packages (several
GB) that kb-intake does not use: packages are made on CPU. PyTorch publishes CPU builds on its own index,
`https://download.pytorch.org/whl/cpu` (versions with a `+cpu` suffix). Resolving kb-intake 0.3.0 for Linux x86_64
with uv 0.10.2 (`uv pip compile --python-platform x86_64-manylinux_2_28`, resolution only):

| Set-up | torch resolved |
|---|---|
| PyPI only | `torch==2.14.1` plus `triton` and 15 `nvidia-*` packages |
| `--torch-backend cpu` | `torch==2.14.1+cpu`, `torchvision==0.29.1+cpu`, no nvidia package |
| CPU wheels in a directory, `--find-links`, no PyTorch index | `torch==2.14.1+cpu`, `torchvision==0.29.1+cpu`, no nvidia package |

Choose whichever works in your network.

1. **The PyTorch index is reachable** (directly or through the proxy). Use `--torch-backend cpu` (or
   `UV_TORCH_BACKEND=cpu`). uv then takes torch and torchvision from `download.pytorch.org/whl/cpu` and everything
   else from your default index or mirror. The PyTorch URL is built into uv and cannot be redirected to a mirror.
2. **The PyTorch index is not reachable.** On any machine that can reach `download.pytorch.org`, download the two
   CPU wheels for Linux x86_64 and Python 3.12. If your mirror proxies the PyTorch CPU index (Artifactory and Nexus
   can proxy it as a PyPI remote), pass that URL to `--index-url` instead. Then install with the wheels as an extra
   source, pinned to the CPU build:

   ```bash
   # where download.pytorch.org is reachable (any OS)
   uvx --python 3.12 --from pip==25.2 pip download "torch==2.14.1+cpu" "torchvision==0.29.1+cpu" \
       --index-url https://download.pytorch.org/whl/cpu --only-binary=:all: --no-deps \
       --platform manylinux_2_28_x86_64 --python-version 3.12 -d torch-cpu      # 198 MB

   # on the Linux machine, with torch-cpu/ copied over
   printf 'torch==2.14.1+cpu\ntorchvision==0.29.1+cpu\n' > cpu-pins.txt
   uv tool install --python 3.12 --constraints constraints.txt --constraints cpu-pins.txt \
       --find-links ./torch-cpu git+https://github.com/FoFxjc/kb-intake
   ```

   The `+cpu` pins are a guard: if the wheel directory is missing, the installation fails instead of quietly
   taking the CUDA build. Take the version numbers from `constraints.txt` of the release you install.

Afterwards, `kb-intake engines` must report `"ok": true`. A CPU torch satisfies the `torch==2.14.1` constraint.

## 4. Development installation (with Node, to change the web UI)

```bash
git clone https://github.com/FoFxjc/kb-intake && cd kb-intake
uv sync --locked                       # Python 3.12 and the exact environment of uv.lock in .venv
uv run pytest -q                       # fake engine; no weights needed
uv run lint-imports
cd web && nvm use && npm ci            # Node from web/.nvmrc
npm run build                          # writes src/kb_intake/service/static/ (committed)
npx playwright install chromium && npx playwright test   # starts its own fake-engine servers
```

Commit `src/kb_intake/service/static/` together with the source change. CI rebuilds the UI and fails when the
committed build differs. The browser walkthrough on a real document is `web/e2e/ocbc.spec.ts`; it needs the models
and does not run in CI. On Linux, `uv sync --locked` installs exactly what `uv.lock` records, which is the PyPI
(CUDA) build of torch. CI does the same.

## 5. Self-check after installing

Every command prints one JSON object per line on stdout. Exit code 0 means ok.

**1. The models.**

```bash
kb-intake models verify
```

Expected: `{"ok": true, "models_dir": "...", "models": [three entries with repo_id, revision, role]}`. A missing or
altered file gives `"ok": false` and names the file.

**2. Engines, versions and dependencies.**

```bash
kb-intake engines
```

Expected: `"ok": true`, and under `docling.label` and `docling.extract`: `"ready": true` with `"problems": []`.
Installed versions that differ from `constraints.txt`, or weights that do not match the lock, appear in `problems`,
and `ok` is false.

**3. A small PDF through the whole flow.** Use any PDF with a text layer, a few pages long:

```bash
cat > decl.yaml <<'EOF'
doc_id: selfcheck
issuer_id: test
doc_type: annual_report
period_end: 2025-12-31
source_url: https://example.com/selfcheck.pdf
completeness: {full: false}
EOF
kb-intake label small.pdf --decl decl.yaml            # prints the job id; "status": "planned"
kb-intake approve <job_id> --by "$USER"                # "status": "approved"
kb-intake extract <job_id>                             # "status": "in_review", "engine_status": "success"
kb-intake confirm <job_id> --by "$USER"                # "package": "selfcheck@…", "check": {"ok": true, ...}
kb-intake check ~/.kb-intake/delivery/selfcheck@*      # "ok": true, "errors": []
```

Measured on six pages of OCBC 2025 (p101–106), M3 Pro, empty cache: label 9.6 s (70 regions, 8 tables), approve 0.05 s,
extract 16.9 s (`engine_status: success`), confirm 0.35 s, `check` ok. Then `kb-intake serve` should show the job,
delivered, at http://127.0.0.1:8700/.

A self-check job is a normal job. Its package stays in `delivery_dir`, and the same file cannot be delivered twice.
Use a scratch `delivery_dir` (`INTAKE_DELIVERY_DIR`) for the check if you do not want it there.

## 6. Configuration

### 6.1 `intake.yaml`

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
| `llm.enabled` | `false` | the optional LLM (§6.2) |
| `llm.provider` | `openai_compatible` | the only provider |
| `llm.base_url` | `https://api.minimax.io/v1` | must be `https`, except for a localhost endpoint |
| `llm.model` | `MiniMax-M3` | |
| `llm.api_key_env` | `INTAKE_LLM_API_KEY` | the name of the environment variable that holds the key; the key itself is never in this file |
| `llm.remote` | `true` | shown in the UI and the manifest; `false` is accepted only for a localhost `base_url` |
| `llm.features` | `{summary: true, describe: true, chart_table: false}` | which LLM uses are allowed; a job's workflow can switch `describe` and `chart_table` off |

The CLI (and so `serve`) also reads `INTAKE_*` lines of a `.env` file in the working directory, for variables the
environment does not set. `.env` is gitignored; `.env.example` is the template.

### 6.2 The optional LLM (MiniMax)

Off by default. It is on only when **both** `llm.enabled: true` is set **and** the key's environment variable is set
for the `kb-intake` process:

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

The upload form and the Workflow page say this before anything is sent.
- **Where answers go:** they are kept apart in `llm.jsonl` and marked low weight. They are cached by provider + model
  + prompt version + input hash, so re-runs make no calls.
- **When a call fails:** it is recorded as an `llm_failed` gap, and the flow goes on.
- **When it is unavailable:** if the approved workflow asks for descriptions but the LLM is off or has no key at
  extraction, each such region gets an `llm_failed` gap with reason `unavailable`, and `manifest.llm.used` stays
  false.
- **The key:** it goes only in the `Authorization` header to `llm.base_url`. Redirects are refused, so it never goes
  to another host. It is not written to logs, the cache, the work files or the package.
- **Network:** the proxy and CA settings of §3.3 and §3.4 apply.

## 7. Operations

### 7.1 Directories

```
work_dir/
  state.db                 jobs, workflow versions, review operations (SQLite)
  uploads/                 files being received by the server (temporary)
  jobs/<job_id>/
    source.pdf
    label/                 docling.json, regions, pages, layout (words), gaps, stats, run.json
    extract/               docling.extract.json, regions, tables, gaps, run.json
    review/                gaps added by review, resolved items, absorbed regions
    llm/                   llm.jsonl, gaps.jsonl, summary.json, run.json
cache_dir/
  docling/                 Docling output per (source, versions, model revisions, parameters)
  llm/                     LLM answers per (provider, model, prompt version, input hash)
delivery_dir/
  <doc_id>@<sha256[:12]>/  one confirmed package; nothing else is written here
```

Consumers read `delivery_dir` only, and package files get ordinary permissions (0666 minus the umask).
- **Atomic:** a package appears complete or not at all. It is built under `delivery_dir/.tmp-<name>-<random>/`,
  checked against contract v2, then renamed into place.
- **Final:** a delivered package is never replaced, and a file that was delivered cannot start a new job.

### 7.2 Clearing the cache

`cache_dir` only saves time. Delete it, or any folder below it, only while no job is running.
- **Docling cache:** the next run recomputes. OCBC then takes about 7.5 minutes instead of seconds.
- **`cache_dir/llm`:** the next LLM use calls the service again, and may answer differently.

`work_dir` is different. Do not delete it while jobs are open: it holds their state. Work folders of delivered jobs
can be archived or removed, since their packages are in `delivery_dir`.

### 7.3 Restart and recovery

One step runs at a time per server. When `serve` starts it:
- clears the running step of any job whose owning process is gone. The job keeps its status, and `last_error` says
  "interrupted". A step owned by a live process, for example a CLI run, is left alone;
- marks LLM summaries left `pending` as failed (reason `interrupted`).

Then **Retry** runs the step again: in the UI, through `POST /v1/jobs/{id}/retry`, or by running the same CLI
command. The step that runs depends on the status: `uploaded` → label, `approved` → extract, `confirmed` → deliver.
Work files are replaced atomically one file at a time. Delivery is idempotent: a package that an interrupted delivery
already renamed into place is recognised and recorded.

If the server process is killed hard (SIGKILL) during a Docling run, the Docling child process may keep running
until it finishes or reaches `wall_timeout`. Stop it by hand before retrying, so two conversions do not run at once.

### 7.4 Upgrading Docling, its dependencies or the models

Pinned versions and model revisions are part of the output contract: the same file must keep giving the same
package. An upgrade is a change like any other and must pass the regression gate before it is merged:

1. Change the pins (`pyproject.toml`, then `uv lock`) or `src/kb_intake/models.lock.json` (the revision and the
   sha256 of every file).
2. Regenerate `constraints.txt` (§1.3), run `uv sync --locked`, then `kb-intake models download`.
3. Run the gate in [tests/regression/](../tests/regression/):
   - the 24 reference tables of OCBC 2025 (`compare_snapshot.py`: every number identical);
   - determinism (`same_packages.py`: two runs byte-identical except the exempt fields);
   - the offline run (`test_offline.py`).
4. Rebuild the OCBC and UOB packages and compare them with the previous release's. Explain every difference in the
   PR.
5. Old cache entries stay valid for the old versions only, because the key includes versions and revisions. Delete
   them to save space.

### 7.5 Exposing the server

`serve` listens on 127.0.0.1 and has **no authentication**.
- **Host check:** it refuses requests whose `Host` header is not localhost. That protects against DNS rebinding but
  is not access control.
- **Do not use `--host 0.0.0.0` on a shared network:** any client that sends `Host: localhost` gets the whole API.
- **For several users:** put it behind a reverse proxy that does authentication and TLS and forwards to 127.0.0.1.
- **Upload size:** uploads have no size limit, so limit the body size in the proxy.

## 8. Resource use (measured)

### 8.1 OCBC Group Annual Report 2025 (276 pages)

Apple M3 Pro, 11 cores, 18 GB RAM, macOS; commit b3da9d7. Docling ran on CPU with `num_threads: 4`, a fresh cache,
and outbound network blocked. Wall time and peak resident memory are per CLI step, measured with `/usr/bin/time -l`,
which includes Docling's child process:

| Step | Wall time | CPU (user + sys) | Peak memory |
|---|---|---|---|
| label (layout + picture classifier) | 119.5 s | 256.6 s | 3.0 GB |
| extract (layout + TableFormer) | 322.0 s | 595.5 s | 3.1 GB |
| confirm (write and check the package) | 6.3 s | | 0.1 GB |
| **total** | 447.8 s (7.5 min) | | |

With the Docling cache warm (the same file again), label takes about 21 s and extract about 4 s (PR #5
measurements). UOB 2025 with a fresh cache (PR #5): label 127.9 s, extract 223.9 s.

Disk, same run:

| What | Size |
|---|---|
| Model weights (`models_dir`) | 521 MB |
| Installed tool (Python + dependencies, macOS arm64) | 1.1 GB |
| Docling cache for this document (`cache_dir`) | 24 MB |
| Job work files (`work_dir`, one job) | 56 MB |
| Delivered package (`delivery_dir`) | 55 MB (the PDF itself is 16 MB) |

### 8.2 Sizing

| | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 8 cores |
| Memory | 8 GB | 16 GB |
| Disk | 3 GB + the documents | 10 GB + about 10× the documents |

- **CPU:** Docling uses `num_threads` (default 4). More cores were not measured.
- **Memory:** about 3.1 GB peak for a 276-page report, plus the server and the browser.
- **Disk:** the weights and the tool, then per document the cache, work files and package. For OCBC those came to
  about 8× the PDF.

These are derived from the one document measured above; a much longer or image-heavy PDF needs more.

## 9. Known limitations

- **No OCR.**
  - Scanned pages and text inside images are not read. Such pages get a `no_text_layer` gap, and image regions keep
    no text.
  - The `ocr` handler exists in the workflow vocabulary but is not implemented.
- **Cell text placement.** Text is read from the PDF text layer into Docling's cell boxes. Where Docling's table
  structure itself is wrong, the text follows it. Known cases on OCBC 2025:
  - p131: "Changes in non-controlling interests" is split over two rows;
  - p183: the header "Liabilities for remaining coverage" is merged into the "2024" cell.

  Numbers are not affected on the 24 reference tables (1,906 numeric cells identical).
- **Suspected tables from the picture classifier** (a table drawn as an image) need OCR to be read. Reclassifying
  them as a table extracts nothing useful.
- **Not tested:**
  - the first installation time on a clean machine;
  - a full Docling run on Linux;
  - installing on Linux at all: only the Linux dependency resolution was checked (§3.6);
  - a real company network. The mirror, credential, proxy and CA settings of §3.2–3.4 were checked against local
    test servers: an HTTPS index with Basic auth signed by a test CA, and a CONNECT proxy.
- **The server is single-user:** no authentication and no upload size limit (§7.5).
- **Only one step runs at a time** per server; a 276-page report blocks others for about 7.5 minutes.
- **Killing the server hard during Docling** can leave the conversion process running (§7.3).
- **LLM cache key:** it is provider + model + prompt version + input hash, as designed. Pointing the same model name
  at another endpoint reuses the cached answers.
- **A summary still pending at confirmation:** the job is delivered without the summary.
- **Only PDF** files are accepted.
