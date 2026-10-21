"""Declarations: what a submitter states about a file (intake-design §1).

kb-intake only checks the format. A consumer that has its own naming rules for `doc_id` applies them when it imports
the package and refuses a mismatch.

- `doc_id` must be file-name safe (it names the package directory). A directory scan defaults it to the slug of the
  file name.
- `lang` is an ISO 639-1 code and may be omitted; the detected language is used then. A declared language that
  differs from the detected one is recorded as a `decl_mismatch` gap, not refused.

A directory scan reads one declaration file with defaults and per-file overrides:

    defaults: {issuer_id: ..., doc_type: ..., source_url: ..., completeness: {full: true}}
    files:
      report-2025.pdf: {doc_id: ..., period_end: 2025-12-31, lang: en}

An override replaces a field as a whole (no deep merge). A single file may also use a plain mapping.
"""

import re
import unicodedata
from datetime import date
from pathlib import Path

REQUIRED = ("doc_id", "issuer_id", "doc_type", "period_end", "source_url", "completeness")
OPTIONAL = ("lang", "translation_of", "publisher", "published_at")
SAFE = re.compile(r"[a-z0-9][a-z0-9._-]*")
LANG = re.compile(r"[a-z]{2}")


class DeclarationError(ValueError):
    def __init__(self, param: str, message: str, allowed=None):
        super().__init__(message)
        self.param, self.allowed = param, allowed


def slug(filename: str, sha256: str | None = None) -> str:
    """A file-name-safe doc_id from a file name: "878788_UOB AR2025.pdf" -> "878788_uob-ar2025". A name without
    ASCII letters or digits (e.g. only CJK) gives "doc-<sha256[:12]>"."""
    stem = unicodedata.normalize("NFKD", Path(filename).stem).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", stem)
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return s or f"doc-{(sha256 or '0' * 12)[:12]}"


def validate(decl: dict) -> dict:
    """Return a normalised copy of the declaration, or raise DeclarationError naming the first bad field."""
    if not isinstance(decl, dict):
        raise DeclarationError("declaration", "declaration must be a mapping")
    missing = [k for k in REQUIRED if decl.get(k) in (None, "")]
    if missing:
        raise DeclarationError(missing[0], f"declaration missing field(s): {missing}", list(REQUIRED))
    unknown = sorted(set(decl) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise DeclarationError(unknown[0], f"unknown declaration field(s): {unknown}", list(REQUIRED + OPTIONAL))
    if not SAFE.fullmatch(str(decl["doc_id"])):
        raise DeclarationError("doc_id", "doc_id must match ^[a-z0-9][a-z0-9._-]*$ (it names the package directory)")
    if decl.get("lang") not in (None, "") and not LANG.fullmatch(str(decl["lang"])):
        raise DeclarationError("lang", "lang must be an ISO 639-1 code (two lowercase letters)")
    if not SAFE.fullmatch(str(decl["issuer_id"])):
        raise DeclarationError("issuer_id", "issuer_id must match ^[a-z0-9][a-z0-9._-]*$")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(decl["doc_type"])):
        raise DeclarationError("doc_type", "doc_type must match [a-z][a-z0-9_]*")
    try:
        date.fromisoformat(str(decl["period_end"]))
    except ValueError:
        raise DeclarationError("period_end", "period_end must be an ISO date (YYYY-MM-DD)")
    comp = decl["completeness"]
    if not isinstance(comp, dict) or not isinstance(comp.get("full"), bool):
        raise DeclarationError("completeness", 'completeness must be {"full": bool, "sections": [...] | null}')
    out = {k: v for k, v in decl.items() if not (k == "lang" and v in (None, ""))}
    out["period_end"] = str(decl["period_end"])        # YAML parses dates; the package stores the ISO string
    if out.get("published_at") is not None:
        out["published_at"] = str(out["published_at"])
    return out


def is_directory_form(raw) -> bool:
    return isinstance(raw, dict) and bool(raw) and set(raw) <= {"defaults", "files"}


def for_file(raw: dict | None, path: Path, root: Path | None = None, sha256: str | None = None) -> dict:
    """The declaration of one file. Plain form: as is. Directory form: defaults + the file's override (keyed by the
    path relative to the scanned directory). In a directory scan doc_id defaults to the slug of the file name."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise DeclarationError("declaration", "declaration must be a mapping")
    if not is_directory_form(raw):
        decl = dict(raw)
    else:
        files = raw.get("files") or {}
        if not isinstance(files, dict):
            raise DeclarationError("files", "files must map file names to overrides")
        key = path.relative_to(root).as_posix() if root else path.name
        override = files.get(key)
        if override is not None and not isinstance(override, dict):
            raise DeclarationError("files", f"the override of {key} must be a mapping")
        decl = {**(raw.get("defaults") or {}), **(override or {})}
    if root is not None and decl.get("doc_id") in (None, ""):
        decl["doc_id"] = slug(path.name, sha256)
    return decl
