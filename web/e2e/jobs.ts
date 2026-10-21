import { expect, type APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Fresh jobs for specs that change review state, so each test starts from the same queue on a reused server.

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '.fixtures');

/** The server with the LLM enabled against the local fake model (playwright.config.ts, second web server). */
export const LLM_BASE_URL = `http://127.0.0.1:${process.env.E2E_LLM_PORT ?? 8798}/ui/`;

export interface FreshJob {
  jobId: string;
  docId: string;
  /** a region id of this job: page and number as in tests/jobflow.py */
  rid: (page: number, n: number) => string;
}

async function waitStatus(request: APIRequestContext, jobId: string, status: string) {
  await expect
    .poll(
      async () => {
        const r = await request.get(`/v1/jobs/${jobId}`);
        const { job } = (await r.json()) as { job: { status: string; running_op: string | null; last_error: string | null } };
        if (job.last_error) throw new Error(job.last_error);
        return job.running_op ? 'running' : job.status;
      },
      { timeout: 45_000, intervals: [200, 300, 500] },
    )
    .toBe(status);
}

/** Uploads the two-page fixture with new bytes and waits until it is labelled; with `extract`, approves it and
 * waits until it is in review. */
export async function freshJob(request: APIRequestContext, { extract }: { extract: boolean }): Promise<FreshJob> {
  const docId = `e2e-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const pdf = Buffer.concat([readFileSync(join(FIXTURES, 'upload.pdf')), Buffer.from(`\n% kb-intake e2e ${docId}\n`)]);
  const declaration = [
    `doc_id: "${docId}"`,
    'issuer_id: "demo"',
    'doc_type: "annual_report"',
    'period_end: "2025-12-31"',
    'source_url: "https://example.com/report.pdf"',
    'completeness:',
    '  full: true',
    'lang: "en"',
    '',
  ].join('\n');
  const r = await request.post('/v1/jobs', {
    multipart: { file: { name: `${docId}.pdf`, mimeType: 'application/pdf', buffer: pdf }, declaration },
  });
  expect(r.status(), await r.text()).toBe(202);
  const { job_id: jobId } = (await r.json()) as { job_id: string };
  await waitStatus(request, jobId, 'planned');
  if (extract) {
    const a = await request.post(`/v1/jobs/${jobId}/approve`, { data: { by: 'e2e' } });
    expect(a.status(), await a.text()).toBe(202);
    await waitStatus(request, jobId, 'in_review');
  }
  return { jobId, docId, rid: (page, n) => `${docId}:p${String(page).padStart(4, '0')}:r${String(n).padStart(3, '0')}` };
}
