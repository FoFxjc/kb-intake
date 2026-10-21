"""Response models and request-body schemas of the HTTP API (intake-ui-llm-design-v1 §3), so the OpenAPI document
describes every body and can be frozen (contract/api/openapi-v1.json).

- Routes keep parsing request bodies themselves (status codes and error bodies of v2 stay as they were); the request
  models here only document them, through `openapi_extra`.
- Routes answer with `response_model_exclude_unset=True`: a field the handler did not set is left out, never added
  as null, so a model does not change what an endpoint returns.
- Records that the contract defines (regions, tables, gaps; contract/v2) and that may carry work-only fields
  (`docling_label`, `classes`, `stage`) allow extra keys.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Box = list[float]


class Open(BaseModel):
    """A record that may carry more keys than the ones named here."""
    model_config = ConfigDict(extra="allow")


# ---------- contract records (contract/v2) ----------

class Font(BaseModel):
    name: str
    size: float
    bold: bool


class Region(Open):
    """A region: label-stage (with docling_label, classes; no status yet) or current (contract/v2 region)."""
    region_id: str
    page_no: int
    bbox: Box
    type: str
    subtype: str | None
    score: float | None
    label_source: str
    layer: str
    reading_order: int
    font: Font | None
    text: str | None = None
    text_source: str | None = None
    status: str | None = None
    handler: str | None = None
    table_id: str | None = None
    absorbed_into: str | None = None
    docling_label: str | None = None
    classes: list[list[Any]] | None = None


class Cell(Open):
    r: int
    c: int
    rs: int
    cs: int
    text: str | None
    bbox: Box | None
    bbox_source: str | None = None
    is_header: bool


class ContinuedHint(BaseModel):
    table_id: str
    reason: str


class Table(Open):
    table_id: str
    region_id: str
    page_no: int
    bbox: Box
    n_rows: int
    n_cols: int
    cells: list[Cell]
    markdown: str
    extract_method: str
    checks: dict[str, Any] | None = None
    continued_hint: ContinuedHint | None = None


class Resolution(BaseModel):
    action: str
    review_seq: int


class Gap(Open):
    code: str
    page_no: int | None
    detail: str
    region_id: str | None = None
    table_id: str | None = None
    resolved: Resolution | None = None
    stage: Literal["label", "extract", "review"] | None = None


class ReviewRow(Open):
    """A review.jsonl row (contract/v2 review)."""
    action: str
    region_id: str | None
    from_: str | None = Field(alias="from")
    to: str | None
    by: str
    at: str
    note: str | None


# ---------- jobs ----------

class JobSummary(BaseModel):
    job_id: str
    status: str
    running_op: str | None
    doc_id: str
    source_name: str
    created_at: str
    updated_at: str
    last_error: str | None
    package: str | None


class JobList(BaseModel):
    jobs: list[JobSummary]


class Job(BaseModel):
    id: str
    status: str
    running_op: str | None
    last_error: str | None
    sha256: str
    doc_id: str
    source_name: str
    declaration: dict[str, Any]
    created_at: str
    updated_at: str
    approved_by: str | None
    approved_at: str | None
    workflow_version: int | None
    confirmed_by: str | None
    confirmed_at: str | None
    package: str | None
    dir: str


class Event(BaseModel):
    seq: int
    at: str
    step: str
    status: str
    info: dict[str, Any] | None


class EventList(BaseModel):
    events: list[Event]


class JobDetail(BaseModel):
    job: Job
    stats: dict[str, Any] | None = Field(description="label/stats.json; null before labelling")
    extract: dict[str, Any] | None = Field(description="extract/run.json; null before extraction")
    events: list[Event] = Field(description="the last 50 events")
    busy: bool


class CreateJobResult(BaseModel):
    job_id: str
    created: bool
    existing_job: bool
    status: str
    started: bool
    existing_note: str | None = None


class Started(BaseModel):
    job_id: str
    started: str


class GapList(BaseModel):
    gaps: list[Gap]


# ---------- workflow ----------

class WorkflowView(BaseModel):
    version: int
    created_at: str
    text: str
    workflow: dict[str, Any] | None = Field(description="the parsed workflow; null when the text is invalid")
    error: str | None
    approved_version: int | None
    types: list[str]
    handlers: list[str]
    notes: list[Any]


class WorkflowSaved(BaseModel):
    version: int


# ---------- review ----------

class PageView(BaseModel):
    page_no: int
    width: float
    height: float
    page_label: str | None
    kind: str | None
    excluded: bool
    regions: list[Region]
    tables: list[Table]
    gaps: list[Gap]


class ReviewResult(BaseModel):
    applied: list[ReviewRow]
    status: str


# ---------- packages, engines, directories, health ----------

class PackageCheck(BaseModel):
    contract_version: str
    ok: bool
    errors: list[str]
    counts: dict[str, Any]


class PackageEntry(BaseModel):
    package: str
    job_id: str | None
    check: PackageCheck


class PackagesView(BaseModel):
    delivery_dir: str
    packages: list[PackageEntry]


class EngineStage(BaseModel):
    name: str
    version: str
    models: list[dict[str, Any]]
    params: dict[str, Any]
    ready: bool
    problems: list[str]


class EnginesView(BaseModel):
    ready: bool
    stages: dict[str, EngineStage]
    models_dir: str
    pymupdf: str
    layout_enabled: bool


class DirectoryEntry(BaseModel):
    name: str
    path: str
    kind: Literal["directory", "pdf"]


class DirectoriesView(BaseModel):
    roots: list[str]
    path: str | None = None
    entries: list[DirectoryEntry] | None = None


class StepRef(Open):
    job_id: str
    step: str


class Health(BaseModel):
    ok: bool
    version: str
    contract_version: str
    busy: StepRef | None
    last: StepRef | None


class ErrorBody(BaseModel):
    detail: Any
    error: str | None = None


# ---------- request bodies (documentation only; routes parse them) ----------

class CreateJobJson(BaseModel):
    path: str = Field(description="an absolute path of a PDF inside scan_roots")
    declaration: str = Field(description="the declaration, YAML text")


class CreateJobUpload(BaseModel):
    file: bytes = Field(json_schema_extra={"format": "binary"})
    declaration: str = Field(description="the declaration, YAML text")


class ApproveBody(BaseModel):
    by: str
    version: int | None = None


class ReclassifyOp(BaseModel):
    action: Literal["reclassify"]
    region_id: str
    to: str
    note: str | None = None


class ExcludeOp(BaseModel):
    action: Literal["exclude"]
    region_id: str
    note: str | None = None


class ReviewBody(BaseModel):
    by: str
    ops: list[ReclassifyOp | ExcludeOp]


class ConfirmBody(BaseModel):
    by: str


# ---------- search (intake-ui-llm-design-v1 §3.2) ----------

class Match(BaseModel):
    source: Literal["region", "cell", "llm"] = Field(description="what matched: the region's text, a table cell, "
                                                                  "or an LLM description")
    snippet: str = Field(description="the matching text with the hits between « and »")
    bbox: Box = Field(description="where to highlight: the cell's box for a cell, else the region's box")
    table_id: str | None = None
    r: int | None = None
    c: int | None = None


class RegionRow(BaseModel):
    region_id: str
    page_no: int
    bbox: Box
    type: str
    subtype: str | None
    score: float | None
    label_source: str
    layer: str
    reading_order: int
    status: str | None = Field(description="null before extraction")
    handler: str | None
    table_id: str | None
    font: Font | None
    text_preview: str | None = Field(description="the first 200 characters of the region's text (text by box from "
                                                 "the text layer where the region has none)")
    gaps: list[str] = Field(description="codes of the region's open gaps")
    llm: list[str] = Field(description="kinds of LLM output the region has")
    match: Match | None


class RegionPage(BaseModel):
    total: int = Field(description="regions passing the filters")
    items: list[RegionRow]
    next_cursor: str | None = Field(description="pass as `cursor` for the next page; null on the last page")


class Facets(BaseModel):
    total: int
    type: dict[str, int] = Field(description="counted with every filter except `type`")
    status: dict[str, int] = Field(description="counted with every filter except `status`")
    gap: dict[str, int] = Field(description="open gap codes, counted with every filter except `gap`")


# ---------- clusters (intake-ui-llm-design-v1 §3.3) ----------

class Cluster(BaseModel):
    cluster_id: str = Field(description="stable for the same members and rule: kind + ':' + 12 hex")
    kind: Literal["repeated_text", "same_shape", "same_table_header", "same_gap"]
    label: str
    basis: dict[str, Any] = Field(description="the values the members share (the grouping key)")
    n: int
    pages: list[int]
    members: list[str] = Field(description="region ids in reading order")


class ClusterList(BaseModel):
    clusters: list[Cluster]
