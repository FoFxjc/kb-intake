# kb-intake HTTP API v1 (frozen)

The API served by `kb-intake serve` (default `http://127.0.0.1:8700`). The OpenAPI document is frozen in
`contract/api/openapi-v1.json`; `tests/test_openapi.py` fails when the running API differs from it. The API only
gains endpoints and fields; to change it on purpose, run
`uv run python -m kb_intake.service.openapi_snapshot --write` and explain the change in the PR.

Design: `docs/intake-design-v2.md` §6 (v2 endpoints) and `docs/intake-ui-llm-design-v1.md` §3 (search, clusters,
queue, ack, LLM). Interactive docs at `/docs` while the server runs.

## Conventions

- Served for local hosts only (127.0.0.1, localhost, ::1, or the address given to `--host`); a write whose `Origin`
  names another host is refused (403). Bodies of POST requests are JSON (`application/json`, else 415), except the
  upload (multipart) and `PUT /workflow` (YAML text).
- Errors: `{"detail": ..., "error"?: "WrongState" | "Busy" | ...}`. 404 unknown job or page; 409 wrong state or a
  step already running; 422 invalid input; 400 a review op refused (nothing applied).
- Fields a response does not set are left out, never sent as null.
- Coordinates are PDF points with the origin at the top left of the page.

## Endpoints

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| POST | `/v1/jobs` | CreateJobJson, CreateJobUpload | 202 CreateJobResult | 403 409 415 422 |
| GET | `/v1/jobs` | - | 200 JobList | |
| GET | `/v1/jobs/{job_id}` | - | 200 JobDetail | 404 |
| GET | `/v1/jobs/{job_id}/events` | - | 200 EventList | 404 |
| GET | `/v1/jobs/{job_id}/gaps` | - | 200 GapList | 404 |
| GET | `/v1/jobs/{job_id}/source.pdf` | - | 200 / 206 application/pdf (HTTP Range) | 404 416 |
| GET | `/v1/jobs/{job_id}/regions` | - | 200 RegionPage | 404 409 422 |
| GET | `/v1/jobs/{job_id}/facets` | - | 200 Facets | 404 409 422 |
| GET | `/v1/jobs/{job_id}/clusters` | - | 200 ClusterList | 404 409 422 |
| GET | `/v1/jobs/{job_id}/queue` | - | 200 QueueView | 404 409 |
| POST | `/v1/jobs/{job_id}/queue/seen` | SeenBody | 200 SeenResult | 404 409 415 422 |
| GET | `/v1/jobs/{job_id}/summary` | - | 200 Summary | 404 |
| GET | `/v1/jobs/{job_id}/llm` | - | 200 LlmView | 404 422 |
| GET | `/v1/jobs/{job_id}/workflow` | - | 200 WorkflowView | 404 |
| PUT | `/v1/jobs/{job_id}/workflow` | YAML text | 200 WorkflowSaved | 404 409 422 |
| POST | `/v1/jobs/{job_id}/approve` | ApproveBody | 202 Started | 404 409 415 422 |
| GET | `/v1/jobs/{job_id}/pages/{n}` | - | 200 PageView | 404 409 |
| GET | `/v1/jobs/{job_id}/pages/{n}/image` | - | 200 image/png (`?dpi=` 24-300) | 404 |
| GET | `/v1/jobs/{job_id}/review` | - | 200 ReviewOps | 404 |
| POST | `/v1/jobs/{job_id}/review` | ReviewBody | 200 ReviewResult | 400 404 409 415 422 |
| POST | `/v1/jobs/{job_id}/confirm` | ConfirmBody | 202 Started | 404 409 415 422 |
| POST | `/v1/jobs/{job_id}/retry` | - | 202 Started | 404 409 |
| GET | `/v1/packages` | - | 200 PackagesView | |
| GET | `/v1/engines` | - | 200 EnginesView | |
| GET | `/v1/directories` | - | 200 DirectoriesView | 403 422 |
| GET | `/v1/health` | - | 200 Health | |

Endpoints added in v1 over v2: `source.pdf`, `regions`, `facets`, `clusters`, `queue`, `queue/seen`, `summary`,
`llm`, `GET review`. Additive fields: `resolved` on `/gaps`; `llm` on `/workflow`, `/health`, `/engines`; the review
op `ack`.

## Regions and search (`/regions`, `/facets`)

- Before extraction the label-stage regions are listed (no status or handler yet), after it the current ones.
- Query parameters: `q`, `type`, `status`, `gap` (repeat for several values), `page`, `min_score`, `max_score`;
  `/regions` also takes `cursor` and `limit` (1-500, default 100). Unknown values are 422.
- `q` searches region text, table cells and LLM descriptions. Every word must match, each as a prefix; the input
  is never read as query syntax. A region whose own text is null (label stage; discarded, recorded, excluded or
  absorbed regions) is searched by its text by box from the text layer. A table with cells is searched by cell: the
  hit's `match` names the cell (`table_id`, `r`, `c`) and its box. Snippets mark hits with « ».
- Results are in reading order; `next_cursor` is null on the last page.
- `/facets` counts types, statuses and open gap codes for the same filters; each dimension is counted without its
  own filter, so picking a type keeps the other types' counts visible.
- The index lives in `state.db` (SQLite FTS5) and is rebuilt for a job when its work files change, including
  changes made by the CLI. The first search of a job builds it (OCBC, 276 pages: 1.7 s).

## Clusters (`/clusters`)

Groups of two or more similar regions; a group changes no data. An action on a group is sent as one review op per
member. Text is normalised: NFKC, case-folded, every run of digits becomes `#`, whitespace collapsed.

| kind | members | grouped by |
|---|---|---|
| `repeated_text` | text-like regions whose vertical centre is in the top 12 % or bottom 12 % of the page | type, normalised text, band, font name, font size (1 pt); on 2+ pages |
| `same_shape` | icon, logo, signature, stamp, picture, chart, diagram | type, subtype, size bucket `round(2·log2 w)`, `round(2·log2 h)` (points) |
| `same_table_header` | tables whose first row has text | column count, normalised first-row cells |
| `same_gap` | regions with an open gap | gap code |

Excluded and absorbed regions are left out. `cluster_id` is `kind:` plus 12 hex digits of the key's hash, stable
for the same key.

## Review queue (`/queue`, `/queue/seen`) and `ack`

- Items: every gap (label, extract, review stages) and every table with a weak continuation hint
  (`same_first_row`); empty before extraction. Groups follow the gap codes, then `continued_hint`.
- `item_id` is stable across review: `gap:{code}:{page or 0}:{region_id or table_id or -}`, `hint:{region_id}`.
- Status: `resolved` (review dealt with it: reclassify, exclude, or `ack`), else `seen` (the UI reported it shown;
  kept in `state.db` only, never delivered), else `open`.
- `ack` (`{"action": "ack", "item_id", "verdict"?: "ok" | "wrong", "note"?}` in `POST /review`) records that the
  reviewer looked at an item and changes nothing. The `review.jsonl` row carries `target` (the item) and `verdict`;
  a gap item gets `resolved: {"action": "ack", "review_seq"}`. Refused (400) for an unknown or resolved item, twice
  in a call, or with a reclassify or exclude of the same region in that call.

## LLM (`/summary`, `/llm`)

Off unless `llm.enabled` is set in the configuration and the key is in the environment variable it names
(`INTAKE_LLM_API_KEY` by default). The `llm` block on `/workflow`, `/health` and `/engines` says which, and never
contains the key. Until stage L, `/summary` answers `off` and `/llm` has no items.
