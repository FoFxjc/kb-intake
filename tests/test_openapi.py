"""The frozen HTTP API (intake-ui-llm-design-v1 §3.5): the OpenAPI document generated at run time must equal the
snapshot in contract/api/openapi-v1.json. To change the API on purpose, regenerate the snapshot with
`uv run python -m kb_intake.service.openapi_snapshot --write` and explain the change in the PR."""

from test_service import make_cfg

from kb_intake.service import openapi_snapshot
from kb_intake.service.app import create_app


def _doc(tmp_path) -> dict:
    return openapi_snapshot.document(create_app(make_cfg(tmp_path)))


def test_openapi_matches_snapshot(tmp_path):
    assert openapi_snapshot.dump(_doc(tmp_path)) == openapi_snapshot.SNAPSHOT.read_text(encoding="utf-8")


def test_every_operation_documents_its_body(tmp_path):
    doc = _doc(tmp_path)
    missing = []
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            for code, resp in op.get("responses", {}).items():
                schema = resp.get("content", {}).get("application/json", {}).get("schema")
                if code.startswith("2") and schema is not None and schema in ({}, {"title": "Response"}):
                    missing.append(f"{method.upper()} {path} {code} response")
                if code.startswith("2") and "content" not in resp and code != "204" \
                        and not path.endswith(("/image", "/source.pdf")) and path != "/":
                    missing.append(f"{method.upper()} {path} {code} has no content")
            if method in ("post", "put") and "requestBody" not in op and not path.endswith("/retry"):
                missing.append(f"{method.upper()} {path} request body")
    assert missing == []


def test_snapshot_check_reports_drift(tmp_path, capsys):
    doc = _doc(tmp_path)
    doc["paths"]["/v1/health"]["get"]["summary"] = "changed"
    assert openapi_snapshot.check(doc) is False
    assert "changed" in capsys.readouterr().out
