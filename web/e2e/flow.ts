import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, type APIRequestContext, type Page, type Request } from '@playwright/test';

// Helpers of the workflow / progress / deliver specs: fresh jobs made through the API (the seeded job stays
// in_review for the other specs), and the second server with the LLM enabled (playwright.config.ts).
const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '.fixtures');
export const LLM_BASE = `http://127.0.0.1:${Number(process.env.E2E_LLM_PORT ?? 8798)}/ui/`;

let n = 0;

/** A new job from upload.pdf with other bytes (a unique trailer comment), labelled and waiting for approval. */
export async function plannedJob(request: APIRequestContext): Promise<string> {
  const stamp = `${Date.now()}-${process.pid}-${n++}`;
  const pdf = Buffer.concat([readFileSync(join(FIXTURES, 'upload.pdf')), Buffer.from(`\n% kb-intake e2e flow ${stamp}\n`)]);
  const declaration = [
    `doc_id: "e2e-flow-${stamp}"`,
    'issuer_id: "demo"',
    'doc_type: "annual_report"',
    'period_end: "2025-12-31"',
    'source_url: "https://example.com/report.pdf"',
    'completeness:',
    '  full: true',
    'lang: "en"',
  ].join('\n');
  const r = await request.post('/v1/jobs', {
    multipart: { file: { name: `flow-${stamp}.pdf`, mimeType: 'application/pdf', buffer: pdf }, declaration },
  });
  expect(r.status(), await r.text()).toBe(202);
  const { job_id } = (await r.json()) as { job_id: string };
  await waitStatus(request, job_id, 'planned');
  return job_id;
}

interface JobBody {
  job: { status: string; running_op: string | null; last_error: string | null; doc_id: string; sha256: string; package: string | null };
}

export async function job(request: APIRequestContext, jobId: string): Promise<JobBody['job']> {
  const r = await request.get(`/v1/jobs/${jobId}`);
  expect(r.ok()).toBeTruthy();
  return ((await r.json()) as JobBody).job;
}

/** Waits until the job is in `status` with no step running (fails on a recorded error). */
export async function waitStatus(request: APIRequestContext, jobId: string, status: string, timeout = 60_000) {
  await expect
    .poll(
      async () => {
        const j = await job(request, jobId);
        if (j.last_error && !j.running_op) return `error: ${j.last_error}`;
        return j.running_op ? `${j.status} (running ${j.running_op})` : j.status;
      },
      { timeout, intervals: [250, 500, 1000] },
    )
    .toBe(status);
}

/** Approves the job's current (suggested) workflow through the API and waits for review. */
export async function approvedJob(request: APIRequestContext): Promise<string> {
  const jobId = await plannedJob(request);
  const wf = (await (await request.get(`/v1/jobs/${jobId}/workflow`)).json()) as { version: number };
  const r = await request.post(`/v1/jobs/${jobId}/approve`, { data: { by: 'E2E Tester', version: wf.version } });
  expect(r.status(), await r.text()).toBe(202);
  await waitStatus(request, jobId, 'in_review');
  return jobId;
}

/** Records the API paths the page asks for (to check that no LLM endpoint is called). */
export function trackPaths(page: Page): string[] {
  const paths: string[] = [];
  page.on('request', (r: Request) => paths.push(new URL(r.url()).pathname));
  return paths;
}

export const llmPaths = (paths: string[]) => paths.filter((p) => /\/(summary|llm)$/.test(p));
