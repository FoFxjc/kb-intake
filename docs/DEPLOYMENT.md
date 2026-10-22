# Deploying kb-intake

For whoever installs kb-intake in a company environment. You download the source, put the model files in place,
fill in one settings file, and run one command. That command installs everything, then checks the installation
and runs a small end-to-end test. Below that come the recommended dependencies, the configuration, operations,
measured resource use and known limits. What kb-intake does and the package format are in the
[README](../README.md); the HTTP API is in [api-v1.md](api-v1.md).

Every number below was measured, on the machine and commit named next to it. Anything not tested says so.

> **License notice: PyMuPDF is AGPL-3.0.** kb-intake depends on PyMuPDF (text layer, page images, `find_tables`),
> which is licensed under the GNU AGPL-3.0 or, alternatively, a commercial license from Artifex. Offering kb-intake
> to others over a network, or distributing it, brings AGPL obligations unless your organisation holds an Artifex
> commercial license. Have this checked before deploying beyond your own team.

> **Use a separate environment for kb-intake.** Docling and the libraries under it (torch, transformers, pillow,
> …) are pinned to exact versions, because they decide what the packages contain. Do not install kb-intake into an
> environment shared with other projects that use Docling: either side's upgrade would break the other.
> `./setup.sh` makes its own environment (§4).

## What you need

| | |
|---|---|
| Operating system | macOS arm64 or Linux (§10 says what was tested on each) |
| Python | **3.12**: from conda (any distribution; `./setup.sh` creates an environment `kb-intake`), or a `python3.12` on the PATH |
| Packages | a pip index that has the packages of `constraints.txt` (PyPI or the company mirror) |
| Models | the files of three Hugging Face models at pinned commits, 521 MB (§2) |
| Disk | 3 GB for the environment and the models, plus about 8× the size of the PDFs processed (§9) |
| Memory | 8 GB at least, 16 GB recommended (a 276-page report peaks at 3.1 GB) |
| Network | only for installing, unless the optional LLM is on (§3) |

## 1. Download the source

Download the source zip of the release from GitHub and unpack it. For example, for v0.3.1 (Code → Download ZIP, or
the release page's "Source code (zip)"):

```bash
unzip kb-intake-0.3.1.zip && cd kb-intake-0.3.1      # the folder the zip unpacks to
```

Everything below happens in that directory. Git and Node are not needed: the web UI is prebuilt inside the source.

## 2. Put the model files in place

kb-intake reads three models from a local directory, `INTAKE_MODELS_DIR` (by default `./models`). It never
downloads at run time. Every file is checked against the sha256 in `src/kb_intake/models.lock.json`, and a
directory that does not match is refused.

**Use exactly the pinned commits below.** The company's Hugging Face copy may also hold other revisions of these
repos, or another `main`. Those are different weights: they would change the packages, and kb-intake refuses them.

The expected layout:

```
models/                                              <- INTAKE_MODELS_DIR
  docling-project--docling-layout-heron/
    .gitattributes  README.md  config.json  docling_heron_400.png  model.safetensors  preprocessor_config.json
  docling-project--docling-models/
    .gitattributes  .gitignore  README.md  config.json
    model_artifacts/tableformer/accurate/tableformer_accurate.safetensors
    model_artifacts/tableformer/accurate/tm_config.json
    model_artifacts/tableformer/fast/tableformer_fast.safetensors
    model_artifacts/tableformer/fast/tm_config.json
  docling-project--DocumentFigureClassifier-v2.5/
    .gitattributes  README.md  config.json  model.safetensors  preprocessor_config.json
```

**You do not have to arrange the files yourself.** Copy what you have anywhere below `INTAKE_MODELS_DIR`:
- a Hugging Face cache (`models--docling-project--…/snapshots/<commit>/…`), as `huggingface-cli download` or
  `snapshot_download` writes it;
- the repo folders;
- or the plain files.

`./setup.sh` finds every pinned file by name and sha256 and copies it into the layout above. You can also do it by
hand: `kb-intake models import <directory>`. If a file is missing, both name it together with the page of its
pinned commit.

### The files

Every file of every model is required, including the small ones (`.gitattributes`, `README.md`, …).

#### docling-project/docling-layout-heron  (layout: region types and boxes; Apache-2.0)

- Commit: `8f39ad3c0b4c58e9c2d2c84a38465abf757272d8`
- Files of that commit: https://huggingface.co/docling-project/docling-layout-heron/tree/8f39ad3c0b4c58e9c2d2c84a38465abf757272d8
- Target directory: `<INTAKE_MODELS_DIR>/docling-project--docling-layout-heron/` (6 files, 171.8 MB)

| File (path below the target directory) | Size | sha256 |
|---|---|---|
| `.gitattributes` | 1.5 kB | `11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361` |
| `README.md` | 3.2 kB | `175700839bc7808eac6af1d0c23e4f483606ab2276fe01122f4093e61a1a65b6` |
| `config.json` | 3.3 kB | `fdea30805ce2f5666b147fca941dcdd27ad468e27d6ed21902207d3da056a97d` |
| `docling_heron_400.png` | 96.9 kB | `e7f78610372b32a7938e480d2c7fa1c3037ee170bd82282a5bd026232f6e6f9e` |
| `model.safetensors` | 171.7 MB | `00333a43451945aaf89db8ca9c0a17e75d1537c17db60fdb91aa95f4c7929e0c` |
| `preprocessor_config.json` | 444 B | `cd38cd59999e7a95d68e487fbe5132df3d4e5c32a0836add57e6126ba0c4eaf1` |

#### docling-project/docling-models  (TableFormer: table rows and columns; CDLA-Permissive-2.0)

- Commit: `fc0f2d45e2218ea24bce5045f58a389aed16dc23` (tag v2.3.0)
- Files of that commit: https://huggingface.co/docling-project/docling-models/tree/fc0f2d45e2218ea24bce5045f58a389aed16dc23
- Target directory: `<INTAKE_MODELS_DIR>/docling-project--docling-models/` (8 files, 358.2 MB)

| File (path below the target directory) | Size | sha256 |
|---|---|---|
| `.gitattributes` | 1.9 kB | `360aba3ff7ce75396f13f0a77925fe9e79997e8d4bac064595ebb00c82700386` |
| `.gitignore` | 5.2 kB | `b44b404189eaff3631c470946e7350c2e88cead304d332a8ad50325e6f56e95d` |
| `README.md` | 3.4 kB | `d17f233378eff1240b623b36da76ee8b40afcca05d505949713bf03f7e00822a` |
| `config.json` | 41 B | `9c34024dc28ff47b75818f415e769809798c29bf9bde6f2ccc63a4acb62396d9` |
| `model_artifacts/tableformer/accurate/tableformer_accurate.safetensors` | 212.8 MB | `2a7d6c924b3cd12fb99a09280ca9c33a89c5d60b93253617d2e088c1a40374d9` |
| `model_artifacts/tableformer/accurate/tm_config.json` | 7.1 kB | `984e122ceb8ccf84d84c9d2882f6f2302a44b4f1e577babd6289892c36f3cffd` |
| `model_artifacts/tableformer/fast/tableformer_fast.safetensors` | 145.5 MB | `3119563aab5a7c96fda4d621119b63fd8806272b86c30936d15507616422f718` |
| `model_artifacts/tableformer/fast/tm_config.json` | 7.1 kB | `dca6762508dddfae6d57d6cb4ef822c6000119dff0f3b6489db7413118c2622a` |

#### docling-project/DocumentFigureClassifier-v2.5  (picture classifier: chart, logo, icon, signature, ...; MIT)

- Commit: `f859dfbff5c9916cd996942d4b0db7fa25808220`
- Files of that commit: https://huggingface.co/docling-project/DocumentFigureClassifier-v2.5/tree/f859dfbff5c9916cd996942d4b0db7fa25808220
- Target directory: `<INTAKE_MODELS_DIR>/docling-project--DocumentFigureClassifier-v2.5/` (5 files, 16.4 MB)

| File (path below the target directory) | Size | sha256 |
|---|---|---|
| `.gitattributes` | 1.6 kB | `578ac51c49a4dc3bb76753773665a312894ace5cbbc9db61eb522eba28fc893e` |
| `README.md` | 7.5 kB | `df8d868e8a850ae0edd585a21eaf42d6ec00cd07374465e8e98a3a73df429e43` |
| `config.json` | 2.5 kB | `dbed0349fb8b12fad95d8ff7f43b062c40971e06d9d957b25e90d91dfd3bd77e` |
| `model.safetensors` | 16.4 MB | `6bf1e44d6bce316dcade6eb9929d8f8d23b6e8d9d29062b3b4011cff87c7c3cd` |
| `preprocessor_config.json` | 377 B | `118dc069572036346fbacc3e15586089d3f8a9aaf9036c0c54b4666236a28032` |

## 3. Fill in `.env`

```bash
cp .env.example .env
```

`.env` holds the settings and the LLM key, so it is never committed (it is in `.gitignore`). `./setup.sh` reads
it, and so does kb-intake (CLI and `serve`) when it is started in this directory. A variable already set in the
environment wins over the file. Relative paths are relative to this directory.

| Variable | Default | Meaning |
|---|---|---|
| `INTAKE_MODELS_DIR` | `./models` | the model files (§2) |
| `INTAKE_DATA_DIR` | `./data` | jobs (`work/`), cache (`cache/`) and delivered packages (`delivery/`) |
| `INTAKE_LLM_ENABLED` | `false` | the optional LLM; **nothing is sent anywhere unless this is `true`** |
| `INTAKE_LLM_BASE_URL` | | the OpenAI-compatible base URL, usually ending in `/v1` (requests go to `<base>/chat/completions`) |
| `INTAKE_LLM_MODEL` | | the model name the endpoint serves |
| `INTAKE_LLM_API_KEY` | | the key; sent only in the `Authorization` header to that URL, never written anywhere else |
| `INTAKE_LLM_ALLOW_HTTP` | `false` | `true` accepts a plain `http://` URL on another host; the key then crosses the network unencrypted. Without it such a URL is refused |
| `INTAKE_LLM_DESCRIBE` | | `false` switches chart and picture descriptions off. You do not need it for a text-only model: `kb-intake doctor` tests images on every run and switches descriptions off by itself (§4) |
| `PIP_INDEX_URL` | | the pip mirror for `./setup.sh`. Without it, pip uses your existing pip configuration, which `./setup.sh` never changes |
| `PIP_EXTRA_INDEX_URL` | | a mirror of PyTorch's CPU wheels (Linux; §6.3) |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | | a proxy for pip and for the LLM requests |
| `SSL_CERT_FILE` | | a CA bundle (public roots plus the company's root CA) if TLS is re-signed. The LLM client uses it; `./setup.sh` hands it to pip as `PIP_CERT` (pip does not read `SSL_CERT_FILE`) |
| `INTAKE_ENV` | `auto` | the environment `./setup.sh` makes: `auto` (conda when available, else a venv), `conda` or `venv` |
| `INTAKE_CONDA_ENV` | `kb-intake` | the conda environment's name |
| `INTAKE_PYTHON` | `python3.12` | the Python 3.12 for the venv when there is no conda |

**What the LLM is sent** when `INTAKE_LLM_ENABLED=true`. The upload form, the Workflow page, the package manifest
(`manifest.llm.sent_to`) and `kb-intake doctor` all name the host:

| Use | When | What is sent |
|---|---|---|
| Document summary | right after labelling, for every job | the declaration (doc_id, issuer_id, doc_type, period_end, lang) and the start of every page's text, up to 24,000 characters in all |
| Chart and picture descriptions | during extraction, for regions whose approved handler is "LLM describe" | each region's image (144 dpi, at most 2,000 px) and the text found in it |
| Chart to table (experimental, off by default) | as descriptions; bar, line and pie charts | the same image and text |

Answers are kept apart in `llm.jsonl` and marked low weight. A failed call becomes an `llm_failed` gap, and the
flow goes on.

## 4. Run `./setup.sh`

```bash
./setup.sh
```

One command, five steps. It can be run again at any time: what is already in place is kept, and an unchanged
source is not reinstalled.

1. **Settings.** It reads `.env`. Without a `.env` it stops and says so.
2. **Python 3.12.**
   - **With conda:** it creates a conda environment `kb-intake` with Python 3.12. Conda provides only Python; the
     conda channels come from your `.condarc`.
   - **Without conda:** it creates `./.venv` from `python3.12`.
3. **Packages.** It runs `pip install -c constraints.txt .` into that environment. All packages come from pip with
   the pinned versions; conda packages are not used, because their builds differ and the startup dependency check
   would report them as different.
   - The output is in `setup-pip.log`.
   - The installed environment is about 1.1 GB (torch and Docling are large).
4. **Models.** It verifies `INTAKE_MODELS_DIR`. If the files are there but laid out differently, it runs
   `kb-intake models import` first.
5. **`kb-intake doctor --smoke`.** It checks the installation item by item, then runs a generated three-page PDF
   (title, text, one ruled table) through label → approve → extract → confirm → check, in a temporary directory.

It exits 0 when kb-intake is ready, and 1 otherwise; each failure says why and what to do. At the end it prints how
to start kb-intake: `./kb-intake serve` in this directory, which opens http://127.0.0.1:8700/. `./kb-intake` is a
small launcher that `./setup.sh` writes for the environment it made.

`kb-intake doctor` can be run on its own at any time, with or without `--smoke`. The same table goes to the
terminal and to `kb-intake-doctor.json`. Send that file when asking for help; it never contains the key.

### What `kb-intake doctor` checks, and what to do when a check fails

| Check | FAIL means | What to do |
|---|---|---|
| `python` | not Python 3.12 | remove the environment (`conda env remove -n kb-intake`, or `rm -rf .venv`) and run `./setup.sh` again |
| `dependencies` | installed versions differ from `constraints.txt` (listed) | usually a shared or edited environment; run `./setup.sh` in a separate environment |
| `sqlite_fts5` | this Python's SQLite has no FTS5 (search needs it) | use a conda-forge or python.org Python 3.12 |
| `model layout` / `model table_structure` / `model picture_classifier` | files missing or not the pinned ones (named) | add the named files (§2); `kb-intake models import <dir>` arranges them |
| `torch` | never fails: reports CPU or CUDA build | a CUDA build works but is several GB larger (§6.3) |
| `llm` | enabled without a key | set `INTAKE_LLM_API_KEY`, or `INTAKE_LLM_ENABLED=false` |
| `llm_text` | the endpoint cannot be reached or refuses (the error kind is named) | network: check `INTAKE_LLM_BASE_URL`, `HTTPS_PROXY` / `NO_PROXY`, `SSL_CERT_FILE`. 401/403: the key. 404: the base URL (usually ends in `/v1`) or the model name |
| `llm_image` | the model rejects images | **not blocking**: chart and picture descriptions are switched off automatically; summaries still work. The result is recorded in `<INTAKE_DATA_DIR>/work/llm-capabilities.json` (never in `.env`), and the latest check counts. After switching to a model that accepts images, run `kb-intake doctor` again: descriptions come back. Restart `serve` after a doctor run |
| `smoke_label` … `smoke_package` | the end-to-end test failed (the step and message are shown) | usually models or dependencies (see those rows); otherwise send `kb-intake-doctor.json` |
| `config` | a setting cannot be used (e.g. `http://` on another host without `INTAKE_LLM_ALLOW_HTTP`) | correct it in `.env` |

### What it looks like

A source zip with the models laid out as a Hugging Face cache and an OpenAI-compatible endpoint on another host
over `http://` (`INTAKE_LLM_ALLOW_HTTP=true`). The run was a venv on macOS arm64 (M3 Pro), commit d3efe3e, with a
local stand-in for the intranet model. The first run took 106 s with pip's download cache warm.

```
== 4/5 models (…/kb-intake-0.3.0/models)
   not in the expected layout or incomplete: looking for the pinned files below it (kb-intake models import)
   ok: put into the expected layout and verified

== 5/5 kb-intake doctor --smoke
PASS  python                    CPython 3.12.12 (…/kb-intake-0.3.0/.venv/bin/python)
PASS  dependencies              every installed version matches constraints.txt (107 pins)
PASS  sqlite_fts5               SQLite has FTS5 (search)
PASS  model layout              docling-project/docling-layout-heron@8f39ad3c0b4c: 6 files, sha256 match
PASS  model table_structure     docling-project/docling-models@fc0f2d45e221: 8 files, sha256 match
PASS  model picture_classifier  docling-project/DocumentFigureClassifier-v2.5@f859dfbff5c9: 5 files, sha256 match
PASS  torch                     torch 2.14.1: CPU build
PASS  llm                       qwen-test at http://192.168.50.203:8781/v1: data will be sent to 192.168.50.203:8781
PASS  llm_text                  answered in 0.0 s
PASS  llm_image                 answered in 0.0 s; chart and picture descriptions available
PASS  smoke_label               39.0 s; 3 pages, 7 regions, 1 table(s) found
PASS  smoke_summary             document summary made
PASS  smoke_approve             0.1 s
PASS  smoke_extract             8.4 s; Docling success, 1 table(s), 0 gap(s)
PASS  smoke_package             0.4 s; contract check ok; table cells read: 3/3 sample values

kb-intake 0.3.0 on arm64: ready
```

A model that accepts only text. The exit code is 0, and `.env` is not changed:

```
PASS  llm_text                  answered in 0.0 s
FAIL  llm_image                 http_400: HTTP 400: images not accepted: chart and picture descriptions switched off automatically (latest check, recorded in …/data/work/llm-capabilities.json); the document summary still works
                                fix: use a model that accepts images to get descriptions; the next `kb-intake doctor` checks again
PASS  smoke_summary             document summary made
…
kb-intake 0.3.0 on arm64: ready
```

An endpoint that cannot be reached. The exit code is 1:

```
FAIL  llm_text                  network: connection failed (ConnectionRefusedError)
                                fix: cannot connect to 192.168.50.203:8783: check INTAKE_LLM_BASE_URL, HTTPS_PROXY / NO_PROXY, and SSL_CERT_FILE if the company re-signs TLS
SKIP  llm_image                 the text request failed
FAIL  smoke_summary             no summary (status failed, network)
…
kb-intake 0.3.0 on arm64: NOT ready: llm_text, smoke_summary
FAILED: kb-intake doctor found a problem (report: …/kb-intake-doctor.json)
What to do: follow the 'fix:' line under each FAIL above, then run ./setup.sh again
```

## 5. Using it

```bash
cd kb-intake-0.3.1
./kb-intake serve                      # http://127.0.0.1:8700/   (or: conda activate kb-intake / source .venv/bin/activate)
./kb-intake doctor                     # again, any time
```

Start kb-intake in this directory so that it reads `.env`. The command line (`label`, `approve`, `extract`,
`review`, `confirm`, `check`) is in the [README](../README.md).

## 6. Recommended dependencies

### 6.1 Python packages

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

### 6.2 `constraints.txt`: why the installation always uses it

`pyproject.toml` pins only the direct dependencies. The about 130 packages under them (torch, transformers, numpy,
pillow, …) would otherwise be resolved to whatever is newest on the day of installation. A different torch,
transformers or pillow can change the model output, so the same PDF would no longer give the same package. Those
versions are part of the output contract, and only `constraints.txt` pins them.

- `constraints.txt` is generated from `uv.lock`, the full tested environment. CI fails when the two differ.
- `./setup.sh` installs with `pip install -c constraints.txt .`.
- `kb-intake doctor` (the `dependencies` row) and `kb-intake engines` compare the installed versions with it.

uv users can install the same way with `uv pip install -c constraints.txt .` in their own environment.

### 6.3 torch on Linux: CPU build

On Linux, the torch wheels on PyPI are CUDA builds. They pull in triton and 15 `nvidia-*` packages, several GB,
which kb-intake does not use: it runs on CPU. They work, though, and `kb-intake doctor` reports which build is
installed.

PyTorch publishes CPU builds (versions ending in `+cpu`) on its own index, `https://download.pytorch.org/whl/cpu`.
To get them:

- **The company mirror proxies that index.** Put the mirror's URL for it in `.env` as `PIP_EXTRA_INDEX_URL`. pip
  then takes `torch==2.14.1+cpu`, which satisfies the `torch==2.14.1` constraint and sorts above it.
- **`download.pytorch.org` is reachable** (directly or through the proxy). Use `PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu`.
- **Neither.** Install the CUDA build from the normal mirror. Or, on a machine that can reach the PyTorch index,
  download the two wheels (`torch-2.14.1+cpu-cp312-cp312-manylinux_2_28_x86_64.whl` and the matching
  `torchvision-0.29.1+cpu-…`). Put them in a directory and add `PIP_FIND_LINKS=/that/directory` to `.env`.

## 7. Configuration

### 7.1 `intake.yaml` (optional)

Not needed for the setup above: `.env` covers what most installations change. For the rest (Docling parameters,
`scan_roots`, `low_confidence`) an `intake.yaml` can be given with `--config FILE` or `INTAKE_CONFIG`. Every key is
optional and unknown keys are refused. Precedence: command-line flag > environment variable (`.env`) >
`intake.yaml` > default. A template is [intake.example.yaml](../intake.example.yaml).

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
| `llm.enabled` | `false` | the optional LLM (`INTAKE_LLM_ENABLED`) |
| `llm.provider` | `openai_compatible` | the only provider |
| `llm.base_url` | `https://api.minimax.io/v1` | `INTAKE_LLM_BASE_URL`; `https`, or `http` on localhost or with `allow_http` |
| `llm.model` | `MiniMax-M3` | `INTAKE_LLM_MODEL` |
| `llm.api_key_env` | `INTAKE_LLM_API_KEY` | the name of the environment variable that holds the key; the key itself is never in this file |
| `llm.allow_http` | `false` | `INTAKE_LLM_ALLOW_HTTP` |
| `llm.remote` | derived | whether data leaves this machine: true unless `base_url` is localhost; `false` is accepted only for localhost |
| `llm.features` | `{summary: true, describe: true, chart_table: false}` | which LLM uses are allowed (`INTAKE_LLM_DESCRIBE` for describe; also off when the latest doctor check found that the model refuses images); a job's workflow can switch `describe` and `chart_table` off |

`.env` (§3) is read from the directory kb-intake is started in: the `INTAKE_*` variables and the proxy and CA
variables, for whatever the environment does not set.

### 7.2 The LLM

Everything about it is in §3: the switch, the endpoint, the key and what is sent. Without `INTAKE_LLM_BASE_URL` or
`llm.base_url`, the endpoint defaults to MiniMax (`https://api.minimax.io/v1`, model `MiniMax-M3`). The key is sent
only in the `Authorization` header to the configured endpoint; redirects are refused, so it never goes to another
host. It is not written to logs, the cache, the work files, the package or the doctor report.

## 8. Operations

### 8.1 Directories

```
work_dir/
  state.db                 jobs, workflow versions, review operations (SQLite)
  llm-capabilities.json    the latest `kb-intake doctor` image check of the configured model
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

### 8.2 Clearing the cache

`cache_dir` only saves time. Delete it, or any folder below it, only while no job is running.
- **Docling cache:** the next run recomputes. OCBC then takes about 7.5 minutes instead of seconds.
- **`cache_dir/llm`:** the next LLM use calls the service again, and may answer differently.

`work_dir` is different. Do not delete it while jobs are open: it holds their state. Work folders of delivered jobs
can be archived or removed, since their packages are in `delivery_dir`.

### 8.3 Restart and recovery

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

### 8.4 Upgrading Docling, its dependencies or the models

Pinned versions and model revisions are part of the output contract: the same file must keep giving the same
package. An upgrade is a change like any other and must pass the regression gate before it is merged:

1. Change the pins (`pyproject.toml`, then `uv lock`) or `src/kb_intake/models.lock.json` (the revision and the
   sha256 of every file).
2. Regenerate `constraints.txt` (§6.2), run `uv sync --locked`, then `kb-intake models download`.
3. Run the gate in [tests/regression/](../tests/regression/):
   - the 24 reference tables of OCBC 2025 (`compare_snapshot.py`: every number identical);
   - determinism (`same_packages.py`: two runs byte-identical except the exempt fields);
   - the offline run (`test_offline.py`).
4. Rebuild the OCBC and UOB packages and compare them with the previous release's. Explain every difference in the
   PR.
5. Old cache entries stay valid for the old versions only, because the key includes versions and revisions. Delete
   them to save space.

### 8.5 Exposing the server

`serve` listens on 127.0.0.1 and has **no authentication**.
- **Host check:** it refuses requests whose `Host` header is not localhost. That protects against DNS rebinding but
  is not access control.
- **Do not use `--host 0.0.0.0` on a shared network:** any client that sends `Host: localhost` gets the whole API.
- **For several users:** put it behind a reverse proxy that does authentication and TLS and forwards to 127.0.0.1.
- **Upload size:** uploads have no size limit, so limit the body size in the proxy.

## 9. Resource use (measured)

### 9.1 OCBC Group Annual Report 2025 (276 pages)

Apple M3 Pro, 11 cores, 18 GB RAM, macOS; commit b3da9d7. Docling ran on CPU with `num_threads: 4`, a fresh cache,
and outbound network blocked. Wall time and peak resident memory are per CLI step, measured with
`/usr/bin/time -l`, which includes Docling's child process.

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
| Model files (`INTAKE_MODELS_DIR`) | 521 MB |
| The environment (Python + packages, macOS arm64) | 1.1 GB |
| Docling cache for this document (`data/cache`) | 24 MB |
| Job work files (`data/work`, one job) | 56 MB |
| Delivered package (`data/delivery`) | 55 MB (the PDF itself is 16 MB) |

### 9.2 Sizing

| | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 8 cores |
| Memory | 8 GB | 16 GB |
| Disk | 3 GB + the documents | 10 GB + about 10× the documents |

- **CPU:** Docling uses `num_threads` (default 4). More cores were not measured.
- **Memory:** about 3.1 GB peak for a 276-page report, plus the server and the browser.
- **Disk:** the models and the environment, then per document the cache, work files and package. For OCBC those
  came to about 8× the PDF.

These are derived from the one document measured above. A much longer or image-heavy PDF needs more.

## 10. What was tested

The steps above were walked from a source zip (`git archive`), unpacked into a new directory, with the model files
laid out as a Hugging Face cache (so `./setup.sh` had to import them). The LLM was a local OpenAI-compatible
stand-in on another address, over `http://` with `INTAKE_LLM_ALLOW_HTTP=true`, in three set-ups.

| Set-up | Result |
|---|---|
| macOS arm64, venv (`python3.12`), text and images work | setup 106 s, exit 0; every check PASS; `./kb-intake serve` serves the UI |
| same, a text-only model (images refused) | exit 0; `llm_image` FAIL (not blocking); descriptions switched off through `data/work/llm-capabilities.json`, `.env` unchanged (checksum); `serve` then reports `images: false` and the UI no longer offers "LLM describe"; the upload form names the host |
| same, the endpoint unreachable | exit 1; `llm_text` FAIL with what to check |
| macOS arm64, miniforge 26.7.2 (conda-forge only, a clean home directory) | conda environment `kb-intake` with Python 3.12.15; setup 132 s, exit 0; every check PASS; `serve` works |
| Linux aarch64 in Docker (`condaforge/miniforge3`, on the Mac), `PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu`, no pip cache, the LLM on the host | conda environment with Python 3.12.15; first installation from scratch 3 min 17 s, exit 0; `torch 2.14.1+cpu` installed; every check PASS |

**Not tested:**
- a real company network. The pip mirror, proxy and CA handling were checked against local test servers only:
  HTTPS signed by a test CA, Basic auth, and a CONNECT proxy. pip honoured `PIP_CERT`, `REQUESTS_CA_BUNDLE` and
  `HTTPS_PROXY`, and ignored `SSL_CERT_FILE`;
- Linux x86_64: the Linux run above was aarch64, because Docker on an Apple Silicon Mac runs arm64 containers;
- a conda mirror;
- a first installation without pip's download cache on macOS (Linux from scratch: 3 min 17 s, above).

## 11. Known limitations

- **No OCR.**
  - Scanned pages and text inside images are not read. Such pages get a `no_text_layer` gap, and image regions
    keep no text.
  - The `ocr` handler exists in the workflow vocabulary but is not implemented.
- **Cell text placement.** Text is read from the PDF text layer into Docling's cell boxes. Where Docling's table
  structure itself is wrong, the text follows it. Known cases on OCBC 2025:
  - p131: "Changes in non-controlling interests" is split over two rows;
  - p183: the header "Liabilities for remaining coverage" is merged into the "2024" cell.

  Numbers are not affected on the 24 reference tables (1,906 numeric cells identical).
- **Suspected tables from the picture classifier** (a table drawn as an image) need OCR to be read. Reclassifying
  them as a table extracts nothing useful.
- **The server is single-user:** no authentication and no upload size limit (§8.5).
- **Only one step runs at a time** per server; a 276-page report blocks others for about 7.5 minutes.
- **Killing the server hard during Docling** can leave the conversion process running (§8.3).
- **LLM cache key:** provider + model + prompt version + input hash, as designed. The same model name on another
  endpoint reuses the cached answers.
- **A summary still pending at confirmation:** the job is delivered without the summary.
- **Only PDF** files are accepted.
