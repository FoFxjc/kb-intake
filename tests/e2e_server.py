"""The server the browser tests (web/e2e, Playwright) run against: `create_app` on a fresh temporary work directory
with the fake Docling engines of tests/jobflow.py (two pages, a table on page 1, a p106-like table that Docling
missed on page 2 and split into one text region per row, a bar chart on page 1). Test-only; never used by kb-intake itself.

    uv run python tests/e2e_server.py --port 8799 [--llm-url URL | --fake-llm] [--fixtures DIR]

- One job is seeded in state `in_review` (labelled, approved and extracted with the fake engines), so the page
  viewer and the review pages have data without waiting.
- `--fixtures DIR` (default web/e2e/.fixtures) receives `upload.pdf`: a two-page PDF with other bytes than the
  seeded job's source, for the upload test (a new job), and `seeded.pdf`, the seeded job's source (an upload of
  it reports the existing job).
- `--llm-url URL` enables the LLM against an OpenAI-compatible endpoint; `--fake-llm` starts tests/fake_llm.py on
  127.0.0.1 and uses it. The key variable is set to a dummy value for this process, so no real key is ever used.
"""

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))      # jobflow, fakes, fake_llm; spawned engine processes inherit sys.path

import jobflow  # noqa: E402
import pymupdf  # noqa: E402


def write_fixtures(fixtures: Path, seeded: Path) -> None:
    fixtures.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(seeded, fixtures / "seeded.pdf")
    upload = fixtures / "upload.pdf"
    jobflow.build_pdf(upload)
    with pymupdf.open(upload) as doc:            # other bytes than the seeded source: a new job on upload
        doc.set_metadata({"title": "kb-intake e2e upload fixture"})
        data = doc.tobytes()
    upload.write_bytes(data)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--llm-url", default=None)
    ap.add_argument("--fake-llm", action="store_true")
    ap.add_argument("--fixtures", type=Path, default=HERE.parent / "web" / "e2e" / ".fixtures")
    args = ap.parse_args(argv)

    import uvicorn

    from kb_intake.service.app import create_app

    llm_url = args.llm_url
    fake = None
    if args.fake_llm:
        from fake_llm import FakeLlm
        fake = FakeLlm()
        llm_url = fake.url
    if llm_url:
        os.environ["INTAKE_LLM_API_KEY"] = "e2e-dummy-key"      # never a real key in the browser tests

    tmp = Path(tempfile.mkdtemp(prefix="kb-intake-e2e-"))
    try:
        flow = jobflow.in_review(tmp, chart=True, p106_split=True, llm_url=llm_url)
        write_fixtures(args.fixtures, flow.src)
        engines = {"label": jobflow.engine(tmp, "label", chart=True, p106_split=True),
                   "extract": jobflow.engine(tmp, "extract", chart=True, p106_split=True)}
        app = create_app(flow.cfg, engines=engines)
        print(f"e2e server: job {flow.job.id} in_review; work dir {tmp}; fixtures {args.fixtures}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        if fake is not None:
            fake.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
