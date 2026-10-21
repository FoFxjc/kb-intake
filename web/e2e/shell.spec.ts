import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, shot, test } from './fixtures';
import { LLM_BASE, plannedJob } from './flow';

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), '.fixtures');

/** upload.pdf with a unique trailer comment: other bytes on every run, so the first upload is a new job even
 * against a server that is reused between runs. */
function uniquePdf(): Buffer {
  const pdf = readFileSync(join(FIXTURES, 'upload.pdf'));
  return Buffer.concat([pdf, Buffer.from(`\n% kb-intake e2e ${Date.now()} ${Math.random()}\n`)]);
}

async function fillDeclaration(page: import('@playwright/test').Page) {
  await page.getByLabel('Issuer id').fill('demo');
  await page.getByLabel('Document type').fill('annual_report');
  await page.getByLabel('Period end').fill('2025-12-31');
  await page.getByLabel('Source URL').fill('https://example.com/report.pdf');
  await page.getByLabel('Language (optional)').fill('en');
}

test('jobs page lists the seeded job', async ({ page }) => {
  await page.goto('./');
  await expect(page.getByRole('heading', { name: 'Jobs' })).toBeVisible();
  await expect(page.getByTestId('job-row').filter({ hasText: 'demo.pdf' })).toHaveCount(1);
  await shot(page, 'jobs');
});

test('upload creates a job and lands on Labels; the same file again shows the existing job', async ({ page }) => {
  const pdf = uniquePdf();
  await page.goto('./#/jobs');
  await page.getByLabel('PDF file').setInputFiles({ name: 'e2e-upload.pdf', mimeType: 'application/pdf', buffer: pdf });
  await expect(page.getByLabel('Doc id')).toHaveValue('e2e-upload');
  await fillDeclaration(page);
  await page.getByRole('button', { name: 'Upload and label' }).click();

  await expect(page).toHaveURL(/#\/job\/[^/]+\/labels$/);
  const jobId = decodeURIComponent(page.url().split('#/job/')[1].split('/')[0]);
  await expect(page.getByTestId('header-file')).toContainText('e2e-upload.pdf');
  await expect(page.getByRole('navigation', { name: 'Flow' })).toContainText('Stats & inventory');
  // labelling runs in the background (busy polling), then the step bar asks for the workflow
  await expect(page.locator('.step-next')).toContainText('Workflow (your call)', { timeout: 45_000 });
  await expect(page.getByTestId('busy')).toHaveCount(0);

  await page.goto('./#/jobs');
  await page.getByLabel('PDF file').setInputFiles({ name: 'e2e-upload.pdf', mimeType: 'application/pdf', buffer: pdf });
  await fillDeclaration(page);
  await page.getByLabel('Doc id').fill('another-id');
  await page.getByRole('button', { name: 'Upload and label' }).click();
  const notice = page.getByTestId('existing-job');
  await expect(notice).toBeVisible();
  await expect(notice).toContainText('already has a job');
  await expect(notice).toContainText('not used');
  await expect(notice.getByRole('link')).toHaveAttribute('href', new RegExp(jobId));
  await expect(page).toHaveURL(/#\/jobs$/);
});

test('a 409 answer shows the error banner with the server message', async ({ page, guard }) => {
  guard.allowConsole(/status of 409/);
  await page.route('**/v1/jobs', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'job 20261021T0000-e2e is running label', error: 'Busy' }),
    });
  });
  await page.goto('./#/jobs');
  await page.getByLabel('PDF file').setInputFiles({ name: 'busy.pdf', mimeType: 'application/pdf', buffer: uniquePdf() });
  await fillDeclaration(page);
  await page.getByRole('button', { name: 'Upload and label' }).click();
  const banner = page.getByTestId('error-banner');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText('409');
  await expect(banner).toContainText('job 20261021T0000-e2e is running label');
  await expect(page).toHaveURL(/#\/jobs$/);
});

test('an unknown job shows the 404 banner with a retry, not a blank page', async ({ page, guard }) => {
  guard.allowConsole(/status of 404/);
  await page.goto('./#/job/no-such-job/labels');
  const banner = page.getByTestId('error-banner');
  await expect(banner).toContainText('404');
  await expect(banner).toContainText('no job no-such-job');
  await expect(banner.getByRole('button', { name: 'Retry' })).toBeVisible();
});

test('with the LLM off the upload form says nothing about sending data', async ({ page }) => {
  await page.goto('./#/jobs');
  await expect(page.getByTestId('upload-form')).toBeVisible();
  await expect(page.getByTestId('upload-llm-notice')).toHaveCount(0);
});

test.describe('LLM enabled (fake model on 127.0.0.1)', () => {
  test.use({ baseURL: LLM_BASE });

  test('the upload form says the summary sends the start of every page before anything is uploaded', async ({ page }) => {
    await page.goto('./#/jobs');
    const notice = page.getByTestId('upload-llm-notice');
    await expect(notice).toContainText(`sent to ${new URL(LLM_BASE).hostname}`);
    await expect(notice).toContainText('the start of every page');
    await expect(notice).toContainText('24,000 characters');
  });

  test('the workflow notice names what each LLM use sends', async ({ request, page }) => {
    const jobId = await plannedJob(request);
    await page.goto(`./#/job/${jobId}/workflow`);
    const notice = page.getByTestId('llm-notice');
    await expect(notice).toContainText('the start of every page');
    await expect(notice).toContainText("each region's image");
  });
});
