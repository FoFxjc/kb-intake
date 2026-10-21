import { readFileSync } from 'node:fs';

import { expect, shot, test } from './fixtures';

// intake-ui-llm-design-v1 §6 "UI": the whole flow on OCBC 2025 through the browser: upload, search, filter by type,
// approve (charts described when the LLM is on), the review inbox (p106 reclassified to a table, a group of icons
// excluded in one request, one item acked), confirm and deliver. A screenshot per page (E2E_SHOTS); no console
// error and no request to another host (fixtures.ts).
const PDF = process.env.OCBC_PDF ?? '';
const LLM = process.env.OCBC_LLM === '1';
const BY = 'Peter (OCBC e2e)';

test('OCBC end to end through the UI', async ({ page, request }) => {
  test.skip(!PDF, 'set OCBC_PDF');
  const health = await (await request.get('/v1/health')).json();
  expect(health.llm.enabled, 'the server LLM setting matches OCBC_LLM').toBe(LLM);

  // ---------- upload ----------
  await page.goto('./#/jobs');
  await page.getByLabel('PDF file').setInputFiles({
    name: 'ocbc-group_ar_2025_en.pdf', mimeType: 'application/pdf', buffer: readFileSync(PDF) });
  await expect(page.getByLabel('Doc id')).toHaveValue('ocbc-group_ar_2025_en');
  await page.getByLabel('Issuer id').fill('ocbc');
  await page.getByLabel('Document type').fill('annual_report');
  await page.getByLabel('Period end').fill('2025-12-31');
  await page.getByLabel('Source URL').fill('https://www.ocbc.com/group/investors/annual-reports');
  await page.getByLabel('Language (optional)').fill('en');
  await shot(page, '01-upload');
  await page.getByRole('button', { name: 'Upload and label' }).click();
  await expect(page).toHaveURL(/#\/job\/[^/]+\/labels$/);
  const jobId = decodeURIComponent(page.url().split('#/job/')[1].split('/')[0]);
  await expect(page.locator('.step-next')).toContainText('Workflow (your call)', { timeout: 10 * 60_000 });

  // ---------- labels & inventory: search, filter by type ----------
  await expect(page.getByTestId('metadata')).toContainText('276');
  if (LLM) {
    await expect(page.getByTestId('summary-card')).toContainText('OCBC', { timeout: 3 * 60_000 });
    await expect(page.getByTestId('summary-card')).toContainText('low weight');
  } else {
    await expect(page.getByTestId('summary-card')).toHaveCount(0);
  }
  await page.getByTestId('search').fill('overdue');
  const rows = page.getByTestId('region-row');
  await expect(rows.filter({ hasText: 'p106' }).first()).toBeVisible();
  await rows.filter({ hasText: 'p106' }).first().click();
  await expect(page.getByTestId('pdf-page')).toHaveAttribute('data-rendered', 'true', { timeout: 60_000 });
  await expect(page.getByTestId('ov-match')).toHaveCount(1);
  await shot(page, '02-labels-search-overdue');
  await page.getByTestId('search').fill('');
  const tables = page.locator('[data-testid="facet"][data-label="Tables"]');
  await expect(tables.getByTestId('facet-count')).toHaveText('201');      // the cleared search has applied (debounced)
  await tables.click();
  await expect(page.getByTestId('list-title')).toHaveText('Tables');
  await expect(page.getByTestId('list-total')).toHaveText('201 regions');
  await expect(rows.first()).toBeVisible();
  for (const t of await rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-type')))) expect(t).toBe('table');
  await shot(page, '03-labels-tables');

  // ---------- workflow & approval ----------
  await page.goto(`./#/job/${encodeURIComponent(jobId)}/workflow`);
  await expect(page.getByRole('heading', { name: 'How to handle each region type' })).toBeVisible();
  const chart = page.getByTestId('wf-row-chart');
  if (LLM) {
    await expect(chart.getByRole('button', { name: 'LLM describe' })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByTestId('llm-panel')).toContainText('api.minimax.io');
  } else {
    await expect(chart.getByRole('button', { name: 'LLM describe' })).toHaveCount(0);
  }
  await page.getByLabel('Approved by').fill(BY);
  await shot(page, '04-workflow');
  await page.getByRole('button', { name: 'Approve and start extraction' }).click();
  await expect(page).toHaveURL(/\/progress$/);
  await expect(page.getByRole('link', { name: /Review/ }).first()).toBeVisible({ timeout: 15 * 60_000 });
  await shot(page, '05-progress-done');

  // ---------- review inbox ----------
  await page.goto(`./#/job/${encodeURIComponent(jobId)}/review`);
  const group = (key: string) => page.locator(`[data-testid="queue-group"][data-group="${key}"]`);
  await group('table_suspected').click();
  for (let i = 0; i < 5; i++) {                          // the p106 item: the find_tables table Docling missed
    if ((await page.getByTestId('issue-title').textContent())?.includes('p106')) break;
    await page.keyboard.press('ArrowRight');
  }
  await expect(page.getByTestId('issue-title')).toContainText('p106');
  await expect(page.getByTestId('pdf-page')).toHaveAttribute('data-rendered', 'true', { timeout: 60_000 });
  await shot(page, '06-review-p106');
  await page.getByTestId('act-reclassify-table').click();
  await expect(page.getByTestId('resolved')).toContainText('absorbed', { timeout: 120_000 });
  await shot(page, '07-review-p106-table');

  const icons = page.getByTestId('cluster').filter({ hasText: 'Icons about 29' });
  await expect(icons).toHaveCount(1);
  const reviewPosts: string[] = [];
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/review')) reviewPosts.push(r.postData() ?? ''); });
  await icons.getByRole('button', { name: 'Exclude all' }).click();
  await expect(page.getByTestId('cluster-result')).toContainText('3 excluded', { timeout: 120_000 });
  expect(reviewPosts).toHaveLength(1);
  expect(JSON.parse(reviewPosts[0]).ops.map((o: { action: string }) => o.action)).toEqual(['exclude', 'exclude', 'exclude']);

  await group('low_confidence').click();
  await page.getByTestId('act-ack-ok').click();
  await expect(page.getByTestId('resolved')).toBeVisible({ timeout: 60_000 });
  if (LLM) {
    await group('llm_check').click();
    await expect(page.locator('[data-testid="llm-panel"][data-kind="describe"]')).toHaveCount(1);
    await shot(page, '08-review-llm');
  } else {
    await expect(group('llm_check')).toHaveCount(0);
  }
  // 68 similar groups on OCBC scroll inside their card: the review page stays about one screen high
  expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBeLessThan(3000);
  await shot(page, '09-review');
  const ops = (await (await request.get(`/v1/jobs/${jobId}/review`)).json()).ops as { action: string }[];
  expect(ops.map((o) => o.action)).toEqual(['reclassify', 'exclude', 'exclude', 'exclude', 'ack']);

  // ---------- confirm & deliver ----------
  await page.goto(`./#/job/${encodeURIComponent(jobId)}/deliver`);
  await page.getByLabel('Confirmed by').fill(BY);
  await shot(page, '10-deliver');
  await page.getByRole('button', { name: 'Confirm and deliver' }).click();
  await expect(page.getByTestId('delivered')).toBeVisible({ timeout: 10 * 60_000 });
  await expect(page.getByTestId('package-check')).toContainText('check passed');
  await shot(page, '11-delivered');
  const job = (await (await request.get(`/v1/jobs/${jobId}`)).json()).job;
  expect(job.status).toBe('delivered');
});
