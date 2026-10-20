"""Declarations: validation of the fields a submitter states about a file.

The format checks come from kb `pipeline/registry.validate()` (D49 split). Checks that need kb's type
configuration (doc_type reserved as a doc_id abbreviation, recomputing doc_id from the profile) stay in kb and
run when kb imports the package. kb-intake only checks the format of `doc_id` and uses it to name the package.
"""

import re
from datetime import date

REQUIRED = ("doc_id", "issuer_id", "doc_type", "period_end", "lang", "source_url", "completeness")
OPTIONAL = ("translation_of", "publisher", "published_at")
LANGS = ("en", "zh")
DOC_ID = re.compile(r"[a-z0-9-]+_[a-z][a-z0-9_]*_\d{4}_(?:en|zh)")


class DeclarationError(ValueError):
    def __init__(self, param: str, message: str, allowed=None):
        super().__init__(message)
        self.param, self.allowed = param, allowed


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
    if not DOC_ID.fullmatch(str(decl["doc_id"])):
        raise DeclarationError("doc_id", "doc_id must look like <issuer>_<type>_<year>_<lang>, e.g. ocbc-group_ar_2025_en")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(decl["doc_type"])):
        raise DeclarationError("doc_type", "doc_type must match [a-z][a-z0-9_]*")
    if decl["lang"] not in LANGS:
        raise DeclarationError("lang", "lang must be en or zh", list(LANGS))
    if not str(decl["doc_id"]).endswith("_" + decl["lang"]):
        raise DeclarationError("doc_id", "doc_id must end with the declared lang")
    if not re.fullmatch(r"[a-z0-9-]+", str(decl["issuer_id"])):
        raise DeclarationError("issuer_id", "issuer_id must match [a-z0-9-]+")
    if not str(decl["doc_id"]).startswith(decl["issuer_id"] + "_"):
        raise DeclarationError("doc_id", "doc_id must start with the declared issuer_id")
    try:
        date.fromisoformat(str(decl["period_end"]))
    except ValueError:
        raise DeclarationError("period_end", "period_end must be an ISO date (YYYY-MM-DD)")
    comp = decl["completeness"]
    if not isinstance(comp, dict) or not isinstance(comp.get("full"), bool):
        raise DeclarationError("completeness", 'completeness must be {"full": bool, "sections": [...] | null}')
    out = dict(decl)
    out["period_end"] = str(decl["period_end"])        # YAML parses dates; the package stores the ISO string
    if out.get("published_at") is not None:
        out["published_at"] = str(out["published_at"])
    return out
