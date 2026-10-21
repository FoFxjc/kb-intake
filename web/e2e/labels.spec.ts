import type { Page, Request } from '@playwright/test';
import { Sequencer } from '../src/lib/sequencer';
import { expect, seededJob, shot, test } from './fixtures';
import { freshJob, LLM_BASE_URL } from './jobs';

// Labels & inventory (plan Task 4) against tests/e2e_server.py: the seeded job has 8 regions on two pages (a
// table and a bar chart on page 1; page 2's table split by Docling into two text regions, the first flagged
// table_suspected).

const facet = (page: Page, label: string) => page.locator(`[data-testid="facet"][data-label="${label}"]`);
const TEXT = 'Text (body, headings, lists…)';
const facetCount = (page: Page, label: string) => facet(page, label).getByTestId('facet-count');
const rows = (page: Page) => page.getByTestId('region-row');

async function rendered(page: Page) {
  await expect(page.getByTestId('pdf-page')).toHaveAttribute('data-rendered', 'true', { timeout: 20_000 });
}

function regionRequests(page: Page): Request[] {
  const out: Request[] = [];
  page.on('request', (r) => {
    if (new URL(r.url()).pathname.endsWith('/regions')) out.push(r);
  });
  return out;
}

test.describe('request sequencer (latest wins)', () => {
  test('an older answer arriving after a newer one is not applied', async () => {
    const seq = new Sequencer();
    const applied: string[] = [];
    let resolveOld!: (v: string) => void;
    let resolveNew!: (v: string) => void;
    // the server ignores the abort: both answers arrive, the older one last
    const older = seq.run(() => new Promise<string>((r) => (resolveOld = r)), (v) => applied.push(v));
    const newer = seq.run(() => new Promise<string>((r) => (resolveNew = r)), (v) => applied.push(v));
    resolveNew('overdue');
    expect(await newer).toBe(true);
    resolveOld('o');
    expect(await older).toBe(false);
    expect(applied).toEqual(['overdue']);
  });

  test('start() aborts the previous request; follow() goes stale with it; cancel() stales everything', async () => {
    const seq = new Sequencer();
    const a = seq.start();
    const more = seq.follow();
    expect(more.isLatest()).toBe(true);
    const b = seq.start();
    expect(a.signal.aborted).toBe(true);
    expect(a.isLatest()).toBe(false);
    expect(more.isLatest()).toBe(false);
    expect(b.isLatest()).toBe(true);
    seq.cancel();
    expect(b.signal.aborted).toBe(true);
    expect(b.isLatest()).toBe(false);
    // an error of a stale request is swallowed, of the latest one passed on
    expect(await seq.run(() => Promise.reject(new DOMException('aborted', 'AbortError')), () => {})).toBe(false);
    await expect(seq.run(() => Promise.reject(new Error('boom')), () => {})).rejects.toThrow('boom');
  });
});

test('search: debounced, hits marked, facet counts follow the query', async ({ page, request }) => {
  const jobId = await seededJob(request);
  const reqs = regionRequests(page);
  await page.goto(`./#/job/${jobId}/labels`);
  await expect(page.getByTestId('list-total')).toHaveText('8 regions');
  await expect(rows(page)).toHaveCount(8);
  await expect(facetCount(page, 'All')).toHaveText('8');
  await expect(facetCount(page, 'Tables')).toHaveText('1');
  await expect(facetCount(page, 'Charts')).toHaveText('1');
  await expect(facetCount(page, TEXT)).toHaveText('6');
  await expect(facetCount(page, 'Suspected missed tables')).toHaveText('1');
  await expect(facetCount(page, 'No text layer')).toHaveText('1');
  await expect(page.getByTestId('metadata')).toContainText('Native 2');

  const before = reqs.length;
  await page.getByTestId('search').pressSequentially('overdue', { delay: 40 });
  await expect(page.getByTestId('list-total')).toHaveText('1 region');
  // typing seven characters at 40 ms each is one pause: one request (200 ms debounce)
  expect(reqs.slice(before).filter((r) => new URL(r.url()).searchParams.get('q'))).toHaveLength(1);
  expect(new URL(reqs[reqs.length - 1].url()).searchParams.get('q')).toBe('overdue');

  const hit = rows(page).first();
  await expect(hit).toHaveAttribute('data-page', '2');
  await expect(hit.locator('mark')).toHaveText('Overdue');
  await expect(hit).toContainText('Missed table?');
  // counts for the query: each dimension without its own filter
  await expect(facetCount(page, 'All')).toHaveText('1');
  await expect(facetCount(page, 'Tables')).toHaveText('0');
  await expect(facetCount(page, TEXT)).toHaveText('1');
  await expect(facetCount(page, 'Suspected missed tables')).toHaveText('1');
  await expect(facet(page, 'No text layer')).toHaveCount(0);
  await rendered(page);
  await shot(page, 'labels-search');

  await page.getByTestId('search').fill('');
  await expect(page.getByTestId('list-total')).toHaveText('8 regions');
  await expect(facetCount(page, 'Tables')).toHaveText('1');
});

test('type filter Tables shows only tables; open issues are counted for the filter', async ({ page, request }) => {
  const jobId = await seededJob(request);
  await page.goto(`./#/job/${jobId}/labels`);
  await expect(rows(page)).toHaveCount(8);
  await facet(page, 'Tables').click();
  await expect(page.getByTestId('list-title')).toHaveText('Tables');
  await expect(page.getByTestId('list-total')).toHaveText('1 region');
  await expect(rows(page)).toHaveCount(1);
  for (const t of await rows(page).evaluateAll((els) => els.map((e) => e.getAttribute('data-type')))) expect(t).toBe('table');
  // type counts stay visible for the other types; the gap counts follow the filter
  await expect(facetCount(page, 'Charts')).toHaveText('1');
  await expect(page.getByTestId('facet-gaps')).toContainText('Nothing open');

  await facet(page, TEXT).click();
  await expect(page.getByTestId('list-total')).toHaveText('6 regions');
  for (const t of await rows(page).evaluateAll((els) => els.map((e) => e.getAttribute('data-type')))) {
    expect(['heading', 'text']).toContain(t);
  }
  // an issue replaces the type filter
  await facet(page, 'Suspected missed tables').click();
  await expect(page.getByTestId('list-title')).toHaveText('Suspected missed tables');
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toHaveAttribute('data-page', '2');
});

test('selecting a row shows its page with the box and the search hit', async ({ page, request }) => {
  const jobId = await seededJob(request);
  await page.goto(`./#/job/${jobId}/labels`);
  await page.getByTestId('search').fill('overdue');
  await expect(page.getByTestId('list-total')).toHaveText('1 region');
  const row = rows(page).first();
  const rid = (await row.getAttribute('data-region'))!;
  await row.click();
  const pdfPage = page.getByTestId('pdf-page');
  await expect(pdfPage).toHaveAttribute('data-page', '2');
  await expect(pdfPage).toHaveAttribute('data-rendered', 'true', { timeout: 20_000 });
  await expect(page.locator(`[data-testid="ov-region"][data-region="${rid}"]`)).toHaveClass(/sel/);
  await expect(page.getByTestId('ov-match')).toHaveCount(1);
  const detail = page.getByTestId('region-detail');
  await expect(detail).toContainText(rid);
  await expect(page.getByTestId('region-flag')).toContainText('find_tables found a 2 x 2 table');
  await expect(row).toHaveClass(/on/);
  await shot(page, 'labels-selected');
});

test('a slow answer to an older search never replaces the newer list', async ({ page, request }) => {
  const jobId = await seededJob(request);
  await page.route('**/regions?*', async (route) => {
    if (new URL(route.request().url()).searchParams.get('q') === 'sec') {
      await new Promise((r) => setTimeout(r, 1500));
      try {
        await route.continue();
      } catch {
        // the page aborted it
      }
      return;
    }
    await route.continue();
  });
  await page.goto(`./#/job/${jobId}/labels`);
  await expect(rows(page)).toHaveCount(8);
  const search = page.getByTestId('search');
  const older = page.waitForRequest((r) => new URL(r.url()).searchParams.get('q') === 'sec');
  await search.fill('sec');
  await older;
  await search.fill('overdue');
  await expect(page.getByTestId('list-total')).toHaveText('1 region');
  await page.waitForTimeout(2000);
  await expect(page.getByTestId('list-total')).toHaveText('1 region');
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first().locator('mark')).toHaveText('Overdue');
});

test('LLM disabled: no summary card, no LLM element, no /summary or /llm request', async ({ page, request }) => {
  const jobId = await seededJob(request);
  const asked: string[] = [];
  page.on('request', (r) => asked.push(new URL(r.url()).pathname));
  await page.goto(`./#/job/${jobId}/labels`);
  await expect(rows(page)).toHaveCount(8);
  await expect(page.getByTestId('metadata')).toBeVisible();
  await page.waitForTimeout(500);
  expect(asked.some((p) => p.endsWith('/v1/health'))).toBe(true);
  await expect(page.getByTestId('summary-card')).toHaveCount(0);
  await expect(page.getByTestId('llm-chip')).toHaveCount(0);
  expect(asked.filter((p) => p.endsWith('/summary') || p.endsWith('/llm'))).toEqual([]);
  await rendered(page);
  await shot(page, 'labels-no-llm');
});

test.describe('with the LLM enabled (fake model)', () => {
  test.use({ baseURL: LLM_BASE_URL });

  test('the summary card shows the summary, the low-weight chip and the remote notice', async ({ page, request }) => {
    const job = await freshJob(request, { extract: false });
    await page.goto(`./#/job/${job.jobId}/labels`);
    const card = page.getByTestId('summary-card');
    await expect(card).toBeVisible();
    await expect(card.getByTestId('llm-chip')).toHaveText('Generated by MiniMax-M3 · low weight · for reference');
    // the host the text went to, not "an external service" (an intranet endpoint is not external)
    const health = await (await request.get('/v1/health')).json();
    await expect(card).toContainText(`Text was sent to ${new URL(health.llm.base_url).host}`);
    await expect(card).not.toContainText('external');
    await expect(page.getByTestId('summary-text')).toContainText('An annual report');
    await expect(rows(page)).toHaveCount(8);
    await rows(page).filter({ hasText: 'Chart' }).click();
    await rendered(page);
    await shot(page, 'labels-llm');
  });

  test('a page opened while the job was labelling shows the summary once labelling is done', async ({ page, request }) => {
    // OCBC walk-through: the card asked once (status off, labelling still running) and never asked again
    const job = await freshJob(request, { extract: false });
    let labelling = true;
    await page.route(`**/v1/jobs/${job.jobId}`, async (route) => {
      const r = await route.fetch();
      const body = await r.json();
      if (labelling) body.job = { ...body.job, status: 'uploaded', running_op: 'label' };
      await route.fulfill({ response: r, json: body });
    });
    await page.route(`**/v1/jobs/${job.jobId}/summary`, async (route) => {
      const r = await route.fetch();
      const body = await r.json();
      await route.fulfill({ response: r, json: labelling ? { ...body, status: 'off', text: null } : body });
    });
    await page.goto(`./#/job/${job.jobId}/labels`);
    await expect(page.getByTestId('summary-card')).toContainText('No summary');
    labelling = false;
    await expect(page.getByTestId('summary-text')).toContainText('An annual report', { timeout: 15_000 });
  });

  test('a job labelled before the summary existed says so', async ({ page, request }) => {
    const jobId = await seededJob(request);
    await page.goto(`./#/job/${jobId}/labels`);
    const card = page.getByTestId('summary-card');
    await expect(card).toContainText('No summary for this job');
    await expect(card).not.toContainText('Remote model');
  });
});
