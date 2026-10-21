import type { Page, Request } from '@playwright/test';
import { expect, shot, test } from './fixtures';
import { freshJob, LLM_BASE_URL } from './jobs';

// Review inbox (plan Task 6). Every test reviews a job of its own (uploaded, labelled, approved and extracted with
// the fake engines), so the queue starts the same on a reused server: a suspected table on page 2 (Docling split
// it into two text regions, r003 and r004), a chart without text layer on page 1, and the repeated headings
// "Section 1" / "Section 2" as a similar group.

interface Posted {
  path: string;
  body: Record<string, unknown>;
}

/** The POST bodies the page sends to the review and seen endpoints. */
function posts(page: Page): Posted[] {
  const out: Posted[] = [];
  page.on('request', (r: Request) => {
    if (r.method() !== 'POST') return;
    const path = new URL(r.url()).pathname;
    if (path.endsWith('/review') || path.endsWith('/queue/seen')) out.push({ path, body: r.postDataJSON() as Record<string, unknown> });
  });
  return out;
}
const reviewPosts = (all: Posted[]) => all.filter((p) => p.path.endsWith('/review'));
const seenPosts = (all: Posted[]) => all.filter((p) => p.path.endsWith('/queue/seen'));

/** The reviewer's name the specs give (the actions wait for one). */
const NAME = 'E2E Reviewer';
const named = (page: Page) => page.getByLabel('Reviewer').fill(NAME);

const group = (page: Page, key: string) => page.locator(`[data-testid="queue-group"][data-group="${key}"]`);

async function rendered(page: Page, n: number) {
  const pdfPage = page.getByTestId('pdf-page');
  await expect(pdfPage).toHaveAttribute('data-page', String(n));
  await expect(pdfPage).toHaveAttribute('data-rendered', 'true', { timeout: 20_000 });
}

test('groups, counts and the first open item on its page', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  const asked: string[] = [];
  page.on('request', (r) => asked.push(new URL(r.url()).pathname));
  await page.goto(`./#/job/${job.jobId}/review`);
  await expect(page.getByTestId('queue-done')).toHaveText('Done 0 / 2');
  await expect(page.getByTestId('queue-group')).toHaveCount(2);
  await expect(group(page, 'table_suspected')).toContainText('Suspected missed tables');
  await expect(group(page, 'table_suspected').getByTestId('queue-group-count')).toHaveText('0/1');
  await expect(group(page, 'no_text_layer').getByTestId('queue-group-count')).toHaveText('0/1');
  await expect(group(page, 'table_suspected')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByTestId('item-pos')).toHaveText('Suspected missed tables · item 1 of 1');
  await expect(page.getByTestId('issue-title')).toHaveText('Suspected missed table · p2');
  await rendered(page, 2);
  await expect(page.getByTestId('ov-find')).toHaveCount(1);
  const cluster = page.getByTestId('cluster');
  await expect(cluster).toHaveCount(1);
  await expect(cluster).toContainText('×2');
  await expect(cluster).toContainText('Same text · same spot at the page top · same font');
  // LLM disabled on this server: no LLM element, no /llm or /summary request
  await expect(page.getByTestId('llm-panel')).toHaveCount(0);
  await expect(page.getByTestId('llm-chip')).toHaveCount(0);
  expect(asked.filter((p) => p.endsWith('/llm') || p.endsWith('/summary'))).toEqual([]);
  await shot(page, 'review');
});

test('a suspected table from the picture classifier (no find_tables box) is framed on its region', async ({ page, request }) => {
  // OCBC p15: the classifier's table_suspected gap has no bbox of its own; neither box was drawn
  const job = await freshJob(request, { extract: true });
  await page.route(`**/v1/jobs/${job.jobId}/pages/2`, async (route) => {
    const r = await route.fetch();
    const view = await r.json();
    view.gaps = view.gaps.map((g: Record<string, unknown>) =>
      g.code === 'table_suspected' ? { ...g, bbox: undefined, source: 'classifier' } : g);
    await route.fulfill({ response: r, json: view });
  });
  await page.goto(`./#/job/${job.jobId}/review`);
  await expect(page.getByTestId('issue-title')).toHaveText('Suspected missed table · p2');
  await rendered(page, 2);
  await expect(page.getByTestId('ov-find')).toHaveCount(0);
  await expect(page.getByTestId('ov-focus')).toHaveCount(1);
});

test('reclassify the suspected table to table: one op, the absorbed region shown, counts update', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  const sent = posts(page);
  await page.goto(`./#/job/${job.jobId}/review`);
  await named(page);
  await rendered(page, 2);
  await page.getByTestId('act-reclassify-table').click();
  const resolved = page.getByTestId('resolved');
  await expect(resolved).toContainText('Reclassified as table');
  await expect(resolved).toContainText(`1 region absorbed: ${job.rid(2, 4)}`);
  expect(reviewPosts(sent).map((p) => p.body)).toEqual([
    { by: NAME, ops: [{ action: 'reclassify', region_id: job.rid(2, 3), to: 'table', note: null }] },
  ]);
  await expect(page.locator(`[data-testid="ov-region"][data-region="${job.rid(2, 4)}"]`)).toHaveAttribute('data-status', 'absorbed');
  await expect(page.locator(`[data-testid="ov-region"][data-region="${job.rid(2, 3)}"]`)).toHaveAttribute('data-type', 'table');
  await expect(group(page, 'table_suspected').getByTestId('queue-group-count')).toHaveText('1/1');
  await expect(page.getByTestId('queue-done')).toHaveText('Done 1 / 2');
  await expect(page.getByTestId('act-reclassify-table')).toHaveCount(0);
  await shot(page, 'review-reclassified');
});

test('bulk exclude of a similar group: one request, one op per member not already excluded', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  const sent = posts(page);
  await page.goto(`./#/job/${job.jobId}/review`);
  await named(page);
  const cluster = page.getByTestId('cluster');
  await expect(cluster).toContainText('×2');
  // meanwhile one member is excluded elsewhere (the CLI, another tab): the shown group is stale
  const r = await request.post(`/v1/jobs/${job.jobId}/review`, {
    data: { by: 'e2e', ops: [{ action: 'exclude', region_id: job.rid(2, 1) }] },
  });
  expect(r.ok()).toBeTruthy();
  await cluster.getByRole('button', { name: 'Exclude all' }).click();
  await expect(page.getByTestId('cluster-result')).toHaveText('1 excluded');
  expect(reviewPosts(sent).map((p) => p.body)).toEqual([
    { by: NAME, ops: [{ action: 'exclude', region_id: job.rid(1, 1), note: null }] },
  ]);
  const ops = (await (await request.get(`/v1/jobs/${job.jobId}/review`)).json()) as { ops: { action: string; region_id: string }[] };
  expect(ops.ops.map((o) => [o.action, o.region_id])).toEqual([
    ['exclude', job.rid(2, 1)],
    ['exclude', job.rid(1, 1)],
  ]);
});

test('a refused bulk action shows the server message and changes nothing', async ({ page, request, guard }) => {
  guard.allowConsole(/status of 400/);
  const job = await freshJob(request, { extract: true });
  await page.route('**/review', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    await route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'op 1: region is excluded already' }),
    });
  });
  await page.goto(`./#/job/${job.jobId}/review`);
  await named(page);
  const cluster = page.getByTestId('cluster');
  await cluster.getByRole('button', { name: 'Exclude all' }).click();
  const banner = page.getByTestId('error-banner');
  await expect(banner).toContainText('400');
  await expect(banner).toContainText('op 1: region is excluded already');
  await expect(cluster.getByRole('button', { name: 'Exclude all' })).toBeVisible();
  await expect(page.getByTestId('cluster-result')).toHaveCount(0);
  const ops = (await (await request.get(`/v1/jobs/${job.jobId}/review`)).json()) as { ops: unknown[] };
  expect(ops.ops).toEqual([]);
});

test('ack ok and ack wrong (with a note) resolve items', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  const sent = posts(page);
  await page.goto(`./#/job/${job.jobId}/review`);
  await named(page);
  await group(page, 'no_text_layer').click();
  await expect(page.getByTestId('issue-title')).toHaveText('No text layer · p1');
  await page.getByTestId('act-ack-ok').click();
  await expect(page.getByTestId('resolved')).toHaveText('Marked seen, no change.');
  await expect(page.getByTestId('queue-done')).toHaveText('Done 1 / 2');

  await group(page, 'table_suspected').click();
  await expect(page.getByTestId('issue-title')).toHaveText('Suspected missed table · p2');
  await page.getByLabel('Note').fill('an aligned list, not a table');
  await page.getByTestId('act-ack-wrong').click();
  await expect(page.getByTestId('resolved')).toHaveText('Flag marked wrong. Note: an aligned list, not a table');
  await expect(page.getByTestId('queue-done')).toHaveText('Done 2 / 2');

  expect(reviewPosts(sent).map((p) => p.body)).toEqual([
    { by: NAME, ops: [{ action: 'ack', item_id: `gap:no_text_layer:1:${job.rid(1, 4)}`, verdict: 'ok', note: null }] },
    {
      by: NAME,
      ops: [{ action: 'ack', item_id: `gap:table_suspected:2:${job.rid(2, 3)}`, verdict: 'wrong', note: 'an aligned list, not a table' }],
    },
  ]);
});

test('the actions wait for the reviewer name; the ops carry it and it is remembered', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  const sent = posts(page);
  await page.goto(`./#/job/${job.jobId}/review`);
  await group(page, 'no_text_layer').click();
  await expect(page.getByTestId('issue-title')).toHaveText('No text layer · p1');
  await expect(page.getByLabel('Reviewer')).toHaveValue('');
  await expect(page.getByTestId('act-ack-ok')).toBeDisabled();
  await expect(page.getByTestId('act-exclude')).toBeDisabled();
  await expect(page.getByTestId('cluster').getByRole('button', { name: 'Exclude all' })).toBeDisabled();
  await page.getByLabel('Reviewer').fill('Ada Reviewer');
  await expect(page.getByTestId('act-ack-ok')).toBeEnabled();
  await expect(page.getByTestId('cluster').getByRole('button', { name: 'Exclude all' })).toBeEnabled();
  await page.getByTestId('act-ack-ok').click();
  await expect(page.getByTestId('resolved')).toHaveText('Marked seen, no change.');
  expect(reviewPosts(sent).map((p) => p.body.by)).toEqual(['Ada Reviewer']);
  const ops = (await (await request.get(`/v1/jobs/${job.jobId}/review`)).json()) as { ops: { by: string }[] };
  expect(ops.ops.map((o) => o.by)).toEqual(['Ada Reviewer']);
  await page.reload();
  await expect(page.getByLabel('Reviewer')).toHaveValue('Ada Reviewer');
});

test('prev / next with the keyboard through an opened group', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  await page.goto(`./#/job/${job.jobId}/review`);
  await page.getByTestId('cluster').getByRole('button', { name: 'Open this group' }).click();
  const pos = page.getByTestId('item-pos');
  await expect(pos).toHaveText('Section 1 · item 1 of 2');
  await rendered(page, 1);
  await expect(page.getByTestId('issue')).toContainText(job.rid(1, 1));
  await page.keyboard.press('ArrowRight');
  await expect(pos).toHaveText('Section 1 · item 2 of 2');
  await rendered(page, 2);
  await expect(page.getByTestId('issue')).toContainText(job.rid(2, 1));
  await page.keyboard.press('ArrowRight');           // wraps
  await expect(pos).toHaveText('Section 1 · item 1 of 2');
  await page.keyboard.press('ArrowLeft');
  await expect(pos).toHaveText('Section 1 · item 2 of 2');
  // typing in the note field does not move
  await page.getByLabel('Note').press('ArrowLeft');
  await expect(pos).toHaveText('Section 1 · item 2 of 2');
  await page.getByRole('button', { name: 'Previous item' }).click();
  await expect(pos).toHaveText('Section 1 · item 1 of 2');
  await page.getByRole('button', { name: 'Back to the queue' }).click();
  await expect(pos).toHaveText('Suspected missed tables · item 1 of 1');
});

test('each shown item is reported seen once', async ({ page, request }) => {
  const job = await freshJob(request, { extract: true });
  const sent = posts(page);
  await page.goto(`./#/job/${job.jobId}/review`);
  const first = `gap:table_suspected:2:${job.rid(2, 3)}`;
  const second = `gap:no_text_layer:1:${job.rid(1, 4)}`;
  await expect.poll(() => seenPosts(sent).length).toBe(1);
  await group(page, 'no_text_layer').click();
  await expect.poll(() => seenPosts(sent).length).toBe(2);
  await group(page, 'table_suspected').click();
  await group(page, 'no_text_layer').click();
  await page.waitForTimeout(800);
  expect(seenPosts(sent).map((p) => p.body)).toEqual([{ item_ids: [first] }, { item_ids: [second] }]);
  const q = (await (await request.get(`/v1/jobs/${job.jobId}/queue`)).json()) as { groups: { items: { item_id: string; status: string }[] }[] };
  const status = Object.fromEntries(q.groups.flatMap((g) => g.items.map((i) => [i.item_id, i.status])));
  expect(status).toEqual({ [first]: 'seen', [second]: 'seen' });
});

test.describe('with the LLM enabled (fake model)', () => {
  test.use({ baseURL: LLM_BASE_URL });

  test('a chart description to check: the LLM panel, then "Description is fine"', async ({ page, request }) => {
    const job = await freshJob(request, { extract: true });
    const sent = posts(page);
    await page.goto(`./#/job/${job.jobId}/review`);
    await named(page);
    await expect(page.getByTestId('queue-done')).toHaveText('Done 0 / 3');
    await group(page, 'llm_check').click();
    await expect(page.getByTestId('issue-title')).toHaveText('Chart description to check · p1');
    const panel = page.locator('[data-testid="llm-panel"][data-kind="describe"]');
    await expect(panel).toHaveCount(1);
    await expect(panel.getByTestId('llm-chip')).toHaveText('MiniMax-M3 description · low weight');
    await expect(panel).toContainText('A bar chart of revenue by year');
    // the LLM server of the specs has chart to table on: the bar chart's estimated table is shown beside it
    const table = page.locator('[data-testid="llm-panel"][data-kind="chart_table"]');
    await expect(table).toHaveCount(1);
    await expect(table.getByTestId('llm-chip')).toHaveText('MiniMax-M3 chart table (experimental) · low weight');
    await expect(table.getByTestId('llm-table')).toContainText('2025');
    await rendered(page, 1);
    await expect(page.getByTestId('ov-focus')).toHaveCount(1);
    await shot(page, 'review-llm');
    await page.getByTestId('act-ack-ok').click();
    await expect(page.getByTestId('resolved')).toHaveText('Description checked.');
    expect(reviewPosts(sent).map((p) => p.body)).toEqual([
      { by: NAME, ops: [{ action: 'ack', item_id: `llm:describe:${job.rid(1, 4)}`, verdict: 'ok', note: null }] },
    ]);
    await expect(page.getByTestId('queue-done')).toHaveText('Done 1 / 3');
  });
});
