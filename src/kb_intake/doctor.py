"""`kb-intake doctor [--smoke]`: is this installation ready, item by item (PASS / FAIL / SKIP), with what to do
about each failure; a table on stderr and the same as JSON in a report file (never the API key).

Checks: Python 3.12; installed versions against constraints.txt; SQLite FTS5 (search); every pinned model file;
torch's build (CPU or CUDA: reported, never a failure); the LLM when it is configured: where the data goes, a text
request, a request with a small image. The image result is recorded in the data directory (llm-capabilities.json;
the user's .env is never changed) and the next start follows it: when images fail (a text-only model) chart and
picture descriptions are switched off automatically, and that row is a FAIL that does not block; when they work
again, descriptions come back.
`--smoke` writes a small PDF (title, text, a ruled table) and runs label -> approve (the suggested workflow) ->
extract -> confirm -> check on it in a temporary data directory with the real models; with the LLM on, the label
step makes the document summary too. The exit code is 1 when a blocking check failed.
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kb_intake import __version__, config, deps, llm_client, models

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
SMOKE_TABLE = [["Item", "2024", "2025"], ["Revenue", "1,234", "1,410"], ["Expenses", "(567)", "(602)"],
               ["Profit", "667", "808"]]


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str | None = None
    blocking: bool = True
    data: dict = field(default_factory=dict)


# ---------- the installation ----------

def check_python() -> Check:
    v = sys.version_info
    ok = (v.major, v.minor) == (3, 12)
    return Check("python", PASS if ok else FAIL, f"{platform.python_implementation()} {platform.python_version()} "
                 f"({sys.executable})", None if ok else "kb-intake needs Python 3.12: run ./setup.sh, which creates "
                 "a Python 3.12 environment")


def check_dependencies() -> Check:
    try:
        problems = deps.problems()
    except Exception as e:  # noqa: BLE001 - a broken installation is what this reports
        problems = [f"{type(e).__name__}: {e}"]
    if not problems:
        return Check("dependencies", PASS, f"every installed version matches constraints.txt ({len(deps.expected())} "
                     "pins)")
    return Check("dependencies", FAIL, f"{len(problems)} difference(s) from constraints.txt: " + "; ".join(problems[:5]),
                 "install into a separate environment with `pip install -c constraints.txt .` (./setup.sh does); "
                 "do not share it with other projects that use Docling", data={"problems": problems})


def check_fts5() -> Check:
    from kb_intake import search
    ok = search.fts5_available()
    return Check("sqlite_fts5", PASS if ok else FAIL, "SQLite has FTS5 (search)" if ok else "this Python's SQLite "
                 "has no FTS5; search and the web UI need it", None if ok else "use a conda-forge or python.org "
                 "Python 3.12 (./setup.sh with conda, or another python3.12)")


def check_models(models_dir: Path) -> list[Check]:
    out = []
    for spec in models.lock():
        d = models_dir / models.folder(spec["repo_id"])
        problems = models._check_dir(d, spec)
        name = f"model {spec['role']}"
        if problems:
            out.append(Check(name, FAIL, f"{spec['repo_id']} in {d}: " + "; ".join(problems[:4])
                             + (f" (+{len(problems) - 4} more)" if len(problems) > 4 else ""),
                             f"put the files of {models.tree_url(spec)} into {d} (or anywhere, then run "
                             f"`kb-intake models import <that directory>`)", data={"problems": problems}))
        else:
            out.append(Check(name, PASS, f"{spec['repo_id']}@{spec['revision'][:12]}: {len(spec['files'])} files, "
                             "sha256 match"))
    return out


def check_torch() -> Check:
    try:
        import torch
    except Exception as e:  # noqa: BLE001
        return Check("torch", FAIL, f"torch cannot be imported: {type(e).__name__}: {e}"[:300],
                     "reinstall with ./setup.sh")
    cuda = getattr(torch.version, "cuda", None)
    build = f"CUDA {cuda} build" if cuda else "CPU build"
    note = "; kb-intake runs on CPU, so the CUDA libraries are unused (a CPU build is smaller)" if cuda else ""
    return Check("torch", PASS, f"torch {torch.__version__}: {build}{note}", blocking=False,
                 data={"version": torch.__version__, "cuda": cuda})


# ---------- the LLM ----------

def _llm_fix(code: str, cfg: config.Config) -> str:
    host = config.llm_host(cfg.llm)
    return {
        "network": f"cannot connect to {host}: check INTAKE_LLM_BASE_URL, HTTPS_PROXY / NO_PROXY, and SSL_CERT_FILE "
                   "if the company re-signs TLS",
        "timeout": f"{host} did not answer in time: check that the service is up and the model loaded",
        "auth": "the endpoint refused the key: check INTAKE_LLM_API_KEY",
        "http_401": "the endpoint refused the key: check INTAKE_LLM_API_KEY",
        "http_403": "the endpoint refused the key or the model: check INTAKE_LLM_API_KEY and INTAKE_LLM_MODEL",
        "http_404": "not found: INTAKE_LLM_BASE_URL must be the OpenAI-compatible base (usually ending in /v1), and "
                    "INTAKE_LLM_MODEL a model the endpoint serves",
    }.get(code, f"the request failed ({code}): check INTAKE_LLM_BASE_URL and INTAKE_LLM_MODEL")


def _chat(cfg, env, content, chat):
    key = env.get(cfg.llm.api_key_env, "")
    return (chat or llm_client.chat)(cfg.llm.base_url, cfg.llm.model, key, content, max_tokens=1024, timeout=60.0,
                                     retries=0)


def _tiny_png() -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=160, height=100)
    for i, h in enumerate((30, 55, 75)):
        page.draw_rect(pymupdf.Rect(20 + i * 45, 90 - h, 50 + i * 45, 90), color=(0, 0, 0), fill=(0.2, 0.4, 0.8))
    page.insert_text((20, 14), "Revenue", fontsize=9)
    png = page.get_pixmap(dpi=96).tobytes("png")
    doc.close()
    return png


def check_llm(cfg: config.Config, env: dict, chat=None) -> list[Check]:
    if not cfg.llm.enabled:
        return [Check("llm", SKIP, "off (INTAKE_LLM_ENABLED is not true); nothing is sent anywhere", blocking=False)]
    if not env.get(cfg.llm.api_key_env):
        return [Check("llm", FAIL, f"INTAKE_LLM_ENABLED is true but {cfg.llm.api_key_env} is not set",
                      f"set {cfg.llm.api_key_env} in .env, or INTAKE_LLM_ENABLED=false")]
    host = config.llm_host(cfg.llm)
    where = f"data will be sent to {host}" if cfg.llm.remote else f"data stays on this machine ({host})"
    out = [Check("llm", PASS, f"{cfg.llm.model} at {cfg.llm.base_url}: {where}", blocking=False,
                 data={"host": host, "remote": cfg.llm.remote, "model": cfg.llm.model})]
    t0 = time.monotonic()
    try:
        answer = _chat(cfg, env, [{"type": "text", "text": "Reply with the single word OK."}], chat)
        text_ok = bool(answer.text.strip())
        out.append(Check("llm_text", PASS if text_ok else FAIL,
                         f"answered in {time.monotonic() - t0:.1f} s" if text_ok else "an empty answer",
                         None if text_ok else _llm_fix("empty", cfg)))
    except llm_client.LlmError as e:
        text_ok = False
        out.append(Check("llm_text", FAIL, f"{e.code}: {e}", _llm_fix(e.code, cfg)))
    if not text_ok:
        out.append(Check("llm_image", SKIP, "the text request failed", blocking=False))
        return out
    t0 = time.monotonic()
    try:
        answer = _chat(cfg, env, [{"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                                   + __import__("base64").b64encode(_tiny_png()).decode()}},
                                  {"type": "text", "text": "Describe this image in one sentence."}], chat)
        image_ok, why = bool(answer.text.strip()), "an empty answer"
    except llm_client.LlmError as e:
        image_ok, why = False, f"{e.code}: {e}"
    where = config.record_capabilities(cfg, images=image_ok, reason=None if image_ok else why)
    if image_ok:
        out.append(Check("llm_image", PASS, f"answered in {time.monotonic() - t0:.1f} s; chart and picture "
                         "descriptions available", blocking=False))
    else:
        env["INTAKE_LLM_DESCRIBE"] = "false"            # this run's smoke test, as the next start will see it
        out.append(Check("llm_image", FAIL, f"{why}: images not accepted: chart and picture descriptions switched off "
                         f"automatically (latest check, recorded in {where}); the document summary still works",
                         "use a model that accepts images to get descriptions; the next `kb-intake doctor` checks "
                         "again", blocking=False))
    return out


# ---------- smoke test ----------

def smoke_pdf(path: Path) -> None:
    """Three pages: a title and text, a ruled table of SMOKE_TABLE, more text."""
    import pymupdf
    doc = pymupdf.open()
    body = ("kb-intake checks this installation with a small document. It has a title, a few paragraphs of text and "
            "one ruled table with numbers, so that the layout model, the table structure model and the text layer "
            "are all exercised. ") * 3
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "Installation Check Report", fontsize=22)
    p.insert_textbox(pymupdf.Rect(72, 140, 523, 400), body, fontsize=11)
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 90), "Financial Summary", fontsize=16)
    p.insert_textbox(pymupdf.Rect(72, 110, 523, 200), body[:300], fontsize=11)
    x0, y0, cw, rh = 100, 230, 130, 26
    for r, row in enumerate(SMOKE_TABLE):
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            p.draw_rect(rect, color=(0, 0, 0), width=0.8)
            p.insert_text((rect.x0 + 6, rect.y1 - 8), cell, fontsize=11)
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 90), "Notes", fontsize=16)
    p.insert_textbox(pymupdf.Rect(72, 110, 523, 400), body, fontsize=11)
    doc.save(path)
    doc.close()


def _cli(args: list[str], env: dict, cwd: Path, timeout: float) -> tuple[int, dict | None, str]:
    r = subprocess.run([sys.executable, "-m", "kb_intake.cli", *args], env=env, cwd=cwd, capture_output=True,
                       text=True, timeout=timeout)
    last = next((line for line in reversed(r.stdout.splitlines()) if line.startswith("{")), None)
    return r.returncode, json.loads(last) if last else None, r.stderr[-600:]


def run_smoke(cfg: config.Config, env: dict, timeout: float = 1800) -> list[Check]:
    out = []
    with tempfile.TemporaryDirectory(prefix="kb-intake-smoke-") as tmp:
        tmp = Path(tmp)
        smoke_pdf(tmp / "smoke.pdf")
        (tmp / "decl.yaml").write_text("doc_id: kb-intake-smoke\nissuer_id: smoke\ndoc_type: annual_report\n"
                                       "period_end: 2025-12-31\nsource_url: https://example.com/smoke.pdf\n"
                                       "completeness: {full: true}\nlang: en\n", encoding="utf-8")
        senv = {k: v for k, v in env.items() if k not in ("INTAKE_CONFIG", "INTAKE_CACHE_DIR", "INTAKE_WORK_DIR",
                                                          "INTAKE_DELIVERY_DIR")}
        senv.update(INTAKE_DATA_DIR=str(tmp / "data"), INTAKE_MODELS_DIR=str(cfg.models_dir))
        steps = [("label", ["label", "smoke.pdf", "--decl", "decl.yaml"])]
        t0 = time.monotonic()
        rc, res, err = _cli(steps[0][1], senv, tmp, timeout)
        if rc != 0 or not res:
            return [Check("smoke_label", FAIL, f"label failed (exit {rc}): {err.strip()[-300:]}",
                          "see the message; `kb-intake engines` shows the Docling and model state")]
        job, stats = res["job_id"], res.get("stats") or {}
        tables = (stats.get("regions_by_type") or {}).get("table", 0)
        out.append(Check("smoke_label", PASS if tables else FAIL,
                         f"{time.monotonic() - t0:.1f} s; {stats.get('pages')} pages, {stats.get('regions')} regions, "
                         f"{tables} table(s) found", None if tables else "the layout model found no table: check the "
                         "model files (`kb-intake models verify`)"))
        if cfg.llm.enabled and env.get(cfg.llm.api_key_env) and cfg.llm.features.get("summary"):
            state = tmp / "data" / "work" / "jobs" / job / "llm" / "summary.json"
            s = json.loads(state.read_text()) if state.is_file() else {}
            ok = s.get("status") == "done"
            out.append(Check("smoke_summary", PASS if ok else FAIL, "document summary made" if ok else
                             f"no summary (status {s.get('status')}, {s.get('reason') or s.get('error') or ''})",
                             None if ok else "see the llm_text check"))
        for name, args in (("approve", ["approve", job, "--by", "kb-intake doctor"]), ("extract", ["extract", job]),
                           ("confirm", ["confirm", job, "--by", "kb-intake doctor"])):
            t0 = time.monotonic()
            rc, res, err = _cli(args, senv, tmp, timeout)
            if rc != 0 or not res:
                out.append(Check(f"smoke_{name}", FAIL, f"{name} failed (exit {rc}): {err.strip()[-300:]}",
                                 "see the message; `kb-intake engines` shows the Docling and model state"))
                return out
            secs = f"{time.monotonic() - t0:.1f} s"
            if name == "extract":
                summ = res.get("summary") or {}
                ok = summ.get("engine_status") == "success" and summ.get("tables", 0) >= 1
                out.append(Check("smoke_extract", PASS if ok else FAIL, f"{secs}; Docling {summ.get('engine_status')}, "
                                 f"{summ.get('tables')} table(s), {summ.get('gaps')} gap(s)",
                                 None if ok else "Docling did not complete the table structure; see `kb-intake engines`"))
            elif name == "confirm":
                pkg = Path(res["path"])
                cells = [c.get("text") for line in (pkg / "tables.jsonl").read_text().splitlines() if line.strip()
                         for c in json.loads(line)["cells"]]
                want = {"1,234", "(567)", "808"}
                found = want & set(cells)
                rc2, chk, _ = _cli(["check", str(pkg)], senv, tmp, 300)
                ok = rc2 == 0 and chk and chk.get("ok") and found == want
                out.append(Check("smoke_package", PASS if ok else FAIL,
                                 f"{secs}; contract check {'ok' if chk and chk.get('ok') else 'FAILED'}; table cells "
                                 f"read: {len(found)}/{len(want)} sample values",
                                 None if ok else "the package did not pass; send the report"))
            else:
                out.append(Check(f"smoke_{name}", PASS, secs))
    return out


# ---------- the run ----------

def run(cfg: config.Config, env: dict, smoke: bool = False, chat=None, log=None) -> dict:
    checks: list[Check] = []

    def add(items):
        for c in items if isinstance(items, list) else [items]:
            checks.append(c)
            if log:
                log(c)
    add(check_python())
    add(check_dependencies())
    add(check_fts5())
    add(check_models(cfg.models_dir))
    add(check_torch())
    add(check_llm(cfg, env, chat))
    if smoke:
        if any(c.status == FAIL and c.blocking for c in checks if c.name.startswith(("model", "dependencies",
                                                                                   "python"))):
            add(Check("smoke", SKIP, "skipped: fix the failures above first", blocking=False))
        else:
            add(run_smoke(cfg, env))
    blocking = [c.name for c in checks if c.status == FAIL and c.blocking]
    report = {"ok": not blocking, "blocking_failures": blocking, "kb_intake": __version__,
              "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "platform": platform.platform(),
              "machine": platform.machine(), "python": sys.version.split()[0], "models_dir": str(cfg.models_dir),
              "data_dir": str(cfg.work_dir.parent), "checks": [asdict(c) for c in checks]}
    key = env.get(cfg.llm.api_key_env)
    text = json.dumps(report, ensure_ascii=False)
    if key and key in text:                       # never: no check copies it, but the report is meant to be sent
        report = json.loads(text.replace(key, "<key>"))
    return report


def table(report: dict) -> str:
    rows = [(c["status"], c["name"], c["detail"] + (f"\n{'':6}{'':26}fix: {c['fix']}" if c["fix"] and
             c["status"] == FAIL else "")) for c in report["checks"]]
    lines = [f"{s:6}{n:26}{d}" for s, n, d in rows]
    verdict = "ready" if report["ok"] else "NOT ready: " + ", ".join(report["blocking_failures"])
    return "\n".join(lines + [f"\nkb-intake {report['kb_intake']} on {report['machine']}: {verdict}"])


def table_short(report: dict) -> str:
    """The table of a report that stopped at the configuration."""
    return "\n".join(f"{c['status']:6}{c['name']:26}{c['detail']}\n{'':32}fix: {c['fix']}" for c in report["checks"])
