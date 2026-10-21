"""The existing endpoints answer as they did in v2 (intake-ui-llm-design-v1 §3.1: existing interfaces stay unchanged).

One fake-engine flow through every v2 endpoint; each response is recorded with volatile values masked and compared
with tests/data/api_v2_responses.json. Additive changes (a new field) are allowed by the design: regenerate with
KB_INTAKE_WRITE_GOLDEN=1 and check that the diff only adds.
"""

import json
import os
import re

from samples import DOC
from test_service import labelled, settle, svc, upload  # noqa: F401  (svc is a fixture)

GOLDEN = os.path.join(os.path.dirname(__file__), "data", "api_v2_responses.json")
VOLATILE = {"job_id", "id", "created_at", "updated_at", "at", "approved_at", "confirmed_at", "dir", "sha256",
            "package", "delivery_dir", "models_dir", "version", "run", "seconds", "elapsed_s", "duration_s",
            "last", "busy", "pymupdf", "docling", "kb_intake_version", "estimate_s", "label_s", "extract_s",
            "cpu_seconds", "cache_key"}
JOB_ID = re.compile(r"\d{8}T\d{6}-[0-9a-f]{6}")


def masked(obj, tmp: str = ""):
    """`obj` with volatile values replaced by "<masked>", recursively (keys and value types are kept)."""
    if isinstance(obj, dict):
        return {k: "<masked>" if k in VOLATILE else masked(v, tmp) for k, v in obj.items()}
    if isinstance(obj, list):
        return [masked(v, tmp) for v in obj]
    if isinstance(obj, str):
        if tmp:
            obj = obj.replace(tmp, "<tmp>")
        return JOB_ID.sub("<job>", obj)
    return obj


def record(c, method: str, path: str, **kw) -> dict:
    r = c.request(method, path, follow_redirects=False, **kw)
    out = {"status": r.status_code}
    ctype = r.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        out["body"] = masked(r.json(), str(c.tmp))
    else:
        out["content_type"] = ctype.split(";")[0]
    if "location" in r.headers:
        out["location"] = r.headers["location"]
    return out


def test_existing_endpoints_answer_as_before(svc):  # noqa: F811
    c = svc
    (c.tmp / "roots" / "inbox" / "a.pdf").write_bytes(b"%PDF-1.4 not really")
    got = {}
    got["POST /v1/jobs (bad)"] = record(c, "POST", "/v1/jobs", files={"file": ("x.txt", b"hello", "text/plain")},
                                        data={"declaration": "doc_id: x"})
    job_id = labelled(c)
    j = f"/v1/jobs/{job_id}"
    got["POST /v1/jobs (again)"] = {"status": (r := upload(c)).status_code, "body": masked(r.json(), str(c.tmp))}
    got["GET /v1/jobs"] = record(c, "GET", "/v1/jobs")
    got["GET /v1/jobs/{id} (planned)"] = record(c, "GET", j)
    got["GET /v1/jobs/{id}/events"] = record(c, "GET", f"{j}/events?after=0")
    got["GET /v1/jobs/{id}/gaps (planned)"] = record(c, "GET", f"{j}/gaps")
    got["GET /v1/jobs/{id}/workflow"] = record(c, "GET", f"{j}/workflow")
    got["PUT /v1/jobs/{id}/workflow (bad)"] = record(c, "PUT", f"{j}/workflow", content="nope: [")
    got["GET /v1/jobs/{id}/pages/1 (planned)"] = record(c, "GET", f"{j}/pages/1")
    got["GET /v1/jobs/{id}/pages/9"] = record(c, "GET", f"{j}/pages/9")
    got["GET /v1/jobs/{id}/pages/1/image"] = record(c, "GET", f"{j}/pages/1/image?dpi=40")
    got["POST /v1/jobs/{id}/review (planned)"] = record(c, "POST", f"{j}/review", json={"by": "b", "ops": []})
    got["POST /v1/jobs/{id}/approve"] = record(c, "POST", f"{j}/approve", json={"by": "alice"})
    settle(c, job_id)
    got["GET /v1/jobs/{id} (in_review)"] = record(c, "GET", j)
    got["GET /v1/jobs/{id}/gaps (in_review)"] = record(c, "GET", f"{j}/gaps")
    got["GET /v1/jobs/{id}/pages/1 (in_review)"] = record(c, "GET", f"{j}/pages/1")
    got["POST /v1/jobs/{id}/review (bad op)"] = record(
        c, "POST", f"{j}/review", json={"by": "bob", "ops": [{"action": "exclude", "region_id": "nope"}]})
    got["POST /v1/jobs/{id}/review"] = record(c, "POST", f"{j}/review", json={"by": "bob", "ops": [
        {"action": "exclude", "region_id": f"{DOC}:p0001:r002"},
        {"action": "reclassify", "region_id": f"{DOC}:p0002:r001", "to": "text", "note": "n"}]})
    got["POST /v1/jobs/{id}/retry (in_review)"] = record(c, "POST", f"{j}/retry")
    got["POST /v1/jobs/{id}/confirm"] = record(c, "POST", f"{j}/confirm", json={"by": "carol"})
    settle(c, job_id)
    got["GET /v1/jobs/{id} (delivered)"] = record(c, "GET", j)
    got["GET /v1/packages"] = record(c, "GET", "/v1/packages")
    got["GET /v1/engines"] = record(c, "GET", "/v1/engines")
    got["GET /v1/directories"] = record(c, "GET", "/v1/directories")
    got["GET /v1/directories?path"] = record(c, "GET", f"/v1/directories?path={c.tmp / 'roots' / 'inbox'}")
    got["GET /v1/health"] = record(c, "GET", "/v1/health")
    got["GET /v1/jobs/nope"] = record(c, "GET", "/v1/jobs/nope")
    got["GET /"] = record(c, "GET", "/")

    text = json.dumps(got, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("KB_INTAKE_WRITE_GOLDEN") == "1":
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w", encoding="utf-8") as f:
            f.write(text)
    with open(GOLDEN, encoding="utf-8") as f:
        assert json.loads(text) == json.load(f)
